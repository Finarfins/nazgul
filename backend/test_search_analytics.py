import os, shutil, tempfile
from pathlib import Path

tmp=Path(tempfile.mkdtemp())/'test.db'
shutil.copy2(Path(__file__).with_name('veriler.db'),tmp)
os.environ['DATABASE_URL']=f'sqlite:///{tmp.as_posix()}'
from fastapi.testclient import TestClient
from app.main import app

client=TestClient(app)
login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'})
assert login.status_code==200,login.text
headers={'Authorization':'Bearer '+login.json()['access_token']}
customers=client.get('/api/customers',headers=headers).json()
products=client.get('/api/products',headers=headers).json()
assert customers and products
cq=str(customers[0]['name'])[:3]
pq=str(products[0]['name'])[:3]
cr=client.get('/api/search',headers=headers,params={'q':cq}).json()
pr=client.get('/api/search',headers=headers,params={'q':pq}).json()
assert any(x['type']=='customer' for x in cr['items']),cr
assert any(x['type']=='product' for x in pr['items']),pr
ins=client.get('/api/analytics/insights',headers=headers)
assert ins.status_code==200,ins.text
body=ins.json()
for key in ('sales_trend','overdue','inactive_customers','critical_products','profitable_products','recommendations'):
    assert key in body,key
print('ALL_NEXT_0_9_COMMAND_ANALYTICS_TESTS_PASSED')
