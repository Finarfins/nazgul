"""FAZ-3 migration EFFECT tests — does it actually do the work?

Two gaps these close:

* every block in the migration is skipped when its table is missing (partially
  built schemas exist in other migration tests). A typo in one of those guarded
  table names would silently skip real work and still leave CI green — "it did
  not crash" is not "it did its job". :func:`test_migration_applies_every_column_and_constraint`
  reads the resulting schema back and asserts every column, CHECK and index.
* the AST test in ``test_servis_v2_faz3_reservation`` proves the backfill SQL is
  *written* correctly, not that it *ran* correctly.
  :func:`test_legacy_part_rows_are_backfilled_as_issued_in_the_database` builds a
  pre-FAZ-3 database, inserts legacy part rows the way v1 wrote them, runs the
  migration and reads the rows back.

Both are deliberate duplicates of static checks: the static ones fail fast and
name the problem, these prove the effect.
"""
from __future__ import annotations

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


BACKEND = Path(__file__).resolve().parent


def _alembic(database_url: str, target: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_migration_applies_every_column_and_constraint(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'effect.db').as_posix()}"
    _alembic(database_url, "head")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)

        stock = {c["name"]: c for c in inspector.get_columns("warehouse_stocks")}
        assert "reserved_quantity" in stock, sorted(stock)
        assert not stock["reserved_quantity"]["nullable"]

        parts = {c["name"]: c for c in inspector.get_columns("work_order_parts")}
        for column in ("line_status", "reserved_quantity"):
            assert column in parts, sorted(parts)
            assert not parts[column]["nullable"], column
        part_checks = {
            c["name"] for c in inspector.get_check_constraints("work_order_parts")
        }
        assert "ck_work_order_parts_line_status" in part_checks, part_checks
        assert "ck_work_order_parts_reserved_nonnegative" in part_checks, part_checks
        part_indexes = {i["name"] for i in inspector.get_indexes("work_order_parts")}
        assert "ix_work_order_parts_company_status" in part_indexes, part_indexes

        companies = {c["name"] for c in inspector.get_columns("companies")}
        assert "service_parts_mode" in companies, sorted(companies)
        company_checks = {
            c["name"] for c in inspector.get_check_constraints("companies")
        }
        assert "ck_companies_service_parts_mode" in company_checks, company_checks

        warehouses = {c["name"] for c in inspector.get_columns("warehouses")}
        assert {"warehouse_type", "technician_user_id"} <= warehouses, sorted(warehouses)
        warehouse_checks = {
            c["name"] for c in inspector.get_check_constraints("warehouses")
        }
        assert "ck_warehouses_type" in warehouse_checks, warehouse_checks

        assert inspector.has_table("work_order_stock_events")
        event_columns = {
            c["name"] for c in inspector.get_columns("work_order_stock_events")
        }
        assert {
            "company_id",
            "work_order_id",
            "part_id",
            "event_type",
            "idempotency_key",
            "created_at",
        } <= event_columns, sorted(event_columns)
        event_unique = {
            tuple(c["column_names"])
            for c in inspector.get_unique_constraints("work_order_stock_events")
        }
        assert ("company_id", "idempotency_key") in event_unique, event_unique
    finally:
        engine.dispose()


LEGACY_SEED = (
    "INSERT INTO companies(id,name,is_active,created_at) "
    "VALUES(901,'Legacy Firma',1,'2026-07-28')",
    "INSERT INTO customers(id,company_id,name) VALUES(901,901,'Legacy Musteri')",
    "INSERT INTO machines(id,company_id,customer_id,brand,model,status,is_active,"
    "created_at,updated_at) "
    "VALUES(901,901,901,'S','M','active',1,'2026-07-28','2026-07-28')",
    "INSERT INTO app_users(id,username,display_name,password_hash,role,is_active,"
    "created_at,must_change_password) "
    "VALUES(901,'legacy','Legacy','x','admin',1,'2026-07-28',0)",
    "INSERT INTO work_orders(id,company_id,machine_id,customer_id,technician_id,"
    "work_order_no,opened_at,status,priority,estimated_hours,actual_hours,labor_rate,"
    "total_labor_cost,warranty,created_by,created_at,updated_at) "
    "VALUES(901,901,901,901,901,'ISE-LEGACY','2026-07-28','OPEN','NORMAL',"
    "0,0,0,0,0,901,'2026-07-28','2026-07-28')",
    "INSERT INTO warehouses(id,company_id,name,is_active,is_default) "
    "VALUES(901,901,'Legacy Depo',1,0)",
    "INSERT INTO products(id,company_id,name,sale_price,purchase_price,stock) "
    "VALUES(901,901,'Legacy Parca',10,5,0)",
    "INSERT INTO products(id,company_id,name,sale_price,purchase_price,stock) "
    "VALUES(902,901,'Legacy Parca 2',10,5,0)",
    # v1 wrote these lines AND decremented warehouse stock at the same time, so
    # the reserved half must stay 0 after the migration.
    "INSERT INTO warehouse_stocks(company_id,warehouse_id,product_id,quantity,"
    "critical_stock) VALUES(901,901,901,20,0)",
    "INSERT INTO work_order_parts(id,company_id,work_order_id,product_id,warehouse_id,"
    "quantity,unit_price,discount,tax_rate,total_price,created_at,updated_at) "
    "VALUES(9011,901,901,901,901,4,10,0,0,40,'2026-07-28','2026-07-28')",
    "INSERT INTO work_order_parts(id,company_id,work_order_id,product_id,warehouse_id,"
    "quantity,unit_price,discount,tax_rate,total_price,created_at,updated_at) "
    "VALUES(9012,901,901,902,901,3,10,0,0,30,'2026-07-28','2026-07-28')",
)


def test_legacy_part_rows_are_backfilled_as_issued_in_the_database(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    # Schema exactly as it stood BEFORE this migration.
    _alembic(database_url, "20260728_0034")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            existing = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(work_order_parts)"))
            }
            assert "line_status" not in existing, existing
            assert "reserved_quantity" not in existing, existing
            for statement in LEGACY_SEED:
                connection.execute(text(statement))
    finally:
        engine.dispose()

    _alembic(database_url, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id,quantity,line_status,reserved_quantity "
                    "FROM work_order_parts WHERE company_id=901 ORDER BY id"
                )
            ).mappings().all()
            assert len(rows) == 2, rows
            for row in rows:
                assert row["line_status"] == "ISSUED", dict(row)
                reserved = Decimal(str(row["reserved_quantity"]))
                quantity = Decimal(str(row["quantity"]))
                # THE assertion: not reserved_quantity = quantity. v1 already
                # took this stock out, so reserving it again would make the
                # line unavailable twice over.
                assert reserved == Decimal("0"), dict(row)
                assert reserved != quantity, dict(row)
            stock = connection.execute(
                text(
                    "SELECT quantity,reserved_quantity FROM warehouse_stocks "
                    "WHERE company_id=901"
                )
            ).mappings().all()
            assert stock, "seeded warehouse stock row disappeared"
            for row in stock:
                # Physical quantity untouched, nothing newly reserved.
                assert Decimal(str(row["quantity"])) == Decimal("20"), dict(row)
                assert Decimal(str(row["reserved_quantity"])) == Decimal("0"), dict(row)
    finally:
        engine.dispose()
