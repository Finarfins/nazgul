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
# müşteri CRUD
c=ok(client.post('/api/customers',headers=headers,json={'name':'E2E Müşteri','phone':'5550001122','opening_balance':100}))
ok(client.put(f"/api/customers/{c['id']}",headers=headers,json={'name':'E2E Müşteri Güncel','phone':'5550003344','opening_balance':150}))
d=ok(client.get(f"/api/customers/{c['id']}",headers=headers));assert d['customer']['name']=='E2E Müşteri Güncel'
# tedarikçi CRUD
s=ok(client.post('/api/suppliers',headers=headers,json={'name':'E2E Tedarikçi','opening_balance':20}))
ok(client.put(f"/api/suppliers/{s['id']}",headers=headers,json={'name':'E2E Tedarikçi Güncel','opening_balance':25}))
# ödeme ekle/düzenle/sil
p=ok(client.post('/api/payments',headers=headers,json={'entity_type':'customer','entity_id':c['id'],'amount':50,'payment_date':'2026-07-11','note':'ilk'}))
ok(client.put(f"/api/payments/{p['id']}",headers=headers,json={'entity_type':'customer','entity_id':c['id'],'amount':75,'payment_date':'2026-07-12','note':'güncel'}))
rows=ok(client.get('/api/payments',headers=headers));row=next(x for x in rows if x['id']==p['id']);assert row['amount']==75 and row['note']=='güncel'
r=client.delete(f"/api/payments/{p['id']}",headers=headers);assert r.status_code==204
# boş cariler güvenli silinir
r=client.delete(f"/api/customers/{c['id']}",headers=headers);assert r.status_code==204,r.text
r=client.delete(f"/api/suppliers/{s['id']}",headers=headers);assert r.status_code==204,r.text
con=sqlite3.connect(tmp);assert con.execute('pragma integrity_check').fetchone()[0]=='ok';con.close()
print('ALL_NEXT_1_0_1_STABILIZATION_TESTS_PASSED')
