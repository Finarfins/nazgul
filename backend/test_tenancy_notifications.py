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
data=login.json(); token=data['access_token']; default_company=data['companies'][0]['id']
base={'Authorization':'Bearer '+token,'X-Company-ID':str(default_company)}
original=client.get('/api/customers',headers=base).json(); original_ids={x['id'] for x in original}
created=client.post('/api/companies',headers=base,json={'name':'Test İzole Firma','tax_number':'1234567890'})
assert created.status_code==201,created.text
new_company=created.json()['id']; isolated={**base,'X-Company-ID':str(new_company)}
assert client.get('/api/branches',headers=isolated).status_code==200
assert client.get('/api/customers',headers=isolated).json()==[]
customer=client.post('/api/customers',headers=isolated,json={'name':'Sadece İkinci Firma','phone':'5550000000','opening_balance':10})
assert customer.status_code==201,customer.text
new_customer_id=customer.json()['id']
assert any(x['id']==new_customer_id for x in client.get('/api/customers',headers=isolated).json())
assert new_customer_id not in {x['id'] for x in client.get('/api/customers',headers=base).json()}
notice=client.get('/api/notifications',headers=base)
assert notice.status_code==200 and 'items' in notice.json()
forbidden=client.get('/api/customers',headers={**base,'X-Company-ID':'999999'})
assert forbidden.status_code==403
con=sqlite3.connect(tmp);assert con.execute('pragma integrity_check').fetchone()[0]=='ok';con.close()
print('ALL_NEXT_0_5_TENANCY_NOTIFICATIONS_TESTS_PASSED')
