"""H87: demo tohumunun telefonları idx ≥ 100 için de GEÇERLİ ve TEKİL.

Eski satır içi desenler (`seed_demo_data.py`, müşteri + tedarikçi) idx ≥ 100'de
`{idx:02d}` üç, `{100 + idx:03d}` dört rakama taşıyordu → ON İKİ ulusal rakam,
`consents.normalize_msisdn` `None`. Bugünkü 25 + 10 satırda görünmüyordu;
listeler 150'ye zorlandığında 300 telefonun 102'si geçersizdi (ölçüldü,
78b9ad0). Düzeltme: her tohum telefonu `_seed_telefon`dan türer; ilk 35
çıktı develop'takiyle BAYT BAYT aynıdır (F10-1 K2 kapısı ve olası
fikstürler için).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

from app.core_schema import customers, suppliers
from app.notifications.consents import normalize_msisdn
from app.whatsapp.telefon import normalize_phone
from seed_demo_data import TOHUM_TELEFON_SINIRI, _seed_telefon, build_demo

BACKEND = Path(__file__).resolve().parents[1]

# 78b9ad0'daki `build_demo` çıktısı: 25 müşteri, sonra 10 tedarikçi (id sırası).
DONDURULMUS_ILK_35 = [
    "+90 531 5501 101", "+90 532 5502 102", "+90 533 5503 103", "+90 534 5504 104",
    "+90 535 5505 105", "+90 536 5506 106", "+90 537 5507 107", "+90 538 5508 108",
    "+90 539 5509 109", "+90 540 5510 110", "+90 541 5511 111", "+90 542 5512 112",
    "+90 543 5513 113", "+90 544 5514 114", "+90 545 5515 115", "+90 546 5516 116",
    "+90 547 5517 117", "+90 548 5518 118", "+90 549 5519 119", "+90 530 5520 120",
    "+90 531 5521 121", "+90 532 5522 122", "+90 533 5523 123", "+90 534 5524 124",
    "+90 535 5525 125",
    "+90 531 4401 201", "+90 532 4402 202", "+90 533 4403 203", "+90 534 4404 204",
    "+90 535 4405 205", "+90 536 4406 206", "+90 537 4407 207", "+90 538 4408 208",
    "+90 539 4409 209", "+90 530 4410 210",
]


def _gecerli_anahtar(numara: str) -> str:
    """İki normalleştiricide de geçen, aynı rakamlara inen numaranın anahtarı."""
    siki = normalize_msisdn(numara)
    gevsek = normalize_phone(numara)
    assert siki is not None, numara
    assert gevsek == siki.lstrip("+"), (numara, gevsek, siki)
    assert len(gevsek) == 12 and gevsek.startswith("905"), (numara, gevsek)
    return siki


@pytest.mark.parametrize("n", [35, 150, 1000])
def test_TOHUM_TELEFONLARI_HER_N_ICIN_GECERLI_VE_TEKIL(n: int) -> None:
    numaralar = [_seed_telefon(idx, grup) for grup in ("musteri", "tedarikci") for idx in range(1, n + 1)]
    anahtarlar = [_gecerli_anahtar(numara) for numara in numaralar]
    assert len(set(anahtarlar)) == len(anahtarlar) == 2 * n


def test_TOHUM_TELEFONLARI_SINIRA_KADAR_TEKIL() -> None:
    """Sözleşme `idx < 10_000`: sınırın tamamı geçerli ve tekil."""
    anahtarlar = {
        _gecerli_anahtar(_seed_telefon(idx, grup))
        for grup in ("musteri", "tedarikci")
        for idx in range(1, TOHUM_TELEFON_SINIRI)
    }
    assert len(anahtarlar) == 2 * (TOHUM_TELEFON_SINIRI - 1)
    for kotu in (0, TOHUM_TELEFON_SINIRI):
        with pytest.raises(ValueError):
            _seed_telefon(kotu, "musteri")
    with pytest.raises(ValueError):
        _seed_telefon(1, "personel")


def test_ILK_35_TELEFON_DEVELOP_ILE_BAYT_BAYT_AYNI() -> None:
    uretilen = [_seed_telefon(idx, "musteri") for idx in range(1, 26)]
    uretilen += [_seed_telefon(idx, "tedarikci") for idx in range(1, 11)]
    assert uretilen == DONDURULMUS_ILK_35


def test_TOHUM_BETIGI_TELEFONU_YARDIMCIDAN_URETIYOR(tmp_path: Path) -> None:
    """Betik gerçekten yardımcıyı kullanıyor: DB'ye yazılan 35 telefon donmuş liste."""
    url = f"sqlite:///{(tmp_path / 'h87.db').as_posix()}"
    sayim = build_demo(url)
    assert (sayim["customers"], sayim["suppliers"]) == (25, 10)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            yazilan = list(conn.execute(select(customers.c.phone).order_by(customers.c.id)).scalars())
            yazilan += list(conn.execute(select(suppliers.c.phone).order_by(suppliers.c.id)).scalars())
    finally:
        engine.dispose()
    assert yazilan == DONDURULMUS_ILK_35

    kaynak = (BACKEND / "seed_demo_data.py").read_text(encoding="utf-8")
    assert kaynak.count('phone=_seed_telefon(idx, "musteri")') == 1
    assert kaynak.count('phone=_seed_telefon(idx, "tedarikci")') == 1
