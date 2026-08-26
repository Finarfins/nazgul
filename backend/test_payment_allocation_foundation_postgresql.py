from __future__ import annotations

import os

import pytest

from test_payment_allocation_foundation import run_payment_allocation_smoke


@pytest.mark.postgresql
def test_payment_allocation_foundation_postgresql() -> None:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    run_payment_allocation_smoke(database_url, concurrent=True)
