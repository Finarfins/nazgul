from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_workflow_and_cross_tenant_idor_on_clean_database(tmp_path: Path) -> None:
    database = tmp_path / "workflow.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    script = r'''
from fastapi.testclient import TestClient
from app.main import app
from app.db import engine
from sqlalchemy import text

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
company_id = body['companies'][0]['id']
headers = {
    'Authorization':'Bearer ' + body['access_token'],
    'X-Company-ID':str(company_id),
}
password_change = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert password_change.status_code == 200, password_change.text
headers['Authorization'] = 'Bearer ' + password_change.json()['access_token']
customer = client.post('/api/customers', headers=headers, json={
    'name':'Workflow Müşterisi','opening_balance':0,'risk_limit':0,
    'payment_term_days':0,'is_active':True,
})
assert customer.status_code == 201, customer.text
product = client.post('/api/products', headers=headers, json={
    'name':'Workflow Ürünü','purchase_price':50,'sale_price':100,
    'vat_rate':20,'stock':10,'unit':'Adet','category':'Test',
})
assert product.status_code == 201, product.text
product_2 = client.post('/api/products', headers=headers, json={
    'name':'Workflow Ürünü 2','purchase_price':0.05,'sale_price':0.10,
    'vat_rate':20,'stock':10,'unit':'Adet','category':'Test',
})
assert product_2.status_code == 201, product_2.text
warehouses = client.get('/api/warehouses', headers=headers)
assert warehouses.status_code == 200 and warehouses.json(), warehouses.text
customer_id = customer.json()['id']
product_id = product.json()['id']
product_2_id = product_2.json()['id']
warehouse_id = warehouses.json()[0]['id']
payload = {
    'entity_id':customer_id,'document_date':'2026-07-11',
    'valid_until':'2026-07-20','warehouse_id':warehouse_id,
    'status':'approved','discount_percent':0,
    'items':[{'product_id':product_id,'quantity':2,'unit_price':100,
              'vat_rate':20,'discount_percent':0}],
}
quote = client.post('/api/workflow/quote', headers=headers, json=payload)
assert quote.status_code == 200, quote.text
quote_id = quote.json()['id']
assert client.get(f'/api/workflow/quote/{quote_id}', headers=headers).status_code == 200
sales_order = client.post(f'/api/workflow/quote/{quote_id}/to-order', headers=headers)
assert sales_order.status_code == 200, sales_order.text
sales_order_again = client.post(f'/api/workflow/quote/{quote_id}/to-order', headers=headers)
assert sales_order_again.status_code == 200, sales_order_again.text
assert sales_order_again.json()['id'] == sales_order.json()['id']
assert sales_order_again.json()['already_converted'] is True
sale = client.post(
    f"/api/workflow/sales_order/{sales_order.json()['id']}/to-sale",
    headers=headers,
)
assert sale.status_code == 200, sale.text
sale_again = client.post(
    f"/api/workflow/sales_order/{sales_order.json()['id']}/to-sale",
    headers=headers,
)
assert sale_again.status_code == 200, sale_again.text
assert sale_again.json()['id'] == sale.json()['id']
assert sale_again.json()['already_converted'] is True
conflicting_conversion = client.post(
    f"/api/workflow/sales_order/{sales_order.json()['id']}/to-delivery",
    headers=headers,
)
assert conflicting_conversion.status_code == 409, conflicting_conversion.text
before = client.get(f'/api/products/{product_id}', headers=headers).json()['product']['stock']
return_payload = {
    'entity_id':customer_id,'document_date':'2026-07-12',
    'warehouse_id':warehouse_id,'status':'completed',
    'source_type':'order','source_id':sale.json()['id'],
    'items':[{'product_id':product_id,'quantity':1,'unit_price':100,
              'vat_rate':20,'discount_percent':0}],
}
returned = client.post('/api/workflow/sale_return', headers=headers, json=return_payload)
assert returned.status_code == 200, returned.text
after = client.get(f'/api/products/{product_id}', headers=headers).json()['product']['stock']
assert after == before + 1, (before, after)

direct_order = client.post('/api/workflow/sales_order', headers=headers, json=payload)
assert direct_order.status_code == 200, direct_order.text
deleted = client.delete(
    f"/api/workflow/sales_order/{direct_order.json()['id']}", headers=headers
)
assert deleted.status_code == 200 and deleted.json()['deleted'] is True, deleted.text

# Header discount and tied one-cent allocation must survive order -> delivery
# at the same product rows. 6.6667 also proves calculation uses the persisted
# NUMERIC(18,2) percentage contract (6.67), not a transient four-decimal value.
delivery_payload = {
    'entity_id':customer_id,'document_date':'2026-07-13',
    'warehouse_id':warehouse_id,'status':'approved','discount_percent':'6.6667',
    'items':[
        {'product_id':product_id,'quantity':1,'unit_price':'0.10',
         'vat_rate':20,'discount_percent':0},
        {'product_id':product_2_id,'quantity':1,'unit_price':'0.10',
         'vat_rate':20,'discount_percent':0},
    ],
}
delivery_order = client.post('/api/workflow/sales_order', headers=headers, json=delivery_payload)
assert delivery_order.status_code == 200, delivery_order.text
delivery = client.post(
    f"/api/workflow/sales_order/{delivery_order.json()['id']}/to-delivery",
    headers=headers,
)
assert delivery.status_code == 200, delivery.text
with engine.connect() as conn:
    order_header = conn.execute(text(
        'SELECT discount_percent,grand_total FROM sales_orders WHERE id=:id'
    ), {'id':delivery_order.json()['id']}).mappings().one()
    delivery_header = conn.execute(text(
        'SELECT total FROM delivery_notes WHERE id=:id'
    ), {'id':delivery.json()['id']}).mappings().one()
    order_lines = conn.execute(text(
        'SELECT product_id,line_total FROM sales_order_items '
        'WHERE sales_order_id=:id ORDER BY id'
    ), {'id':delivery_order.json()['id']}).mappings().all()
    delivery_lines = conn.execute(text(
        'SELECT product_id,line_total FROM delivery_note_items '
        'WHERE delivery_note_id=:id ORDER BY id'
    ), {'id':delivery.json()['id']}).mappings().all()
assert str(order_header['discount_percent']) in {'6.67', '6.6700'}
assert order_header['grand_total'] == delivery_header['total']
assert order_lines == delivery_lines
assert sum(row['line_total'] for row in delivery_lines) == delivery_header['total']

company_b = client.post('/api/companies', headers=headers, json={'name':'Workflow Tenant B'})
assert company_b.status_code == 201, company_b.text
headers_b = {**headers, 'X-Company-ID':str(company_b.json()['id'])}
assert client.get(f'/api/workflow/quote/{quote_id}', headers=headers_b).status_code == 404
assert client.put(f'/api/workflow/quote/{quote_id}', headers=headers_b, json=payload).status_code == 404
assert client.delete(f'/api/workflow/quote/{quote_id}', headers=headers_b).status_code == 404
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
