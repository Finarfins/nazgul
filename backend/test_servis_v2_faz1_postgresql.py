"""PostgreSQL twins of the Servis v2 FAZ-1 smokes.

Runs the exact SQLite scenarios against real PostgreSQL 16 so the dialect-
sensitive pieces get true parity coverage: the FOR UPDATE lock on the machine
row during a reading, the composite tenant FKs, NUMERIC(12,2) bounds, the
CHECK-constraint widening for SCHEDULED, and the RETURNING-based inserts. Both
smokes use the shared resilient admin login, so running them sequentially
against the job's single database is safe.
"""
from __future__ import annotations

import os

import pytest

from test_servis_v2_faz1 import run_servis_v2_faz1_smoke
from test_servis_v2_faz1_attachments import run_servis_v2_faz1_attachments_smoke


def _database_url() -> str:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    return database_url


@pytest.mark.postgresql
def test_servis_v2_faz1_postgresql() -> None:
    run_servis_v2_faz1_smoke(_database_url())


@pytest.mark.postgresql
def test_servis_v2_faz1_attachments_postgresql() -> None:
    run_servis_v2_faz1_attachments_smoke(_database_url())
