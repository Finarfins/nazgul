from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


BACKEND = Path(__file__).resolve().parent


def test_cari_360_uses_full_history_with_bounded_previews(tmp_path: Path) -> None:
    database = tmp_path / "cari-360-bounded.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)

    smoke = r'''
import sqlite3

from fastapi.testclient import TestClient
from app.main import app


def ok(response):
    assert response.status_code < 300, (response.status_code, response.text)
    return response.json() if response.content else None


client = TestClient(app)
login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
assert login.status_code == 200, login.text
payload = login.json()
company_id = int(payload["companies"][0]["id"])
headers = {
    "Authorization": "Bearer " + payload["access_token"],
    "X-Company-ID": str(company_id),
}
if payload["user"].get("must_change_password"):
    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={
            "current_password": "admin123",
            "new_password": "Cari-360-Guvenli!2026",
        },
    )
    assert changed.status_code == 200, changed.text
    headers["Authorization"] = "Bearer " + changed.json()["access_token"]

customer = ok(
    client.post(
        "/api/customers",
        headers=headers,
        json={
            "name": "301 Belgeli Müşteri",
            "opening_balance": 100,
            "risk_limit": 10000,
            "payment_term_days": 30,
            "is_active": True,
        },
    )
)
supplier = ok(
    client.post(
        "/api/suppliers",
        headers=headers,
        json={
            "name": "301 Belgeli Tedarikçi",
            "opening_balance": 50,
            "risk_limit": 10000,
            "payment_term_days": 30,
            "is_active": True,
        },
    )
)

database_path = __import__("os").environ["DATABASE_URL"].removeprefix("sqlite:///")
con = sqlite3.connect(database_path)
warehouse_id = int(
    con.execute(
        "SELECT id FROM warehouses WHERE company_id=? ORDER BY id LIMIT 1",
        (company_id,),
    ).fetchone()[0]
)
orders = [
    (
        customer["id"],
        "2026-07-01",
        10,
        0,
        10,
        0,
        0,
        10,
        f"C360-S-{index:03d}",
        "2026-07-02",
        None,
        company_id,
        warehouse_id,
        None,
        "completed",
        "credit",
        0,
        "standard",
    )
    for index in range(301)
]
con.executemany(
    """INSERT INTO orders(
    customer_id,order_date,subtotal,vat_total,grand_total,discount_percent,
    discount_amount,final_total,document_no,due_date,note,company_id,
    warehouse_id,branch_id,status,payment_method,paid_amount,payment_term
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
    orders,
)
purchases = [
    (
        supplier["id"],
        "2026-07-01",
        5,
        0,
        5,
        0,
        0,
        5,
        f"C360-A-{index:03d}",
        "2026-07-02",
        None,
        company_id,
        warehouse_id,
        None,
        "completed",
        "credit",
        0,
    )
    for index in range(301)
]
con.executemany(
    """INSERT INTO purchases(
    supplier_id,purchase_date,subtotal,vat_total,grand_total,discount_percent,
    discount_amount,final_total,document_no,due_date,note,company_id,
    warehouse_id,branch_id,status,payment_method,paid_amount
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
    purchases,
)
for index in range(20):
    con.execute(
        """INSERT INTO payments(
        entity_type,entity_id,amount,payment_date,note,company_id,payment_method
        ) VALUES('customer',?,?,?,?,?,'credit')""",
        (customer["id"], 1, "2026-07-03", f"Tahsilat {index}", company_id),
    )
    con.execute(
        """INSERT INTO payments(
        entity_type,entity_id,amount,payment_date,note,company_id,payment_method
        ) VALUES('supplier',?,?,?,?,?,'credit')""",
        (supplier["id"], 1, "2026-07-03", f"Ödeme {index}", company_id),
    )
con.commit()
con.close()

customer_detail = ok(client.get(f"/api/customers/{customer['id']}", headers=headers))
supplier_detail = ok(client.get(f"/api/suppliers/{supplier['id']}", headers=headers))

assert customer_detail["summary"]["document_count"] == 301
assert round(float(customer_detail["summary"]["document_total"]), 2) == 3010.00
assert round(float(customer_detail["summary"]["payment_total"]), 2) == 20.00
assert round(float(customer_detail["summary"]["current_balance"]), 2) == 3090.00
assert round(float(customer_detail["summary"]["overdue_amount"]), 2) == 3010.00
assert len(customer_detail["documents"]) == 8
assert len(customer_detail["payments"]) == 8

assert supplier_detail["summary"]["document_count"] == 301
assert round(float(supplier_detail["summary"]["document_total"]), 2) == 1505.00
assert round(float(supplier_detail["summary"]["payment_total"]), 2) == 20.00
assert round(float(supplier_detail["summary"]["current_balance"]), 2) == 1535.00
assert round(float(supplier_detail["summary"]["overdue_amount"]), 2) == 1505.00
assert len(supplier_detail["documents"]) == 8
assert len(supplier_detail["payments"]) == 8

integrity = sqlite3.connect(database_path)
assert integrity.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
integrity.close()
client.close()
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
