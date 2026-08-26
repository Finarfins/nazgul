"""PostgreSQL ikizi: Gerçek Maliyet V1 — FAZ 1 maliyet oranları.

İkizin sebebi bu modülde tek bir yapıda toplanıyor: **koşullu tekil indeksler.**

"Aynı hedefe iki AKTİF oran olamaz" iddiasının tamamı üç kısmi indekse dayanıyor
(`uq_cost_rates_company_default` / `_machine` / `_user`) ve kısmi indeks desteği
diyalekte göre değişir. SQLite'ta geçen bir iddia PostgreSQL'de geçmeyebilir —
ya da tersi. Geçmezse iki aktif oran oluşur ve hangisinin uygulandığı satır
sırasına kalır; yani MALİYET, kod hiç değişmeden sessizce değişebilir hâle gelir.

Ayrıca CHECK kısıtları (`hourly_rate >= 0`, hedef tekliği, tür-hedef tutarlılığı)
ve `NUMERIC(18,2)` aritmetiği: SQLite sayıyı REAL saklayıp float döndürür,
PostgreSQL gerçek `Decimal` verir.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "cost_rates_api", BACKEND / "tests" / "test_cost_rates_api.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_cost_rate_smoke = _contract.run_cost_rate_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The cost rate test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_cost_rates_postgresql() -> None:
    run_cost_rate_smoke(_pg_url())
