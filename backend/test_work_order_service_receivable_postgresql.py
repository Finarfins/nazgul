from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier

import pytest
from sqlalchemy import text


@pytest.mark.postgresql
def test_parallel_completed_creates_one_service_receivable() -> None:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")

    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.main import app

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123"},
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
                "new_password": "ServiceReceivablePg123!",
            },
        ).json()
        headers["Authorization"] = "Bearer " + changed["access_token"]
        customer = client.post(
            "/api/customers", headers=headers, json={"name": "PG Service Cari"}
        ).json()
        machine = client.post(
            "/api/machines",
            headers=headers,
            json={
                "customer_id": customer["id"],
                "brand": "PG",
                "model": "Service",
                "serial_number": "PG-SERVICE-REC",
            },
        ).json()
        work_order = client.post(
            "/api/work-orders",
            headers=headers,
            json={
                "machine_id": machine["id"],
                "customer_id": customer["id"],
                "technician_id": uid,
                "actual_hours": "1",
                "labor_rate": "100",
                "warranty_type": "NONE",
                "warranty_percent": "0",
            },
        ).json()
        work_order_id = int(work_order["id"])
        started = client.patch(
            f"/api/work-orders/{work_order_id}/status",
            headers=headers,
            json={"status": "IN_PROGRESS"},
        )
        assert started.status_code == 200, started.text

    barrier = Barrier(2)

    def complete() -> int:
        with TestClient(app) as concurrent_client:
            barrier.wait()
            response = concurrent_client.patch(
                f"/api/work-orders/{work_order_id}/status",
                headers=headers,
                json={"status": "COMPLETED"},
            )
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(complete) for _ in range(2)]
        statuses = sorted(future.result() for future in futures)

    assert statuses == [200, 409]
    with SessionLocal() as db:
        count = db.execute(
            text(
                """SELECT COUNT(*) FROM receivable_charge_documents
                WHERE company_id=:cid AND work_order_id=:work_order_id
                  AND charge_type='service_fee'"""
            ),
            {"cid": cid, "work_order_id": work_order_id},
        ).scalar_one()
        assert count == 1
