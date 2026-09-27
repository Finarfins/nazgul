"""H109: Servis alacak ters kaydı nedeni olayı yansıtır.

Testler:
1. `work_order_parts.py`: COMPLETED iş emrinde parça değişikliği ters kaydının
   nedeni `parts_reconciliation` olur.
2. `invoice_service.py` & `invoices.py`: Fatura oluşturma ve iptal ters kaydının
   nedeni `invoice_reconciliation` olur.
3. `work_orders.py`: `update_work_order_status` çağrısında `reason="work_order_reconciliation"`
   aktarılır.
4. `service_receivable_engine.py`: Doğrudan `reconcile_service_receivable` çağrıldığında
   varsayılan `invoice_reconciliation` kalır, verilen `reason` ters kayda aktarılır.
"""
from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

_CALISMA_ALANI = tempfile.mkdtemp(prefix="h109-reversal-reason-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "h109.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app
from app.service_receivable_engine import reconcile_service_receivable


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth_context(client):
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
    cid = login["companies"][0]["id"]
    uid = login["user"]["id"]
    h = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid)}
    changed = client.post(
        "/api/auth/change-password",
        headers=h,
        json={"current_password": "admin123", "new_password": "H109TestPass123!"},
    )
    h["Authorization"] = "Bearer " + changed.json()["access_token"]
    return {"c": client, "h": h, "cid": cid, "uid": uid}


def test_parts_events_reversal_reason_is_parts_reconciliation(auth_context):
    """COMPLETED iş emrine parça eklendiğinde oluşan ters kaydın nedeni parts_reconciliation olmalıdır."""
    c = auth_context["c"]
    h = auth_context["h"]
    cid = auth_context["cid"]
    uid = auth_context["uid"]

    customer = c.post("/api/customers", headers=h, json={"name": "H109 Parça Cari"}).json()
    machine = c.post(
        "/api/machines",
        headers=h,
        json={"customer_id": customer["id"], "brand": "Brand", "model": "Model", "serial_number": "H109-PARCA-1"},
    ).json()
    warehouse = c.get("/api/warehouses", headers=h).json()[0]
    product = c.post(
        "/api/products",
        headers=h,
        json={"name": "H109 Parça", "purchase_price": "10", "sale_price": "50", "vat_rate": "20", "stock": "20", "unit": "Adet"},
    ).json()

    wo = c.post(
        "/api/work-orders",
        headers=h,
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
    wid = wo["id"]

    for st in ("IN_PROGRESS", "COMPLETED"):
        c.patch(f"/api/work-orders/{wid}/status", headers=h, json={"status": st})

    # COMPLETED iken parça ekle -> H102 uzlaştırması -> H109 parts_reconciliation
    r = c.post(
        f"/api/work-orders/{wid}/parts",
        headers=h,
        json={"product_id": product["id"], "warehouse_id": warehouse["id"], "quantity": "1", "unit_price": "50", "discount": "0", "tax_rate": "20"},
    )
    assert r.status_code in (200, 201), r.text

    with SessionLocal() as db:
        docs = db.execute(
            text(
                """SELECT id,revision_no,status,reversal_of_document_id,calculation_snapshot
                FROM receivable_charge_documents WHERE company_id=:cid AND work_order_id=:wid
                ORDER BY revision_no"""
            ),
            {"cid": cid, "wid": wid},
        ).mappings().all()
        assert len(docs) == 3
        # R2: ters kayıt (reversal)
        assert docs[1]["status"] == "posted"
        assert docs[1]["reversal_of_document_id"] == docs[0]["id"]
        snap = json.loads(docs[1]["calculation_snapshot"])
        assert snap["source"] == "reversal"
        assert snap["reason"] == "parts_reconciliation"


def test_invoice_reconciliation_reasons(auth_context):
    """Fatura oluşturma ve iptal ters kayıtlarının nedeni invoice_reconciliation olmalıdır."""
    c = auth_context["c"]
    h = auth_context["h"]
    cid = auth_context["cid"]
    uid = auth_context["uid"]

    customer = c.post("/api/customers", headers=h, json={"name": "H109 Fatura Cari"}).json()
    machine = c.post(
        "/api/machines",
        headers=h,
        json={"customer_id": customer["id"], "brand": "Brand", "model": "Model", "serial_number": "H109-FATURA-1"},
    ).json()

    wo = c.post(
        "/api/work-orders",
        headers=h,
        json={
            "machine_id": machine["id"],
            "customer_id": customer["id"],
            "technician_id": uid,
            "actual_hours": "2",
            "labor_rate": "100",
            "warranty_type": "NONE",
            "warranty_percent": "0",
        },
    ).json()
    wid = wo["id"]

    for st in ("IN_PROGRESS", "COMPLETED"):
        c.patch(f"/api/work-orders/{wid}/status", headers=h, json={"status": st})

    # Fatura oluştur (farklı tutarla revizyon tetikle)
    inv = c.post(
        "/api/invoices/generate",
        headers=h,
        json={"work_order_id": wid, "branch_prefix": "H109", "global_discount_type": "FIXED", "global_discount_value": "10"},
    )
    assert inv.status_code == 201, inv.text
    iid = inv.json()["id"]

    with SessionLocal() as db:
        docs = db.execute(
            text(
                """SELECT revision_no,status,reversal_of_document_id,calculation_snapshot
                FROM receivable_charge_documents WHERE company_id=:cid AND work_order_id=:wid
                ORDER BY revision_no"""
            ),
            {"cid": cid, "wid": wid},
        ).mappings().all()
        assert len(docs) == 3
        snap_r2 = json.loads(docs[1]["calculation_snapshot"])
        assert snap_r2["reason"] == "invoice_reconciliation"

    # Fatura iptal et
    can = c.post(f"/api/invoices/{iid}/cancel", headers=h, json={"reason": "H109 Test İptal"})
    assert can.status_code == 200, can.text

    with SessionLocal() as db:
        docs = db.execute(
            text(
                """SELECT revision_no,status,reversal_of_document_id,calculation_snapshot
                FROM receivable_charge_documents WHERE company_id=:cid AND work_order_id=:wid
                ORDER BY revision_no"""
            ),
            {"cid": cid, "wid": wid},
        ).mappings().all()
        assert len(docs) == 5
        snap_r4 = json.loads(docs[3]["calculation_snapshot"])
        assert snap_r4["reason"] == "invoice_reconciliation"


def test_engine_reconciliation_reasons(auth_context):
    """Motor çağrısı varsayılan olarak invoice_reconciliation, parametre verildiğinde onu yazar."""
    c = auth_context["c"]
    h = auth_context["h"]
    cid = auth_context["cid"]
    uid = auth_context["uid"]

    customer = c.post("/api/customers", headers=h, json={"name": "H109 Motor Cari"}).json()
    machine = c.post(
        "/api/machines",
        headers=h,
        json={"customer_id": customer["id"], "brand": "Brand", "model": "Model", "serial_number": "H109-MOTOR-1"},
    ).json()

    wo = c.post(
        "/api/work-orders",
        headers=h,
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
    wid = wo["id"]

    for st in ("IN_PROGRESS", "COMPLETED"):
        c.patch(f"/api/work-orders/{wid}/status", headers=h, json={"status": st})

    # Varsayılan reason (invoice_reconciliation)
    with SessionLocal() as db:
        db.execute(
            text("UPDATE work_orders SET actual_hours=3 WHERE id=:id AND company_id=:cid"),
            {"id": wid, "cid": cid},
        )
        db.commit()

        reconcile_service_receivable(db, cid, wid, actor_id=uid)
        db.commit()

        docs = db.execute(
            text(
                """SELECT revision_no,status,calculation_snapshot
                FROM receivable_charge_documents WHERE company_id=:cid AND work_order_id=:wid
                ORDER BY revision_no"""
            ),
            {"cid": cid, "wid": wid},
        ).mappings().all()
        assert len(docs) == 3
        snap_r2 = json.loads(docs[1]["calculation_snapshot"])
        assert snap_r2["reason"] == "invoice_reconciliation"

    # work_order_reconciliation
    with SessionLocal() as db:
        db.execute(
            text("UPDATE work_orders SET actual_hours=5 WHERE id=:id AND company_id=:cid"),
            {"id": wid, "cid": cid},
        )
        db.commit()

        reconcile_service_receivable(db, cid, wid, actor_id=uid, reason="work_order_reconciliation")
        db.commit()

        docs = db.execute(
            text(
                """SELECT revision_no,status,calculation_snapshot
                FROM receivable_charge_documents WHERE company_id=:cid AND work_order_id=:wid
                ORDER BY revision_no"""
            ),
            {"cid": cid, "wid": wid},
        ).mappings().all()
        assert len(docs) == 5
        snap_r4 = json.loads(docs[3]["calculation_snapshot"])
        assert snap_r4["reason"] == "work_order_reconciliation"
