from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def _run(smoke: str, database: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    return subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )


def test_transfer_detail_shape_and_audit_rows(tmp_path: Path) -> None:
    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'})
    assert login.status_code==200,login.text
    csrf=client.cookies.get('yhp_csrf_token')
    changed=client.post('/api/auth/change-password',json={
        'current_password':'admin123','new_password':'TransferDetail!2026x'
    },headers={'X-CSRF-Token':csrf})
    assert changed.status_code==200,changed.text
    csrf=client.cookies.get('yhp_csrf_token')

    warehouses=client.get('/api/warehouses').json()
    src=warehouses[0]['id']
    created=client.post('/api/warehouses',json={'name':'Transfer Detay Hedef'},headers={'X-CSRF-Token':csrf})
    assert created.status_code==201,created.text
    dst=created.json()['id']

    product=client.post('/api/products',json={
        'name':'Transfer Detay Ürünü','purchase_price':10,'sale_price':20,
        'vat_rate':20,'stock':20,'unit':'Adet','category':'Test'
    },headers={'X-CSRF-Token':csrf})
    assert product.status_code==201,product.text
    pid=product.json()['id']

    transferred=client.post('/api/warehouses/transfers',json={
        'source_warehouse_id':src,'target_warehouse_id':dst,
        'transfer_date':'2026-07-20','note':'Detay ekranı testi',
        'items':[{'product_id':pid,'quantity':3}]
    },headers={'X-CSRF-Token':csrf})
    assert transferred.status_code==201,transferred.text
    tid=transferred.json()['id']

    response=client.get(f'/api/warehouse-transfers/{tid}')
    assert response.status_code==200,response.text
    data=response.json()
    assert set(data)=={'transfer','items','movements'},data.keys()
    header=data['transfer']
    assert header['id']==tid
    assert header['source_warehouse_id']==src
    assert header['target_warehouse_id']==dst
    assert header['source_name'] and header['target_name']
    assert int(header['item_count'])==1
    assert float(header['total_quantity'])==3.0
    assert data['items']==[data['items'][0]]
    assert data['items'][0]['product_id']==pid
    assert data['items'][0]['product_name']=='Transfer Detay Ürünü'
    assert float(data['items'][0]['quantity'])==3.0
    movement_types={row['movement_type'] for row in data['movements']}
    assert movement_types=={'transfer_out','transfer_in'},movement_types
    assert {row['warehouse_id'] for row in data['movements']}=={src,dst}
    assert sum(float(row['quantity']) for row in data['movements'])==0.0
    assert client.get('/api/warehouse-transfers/999999').status_code==404
    print('TRANSFER DETAIL OK')
'''
    result = _run(smoke, tmp_path / "transfer-detail.db")
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_transfer_detail_is_tenant_scoped(tmp_path: Path) -> None:
    smoke = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.db import engine
from app.auth import utcnow

with TestClient(app) as client:
    login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'})
    assert login.status_code==200,login.text
    csrf=client.cookies.get('yhp_csrf_token')
    changed=client.post('/api/auth/change-password',json={
        'current_password':'admin123','new_password':'TransferTenant!2026x'
    },headers={'X-CSRF-Token':csrf})
    assert changed.status_code==200,changed.text

    with engine.begin() as c:
        cid=c.execute(text("INSERT INTO companies (name,is_active,created_at) VALUES ('Diger',1,:now) RETURNING id"),{'now':utcnow()}).scalar()
        src=c.execute(text("INSERT INTO warehouses (company_id,name,is_default,is_active,created_at) VALUES (:cid,'Diger Kaynak',1,1,:now) RETURNING id"),{'cid':cid,'now':utcnow()}).scalar()
        dst=c.execute(text("INSERT INTO warehouses (company_id,name,is_default,is_active,created_at) VALUES (:cid,'Diger Hedef',0,1,:now) RETURNING id"),{'cid':cid,'now':utcnow()}).scalar()
        tid=c.execute(text("INSERT INTO stock_transfers (company_id,transfer_date,source_warehouse_id,target_warehouse_id,status,created_at) VALUES (:cid,'2026-07-20',:src,:dst,'completed',:now) RETURNING id"),{'cid':cid,'src':src,'dst':dst,'now':utcnow()}).scalar()
    assert client.get(f'/api/warehouse-transfers/{tid}').status_code==404
    print('TRANSFER DETAIL TENANT OK')
'''
    result = _run(smoke, tmp_path / "transfer-detail-tenant.db")
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
