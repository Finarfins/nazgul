from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine

from test_payment_allocation_api import run_payment_allocation_api_smoke


@pytest.mark.postgresql
def test_payment_allocation_api_postgresql() -> None:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    run_payment_allocation_api_smoke(
        database_url,
        enabled=True,
        concurrent=True,
    )
    reset_engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with reset_engine.connect() as connection:
            connection.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
    finally:
        reset_engine.dispose()
    run_payment_allocation_api_smoke(
        database_url,
        enabled=False,
    )
