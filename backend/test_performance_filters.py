from fastapi.testclient import TestClient
from app.main import app
from app.db import engine
from sqlalchemy import text

client=TestClient(app)
login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'})
assert login.status_code==200,login.text
token=login.json()['access_token']
headers={'Authorization':f'Bearer {token}'}
companies=client.get('/api/companies',headers=headers)
assert companies.status_code==200,companies.text
company_id=companies.json()[0]['id']
headers['X-Company-ID']=str(company_id)

orders=client.get('/api/orders',headers=headers,params={'sort':'total_desc','date_from':'2023-01-01','date_to':'2027-12-31'})
assert orders.status_code==200,orders.text
rows=orders.json()
assert all(float(rows[i]['final_total'] or 0)>=float(rows[i+1]['final_total'] or 0) for i in range(len(rows)-1))

asc=client.get('/api/orders',headers=headers,params={'sort':'date_asc'})
assert asc.status_code==200,asc.text
asc_rows=asc.json()
assert all(str(asc_rows[i]['normalized_date'])<=str(asc_rows[i+1]['normalized_date']) for i in range(len(asc_rows)-1))

suppliers=client.get('/api/suppliers',headers=headers,params={'sort':'balance_desc'})
assert suppliers.status_code==200,suppliers.text
assert all('order_count' in x for x in suppliers.json())

with engine.connect() as conn:
    assert conn.execute(text('PRAGMA integrity_check')).scalar()=='ok'
print('ALL_NEXT_1_0_3_PERFORMANCE_FILTER_TESTS_PASSED')
