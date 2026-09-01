"""PostgreSQL ikizi: tarlaya giriş yasağı yazma kilidi.

FAZ 5'teki ders: `performed_at` SQLite'ta str, PG'de datetime. `_yerel_gun`
ikisini de kabul ediyor; yazma kilidi aynı hesabı kullandığı için ikiz şart.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "farm_reentry_enforcement", BACKEND / "tests" / "test_farm_reentry_enforcement.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_reentry_smoke = _contract.run_reentry_smoke
run_unfiltered_field_safety_smoke = _contract.run_unfiltered_field_safety_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The farm reentry test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_reentry_enforcement_postgresql() -> None:
    run_reentry_smoke(_pg_url())


@pytest.mark.postgresql
def test_unfiltered_field_safety_postgresql() -> None:
    run_unfiltered_field_safety_smoke(_pg_url())
