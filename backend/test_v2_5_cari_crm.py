from pathlib import Path
import os, shutil, sqlite3, tempfile
from datetime import date, timedelta

TMP=Path(tempfile.mkdtemp(prefix='yhp_v25_'))
DB=TMP/'test.db'
shutil.copy2(Path(__file__).with_name('veriler.db'),DB)
os.environ['DATABASE_URL']=f'sqlite:///{DB.as_posix()}'

from fastapi.testclient import TestClient
from app.main import app

client=TestClient(app)
login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'})
assert login.status_code==200,login.text
headers={'Authorization':'Bearer '+login.json()['access_token'],'X-Company-ID':str(login.json()['companies'][0]['id'])}

def ok(r):
    assert r.status_code<300,(r.status_code,r.text)
    return r.json() if r.content else None

customer=ok(client.post('/api/customers',headers=headers,json={
    'name':'V25 CRM Müşteri','owner_name':'Ali Veli','phone':'5551112233','email':'crm@example.com',
    'opening_balance':100,'risk_limit':500,'payment_term_days':30,'notes':'Önemli müşteri','is_active':True
}))
assert customer['risk_limit']==500
cid=customer['id']

supplier=ok(client.post('/api/suppliers',headers=headers,json={
    'name':'V25 CRM Tedarikçi','owner_name':'Satın Alma','opening_balance':0,'risk_limit':1000,'payment_term_days':45,'is_active':True
}))
sid=supplier['id']

# create overdue sale directly to test CRM summary
con=sqlite3.connect(DB)
due=(date.today()-timedelta(days=7)).isoformat()
con.execute("INSERT INTO orders(customer_id,order_date,subtotal,vat_total,grand_total,discount_percent,discount_amount,final_total,document_no,due_date,company_id,status,payment_method,paid_amount) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(cid,date.today().isoformat(),600,0,600,0,0,600,'CRM-OVERDUE',due,1,'completed','credit',0))
con.commit();con.close()

detail=ok(client.get(f'/api/customers/{cid}',headers=headers))
assert detail['entity']['owner_name']=='Ali Veli'
assert float(detail['summary']['overdue_amount'])>=600
assert detail['summary']['risk_exceeded'] is True

note=ok(client.post(f'/api/customers/{cid}/notes',headers=headers,json={'note':'  Görüşme yapıldı   '}))
assert note['note']=='Görüşme yapıldı'
detail=ok(client.get(f'/api/customers/{cid}',headers=headers))
assert any(x['id']==note['id'] for x in detail['notes'])
ok(client.delete(f"/api/customers/{cid}/notes/{note['id']}",headers=headers))

# active/inactive filter and toggle
ok(client.patch(f'/api/customers/{cid}/active?active=false',headers=headers))
active_rows=ok(client.get('/api/customers?active=active',headers=headers))
assert not any(x['id']==cid for x in active_rows)
inactive_rows=ok(client.get('/api/customers?active=inactive',headers=headers))
assert any(x['id']==cid for x in inactive_rows)

snote=ok(client.post(f'/api/suppliers/{sid}/notes',headers=headers,json={'note':'Tedarikçi notu'}))
sdetail=ok(client.get(f'/api/suppliers/{sid}',headers=headers))
assert sdetail['entity']['payment_term_days']==45
assert any(x['id']==snote['id'] for x in sdetail['notes'])

con=sqlite3.connect(DB)
assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
cols={r[1] for r in con.execute('PRAGMA table_info(customers)')}
assert {'risk_limit','payment_term_days','notes','is_active'}<=cols
assert con.execute('SELECT COUNT(*) FROM entity_notes').fetchone()[0]>=1
con.close()
print('ALL_V2_5_CARI_CRM_TESTS_PASSED')
