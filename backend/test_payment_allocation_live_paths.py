from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


BACKEND = Path(__file__).resolve().parent


def run_live_payment_smoke(database_url: str, *, enabled: bool) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    env["PAYMENT_ALLOCATION_ENGINE_ENABLED"] = "true" if enabled else "false"
    completed = subprocess.run(
        [sys.executable, "-c", _ENABLED_SMOKE if enabled else _LEGACY_SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_live_payment_allocation_paths_sqlite(tmp_path: Path) -> None:
    run_live_payment_smoke(
        f"sqlite:///{(tmp_path / 'enabled.db').as_posix()}",
        enabled=True,
    )
    run_live_payment_smoke(
        f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}",
        enabled=False,
    )


_ENABLED_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app


def dec(value):
    return Decimal(str(value))


with TestClient(app) as client:
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()
    headers = {
        "Authorization": "Bearer " + login["access_token"],
        "X-Company-ID": str(login["companies"][0]["id"]),
    }
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "admin123", "new_password": "AllocationLive123!"},
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    cid = int(headers["X-Company-ID"])

    customer = client.post(
        "/api/customers", headers=headers, json={"name": "Canlı Tahsis"}
    ).json()["id"]
    product = client.post(
        "/api/products",
        headers=headers,
        json={"name": "Tahsis Ürünü", "sale_price": "100", "stock": "100"},
    ).json()["id"]
    sale = client.post(
        "/api/orders",
        headers=headers,
        json={
            "entity_id": customer,
            "transaction_date": "2026-07-01",
            "status": "completed",
            "payment_method": "cash",
            "paid_amount": "40",
            "items": [{
                "product_id": product,
                "quantity": "1",
                "unit_price": "100",
                "vat_rate": 20,
            }],
        },
    )
    assert sale.status_code == 201, sale.text
    sale_id = sale.json()["id"]
    with SessionLocal() as db:
        sale_row = db.execute(
            text("SELECT paid_amount FROM orders WHERE id=:id AND company_id=:cid"),
            {"id": sale_id, "cid": cid},
        ).one()
        assert dec(sale_row[0]) == dec("40.00")
        allocation = db.execute(
            text(
                """SELECT a.amount,a.allocation_type
                FROM payment_allocations a
                JOIN payments p ON p.id=a.payment_id
                WHERE a.company_id=:cid AND a.order_id=:order_id"""
            ),
            {"cid": cid, "order_id": sale_id},
        ).one()
        assert dec(allocation[0]) == dec("40.00")
        assert allocation[1] == "document_create"

    open_sale = client.post(
        "/api/orders",
        headers=headers,
        json={
            "entity_id": customer,
            "transaction_date": "2026-07-02",
            "due_date": "2026-10-01",
            "payment_term": "HARMAN_VADELI",
            "status": "completed",
            "payment_method": "credit",
            "paid_amount": "0",
            "items": [{
                "product_id": product,
                "quantity": "1",
                "unit_price": "100",
                "vat_rate": 20,
            }],
        },
    )
    assert open_sale.status_code == 201, open_sale.text
    open_sale_id = open_sale.json()["id"]

    with SessionLocal() as db:
        legacy_direct_payment = int(db.execute(
            text(
                """INSERT INTO payments(
                    entity_type,entity_id,amount,payment_date,company_id,payment_method
                ) VALUES(
                    'customer',:customer,20,'2026-07-02',:cid,'cash'
                ) RETURNING id"""
            ),
            {"customer": customer, "cid": cid},
        ).scalar_one())
        db.commit()
    direct_update = client.put(
        f"/api/payments/{legacy_direct_payment}",
        headers=headers,
        json={
            "entity_type": "customer",
            "entity_id": customer,
            "amount": "20",
            "payment_date": "2026-07-02",
            "payment_method": "cash",
            "reference_type": "order",
            "reference_id": open_sale_id,
        },
    )
    assert direct_update.status_code == 200, direct_update.text
    with SessionLocal() as db:
        direct_allocation = db.execute(
            text(
                """SELECT amount,allocation_type FROM payment_allocations
                WHERE company_id=:cid AND payment_id=:payment_id"""
            ),
            {"cid": cid, "payment_id": legacy_direct_payment},
        ).one()
        assert dec(direct_allocation[0]) == dec("20.00")
        assert direct_allocation[1] == "document_create"
        assert dec(db.execute(
            text("SELECT paid_amount FROM orders WHERE id=:id AND company_id=:cid"),
            {"id": open_sale_id, "cid": cid},
        ).scalar_one()) == dec("20.00")

    other_customer = client.post(
        "/api/customers", headers=headers, json={"name": "Başka Cari"}
    ).json()["id"]
    other_sale = client.post(
        "/api/orders",
        headers=headers,
        json={
            "entity_id": other_customer,
            "transaction_date": "2026-07-02",
            "due_date": "2026-11-01",
            "status": "completed",
            "payment_method": "credit",
            "paid_amount": "0",
            "items": [{
                "product_id": product,
                "quantity": "1",
                "unit_price": "100",
                "vat_rate": 20,
            }],
        },
    )
    assert other_sale.status_code == 201, other_sale.text
    with SessionLocal() as db:
        wrong_customer_payment = int(db.execute(
            text(
                """INSERT INTO payments(
                    entity_type,entity_id,amount,payment_date,company_id,payment_method
                ) VALUES(
                    'customer',:customer,10,'2026-07-02',:cid,'cash'
                ) RETURNING id"""
            ),
            {"customer": customer, "cid": cid},
        ).scalar_one())
        db.commit()
    wrong_customer_update = client.put(
        f"/api/payments/{wrong_customer_payment}",
        headers=headers,
        json={
            "entity_type": "customer",
            "entity_id": customer,
            "amount": "10",
            "payment_date": "2026-07-02",
            "payment_method": "cash",
            "reference_type": "order",
            "reference_id": other_sale.json()["id"],
        },
    )
    assert wrong_customer_update.status_code == 409, wrong_customer_update.text
    with SessionLocal() as db:
        assert db.execute(
            text(
                """SELECT reference_type,reference_id FROM payments
                WHERE id=:id AND company_id=:cid"""
            ),
            {"id": wrong_customer_payment, "cid": cid},
        ).one() == (None, None)

    payment_headers = {**headers, "Idempotency-Key": "live-payment-1"}
    payment_payload = {
        "entity_type": "customer",
        "entity_id": customer,
        "amount": "30",
        "payment_date": "2026-07-03",
        "payment_method": "cash",
        "note": "Idempotent tahsilat",
    }
    first = client.post("/api/payments", headers=payment_headers, json=payment_payload)
    replay = client.post("/api/payments", headers=payment_headers, json=payment_payload)
    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert first.json()["id"] == replay.json()["id"]
    payment_id = first.json()["id"]
    with SessionLocal() as db:
        assert db.execute(
            text(
                """SELECT COUNT(*) FROM payments
                WHERE company_id=:cid AND note='Idempotent tahsilat'"""
            ),
            {"cid": cid},
        ).scalar_one() == 1
        assert db.execute(
            text(
                """SELECT COUNT(*) FROM payment_allocations
                WHERE company_id=:cid AND payment_id=:payment_id"""
            ),
            {"cid": cid, "payment_id": payment_id},
        ).scalar_one() == 1

    blocked_update = client.put(
        f"/api/payments/{payment_id}",
        headers=headers,
        json={**payment_payload, "amount": "35"},
    )
    blocked_delete = client.delete(f"/api/payments/{payment_id}", headers=headers)
    assert blocked_update.status_code == 409, blocked_update.text
    assert blocked_delete.status_code == 409, blocked_delete.text

    supplier = client.post(
        "/api/suppliers", headers=headers, json={"name": "Motor Tedarikçi"}
    ).json()["id"]
    supplier_payment = client.post(
        "/api/payments",
        headers={**headers, "Idempotency-Key": "supplier-payment-1"},
        json={
            "entity_type": "supplier",
            "entity_id": supplier,
            "amount": "25",
            "payment_date": "2026-07-03",
            "payment_method": "cash",
        },
    )
    assert supplier_payment.status_code == 201, supplier_payment.text
    supplier_payment_id = supplier_payment.json()["id"]
    supplier_update = client.put(
        f"/api/payments/{supplier_payment_id}",
        headers=headers,
        json={
            "entity_type": "supplier",
            "entity_id": supplier,
            "amount": "30",
            "payment_date": "2026-07-04",
            "payment_method": "cash",
        },
    )
    assert supplier_update.status_code == 200, supplier_update.text
    supplier_delete = client.delete(
        f"/api/payments/{supplier_payment_id}", headers=headers
    )
    assert supplier_delete.status_code == 204, supplier_delete.text

    with SessionLocal() as db:
        reversed_payment = int(db.execute(
            text(
                """INSERT INTO payments(
                    entity_type,entity_id,amount,payment_date,company_id,payment_method
                ) VALUES(
                    'customer',:customer,5,'2026-07-03',:cid,'cash'
                ) RETURNING id"""
            ),
            {"customer": customer, "cid": cid},
        ).scalar_one())
        original_allocation = int(db.execute(
            text(
                """INSERT INTO payment_allocations(
                    company_id,payment_id,order_id,amount,allocation_type,effective_date
                ) VALUES(
                    :cid,:payment_id,:order_id,5,'fifo','2026-07-03'
                ) RETURNING id"""
            ),
            {
                "cid": cid,
                "payment_id": reversed_payment,
                "order_id": open_sale_id,
            },
        ).scalar_one())
        reversal_allocation = int(db.execute(
            text(
                """INSERT INTO payment_allocations(
                    company_id,payment_id,order_id,amount,allocation_type,effective_date,
                    reversal_of_allocation_id
                ) VALUES(
                    :cid,:payment_id,:order_id,5,'manual','2026-07-04',:original
                ) RETURNING id"""
            ),
            {
                "cid": cid,
                "payment_id": reversed_payment,
                "order_id": open_sale_id,
                "original": original_allocation,
            },
        ).scalar_one())
        db.execute(
            text(
                """UPDATE payment_allocations
                SET reversed_at=CURRENT_TIMESTAMP,reversal_allocation_id=:reversal
                WHERE id=:original AND company_id=:cid"""
            ),
            {
                "reversal": reversal_allocation,
                "original": original_allocation,
                "cid": cid,
            },
        )
        db.commit()
    reversed_update = client.put(
        f"/api/payments/{reversed_payment}",
        headers=headers,
        json={
            "entity_type": "customer",
            "entity_id": customer,
            "amount": "5",
            "payment_date": "2026-07-05",
            "payment_method": "cash",
        },
    )
    assert reversed_update.status_code == 200, reversed_update.text

    company_b = client.post(
        "/api/companies", headers=headers, json={"name": "Tahsis Tenant B"}
    ).json()["id"]
    headers_b = {**headers, "X-Company-ID": str(company_b), "Idempotency-Key": "cross-tenant"}
    customer_b = client.post(
        "/api/customers", headers=headers_b, json={"name": "Tenant B Cari"}
    ).json()["id"]
    with SessionLocal() as db:
        cross_tenant_payment = int(db.execute(
            text(
                """INSERT INTO payments(
                    entity_type,entity_id,amount,payment_date,company_id,payment_method
                ) VALUES(
                    'customer',:customer,10,'2026-07-04',:cid,'cash'
                ) RETURNING id"""
            ),
            {"customer": customer_b, "cid": company_b},
        ).scalar_one())
        db.commit()
    cross_tenant_update = client.put(
        f"/api/payments/{cross_tenant_payment}",
        headers=headers_b,
        json={
            "entity_type": "customer",
            "entity_id": customer_b,
            "amount": "10",
            "payment_date": "2026-07-04",
            "payment_method": "cash",
            "reference_type": "order",
            "reference_id": open_sale_id,
        },
    )
    assert cross_tenant_update.status_code == 409, cross_tenant_update.text
    with SessionLocal() as db:
        assert db.execute(
            text(
                """SELECT reference_type,reference_id FROM payments
                WHERE id=:id AND company_id=:cid"""
            ),
            {"id": cross_tenant_payment, "cid": company_b},
        ).one() == (None, None)
    cross = client.post(
        "/api/payments",
        headers=headers_b,
        json={
            "entity_type": "customer",
            "entity_id": customer_b,
            "amount": "10",
            "payment_date": "2026-07-04",
            "payment_method": "cash",
            "reference_type": "order",
            "reference_id": open_sale_id,
        },
    )
    assert cross.status_code == 409, cross.text

print("PAYMENT_ALLOCATION_LIVE_OK")
'''


_LEGACY_SMOKE = r'''
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    login = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()
    headers = {
        "Authorization": "Bearer " + login["access_token"],
        "X-Company-ID": str(login["companies"][0]["id"]),
    }
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "admin123", "new_password": "AllocationLegacy123!"},
    )
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    customer = client.post(
        "/api/customers", headers=headers, json={"name": "Legacy Cari"}
    ).json()["id"]
    payload = {
        "entity_type": "customer",
        "entity_id": customer,
        "amount": "25",
        "payment_date": "2026-07-01",
        "payment_method": "cash",
    }
    created = client.post("/api/payments", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    payment_id = created.json()["id"]
    updated = client.put(
        f"/api/payments/{payment_id}",
        headers=headers,
        json={**payload, "amount": "30"},
    )
    assert updated.status_code == 200, updated.text
    deleted = client.delete(f"/api/payments/{payment_id}", headers=headers)
    assert deleted.status_code == 204, deleted.text

print("PAYMENT_ALLOCATION_LEGACY_OK")
'''
