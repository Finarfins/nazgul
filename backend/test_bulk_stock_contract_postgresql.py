from __future__ import annotations

import os

import pytest

from test_bulk_stock_contract import run_bulk_stock_guards


def test_bulk_stock_selection_guards_on_postgresql() -> None:
    """PG twin of the SQLite selection/atomicity gates.

    The 404 batch rejection and the mid-batch rollback are both transaction
    behaviour, which is exactly the class SQLite can agree with by accident. The
    id filter is also built by string interpolation into an ``IN (...)`` list, so
    the two dialects must agree on it against a real server.
    """
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    run_bulk_stock_guards(database_url)
