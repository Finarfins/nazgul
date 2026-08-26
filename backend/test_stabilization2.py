import os,shutil,sqlite3,tempfile
from pathlib import Path

tmp=Path(tempfile.mkdtemp())/'test.db';shutil.copy2(Path(__file__).with_name('veriler.db'),tmp)
os.environ['DATABASE_URL']=f'sqlite:///{tmp.as_posix()}'
from fastapi.testclient import TestClient
from app.main import app
client=TestClient(app)
login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'});assert login.status_code==200,login.text
headers={'Authorization':'Bearer '+login.json()['access_token'],'X-Company-ID':str(login.json()['companies'][0]['id'])}
def ok(r): assert r.status_code<300,(r.status_code,r.text);return r.json() if r.content else None
# cariler
c=ok(client.post('/api/customers',headers=headers,json={'name':'Detay Müşteri','opening_balance':100}))
s=ok(client.post('/api/suppliers',headers=headers,json={'name':'Detay Tedarikçi','opening_balance':50}))
# manuel tahsilat yöntemi ve düzenleme
p=ok(client.post('/api/payments',headers=headers,json={'entity_type':'customer','entity_id':c['id'],'amount':25,'payment_date':'2026-07-11','note':'manuel','payment_method':'card'}))
rows=ok(client.get('/api/payments?q=Detay&entity_type=customer',headers=headers));row=next(x for x in rows if x['id']==p['id']);assert row['payment_method']=='card' and row['is_document_payment']==0
ok(client.put(f"/api/payments/{p['id']}",headers=headers,json={'entity_type':'customer','entity_id':c['id'],'amount':30,'payment_date':'2026-07-12','note':'güncel','payment_method':'bank_transfer'}))
# ürün ve belgeye bağlı otomatik tahsilat
product=ok(client.post('/api/products',headers=headers,json={'name':'Detay Ürün','purchase_price':10,'sale_price':20,'vat_rate':20,'stock':5,'unit':'Adet'}))
warehouses=ok(client.get('/api/warehouses',headers=headers));wid=warehouses[0]['id']
order=ok(client.post('/api/orders',headers=headers,json={'entity_id':c['id'],'transaction_date':'2026-07-12','warehouse_id':wid,'status':'completed','payment_method':'cash','paid_amount':20,'discount_percent':0,'items':[{'product_id':product['id'],'quantity':1,'unit_price':20,'vat_rate':20,'discount_percent':0}]}))
auto=next(x for x in ok(client.get('/api/payments',headers=headers)) if x['reference_type']=='order' and x['reference_id']==order['id'])
r=client.put(f"/api/payments/{auto['id']}",headers=headers,json={'entity_type':'customer','entity_id':c['id'],'amount':10,'payment_date':'2026-07-12','note':'boz','payment_method':'cash'});assert r.status_code==409,r.text
r=client.delete(f"/api/payments/{auto['id']}",headers=headers);assert r.status_code==409,r.text
# müşteri detay toplamları, ürünler ve ödeme listesi
cd=ok(client.get(f"/api/customers/{c['id']}",headers=headers));assert cd['summary']['current_balance']==70.0,cd['summary'];assert cd['documents'] and cd['products'] and len(cd['payments'])==2
# tedarikçi detay sözleşmesi
sd=ok(client.get(f"/api/suppliers/{s['id']}",headers=headers));assert sd['summary']['current_balance']==50 and sd['entity']['name']=='Detay Tedarikçi'
con=sqlite3.connect(tmp);assert con.execute('pragma integrity_check').fetchone()[0]=='ok';con.close()
print('ALL_NEXT_1_0_2_STABILIZATION_TESTS_PASSED')
