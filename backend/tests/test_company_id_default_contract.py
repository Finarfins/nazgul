from __future__ import annotations

import importlib.util
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app import core_schema, tenancy, workflow


BASELINE_TABLE_PAYLOADS = {
    "customers": {"name": "Tenant contract customer"},
    "suppliers": {"name": "Tenant contract supplier"},
    "products": {"name": "Tenant contract product"},
    "orders": {"customer_id": 1, "order_date": "2026-08-11"},
    "purchases": {"supplier_id": 1, "purchase_date": "2026-08-11"},
    "payments": {
        "entity_type": "customer",
        "entity_id": 1,
        "amount": 1,
        "payment_date": "2026-08-11",
    },
    "stock_movements": {
        "product_id": 1,
        "movement_type": "contract",
        "quantity": 1,
        "movement_date": "2026-08-11",
    },
    "quotes": {"customer_id": 1, "quote_date": "2026-08-11"},
    "returns": {
        "return_type": "sale",
        "entity_id": 1,
        "return_date": "2026-08-11",
    },
    "income_expenses": {
        "txn_date": "2026-08-11",
        "txn_type": "income",
        "amount": 1,
    },
}

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260811_0054_drop_baseline_company_defaults.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("company_default_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_insert_fails_for_company_id(connection: Connection, statement) -> None:
    savepoint = connection.begin_nested()
    try:
        with pytest.raises(IntegrityError, match="company_id"):
            connection.execute(statement)
    finally:
        savepoint.rollback()


@contextmanager
def _migration_connection(tmp_path: Path) -> Iterator[Connection]:
    if os.environ.get("REQUIRE_PG") == "1":
        from app.db import engine

        schema = f"company_default_{uuid.uuid4().hex}"
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            try:
                yield connection
            finally:
                connection.execute(text("SET LOCAL search_path TO public"))
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        return

    engine = create_engine(f"sqlite:///{tmp_path / 'company-default-migration.db'}")
    try:
        with engine.begin() as connection:
            yield connection
    finally:
        engine.dispose()


def test_clean_install_rejects_missing_company_id_for_all_baseline_tables() -> None:
    engine = create_engine("sqlite:///:memory:")
    core_schema.metadata.create_all(engine)

    try:
        with engine.begin() as connection:
            for table_name, payload in BASELINE_TABLE_PAYLOADS.items():
                table = core_schema.metadata.tables[table_name]
                assert table.c.company_id.server_default is None
                _assert_insert_fails_for_company_id(
                    connection,
                    table.insert().values(**payload),
                )
    finally:
        engine.dispose()


def test_explicit_non_default_company_id_remains_supported() -> None:
    engine = create_engine("sqlite:///:memory:")
    core_schema.metadata.create_all(engine)

    try:
        with engine.begin() as connection:
            for table_name, payload in BASELINE_TABLE_PAYLOADS.items():
                table = core_schema.metadata.tables[table_name]
                result = connection.execute(
                    table.insert().values(company_id=2, **payload)
                )
                stored_company_id = connection.execute(
                    select(table.c.company_id).where(table.c.id == result.inserted_primary_key[0])
                ).scalar_one()
                assert stored_company_id == 2
    finally:
        engine.dispose()


def test_upgrade_drops_defaults_and_preserves_existing_tenant_rows(tmp_path: Path) -> None:
    migration = _load_migration()
    assert tuple(BASELINE_TABLE_PAYLOADS) == migration.BASELINE_TENANT_TABLES

    with _migration_connection(tmp_path) as connection:
        for table_name in migration.BASELINE_TENANT_TABLES:
            connection.execute(
                text(
                    f'CREATE TABLE "{table_name}" ('
                    "id INTEGER PRIMARY KEY, "
                    "company_id INTEGER NOT NULL DEFAULT 1)"
                )
            )
            connection.execute(text(f'INSERT INTO "{table_name}" (id) VALUES (1)'))

        original_op = migration.op
        migration.op = Operations(MigrationContext.configure(connection))
        try:
            migration.upgrade()
        finally:
            migration.op = original_op

        inspector = inspect(connection)
        for table_name in migration.BASELINE_TENANT_TABLES:
            company_column = next(
                column
                for column in inspector.get_columns(table_name)
                if column["name"] == "company_id"
            )
            assert company_column["default"] is None
            assert company_column["nullable"] is False
            assert connection.execute(
                text(f'SELECT company_id FROM "{table_name}" WHERE id=1')
            ).scalar_one() == 1
            _assert_insert_fails_for_company_id(
                connection,
                text(f'INSERT INTO "{table_name}" (id) VALUES (2)'),
            )


def test_legacy_runtime_backfills_are_explicit_and_default_free(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-backfill.db'}")
    tenancy.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            tenancy.companies.insert().values(
                id=7,
                name="Existing tenant",
                is_active=True,
                created_at=tenancy.utcnow(),
            )
        )
        connection.execute(text("CREATE TABLE app_users (id INTEGER PRIMARY KEY)"))
        for table_name in BASELINE_TABLE_PAYLOADS:
            connection.execute(text(f'CREATE TABLE "{table_name}" (id INTEGER PRIMARY KEY)'))
            connection.execute(text(f'INSERT INTO "{table_name}" (id) VALUES (1)'))

    try:
        tenancy.initialize_tenancy(engine)
        core_schema.initialize_core_schema(engine)
        workflow.initialize_workflow(engine)

        inspector = inspect(engine)
        with engine.connect() as connection:
            for table_name in BASELINE_TABLE_PAYLOADS:
                company_column = next(
                    column
                    for column in inspector.get_columns(table_name)
                    if column["name"] == "company_id"
                )
                assert company_column["default"] is None
                assert company_column["nullable"] is False
                assert connection.execute(
                    text(f'SELECT company_id FROM "{table_name}" WHERE id=1')
                ).scalar_one() == 7
                _assert_insert_fails_for_company_id(
                    connection,
                    text(f'INSERT INTO "{table_name}" (id) VALUES (2)'),
                )
    finally:
        engine.dispose()
