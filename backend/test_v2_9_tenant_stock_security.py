from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent

def test_stock_endpoints_reject_cross_tenant_warehouse_and_product_ids(tmp_path: Path) -> None:
    database = tmp_path / "stock-tenant.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    script = r'''
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
company_a = body['companies'][0]['id']
headers_a = {
    'Authorization':'Bearer ' + body['access_token'],
    'X-Company-ID':str(company_a),
}
password_change = client.post('/api/auth/change-password', headers=headers_a, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert password_change.status_code == 200, password_change.text
headers_a['Authorization'] = 'Bearer ' + password_change.json()['access_token']
product = client.post('/api/products', headers=headers_a, json={
    'name':'Tenant A Ürünü','purchase_price':10,'sale_price':20,
    'vat_rate':20,'stock':5,'unit':'Adet','category':'Security',
})
assert product.status_code == 201, product.text
product_a = product.json()['id']
company_b_response = client.post('/api/companies', headers=headers_a, json={'name':'Tenant B'})
assert company_b_response.status_code == 201, company_b_response.text
company_b = company_b_response.json()['id']
headers_b = {**headers_a, 'X-Company-ID':str(company_b)}
warehouses_b = client.get('/api/warehouses', headers=headers_b)
assert warehouses_b.status_code == 200 and warehouses_b.json(), warehouses_b.text
warehouse_b = warehouses_b.json()[0]['id']

adjust = client.post(f'/api/products/{product_a}/stock', headers=headers_a, json={
    'mode':'add','warehouse_id':warehouse_b,'quantity':1,
    'movement_date':'2026-07-13','note':'cross tenant attempt',
})
assert adjust.status_code == 404, adjust.text
critical = client.post('/api/warehouses/critical-stock', headers=headers_a, json={
    'product_id':product_a,'warehouse_id':warehouse_b,'critical_stock':3,
})
assert critical.status_code == 404, critical.text
bulk = client.post('/api/products/bulk-stock', headers=headers_a, json={
    'method':'add','warehouse_id':warehouse_b,'value':1,
    'movement_date':'2026-07-13','product_ids':[product_a],
})
assert bulk.status_code == 404, bulk.text
foreign_product = client.post(f'/api/products/{product_a}/stock', headers=headers_b, json={
    'mode':'add','warehouse_id':warehouse_b,'quantity':1,
    'movement_date':'2026-07-13','note':'foreign product attempt',
})
assert foreign_product.status_code == 404, foreign_product.text
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
