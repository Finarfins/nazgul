from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier

import pytest


@pytest.mark.postgresql
def test_work_order_parts_postgresql_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("WORK_ORDER_PARTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("WORK_ORDER_PARTS_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.main import app

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
        cid = login["companies"][0]["id"]
        uid = login["user"]["id"]
        headers = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid)}
        changed = client.post("/api/auth/change-password", headers=headers, json={
            "current_password": "admin123", "new_password": "WorkOrderPartsPg123!"
        }).json()
        headers["Authorization"] = "Bearer " + changed["access_token"]
        customer = client.post("/api/customers", headers=headers, json={"name": "PG Parts"}).json()
        machine = client.post("/api/machines", headers=headers, json={
            "customer_id": customer["id"], "brand": "PG", "model": "Parts", "serial_number": "PG-PARTS-M"
        }).json()
        work_orders = [client.post("/api/work-orders", headers=headers, json={
            "machine_id": machine["id"], "customer_id": customer["id"], "technician_id": uid
        }).json() for _ in range(2)]
        warehouse = client.get("/api/warehouses", headers=headers).json()[0]
        product = client.post("/api/products", headers=headers, json={
            "name": "PG Concurrent Part", "purchase_price": 5, "sale_price": 10,
            "vat_rate": 20, "stock": 5, "unit": "Adet"
        }).json()
        payload = {"product_id": product["id"], "warehouse_id": warehouse["id"],
                   "quantity": "4", "unit_price": "10", "discount": "0", "tax_rate": "20"}

    def reserve(work_order_id: int) -> int:
        with TestClient(app) as concurrent_client:
            return concurrent_client.post(
                f"/api/work-orders/{work_order_id}/parts", headers=headers, json=payload
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(reserve, [row["id"] for row in work_orders]))
    assert statuses == [201, 409]
    with SessionLocal() as db:
        remaining = db.execute(text("""SELECT quantity FROM warehouse_stocks
            WHERE company_id=:cid AND warehouse_id=:warehouse_id AND product_id=:product_id"""),
            {"cid": cid, "warehouse_id": warehouse["id"], "product_id": product["id"]}).scalar_one()
        assert remaining == 1
        assert db.execute(text("SELECT COUNT(*) FROM work_order_parts WHERE company_id=:cid"), {"cid": cid}).scalar_one() == 1

    with TestClient(app) as client:
        race_order = client.post("/api/work-orders", headers=headers, json={
            "machine_id": machine["id"], "customer_id": customer["id"], "technician_id": uid
        }).json()
        race_product = client.post("/api/products", headers=headers, json={
            "name": "PG Terminal Race Part", "purchase_price": 5, "sale_price": 10,
            "vat_rate": 20, "stock": 5, "unit": "Adet"
        }).json()
        assert client.patch(f"/api/work-orders/{race_order['id']}/status", headers=headers,
                            json={"status": "IN_PROGRESS"}).status_code == 200
        assert client.patch(f"/api/work-orders/{race_order['id']}/status", headers=headers,
                            json={"status": "COMPLETED"}).status_code == 200

    race_payload = {**payload, "product_id": race_product["id"], "quantity": "2"}
    barrier = Barrier(2)

    def add_during_delivery() -> int:
        with TestClient(app) as concurrent_client:
            barrier.wait()
            return concurrent_client.post(
                f"/api/work-orders/{race_order['id']}/parts", headers=headers, json=race_payload
            ).status_code

    def deliver() -> int:
        with TestClient(app) as concurrent_client:
            barrier.wait()
            return concurrent_client.patch(
                f"/api/work-orders/{race_order['id']}/status", headers=headers,
                json={"status": "DELIVERED"},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        part_future = pool.submit(add_during_delivery)
        delivery_future = pool.submit(deliver)
        part_status = part_future.result()
        delivery_status = delivery_future.result()
    assert delivery_status == 200
    assert part_status in {201, 409}
    with SessionLocal() as db:
        final_status = db.execute(text("SELECT status FROM work_orders WHERE id=:id AND company_id=:cid"),
                                  {"id": race_order["id"], "cid": cid}).scalar_one()
        reserved = db.execute(text("SELECT COUNT(*) FROM work_order_parts WHERE work_order_id=:id AND company_id=:cid"),
                              {"id": race_order["id"], "cid": cid}).scalar_one()
        remaining = db.execute(text("""SELECT quantity FROM warehouse_stocks
            WHERE company_id=:cid AND warehouse_id=:warehouse_id AND product_id=:product_id"""),
            {"cid": cid, "warehouse_id": warehouse["id"], "product_id": race_product["id"]}).scalar_one()
    assert final_status == "DELIVERED"
    assert (part_status, reserved, remaining) in {(201, 1, 3), (409, 0, 5)}


@pytest.mark.postgresql
def test_work_order_parts_postgresql_cancel_restore_and_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("WORK_ORDER_PARTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("WORK_ORDER_PARTS_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.main import app

    with TestClient(app) as client:
        # This file shares one PostgreSQL database across its tests (the app engine
        # is import-cached), and an earlier test may already have rotated the admin
        # password. Log in resiliently regardless of the current admin password.
        login = None
        current_pw = None
        for candidate in ("admin123", "WorkOrderPartsPg123!", "WorkOrderPartsPg456!"):
            resp = client.post("/api/auth/login", json={"username": "admin", "password": candidate})
            if resp.status_code == 200:
                current_pw, login = candidate, resp.json()
                break
        assert login is not None, "admin login failed for every known password"
        cid = login["companies"][0]["id"]
        uid = login["user"]["id"]
        headers = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid)}
        if login["user"].get("must_change_password"):
            changed = client.post("/api/auth/change-password", headers=headers, json={
                "current_password": current_pw, "new_password": "WorkOrderPartsPg456!"
            }).json()
            headers["Authorization"] = "Bearer " + changed["access_token"]
        customer = client.post("/api/customers", headers=headers, json={"name": "PG Cancel"}).json()
        machine = client.post("/api/machines", headers=headers, json={
            "customer_id": customer["id"], "brand": "PG", "model": "Cancel", "serial_number": "PG-CANCEL-M"
        }).json()
        warehouse = client.get("/api/warehouses", headers=headers).json()[0]
        product = client.post("/api/products", headers=headers, json={
            "name": "PG Cancel Part", "purchase_price": 5, "sale_price": 10,
            "vat_rate": 20, "stock": 5, "unit": "Adet"
        }).json()
        wo = client.post("/api/work-orders", headers=headers, json={
            "machine_id": machine["id"], "customer_id": customer["id"], "technician_id": uid
        }).json()
        reserved = client.post(f"/api/work-orders/{wo['id']}/parts", headers=headers, json={
            "product_id": product["id"], "warehouse_id": warehouse["id"],
            "quantity": "3", "unit_price": "10", "tax_rate": "20",
        })
        assert reserved.status_code == 201, reserved.text

        # oversized values are rejected with a clean 422 (no PostgreSQL DataError / 500)
        for field, value in (("quantity", "1000001"), ("unit_price", "100000001"), ("tax_rate", "101")):
            r = client.post(f"/api/work-orders/{wo['id']}/parts", headers=headers, json={
                "product_id": product["id"], "warehouse_id": warehouse["id"],
                "quantity": "1", "unit_price": "10", "tax_rate": "20", field: value,
            })
            assert r.status_code == 422, (field, r.status_code, r.text)

    def cancel() -> int:
        with TestClient(app) as concurrent_client:
            return concurrent_client.patch(
                f"/api/work-orders/{wo['id']}/status", headers=headers, json={"status": "CANCELLED"}
            ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = sorted(pool.map(lambda _: cancel(), range(8)))
    # exactly one cancellation wins; the rest are rejected
    assert statuses.count(200) == 1
    assert all(s == 409 for s in statuses if s != 200)

    with SessionLocal() as db:
        remaining = db.execute(text("""SELECT quantity FROM warehouse_stocks
            WHERE company_id=:cid AND warehouse_id=:warehouse_id AND product_id=:product_id"""),
            {"cid": cid, "warehouse_id": warehouse["id"], "product_id": product["id"]}).scalar_one()
        status_changes = db.execute(text("""SELECT COUNT(*) FROM entity_change_logs
            WHERE company_id=:cid AND entity_type='work_order' AND entity_id=:id AND action='status_change'"""),
            {"cid": cid, "id": wo["id"]}).scalar_one()
        final_status = db.execute(text("SELECT status FROM work_orders WHERE id=:id AND company_id=:cid"),
                                  {"id": wo["id"], "cid": cid}).scalar_one()
    # stock restored exactly once (5), never twice; audit has a single status_change
    assert remaining == 5
    assert status_changes == 1
    assert final_status == "CANCELLED"
