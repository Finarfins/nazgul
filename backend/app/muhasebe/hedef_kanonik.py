"""Kanonik çıktı: `fisler.json` + `fisler.csv` — HER aktarımda üretilir (keşif §6.4).

Hedef serileştiricisi doğrulanmamış bir müşteri için güvenli geri dönüştür
(Luca şablon başlıkları ve Mikro eşlemesi DOĞRULANMADI, keşif §3.1-3.2).

`fisler.json` YALNIZ `fis_sozlugu`nu taşır — cari adı/VKN YOK. Böylece
`icerik_sha256` (keşif §6.3, durumsuz idempotency) çağıranın ROLÜNDEN
bağımsızdır: aynı dönem aynı veriyle her rolde aynı özeti verir. Rol maskesi
yalnız CSV'nin `cari_vkn` kolonundadır.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence

from .fis import Fis, fis_sozlugu
from .serilestirici import Serilestirici, VknKaynagi, csv_bayt, hucre_metni, tutar_virgullu

JSON_ADI = "fisler.json"
CSV_ADI = "fisler.csv"
CSV_BASLIKLARI = (
    "fis_no", "tarih", "hesap_kodu", "aciklama", "borc", "alacak", "cari_vkn", "kaynak",
)


def kanonik_json(fisler: Sequence[Fis]) -> bytes:
    """Sıralı, sabit biçimli JSON: aynı fişler → aynı bayt."""
    govde = [fis_sozlugu(f) for f in fisler]
    return (json.dumps(govde, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def icerik_sha256(json_bayt: bytes) -> str:
    return hashlib.sha256(json_bayt).hexdigest()


def kanonik_csv(fisler: Sequence[Fis], vkn: VknKaynagi) -> bytes:
    satirlar = []
    for fis in fisler:
        cari_vkn = hucre_metni(vkn(fis))
        for s in fis.satirlar:
            satirlar.append((
                hucre_metni(fis.fis_no),
                fis.fis_tarihi.isoformat(),
                s.hesap_kodu,
                hucre_metni(s.aciklama),
                tutar_virgullu(s.borc),
                tutar_virgullu(s.alacak),
                cari_vkn,
                fis.kaynak,
            ))
    return csv_bayt(CSV_BASLIKLARI, satirlar)


class KanonikSerilestirici(Serilestirici):
    """`target=canonical`: hedefe özgü EK dosya yok; kanonik çift yeter."""

    hedef = "canonical"
    bicim = "csv"

    def dosyalar(
        self, fisler: Sequence[Fis], donem: str, vkn: VknKaynagi
    ) -> Iterator[tuple[str, bytes]]:
        return iter(())
