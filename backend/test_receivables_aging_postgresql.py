from __future__ import annotations

import os

import pytest

from test_receivables_aging import run_receivables_aging_smoke


def test_receivables_aging_endpoint_on_postgresql() -> None:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    run_receivables_aging_smoke(database_url)
