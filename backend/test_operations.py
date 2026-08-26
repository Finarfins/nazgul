import os, shutil, sqlite3, tempfile
from pathlib import Path

tmp=Path(tempfile.mkdtemp())/'test.db'
shutil.copy2(Path(__file__).with_name('veriler.db'),tmp)
os.environ['DATABASE_URL']=f'sqlite:///{tmp.as_posix()}'
from fastapi.testclient import TestClient
from app.main import app
client=TestClient(app)
login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'})
assert login.status_code==200,login.text
client.headers.update({'Authorization':'Bearer '+login.json()['access_token']})

def ok(r):
 assert r.status_code < 300,(r.status_code,r.text)
 return r.json() if r.content else None

customers=ok(client.get('/api/customers'))
suppliers=ok(client.get('/api/suppliers'))
products=ok(client.get('/api/products?limit=10'))
assert customers and suppliers and products
cid=customers[0]['id'];sid=suppliers[0]['id'];p=products[0]
con=sqlite3.connect(tmp);before=con.execute('select stock from products where id=?',(p['id'],)).fetchone()[0];con.close()
# Satış testi için varsayılan depoya yeterli stok ekle
if before < 10:
 ok(client.post(f"/api/products/{p['id']}/stock",json={'mode':'add','quantity':10-before,'movement_date':'2026-07-11','note':'operation test seed'}))
 before=10
payload={'entity_id':cid,'transaction_date':'2026-07-11','document_no':'TEST-S','note':'otomatik test','items':[{'product_id':p['id'],'quantity':2,'unit_price':100,'vat_rate':20}]}
sale=ok(client.post('/api/orders',json=payload));oid=sale['id']
detail=ok(client.get(f'/api/orders/{oid}'));assert len(detail['items'])==1
con=sqlite3.connect(tmp);after_sale=con.execute('select stock from products where id=?',(p['id'],)).fetchone()[0];con.close();assert after_sale==before-2
payload['items'][0]['quantity']=3
ok(client.put(f'/api/orders/{oid}',json=payload))
con=sqlite3.connect(tmp);after_edit=con.execute('select stock from products where id=?',(p['id'],)).fetchone()[0];con.close();assert after_edit==before-3
purchase_payload={'entity_id':sid,'transaction_date':'2026-07-11','document_no':'TEST-A','items':[{'product_id':p['id'],'quantity':5,'unit_price':60,'vat_rate':20}]}
pur=ok(client.post('/api/purchases',json=purchase_payload));pid=pur['id']
con=sqlite3.connect(tmp);after_purchase=con.execute('select stock from products where id=?',(p['id'],)).fetchone()[0];con.close();assert after_purchase==before+2
pay=ok(client.post('/api/payments',json={'entity_type':'customer','entity_id':cid,'amount':250,'payment_date':'2026-07-11','note':'test'}));
ok(client.get('/api/payments'))
r=client.delete(f"/api/payments/{pay['id']}");assert r.status_code==204
r=client.delete(f'/api/orders/{oid}');assert r.status_code==204
r=client.delete(f'/api/purchases/{pid}');assert r.status_code==204
con=sqlite3.connect(tmp);final=con.execute('select stock from products where id=?',(p['id'],)).fetchone()[0];integrity=con.execute('pragma integrity_check').fetchone()[0];con.close();assert final==before and integrity=='ok'
print('ALL_NEXT_0_2_OPERATION_TESTS_PASSED')
