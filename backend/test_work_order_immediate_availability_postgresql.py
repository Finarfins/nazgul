from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier

import pytest


@pytest.mark.postgresql
def test_immediate_parts_race_for_last_available_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ.get("WORK_ORDER_PARTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("WORK_ORDER_PARTS_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.main import app

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        ).json()
        cid = login["companies"][0]["id"]
        uid = login["user"]["id"]
        headers = {
            "Authorization": "Bearer " + login["access_token"],
            "X-Company-ID": str(cid),
        }
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={
                "current_password": "admin123",
                "new_password": "ImmediateAvailability123!",
            },
        ).json()
        headers["Authorization"] = "Bearer " + changed["access_token"]
        customer = client.post(
            "/api/customers", headers=headers, json={"name": "Immediate Race"}
        ).json()
        machines = [
            client.post(
                "/api/machines",
                headers=headers,
                json={
                    "customer_id": customer["id"],
                    "brand": "Race",
                    "model": str(index),
                    "serial_number": f"IMMEDIATE-RACE-{index}",
                },
            ).json()
            for index in range(2)
        ]
        warehouse = client.get("/api/warehouses", headers=headers).json()[0]
        product = client.post(
            "/api/products",
            headers=headers,
            json={
                "name": "Son Kullanılabilir Parça",
                "purchase_price": 5,
                "sale_price": 10,
                "stock": 1,
                "unit": "Adet",
            },
        ).json()

    barrier = Barrier(2)

    def create(machine_id: int) -> int:
        with TestClient(app) as concurrent:
            barrier.wait()
            response = concurrent.post(
                "/api/work-orders",
                headers=headers,
                json={
                    "machine_id": machine_id,
                    "customer_id": customer["id"],
                    "technician_id": uid,
                    "parts": [
                        {
                            "product_id": product["id"],
                            "warehouse_id": warehouse["id"],
                            "quantity": "1",
                            "unit_price": "5",
                        }
                    ],
                },
            )
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(create, [row["id"] for row in machines]))

    assert statuses == [201, 409]
    with SessionLocal() as db:
        stock = db.execute(
            text(
                """SELECT quantity,reserved_quantity FROM warehouse_stocks
                WHERE company_id=:cid AND warehouse_id=:wid AND product_id=:pid"""
            ),
            {"cid": cid, "wid": warehouse["id"], "pid": product["id"]},
        ).first()
        assert tuple(map(float, stock)) == (0, 0)
        assert (
            db.execute(
                text(
                    "SELECT COUNT(*) FROM work_order_parts "
                    "WHERE company_id=:cid AND product_id=:pid"
                ),
                {"cid": cid, "pid": product["id"]},
            ).scalar_one()
            == 1
        )
