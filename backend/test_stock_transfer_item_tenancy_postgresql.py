"""PostgreSQL ikizi: depo transferi satırının kiracı bağı.

İkizin sebebi bu dilimde doğrudan ve ölçülebilir:

1. **Yabancı anahtar ZORLAMASI diyalekte bağlı.** SQLite yabancı anahtarları
   ``PRAGMA foreign_keys=ON`` olmadan hiç zorlamaz ve pragma BAĞLANTI
   başınadır; PostgreSQL her zaman zorlar. SQLite'ta yeşil olan bir kapı,
   pragma'sız bir bağlantıda sessizce vakum olabilir — üretim PostgreSQL
   olduğu için asıl cevap burada.

2. **Yeniden kurulum yalnız SQLite'ta oluyor.** ``batch_alter_table``
   PostgreSQL'de ``ALTER TABLE ... ADD CONSTRAINT``a düşer, tablo yeniden
   kurulmaz. Yani iki diyalekt migration'ı FARKLI yollarla uyguluyor ve
   ikisinin de aynı son şemaya varması ayrıca kanıtlanmalı.

3. **``ck_stock_transfers_source_not_target`` yalnız PostgreSQL'de var**
   (bkz. 0020: SQLite'ta canlı transfer defterini yeniden kurmamak için
   bilinçli atlanmış). Ebeveyne UNIQUE eklerken bu CHECK'in yerinde kalması
   yalnız burada ölçülebilir.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "transfer_tenancy_contract", BACKEND / "tests" / "test_stock_transfer_item_tenancy.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_transfer_tenancy_smoke = _contract.run_transfer_tenancy_smoke
run_transfer_tenancy_migration = _contract.run_transfer_tenancy_migration


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The transfer tenancy test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_transfer_item_tenancy_postgresql() -> None:
    run_transfer_tenancy_smoke(_pg_url())


@pytest.mark.postgresql
def test_transfer_item_migration_preserves_rows_postgresql() -> None:
    run_transfer_tenancy_migration(_pg_url())
