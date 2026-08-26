from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_product_detail_exposes_resolvable_drilldown_ids(tmp_path: Path) -> None:
    """Product detail history rows must carry ids the UI can navigate to."""
    database = tmp_path / "product-detail-drilldown.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["DEMO_MODE"] = "true"
    smoke = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.db import engine
from seed_demo_data import build_demo
import os

build_demo(os.environ['DATABASE_URL'])
with TestClient(app) as client:
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get('yhp_csrf_token')
    changed = client.post(
        '/api/auth/change-password',
        json={'current_password':'admin123','new_password':'ProdRot!2026x'},
        headers={'X-CSRF-Token':csrf},
    )
    assert changed.status_code == 200, changed.text

    with engine.begin() as connection:
        product_id = connection.execute(text(
            "SELECT oi.product_id FROM order_items oi WHERE oi.product_id IS NOT NULL "
            "GROUP BY oi.product_id ORDER BY COUNT(*) DESC LIMIT 1"
        )).scalar()
    assert product_id, 'demo dataset should contain a sold product'

    response = client.get(f'/api/products/{product_id}')
    assert response.status_code == 200, response.text
    data = response.json()

    assert set(data) >= {
        'product','movements','warehouse_stocks','sales_history','purchase_history','commercial_summary'
    }, set(data)

    sales = data['sales_history']
    assert sales, 'demo product should have sales history'
    for row in sales:
        assert isinstance(row['customer_id'], int) and row['customer_id'] > 0, row
        assert isinstance(row['id'], int) and row['id'] > 0, row
        assert 'customer_name' in row and 'document_no' in row, row
    # The advertised customer drill-down target must really exist.
    assert client.get(f"/api/customers/{sales[0]['customer_id']}").status_code == 200

    purchases = data['purchase_history']
    if purchases:
        for row in purchases:
            assert isinstance(row['supplier_id'], int) and row['supplier_id'] > 0, row
            assert isinstance(row['id'], int) and row['id'] > 0, row
        assert client.get(f"/api/suppliers/{purchases[0]['supplier_id']}").status_code == 200

    # Movements must keep the reference pair the UI resolves documents from.
    for row in data['movements']:
        assert 'reference_type' in row and 'reference_id' in row, row
        assert 'movement_type' in row and 'quantity' in row, row

    for row in data['warehouse_stocks']:
        assert isinstance(row['warehouse_id'], int), row
        assert 'name' in row and 'quantity' in row, row
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_product_detail_is_tenant_isolated_and_handles_edge_data(tmp_path: Path) -> None:
    """Another company's product must 404, and odd stock/name data must not break the payload."""
    database = tmp_path / "product-detail-tenant.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.db import engine
from app.auth import utcnow

with TestClient(app) as client:
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get('yhp_csrf_token')
    changed = client.post(
        '/api/auth/change-password',
        json={'current_password':'admin123','new_password':'ProdRot!2026x'},
        headers={'X-CSRF-Token':csrf},
    )
    assert changed.status_code == 200, changed.text

    with engine.begin() as connection:
        cid = connection.execute(text('SELECT id FROM companies LIMIT 1')).scalar()
        other = connection.execute(text(
            "INSERT INTO companies (name,is_active,created_at) "
            "VALUES ('Diger Firma',1,:now) RETURNING id"
        ), {'now': utcnow()}).scalar()
        # Negative stock plus a duplicate product name in the same tenant.
        negative_id = connection.execute(text(
            "INSERT INTO products (company_id,name,unit,stock,critical_stock,minimum_stock,"
            "purchase_price,sale_price,vat_rate,active) "
            "VALUES (:cid,'Cakisan Parca','adet',-4,10,0,100,150,20,1) RETURNING id"
        ), {'cid': cid}).scalar()
        connection.execute(text(
            "INSERT INTO products (company_id,name,unit,stock,critical_stock,minimum_stock,"
            "purchase_price,sale_price,vat_rate,active) "
            "VALUES (:cid,'Cakisan Parca','adet',7,0,0,100,150,20,1)"
        ), {'cid': cid})
        foreign_id = connection.execute(text(
            "INSERT INTO products (company_id,name,unit,stock,critical_stock,minimum_stock,"
            "purchase_price,sale_price,vat_rate,active) "
            "VALUES (:other,'Baska Firma Urunu','adet',5,0,0,10,20,20,1) RETURNING id"
        ), {'other': other}).scalar()

    # Negative stock is reported as-is, never clamped to zero.
    detail = client.get(f'/api/products/{negative_id}')
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert float(body['product']['stock']) == -4.0, body['product']['stock']
    assert body['sales_history'] == [] and body['purchase_history'] == []
    assert body['movements'] == [] and body['warehouse_stocks'] == []

    # Tenant isolation: a product owned by another company must not be readable.
    assert client.get(f'/api/products/{foreign_id}').status_code == 404
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
