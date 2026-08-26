from __future__ import annotations

import os

import pytest

from test_late_fee_charge import run_late_fee_charge_smoke


@pytest.mark.postgresql
def test_late_fee_charge_postgresql() -> None:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    run_late_fee_charge_smoke(database_url)
