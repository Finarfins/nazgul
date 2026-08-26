from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session

from app.core_schema import metadata as core_metadata
from app.core_schema import products
from app.inventory import (
    adjust_warehouse_stock,
    metadata as inventory_metadata,
    warehouse_stocks,
)

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent


def _expected_revision() -> str:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head
    return head


def test_atomic_stock_guard_and_decimal_precision(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'stock.db'}")
    core_metadata.create_all(engine)
    inventory_metadata.create_all(engine)

    with Session(engine) as db:
        db.execute(
            insert(products).values(
                id=1,
                name="Atomik Stok Ürünü",
                company_id=1,
                stock=Decimal("1.0000"),
            )
        )
        db.execute(
            insert(warehouse_stocks).values(
                company_id=1,
                warehouse_id=10,
                product_id=1,
                quantity=Decimal("1.0000"),
                critical_stock=Decimal("0"),
            )
        )
        db.commit()

        assert adjust_warehouse_stock(db, 1, 10, 1, "-0.1000") == Decimal("0.9000")
        assert adjust_warehouse_stock(db, 1, 10, 1, Decimal("-0.2000")) == Decimal("0.7000")
        db.commit()

        with pytest.raises(ValueError, match="yetersiz stok"):
            adjust_warehouse_stock(db, 1, 10, 1, Decimal("-0.8000"))
        db.rollback()

        quantity = db.execute(
            select(warehouse_stocks.c.quantity).where(
                warehouse_stocks.c.company_id == 1,
                warehouse_stocks.c.warehouse_id == 10,
                warehouse_stocks.c.product_id == 1,
            )
        ).scalar_one()
        product_stock = db.execute(
            select(products.c.stock).where(products.c.id == 1, products.c.company_id == 1)
        ).scalar_one()
        assert quantity == Decimal("0.7000")
        assert product_stock == Decimal("0.7000")

        assert adjust_warehouse_stock(
            db, 1, 10, 1, Decimal("-0.8000"), allow_negative=True
        ) == Decimal("-0.1000")
        db.commit()


def test_live_ready_and_auth_boundaries_on_clean_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "health.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    script = r'''
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
live = client.get('/api/live')
assert live.status_code == 200
assert live.json()['status'] == 'alive'
assert live.headers['cache-control'] == 'no-store'
assert client.get('/api/ready').status_code == 200
assert client.get('/api/ready').json()['database'] == 'ok'
assert client.get('/api/health').status_code == 200
assert client.get('/api/dashboard').status_code == 401
root = client.get('/')
assert root.status_code == 200
assert 'no-cache' in root.headers['cache-control']
client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_alembic_bootstrap_records_revision_on_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)

    bootstrap = subprocess.run(
        [sys.executable, "-c", "from app.main import app; print('schema-ready')"],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert bootstrap.returncode == 0, bootstrap.stdout + "\n" + bootstrap.stderr

    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert migration.returncode == 0, migration.stdout + "\n" + migration.stderr

    with sqlite3.connect(database) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert revision == (_expected_revision(),)


def test_delivery_contains_ci_and_non_root_container() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    assert "USER app" in dockerfile
    assert "/api/live" in dockerfile
    assert workflow.is_file()


def test_numeric_migration_manifest_covers_all_declared_numeric_columns() -> None:
    import importlib.util

    from sqlalchemy import Numeric

    from app import core_schema, crm, document_engine, finance_engine, inventory, workflow

    migration_path = (
        BACKEND / "alembic" / "versions" / "20260713_0001_numeric_hardening.py"
    )
    spec = importlib.util.spec_from_file_location("numeric_hardening", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    declared: dict[tuple[str, str], tuple[int, int]] = {}
    for app_module in (
        core_schema,
        workflow,
        inventory,
        finance_engine,
        document_engine,
        crm,
    ):
        metadata = getattr(app_module, "metadata", None)
        if metadata is None:
            continue
        for table in metadata.tables.values():
            for column in table.columns:
                if isinstance(column.type, Numeric):
                    declared[(table.name, column.name)] = (
                        int(column.type.precision),
                        int(column.type.scale),
                    )

    migration_columns: dict[tuple[str, str], tuple[int, int]] = {}
    for table_name, column_names in module.MONEY_COLUMNS.items():
        for column_name in column_names:
            migration_columns[(table_name, column_name)] = (18, 2)
    for table_name, column_names in module.QUANTITY_COLUMNS.items():
        for column_name in column_names:
            migration_columns[(table_name, column_name)] = (18, 4)

    # Columns introduced AFTER the numeric-hardening migration are created with
    # the correct NUMERIC type by their own migration, so they are deliberately
    # absent from that historical manifest — back-dating them into it would
    # rewrite a migration that runs before the column exists. They are listed
    # here (with their expected precision) instead of loosening the check to a
    # subset test, so a wrong type is still caught.
    post_hardening = {
        # Servis v2 FAZ-3 — reservations, created by 20260728_0033.
        ("warehouse_stocks", "reserved_quantity"): (18, 4),
    }
    for key, expected in post_hardening.items():
        assert declared.get(key) == expected, (key, declared.get(key), expected)

    legacy_declared = {
        key: value for key, value in declared.items() if key not in post_hardening
    }
    assert migration_columns == legacy_declared
