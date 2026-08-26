"""PostgreSQL ikizi: Tarla Yönetimi V1 — FAZ 7 parsel zaman çizelgesi.

Bu ikizin somut sebebi var: uç `IN :ids` genişleyen bind parametresi ve
korele alt sorgu kullanıyor, ayrıca NUMERIC toplamlarını Decimal'e çeviriyor.

* Genişleyen bind (`bindparam(expanding=True)`) iki sürücüde farklı SQL
  üretir; boş listede sözdizimi hatası vermemesi ölçülmeli (sezonsuz parsel
  senaryosu bunu kapsıyor).
* NUMERIC toplamları PG'den `Decimal`, SQLite'tan `float`/`str` gelir —
  `total_cost`/`yield_per_decare` iddiaları bu yüzden diyalekte duyarlı.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "farm_parcel_timeline", BACKEND / "tests" / "test_farm_parcel_timeline.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_timeline_smoke = _contract.run_timeline_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The parcel timeline test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_parcel_timeline_postgresql() -> None:
    run_timeline_smoke(_pg_url())
