"""Meta WhatsApp Cloud API taşıma sınırı — SAF KOD, bağımlılıksız (WA1).

Kaynak `nazgul_website/backend/app/whatsapp/cloud_api.py`. `verify_signature`
BİREBİR taşındı. Ayrıştırma tarafı UYARLANDI: kaynakta metin ve medya İKİ
AYRI fonksiyondan (`parse_text_messages`, `parse_media_messages`) çıkıyordu
çünkü orada metin yolu LLM'siz kalmak ZORUNDAYDI ve medya yolu fatura
okuyucusuna gidiyordu — yani ayrım BİR TÜKETİCİ AYRIMIYDI. Bu depoda o iki
tüketicinin İKİSİ DE YOK: bu dilim hiçbir şey işlemez, yalnız kuyruğa
yazar. İki fonksiyon tutmak, ayrımı OLMAYAN bir şeyi anlatan ölü bir
sınır olurdu; `gelen_mesajlari_coz` ikisini TEK sırada döndürür ve tür
ayrımı satırın `media_id` sütununda GÖRÜNÜR kalır.

GÜVENLİK KURALLARI BURADA, ROTA YÖNETİCİSİNDE DEĞİL: imza HAM BAYTLAR
üzerinde, JSON AYRIŞTIRILMADAN ÖNCE doğrulanır. Ayrıştırılmış gövde
üzerinden imza almak, `json.loads` → `json.dumps` turunun anahtar
sırasını, boşluğu ve sayı biçimini değiştirebilmesi yüzünden GEÇERLİ bir
imzayı da reddedebilir; daha kötüsü, doğrulamayı ayrıştırmanın ARDINA
koymak, imzasız bir gövdenin ayrıştırıcıya ulaşmasına izin verir.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

#: Meta imzayı bu önekle gönderir: ``sha256=<hex>``.
SIGNATURE_PREFIX = "sha256="

#: İmza başlığının adı. Meta HTTP başlıklarını büyük/küçük harf duyarsız
#: gönderir; okuma tarafı (Starlette `Headers`) zaten duyarsızdır.
SIGNATURE_HEADER = "X-Hub-Signature-256"

#: Fatura okuyucusunun kabul ettiği türler. Kaynakla BİREBİR aynı küme.
#: BU DİLİMDE HİÇBİR MEDYA İNDİRİLMEZ; küme yalnız kuyruğa YAZILACAK
#: satırları süzer — panelin reddettiği bir dosya sohbetten de girmemeli.
SUPPORTED_MEDIA_MIME = frozenset({
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
})

#: Fatura taşıyabilecek TEK iki mesaj türü. Ses, video, çıkartma, kişi ve
#: konum SESSİZCE elenir.
_MEDIA_TYPES = ("image", "document")

#: `whatsapp_inbound.text` sütununa yazılmadan önce metin bu uzunlukta
#: kırpılır. Sütun `TEXT` yani sınırsız; kırpma bir ŞEMA zorunluluğu değil,
#: bir MALİYET kararıdır (bir sohbet mesajı bundan uzunsa taşıdığı şey
#: soru değildir) ve `whatsapp/kopru.SORU_MAKS` ile AYNI sayıdır.
METIN_MAKS = 500


@dataclass(frozen=True, slots=True)
class GelenMesaj:
    """Normalleştirilmiş gelen mesaj — HİÇBİR firma/rol iddiası taşımaz.

    `sender_phone` Meta'nın `from` alanıdır ve bir KİMLİK DEĞİL, bir
    dizedir: kimin olduğu bu dilimde SORULMAZ.

    `media_id` doluysa satır bir medya satırıdır ve `text` altyazıdır
    (çoğu kez boş). İkili veri BURADA DEĞİLDİR: Meta medyayı ikinci bir
    kimlik doğrulamalı istekle sunar ve o isteği webhook'un içinde yapmak,
    Meta'nın beklediği 2xx'i geciktirip teslimat tekrarını üretirdi.
    """

    wamid: str
    sender_phone: str
    phone_number_id: str
    text: str
    media_id: str | None
    media_mime: str | None


def verify_signature(ham_govde: bytes, imza_basligi: str | None, app_secret: str) -> bool:
    """`X-Hub-Signature-256`i TAM ham gövde üzerinde doğrular.

    Kaynaktan BİREBİR. Eksik yapılandırma FAIL-CLOSED'dır: `app_secret`
    boşsa hiçbir imza geçerli değildir. `compare_digest`, sahte bir
    imzanın NE KADARININ tuttuğunu sızdırmaz.

    Uzunluk denetimi (64 onaltılık karakter) `compare_digest`ten ÖNCE
    duruyor ve gereklidir: farklı uzunluktaki girdilerde `compare_digest`
    zaten `False` döner ama bu denetim, biçimi bozuk başlığın hiç
    HMAC hesaplatmamasını da sağlar.
    """
    if not app_secret or not imza_basligi or not imza_basligi.startswith(SIGNATURE_PREFIX):
        return False
    verilen = imza_basligi[len(SIGNATURE_PREFIX):]
    if len(verilen) != hashlib.sha256().digest_size * 2:
        return False
    beklenen = hmac.new(app_secret.encode("utf-8"), ham_govde, hashlib.sha256).hexdigest()
    return hmac.compare_digest(beklenen, verilen.lower())


def _dolu_metin(deger: Any) -> bool:
    return isinstance(deger, str) and bool(deger.strip())


def _metin_mesaji(mesaj: dict, phone_number_id: str, gorulen: set[str]) -> GelenMesaj | None:
    govde = mesaj.get("text")
    metin = govde.get("body") if isinstance(govde, dict) else None
    wamid = mesaj.get("id")
    gonderen = mesaj.get("from")
    if not all(_dolu_metin(p) for p in (wamid, gonderen, metin)):
        return None
    if wamid in gorulen:
        return None
    gorulen.add(wamid)
    return GelenMesaj(
        wamid=wamid,
        sender_phone=gonderen,
        phone_number_id=phone_number_id,
        text=metin.strip()[:METIN_MAKS],
        media_id=None,
        media_mime=None,
    )


def _medya_mesaji(
    mesaj: dict, tur: str, phone_number_id: str, gorulen: set[str]
) -> GelenMesaj | None:
    dugum = mesaj.get(tur)
    if not isinstance(dugum, dict):
        return None
    wamid = mesaj.get("id")
    gonderen = mesaj.get("from")
    media_id = dugum.get("id")
    mime = dugum.get("mime_type")
    if not all(_dolu_metin(p) for p in (wamid, gonderen, media_id, mime)):
        return None
    # "image/jpeg; codecs=..." biçimi geliyor; ayıklanmazsa geçerli
    # fotoğraf elenir (kaynakta ölçülmüş bir hata).
    temiz_mime = mime.split(";")[0].strip().lower()
    if temiz_mime not in SUPPORTED_MEDIA_MIME:
        return None
    if wamid in gorulen:
        return None
    gorulen.add(wamid)
    altyazi = dugum.get("caption")
    return GelenMesaj(
        wamid=wamid,
        sender_phone=gonderen,
        phone_number_id=phone_number_id,
        text=(altyazi.strip()[:METIN_MAKS] if isinstance(altyazi, str) else ""),
        media_id=media_id,
        media_mime=temiz_mime,
    )


def gelen_mesajlari_coz(govde: Any) -> list[GelenMesaj]:
    """Meta webhook gövdesinden DESTEKLENEN mesajları çıkarır.

    Teslimat/okundu durumları, bozuk girdiler, tepkiler, desteklenmeyen
    medya türleri ve KİMLİKSİZ mesajlar SESSİZCE elenir — reddetmek Meta
    tarafında bir teslimat fırtınası üretirdi.

    Aynı teslimat içindeki kopya `wamid`ler burada tekilleştirilir. Bu
    KOLAYLIK değildir: aynı gövdede iki kez geçen bir kimlik, tek bir
    `INSERT` turunda kendi UNIQUE kısıtına çarpardı ve yazma döngüsünü
    gereksizce bir SAVEPOINT geri almasına sokardı. Teslimatlar ARASI
    (kalıcı) idempotency bu fonksiyonun işi DEĞİL, veritabanı kısıtının
    işidir — süreç belleği çok konteynerde paylaşılmaz.
    """
    if not isinstance(govde, dict) or govde.get("object") != "whatsapp_business_account":
        return []

    sonuc: list[GelenMesaj] = []
    gorulen: set[str] = set()
    for girdi in govde.get("entry") or []:
        if not isinstance(girdi, dict):
            continue
        for degisim in girdi.get("changes") or []:
            deger = degisim.get("value") if isinstance(degisim, dict) else None
            if not isinstance(deger, dict):
                continue
            ust_veri = deger.get("metadata")
            pnid = ust_veri.get("phone_number_id") if isinstance(ust_veri, dict) else None
            if not _dolu_metin(pnid):
                continue
            for mesaj in deger.get("messages") or []:
                if not isinstance(mesaj, dict):
                    continue
                tur = mesaj.get("type")
                if tur == "text":
                    cozulen = _metin_mesaji(mesaj, pnid, gorulen)
                elif tur in _MEDIA_TYPES:
                    cozulen = _medya_mesaji(mesaj, tur, pnid, gorulen)
                else:
                    continue
                if cozulen is not None:
                    sonuc.append(cozulen)
    return sonuc



# ---------------------------------------------------------------------------
# GIDEN TARAF (WA4) — Meta Cloud API'ye TEK cikis noktasi
# ---------------------------------------------------------------------------
# `SmtpEmailNotificationProvider`in yazili kuralinin AYNISI burada da gecerli:
# tasiyici YALNIZ bu moduldedir. `urllib.request` importu uygulama modulleri
# icinde BASKA hicbir yerde WhatsApp icin kullanilmaz, boylece "kim, nereden
# WhatsApp mesaji gonderiyor" sorusunun TEK bir cevabi olur. Kapi:
# `tests/test_wa4_bekleyen.py::test_META_TASIYICISI_TEK_MODULDE`.

#: Graph API surumu. SABITLENMIS ve bu bilincli: Meta surumsuz bir yol
#: (`/messages`) kabul etmez ve "en yeni"ye baglanmak, Meta bir alan
#: kaldirdiginda uretimi HABERSIZ kirardi.
GRAPH_SURUMU = "v21.0"
GRAPH_TABAN = "https://graph.facebook.com"

#: Gonderim zaman asimi. Bildirim motorunun lease'i 5 dakikadir
#: (`notifications/service._LEASE_MINUTES`); tasiyici hicbir kosulda lease'i
#: asacak kadar beklememelidir, aksi halde satir baska bir surece kapilir.
#: `SMTP_TIMEOUT_SECONDS` (15) ile AYNI buyukluk sinifi.
GONDERIM_ZAMAN_ASIMI = 15


class GonderimHatasi(Exception):
    """Meta'ya gonderim basarisiz. Mesaj metni SUNUCU METNI TASIMAZ.

    Meta'nin hata govdesi jeton parcasi, dahili istek kimligi ve isletme
    hesabi kimligi tasiyabilir; bunlarin bildirim defterine yazilmasi
    denetim kaydini bir sizinti yuzeyine cevirirdi. Yukari yalnizca HTTP
    durum kodu tasinir.
    """


def metin_gonder(
    *,
    access_token: str,
    phone_number_id: str,
    telefon: str,
    metin: str,
    zaman_asimi: int = GONDERIM_ZAMAN_ASIMI,
) -> str:
    """Meta Cloud API uzerinden DUZ METIN mesaji gonderir; `wamid` dondurur.

    FAIL-CLOSED: jeton ya da numara kimligi bossa AGA HIC CIKILMAZ. Cagiran
    (`notifications/provider.WhatsAppNotificationProvider`) bu durumu zaten
    onceden eliyor; buradaki denetim ikinci kapidir ve gerekli, cunku bos
    bir `Bearer ` basligiyla yapilan istek Meta tarafinda kimlik dogrulama
    hatasi olarak SAYILIR.

    Donen deger Meta'nin verdigi mesaj kimligidir (`messages[0].id`).
    Kimlik GELMEZSE `GonderimHatasi` yukselir ve bu bilincli: kimliksiz bir
    2xx, gonderimin gerceklestiginin KANITI DEGILDIR ve `SENT` yazmak icin
    kanit sarttir.
    """
    jeton = (access_token or "").strip()
    numara_kimligi = (phone_number_id or "").strip()
    if not jeton or not numara_kimligi:
        raise GonderimHatasi("WhatsApp gonderimi yapilandirilmamis")

    govde = json.dumps(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": telefon,
            "type": "text",
            # `preview_url` KAPALI: acik olsaydi mesajdaki bir baglantiyi
            # Meta'nin getirmesini tetiklerdi ve bu, kullaniciya giden
            # metnin icerigine bagli bir DIS ISTEK uretirdi.
            "text": {"preview_url": False, "body": metin},
        },
        ensure_ascii=False,
    ).encode("utf-8")

    istek = urllib.request.Request(
        f"{GRAPH_TABAN}/{GRAPH_SURUMU}/{numara_kimligi}/messages",
        data=govde,
        headers={
            "Authorization": f"Bearer {jeton}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(istek, timeout=zaman_asimi) as cevap:
            cozulen = json.loads(cevap.read().decode("utf-8"))
    except urllib.error.HTTPError as hata:
        # Govde OKUNMAZ ve yukari TASINMAZ — gerekce `GonderimHatasi`nda.
        raise GonderimHatasi(f"Meta gonderimi reddetti (HTTP {hata.code})") from hata
    except Exception as hata:  # aglar kopar, DNS duser, zaman asimi olur
        raise GonderimHatasi(
            f"Meta gonderimi basarisiz ({type(hata).__name__})"
        ) from hata

    mesajlar = cozulen.get("messages") if isinstance(cozulen, dict) else None
    if isinstance(mesajlar, list) and mesajlar:
        wamid = mesajlar[0].get("id") if isinstance(mesajlar[0], dict) else None
        if _dolu_metin(wamid):
            return str(wamid)
    raise GonderimHatasi("Meta cevabinda mesaj kimligi yok")


__all__ = [
    "METIN_MAKS",
    "GONDERIM_ZAMAN_ASIMI",
    "GRAPH_SURUMU",
    "GRAPH_TABAN",
    "SIGNATURE_HEADER",
    "SIGNATURE_PREFIX",
    "SUPPORTED_MEDIA_MIME",
    "GelenMesaj",
    "GonderimHatasi",
    "gelen_mesajlari_coz",
    "metin_gonder",
    "verify_signature",
]
