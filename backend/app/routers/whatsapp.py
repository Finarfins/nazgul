"""Meta WhatsApp webhook uçları — GİRİŞ, işleme YOK (WA1).

Kaynak `nazgul_website/backend/app/routers/whatsapp.py`nin YALNIZ webhook
bölümü. Oradaki numara bağlantısı ve eşleştirme uçları TAŞINMADI: ikisi de
KİRACI uçlarıdır (firma + kullanıcı çözer) ve bu dilimin kapsamında değil.

--- BU İKİ UÇ NEDEN `PUBLIC_API`DE -------------------------------------

Meta'nın sunucuları OTURUM AÇAMAZ. `app/main.py`nin `security_and_audit`
ara katmanı `/api` ile başlayan her yolda önce kiracı seçicisini, sonra
jetonu ister; `PUBLIC_API` üyeliği o bloğun TAMAMINI atlar — yani hem
kimlik hem CSRF kapısını. Muafiyet `/api/auth/login`ınkiyle AYNI biçimdedir
ve BİLEREK öyledir: TAM YOL eşleşmesi, önek DEĞİL. Önek muafiyeti
olsaydı, bu router'a yarın eklenecek bir yönetim ucu da SESSİZCE oturumsuz
açılırdı.

KİMLİK DOĞRULAMANIN YERİNİ NE ALIYOR — iki ayrı şey, iki ayrı uçta:

* GET  — `hub.verify_token` SABİT ZAMANLI karşılaştırma. Kurulum el
  sıkışmasıdır; hiçbir şey yazmaz, yalnız `hub.challenge`i aynen döner.
* POST — `X-Hub-Signature-256` HMAC'i, JSON AYRIŞTIRILMADAN ÖNCE HAM
  gövde üzerinde (`whatsapp/cloud_api.verify_signature`). Sır yoksa ya da
  imza tutmuyorsa FAIL-CLOSED.

--- 403 mi 401 mi: AYRIM SIZDIRILMIYOR ---------------------------------

İmzasız istek ile İMZASI YANLIŞ istek AYNI cevabı alır: 403. Ayırmak,
"secret yapılandırılmış mı" sorusunu kimliksiz bir çağırana cevaplamak
olurdu. 401 seçilmedi çünkü 401 sözleşme gereği `WWW-Authenticate` ile bir
kimlik doğrulama ŞEMASI önerir ve burada önerilecek şema yoktur: Meta
başlıkta bir kimlik taşımaz, gövdeyi imzalar.

--- KANAL KAPALIYKEN 404, 403 DEĞİL ------------------------------------

Üç ayarın (`WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`,
`WHATSAPP_PHONE_NUMBER_ID`) biri bile boşsa iki uç da 404 döner. 403
DEĞİL ve bu bilinçli: 403 "bu uç var ama sen giremezsin" der ve böylece
tarayan birine kurulumun WhatsApp kanalı taşıdığını SÖYLER. 404 hiçbir şey
söylemez. Daha önemlisi: yapılandırılmamış bir kurulumda gövde
AYRIŞTIRILMAZ ve veritabanına HİÇBİR ŞEY YAZILMAZ — kapı en dışta.

--- WEBHOOK UZUN İŞ YAPMAZ ---------------------------------------------

Bu dilimde işçi YOKTUR; uç yalnız doğrulanmış mesajı KALICI kuyruğa yazar
ve 200 döner. Satırlar `RECEIVED` durumunda kalır. Meta 2xx'i geç alırsa
teslimatı TEKRARLAR, bu yüzden bu yolun içine yavaş hiçbir çağrı
girmemelidir — medya bile İNDİRİLMEZ, yalnız kimliği yazılır.
"""

from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, Response

from ..config import settings
from ..db import SessionLocal
from ..whatsapp import giris
from ..whatsapp.cloud_api import (
    SIGNATURE_HEADER,
    gelen_mesajlari_coz,
    verify_signature,
)

log = logging.getLogger("nazgul.whatsapp.webhook")

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

#: Meta tek teslimatta birden çok mesaj gönderir ama gövde makul kalır;
#: üstü ya saldırıdır ya hatadır. Sınır AYRIŞTIRMADAN ÖNCE uygulanır.
#:
#: Küresel gövde sınırı (`Settings.max_request_body_bytes`) 2 MiB'dir ve bu
#: uç için ÇOK GENİŞ: 2 MiB'lik bir "webhook gövdesi" hiçbir kurulumda
#: meşru değildir. Sınır bu yüzden BURADA, uca özel olarak daralıyor;
#: küresel sınırı düşürmek bütün içe aktarma uçlarını kırardı.
GOVDE_MAKS_BAYT = 256 * 1024


@dataclass(frozen=True, slots=True)
class KanalAyari:
    """Üçü de dolu olduğunda kanal AÇIKTIR; biri boşsa ayar üretilmez."""

    app_secret: str
    verify_token: str
    phone_number_id: str


def _sir_metni(sir) -> str:
    return sir.get_secret_value().strip() if sir is not None else ""


def _kanal_ayari() -> KanalAyari | None:
    """Ayarları okur; kanal kapalıysa ``None``.

    Boşluktan ibaret bir sır BOŞ SAYILIR: `WHATSAPP_APP_SECRET=" "` yazan
    bir kurulumda kanalı açık saymak, hiç kimsenin üretemeyeceği bir
    imzayla korunuyor sanmak olurdu.
    """
    app_secret = _sir_metni(settings.whatsapp_app_secret)
    verify_token = _sir_metni(settings.whatsapp_verify_token)
    phone_number_id = (settings.whatsapp_phone_number_id or "").strip()
    if not (app_secret and verify_token and phone_number_id):
        return None
    return KanalAyari(app_secret, verify_token, phone_number_id)


def _acik_ayar() -> KanalAyari:
    ayar = _kanal_ayari()
    if ayar is None:
        # Gerekçe başlıkta: 404, 403 DEĞİL.
        raise HTTPException(404, "Not Found")
    return ayar


@router.get("/webhook")
def dogrula(request: Request) -> Response:
    """Meta kurulum el sıkışması: doğru token'a `hub.challenge` aynen döner.

    Karşılaştırma `hmac.compare_digest` ile SABİT ZAMANLIDIR. `==` ile
    yazılsaydı, cevabın gecikmesi token'ın kaç karakterinin tuttuğunu
    sızdırırdı — ve bu uç oturumsuz olduğu için deneme sayısı da serbest
    olurdu.

    Cevap `text/plain`dir ve JSON DEĞİL: Meta gövdeyi HAM METİN olarak
    karşılaştırır, tırnak içine alınmış bir challenge el sıkışmasını
    düşürür.
    """
    ayar = _acik_ayar()
    mode = request.query_params.get("hub.mode") or ""
    token = request.query_params.get("hub.verify_token") or ""
    challenge = request.query_params.get("hub.challenge") or ""
    if mode != "subscribe" or not hmac.compare_digest(token, ayar.verify_token):
        raise HTTPException(403, "Doğrulama başarısız.")
    return Response(content=challenge, media_type="text/plain")


@router.post("/webhook")
async def webhook(request: Request) -> dict[str, str]:
    """Doğrulanmış mesajları kuyruğa yazar ve HER ZAMAN 200 döner.

    SIRA ÖNEMLİ ve her adım bir öncekine bağımlı:

      1. Kanal açık mı  → değilse 404 (gövde HİÇ OKUNMAZ).
      2. Gövde sınırı   → aşılırsa 413 (JSON HİÇ AYRIŞTIRILMAZ).
      3. HMAC           → tutmazsa 403 (JSON HİÇ AYRIŞTIRILMAZ).
      4. JSON           → bozuksa 400.
      5. Ayrıştırma + `phone_number_id` süzgeci.
      6. Yazma, sonra 200.

    3'ün 4'ten ÖNCE olması bu dosyanın tek en önemli satır sırasıdır:
    doğrulanmamış bir gövde ayrıştırıcıya ULAŞMAZ.
    """
    ayar = _acik_ayar()

    # Önce BEYAN EDİLEN uzunluk: aşırı büyük bir gövdeyi belleğe hiç
    # almadan reddeder. Başlık yalan söyleyebilir, bu yüzden aşağıda
    # GERÇEK uzunluk da ölçülüyor — ikisi de gerekli.
    beyan = request.headers.get("content-length")
    if beyan is not None and beyan.isdigit() and int(beyan) > GOVDE_MAKS_BAYT:
        raise HTTPException(413, "Gövde çok büyük.")

    ham = await request.body()
    if len(ham) > GOVDE_MAKS_BAYT:
        raise HTTPException(413, "Gövde çok büyük.")

    if not verify_signature(ham, request.headers.get(SIGNATURE_HEADER), ayar.app_secret):
        # İmzasız istek ile imzası YANLIŞ istek aynı kapıdan düşer;
        # ayrım sızdırılmaz (başlık).
        raise HTTPException(403, "İmza doğrulanamadı.")

    try:
        govde = json.loads(ham)
    except ValueError:
        raise HTTPException(400, "Geçersiz JSON.") from None

    mesajlar = gelen_mesajlari_coz(govde)

    # İMZA DOĞRU OLSA BİLE MESAJ BİZİM NUMARAMIZA GELMİŞ OLMALI. Aynı Meta
    # uygulamasında birden çok işletme numarası olabilir ve HMAC hepsinde
    # AYNI app secret'la atılır; yani imza "bu mesaj bize geldi" DEMEZ.
    # Yabancı numaranın olayı kuyruğa YAZILMADAN elenir ve yine 200 alır:
    # 4xx dönmek Meta tarafında bir yeniden teslimat fırtınası üretirdi.
    # Günlük yabancı kimliği de içeriği de YAZMAZ, yalnız SAYAR.
    eslesen = [m for m in mesajlar if m.phone_number_id == ayar.phone_number_id]
    if len(eslesen) != len(mesajlar):
        log.warning(
            "whatsapp: %d mesaj beklenmeyen phone_number_id ile elendi",
            len(mesajlar) - len(eslesen),
        )

    if eslesen:
        with SessionLocal() as db:
            giris.gelen_kaydet(db, eslesen)

    # Durum olayları, desteklenmeyen medya ve boş teslimatlar da 200 alır.
    return {"status": "ok"}
