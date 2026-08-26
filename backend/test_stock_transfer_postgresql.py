"""Şubeler Arası Parça Transferi — PostgreSQL 16 parity twin.

Runs the exact SQLite scenario (``_SMOKE`` from test_v3_stock_transfer) against
real PostgreSQL so the engine-sensitive parts hold on both: the guarded atomic
``UPDATE ... WHERE quantity-:d >= 0`` used for both transfer legs, the
COALESCE/boolean predicates in the cross-branch visibility query, NUMERIC(18,4)
Decimal round-tripping, and the ROLLBACK of a partially-applied multi-line
transfer. It then asserts the CHECK constraint added by migration 0020 is
really live on this database. Auto-discovered by the CI ``test_*postgresql*.py``
glob.
"""

from __future__ import annotations

import os

import pytest


def _postgres_url() -> str:
    # Prefer a dedicated URL, but fall back to the shared APP url the CI parity
    # job already exports so this test runs without touching the workflow.
    database_url = os.environ.get("STOCK_TRANSFER_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip(
            "STOCK_TRANSFER_TEST_DATABASE_URL / APP_TEST_DATABASE_URL is not configured"
        )
    # Guard the whole point of the twin: a misconfigured URL must fail loudly
    # instead of silently re-running the SQLite scenario on SQLite.
    assert database_url.startswith("postgresql"), (
        "the PostgreSQL twin must not run on another engine: " + database_url
    )
    return database_url


@pytest.mark.postgresql
def test_stock_transfer_flow_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", _postgres_url())

    # Import AFTER DATABASE_URL is set: pulling _SMOKE at module level would
    # import the app package (via app.auth) during collection and freeze the
    # engine on the default SQLite URL, so the "PG" run would silently test
    # SQLite instead (and trip over state left by earlier twin files).
    from test_v3_stock_transfer import _SMOKE

    # The scenario is self-contained (creates its own company, warehouses and
    # products) and asserts inline; a clean exec reproduces the SQLite run.
    exec(compile(_SMOKE, "<stock_transfer_smoke>", "exec"), {})


@pytest.mark.postgresql
def test_stock_transfer_check_constraint_postgresql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration 0020's self-transfer CHECK must be enforced by the database.

    The API already rejects source == target with 400; this proves the schema
    refuses the same document even when it is written around the API.
    """
    monkeypatch.setenv("DATABASE_URL", _postgres_url())

    import sqlalchemy as sa

    # Importing app.main is what applies Alembic to head (migration 0020 adds
    # the constraint); the schema is dropped and recreated per file in CI, so
    # this test must not assume an earlier test already bootstrapped it.
    import app.main  # noqa: F401
    from app.db import engine

    with engine.connect() as connection:
        constraints = {
            row["name"]
            for row in sa.inspect(connection).get_check_constraints("stock_transfers")
        }
    assert "ck_stock_transfers_source_not_target" in constraints, constraints

    # A self-transfer written straight to the table must be refused by the DB.
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            company = connection.execute(
                sa.text(
                    "INSERT INTO companies (name,is_active,created_at) "
                    "VALUES ('Transfer CHECK', TRUE, CURRENT_TIMESTAMP) RETURNING id"
                )
            ).scalar_one()
            warehouse = connection.execute(
                sa.text(
                    "INSERT INTO warehouses (company_id,name,is_default,is_active,created_at) "
                    "VALUES (:cid,'CHECK Depo', FALSE, TRUE, CURRENT_TIMESTAMP) RETURNING id"
                ),
                {"cid": company},
            ).scalar_one()
            connection.execute(
                sa.text(
                    """INSERT INTO stock_transfers(
                    company_id,transfer_date,source_warehouse_id,target_warehouse_id,
                    status,created_at)
                    VALUES(:cid,'2026-07-24',:wid,:wid,'completed',CURRENT_TIMESTAMP)"""
                ),
                {"cid": company, "wid": warehouse},
            )
