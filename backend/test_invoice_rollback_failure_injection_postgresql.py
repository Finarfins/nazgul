"""PostgreSQL atomicity: a late write failure rolls back the whole invoice.

generate_invoice writes the invoice header, every invoice_item, then the
audit + history rows, and only then commits (invoice_service.py). If a write
late in that sequence fails, the entire transaction must roll back -- no
invoice, no items, no audit, no history may survive. This injects a failure at
the last write (log_invoice_action) and proves nothing persists, then proves a
real generate still succeeds afterwards (the work order was left cleanly
re-invoiceable, not half-written or frozen).
"""
from __future__ import annotations

import os

import pytest


@pytest.mark.postgresql
def test_late_write_failure_rolls_back_entire_invoice(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("INVOICE_ROLLBACK_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("INVOICE_ROLLBACK_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    import app.invoice_service as invoice_service
    from app.db import SessionLocal
    from app.main import app

    # raise_server_exceptions=False so the injected error surfaces as a 500
    # response instead of propagating into the test.
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
        cid = login["companies"][0]["id"]
        uid = login["user"]["id"]
        headers = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid)}
        changed = client.post("/api/auth/change-password", headers=headers, json={
            "current_password": "admin123", "new_password": "InvoiceRollbackPg123!"
        }).json()
        headers["Authorization"] = "Bearer " + changed["access_token"]

        customer = client.post("/api/customers", headers=headers, json={"name": "PG Rollback"}).json()
        machine = client.post("/api/machines", headers=headers, json={
            "customer_id": customer["id"], "brand": "PG", "model": "Rollback", "serial_number": "PG-INV-RB"
        }).json()
        warehouse = client.get("/api/warehouses", headers=headers).json()[0]
        product = client.post("/api/products", headers=headers, json={
            "name": "PG Rollback Part", "purchase_price": 5, "sale_price": 10,
            "vat_rate": 20, "stock": 20, "unit": "Adet"
        }).json()
        wo = client.post("/api/work-orders", headers=headers, json={
            "machine_id": machine["id"], "customer_id": customer["id"], "technician_id": uid,
            "actual_hours": "2", "labor_rate": "100", "warranty_type": "NONE", "warranty_percent": "0",
        }).json()
        wo_id = wo["id"]
        assert client.post(f"/api/work-orders/{wo_id}/parts", headers=headers, json={
            "product_id": product["id"], "warehouse_id": warehouse["id"],
            "quantity": "3", "unit_price": "19.99", "discount": "10", "tax_rate": "20",
        }).status_code == 201
        for status in ("IN_PROGRESS", "COMPLETED"):
            assert client.patch(f"/api/work-orders/{wo_id}/status", headers=headers,
                                json={"status": status}).status_code == 200

        def invoice_footprint():
            with SessionLocal() as db:
                params = {"c": cid, "w": wo_id}
                invoices = db.execute(text(
                    "SELECT COUNT(*) FROM invoices WHERE company_id=:c AND work_order_id=:w"), params).scalar_one()
                items = db.execute(text(
                    "SELECT COUNT(*) FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id "
                    "WHERE i.company_id=:c AND i.work_order_id=:w"), params).scalar_one()
                audit = db.execute(text(
                    "SELECT COUNT(*) FROM invoice_audit a JOIN invoices i ON i.id=a.invoice_id "
                    "WHERE i.company_id=:c AND i.work_order_id=:w"), params).scalar_one()
                history = db.execute(text(
                    "SELECT COUNT(*) FROM invoice_history h JOIN invoices i ON i.id=h.invoice_id "
                    "WHERE i.company_id=:c AND i.work_order_id=:w"), params).scalar_one()
            return {"invoices": invoices, "items": items, "audit": audit, "history": history}

        original = invoice_service.log_invoice_action

        def boom(*args, **kwargs):
            # Fail at the last write, after the header + all items are inserted.
            raise RuntimeError("injected late write failure")

        monkeypatch.setattr(invoice_service, "log_invoice_action", boom)
        failed = client.post("/api/invoices/generate", headers=headers, json={"work_order_id": wo_id})
        assert failed.status_code == 500, failed.text

        # Full rollback: the header INSERT and every item INSERT that ran before
        # the failure must be gone -- nothing partial survives.
        assert invoice_footprint() == {"invoices": 0, "items": 0, "audit": 0, "history": 0}

        # Remove the injection: a genuine generate now succeeds, proving the
        # failed attempt left the work order cleanly re-invoiceable.
        monkeypatch.setattr(invoice_service, "log_invoice_action", original)
        ok = client.post("/api/invoices/generate", headers=headers, json={"work_order_id": wo_id})
        assert ok.status_code == 201, ok.text

        footprint = invoice_footprint()
        assert footprint["invoices"] == 1, footprint
        assert footprint["items"] >= 1, footprint
        assert footprint["audit"] == 1, footprint
        assert footprint["history"] == 1, footprint
