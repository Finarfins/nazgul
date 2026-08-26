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


def test_physical_count_is_atomic_grouped_and_traceable(tmp_path: Path) -> None:
    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'})
    assert login.status_code==200,login.text
    csrf=client.cookies.get('yhp_csrf_token')
    changed=client.post('/api/auth/change-password',json={
        'current_password':'admin123','new_password':'PhysicalCount!2026x'
    },headers={'X-CSRF-Token':csrf})
    assert changed.status_code==200,changed.text
    csrf=client.cookies.get('yhp_csrf_token')

    warehouse_id=client.get('/api/warehouses').json()[0]['id']
    def create_product(name,stock):
        response=client.post('/api/products',json={
            'name':name,'purchase_price':10,'sale_price':20,'vat_rate':20,
            'stock':stock,'unit':'Adet','category':'Sayım Testi'
        },headers={'X-CSRF-Token':csrf})
        assert response.status_code==201,response.text
        return response.json()['id']

    p1=create_product('Sayım Ürünü A',10)
    p2=create_product('Sayım Ürünü B',5)

    response=client.post('/api/warehouses/counts',json={
        'warehouse_id':warehouse_id,'count_date':'20.07.2026','note':'Raf sayımı',
        'items':[
            {'product_id':p1,'counted_quantity':8,'critical_stock':2},
            {'product_id':p2,'counted_quantity':7,'critical_stock':3},
        ],
    },headers={'X-CSRF-Token':csrf})
    assert response.status_code==201,response.text
    data=response.json()
    count_id=data['id']
    assert isinstance(count_id,int) and count_id>0
    assert data['count_date']=='2026-07-20'
    assert data['item_count']==2
    assert data['changed_count']==2
    assert data['increase_count']==1
    assert data['decrease_count']==1
    assert float(data['total_absolute_variance'])==4.0

    stocks={row['product_id']:row for row in client.get('/api/warehouses/stock',params={'warehouse_id':warehouse_id}).json()}
    assert float(stocks[p1]['quantity'])==8.0
    assert float(stocks[p2]['quantity'])==7.0
    assert float(stocks[p1]['critical_stock'])==2.0
    assert float(stocks[p2]['critical_stock'])==3.0

    listing=client.get('/api/warehouses/counts')
    assert listing.status_code==200,listing.text
    listed=next(row for row in listing.json() if row['id']==count_id)
    assert listed['warehouse_id']==warehouse_id
    assert listed['item_count']==2
    assert listed['changed_count']==2
    assert listed['unchanged_count']==0
    assert listed['increase_count']==1
    assert listed['decrease_count']==1
    assert float(listed['total_absolute_variance'])==4.0

    filtered=client.get('/api/warehouses/counts',params={
        'warehouse_id':warehouse_id,'date_from':'20.07.2026','date_to':'2026-07-20',
        'changed_only':'true','limit':10,
    })
    assert filtered.status_code==200,filtered.text
    assert [row['id'] for row in filtered.json()]==[count_id]
    assert client.get('/api/warehouses/counts',params={'date_from':'2026-07-21'}).json()==[]
    invalid_range=client.get('/api/warehouses/counts',params={'date_from':'2026-07-21','date_to':'2026-07-20'})
    assert invalid_range.status_code==422,invalid_range.text
    invalid_date=client.get('/api/warehouses/counts',params={'date_from':'20/07/2026'})
    assert invalid_date.status_code==422,invalid_date.text

    detail=client.get(f'/api/warehouses/counts/{count_id}')
    assert detail.status_code==200,detail.text
    detail_data=detail.json()
    assert detail_data['count']['note']=='Raf sayımı'
    rows={row['product_id']:row for row in detail_data['items']}
    assert float(rows[p1]['system_quantity'])==10.0
    assert float(rows[p1]['counted_quantity'])==8.0
    assert float(rows[p1]['variance'])==-2.0
    assert float(rows[p2]['system_quantity'])==5.0
    assert float(rows[p2]['counted_quantity'])==7.0
    assert float(rows[p2]['variance'])==2.0

    movements=client.get('/api/products/stock/movements/all',params={'warehouse_id':warehouse_id}).json()
    grouped=[row for row in movements if row['reference_type']=='inventory_count' and row['reference_id']==count_id]
    assert len(grouped)==2,grouped
    assert {float(row['quantity']) for row in grouped}=={-2.0,2.0}
    assert all('Sistem:' in row['note'] and 'Sayılan:' in row['note'] for row in grouped)

    # The endpoint validates every product before applying any line. An invalid
    # second row must leave the valid first product unchanged.
    before=next(row for row in client.get('/api/warehouses/stock',params={'warehouse_id':warehouse_id}).json() if row['product_id']==p1)['quantity']
    bad=client.post('/api/warehouses/counts',json={
        'warehouse_id':warehouse_id,'count_date':'2026-07-20',
        'items':[{'product_id':p1,'counted_quantity':1},{'product_id':999999,'counted_quantity':1}],
    },headers={'X-CSRF-Token':csrf})
    assert bad.status_code==400,bad.text
    after=next(row for row in client.get('/api/warehouses/stock',params={'warehouse_id':warehouse_id}).json() if row['product_id']==p1)['quantity']
    assert float(before)==float(after)==8.0

    duplicate=client.post('/api/warehouses/counts',json={
        'warehouse_id':warehouse_id,'count_date':'2026-07-20',
        'items':[{'product_id':p1,'counted_quantity':8},{'product_id':p1,'counted_quantity':8}],
    },headers={'X-CSRF-Token':csrf})
    assert duplicate.status_code==422,duplicate.text
    assert client.get('/api/warehouses/counts/999999').status_code==404
    print('PHYSICAL COUNT OK')
'''
    result = _run(smoke, tmp_path / "physical-count.db")
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_physical_count_rejects_foreign_warehouse(tmp_path: Path) -> None:
    smoke = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.auth import utcnow
from app.db import engine

with TestClient(app) as client:
    login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'})
    assert login.status_code==200,login.text
    csrf=client.cookies.get('yhp_csrf_token')
    changed=client.post('/api/auth/change-password',json={
        'current_password':'admin123','new_password':'CountTenant!2026x'
    },headers={'X-CSRF-Token':csrf})
    assert changed.status_code==200,changed.text
    csrf=client.cookies.get('yhp_csrf_token')

    product=client.post('/api/products',json={
        'name':'Tenant Sayım Ürünü','purchase_price':1,'sale_price':2,
        'vat_rate':20,'stock':4,'unit':'Adet','category':'Test'
    },headers={'X-CSRF-Token':csrf})
    assert product.status_code==201,product.text
    product_id=product.json()['id']

    with engine.begin() as c:
        other=c.execute(text("INSERT INTO companies (name,is_active,created_at) VALUES ('Diger',1,:now) RETURNING id"),{'now':utcnow()}).scalar()
        foreign_warehouse=c.execute(text(
            "INSERT INTO warehouses (company_id,name,is_default,is_active,created_at) "
            "VALUES (:cid,'Yabancı Sayım Deposu',1,1,:now) RETURNING id"
        ),{'cid':other,'now':utcnow()}).scalar()

    response=client.post('/api/warehouses/counts',json={
        'warehouse_id':foreign_warehouse,'count_date':'2026-07-20',
        'items':[{'product_id':product_id,'counted_quantity':3}],
    },headers={'X-CSRF-Token':csrf})
    assert response.status_code==404,response.text
    assert client.get('/api/warehouses/counts',params={'warehouse_id':foreign_warehouse}).status_code==404
    print('PHYSICAL COUNT TENANT OK')
'''
    result = _run(smoke, tmp_path / "physical-count-tenant.db")
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
