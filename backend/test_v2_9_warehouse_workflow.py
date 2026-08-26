from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def _run(smoke: str, database: Path, *, demo: bool = False) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    if demo:
        env["DEMO_MODE"] = "true"
    return subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )


def test_warehouse_detail_and_replenishment_shapes(tmp_path: Path) -> None:
    """Warehouse detail exposes header/stats/products; replenishment classifies rows."""
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
    ch = client.post('/api/auth/change-password',
        json={'current_password':'admin123','new_password':'WhWorkflow!2026x'},
        headers={'X-CSRF-Token':csrf})
    assert ch.status_code == 200, ch.text

    wid = client.get('/api/warehouses').json()[0]['id']

    detail = client.get(f'/api/warehouses/{wid}')
    assert detail.status_code == 200, detail.text
    data = detail.json()
    assert set(data) == {'warehouse','stats','products'}, set(data)
    assert data['warehouse']['id'] == wid
    stats = data['stats']
    for key in ('product_count','negative_count','zero_count','critical_count','total_quantity'):
        assert key in stats, key
    assert data['products'], 'demo warehouse should hold products'
    for row in data['products']:
        assert isinstance(row['product_id'], int) and row['product_id'] > 0, row
        assert 'quantity' in row and 'critical_stock' in row, row
        assert 'last_movement_date' in row, row
    # stats.critical_count matches a manual classification of the product rows
    manual_crit = sum(1 for r in data['products']
                      if float(r['quantity'])>0 and float(r['critical_stock'])>0
                      and float(r['quantity'])<=float(r['critical_stock']))
    assert int(stats['critical_count']) == manual_crit, (stats['critical_count'], manual_crit)

    # literal routes are not shadowed by /{warehouse_id}
    assert client.get('/api/warehouses/stock', params={'warehouse_id':wid}).status_code == 200
    assert client.get('/api/warehouses/transfers').status_code == 200
    assert client.get('/api/warehouses/999999').status_code == 404

    repl = client.get('/api/warehouses/replenishment')
    assert repl.status_code == 200, repl.text
    rows = repl.json()
    for row in rows:
        assert isinstance(row['product_id'], int) and row['product_id'] > 0, row
        assert isinstance(row['warehouse_id'], int), row
        q, c = float(row['quantity']), float(row['critical_stock'])
        # every worklist row is genuinely at/below critical, zero, or negative
        assert q <= 0 or (c > 0 and q <= c), row
        # supplier id, when present, is a stable int (never guessed from a name)
        assert row['last_supplier_id'] is None or isinstance(row['last_supplier_id'], int), row
    print('WAREHOUSE WORKFLOW SHAPES OK')
'''
    result = _run(smoke, tmp_path / "wh-workflow.db", demo=True)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_warehouse_detail_tenant_isolation_and_transfer_rules(tmp_path: Path) -> None:
    """Another company's warehouse 404s; transfer validation is enforced by the backend."""
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
    ch = client.post('/api/auth/change-password',
        json={'current_password':'admin123','new_password':'WhWorkflow!2026x'},
        headers={'X-CSRF-Token':csrf})
    assert ch.status_code == 200, ch.text
    csrf = client.cookies.get('yhp_csrf_token')

    # A warehouse owned by another company must not be readable.
    with engine.begin() as c:
        other = c.execute(text(
            "INSERT INTO companies (name,is_active,created_at) VALUES ('Diger',1,:now) RETURNING id"
        ), {'now': utcnow()}).scalar()
        foreign_wid = c.execute(text(
            "INSERT INTO warehouses (company_id,name,is_default,is_active,created_at) "
            "VALUES (:cid,'Yabanci Depo',0,1,:now) RETURNING id"
        ), {'cid': other, 'now': utcnow()}).scalar()
    assert client.get(f'/api/warehouses/{foreign_wid}').status_code == 404

    # Need two warehouses and a product in our own company.
    whs = client.get('/api/warehouses').json()
    assert whs, 'expected at least one warehouse'
    src = whs[0]['id']
    # create a second warehouse
    created = client.post('/api/warehouses', json={'name':'İkinci Depo'}, headers={'X-CSRF-Token':csrf})
    assert created.status_code == 201, created.text
    dst = created.json()['id']

    product = client.post('/api/products', json={
        'name':'Transfer Test Ürünü','purchase_price':10,'sale_price':20,
        'vat_rate':20,'stock':50,'unit':'Adet','category':'Test',
    }, headers={'X-CSRF-Token':csrf})
    assert product.status_code == 201, product.text
    pid = product.json()['id']

    # Same source and destination must be rejected by the backend.
    same = client.post('/api/warehouses/transfers', json={
        'source_warehouse_id':src,'target_warehouse_id':src,'transfer_date':'2026-07-20',
        'items':[{'product_id':pid,'quantity':1}],
    }, headers={'X-CSRF-Token':csrf})
    assert same.status_code == 400, same.text

    # Invalid product rejected.
    badp = client.post('/api/warehouses/transfers', json={
        'source_warehouse_id':src,'target_warehouse_id':dst,'transfer_date':'2026-07-20',
        'items':[{'product_id':999999,'quantity':1}],
    }, headers={'X-CSRF-Token':csrf})
    assert badp.status_code == 400, badp.text

    # Baseline source stock, then a successful transfer moves quantity src->dst.
    src_before = next(r for r in client.get('/api/warehouses/stock', params={'warehouse_id':src}).json() if r['product_id']==pid)['quantity']
    ok = client.post('/api/warehouses/transfers', json={
        'source_warehouse_id':src,'target_warehouse_id':dst,'transfer_date':'2026-07-20',
        'note':'Test transferi','items':[{'product_id':pid,'quantity':10}],
    }, headers={'X-CSRF-Token':csrf})
    assert ok.status_code == 201, ok.text

    src_after = next(r for r in client.get('/api/warehouses/stock', params={'warehouse_id':src}).json() if r['product_id']==pid)['quantity']
    dst_after = next(r for r in client.get('/api/warehouses/stock', params={'warehouse_id':dst}).json() if r['product_id']==pid)['quantity']
    assert float(src_before) - float(src_after) == 10.0, (src_before, src_after)
    assert float(dst_after) == 10.0, dst_after

    # Transfer produced auditable out/in movements on the detail endpoint.
    moves = client.get(f'/api/products/{pid}').json()['movements']
    types = [m['movement_type'] for m in moves if m['reference_type']=='transfer']
    assert 'transfer_out' in types and 'transfer_in' in types, types
    print('WAREHOUSE TRANSFER RULES OK')
'''
    result = _run(smoke, tmp_path / "wh-transfer.db")
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
