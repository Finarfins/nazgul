"""Harman AI köprüsü — imza üretimi/doğrulaması ve istemci (WA3-core).

Kaynak ``nazgul_website/backend/app/whatsapp/danisman.py``. Oradaki
``sor`` gövdeyi ``{"numara","soru"}`` olarak POST'luyor ve sırrı DÜZ METİN
bir başlıkta (``X-Harman-Kopru-Sirri``) taşıyordu. Bu depoda sözleşme
DEĞİŞTİ: sır telin üzerinden hiç geçmez; istek imzalanır.

SÖZLEŞME (``docs/whatsapp/KOPRU_SOZLESMESI.md`` ile birebir)
--------------------------------------------------------------
Başlıklar::

    X-Harman-Kopru-Timestamp : unix saniye (ondalık tamsayı metni)
    X-Harman-Kopru-Nonce     : en az 16 bayt, küçük harf hex (>= 32 karakter)
    X-Harman-Kopru-Imza      : hex( HMAC-SHA256( sır, imza_metni ) )

    imza_metni = f"{timestamp}\\n{nonce}\\n{sha256_hex(govde)}"
    sır        = SecretStr'in UTF-8 baytları
    govde      = tel üzerinden giden ham baytlar (JSON, ensure_ascii=False)

Kabul: |simdi - timestamp| <= 300 s ve imza BİRİNCİL ya da İKİNCİL sırla
sabit süreli karşılaştırmada eşleşir. İmzalama her zaman BİRİNCİL sırla
yapılır; ikincil yalnız DOĞRULAMADA kabul edilir (sır döndürme penceresi).

NONCE ÖNBELLEĞİ BURADA DEĞİL: aynı nonce'un ikinci kez reddi ALICININ
(web ucunun) işidir; bu modül yalnız üretir ve biçimini doğrular.

FAIL-CLOSED
-----------
URL ya da birincil sır boşsa köprü KAPALIDIR: :class:`KopruIstemcisi`
``KopruKapali`` yükseltir ve HİÇBİR ağ çağrısı yapmaz. Varsayılan ayar
boştur; yani bu PR'ın kendisi hiçbir şeyi açmaz.

TAŞIMA
------
* ``acik_mi`` — kaynaktan uyarlandı (ayar okuması aynı, ikincil sır eklendi).
* ``sor``'un gövde/yanıt sözleşmesi (``{"numara","soru"}`` → ``metin``)
  ve hata yutma davranışı (HTTP/ağ hatasında ``None``) BİREBİR; taşıma
  katmanı ``urllib`` (kaynakla aynı; ``httpx`` bu deponun ÇALIŞMA ZAMANI
  bağımlılığı DEĞİL, yalnız ``requirements-dev.txt``te) ve DIŞARIDAN
  VERİLEBİLİR ki testler ağa çıkmadan ölçsün.
* ``gunluk_sinir_asildi_mi`` TAŞINMADI: ``whatsapp_inbound`` tablosunu
  sayıyordu ve bu PR'da tablo YOK.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import SecretStr

from ..config import settings

log = logging.getLogger("nazgul.whatsapp.kopru")

BASLIK_ZAMAN = "X-Harman-Kopru-Timestamp"
BASLIK_NONCE = "X-Harman-Kopru-Nonce"
BASLIK_IMZA = "X-Harman-Kopru-Imza"

#: Kabul edilen saat kayması (her iki yönde, saniye).
ZAMAN_TOLERANSI_SANIYE = 300
#: Nonce en az bu kadar BAYT taşır (hex'te iki katı karakter).
NONCE_EN_AZ_BAYT = 16
_TIMEOUT_SANIYE = 30
#: Soru uzunluğu web ucuyla aynı sınırda kırpılır.
SORU_MAKS = 500
_YANIT_EN_COK_BAYT = 64_000


class KopruKapali(RuntimeError):
    """URL ya da birincil sır yapılandırılmamış; ağa çıkılmadı."""


class ImzaGecersiz(ValueError):
    """Doğrulama düştü; ``sebep`` alanı NEDEN'i kısa bir kodla söyler."""

    def __init__(self, sebep: str) -> None:
        super().__init__(sebep)
        self.sebep = sebep


# ---------------------------------------------------------------------------
# Saf imza ilkelleri — web ucu bunları birebir yansıtır
# ---------------------------------------------------------------------------


def govde_ozeti(govde: bytes) -> str:
    return hashlib.sha256(govde).hexdigest()


def imza_metni(timestamp: int, nonce: str, govde: bytes) -> bytes:
    return f"{timestamp}\n{nonce}\n{govde_ozeti(govde)}".encode("utf-8")


def imza_uret(sir: str, timestamp: int, nonce: str, govde: bytes) -> str:
    return hmac.new(
        sir.encode("utf-8"), imza_metni(timestamp, nonce, govde), hashlib.sha256
    ).hexdigest()


def nonce_uret() -> str:
    return secrets.token_hex(NONCE_EN_AZ_BAYT)


def _nonce_bicimi_gecerli(nonce: str) -> bool:
    if len(nonce) < NONCE_EN_AZ_BAYT * 2 or len(nonce) % 2:
        return False
    try:
        bytes.fromhex(nonce)
    except ValueError:
        return False
    return nonce == nonce.lower()


def imzala(
    govde: bytes,
    *,
    sir: str,
    simdi: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Üç imza başlığını üretir (birincil sırla)."""
    zaman = int(time.time()) if simdi is None else int(simdi)
    n = nonce_uret() if nonce is None else nonce
    return {
        BASLIK_ZAMAN: str(zaman),
        BASLIK_NONCE: n,
        BASLIK_IMZA: imza_uret(sir, zaman, n, govde),
    }


def dogrula(
    *,
    timestamp: str | int,
    nonce: str,
    imza: str,
    govde: bytes,
    sirlar: Sequence[str],
    simdi: int | None = None,
    tolerans: int = ZAMAN_TOLERANSI_SANIYE,
) -> str:
    """İmzayı doğrular; döndürdüğü değer eşleşen sırrın SIRA numarasıdır
    ("birincil" / "ikincil"). Düşerse :class:`ImzaGecersiz`.

    Nonce TEKRARI burada denetlenmez (bkz. modül başlığı).
    """
    # Boş ya da yalnız boşluktan oluşan sır aday DEĞİLDİR: "  " ile
    # imzalanmış bir istek hiçbir kurulumda geçerli sayılmamalı.
    sirlar = [s.strip() for s in sirlar if s and s.strip()]
    if not sirlar:
        raise ImzaGecersiz("sir_yok")
    try:
        zaman = int(str(timestamp).strip())
    except ValueError:
        raise ImzaGecersiz("zaman_bicimi") from None
    su_an = int(time.time()) if simdi is None else int(simdi)
    if abs(su_an - zaman) > tolerans:
        raise ImzaGecersiz("zaman_kaymasi")
    if not _nonce_bicimi_gecerli(nonce):
        raise ImzaGecersiz("nonce_bicimi")
    verilen = (imza or "").strip().lower()
    for sira, sir in enumerate(sirlar):
        beklenen = imza_uret(sir, zaman, nonce, govde)
        if hmac.compare_digest(beklenen, verilen):
            return "birincil" if sira == 0 else "ikincil"
    raise ImzaGecersiz("imza_uyusmuyor")


# ---------------------------------------------------------------------------
# İstemci
# ---------------------------------------------------------------------------

#: (url, gövde, başlıklar, zaman aşımı) -> ham yanıt baytları.
Gonderici = Callable[[str, bytes, dict[str, str], int], bytes]


def _urllib_gonderici(url: str, govde: bytes, basliklar: dict[str, str], zaman_asimi: int) -> bytes:
    istek = urllib.request.Request(url, data=govde, method="POST", headers=basliklar)
    with urllib.request.urlopen(istek, timeout=zaman_asimi) as yanit:  # noqa: S310 - https URL ayar dosyasından
        return yanit.read(_YANIT_EN_COK_BAYT)


def _sir_metni(sir: SecretStr | None) -> str:
    return sir.get_secret_value().strip() if sir is not None else ""


def acik_mi() -> bool:
    return bool((settings.harman_kopru_url or "").strip() and _sir_metni(settings.harman_kopru_sirri))


@dataclass(frozen=True, slots=True)
class KopruAyari:
    url: str
    sir: str
    ikincil_sir: str = ""

    @classmethod
    def ayardan(cls) -> "KopruAyari":
        return cls(
            url=(settings.harman_kopru_url or "").strip(),
            sir=_sir_metni(settings.harman_kopru_sirri),
            ikincil_sir=_sir_metni(settings.harman_kopru_sirri_ikincil),
        )

    @property
    def acik(self) -> bool:
        # Doğrudan kurulan ayar da (ayardan() dışı) boşluğu boş sayar.
        return bool(self.url.strip() and self.sir.strip())

    @property
    def dogrulama_sirlari(self) -> tuple[str, ...]:
        return tuple(s.strip() for s in (self.sir, self.ikincil_sir) if s and s.strip())


class KopruIstemcisi:
    """Soruyu imzalayıp köprüye iletir.

    ``gonderici`` verilmezse ``urllib`` kullanılır; testler sahte bir
    gönderici verir. Köprü kapalıysa ``sor`` GÖNDERİCİYE HİÇ DOKUNMADAN
    :class:`KopruKapali` yükseltir.
    """

    def __init__(self, ayar: KopruAyari | None = None, gonderici: Gonderici | None = None) -> None:
        self.ayar = ayar if ayar is not None else KopruAyari.ayardan()
        self._gonderici = gonderici if gonderici is not None else _urllib_gonderici

    def govde_hazirla(self, telefon: str, soru: str) -> bytes:
        return json.dumps(
            {"numara": telefon, "soru": soru[:SORU_MAKS]}, ensure_ascii=False
        ).encode("utf-8")

    def sor(self, telefon: str, soru: str) -> str | None:
        """Gönderilecek metni ya da ``None`` (cevap üretilemedi) döner.

        İstisna dışarı SIZMAZ (kaynakla aynı): HTTP/ağ hatası ``None``dır.
        Tek istisna :class:`KopruKapali` — o bir yapılandırma hatasıdır ve
        çağıranın onu "cevap yok" sanmaması gerekir.
        """
        if not self.ayar.acik:
            raise KopruKapali("harman_kopru_url ya da harman_kopru_sirri boş")

        govde = self.govde_hazirla(telefon, soru)
        basliklar = {"Content-Type": "application/json", **imzala(govde, sir=self.ayar.sir)}
        try:
            ham = self._gonderici(self.ayar.url, govde, basliklar, _TIMEOUT_SANIYE)
        except urllib.error.HTTPError as hata:
            log.warning("harman kopru: http %s", hata.code)
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as hata:
            log.warning("harman kopru: ag hatasi tur=%s", type(hata).__name__)
            return None

        try:
            veri = json.loads(ham.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning("harman kopru: yanit cozumlenemedi")
            return None

        metin = veri.get("metin") if isinstance(veri, dict) else None
        if not isinstance(metin, str) or not metin.strip():
            return None
        return metin.strip()


__all__ = [
    "BASLIK_IMZA",
    "BASLIK_NONCE",
    "BASLIK_ZAMAN",
    "ImzaGecersiz",
    "KopruAyari",
    "KopruIstemcisi",
    "KopruKapali",
    "NONCE_EN_AZ_BAYT",
    "SORU_MAKS",
    "ZAMAN_TOLERANSI_SANIYE",
    "acik_mi",
    "dogrula",
    "govde_ozeti",
    "imza_metni",
    "imza_uret",
    "imzala",
    "nonce_uret",
]
