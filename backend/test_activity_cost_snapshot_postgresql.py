"""PostgreSQL ikizi: Gerçek Maliyet V1 — FAZ 2, oranın satıra kopyalanması.

İkizin sebebi tek bir yerde toplanıyor: **NUMERIC aritmetiği ve para temsili.**

FAZ 1'de tam bu sınırda gerçek bir hata çıktı — ham `text()` sonucu PostgreSQL'de
`NUMERIC`i float'a çeviren bir JSON kodlamasına düşerken SQLite `Decimal` benzeri
bir değer veriyordu; yani aynı uç veritabanına göre FARKLI TİPTE para
döndürüyordu ve PG'de para tele **float** olarak çıkıyordu. FAZ 2 dört yeni
`NUMERIC` sütun ekliyor ve maliyeti `saat × oran` ile TÜRETİYOR, yani aynı
sınıftan sapmaya iki kat açık: hem saklanan değerlerin okunuşu hem çarpımın
ölçeği diyalekte göre kayabilir.

Ayrıca `hourly_rate` çözümü `cost_rates`in KOŞULLU TEKİL indekslerine dayanan
satırları okuyor (`status='ACTIVE'` kısmi indeksleri, migration 0052) ve kısmi
indeks desteği diyalekte göre değişir.

SQLite tek başına bu iddialar için kanıt değildir.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "activity_cost_snapshot_api", BACKEND / "tests" / "test_activity_cost_snapshot.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_cost_snapshot_smoke = _contract.run_cost_snapshot_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The activity cost snapshot test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_activity_cost_snapshot_postgresql() -> None:
    run_cost_snapshot_smoke(_pg_url())
