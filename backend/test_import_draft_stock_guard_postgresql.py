from __future__ import annotations

import os

import pytest

from test_import_draft_stock_guard import run_guard_smoke


def test_imported_draft_sale_cannot_apply_stock_on_postgresql() -> None:
    database_url = os.getenv("TRANSACTIONS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TRANSACTIONS_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("TRANSACTIONS_TEST_DATABASE_URL must point to PostgreSQL")
    run_guard_smoke(database_url)
