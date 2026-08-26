from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_transaction_and_workflow_totals_load_products_in_one_query(tmp_path: Path) -> None:
    database = tmp_path / "batch-product-lookup.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    script = r'''
from sqlalchemy import event
from fastapi.testclient import TestClient
from app.main import app
from app.db import engine

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json(); cid = body['companies'][0]['id']
headers = {'Authorization':'Bearer ' + body['access_token'], 'X-Company-ID':str(cid)}
changed = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert changed.status_code == 200, changed.text
headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
warehouse = client.get('/api/warehouses', headers=headers).json()[0]
customer = client.post('/api/customers', headers=headers, json={'name':'Toplu Sorgu Müşterisi','risk_limit':0})
assert customer.status_code == 201, customer.text
products = []
for index in range(3):
    response = client.post('/api/products', headers=headers, json={
        'name':f'Toplu Ürün {index + 1}','code':f'BATCH-{index + 1}',
        'purchase_price':10,'sale_price':20 + index,'vat_rate':20,
        'stock':20,'unit':'Adet',
    })
    assert response.status_code == 201, response.text
    products.append(response.json()['id'])

statements = []
def capture(conn, cursor, statement, parameters, context, executemany):
    normalized = ' '.join(statement.lower().split())
    if 'from products' in normalized and normalized.startswith('select'):
        statements.append(normalized)

event.listen(engine, 'before_cursor_execute', capture)
try:
    payload = {
        'entity_id':customer.json()['id'],'transaction_date':'2026-07-15',
        'warehouse_id':warehouse['id'],'status':'completed','payment_method':'credit',
        'paid_amount':0,'discount_percent':0,
        'items':[
            {'product_id':product_id,'quantity':1,'unit_price':20 + index,'vat_rate':20,'discount_percent':0}
            for index, product_id in enumerate(products)
        ],
    }
    created = client.post('/api/orders', headers=headers, json=payload)
    assert created.status_code == 201, created.text
    order_product_selects = list(statements)
    statements.clear()

    quote_payload = {
        'entity_id':customer.json()['id'],'document_date':'2026-07-15',
        'warehouse_id':warehouse['id'],'status':'draft','discount_percent':0,
        'items':payload['items'],
    }
    quote = client.post('/api/workflow/quote', headers=headers, json=quote_payload)
    assert quote.status_code == 200, quote.text
    quote_product_selects = list(statements)
finally:
    event.remove(engine, 'before_cursor_execute', capture)
    client.close()

assert len(order_product_selects) == 1, order_product_selects
assert ' in (' in order_product_selects[0], order_product_selects
assert len(quote_product_selects) == 1, quote_product_selects
assert ' in (' in quote_product_selects[0], quote_product_selects
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
