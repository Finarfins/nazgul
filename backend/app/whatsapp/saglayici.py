"""WhatsApp mesaj sağlayıcısı: metin GÖNDERİR, medya İNDİRİR (WA5).

Kaynak `nazgul_website/backend/app/whatsapp/provider.py`. WA3-full yalnız
**metin gönderme** yarısını taşımış, `medya_indir` yarısını kapsam
gerekçesiyle DIŞARIDA bırakmıştı: "indirilen baytı okuyacak hiçbir çağıran
yok". WA5 O ÇAĞIRANI GETİRDİ (`fatura.py`), yani gerekçe DÜŞTÜ ve yarı
taşındı. Kullanılmayan bir ağ yolu ölçülmeyen bir ağ yoludur; bu yol artık
`tests/test_wa5_fatura.py`de sahte `urlopen` ile ölçülüyor.

--- `metin_gonder` ARTIK MESAJ KİMLİĞİ DÖNER -----------------------------

WA4 `external_id`yi BOŞ bırakmıştı ve gerekçesini yazmıştı: kimliği
döndürmek "WA3'ün sözleşmesini WA4 uğruna genişletmek olurdu". WA5 o
genişletmeyi YAPIYOR ve bedeli ölçüldü: dönüş tipi `None` yerine
`str | None`; dönüşü YOK SAYAN çağıran (WA3 işçisi, `service.py`)
etkilenmez. `SENT`in anlamı DEĞİŞMEDİ — hâlâ "Meta POST'u 2xx ile kabul
etti", bir teslimat kanıtı değil.

--- VARSAYILAN AĞA ÇIKMAZ ------------------------------------------------

`saglayici_al()` yalnız `settings.whatsapp_access_token` DOLUYKEN Meta
sağlayıcısını kurar; boşken :class:`NoOpSaglayici` döner ve o sınıf
`urllib`i İÇE BİLE AKTARMAZ — gönderilen mesajları bellekte biriktirir.
Yani yapılandırılmamış bir kurulumda bu modülün ağ yolu ÇALIŞTIRILAMAZ,
"çalıştırılır ama bir yere gitmez" değil.

--- JETON HİÇBİR YERE YAZILMAZ -------------------------------------------

Jeton yalnız `Authorization` başlığında görünür. Günlük satırları jetonu,
mesaj metnini ve müşteri mali verisini TAŞIMAZ; telefon son dört hane hariç
maskelenir. Kapı: `tests/test_wa3_worker.py::
test_JETON_HICBIR_GUNLUGE_YAZILMIYOR`.

--- `httpx` DEĞİL `urllib` -----------------------------------------------

`httpx` bu depoda yalnız TEST bağımlılığıdır (`requirements-dev`); çalışma
zamanı bağımlılığı DEĞİL. Kaynak da stdlib `urllib` kullanıyor. Yeni bir
çalışma zamanı bağımlılığı, bir cevap göndermek için ödenecek yanlış bedel
olurdu.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from ..config import settings

log = logging.getLogger("nazgul.whatsapp.saglayici")

#: Meta metin mesajı sınırı 4096; kırpmayı BİZ yaparız ki API 400 dönmesin
#: ve kırpılmış da olsa bir cevap gitsin.
MESAJ_MAKS = 4000

#: Tam saniye BİLEREK: `app/` altında `float` ADI decimal sözleşme kapısıyla
#: yasak (`test_v2_9_decimal_contract`) ve zaman aşımının kesirli olması
#: gerekmiyor. Aynı karar `kopru.py::_TIMEOUT_SANIYE`de de verildi.
ZAMAN_ASIMI_SANIYE = 15

#: Yanıt gövdesi OKUNUR ama SINIRLI okunur: bağlantıyı kapatmadan önce
#: gövdeyi tüketmek keep-alive için gerekli, sınırsız okumak ise karşı
#: tarafın belirlediği bir bellek tüketimi olurdu. `kopru.py` ile aynı sınır.
_YANIT_EN_COK_BAYT = 64_000

#: Medya için AYRI ve daha uzun zaman aşımı: bir fotoğraf birkaç megabayt
#: olabilir ve metin gönderiminin 15 saniyesi indirme için dar. Yine tam
#: saniye (decimal sözleşme kapısı `float` ADINI da yasaklıyor).
MEDYA_ZAMAN_ASIMI_SANIYE = 30

#: Meta'nın kendi görsel/belge tavanının üstünde bir sınır DEĞİL, BİZİM
#: bellek sınırımız: indirilen bayt tümüyle bellekte tutuluyor ve işçi
#: süreci birden çok satırı sırayla işliyor.
MEDYA_MAKS_BAYT = 8 * 1024 * 1024


def mesaj_kimligi(govde: bytes) -> str | None:
    """2xx gövdesinden Meta'nın mesaj kimliğini (`messages[0].id`) ayıklar.

    HER BAŞARISIZLIK ``None``DIR, istisna DEĞİL. Bu bilerek: kimlik bir
    denetim kolaylığıdır, gönderimin başarı ölçütü DEĞİLDİR (o ölçüt HTTP
    durumudur). Ayrıştırmayı zorunlu kılmak, Meta gövde biçimini
    değiştirdiği gün GERÇEKTEN GİTMİŞ mesajları başarısız sayardı ve işçi
    onları yeniden gönderirdi — yani kullanıcı aynı cevabı iki kez alırdı.
    """
    try:
        cozum = json.loads(govde.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(cozum, dict):
        return None
    mesajlar = cozum.get("messages")
    if not isinstance(mesajlar, list) or not mesajlar:
        return None
    ilk = mesajlar[0]
    if not isinstance(ilk, dict):
        return None
    kimlik = ilk.get("id")
    if not isinstance(kimlik, str) or not kimlik.strip():
        return None
    return kimlik.strip()


class GonderimHatasi(Exception):
    """Yeniden DENENEBİLİR gönderim hatası (ağ, 5xx, 429)."""


class KotaHatasi(GonderimHatasi):
    """Meta oran sınırı (429). Yeniden denenebilir; ayrı ad ölçüm içindir."""


class KaliciGonderimHatasi(Exception):
    """Yeniden denemenin DÜZELTEMEYECEĞİ hata (4xx: bozuk numara, yetki)."""


def maskele(telefon: str) -> str:
    return f"***{telefon[-4:]}" if len(telefon) > 4 else "***"


class MesajSaglayici(ABC):
    @abstractmethod
    def metin_gonder(self, alici: str, metin: str) -> str | None:
        """Kısa metin gönderir; hatayı SINIFLANDIRIP fırlatır.

        Sınıflandırma işçinin sözleşmesidir: :class:`GonderimHatasi`
        satırı RECEIVED'a döndürür (yeniden denenir),
        :class:`KaliciGonderimHatasi` DEAD yapar.

        DÖNÜŞ Meta'nın verdiği mesaj kimliğidir (`wamid`), yoksa ``None``.
        Kimlik BİR YAN ÜRÜNDÜR, bir başarı ölçütü DEĞİL: başarının ölçütü
        istisna ATILMAMASIDIR ve o ölçüt değişmedi. Bu yüzden dönüşü
        yok sayan çağıran (WA3 işçisi) hiç etkilenmez.
        """

    def medya_indir(self, medya_kimligi: str) -> bytes:
        """Meta'daki medyanın baytlarını indirir. Varsayılan: DESTEKLENMİYOR.

        Taban sınıfta gövde var çünkü varsayılan bir SESSİZ BAŞARISIZLIK
        değil, AÇIK bir REDDİR: ağa çıkmayan bir sağlayıcıdan bayt isteyen
        çağıran, boş bir dizi değil bir istisna almalıdır.
        """
        raise KaliciGonderimHatasi("medya indirme desteklenmiyor")


class NoOpSaglayici(MesajSaglayici):
    """Ağ çağrısı YAPMAZ. Yapılandırılmamış kurulumun sağlayıcısı.

    Gönderilen mesajları bellekte tutar ki testler "cevap üretildi ama ağa
    çıkmadı" durumunu gerçekten doğrulayabilsin — sessiz bir `pass`, aynı
    şeyi ölçülemez biçimde yapardı.
    """

    def __init__(self) -> None:
        self.gonderilenler: list[tuple[str, str]] = []

    def metin_gonder(self, alici: str, metin: str) -> str | None:
        self.gonderilenler.append((alici, metin))
        # Meta'ya ÇIKILMADI, dolayısıyla Meta'nın verdiği bir kimlik de YOK.
        # Sahte bir kimlik üretmek, `external_id`si dolu ama hiçbir yere
        # gitmemiş bir denetim satırı demekti.
        return None


class MetaBulutSaglayici(MesajSaglayici):
    """Resmî Cloud API `/{phone_number_id}/messages` ucuna POST atar."""

    def adres(self) -> str:
        taban = (settings.whatsapp_graph_base_url or "").rstrip("/")
        surum = (settings.whatsapp_graph_version or "").strip("/")
        numara = (settings.whatsapp_phone_number_id or "").strip()
        return f"{taban}/{surum}/{numara}/messages"

    def govde(self, alici: str, metin: str) -> bytes:
        return json.dumps(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": alici,
                "type": "text",
                "text": {"preview_url": False, "body": metin[:MESAJ_MAKS]},
            }
        ).encode("utf-8")

    def metin_gonder(self, alici: str, metin: str) -> str | None:
        # İÇE AKTARMA GÖVDEDE: NoOp yolunun `urllib`e HİÇ dokunmaması,
        # "ağa çıkmadı" cümlesini modül düzeyinde de doğru kılar.
        import urllib.error
        import urllib.request

        jeton = settings.whatsapp_access_token
        numara = (settings.whatsapp_phone_number_id or "").strip()
        if jeton is None or not jeton.get_secret_value().strip() or not numara:
            # `saglayici_al` normalde buraya düşürmez; yine de FAIL-CLOSED:
            # eksik yapılandırmayla ağ çağrısı DENENMEZ.
            raise KaliciGonderimHatasi("saglayici yapilandirilmamis")

        istek = urllib.request.Request(
            self.adres(),
            data=self.govde(alici, metin),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {jeton.get_secret_value()}",
            },
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - adres ayar dosyasından
                istek, timeout=ZAMAN_ASIMI_SANIYE
            ) as yanit:
                govde = yanit.read(_YANIT_EN_COK_BAYT)
        except urllib.error.HTTPError as hata:
            durum = int(hata.code)
            # Günlük yalnız DURUM ve maskeli numara taşır: gövde jetonu
            # içermez ama Meta'nın hata gövdesi kullanıcı metnini
            # yankılayabilir ve o metin mali veri taşıyabilir.
            log.warning(
                "whatsapp gonderim hatasi durum=%s alici=%s", durum, maskele(alici)
            )
            if durum == 429:
                raise KotaHatasi(f"http {durum}") from None
            if 400 <= durum < 500:
                raise KaliciGonderimHatasi(f"http {durum}") from None
            raise GonderimHatasi(f"http {durum}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as hata:
            log.warning(
                "whatsapp gonderim ag hatasi tur=%s alici=%s",
                type(hata).__name__,
                maskele(alici),
            )
            raise GonderimHatasi(type(hata).__name__) from None
        # AYRIŞTIRMA BAŞARI ÖLÇÜTÜ DEĞİL: buraya gelindiyse Meta 2xx döndü
        # ve mesaj KABUL EDİLDİ. Gövde okunamazsa kimlik `None` kalır ve
        # çağıran yine "gönderildi" sayar — kimliği zorunlu kılmak, Meta
        # gövde biçimini değiştirdiği gün ÇALIŞAN gönderimleri hata
        # yapardı.
        return mesaj_kimligi(govde)

    def medya_indir(self, medya_kimligi: str) -> bytes:
        """İKİ adımlı indirme: önce medyanın adresi, sonra içeriği.

        Meta doğrudan indirilebilir bir bağlantı vermez; `/{media_id}` ucu
        KISA ÖMÜRLÜ bir URL döndürür ve o URL de Bearer jetonu ister —
        ikinci istekte `Authorization` başlığı düşerse 401 gelir.

        BOYUT SINIRI OKURKEN UYGULANIR. `Content-Length` başlığına güvenmek
        yetmez: sunucu yalan söyleyebilir ya da başlığı hiç göndermeyebilir.
        Sınırı okumaya taşımak, karşı tarafın belirlediği bir bellek
        tüketimini bizim belirlediğimiz bir sınıra çevirir.

        ADRES DOĞRULANIR: ikinci istek Meta'nın GÖVDEDEN verdiği adrese
        gider ve o gövde bizim denetimimizde değildir. `https://` şartı,
        yanıtın `file://` ya da iç ağ adresi göstererek jetonlu bir isteği
        başka yere sürüklemesini engeller.
        """
        import urllib.request

        jeton = settings.whatsapp_access_token
        if jeton is None or not jeton.get_secret_value().strip():
            raise KaliciGonderimHatasi("saglayici yapilandirilmamis")
        basliklar = {"Authorization": f"Bearer {jeton.get_secret_value()}"}

        taban = (settings.whatsapp_graph_base_url or "").rstrip("/")
        surum = (settings.whatsapp_graph_version or "").strip("/")
        ustveri = self._al(
            urllib.request.Request(
                f"{taban}/{surum}/{medya_kimligi}", headers=basliklar
            )
        )
        try:
            adres = json.loads(ustveri.decode("utf-8")).get("url")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise KaliciGonderimHatasi("medya ustverisi cozumlenemedi") from None
        if not isinstance(adres, str) or not adres.startswith("https://"):
            raise KaliciGonderimHatasi("medya adresi gecersiz")

        return self._al(urllib.request.Request(adres, headers=basliklar))

    @staticmethod
    def _al(istek: Any) -> bytes:
        """Tek GET; hatayı `metin_gonder` ile AYNI sınıflara ayırır."""
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(  # noqa: S310 - adres ayar dosyasından
                istek, timeout=MEDYA_ZAMAN_ASIMI_SANIYE
            ) as yanit:
                # sinir+1 okunur: TAM sınırda duran bir okuma, dosyanın
                # bitip bitmediğini ayırt EDEMEZ.
                veri = yanit.read(MEDYA_MAKS_BAYT + 1)
        except urllib.error.HTTPError as hata:
            durum = int(hata.code)
            # Günlük yalnız DURUM taşır: medya kimliği de, indirme adresi
            # de kısa ömürlü ama yine de müşteriye ait bir işarettir.
            log.warning("whatsapp medya indirme hatasi durum=%s", durum)
            if durum == 429:
                raise KotaHatasi(f"http {durum}") from None
            if 400 <= durum < 500:
                raise KaliciGonderimHatasi(f"http {durum}") from None
            raise GonderimHatasi(f"http {durum}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as hata:
            log.warning("whatsapp medya ag hatasi tur=%s", type(hata).__name__)
            raise GonderimHatasi(type(hata).__name__) from None
        if len(veri) > MEDYA_MAKS_BAYT:
            raise KaliciGonderimHatasi("medya cok buyuk")
        if not veri:
            raise KaliciGonderimHatasi("medya bos")
        return veri


#: TEK NoOp örneği: testler `gonderilenler` listesini buradan okur. Örnek
#: paylaşıldığı için iki ardışık `saglayici_al()` AYNI listeyi görür.
_noop = NoOpSaglayici()


def yapilandirildi_mi() -> bool:
    """Meta sağlayıcısı için gereken İKİ alan da dolu mu."""
    jeton = settings.whatsapp_access_token
    return bool(
        jeton is not None
        and jeton.get_secret_value().strip()
        and (settings.whatsapp_phone_number_id or "").strip()
    )


def saglayici_al() -> MesajSaglayici:
    """Yapılandırma DOLUYSA Meta, değilse NoOp. Varsayılan ağa çıkmaz."""
    if yapilandirildi_mi():
        return MetaBulutSaglayici()
    return _noop


__all__ = [
    "GonderimHatasi",
    "KaliciGonderimHatasi",
    "KotaHatasi",
    "MEDYA_MAKS_BAYT",
    "MEDYA_ZAMAN_ASIMI_SANIYE",
    "MESAJ_MAKS",
    "MesajSaglayici",
    "MetaBulutSaglayici",
    "NoOpSaglayici",
    "ZAMAN_ASIMI_SANIYE",
    "maskele",
    "mesaj_kimligi",
    "saglayici_al",
    "yapilandirildi_mi",
]
