from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
APP_DIR = BACKEND / "app"


def test_runtime_has_no_sqlite_only_id_or_date_patterns() -> None:
    forbidden = (
        "AUTOINCREMENT",
        "lastrowid",
        "last_insert_rowid",
        "instr(",
        "date('now')",
        'date("now")',
    )
    offenders: list[str] = []
    for path in APP_DIR.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                offenders.append(f"{path.relative_to(BACKEND)}: {token}")
    assert not offenders, "\n".join(offenders)


def test_clean_sqlite_install_and_multicompany_restart(tmp_path: Path) -> None:
    database = tmp_path / "clean.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)

    smoke = r'''
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from app.main import app
from app.db import engine

required = {
    "customers", "suppliers", "products", "orders", "order_items",
    "purchases", "purchase_items", "payments", "stock_movements",
    "quotes", "quote_items", "returns", "return_items", "income_expenses",
    "warehouses", "warehouse_stocks", "finance_accounts", "sales_orders",
}
missing = required - set(inspect(engine).get_table_names())
assert not missing, missing

client = TestClient(app)
login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
assert login.status_code == 200, login.text
body = login.json()
company_id = body["companies"][0]["id"]
headers = {
    "Authorization": "Bearer " + body["access_token"],
    "X-Company-ID": str(company_id),
}
password_change = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert password_change.status_code == 200, password_change.text
headers['Authorization'] = 'Bearer ' + password_change.json()['access_token']
for path in ("/api/health", "/api/dashboard", "/api/customers", "/api/products", "/api/warehouses"):
    response = client.get(path, headers=headers)
    assert response.status_code == 200, (path, response.status_code, response.text)

customer = client.post("/api/customers", headers=headers, json={
    "name": "Temiz Kurulum Müşterisi", "opening_balance": 0,
    "risk_limit": 0, "payment_term_days": 0, "is_active": True,
})
assert customer.status_code == 201, customer.text
product = client.post("/api/products", headers=headers, json={
    "name": "Temiz Kurulum Ürünü", "purchase_price": 10, "sale_price": 20,
    "vat_rate": 20, "stock": 5, "unit": "Adet", "category": "Test",
})
assert product.status_code == 201, product.text
warehouse = client.get("/api/warehouses", headers=headers).json()[0]
order = client.post("/api/orders", headers=headers, json={
    "entity_id": customer.json()["id"], "transaction_date": "2026-07-12",
    "warehouse_id": warehouse["id"], "status": "completed",
    "payment_method": "credit", "paid_amount": 0, "discount_percent": 0,
    "items": [{"product_id": product.json()["id"], "quantity": 1,
               "unit_price": 20, "vat_rate": 20, "discount_percent": 0}],
})
assert order.status_code == 201, order.text

company_b = client.post("/api/companies", headers=headers, json={"name": "Firma B"})
assert company_b.status_code == 201, company_b.text
client.close()
'''
    first = subprocess.run(
        [sys.executable, "-c", smoke], cwd=BACKEND, env=env,
        text=True, capture_output=True, timeout=90,
    )
    assert first.returncode == 0, first.stdout + "\n" + first.stderr

    restart = subprocess.run(
        [sys.executable, "-c", "from app.main import app; print('restart-ok')"],
        cwd=BACKEND, env=env, text=True, capture_output=True, timeout=90,
    )
    assert restart.returncode == 0, restart.stdout + "\n" + restart.stderr
