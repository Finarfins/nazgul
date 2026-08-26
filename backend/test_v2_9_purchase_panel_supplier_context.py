from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_supplier_summary_and_last_purchase_price_are_tenant_scoped(tmp_path: Path) -> None:
    database = tmp_path / "purchase-supplier-context.db"
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
supplier = client.post('/api/suppliers', headers=headers, json={'name':'Son Alış Tedarikçi','opening_balance':50,'risk_limit':1000})
assert supplier.status_code == 201, supplier.text
product = client.post('/api/products', headers=headers, json={'name':'Son Alış Ürün','purchase_price':10,'sale_price':25,'vat_rate':20,'stock':0,'unit':'Adet'})
assert product.status_code == 201, product.text
warehouse = client.get('/api/warehouses', headers=headers).json()[0]
purchase = client.post('/api/purchases', headers=headers, json={
  'entity_id':supplier.json()['id'],'transaction_date':'2026-07-15','warehouse_id':warehouse['id'],
  'status':'completed','payment_method':'credit','paid_amount':0,'discount_percent':0,
  'items':[{'product_id':product.json()['id'],'quantity':2,'unit_price':11.75,'vat_rate':20,'discount_percent':5}],
})
assert purchase.status_code == 201, purchase.text
summary = client.get(f"/api/suppliers/{supplier.json()['id']}", headers=headers)
assert summary.status_code == 200, summary.text
assert float(summary.json()['summary']['current_balance']) == 72.32, summary.json()['summary']
last = client.get('/api/purchases/last-purchase-price', headers=headers, params={'supplier_id':supplier.json()['id'],'product_id':product.json()['id']})
assert last.status_code == 200, last.text
assert float(last.json()['unit_price']) == 11.75, last.json()
assert float(last.json()['discount_percent']) == 5, last.json()
missing = client.get('/api/purchases/last-purchase-price', headers=headers, params={'supplier_id':supplier.json()['id'],'product_id':999999})
assert missing.status_code == 404, missing.text
client.close()
'''
    result = subprocess.run([sys.executable, '-c', smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
