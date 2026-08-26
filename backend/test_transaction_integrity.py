from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_transaction_update_and_delete_keep_stock_and_finance_consistent(tmp_path: Path) -> None:
    database = tmp_path / "transaction-integrity.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.db import SessionLocal

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
assert client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'Admin123!'}).status_code == 200

customer = client.post('/api/customers', headers=headers, json={'name':'İşlem Bütünlüğü Müşterisi','risk_limit':100000})
assert customer.status_code == 201, customer.text
product = client.post('/api/products', headers=headers, json={'name':'İşlem Bütünlüğü Ürünü','purchase_price':10,'sale_price':20,'vat_rate':20,'stock':20,'unit':'Adet'})
assert product.status_code == 201, product.text
warehouse = client.get('/api/warehouses', headers=headers).json()[0]

payload = {
    'entity_id':customer.json()['id'],'transaction_date':'2026-07-15','warehouse_id':warehouse['id'],
    'status':'completed','payment_method':'cash','paid_amount':40,'discount_percent':0,
    'items':[{'product_id':product.json()['id'],'quantity':2,'unit_price':20,'vat_rate':20,'discount_percent':0}],
}
created = client.post('/api/orders', json=payload, headers=headers)
assert created.status_code == 201, created.text
order_id = created.json()['id']

with SessionLocal() as db:
    assert db.execute(text("SELECT COUNT(*) FROM stock_movements WHERE reference_type='orders' AND reference_id=:id"), {'id':order_id}).scalar_one() == 1
    payment = db.execute(text("SELECT id,financial_transaction_id FROM payments WHERE reference_type='order' AND reference_id=:id"), {'id':order_id}).first()
    assert payment and payment[1]

payload['items'][0]['quantity'] = 3
payload['paid_amount'] = 60
updated = client.put(f'/api/orders/{order_id}', json=payload, headers=headers)
assert updated.status_code == 200, updated.text

with SessionLocal() as db:
    assert db.execute(text("SELECT COUNT(*) FROM stock_movements WHERE reference_type='orders' AND reference_id=:id"), {'id':order_id}).scalar_one() == 1
    payment = db.execute(text("SELECT id,financial_transaction_id FROM payments WHERE reference_type='order' AND reference_id=:id"), {'id':order_id}).first()
    assert payment and payment[1]
    finance_id = int(payment[1])
    assert db.execute(text("SELECT COUNT(*) FROM finance_transactions WHERE reference_type='payment' AND reference_id NOT IN (SELECT id FROM payments)" )).scalar_one() == 0

removed = client.delete(f'/api/orders/{order_id}', headers=headers)
assert removed.status_code == 204, removed.text
with SessionLocal() as db:
    assert db.execute(text("SELECT COUNT(*) FROM stock_movements WHERE reference_type='orders' AND reference_id=:id"), {'id':order_id}).scalar_one() == 0
    assert db.execute(text("SELECT COUNT(*) FROM payments WHERE reference_type='order' AND reference_id=:id"), {'id':order_id}).scalar_one() == 0
    assert db.execute(text("SELECT COUNT(*) FROM finance_transactions WHERE id=:id"), {'id':finance_id}).scalar_one() == 0
    assert db.execute(text("SELECT COUNT(*) FROM orders WHERE id=:id"), {'id':order_id}).scalar_one() == 0
client.close()
'''
    result = subprocess.run([sys.executable, "-c", smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
