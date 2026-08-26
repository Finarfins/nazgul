"""PostgreSQL ikizi: Tarla Yönetimi V1 — FAZ 8 hasat geliri.

İkizin somut sebebi: gelir toplamları NUMERIC(18,2) üzerinden yapılıyor ve
"gelir girilmemiş" ayrımı `IS NOT NULL` sayımıyla kuruluyor.

* SQLite NUMERIC'i float/str olarak geri verir, PostgreSQL `Decimal`. Toplam
  ve çıkarma (brüt katkı) bu yüzden diyalekte duyarlı.
* `SUM(...)` boş kümede iki sürücüde de NULL döner ama `COALESCE` sarmalaması
  ve `COUNT(*)` ile birlikte davranışın aynı olduğu ölçülmeli — çünkü SIFIR ile
  BİLİNMİYOR ayrımının tamamı buna dayanıyor ve yanlış tarafa düşerse
  satılmamış bir sezon zarar etmiş görünür.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "farm_harvest_revenue", BACKEND / "tests" / "test_farm_harvest_revenue.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_revenue_smoke = _contract.run_revenue_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The harvest revenue test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_harvest_revenue_postgresql() -> None:
    run_revenue_smoke(_pg_url())
