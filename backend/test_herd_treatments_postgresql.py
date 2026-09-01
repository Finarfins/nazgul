"""PostgreSQL ikizi: Hayvan ilaç bekleme süresi (PR-1).

Kısıt: bileşik yabancı anahtar (animal_drug_treatments → animals ve
animal_drug_catalogue); Date diyalekt; Numeric doz sütunu. Bu üçü SQLite'ta
PRAGMA'ya bağlı ya da sessizce farklı davranır; PG'de her zaman uygulanır.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "herd_treatments", BACKEND / "tests" / "test_herd_treatments.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_treatment_smoke = _contract.run_treatment_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The herd treatment test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_herd_treatments_postgresql() -> None:
    run_treatment_smoke(_pg_url())
