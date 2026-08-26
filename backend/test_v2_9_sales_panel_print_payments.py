from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_split_payments_are_exposed_in_detail_and_pdf_context(tmp_path: Path) -> None:
    database = tmp_path / "print-payments.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.routers.outputs import _document

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
_pw = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert _pw.status_code == 200, _pw.text
headers['Authorization'] = 'Bearer ' + _pw.json()['access_token']
customer = client.post('/api/customers', headers=headers, json={'name':'Fiş Müşterisi','opening_balance':0}).json()
product = client.post('/api/products', headers=headers, json={'name':'Fiş Ürünü','purchase_price':50,'sale_price':100,'vat_rate':20,'stock':5,'unit':'Adet'}).json()
warehouse = client.get('/api/warehouses', headers=headers).json()[0]
accounts = client.get('/api/finance/accounts?active_only=true', headers=headers).json()
cash = next(a for a in accounts if a['account_type']=='cash')
pos = next(a for a in accounts if a['account_type']=='pos')
order = client.post('/api/orders', headers=headers, json={
  'entity_id':customer['id'],'transaction_date':'2026-07-14','warehouse_id':warehouse['id'],
  'status':'completed','payment_method':'mixed','paid_amount':75,'discount_percent':0,
  'payment_allocations':[
    {'payment_method':'cash','amount':25,'account_id':cash['id']},
    {'payment_method':'card','amount':50,'account_id':pos['id']},
  ],
  'items':[{'product_id':product['id'],'quantity':1,'unit_price':100,'vat_rate':20,'discount_percent':0}],
})
assert order.status_code == 201, order.text
order_id = order.json()['id']
detail = client.get(f'/api/orders/{order_id}', headers=headers)
assert detail.status_code == 200, detail.text
assert [(x['payment_method'], float(x['amount'])) for x in detail.json()['payments']] == [('cash',25.0),('card',50.0)]
with SessionLocal() as db:
    _, head, _ = _document(db, int(body['companies'][0]['id']), 'orders', order_id)
    assert [(x['payment_method'], float(x['amount'])) for x in head['payments']] == [('cash',25.0),('card',50.0)]
    assert float(head['paid_total']) == 75.0
    assert float(head['remaining_total']) == 25.0
pdf = client.get(f'/api/documents/orders/{order_id}/pdf', headers=headers)
assert pdf.status_code == 200, pdf.text
assert 'application/pdf' in pdf.headers['content-type']
assert len(pdf.content) > 500
client.close()
'''
    result = subprocess.run([sys.executable, '-c', smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
