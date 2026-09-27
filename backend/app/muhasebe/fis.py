"""Kanonik muhasebe fişi — SAF, SQL'siz, oturumsuz (F9-5a, keşif §3.6).

`app/mustahsil.py`nin duruşu: aritmetik ve kapılar, onları üreten sorgudan
BAĞIMSIZ sınanabilmelidir. Bu modül bir `Session` görmez; `kaynak.py` belge
satırlarını okur, buradaki yapıcılarla fiş kurar ve buradaki kapıdan geçirir.

--- KAPILAR (e-Defter şartı + çift taraflı kayıt) ----------------------------

1. En az İKİ satır.
2. Her satırda TAM OLARAK bir taraf > 0; negatif tutar YOK; tutar kuruşa
   tam (`Decimal("0.01")`in katı). Sıfır satır yapıcıda DÜŞÜRÜLÜR, kapıya
   gelmez (`SatirToplayici`).
3. Σ borç == Σ alacak, KURUŞU KURUŞUNA (`Decimal` eşitliği; tolerans YOK).
4. K11: `belge_tipi`, `belge_no`, `belge_tarihi`, `odeme_yontemi` DOLU.

Kapıdan geçemeyen fiş YAZILMAZ (`ayikla`): çağıran onu `uyarilar`da
adıyla ve sebebiyle listeler. "Neredeyse dengeli" bir fişi yuvarlayıp
geçirmek, müşavirin programında dengeli GÖRÜNEN yanlış bir kayıt üretirdi.

--- FİŞ NO BELİRLENİMCİDİR --------------------------------------------------

`fis_no` belgenin KİMLİĞİNDEN türer, sıradan DEĞİL: aynı belge her aktarımda
aynı numarayı alır (keşif §6.3 — durumsuz yeniden aktarım). Önekler kapalı
bir sözlüktür (`FIS_ONEKLERI`); iki kaynak aynı öneki PAYLAŞMAZ, yani iki
farklı belge aynı fiş numarasını ALAMAZ (Luca'nın (no, tarih) birleştirmesi
iki faturayı tek fişte birleştirirdi — keşif §9 risk 2).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..money import MONEY_QUANTUM

SIFIR = Decimal("0.00")

#: Kaynak → fiş no öneki. KAPALI küme; iadeler keşifte önek almamıştı ve
#: brifingin dört önekiyle ÇAKIŞMAYAN iki önek aldı (`SIA`/`AIA`).
FIS_ONEKLERI: dict[str, str] = {
    "SATIS": "SAT",
    "ALIS": "ALS",
    "SATIS_IADE": "SIA",
    "ALIS_IADE": "AIA",
    "SERVIS_FATURA": "SVF",
    "MUSTAHSIL": "MM",
}

CARI_TIPLERI = ("CUSTOMER", "SUPPLIER")


class FisHatasi(ValueError):
    """Kapıdan geçemeyen fiş; `nedenler` insan okunur Türkçe."""

    def __init__(self, fis: "Fis", nedenler: list[str]) -> None:
        super().__init__(f"{fis.fis_no}: " + "; ".join(nedenler))
        self.fis = fis
        self.nedenler = list(nedenler)


@dataclass(frozen=True)
class FisSatiri:
    hesap_kodu: str
    borc: Decimal
    alacak: Decimal
    aciklama: str
    kdv_orani: Decimal | None = None
    cari_tipi: str | None = None
    cari_id: int | None = None


@dataclass(frozen=True)
class Fis:
    fis_no: str
    fis_tarihi: date
    belge_tipi: str
    belge_no: str
    belge_tarihi: date
    odeme_yontemi: str
    kaynak: str
    satirlar: tuple[FisSatiri, ...]
    # Açıklamaya GÖMÜLMEZ: VKN rol maskesine tabidir (SEC-3b) ve serbest
    # metin maskelenemez. Önizleme onu `maskele_cari`den geçirir.
    cari_ad: str | None = None
    cari_vkn: str | None = None

    @property
    def borc_toplami(self) -> Decimal:
        return sum((s.borc for s in self.satirlar), SIFIR)

    @property
    def alacak_toplami(self) -> Decimal:
        return sum((s.alacak for s in self.satirlar), SIFIR)


def fis_no(kaynak: str, kimlik: object) -> str:
    """Belirlenimci fiş no: `SAT-<order_id>`, `SVF-<invoice_no>`…"""
    try:
        onek = FIS_ONEKLERI[kaynak]
    except KeyError as exc:
        raise ValueError(f"Bilinmeyen fiş kaynağı: {kaynak}") from exc
    metin = str(kimlik).strip() if kimlik is not None else ""
    if not metin:
        raise ValueError(f"{kaynak} fişinin belge kimliği boş")
    return f"{onek}-{metin}"


def _kurusa_tam(tutar: Decimal) -> bool:
    return tutar.is_finite() and tutar == tutar.quantize(MONEY_QUANTUM)


def denge_hatalari(fis: Fis) -> list[str]:
    """Kapı ihlallerinin listesi; boş liste = fiş yazılabilir."""
    nedenler: list[str] = []
    if len(fis.satirlar) < 2:
        nedenler.append(f"en az iki satır gerekir ({len(fis.satirlar)} satır)")
    for sira, satir in enumerate(fis.satirlar, start=1):
        if not (_kurusa_tam(satir.borc) and _kurusa_tam(satir.alacak)):
            nedenler.append(f"satır {sira}: tutar kuruşa tam değil")
            continue
        if satir.borc < 0 or satir.alacak < 0:
            nedenler.append(f"satır {sira}: negatif tutar")
        elif (satir.borc > 0) == (satir.alacak > 0):
            nedenler.append(f"satır {sira}: tam olarak bir taraf sıfırdan büyük olmalı")
        if not satir.hesap_kodu:
            nedenler.append(f"satır {sira}: hesap kodu boş")
    borc, alacak = fis.borc_toplami, fis.alacak_toplami
    if borc != alacak:
        nedenler.append(f"borç {borc} ≠ alacak {alacak} (fark {borc - alacak})")
    for ad in ("belge_tipi", "belge_no", "odeme_yontemi"):
        if not str(getattr(fis, ad) or "").strip():
            nedenler.append(f"{ad} boş (K11)")
    if fis.belge_tarihi is None:
        nedenler.append("belge_tarihi boş (K11)")
    return nedenler


def dogrula(fis: Fis) -> Fis:
    """Kapıdan geçen fişi AYNEN döner, geçemeyende `FisHatasi` yükseltir."""
    nedenler = denge_hatalari(fis)
    if nedenler:
        raise FisHatasi(fis, nedenler)
    return fis


def ayikla(fisler: Iterable[Fis]) -> tuple[list[Fis], list[FisHatasi]]:
    """(yazılacak fişler, reddedilenler). Dengesiz fiş YAZILMAZ."""
    gecen: list[Fis] = []
    reddedilen: list[FisHatasi] = []
    for fis in fisler:
        try:
            gecen.append(dogrula(fis))
        except FisHatasi as hata:
            reddedilen.append(hata)
    return gecen, reddedilen


@dataclass
class SatirToplayici:
    """Aynı (hesap, taraf, oran, cari) satırlarını birleştirir; sıfırı düşürür.

    Negatif tutar KARŞI TARAFA geçer (ters kayıt): eski veride negatif bir
    kalem, fişi kapıdan düşürmek yerine muhasebedeki doğal karşılığıyla yazılır.
    Sıra İLK EKLENİŞ sırasıdır — aynı veri aynı fişi üretir.
    """

    _sira: list[tuple] = field(default_factory=list)
    _tutar: dict[tuple, Decimal] = field(default_factory=dict)
    _aciklama: dict[tuple, str] = field(default_factory=dict)

    def ekle(
        self,
        hesap_kodu: str,
        taraf: str,
        tutar: Decimal,
        aciklama: str,
        *,
        kdv_orani: Decimal | None = None,
        cari_tipi: str | None = None,
        cari_id: int | None = None,
    ) -> None:
        if taraf not in ("B", "A"):
            raise ValueError(f"taraf B ya da A olmalı: {taraf}")
        if tutar < 0:
            taraf, tutar = ("A" if taraf == "B" else "B"), -tutar
        if tutar == 0:
            return
        anahtar = (hesap_kodu, taraf, kdv_orani, cari_tipi, cari_id)
        if anahtar not in self._tutar:
            self._sira.append(anahtar)
            self._tutar[anahtar] = SIFIR
            self._aciklama[anahtar] = aciklama
        self._tutar[anahtar] += tutar

    def satirlar(self) -> tuple[FisSatiri, ...]:
        cikti = []
        for anahtar in self._sira:
            hesap, taraf, oran, cari_tipi, cari_id = anahtar
            tutar = self._tutar[anahtar]
            if tutar == 0:
                continue
            cikti.append(
                FisSatiri(
                    hesap_kodu=hesap,
                    borc=tutar if taraf == "B" else SIFIR,
                    alacak=tutar if taraf == "A" else SIFIR,
                    aciklama=self._aciklama[anahtar],
                    kdv_orani=oran,
                    cari_tipi=cari_tipi,
                    cari_id=cari_id,
                )
            )
        return tuple(cikti)


def _tutar_metni(deger: Decimal | None) -> str | None:
    return None if deger is None else format(deger, "f")


def fis_sozlugu(fis: Fis) -> dict:
    """Kanonik JSON görünümü: tutarlar `str` (Decimal, sabit biçim), tarihler ISO.

    Cari ad/VKN BURADA YOK: onları rol maskesinden geçirmek çağıranın
    (uç) işidir; saf modül rol bilmez.
    """
    return {
        "fis_no": fis.fis_no,
        "fis_tarihi": fis.fis_tarihi.isoformat(),
        "belge_tipi": fis.belge_tipi,
        "belge_no": fis.belge_no,
        "belge_tarihi": fis.belge_tarihi.isoformat(),
        "odeme_yontemi": fis.odeme_yontemi,
        "kaynak": fis.kaynak,
        "borc_toplami": _tutar_metni(fis.borc_toplami),
        "alacak_toplami": _tutar_metni(fis.alacak_toplami),
        "satirlar": [
            {
                "hesap_kodu": s.hesap_kodu,
                "borc": _tutar_metni(s.borc),
                "alacak": _tutar_metni(s.alacak),
                "aciklama": s.aciklama,
                "kdv_orani": _tutar_metni(s.kdv_orani),
                "cari_tipi": s.cari_tipi,
                "cari_id": s.cari_id,
            }
            for s in fis.satirlar
        ],
    }
