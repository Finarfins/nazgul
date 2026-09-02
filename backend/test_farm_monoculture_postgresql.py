"""PostgreSQL ikizi: ÇKS tek ürün kilidi.

Boolean yok; ikizin somut sebebi `crop` serbest metin ve yıl aritmetiği.
SQLite ile PG'nin string karşılaştırması ve IN bağları aynı sonucu vermeli.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "farm_monoculture", BACKEND / "tests" / "test_farm_monoculture.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_monoculture_smoke = _contract.run_monoculture_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The farm monoculture test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_monoculture_postgresql() -> None:
    run_monoculture_smoke(_pg_url())
