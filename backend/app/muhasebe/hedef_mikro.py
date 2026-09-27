"""Mikro Excel/CSV aktarımı (keşif §3.2 — büyük ölçüde DOĞRULANMADI).

Mikro sabit bir şema DAYATMAZ: aktarım kullanıcı tanımlı bir KOLON EŞLEME
ekranıdır. Bu yüzden başlık SABİTTİR (müşavir eşlemeyi bir kez tanımlar),
Borç ve Alacak AYRI kolondur (işaretli tek tutar kolonu YOK), kolon kümesi
ortak asgari kümedir (§3.5): fiş no, fiş tarihi, hesap kodu, açıklama, borç,
alacak. Tarih Türkçe Excel biçimi `GG.AA.YYYY`, tutar ondalık virgüllü.
Varsayım pilot bir müşavirle SINANMALIDIR.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence

from .fis import Fis
from .serilestirici import (
    Serilestirici,
    VknKaynagi,
    csv_bayt,
    fis_anahtarlari_tekil,
    hucre_metni,
    tutar_virgullu,
)

BASLIKLAR = ("Fiş No", "Fiş Tarihi", "Hesap Kodu", "Açıklama", "Borç", "Alacak")


def mikro_csv(fisler: Sequence[Fis]) -> bytes:
    fis_anahtarlari_tekil(fisler)
    satirlar = [
        (
            hucre_metni(fis.fis_no),
            fis.fis_tarihi.strftime("%d.%m.%Y"),
            s.hesap_kodu,
            hucre_metni(s.aciklama),
            tutar_virgullu(s.borc),
            tutar_virgullu(s.alacak),
        )
        for fis in fisler
        for s in fis.satirlar
    ]
    return csv_bayt(BASLIKLAR, satirlar)


class MikroSerilestirici(Serilestirici):
    hedef = "mikro"
    bicim = "csv"

    def dosyalar(
        self, fisler: Sequence[Fis], donem: str, vkn: VknKaynagi
    ) -> Iterator[tuple[str, bytes]]:
        yield f"mikro-{donem}.csv", mikro_csv(fisler)
