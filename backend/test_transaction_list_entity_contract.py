from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def test_transaction_lists_expose_customer_and_supplier_entity_ids(tmp_path: Path) -> None:
    database = tmp_path / "transaction-list-entity.db"
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
headers = {
    'Authorization':'Bearer '+body['access_token'],
    'X-Company-ID':str(body['companies'][0]['id']),
}
changed = client.post('/api/auth/change-password', headers=headers, json={
    'current_password':'admin123',
    'new_password':'Contract!2026',
})
assert changed.status_code == 200, changed.text
headers['Authorization'] = 'Bearer '+changed.json()['access_token']

def ok(response):
    assert response.status_code < 300, (response.status_code, response.text)
    return response.json() if response.content else None

customer = ok(client.post('/api/customers', headers=headers, json={
    'name':'Liste Sözleşme Müşterisi',
    'risk_limit':100000,
}))
supplier = ok(client.post('/api/suppliers', headers=headers, json={
    'name':'Liste Sözleşme Tedarikçisi',
}))
warehouse = ok(client.get('/api/warehouses', headers=headers))[0]
product = ok(client.post('/api/products', headers=headers, json={
    'name':'Liste Sözleşme Ürünü',
    'purchase_price':10,
    'sale_price':20,
    'vat_rate':20,
    'stock':1,
    'unit':'Adet',
}))
order = ok(client.post('/api/orders', headers=headers, json={
    'entity_id':customer['id'],
    'transaction_date':'2026-07-29',
    'warehouse_id':warehouse['id'],
    'status':'draft',
    'items':[{
        'product_id':product['id'],
        'quantity':1,
        'unit_price':20,
        'vat_rate':20,
    }],
}))
purchase = ok(client.post('/api/purchases', headers=headers, json={
    'entity_id':supplier['id'],
    'transaction_date':'2026-07-29',
    'warehouse_id':warehouse['id'],
    'status':'draft',
    'items':[{
        'product_id':product['id'],
        'quantity':1,
        'unit_price':10,
        'vat_rate':20,
    }],
}))

listed_order = next(row for row in ok(client.get('/api/orders', headers=headers)) if row['id']==order['id'])
assert 'entity_id' in listed_order, listed_order
assert listed_order['entity_id'] == customer['id'], listed_order
listed_purchase = next(
    row for row in ok(client.get('/api/purchases', headers=headers))
    if row['id']==purchase['id']
)
assert 'entity_id' in listed_purchase, listed_purchase
assert listed_purchase['entity_id'] == supplier['id'], listed_purchase
client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
