from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import required_permission


BACKEND = Path(__file__).resolve().parent


def test_parts_use_work_order_permissions() -> None:
    assert required_permission("GET", "/api/work-orders/1/parts") == "read"
    assert required_permission("POST", "/api/work-orders/1/parts") == "sales"
    assert required_permission("DELETE", "/api/work-orders/1/parts/1") == "sales"


def test_parts_inventory_totals_audit_and_tenant_isolation(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'parts.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from app.db import engine
from app.main import app

with TestClient(app) as client:
    assert 'work_order_parts' in inspect(engine).get_table_names()
    login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json()
    cid=login['companies'][0]['id']; uid=login['user']['id']
    h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
    changed=client.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'WorkOrderParts123!'}).json()
    h['Authorization']='Bearer '+changed['access_token']
    customer=client.post('/api/customers',headers=h,json={'name':'Parça Müşterisi'}).json()
    machine=client.post('/api/machines',headers=h,json={'customer_id':customer['id'],'brand':'S','model':'P','serial_number':'PART-M'}).json()
    wo=client.post('/api/work-orders',headers=h,json={'machine_id':machine['id'],'customer_id':customer['id'],'technician_id':uid,'actual_hours':'2','labor_rate':'100'}).json()
    warehouse=client.get('/api/warehouses',headers=h).json()[0]
    product=client.post('/api/products',headers=h,json={'name':'Filtre','purchase_price':5,'sale_price':10,'vat_rate':20,'stock':5,'unit':'Adet'}).json()
    payload={'product_id':product['id'],'warehouse_id':warehouse['id'],'quantity':'2','unit_price':'10','discount':'10','tax_rate':'20','total_price':'999999'}
    created=client.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json=payload)
    assert created.status_code==201,created.text
    part=created.json(); assert part['total_price']==21.6
    listed=client.get(f"/api/work-orders/{wo['id']}/parts",headers=h).json()
    assert listed['parts_total']==21.6 and len(listed['items'])==1
    detail=client.get(f"/api/work-orders/{wo['id']}",headers=h).json()
    assert detail['labor_total']==200 and detail['parts_total']==21.6 and detail['grand_total']==221.6
    duplicate=client.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json=payload)
    assert duplicate.status_code==409,duplicate.text
    stock=client.get('/api/warehouses/stock',headers=h,params={'warehouse_id':warehouse['id']}).json()
    assert next(x for x in stock if x['product_id']==product['id'])['quantity']==3
    too_many=client.put(f"/api/work-orders/{wo['id']}/parts/{part['id']}",headers=h,json={**payload,'quantity':'10'})
    assert too_many.status_code==409,too_many.text
    stock=client.get('/api/warehouses/stock',headers=h,params={'warehouse_id':warehouse['id']}).json()
    assert next(x for x in stock if x['product_id']==product['id'])['quantity']==3
    updated=client.put(f"/api/work-orders/{wo['id']}/parts/{part['id']}",headers=h,json={**payload,'quantity':'4'})
    assert updated.status_code==200,updated.text
    assert updated.json()['total_price']==43.2
    product2=client.post('/api/products',headers=h,json={'name':'Kayış','purchase_price':3,'sale_price':6,'vat_rate':20,'stock':2,'unit':'Adet'}).json()
    second_payload={**payload,'product_id':product2['id'],'quantity':'1'}
    second=client.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json=second_payload)
    assert second.status_code==201,second.text
    duplicate_update=client.put(f"/api/work-orders/{wo['id']}/parts/{second.json()['id']}",headers=h,json={**payload,'quantity':'1'})
    assert duplicate_update.status_code==409,duplicate_update.text
    assert client.delete(f"/api/work-orders/{wo['id']}/parts/{second.json()['id']}",headers=h).status_code==204
    second_history=client.get(f"/api/history/work_order_part/{second.json()['id']}",headers=h).json()
    assert [x['action'] for x in second_history].count('delete')==1
    inactive=client.post('/api/products',headers=h,json={'name':'Pasif Parça','purchase_price':1,'sale_price':2,'vat_rate':20,'stock':2,'unit':'Adet'}).json()
    with engine.begin() as conn:
        conn.execute(text('UPDATE products SET active=FALSE WHERE id=:id AND company_id=:cid'),{'id':inactive['id'],'cid':cid})
    inactive_response=client.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json={**payload,'product_id':inactive['id']})
    assert inactive_response.status_code==400,inactive_response.text
    history=client.get(f"/api/history/work_order_part/{part['id']}",headers=h).json()
    assert [x['action'] for x in history].count('create')==1
    assert [x['action'] for x in history].count('update')==1
    other=client.post('/api/companies',headers=h,json={'name':'Başka Firma'}).json()
    hb={**h,'X-Company-ID':str(other['id'])}
    assert client.get(f"/api/work-orders/{wo['id']}/parts",headers=hb).status_code==404
    assert client.delete(f"/api/work-orders/{wo['id']}/parts/{part['id']}",headers=hb).status_code==404
    for status in ('IN_PROGRESS','COMPLETED','DELIVERED'):
        transitioned=client.patch(f"/api/work-orders/{wo['id']}/status",headers=h,json={'status':status})
        assert transitioned.status_code==200,transitioned.text
    assert client.get(f"/api/work-orders/{wo['id']}/parts",headers=h).status_code==200
    assert client.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json={**payload,'product_id':product2['id']}).status_code==409
    assert client.put(f"/api/work-orders/{wo['id']}/parts/{part['id']}",headers=h,json=payload).status_code==409
    assert client.delete(f"/api/work-orders/{wo['id']}/parts/{part['id']}",headers=h).status_code==409
    stock=client.get('/api/warehouses/stock',headers=h,params={'warehouse_id':warehouse['id']}).json()
    assert next(x for x in stock if x['product_id']==product['id'])['quantity']==1
    history=client.get(f"/api/history/work_order_part/{part['id']}",headers=h).json()
    assert [x['action'] for x in history].count('delete')==0
'''
    result = subprocess.run([sys.executable, "-c", smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


_SETUP = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.db import engine
from app.main import app
client = TestClient(app)
lg=client.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json()
cid=lg['companies'][0]['id']; uid=lg['user']['id']
h={'Authorization':'Bearer '+lg['access_token'],'X-Company-ID':str(cid)}
h['Authorization']='Bearer '+client.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'WorkOrderParts123!'}).json()['access_token']
cust=client.post('/api/customers',headers=h,json={'name':'M'}).json()
mach=client.post('/api/machines',headers=h,json={'customer_id':cust['id'],'brand':'S','model':'P','serial_number':'PM'}).json()
wh=client.get('/api/warehouses',headers=h).json()[0]
def new_wo():
    return client.post('/api/work-orders',headers=h,json={'machine_id':mach['id'],'customer_id':cust['id'],'technician_id':uid,'actual_hours':'1','labor_rate':'10'}).json()
def new_prod(stock):
    return client.post('/api/products',headers=h,json={'name':'Part','purchase_price':5,'sale_price':10,'vat_rate':20,'stock':stock,'unit':'Adet'}).json()
def add_part(wo_id,pid,q):
    return client.post(f"/api/work-orders/{wo_id}/parts",headers=h,json={'product_id':pid,'warehouse_id':wh['id'],'quantity':str(q),'unit_price':'10','tax_rate':'20'})
def stock_of(pid):
    s=client.get('/api/warehouses/stock',headers=h,params={'warehouse_id':wh['id']}).json()
    return next((float(x['quantity']) for x in s if x['product_id']==pid),0.0)
def status_change_count(wo_id):
    return [x['action'] for x in client.get(f"/api/history/work_order/{wo_id}",headers=h).json()].count('status_change')
'''


def _smoke(tmp_path: Path, scenario: str, name: str = "wop.db") -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / name).as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    result = subprocess.run([sys.executable, "-c", _SETUP + scenario], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_cancelled_work_order_restores_part_stock(tmp_path: Path) -> None:
    _smoke(tmp_path, r'''
wo=new_wo(); p=new_prod(10)
assert add_part(wo['id'],p['id'],4).status_code==201
assert stock_of(p['id'])==6.0                 # reserved
c1=client.patch(f"/api/work-orders/{wo['id']}/status",headers=h,json={'status':'CANCELLED'})
assert c1.status_code==200,c1.text
assert stock_of(p['id'])==10.0                # restored inside the cancellation
assert status_change_count(wo['id'])==1       # audit consistent
# repeated CANCEL is rejected and never restores stock twice
c2=client.patch(f"/api/work-orders/{wo['id']}/status",headers=h,json={'status':'CANCELLED'})
assert c2.status_code==409,c2.text
assert stock_of(p['id'])==10.0
assert status_change_count(wo['id'])==1
# multiple parts all restored
wo3=new_wo(); a=new_prod(10); b=new_prod(20)
add_part(wo3['id'],a['id'],3); add_part(wo3['id'],b['id'],5)
assert stock_of(a['id'])==7.0 and stock_of(b['id'])==15.0
assert client.patch(f"/api/work-orders/{wo3['id']}/status",headers=h,json={'status':'CANCELLED'}).status_code==200
assert stock_of(a['id'])==10.0 and stock_of(b['id'])==20.0
''')


def test_delivered_work_order_does_not_restore_stock(tmp_path: Path) -> None:
    _smoke(tmp_path, r'''
wo=new_wo(); p=new_prod(10)
assert add_part(wo['id'],p['id'],3).status_code==201
assert stock_of(p['id'])==7.0
for s in ('IN_PROGRESS','COMPLETED','DELIVERED'):
    assert client.patch(f"/api/work-orders/{wo['id']}/status",headers=h,json={'status':s}).status_code==200
assert stock_of(p['id'])==7.0                 # DELIVERED consumes stock: unchanged
''')


def test_cancel_rolls_back_when_stock_restore_fails(tmp_path: Path) -> None:
    _smoke(tmp_path, r'''
# FAZ-3: the cancellation restores stock through work_order_stock.return_part,
# so the failure is injected at the stock call that path actually makes. (It
# used to be patched on app.routers.work_orders, which no longer touches stock.)
import app.work_order_stock as wo_stock
wo=new_wo(); p=new_prod(10)
assert add_part(wo['id'],p['id'],4).status_code==201
assert stock_of(p['id'])==6.0
original=wo_stock.adjust_warehouse_stock
def boom(*a,**k): raise RuntimeError('restore failure injected')
wo_stock.adjust_warehouse_stock=boom
resp=client.patch(f"/api/work-orders/{wo['id']}/status",headers=h,json={'status':'CANCELLED'})
wo_stock.adjust_warehouse_stock=original
assert resp.status_code==409,resp.text          # cancellation refused, not a 500
detail=client.get(f"/api/work-orders/{wo['id']}",headers=h).json()
assert detail['status']=='OPEN'                  # status change rolled back
assert stock_of(p['id'])==6.0                    # stock NOT restored (atomic rollback)
assert status_change_count(wo['id'])==0          # no audit record for the failed cancel
''')


def test_oversized_part_numeric_values_rejected(tmp_path: Path) -> None:
    _smoke(tmp_path, r'''
wo=new_wo(); p=new_prod(100)
base={'product_id':p['id'],'warehouse_id':wh['id'],'quantity':'1','unit_price':'10','tax_rate':'20'}
over_q=client.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json={**base,'quantity':'1000001'})
over_price=client.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json={**base,'unit_price':'100000001'})
over_tax=client.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json={**base,'tax_rate':'101'})
assert over_q.status_code==422 and over_price.status_code==422 and over_tax.status_code==422,(over_q.status_code,over_price.status_code,over_tax.status_code)
# never a 500, and nothing persisted / no stock moved
assert over_q.status_code<500 and over_price.status_code<500 and over_tax.status_code<500
assert client.get(f"/api/work-orders/{wo['id']}/parts",headers=h).json()['items']==[]
assert stock_of(p['id'])==100.0
# max-bound unit_price/tax_rate compute a valid total with no overflow / no 500
ok=client.post(f"/api/work-orders/{wo['id']}/parts",headers=h,json={**base,'quantity':'1','unit_price':'100000000','tax_rate':'100','discount':'0'})
assert ok.status_code==201,ok.text
assert float(ok.json()['total_price'])==200000000.0   # 1 * 1e8 * (1+100%)
''')
