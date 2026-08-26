"""PostgreSQL concurrency: absent stock-row create race is safe.

adjust_warehouse_stock must handle a warehouse_stocks row that does not exist
yet: the guarded UPDATE misses, and the row is created inside a SAVEPOINT whose
IntegrityError fallback re-applies the delta when a concurrent writer inserted
the row first (inventory.py). Two concurrent +5 adjustments against the same
absent (warehouse, product) row must yield exactly one row with quantity 10 --
never a duplicate row and never a lost update.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier

import pytest


@pytest.mark.postgresql
def test_absent_stock_row_concurrent_create_is_race_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("STOCK_RACE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("STOCK_RACE_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from decimal import Decimal

    from fastapi.testclient import TestClient
    from sqlalchemy import delete, func, select

    from app.db import SessionLocal
    from app.inventory import adjust_warehouse_stock, warehouse_stocks
    from app.main import app

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
        cid = login["companies"][0]["id"]
        headers = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid)}
        changed = client.post("/api/auth/change-password", headers=headers, json={
            "current_password": "admin123", "new_password": "StockRacePg123!"
        }).json()
        headers["Authorization"] = "Bearer " + changed["access_token"]

        # A fresh warehouse guarantees every (warehouse, product) pair starts
        # without a warehouse_stocks row.
        wid = client.post("/api/warehouses", headers=headers, json={"name": "Race WH"}).json()["id"]

        def stock_rows(pid: int):
            with SessionLocal() as db:
                return db.execute(
                    select(warehouse_stocks.c.quantity).where(
                        warehouse_stocks.c.company_id == cid,
                        warehouse_stocks.c.warehouse_id == wid,
                        warehouse_stocks.c.product_id == pid,
                    )
                ).all()

        # Repeat so the barrier lands both threads in the absent-row INSERT path
        # (IntegrityError fallback) across several attempts, not just once.
        for i in range(10):
            pid = client.post("/api/products", headers=headers, json={
                "name": f"Race P{i}", "purchase_price": 1, "sale_price": 2,
                "vat_rate": 20, "stock": 0, "unit": "Adet",
            }).json()["id"]
            # Product creation seeds a zero-quantity row in every existing
            # warehouse; delete it so the (warehouse, product) row is genuinely
            # absent and the concurrent INSERT path is what gets exercised.
            with SessionLocal() as db:
                db.execute(delete(warehouse_stocks).where(
                    warehouse_stocks.c.company_id == cid,
                    warehouse_stocks.c.warehouse_id == wid,
                    warehouse_stocks.c.product_id == pid,
                ))
                db.commit()
            assert stock_rows(pid) == [], "precondition: no stock row before the race"

            barrier = Barrier(2)

            def worker(product_id=pid, gate=barrier):
                gate.wait()
                with SessionLocal() as db:
                    adjust_warehouse_stock(db, cid, wid, product_id, "5")
                    db.commit()

            with ThreadPoolExecutor(max_workers=2) as pool:
                for future in [pool.submit(worker), pool.submit(worker)]:
                    future.result()

            rows = stock_rows(pid)
            assert len(rows) == 1, f"iteration {i}: expected one stock row, got {len(rows)}"
            assert Decimal(rows[0][0]) == Decimal("10"), f"iteration {i}: lost update -> {rows[0][0]}"

            with SessionLocal() as db:
                total = db.execute(
                    select(func.coalesce(func.sum(warehouse_stocks.c.quantity), 0)).where(
                        warehouse_stocks.c.company_id == cid,
                        warehouse_stocks.c.product_id == pid,
                    )
                ).scalar()
            assert Decimal(total) == Decimal("10"), f"iteration {i}: products stock desynced -> {total}"
