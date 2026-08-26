"""PostgreSQL ikizi: FAZ 3 dilim 1 — sezon kârına işçilik ve makine.

İkizin sebebi bu dilimde iki katmanlı:

1. **NUMERIC aritmetiği.** İşçilik/makine maliyeti `saat × donmuş oran` ile
   türetilip Decimal olarak TOPLANIYOR. SQLite `NUMERIC`i REAL'e düşürür,
   PostgreSQL gerçek `Decimal` verir; FAZ 1'de tam bu sınırda para tele float
   olarak çıkmıştı. Toplama bir de sezon bazında biriktirme eklendiği için
   sapma tek satırda değil, toplamda görünür.

2. **`IN :ids` genişletmesi.** Hem timeline hem pano faaliyetleri
   `bindparam(expanding=True)` ile sezon kimlikleri üzerinden çekiyor; bu
   genişletmenin davranışı sürücüye bağlı.

Maskenin kendisi de burada yeniden ölçülüyor: `finance` izni olmayan rolün
gördüğü toplamın tabanı iki diyalektte de aynı olmalı.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "season_cost_api", BACKEND / "tests" / "test_season_cost_with_labour.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_season_cost_smoke = _contract.run_season_cost_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The season cost test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_season_cost_with_labour_postgresql() -> None:
    run_season_cost_smoke(_pg_url())
