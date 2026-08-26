from __future__ import annotations

import os

import pytest

from test_activity_log_panel import run_activity_log_smoke


@pytest.mark.postgresql
def test_activity_log_panel_postgresql() -> None:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    run_activity_log_smoke(database_url)
