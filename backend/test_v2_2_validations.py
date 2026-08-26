from pathlib import Path
import os, shutil, sqlite3, tempfile

TMP = Path(tempfile.mkdtemp(prefix='yhp_v22_'))
DB = TMP / 'test.db'
shutil.copy2(Path(__file__).with_name('veriler.db'), DB)
os.environ['DATABASE_URL'] = f'sqlite:///{DB.as_posix()}'

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
headers = {'Authorization': 'Bearer ' + login.json()['access_token'], 'X-Company-ID': str(login.json()['companies'][0]['id'])}

def ok(response):
    assert response.status_code < 300, (response.status_code, response.text)
    return response.json() if response.content else None

customer = ok(client.post('/api/customers', headers=headers, json={'name':'  Validasyon   Müşteri  ','opening_balance':0}))
assert ok(client.get(f"/api/customers/{customer['id']}", headers=headers))['entity']['name'] == 'Validasyon Müşteri'
product = ok(client.post('/api/products', headers=headers, json={'name':'Validasyon Ürün','purchase_price':10,'sale_price':20,'vat_rate':20,'stock':5,'unit':'Adet'}))
warehouse_id = ok(client.get('/api/warehouses', headers=headers))[0]['id']

base = {
    'entity_id': customer['id'],
    'transaction_date': '11.07.2026',
    'document_no': 'S-TEST-001',
    'due_date': '12.07.2026',
    'warehouse_id': warehouse_id,
    'status': 'completed',
    'payment_method': 'credit',
    'paid_amount': 0,
    'discount_percent': 0,
    'items': [{'product_id': product['id'], 'quantity': 1, 'unit_price': 20, 'vat_rate': 20, 'discount_percent': 0}],
}
order = ok(client.post('/api/orders', headers=headers, json=base))
detail = ok(client.get(f"/api/orders/{order['id']}", headers=headers))
assert detail['document']['transaction_date'] == '2026-07-11'
assert detail['document']['due_date'] == '2026-07-12'

# Duplicate document number must fail within same company/document type.
r = client.post('/api/orders', headers=headers, json={**base, 'transaction_date':'2026-07-13', 'due_date':'2026-07-14'})
assert r.status_code == 409, r.text

# Due date before document date must fail.
r = client.post('/api/orders', headers=headers, json={**base, 'document_no':'S-TEST-002', 'transaction_date':'2026-07-15', 'due_date':'2026-07-14'})
assert r.status_code == 422, r.text

# Duplicate product lines must fail.
r = client.post('/api/orders', headers=headers, json={**base, 'document_no':'S-TEST-003', 'items': base['items'] * 2})
assert r.status_code == 422, r.text

# Invalid payment date must fail.
r = client.post('/api/payments', headers=headers, json={'entity_type':'customer','entity_id':customer['id'],'amount':10,'payment_date':'31-12-2026','payment_method':'cash'})
assert r.status_code == 422, r.text

# Payment method/account type mismatch must fail transactionally.
accounts = ok(client.get('/api/finance/accounts?active_only=true', headers=headers))
bank = next(x for x in accounts if x['account_type']=='bank')
r = client.post('/api/payments', headers=headers, json={'entity_type':'customer','entity_id':customer['id'],'amount':10,'payment_date':'2026-07-12','payment_method':'cash','account_id':bank['id']})
assert r.status_code == 400, r.text

con = sqlite3.connect(DB)
assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
# Failed requests must not leave partial payment rows.
assert con.execute("SELECT COUNT(*) FROM payments WHERE entity_type='customer' AND entity_id=?", (customer['id'],)).fetchone()[0] == 0
con.close()
print('ALL_V2_2_VALIDATION_BALANCE_TESTS_PASSED')
