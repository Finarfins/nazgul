"""PostgreSQL ikizi: Hayvancılık V1 — FAZ 2 API.

İkizin sebepleri somut ve bu modülde birden fazla:

* **Koşullu tekil indeks.** "Aynı küpe iki AKTİF hayvanda olamaz, satılanın
  küpesi tekrar kullanılabilir" iddiası kısmi indekse dayanıyor; kısmi indeks
  desteği diyalekte göre değişir.
* **Bileşik yabancı anahtarlar.** SQLite `PRAGMA foreign_keys` olmadan
  zorlamaz; TestClient bunu açmıyor. Çapraz kiracı bağın gerçekten imkânsız
  olduğunun kanıtı burada.
* **CHECK kısıtları** (süt kaydı hayvan XOR grup, doğum güçlüğü 1-4).
* **NUMERIC aritmetiği.** Süt litresi ve canlı ağırlık Decimal; SQLite REAL
  saklayıp float döndürür.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "herd_management_api", BACKEND / "tests" / "test_herd_management_api.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_herd_api_smoke = _contract.run_herd_api_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The herd API test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_herd_api_postgresql() -> None:
    run_herd_api_smoke(_pg_url())
