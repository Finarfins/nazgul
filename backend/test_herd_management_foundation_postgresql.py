"""PostgreSQL ikizi: Hayvancılık V1 — FAZ 1 şeması.

İkizin sebebi somut ve bu modülde ÖZELLİKLE kritik:

* **Bileşik yabancı anahtarlar.** SQLite yabancı anahtarları
  `PRAGMA foreign_keys=ON` olmadan ZORLAMAZ; SQLite koşusu ancak pragma elle
  açılırsa bir şey kanıtlar. PostgreSQL'de kısıt HER ZAMAN uygulanır — çapraz
  kiracı bağın gerçekten imkânsız olduğunun tek kanıtı burası.
* **Koşullu tekil indeks** (`WHERE ear_tag IS NOT NULL AND status='ACTIVE'`).
  Kısmi indeks desteği diyalekte göre değişir; küpe numarasının yeniden
  kullanılabilmesi bu indekse dayanıyor.
* **CHECK kısıtları.** SQLite bazı CHECK'leri gevşek uygular; süt kaydının
  "hayvan YA DA grup" kuralı buna dayanıyor.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "herd_management_foundation", BACKEND / "tests" / "test_herd_management_foundation.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_schema_smoke = _contract.run_schema_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The herd schema test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_herd_schema_postgresql() -> None:
    run_schema_smoke(_pg_url())
