from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_customer_summary_and_last_sale_price_are_tenant_scoped(tmp_path: Path) -> None:
    database = tmp_path / "sales-customer-context.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
changed = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert changed.status_code == 200, changed.text
headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
customer = client.post('/api/customers', headers=headers, json={'name':'Son Fiyat Müşteri','opening_balance':100,'risk_limit':1000})
assert customer.status_code == 201, customer.text
product = client.post('/api/products', headers=headers, json={'name':'Son Fiyat Ürün','purchase_price':10,'sale_price':25,'vat_rate':20,'stock':5,'unit':'Adet'})
assert product.status_code == 201, product.text
warehouse = client.get('/api/warehouses', headers=headers).json()[0]
order = client.post('/api/orders', headers=headers, json={
  'entity_id':customer.json()['id'],'transaction_date':'2026-07-14','warehouse_id':warehouse['id'],
  'status':'completed','payment_method':'credit','paid_amount':0,'discount_percent':0,
  'items':[{'product_id':product.json()['id'],'quantity':1,'unit_price':23.5,'vat_rate':20,'discount_percent':4}],
})
assert order.status_code == 201, order.text
summary = client.get(f"/api/customers/{customer.json()['id']}", headers=headers)
assert summary.status_code == 200, summary.text
assert float(summary.json()['summary']['current_balance']) == 122.56, summary.json()['summary']
last = client.get('/api/orders/last-sale-price', headers=headers, params={'customer_id':customer.json()['id'],'product_id':product.json()['id']})
assert last.status_code == 200, last.text
assert float(last.json()['unit_price']) == 23.5, last.json()
assert float(last.json()['discount_percent']) == 4, last.json()
missing = client.get('/api/orders/last-sale-price', headers=headers, params={'customer_id':customer.json()['id'],'product_id':999999})
assert missing.status_code == 404, missing.text
client.close()
'''
    result = subprocess.run([sys.executable, '-c', smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
