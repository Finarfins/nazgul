"""Meta WhatsApp webhook'u (WA1) + eşleştirme yönetimi (WA2).

Kaynak `nazgul_website/backend/app/routers/whatsapp.py`. WA1 oradan YALNIZ
webhook bölümünü taşımıştı ve başlığı bunu açıkça söylüyordu: "numara
bağlantısı ve eşleştirme uçları TAŞINMADI". WA2 o dört ucu taşıyor.

--- İKİ SINIF UÇ, İKİ FARKLI KAPI — AYNI DOSYADA -------------------------

  * `GET|POST /api/whatsapp/webhook` — PUBLIC (WA1). Çağıran Meta'dır,
    oturum YOKTUR, doğrulama HMAC ve sabit zamanlı token karşılaştırmasıdır.
  * `POST   /api/whatsapp/pairing-codes`        \
    `DELETE /api/whatsapp/pairing-codes/{id}`    | KİRACI uçları (WA2).
    `GET    /api/whatsapp/links`                 | Oturum + `users` izni +
    `DELETE /api/whatsapp/links/{id}`           /  `company_id` yüklemi.

İkisinin AYNI dosyada olması bir kaza değil: `PUBLIC_API` muafiyeti TAM
YOL eşleşmesidir (önek DEĞİL), yani bu dosyaya eklenen bir yönetim ucu
oturumsuz AÇILMAZ. WA1'in `test_MUAFIYET_TAM_YOL_ve_ONEK_DEGIL` kapısı tam
olarak bu senaryoyu koruyordu ve WA2 o korumanın işe yaradığı ilk turdur.

--- KİM ÇAĞIRABİLİR: `users` — ÖLÇÜLMÜŞ BİR SEÇİM, KOLAY YOL DEĞİL -------

`app/auth.py`ye `/api/whatsapp/` öneki eklendi. Alternatifleri TEK TEK
elendi ve elemenin kendisi ÖLÇÜLDÜ (`required_permission`, kural
yazılmadan ÖNCE çağrıldı):

  * KURAL YAZMAMAK: aynı uç ailesi metoda göre İKİ FARKLI kapıdan geçerdi.
    Ölçüldü: `GET /api/whatsapp/links` -> `read` (genel SAFE_METHODS
    kuralı), `POST/DELETE` -> `__admin_only__` (dosyanın sonundaki
    deny-by-default nöbetçisi). Yani okuma yetkisi olan HER rol firmanın
    hangi numaralarının bota bağlı olduğunu görürdü — "kime WhatsApp'tan
    ulaşılabilir" listesi bir KULLANICI YÖNETİMİ yüzeyidir — buna karşılık
    yalnız `admin` kod üretebilirdi.
  * `read`: yukarıdaki sızıntının ta kendisi.
  * YENİ BİR İZİN ADI (`whatsapp.manage`): rol tablosuna hiçbir rolün
    taşımadığı bir sütun açardı ve bugün HERKESİ dışarıda bırakırdı.
  * `SELF_SERVICE_API`: o küme yetki kapısından TAMAMEN muaftır ve üyeliği
    TAM EŞLEŞMEDİR; `{kod_id}` taşıyan bir yol oraya giremez. Ayrıca bu
    uçlar çağıranın KENDİ kimliği üzerinde değil, BAŞKA bir kullanıcının
    numarası üzerinde iş görür.

`users` seçildi çünkü dört uç da tam olarak `/api/users` ailesinin işini
yapıyor: bir kullanıcıya erişim aracı vermek ve geri almak. Önek TAM
EŞLEŞME DEĞİL çünkü `{id}` taşıyan yollar da aynı kapıdan geçmeli.

MERKEZÎ İZNE EK OLARAK her uç, hedefin BU firmadaki üyeliğini işlem anında
yeniden doğrular: merkezî izin "hangi firma" sorusunu cevaplamaz.

--- WEBHOOK UÇLARI NEDEN `PUBLIC_API`DE --------------------------------

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

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..activity_log import log_activity
from ..auth import users, utcnow
from ..config import settings
from ..db import SessionLocal, get_db
from ..tenancy import company_id
from ..whatsapp import eslestirme, giris
from ..whatsapp import schema as ws_schema
from ..whatsapp.cloud_api import (
    SIGNATURE_HEADER,
    gelen_mesajlari_coz,
    verify_signature,
)
from ..whatsapp.schema import whatsapp_links, whatsapp_pairing_codes

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


# ---------------------------------------------------------------------------
# WA2 — EŞLEŞTİRME YÖNETİMİ (kiracı uçları)
# ---------------------------------------------------------------------------
# Dördü de `company_id` yüklemiyle daralır ve dördü de `users` iznine bağlıdır
# (gerekçe başlıkta). `PUBLIC_API`ye GİRMEZLER — muafiyet TAM YOL eşleşmesidir
# ve bu dosyaya eklenen bir uç oturumsuz açılmaz.


class KodGirdisi(BaseModel):
    """Kod üretme gövdesi. `extra="forbid"`: sessizce yok sayılan alan YOK."""

    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)
    #: KOD KİME VERİLİYOR — ZORUNLU (SEC-1, göç `20260912_0082`).
    #:
    #: VARSAYILANI YOK ve bu bilinçli: varsayılan verilseydi bu alanı
    #: yazmayan eski bir istemci sessizce "hedefsiz" kod üretmeye devam
    #: eder, yani kapatılan açık kapalı görünürken AÇIK kalırdı. Alan
    #: zorunlu olduğu için böyle bir istek 422 ile GÜRÜLTÜLÜ düşer.
    #:
    #: Biçim `eslestirme.kod_uret` içinde `telefon.e164` ile doğrulanır;
    #: burada YALNIZ uzunluk sınırı var — asıl karar TEK yerde kalsın.
    phone: str = Field(min_length=1, max_length=32)


def _aktor_id(request: Request) -> int | None:
    user = getattr(request.state, "user", None)
    return user.get("id") if isinstance(user, dict) else None


def _maskeli_telefon(telefon: str | None) -> str:
    """Numaranın YALNIZ son dört hanesi. Ham numara ne cevaba ne loga girer."""
    if not telefon:
        return "***"
    return f"***{telefon[-4:]}"


@router.post("/pairing-codes", status_code=201)
def eslestirme_kodu_uret(
    girdi: KodGirdisi,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """Tek kullanımlık kod üretir. DÜZ KOD YALNIZ BU CEVAPTA, BİR KEZ döner.

    Sonraki hiçbir okuma kodu ya da özetini VERMEZ; kaybedilirse yeni kod
    üretilir (eskisi otomatik iptal olur — `kod_uret` bunu deterministik
    yapar ve hakem `uq_wpc_aktif_kod` kısmi tekilidir).

    Hedef kullanıcı doğrulaması `eslestirme._hedef_dogrula`dadır ve BEŞ ret
    yolu AYNI metni üretir: "kullanıcı yok", "başka firmanın kullanıcısı" ve
    "pasif" ayırt EDİLEMEZ — başka tenant'ın varlığı sızdırılmaz.

    `phone` ZORUNLUDUR (SEC-1, göç `20260912_0082`): kod ÜRETİLDİĞİ ANDA bir
    numaraya bağlanır ve YALNIZ o numaradan kullanılabilir. Alan olmadan bu
    uç, sızan bir kodu ELE GEÇİREN herkese o kullanıcının kimliğini veren
    bir kapı açıyordu; gerekçenin tamamı göçün başlığındadır.
    """
    cid = company_id(request)
    try:
        uretilen = eslestirme.kod_uret(
            db,
            cid,
            girdi.user_id,
            hedef_telefon=girdi.phone,
            created_by=_aktor_id(request),
        )
    except eslestirme.EslestirmeHatasi as hata:
        raise HTTPException(422, str(hata)) from None

    # Aktivite YÜKÜ: yalnız olay türü + kod satırı kimliği + hedef user_id.
    # Kod, özet, telefon ve mesaj metni YAZILMAZ.
    log_activity(
        db,
        cid,
        _aktor_id(request),
        "user.whatsapp_pairing_code_created",
        "user",
        girdi.user_id,
        f"WhatsApp eşleştirme kodu üretildi (kod #{uretilen.kod_id})",
    )
    db.commit()

    # Kod bir SIRDIR: ara katman, tarayıcı ya da CDN önbelleğe ALMAMALIDIR.
    #
    # KATMANLI: bu başlığın BİRİNCİL kaynağı global middleware'dir
    # (`app/main.py` — `/api` önekinin TAMAMINA `no-store` verir). Buradaki
    # satır İKİNCİ katmandır ve uç bir gün `/api` dışına taşınırsa ya da
    # middleware sırası değişirse hâlâ tutar. Yalnız bu satırı kaldıran bir
    # mutasyon testleri KIRMAZ — beklenen sonuç budur.
    response.headers["Cache-Control"] = "no-store"
    return {
        "kod_id": uretilen.kod_id,
        "kod": uretilen.kod,
        "kod_gosterim": eslestirme.kod_bicimle(uretilen.kod),
        "expires_at": uretilen.expires_at,
        "user_id": girdi.user_id,
        # HEDEF MASKELİ döner. Yönetici numarayı ZATEN kendisi yazdı; tam
        # numarayı geri yazmak `GET /links`in maskeleme kararını (defterin
        # amacı "kim bağlı", "hangi numaradan" değil) bu uçtan delerdi.
        "telefon": _maskeli_telefon(uretilen.hedef_telefon),
    }


@router.delete("/pairing-codes/{kod_id}")
def eslestirme_kodu_iptal(
    kod_id: int, request: Request, db: Session = Depends(get_db)
) -> dict:
    """Bekleyen kodu iptal eder. TENANT KAPSAMLI; idempotent DEĞİL (404).

    Yok / başka firmanın / zaten kapanmış — ÜÇÜ DE aynı 404'ü alır. Ayırmak,
    "bu id başka firmada var" bilgisini vermek olurdu.
    """
    cid = company_id(request)
    hedef = db.execute(
        select(whatsapp_pairing_codes.c.user_id).where(
            whatsapp_pairing_codes.c.id == kod_id,
            whatsapp_pairing_codes.c.company_id == cid,
        )
    ).scalar_one_or_none()

    if not eslestirme.kod_iptal(db, cid, kod_id):
        raise HTTPException(404, "Bekleyen eşleştirme kodu bulunamadı.")

    log_activity(
        db,
        cid,
        _aktor_id(request),
        "user.whatsapp_pairing_code_cancelled",
        "user",
        int(hedef) if hedef else None,
        f"WhatsApp eşleştirme kodu iptal edildi (kod #{kod_id})",
    )
    db.commit()
    return {"kod_id": kod_id, "status": ws_schema.PAIRING_CANCELLED}


@router.get("/links")
def baglantilari_listele(
    request: Request, db: Session = Depends(get_db)
) -> list[dict]:
    """Firmanın bağlantı defteri. TELEFON MASKELİ döner.

    Ham numara CEVAPTA YOKTUR ve bu bilinçlidir: defterin amacı "kim bağlı"
    sorusunu cevaplamaktır, "hangi numaradan" sorusunu değil. Son dört hane
    kullanıcının kendi kaydını tanımasına yeter; tam numara, `users` iznine
    sahip herkese firmanın çalışan telefon listesini verirdi.

    Pasif satırlar da dönüyor: "kim ne zaman bağlıydı" izi kapatılan
    bağlantıyla silinmiyor. Sıra deterministik — önce aktifler, sonra en yeni.
    """
    cid = company_id(request)
    satirlar = db.execute(
        select(
            whatsapp_links.c.id,
            whatsapp_links.c.phone,
            whatsapp_links.c.user_id,
            whatsapp_links.c.is_active,
            whatsapp_links.c.created_at,
            users.c.display_name,
        )
        .select_from(whatsapp_links)
        .join(users, users.c.id == whatsapp_links.c.user_id)
        .where(whatsapp_links.c.company_id == cid)
        .order_by(whatsapp_links.c.is_active.desc(), whatsapp_links.c.id.desc())
    ).mappings().all()
    return [
        {
            "id": int(s["id"]),
            "user_id": int(s["user_id"]),
            "display_name": s["display_name"],
            "phone_masked": _maskeli_telefon(s["phone"]),
            "is_active": bool(s["is_active"]),
            "created_at": s["created_at"],
        }
        for s in satirlar
    ]


@router.delete("/links/{baglanti_id}")
def baglanti_kapat(
    baglanti_id: int, request: Request, db: Session = Depends(get_db)
) -> dict:
    """Bağlantıyı PASİFLEŞTİRİR, SİLMEZ: kim ne zaman bağlıydı izi kalır.

    Pasifleştirme `uq_whatsapp_links_aktif_numara` kısmi tekilinin
    KAPSAMINDAN çıkmaktır, yani aynı numara aynı firmaya yeniden
    bağlanabilir hâle gelir. Satırı SİLMEK de aynı sonucu verirdi ama izi de
    silerdi.

    Kiracı yüklemi bir süs DEĞİL: düşseydi bir firmanın yöneticisi BAŞKA
    firmanın bağlantısını kapatabilirdi.
    """
    cid = company_id(request)
    satir = db.execute(
        select(whatsapp_links.c.user_id, whatsapp_links.c.phone).where(
            whatsapp_links.c.id == baglanti_id,
            whatsapp_links.c.company_id == cid,
        )
    ).first()

    sonuc = db.execute(
        update(whatsapp_links)
        .where(
            whatsapp_links.c.id == baglanti_id,
            whatsapp_links.c.company_id == cid,
            whatsapp_links.c.is_active.is_(True),
        )
        .values(is_active=False, updated_at=utcnow())
    )
    if int(sonuc.rowcount or 0) != 1:
        # Yok / başka firmanın / zaten kapalı — ÜÇÜ DE aynı cevap.
        raise HTTPException(404, "Aktif bağlantı bulunamadı.")

    log_activity(
        db,
        cid,
        _aktor_id(request),
        "user.whatsapp_link_deactivated",
        "user",
        int(satir.user_id) if satir else None,
        "WhatsApp bağlantısı kapatıldı: "
        + _maskeli_telefon(satir.phone if satir else None),
    )
    db.commit()
    return {"id": baglanti_id, "is_active": False}
