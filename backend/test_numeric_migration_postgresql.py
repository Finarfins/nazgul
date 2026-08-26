from __future__ import annotations

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.reconciliation import capture_numeric_snapshot, compare_numeric_snapshots

BACKEND = Path(__file__).resolve().parent


def _alembic_head() -> str:
    """Return the single head revision the migration graph upgrades to."""
    script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    return script.get_current_head()


def test_legacy_numeric_columns_migrate_without_accounting_drift() -> None:
    base_url = os.getenv("NUMERIC_MIGRATION_TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("NUMERIC_MIGRATION_TEST_DATABASE_URL is required")
    if not base_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("NUMERIC_MIGRATION_TEST_DATABASE_URL must point to PostgreSQL")

    admin_engine = create_engine(base_url, pool_pre_ping=True)
    schema = f"numeric_migration_{uuid4().hex}"
    quoted_schema = admin_engine.dialect.identifier_preparer.quote(schema)
    with admin_engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))

    test_url = make_url(base_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)
    engine = create_engine(test_url, pool_pre_ping=True)

    try:
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE payments(
                    id INTEGER PRIMARY KEY,
                    amount DOUBLE PRECISION,
                    company_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE products(
                    id INTEGER PRIMARY KEY,
                    purchase_price DOUBLE PRECISION,
                    sale_price DOUBLE PRECISION,
                    vat_rate DOUBLE PRECISION,
                    stock DOUBLE PRECISION,
                    units_per_box DOUBLE PRECISION,
                    critical_stock DOUBLE PRECISION,
                    minimum_stock DOUBLE PRECISION,
                    company_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                INSERT INTO payments(id, amount, company_id)
                VALUES (1, 0.1, 1), (2, 0.2, 1), (3, NULL, 1)
            """))
            connection.execute(text("""
                INSERT INTO products(
                    id, purchase_price, sale_price, vat_rate, stock,
                    units_per_box, critical_stock, minimum_stock, company_id
                ) VALUES (
                    1, 10.125, 20.335, 20.0, 1.23456,
                    1.0, 0.5, 0.25, 1
                )
            """))

        env = os.environ.copy()
        env["DATABASE_URL"] = test_url
        env["PYTHONPATH"] = str(BACKEND)

        # Bring the legacy database up to the schema baseline before fingerprinting
        # it. The baseline migration creates the remaining ERP tables with
        # ``checkfirst=True``, so the manually-created ``payments``/``products``
        # keep their legacy DOUBLE PRECISION storage for the numeric hardening
        # step to convert. Capturing the "before" snapshot here means both
        # fingerprints cover the same set of tables, so ``compare`` isolates real
        # accounting drift instead of flagging the expected schema expansion.
        baseline = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "20260712_0000"],
            cwd=BACKEND,
            env=env,
            text=True,
            capture_output=True,
            timeout=180,
        )
        assert baseline.returncode == 0, baseline.stdout + "\n" + baseline.stderr

        before = capture_numeric_snapshot(engine)
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            cwd=BACKEND,
            env=env,
            text=True,
            capture_output=True,
            timeout=180,
        )
        assert result.returncode == 0, result.stdout + "\n" + result.stderr

        after = capture_numeric_snapshot(engine)
        assert compare_numeric_snapshots(before, after) == []

        inspector = inspect(engine)
        payments = {column["name"]: column for column in inspector.get_columns("payments")}
        products = {column["name"]: column for column in inspector.get_columns("products")}
        assert payments["amount"]["type"].precision == 18
        assert payments["amount"]["type"].scale == 2
        assert products["stock"]["type"].precision == 18
        assert products["stock"]["type"].scale == 4

        with engine.connect() as connection:
            assert connection.execute(text("SELECT SUM(amount) FROM payments")).scalar_one() == Decimal("0.30")
            row = connection.execute(text("""
                SELECT purchase_price, sale_price, stock
                FROM products WHERE id=1
            """)).one()
            assert row.purchase_price == Decimal("10.13")
            assert row.sale_price == Decimal("20.34")
            assert row.stock == Decimal("1.2346")
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _alembic_head()
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {quoted_schema} CASCADE"))
        admin_engine.dispose()
