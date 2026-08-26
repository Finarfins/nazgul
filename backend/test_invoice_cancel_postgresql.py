from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os

import pytest


@pytest.mark.postgresql
def test_invoice_cancel_is_compare_and_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """F4: concurrent invoice cancellations resolve to exactly one winner.

    Two requests can both read status ISSUED, but only the row-winning
    ``UPDATE ... WHERE status='ISSUED'`` may write the cancellation and its
    audit/history. Under 8-way concurrency exactly one request returns 200 and
    exactly one CANCELLED audit + one CANCELLED history row are written.
    """
    database_url = os.environ.get("INVOICE_CANCEL_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("INVOICE_CANCEL_TEST_DATABASE_URL is not configured")
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
            "current_password": "admin123", "new_password": "InvoiceCancelPg123!"
        }).json()
        headers["Authorization"] = "Bearer " + changed["access_token"]
        customer = client.post("/api/customers", headers=headers, json={"name": "PG Cancel Invoice"}).json()
        machine = client.post("/api/machines", headers=headers, json={
            "customer_id": customer["id"], "brand": "PG", "model": "Cancel", "serial_number": "PG-INV-CANCEL"
        }).json()
        warehouse = client.get("/api/warehouses", headers=headers).json()[0]
        product = client.post("/api/products", headers=headers, json={
            "name": "PG Cancel Invoice Part", "purchase_price": 5, "sale_price": 10,
            "vat_rate": 20, "stock": 20, "unit": "Adet"
        }).json()
        wo = client.post("/api/work-orders", headers=headers, json={
            "machine_id": machine["id"], "customer_id": customer["id"], "technician_id": uid,
            "actual_hours": "2", "labor_rate": "100", "warranty_type": "NONE", "warranty_percent": "0",
        }).json()
        assert client.post(f"/api/work-orders/{wo['id']}/parts", headers=headers, json={
            "product_id": product["id"], "warehouse_id": warehouse["id"],
            "quantity": "3", "unit_price": "19.99", "discount": "10", "tax_rate": "20",
        }).status_code == 201
        for status in ("IN_PROGRESS", "COMPLETED"):
            assert client.patch(f"/api/work-orders/{wo['id']}/status", headers=headers,
                                json={"status": status}).status_code == 200
        issued = client.post("/api/invoices/generate", headers=headers, json={"work_order_id": wo["id"]})
        assert issued.status_code == 201, issued.text
        invoice_id = issued.json()["id"]

    def cancel() -> int:
        with TestClient(app) as concurrent_client:
            return concurrent_client.post(
                f"/api/invoices/{invoice_id}/cancel", headers=headers,
                json={"reason": "concurrent cancellation race"},
            ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = sorted(pool.map(lambda _: cancel(), range(8)))

    # exactly one cancellation wins; every other concurrent request is rejected
    assert statuses.count(200) == 1, statuses
    assert all(s == 409 for s in statuses if s != 200), statuses

    with SessionLocal() as db:
        final_status = db.execute(
            text("SELECT status FROM invoices WHERE id=:id AND company_id=:cid"),
            {"id": invoice_id, "cid": cid},
        ).scalar_one()
        audit_count = db.execute(
            text("SELECT COUNT(*) FROM invoice_audit WHERE company_id=:cid AND invoice_id=:id AND action='CANCELLED'"),
            {"cid": cid, "id": invoice_id},
        ).scalar_one()
        history_count = db.execute(
            text("SELECT COUNT(*) FROM invoice_history WHERE company_id=:cid AND invoice_id=:id AND action='CANCELLED'"),
            {"cid": cid, "id": invoice_id},
        ).scalar_one()

    assert final_status == "CANCELLED"
    # the cancellation audit trail is written exactly once, never duplicated
    assert audit_count == 1, audit_count
    assert history_count == 1, history_count
