from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_sale_update_delete_keeps_stock_payment_and_finance_consistent(tmp_path: Path) -> None:
    database = tmp_path / "transaction-integrity.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    script = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.db import SessionLocal

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json(); cid = body['companies'][0]['id']
headers = {'Authorization':'Bearer ' + body['access_token'], 'X-Company-ID':str(cid)}
changed = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert changed.status_code == 200, changed.text
headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
warehouse = client.get('/api/warehouses', headers=headers).json()[0]
customer = client.post('/api/customers', headers=headers, json={'name':'Bütünlük Müşterisi','risk_limit':0})
product = client.post('/api/products', headers=headers, json={
    'name':'Bütünlük Ürünü','purchase_price':10,'sale_price':20,
    'vat_rate':0,'stock':10,'unit':'Adet',
})
assert customer.status_code == 201 and product.status_code == 201
payload = {
    'entity_id':customer.json()['id'],'transaction_date':'2026-07-13',
    'warehouse_id':warehouse['id'],'status':'completed','payment_method':'cash',
    'paid_amount':40,'discount_percent':0,
    'items':[{'product_id':product.json()['id'],'quantity':2,'unit_price':20,'vat_rate':0,'discount_percent':0}],
}
created = client.post('/api/orders', headers=headers, json=payload)
assert created.status_code == 201, created.text
order_id = created.json()['id']

with SessionLocal() as db:
    movement_count = db.execute(text("SELECT COUNT(*) FROM stock_movements WHERE reference_type='orders' AND reference_id=:id"), {'id':order_id}).scalar_one()
    payment = db.execute(text("SELECT id,financial_transaction_id FROM payments WHERE reference_type='order' AND reference_id=:id"), {'id':order_id}).first()
    assert movement_count == 1
    assert payment and payment[1]
    original_finance_id = int(payment[1])

payload['items'][0]['quantity'] = 3
payload['paid_amount'] = 60
updated = client.put(f'/api/orders/{order_id}', headers=headers, json=payload)
assert updated.status_code == 200, updated.text

with SessionLocal() as db:
    movement_count = db.execute(text("SELECT COUNT(*) FROM stock_movements WHERE reference_type='orders' AND reference_id=:id"), {'id':order_id}).scalar_one()
    payment = db.execute(text("SELECT id,financial_transaction_id FROM payments WHERE reference_type='order' AND reference_id=:id"), {'id':order_id}).first()
    linked_finance_count = db.execute(text("SELECT COUNT(*) FROM finance_transactions ft JOIN payments p ON p.financial_transaction_id=ft.id WHERE p.reference_type='order' AND p.reference_id=:id"), {'id':order_id}).scalar_one()
    orphan_finance_count = db.execute(text("SELECT COUNT(*) FROM finance_transactions WHERE reference_type='payment' AND reference_id NOT IN (SELECT id FROM payments)")).scalar_one()
    assert movement_count == 1
    assert payment and payment[1]
    assert linked_finance_count == 1
    assert orphan_finance_count == 0
    current_finance_id = int(payment[1])
    assert db.execute(text('SELECT COUNT(*) FROM finance_transactions WHERE id=:id'), {'id':original_finance_id}).scalar_one() == (1 if current_finance_id == original_finance_id else 0)

removed = client.delete(f'/api/orders/{order_id}', headers=headers)
assert removed.status_code == 204, removed.text
with SessionLocal() as db:
    assert db.execute(text("SELECT COUNT(*) FROM stock_movements WHERE reference_type='orders' AND reference_id=:id"), {'id':order_id}).scalar_one() == 0
    assert db.execute(text("SELECT COUNT(*) FROM payments WHERE reference_type='order' AND reference_id=:id"), {'id':order_id}).scalar_one() == 0
    assert db.execute(text('SELECT COUNT(*) FROM finance_transactions WHERE id=:id'), {'id':current_finance_id}).scalar_one() == 0
    assert db.execute(text('SELECT COUNT(*) FROM orders WHERE id=:id'), {'id':order_id}).scalar_one() == 0
client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
