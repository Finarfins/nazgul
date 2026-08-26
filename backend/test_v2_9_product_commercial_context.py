from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_product_detail_contains_tenant_scoped_commercial_context(tmp_path: Path) -> None:
    database = tmp_path / "product-commercial-context.db"
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
customer = client.post('/api/customers', headers=headers, json={'name':'Ürün Geçmiş Müşteri','opening_balance':0})
assert customer.status_code == 201, customer.text
supplier = client.post('/api/suppliers', headers=headers, json={'name':'Ürün Geçmiş Tedarikçi','opening_balance':0})
assert supplier.status_code == 201, supplier.text
product = client.post('/api/products', headers=headers, json={
  'name':'CS640 Test Kayışı','product_code':'CS640-TST','barcode':'8691234567890',
  'purchase_price':100,'sale_price':180,'vat_rate':20,'stock':10,'unit':'Adet',
  'location':'A-09','oem_number':'84970001','alternative_oem':'84970002,84970003',
  'brand':'Rubena','manufacturer':'Rubena','compatible_models':'CS640, TC56','technical_notes':'Test teknik notu'
})
assert product.status_code == 201, product.text
pid = product.json()['id']
warehouse = client.get('/api/warehouses', headers=headers).json()[0]
purchase = client.post('/api/purchases', headers=headers, json={
  'entity_id':supplier.json()['id'],'transaction_date':'2026-07-10','warehouse_id':warehouse['id'],
  'status':'completed','payment_method':'credit','paid_amount':0,'discount_percent':0,
  'items':[{'product_id':pid,'quantity':3,'unit_price':110,'vat_rate':20,'discount_percent':5}],
})
assert purchase.status_code == 201, purchase.text
order = client.post('/api/orders', headers=headers, json={
  'entity_id':customer.json()['id'],'transaction_date':'2026-07-12','warehouse_id':warehouse['id'],
  'status':'completed','payment_method':'credit','paid_amount':0,'discount_percent':0,
  'items':[{'product_id':pid,'quantity':2,'unit_price':175,'vat_rate':20,'discount_percent':4}],
})
assert order.status_code == 201, order.text
detail = client.get(f'/api/products/{pid}', headers=headers)
assert detail.status_code == 200, detail.text
payload = detail.json()
assert payload['product']['oem_number'] == '84970001'
assert payload['warehouse_stocks'], payload
assert payload['sales_history'][0]['customer_name'] == 'Ürün Geçmiş Müşteri'
assert float(payload['sales_history'][0]['unit_price']) == 175
assert payload['purchase_history'][0]['supplier_name'] == 'Ürün Geçmiş Tedarikçi'
assert float(payload['purchase_history'][0]['unit_price']) == 110
summary = payload['commercial_summary']
assert float(summary['total_sold_quantity']) == 2
assert float(summary['total_purchased_quantity']) == 3
assert float(summary['last_sale_price']) == 175
assert float(summary['last_purchase_price']) == 110
client.close()
'''
    result = subprocess.run(
        [sys.executable, '-c', smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
