from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_documents_use_selected_warehouse_and_reverse_stock_on_delete(tmp_path: Path) -> None:
    database = tmp_path / "transaction-warehouse.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
import sqlite3
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
assert client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'Admin123!'}).status_code == 200
assert client.put('/api/company-settings', headers=headers, json={'negative_stock_policy':'allow','credit_limit_policy':'block'}).status_code == 200

def ok(response):
    assert response.status_code < 300, (response.status_code, response.text)
    return response.json() if response.content else None

source = ok(client.get('/api/warehouses', headers=headers))[0]['id']
target = ok(client.post('/api/warehouses', headers=headers, json={'name':'Belge Test Deposu','code':'BTD'}))['id']
product = ok(client.post('/api/products', headers=headers, json={'name':'Depo Test Ürünü','purchase_price':60,'sale_price':100,'vat_rate':20,'stock':0,'unit':'Adet'}))
pid = product['id']
customer = ok(client.post('/api/customers', headers=headers, json={'name':'Depo Test Müşterisi','risk_limit':100000}))
supplier = ok(client.post('/api/suppliers', headers=headers, json={'name':'Depo Test Tedarikçisi'}))

rows = ok(client.get('/api/products', headers=headers, params={'warehouse_id':target,'limit':2000}))
assert next(x for x in rows if x['id']==pid)['warehouse_stock'] == 0

negative_sale = ok(client.post('/api/orders', headers=headers, json={'entity_id':customer['id'],'transaction_date':'2026-07-15','warehouse_id':target,'items':[{'product_id':pid,'quantity':1,'unit_price':100,'vat_rate':20}]}))
rows = ok(client.get('/api/products', headers=headers, params={'warehouse_id':target,'limit':2000}))
assert next(x for x in rows if x['id']==pid)['warehouse_stock'] == -1

purchase = ok(client.post('/api/purchases', headers=headers, json={'entity_id':supplier['id'],'transaction_date':'2026-07-15','warehouse_id':target,'items':[{'product_id':pid,'quantity':7,'unit_price':60,'vat_rate':20}]}))
rows = ok(client.get('/api/products', headers=headers, params={'warehouse_id':target,'limit':2000}))
assert next(x for x in rows if x['id']==pid)['warehouse_stock'] == 6

sale = ok(client.post('/api/orders', headers=headers, json={'entity_id':customer['id'],'transaction_date':'2026-07-15','due_date':'2026-08-15','warehouse_id':target,'items':[{'product_id':pid,'quantity':3,'unit_price':100,'vat_rate':20}]}))
rows = ok(client.get('/api/products', headers=headers, params={'warehouse_id':target,'limit':2000}))
assert next(x for x in rows if x['id']==pid)['warehouse_stock'] == 3
listed = next(x for x in ok(client.get('/api/orders', headers=headers)) if x['id']==sale['id'])
assert listed['warehouse_name'] == 'Belge Test Deposu'
detail = ok(client.get(f"/api/orders/{sale['id']}", headers=headers))
assert detail['document']['warehouse_name'] == 'Belge Test Deposu'
assert detail['document']['due_date'] == '2026-08-15'

assert client.delete(f"/api/orders/{sale['id']}", headers=headers).status_code == 204
rows = ok(client.get('/api/products', headers=headers, params={'warehouse_id':target,'limit':2000}))
assert next(x for x in rows if x['id']==pid)['warehouse_stock'] == 6
assert client.delete(f"/api/purchases/{purchase['id']}", headers=headers).status_code == 204
assert client.delete(f"/api/orders/{negative_sale['id']}", headers=headers).status_code == 204
client.close()
'''
    result = subprocess.run([sys.executable, "-c", smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
