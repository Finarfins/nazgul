from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def test_atomic_create_rolls_back_parts_stock_and_work_order(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'atomic-create-parts.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from sqlalchemy import text
c=TestClient(app)
login=c.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json()
cid=login['companies'][0]['id']; uid=login['user']['id']
h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
rot=c.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':'AtomicParts123!'}).json()
h['Authorization']='Bearer '+rot['access_token']
customer=c.post('/api/customers',headers=h,json={'name':'Atomik İş Emri'}).json()
machine=c.post('/api/machines',headers=h,json={'customer_id':customer['id'],'brand':'S','model':'P','serial_number':'ATOMIC-PARTS'}).json()
warehouse=c.get('/api/warehouses',headers=h).json()[0]
products=[
 c.post('/api/products',headers=h,json={'name':name,'purchase_price':5,'sale_price':10,'stock':stock,'unit':'Adet'}).json()
 for name,stock in [('İlk Parça',5),('Yetersiz Parça',1),('Üçüncü Parça',5)]
]
part=lambda product,amount:{'product_id':product['id'],'warehouse_id':warehouse['id'],'quantity':str(amount),'unit_price':'5','discount':'0','tax_rate':'0'}
payload={'machine_id':machine['id'],'customer_id':customer['id'],'technician_id':uid,'work_order_no':'ISE-ATOMIC-FAIL',
         'parts':[part(products[0],2),part(products[1],2),part(products[2],1)]}
failed=c.post('/api/work-orders',headers=h,json=payload)
assert failed.status_code==409,failed.text
with SessionLocal() as db:
 assert db.execute(text("SELECT COUNT(*) FROM work_orders WHERE company_id=:cid AND work_order_no='ISE-ATOMIC-FAIL'"),{'cid':cid}).scalar_one()==0
 assert db.execute(text("SELECT COUNT(*) FROM work_order_parts WHERE company_id=:cid"),{'cid':cid}).scalar_one()==0
 assert db.execute(text("SELECT COUNT(*) FROM work_order_stock_events WHERE company_id=:cid"),{'cid':cid}).scalar_one()==0
 assert db.execute(text("SELECT COUNT(*) FROM stock_movements WHERE company_id=:cid AND reference_type='work_order'"),{'cid':cid}).scalar_one()==0
 first_stock=db.execute(text("SELECT quantity FROM warehouse_stocks WHERE company_id=:cid AND warehouse_id=:wid AND product_id=:pid"),
                        {'cid':cid,'wid':warehouse['id'],'pid':products[0]['id']}).scalar_one()
 assert float(first_stock)==5

with SessionLocal.begin() as db:
 db.execute(text("UPDATE warehouse_stocks SET reserved_quantity=4 WHERE company_id=:cid AND warehouse_id=:wid AND product_id=:pid"),
            {'cid':cid,'wid':warehouse['id'],'pid':products[0]['id']})
reserved=c.post('/api/work-orders',headers=h,json={
 'machine_id':machine['id'],'customer_id':customer['id'],'technician_id':uid,'work_order_no':'ISE-RESERVED-FAIL',
 'parts':[part(products[0],2)],
})
assert reserved.status_code==409,reserved.text
with SessionLocal() as db:
 assert db.execute(text("SELECT COUNT(*) FROM work_orders WHERE company_id=:cid AND work_order_no='ISE-RESERVED-FAIL'"),{'cid':cid}).scalar_one()==0
 row=db.execute(text("SELECT quantity,reserved_quantity FROM warehouse_stocks WHERE company_id=:cid AND warehouse_id=:wid AND product_id=:pid"),
                {'cid':cid,'wid':warehouse['id'],'pid':products[0]['id']}).first()
 assert tuple(map(float,row))==(5,4)

with SessionLocal.begin() as db:
 db.execute(text("""INSERT INTO products(company_id,name,purchase_price,sale_price,stock,unit,active)
                   VALUES(:cid,:name,0,0,0,'Adet',TRUE)"""),
            [{'cid':cid,'name':f'Pagination Ürünü {index:04d}'} for index in range(1998)])
page=c.get('/api/products',headers=h,params={'warehouse_id':warehouse['id'],'limit':2000,'include_meta':'true'})
assert page.status_code==200,page.text
assert len(page.json()['items'])==2000 and page.json()['has_more'] is True
legacy=c.get('/api/products',headers=h,params={'warehouse_id':warehouse['id'],'limit':2})
assert isinstance(legacy.json(),list) and len(legacy.json())==2
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
