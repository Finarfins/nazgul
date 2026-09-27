"""Luca Excel veri aktarımı (keşif §3.1 — ÖLÇÜLDÜ, kısmen).

* Zorunlu: Fiş No, Fiş Tarihi, Hesap Kodu, Borç, Alacak.
* Tek yüklemede EN FAZLA 50 fiş → dosya 50 FİŞLİK parçalara bölünür
  (`luca-<YYYY-AA>-001.xlsx`, `-002`…). Bölme FİŞ sınırındadır: bir fişin
  satırları iki parçaya DAĞILMAZ.
* Luca `Fiş No + Fiş Tarihi` aynı satırları TEK fişe birleştirir; fiş no
  belirlenimcidir ve tekildir (`serilestirici.fis_anahtarlari_tekil`).
* Şablonun tam başlık sırası halka açık DEĞİL (DOĞRULANMADI); zorunlu beşe
  açıklama ve e-Defter'in dört belge alanı (§3.4-3.5, K11) eklenir. Pilot
  müşavirle sınanmalıdır.

Tutar hücreleri SAYI (`Decimal`), tarih hücreleri TARİH yazılır — Luca'nın
kendi şablonu Excel hücresi bekler, metin değil.
"""
from __future__ import annotations

import io
from collections.abc import Iterator, Sequence

from openpyxl import Workbook

from .fis import Fis
from .serilestirici import Serilestirici, VknKaynagi, fis_anahtarlari_tekil, hucre_metni

PARCA_FIS_TAVANI = 50
BASLIKLAR = (
    "Fiş No", "Fiş Tarihi", "Hesap Kodu", "Açıklama", "Borç", "Alacak",
    "Belge No", "Belge Tarihi", "Belge Türü", "Ödeme Yöntemi",
)


def parcalar(fisler: Sequence[Fis], tavan: int = PARCA_FIS_TAVANI) -> list[Sequence[Fis]]:
    return [fisler[i:i + tavan] for i in range(0, len(fisler), tavan)]


def _xlsx(fisler: Sequence[Fis]) -> bytes:
    kitap = Workbook(write_only=True)
    sayfa = kitap.create_sheet("Fisler")
    sayfa.append(BASLIKLAR)
    for fis in fisler:
        for s in fis.satirlar:
            sayfa.append((
                hucre_metni(fis.fis_no),
                fis.fis_tarihi,
                s.hesap_kodu,
                hucre_metni(s.aciklama),
                s.borc,
                s.alacak,
                hucre_metni(fis.belge_no),
                fis.belge_tarihi,
                hucre_metni(fis.belge_tipi),
                hucre_metni(fis.odeme_yontemi),
            ))
    tampon = io.BytesIO()
    kitap.save(tampon)
    return tampon.getvalue()


class LucaSerilestirici(Serilestirici):
    hedef = "luca"
    bicim = "xlsx"

    def dosyalar(
        self, fisler: Sequence[Fis], donem: str, vkn: VknKaynagi
    ) -> Iterator[tuple[str, bytes]]:
        fis_anahtarlari_tekil(fisler)
        for sira, parca in enumerate(parcalar(fisler), start=1):
            yield f"luca-{donem}-{sira:03d}.xlsx", _xlsx(parca)
