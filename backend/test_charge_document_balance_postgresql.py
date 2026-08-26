from __future__ import annotations

import os

import pytest

from test_charge_document_balance import (
    run_overdue_date_smoke,
    run_parity_smoke,
    run_reversal_smoke,
    run_snapshot_shape_smoke,
    run_tenant_smoke,
)


def _database_url() -> str:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    return database_url


@pytest.mark.postgresql
def test_charge_documents_make_balance_match_receivables_postgresql() -> None:
    run_parity_smoke(_database_url())


@pytest.mark.postgresql
def test_reversed_charge_nets_out_of_balance_postgresql() -> None:
    run_reversal_smoke(_database_url())


@pytest.mark.postgresql
def test_charge_balance_is_tenant_scoped_postgresql() -> None:
    run_tenant_smoke(_database_url())


@pytest.mark.postgresql
def test_late_fee_overdue_uses_period_end_postgresql() -> None:
    run_overdue_date_smoke(_database_url())


@pytest.mark.postgresql
def test_late_fee_due_date_snapshot_is_the_order_due_date_postgresql() -> None:
    run_snapshot_shape_smoke(_database_url())
