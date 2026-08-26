from fastapi.testclient import TestClient
from app.main import app

c=TestClient(app)
r=c.post('/api/auth/login',json={'username':'admin','password':'admin123'}); assert r.status_code==200,r.text
token=r.json()['access_token']; h={'Authorization':f'Bearer {token}'}
orders=c.get('/api/orders',headers=h).json(); assert orders
purchases=c.get('/api/purchases',headers=h).json(); assert purchases
products=c.get('/api/products',headers=h).json(); assert products
for url,ctype in [
 (f"/api/documents/orders/{orders[0]['id']}/pdf",'application/pdf'),
 (f"/api/documents/orders/{orders[0]['id']}/xlsx",'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
 (f"/api/documents/purchases/{purchases[0]['id']}/pdf",'application/pdf'),
 (f"/api/products/{products[0]['id']}/qr.png",'image/png'),
 (f"/api/products/{products[0]['id']}/label.pdf",'application/pdf'),
 ('/api/exports/products.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')]:
 x=c.get(url,headers=h);assert x.status_code==200,(url,x.status_code,x.text[:200]);assert ctype in x.headers.get('content-type','');assert len(x.content)>100
print('ALL_NEXT_1_0_OUTPUT_PWA_TESTS_PASSED')
