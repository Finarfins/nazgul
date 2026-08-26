from __future__ import annotations

import os

import pytest

from test_harman_v2c_allocation import (
    run_charge_allocation_disabled_smoke,
    run_charge_allocation_race_smoke,
    run_charge_allocation_smoke,
    run_fifo_reversal_pin_smoke,
    run_payment_update_charge_smoke,
)


def _database_url() -> str:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    return database_url


@pytest.mark.postgresql
def test_charge_allocation_postgresql() -> None:
    run_charge_allocation_smoke(_database_url())


@pytest.mark.postgresql
def test_charge_allocation_disabled_postgresql() -> None:
    run_charge_allocation_disabled_smoke(_database_url())


@pytest.mark.postgresql
def test_charge_allocation_race_postgresql() -> None:
    """Concurrency contract: no over-allocation, single winner, no deadlock."""

    run_charge_allocation_race_smoke(_database_url())


@pytest.mark.postgresql
def test_fifo_reversal_pins_allocation_time_order_postgresql() -> None:
    run_fifo_reversal_pin_smoke(_database_url())


@pytest.mark.postgresql
def test_payment_update_writes_charge_allocation_postgresql() -> None:
    run_payment_update_charge_smoke(_database_url())
