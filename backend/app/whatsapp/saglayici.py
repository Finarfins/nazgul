"""Giden WhatsApp mesajı sağlayıcısı (WA3-full).

Kaynak `nazgul_website/backend/app/whatsapp/provider.py`in **metin gönderme**
yarısı. Kaynağın `medya_indir` yarısı TAŞINMADI ve gerekçesi kapsamdır: bu
dilim medya işlemiyor (`whatsapp_inbound.media_id` dolu satır IGNORED ile
kapanır, `service.py`), yani indirilen baytı okuyacak hiçbir çağıran yok.
Kullanılmayan bir ağ yolu, ölçülmeyen bir ağ yoludur.

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
    def metin_gonder(self, alici: str, metin: str) -> None:
        """Kısa metin gönderir; hatayı SINIFLANDIRIP fırlatır.

        Sınıflandırma işçinin sözleşmesidir: :class:`GonderimHatasi`
        satırı RECEIVED'a döndürür (yeniden denenir),
        :class:`KaliciGonderimHatasi` DEAD yapar.
        """


class NoOpSaglayici(MesajSaglayici):
    """Ağ çağrısı YAPMAZ. Yapılandırılmamış kurulumun sağlayıcısı.

    Gönderilen mesajları bellekte tutar ki testler "cevap üretildi ama ağa
    çıkmadı" durumunu gerçekten doğrulayabilsin — sessiz bir `pass`, aynı
    şeyi ölçülemez biçimde yapardı.
    """

    def __init__(self) -> None:
        self.gonderilenler: list[tuple[str, str]] = []

    def metin_gonder(self, alici: str, metin: str) -> None:
        self.gonderilenler.append((alici, metin))


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

    def metin_gonder(self, alici: str, metin: str) -> None:
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
                yanit.read(_YANIT_EN_COK_BAYT)
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
    "MESAJ_MAKS",
    "MesajSaglayici",
    "MetaBulutSaglayici",
    "NoOpSaglayici",
    "ZAMAN_ASIMI_SANIYE",
    "maskele",
    "saglayici_al",
    "yapilandirildi_mi",
]
