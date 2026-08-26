from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def run_guard_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.document_engine import SALES_IMPORT_NOTE
from app.main import app

BLOCK_MESSAGE = (
    "Bu belge içe aktarılmış geçmiş satıştır; onaylanırsa stok çift "
    "düşer. İçe aktarılan satışlar taslak olarak kalır."
)

with TestClient(app) as client:
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={
            "current_password": "admin123",
            "new_password": "ImportGuard123!",
        },
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    company_id = int(body["companies"][0]["id"])

    customer = client.post(
        "/api/customers",
        headers=headers,
        json={"name": "İçe Aktarım Kilidi Müşterisi", "risk_limit": 100000},
    )
    assert customer.status_code == 201, customer.text
    product = client.post(
        "/api/products",
        headers=headers,
        json={
            "name": "İçe Aktarım Kilidi Ürünü",
            "purchase_price": 10,
            "sale_price": 20,
            "vat_rate": 20,
            "stock": 100,
            "unit": "Adet",
        },
    )
    assert product.status_code == 201, product.text
    product_id = int(product.json()["id"])
    warehouse_id = int(client.get("/api/warehouses", headers=headers).json()[0]["id"])

    def stock() -> Decimal:
        with SessionLocal() as db:
            value = db.execute(
                text(
                    "SELECT quantity FROM warehouse_stocks "
                    "WHERE company_id=:cid AND warehouse_id=:wid AND product_id=:pid"
                ),
                {"cid": company_id, "wid": warehouse_id, "pid": product_id},
            ).scalar_one()
        return Decimal(str(value))

    def payload(*, note: str, status: str, quantity: int) -> dict:
        return {
            "entity_id": int(customer.json()["id"]),
            "transaction_date": "2026-07-25",
            "warehouse_id": warehouse_id,
            "status": status,
            "payment_method": "credit",
            "paid_amount": 0,
            "discount_percent": 0,
            "note": note,
            "items": [
                {
                    "product_id": product_id,
                    "quantity": quantity,
                    "unit_price": 20,
                    "vat_rate": 20,
                    "discount_percent": 0,
                }
            ],
        }

    initial_stock = stock()
    imported_ids = []
    for target_status in ("approved", "completed"):
        draft_payload = payload(note=SALES_IMPORT_NOTE, status="draft", quantity=2)
        created = client.post("/api/orders", headers=headers, json=draft_payload)
        assert created.status_code == 201, created.text
        imported_ids.append(int(created.json()["id"]))
        assert stock() == initial_stock

        blocked_payload = {**draft_payload, "status": target_status}
        blocked = client.put(
            f"/api/orders/{created.json()['id']}",
            headers=headers,
            json=blocked_payload,
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"] == BLOCK_MESSAGE
        assert stock() == initial_stock

    editable_payload = payload(note=SALES_IMPORT_NOTE, status="draft", quantity=3)
    edited = client.put(
        f"/api/orders/{imported_ids[0]}",
        headers=headers,
        json=editable_payload,
    )
    assert edited.status_code == 200, edited.text
    assert stock() == initial_stock

    normal_payload = payload(note="Normal taslak satış", status="draft", quantity=4)
    normal = client.post("/api/orders", headers=headers, json=normal_payload)
    assert normal.status_code == 201, normal.text
    approved = client.put(
        f"/api/orders/{normal.json()['id']}",
        headers=headers,
        json={**normal_payload, "status": "approved"},
    )
    assert approved.status_code == 200, approved.text
    assert stock() == initial_stock - Decimal("4")
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_imported_draft_sale_cannot_apply_stock(tmp_path: Path) -> None:
    database = tmp_path / "import-draft-stock-guard.db"
    run_guard_smoke(f"sqlite:///{database.as_posix()}")
