"""PostgreSQL ikizi: Tarla Yönetimi V1 — FAZ 2 API.

SQLite koşusu mantığı doğruluyor; burada üretim diyalektinde iki şey ayrıca
kanıtlanıyor:

* **Çapraz kiracı 404.** Bileşik yabancı anahtarlar PG'de her zaman zorunlu;
  SQLite'ta `PRAGMA foreign_keys` açılmadıkça sessizce geçerdi.
* **NUMERIC aritmetiği.** `total_cost = quantity * unit_cost` ve dekar başına
  maliyet/verim bölmeleri Decimal ile yapılıyor; SQLite REAL saklayıp float
  döndürdüğü için asıl doğrulama burada.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "farm_management_api", BACKEND / "tests" / "test_farm_management_api.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_farm_api_smoke = _contract.run_farm_api_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The farm API test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_farm_api_postgresql() -> None:
    run_farm_api_smoke(_pg_url())
