from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parent


def test_clean_postgresql_application_smoke() -> None:
    base_url = os.getenv("APP_TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("APP_TEST_DATABASE_URL is required for PostgreSQL application smoke test")
    if not base_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")

    admin_engine = create_engine(base_url, pool_pre_ping=True)
    schema = f"app_smoke_{uuid4().hex}"
    quoted_schema = admin_engine.dialect.identifier_preparer.quote(schema)
    with admin_engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))

    test_url = make_url(base_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)
    env = os.environ.copy()
    env["DATABASE_URL"] = test_url
    env["PYTHONPATH"] = str(BACKEND)

    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
assert client.get('/api/live').status_code == 200
assert client.get('/api/ready').status_code == 200
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
headers = {
    'Authorization': 'Bearer ' + body['access_token'],
    'X-Company-ID': str(body['companies'][0]['id']),
}
# The clean-install bootstrap admin must rotate its initial password before it
# can reach any protected endpoint.
password_change = client.post('/api/auth/change-password', headers=headers, json={
    'current_password':'admin123','new_password':'AdminRot!2026x'
})
assert password_change.status_code == 200, password_change.text
headers['Authorization'] = 'Bearer ' + password_change.json()['access_token']
customer = client.post('/api/customers', headers=headers, json={
    'name':'PostgreSQL Smoke Müşterisi','opening_balance':0,
    'risk_limit':0,'payment_term_days':0,'is_active':True,
})
assert customer.status_code == 201, customer.text
product = client.post('/api/products', headers=headers, json={
    'name':'PostgreSQL Smoke Ürünü','purchase_price':10,'sale_price':20,
    'vat_rate':20,'stock':5,'unit':'Adet','category':'Smoke',
})
assert product.status_code == 201, product.text
warehouse = client.get('/api/warehouses', headers=headers)
assert warehouse.status_code == 200 and warehouse.json(), warehouse.text
order = client.post('/api/orders', headers=headers, json={
    'entity_id':customer.json()['id'],'transaction_date':'2026-07-13',
    'warehouse_id':warehouse.json()[0]['id'],'status':'completed',
    'payment_method':'credit','paid_amount':0,'discount_percent':0,
    'items':[{'product_id':product.json()['id'],'quantity':1,
              'unit_price':20,'vat_rate':20,'discount_percent':0}],
})
assert order.status_code == 201, order.text
dashboard = client.get('/api/dashboard', headers=headers)
assert dashboard.status_code == 200
# Drill-down ids must survive the PostgreSQL dialect, not just SQLite.
dashboard_products = dashboard.json()['top_products']
assert dashboard_products, dashboard.text
assert dashboard_products[0]['product_id'] == product.json()['id'], dashboard_products[0]

# /api/reports/summary is PostgreSQL-sensitive: "month" is a reserved column label
# and the top lists must expose ids the UI can navigate to.
report = client.get('/api/reports/summary', headers=headers)
assert report.status_code == 200, report.text
summary = report.json()
assert summary['top_customers'][0]['customer_id'] == customer.json()['id'], summary['top_customers'][0]
assert summary['top_products'][0]['product_id'] == product.json()['id'], summary['top_products'][0]
assert summary['monthly_sales'], summary
client.close()
'''
    try:
        first = subprocess.run(
            [sys.executable, "-c", smoke],
            cwd=BACKEND,
            env=env,
            text=True,
            capture_output=True,
            timeout=180,
        )
        assert first.returncode == 0, first.stdout + "\n" + first.stderr

        restart = subprocess.run(
            [sys.executable, "-c", "from app.main import app; print('postgres-restart-ok')"],
            cwd=BACKEND,
            env=env,
            text=True,
            capture_output=True,
            timeout=180,
        )
        assert restart.returncode == 0, restart.stdout + "\n" + restart.stderr
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {quoted_schema} CASCADE"))
        admin_engine.dispose()
