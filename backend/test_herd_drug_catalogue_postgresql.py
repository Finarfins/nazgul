"""PostgreSQL ikizi: Hayvan ilaç katalogu.

Kısıt: bileşik yabancı anahtar (animal_drug_treatments → catalogue);
`species=''` boş dize iki diyalektte de aynı davranmalı.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "herd_drug_catalogue", BACKEND / "tests" / "test_herd_drug_catalogue.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_catalogue_smoke = _contract.run_catalogue_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The herd drug catalogue test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_herd_drug_catalogue_postgresql() -> None:
    run_catalogue_smoke(_pg_url())
