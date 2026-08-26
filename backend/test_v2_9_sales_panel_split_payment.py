from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_sale_split_payment_creates_separate_finance_transactions(tmp_path: Path) -> None:
    database = tmp_path / "split-payment.db"
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
_pw = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert _pw.status_code == 200, _pw.text
headers['Authorization'] = 'Bearer ' + _pw.json()['access_token']
customer = client.post('/api/customers', headers=headers, json={'name':'Bölünmüş Ödeme Müşterisi','opening_balance':0})
product = client.post('/api/products', headers=headers, json={'name':'Bölünmüş Ödeme Ürünü','purchase_price':50,'sale_price':100,'vat_rate':20,'stock':5,'unit':'Adet'})
warehouse = client.get('/api/warehouses', headers=headers).json()[0]
accounts = client.get('/api/finance/accounts?active_only=true', headers=headers).json()
cash = next(a for a in accounts if a['account_type']=='cash')
pos = next(a for a in accounts if a['account_type']=='pos')
order = client.post('/api/orders', headers=headers, json={
  'entity_id':customer.json()['id'],'transaction_date':'2026-07-14','warehouse_id':warehouse['id'],
  'status':'completed','payment_method':'mixed','paid_amount':100,'discount_percent':0,
  'payment_allocations':[
    {'payment_method':'cash','amount':40,'account_id':cash['id']},
    {'payment_method':'card','amount':60,'account_id':pos['id']},
  ],
  'items':[{'product_id':product.json()['id'],'quantity':1,'unit_price':100,'vat_rate':20,'discount_percent':0}],
})
assert order.status_code == 201, order.text
assert float(order.json()['paid_amount']) == 100
assert float(order.json()['remaining_amount']) == 0
with SessionLocal() as db:
    payments = db.execute(text("SELECT amount,payment_method,account_id,financial_transaction_id FROM payments WHERE reference_type='order' AND reference_id=:rid ORDER BY amount"), {'rid':order.json()['id']}).all()
    assert len(payments) == 2, payments
    assert [(float(x[0]), x[1], int(x[2])) for x in payments] == [(40.0,'cash',int(cash['id'])),(60.0,'card',int(pos['id']))], payments
    assert all(x[3] is not None for x in payments), payments

bad = client.post('/api/orders', headers=headers, json={
  'entity_id':customer.json()['id'],'transaction_date':'2026-07-14','warehouse_id':warehouse['id'],
  'status':'completed','payment_method':'mixed','paid_amount':110,'discount_percent':0,
  'payment_allocations':[{'payment_method':'cash','amount':110,'account_id':cash['id']}],
  'items':[{'product_id':product.json()['id'],'quantity':1,'unit_price':100,'vat_rate':20,'discount_percent':0}],
})
assert bad.status_code == 400, bad.text
client.close()
'''
    result = subprocess.run([sys.executable, '-c', smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
