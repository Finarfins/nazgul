from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_sale_selected_finance_account_is_used(tmp_path: Path) -> None:
    database = tmp_path / "sales-panel.db"
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
changed = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert changed.status_code == 200, changed.text
headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
customer = client.post('/api/customers', headers=headers, json={'name':'Sprint 3 Müşteri','opening_balance':0})
assert customer.status_code == 201, customer.text
product = client.post('/api/products', headers=headers, json={'name':'Sprint 3 Ürün','purchase_price':10,'sale_price':20,'vat_rate':20,'stock':5,'unit':'Adet'})
assert product.status_code == 201, product.text
warehouse = client.get('/api/warehouses', headers=headers).json()[0]
accounts = client.get('/api/finance/accounts?active_only=true', headers=headers)
assert accounts.status_code == 200, accounts.text
cash = next(a for a in accounts.json() if a['account_type']=='cash')
order = client.post('/api/orders', headers=headers, json={
  'entity_id':customer.json()['id'],'transaction_date':'2026-07-14','warehouse_id':warehouse['id'],
  'status':'completed','payment_method':'cash','paid_amount':20,'payment_account_id':cash['id'],'discount_percent':0,
  'items':[{'product_id':product.json()['id'],'quantity':1,'unit_price':20,'vat_rate':20,'discount_percent':0}],
})
assert order.status_code == 201, order.text
with SessionLocal() as db:
    row = db.execute(text("SELECT account_id,financial_transaction_id FROM payments WHERE reference_type='order' AND reference_id=:rid"), {'rid':order.json()['id']}).first()
    assert row and int(row[0]) == int(cash['id']) and row[1] is not None, row
bank = next(a for a in accounts.json() if a['account_type']=='bank')
bad = client.post('/api/orders', headers=headers, json={
  'entity_id':customer.json()['id'],'transaction_date':'2026-07-14','warehouse_id':warehouse['id'],
  'status':'completed','payment_method':'cash','paid_amount':20,'payment_account_id':bank['id'],'discount_percent':0,
  'items':[{'product_id':product.json()['id'],'quantity':1,'unit_price':20,'vat_rate':20,'discount_percent':0}],
})
assert bad.status_code == 400, bad.text
client.close()
'''
    result = subprocess.run([sys.executable, '-c', smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
