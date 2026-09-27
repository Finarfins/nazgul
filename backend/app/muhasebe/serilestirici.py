"""Hedef serileştiricilerin ortak tabanı (F9-5b, keşif §3.6, §6.4).

`app/einvoice/provider.py`nin biçimi: soyut taban + hedef başına somut sınıf.
Serileştirici SAFTIR — `Session` görmez, rol bilmez. Girdi, `kaynak.donem_oku`
ile okunmuş ve `fis.ayikla` kapısından GEÇMİŞ fişlerdir; dengesiz fiş buraya
hiç gelmez (uç onu 409 ile akıştan ÖNCE reddeder).

--- ÇIKTI -------------------------------------------------------------------

`dosyalar()` `(zip içindeki ad, bayt)` çiftleri üretir. Aylık bir KOBİ dönemi
binlerle ölçülür (keşif §6.1); tek dosya (Luca'da tek PARÇA, ≤50 fiş) bellekte
kurulur, zip ise dosya dosya AKAR.

--- CSV (keşif §6.4) ----------------------------------------------------------

UTF-8 BOM + `;` ayraç + `\\r\\n`: Türkçe Excel BOM'suz UTF-8'i cp1254 okur ve
`,` ayraçlı dosyayı ondalık virgülle karıştırır. Tutarlar bu yüzden ONDALIK
VİRGÜLLE yazılır (`1234,50`); binlik ayraç YOK. Kanonik JSON `.` taşır.

--- FORMÜL ENJEKSİYONU --------------------------------------------------------

Açıklama cari ADINI, belge no kullanıcının yazdığı metni taşır. `=`, `+`,
`-`, `@` (ve sekme/CR) ile başlayan hücreyi Excel formül sayar; openpyxl ise
`=` ile başlayan dizeyi DOĞRUDAN formül olarak yazar. Böyle bir METİN hücresi
`'` önekiyle yazılır. Tutar ve hesap kodu metin değildir, önek almaz.
"""
from __future__ import annotations

import csv
import io
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Sequence
from decimal import Decimal

from .fis import Fis

#: Fiş → rol maskesinden geçmiş cari VKN (ya da `None`). Serileştirici rol
#: BİLMEZ; maskeyi uç verir (SEC-3b).
VknKaynagi = Callable[[Fis], "str | None"]

_FORMUL_BASLARI = ("=", "+", "-", "@", "\t", "\r")
BOM = "﻿"


def hucre_metni(deger: object) -> str:
    """Metin hücresi: `None` → boş, formül gibi başlayan → `'` önekli."""
    metin = "" if deger is None else str(deger)
    if metin.startswith(_FORMUL_BASLARI):
        return "'" + metin
    return metin


def tutar_virgullu(tutar: Decimal) -> str:
    """`Decimal("1234.50")` → `"1234,50"` (sabit biçim, binlik ayraç yok)."""
    return format(tutar, "f").replace(".", ",")


def csv_bayt(basliklar: Sequence[str], satirlar: Iterable[Sequence[str]]) -> bytes:
    tampon = io.StringIO(newline="")
    tampon.write(BOM)
    yazici = csv.writer(tampon, delimiter=";", lineterminator="\r\n")
    yazici.writerow(basliklar)
    yazici.writerows(satirlar)
    return tampon.getvalue().encode("utf-8")


def fis_anahtarlari_tekil(fisler: Sequence[Fis]) -> None:
    """İki AYRI fiş aynı fiş numarasını taşıyamaz (keşif §3.1).

    Luca `(Fiş No, Fiş Tarihi)` aynı satırları TEK fişe SESSİZCE birleştirir;
    aynı numara farklı tarihle ise iki belgeyi müşavirin ekranında aynı adla
    gösterir. `fis.FIS_ONEKLERI` bunu kaynakta önler; bu, çıktı kapısıdır.
    """
    gorulen: set[str] = set()
    for fis in fisler:
        if fis.fis_no in gorulen:
            raise ValueError(f"Aynı fiş numarası iki belgede: {fis.fis_no}")
        gorulen.add(fis.fis_no)


class Serilestirici(ABC):
    #: Uçtaki `target` değeri.
    hedef: str
    #: Hedef dosyalarının biçimi (`format` sorgu parametresiyle eşleşir).
    bicim: str

    @abstractmethod
    def dosyalar(
        self, fisler: Sequence[Fis], donem: str, vkn: VknKaynagi
    ) -> Iterator[tuple[str, bytes]]:
        """Hedefe özgü dosyalar; kanonik çift bunlara DAHİL DEĞİL."""
