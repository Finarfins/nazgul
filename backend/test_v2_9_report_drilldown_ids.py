from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_report_and_dashboard_expose_resolvable_drilldown_ids(tmp_path: Path) -> None:
    """Dashboard/report top lists must carry IDs the UI can navigate to."""
    database = tmp_path / "report-drilldown-ids.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["DEMO_MODE"] = "true"
    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app
from seed_demo_data import build_demo
import os

build_demo(os.environ['DATABASE_URL'])
with TestClient(app) as client:
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get('yhp_csrf_token')
    changed = client.post(
        '/api/auth/change-password',
        json={'current_password':'admin123','new_password':'DrillRot!2026x'},
        headers={'X-CSRF-Token':csrf},
    )
    assert changed.status_code == 200, changed.text

    report = client.get('/api/reports/summary')
    assert report.status_code == 200, report.text
    summary = report.json()

    top_customers = summary['top_customers']
    assert top_customers, 'demo dataset should produce top customers'
    for row in top_customers:
        assert 'customer_id' in row, row
        assert isinstance(row['customer_id'], int) and row['customer_id'] > 0, row
        assert 'name' in row and 'total' in row and 'count' in row, row
    # The advertised drill-down target must really exist.
    detail = client.get(f"/api/customers/{top_customers[0]['customer_id']}")
    assert detail.status_code == 200, detail.text

    report_products = summary['top_products']
    assert report_products, 'demo dataset should produce top products'
    assert len(report_products) <= 10
    for row in report_products:
        assert 'product_id' in row, row
        assert 'product_name' in row and 'quantity' in row and 'total' in row, row
        assert row['product_id'] is None or isinstance(row['product_id'], int), row
    resolvable = [row for row in report_products if row['product_id']]
    assert resolvable, 'demo dataset should resolve at least one product id'
    product_detail = client.get(f"/api/products/{resolvable[0]['product_id']}")
    assert product_detail.status_code == 200, product_detail.text

    dashboard = client.get('/api/dashboard')
    assert dashboard.status_code == 200, dashboard.text
    dashboard_products = dashboard.json()['top_products']
    assert dashboard_products, 'demo dataset should produce dashboard top products'
    assert len(dashboard_products) <= 5
    for row in dashboard_products:
        assert 'product_id' in row, row
        assert 'name' in row and 'quantity' in row and 'total' in row, row
        assert row['product_id'] is None or isinstance(row['product_id'], int), row
    dashboard_resolvable = [row for row in dashboard_products if row['product_id']]
    assert dashboard_resolvable, 'dashboard should resolve at least one product id'
    dashboard_detail = client.get(f"/api/products/{dashboard_resolvable[0]['product_id']}")
    assert dashboard_detail.status_code == 200, dashboard_detail.text
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


def test_ambiguous_product_names_do_not_expose_an_id(tmp_path: Path) -> None:
    """Two catalogue products sharing a name must stay unlinked, not guess an ID."""
    database = tmp_path / "report-drilldown-ambiguous.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.db import engine

with TestClient(app) as client:
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get('yhp_csrf_token')
    changed = client.post(
        '/api/auth/change-password',
        json={'current_password':'admin123','new_password':'DrillRot!2026x'},
        headers={'X-CSRF-Token':csrf},
    )
    assert changed.status_code == 200, changed.text

    with engine.begin() as connection:
        cid = connection.execute(text('SELECT id FROM companies LIMIT 1')).scalar()
        customer_id = connection.execute(
            text("INSERT INTO customers (company_id,name,opening_balance) "
                 "VALUES (:cid,'Drilldown Musteri',0) RETURNING id"),
            {'cid': cid},
        ).scalar()
        order_id = connection.execute(
            text("INSERT INTO orders (company_id,customer_id,order_date,subtotal,vat_total,"
                 "final_total,paid_amount,status) "
                 "VALUES (:cid,:customer,'2026-05-05',200,0,200,0,'completed') RETURNING id"),
            {'cid': cid, 'customer': customer_id},
        ).scalar()
        # Same product_name, two different catalogue rows -> ambiguous.
        for product_name, product_id in (('Cakisan Urun', 9001), ('Cakisan Urun', 9002)):
            connection.execute(
                text("INSERT INTO order_items (company_id,order_id,product_id,product_name,"
                     "quantity,unit_price,vat_rate,line_subtotal,line_vat,line_total) "
                     "VALUES (:cid,:order,:pid,:name,1,100,0,100,0,100)"),
                {'cid': cid, 'order': order_id, 'pid': product_id, 'name': product_name},
            )
        # A free-text line with no catalogue product at all.
        connection.execute(
            text("INSERT INTO order_items (company_id,order_id,product_id,product_name,"
                 "quantity,unit_price,vat_rate,line_subtotal,line_vat,line_total) "
                 "VALUES (:cid,:order,NULL,'Serbest Kalem',1,50,0,50,0,50)"),
            {'cid': cid, 'order': order_id},
        )

    rows = {row['product_name']: row for row in client.get('/api/reports/summary').json()['top_products']}
    assert rows['Cakisan Urun']['product_id'] is None, rows['Cakisan Urun']
    # Grouping and totals stay exactly as before: both lines still aggregate by name.
    assert float(rows['Cakisan Urun']['total']) == 200.0, rows['Cakisan Urun']
    assert float(rows['Cakisan Urun']['quantity']) == 2.0, rows['Cakisan Urun']
    assert rows['Serbest Kalem']['product_id'] is None, rows['Serbest Kalem']
    assert float(rows['Serbest Kalem']['total']) == 50.0, rows['Serbest Kalem']

    dashboard_rows = {row['name']: row for row in client.get('/api/dashboard').json()['top_products']}
    assert dashboard_rows['Cakisan Urun']['product_id'] is None, dashboard_rows['Cakisan Urun']
    assert float(dashboard_rows['Cakisan Urun']['total']) == 200.0, dashboard_rows['Cakisan Urun']
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
