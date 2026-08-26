from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app

client=TestClient(app)
login=client.post('/api/auth/login',json={'username':'admin','password':'admin123'})
assert login.status_code==200, login.text
token=login.json()['access_token']
headers={'Authorization':f'Bearer {token}'}

customers=client.get('/api/customers?limit=5',headers=headers)
assert customers.status_code==200 and customers.json(), customers.text
cid=customers.json()[0]['id']
detail=client.get(f'/api/customers/{cid}',headers=headers)
assert detail.status_code==200, detail.text
body=detail.json()
assert {'entity','summary','documents','payments','products'} <= set(body)

products=client.get('/api/products?limit=5',headers=headers)
assert products.status_code==200 and products.json(), products.text
pid=products.json()[0]['id']
pdetail=client.get(f'/api/products/{pid}',headers=headers)
assert pdetail.status_code==200, pdetail.text
pbody=pdetail.json()
assert {'product','movements','warehouse_stocks'} <= set(pbody)

root=Path(__file__).resolve().parents[1]/'frontend'/'src'
entity=(root/'pages'/'EntityDetail.tsx').read_text(encoding='utf-8')
product=(root/'pages'/'ProductDetail.tsx').read_text(encoding='utf-8')
transactions=(root/'pages'/'Transactions.tsx').read_text(encoding='utf-8')
appsrc=(root/'App.tsx').read_text(encoding='utf-8')
assert 'Satış Yap' in entity and 'Tahsilat Al' in entity and 'initialEntityId' in entity
assert 'Depo Stokları' in product and 'Stok Hareketleri' in product
assert 'openId' in transactions and 'newForEntityId' in transactions
assert 'urunler/:id' in appsrc
print('ALL_NEXT_1_0_5_DETAIL_WORKFLOW_TESTS_PASSED')
