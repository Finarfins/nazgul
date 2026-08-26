from pathlib import Path
import os, shutil, sqlite3, tempfile

TMP = Path(tempfile.mkdtemp(prefix='yhp_v23_'))
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

customer = ok(client.post('/api/customers', headers=headers, json={'name':'V23 Müşteri','opening_balance':0}))
supplier = ok(client.post('/api/suppliers', headers=headers, json={'name':'V23 Tedarikçi','opening_balance':0}))

p1 = ok(client.post('/api/payments', headers=headers, json={'entity_type':'customer','entity_id':customer['id'],'amount':100,'payment_date':'2026-07-01','payment_method':'cash','note':'ilk'}))
p2 = ok(client.post('/api/payments', headers=headers, json={'entity_type':'customer','entity_id':customer['id'],'amount':250,'payment_date':'2026-07-03','payment_method':'bank_transfer','note':'ikinci'}))
p3 = ok(client.post('/api/payments', headers=headers, json={'entity_type':'supplier','entity_id':supplier['id'],'amount':75,'payment_date':'2026-07-02','payment_method':'cash','note':'tedarikçi'}))

rows = ok(client.get('/api/payments?sort=amount_desc', headers=headers))
amounts = [float(x['amount']) for x in rows[:3]]
assert amounts == sorted(amounts, reverse=True), amounts

rows = ok(client.get('/api/payments?entity_type=customer&payment_method=cash&source=manual', headers=headers))
assert len(rows) == 1 and rows[0]['id'] == p1['id'], rows

rows = ok(client.get('/api/payments?date_from=2026-07-02&date_to=2026-07-03&sort=date_asc', headers=headers))
assert [x['payment_date'] for x in rows] == ['2026-07-02','2026-07-03'], rows

summary = ok(client.get('/api/payments/summary?date_from=2026-07-01&date_to=2026-07-03', headers=headers))
assert float(summary['customer_total']) == 350
assert float(summary['supplier_total']) == 75
assert float(summary['manual_total']) == 425
assert int(summary['movement_count']) == 3

# Edit must keep finance linkage consistent and source filtering stable.
ok(client.put(f"/api/payments/{p1['id']}", headers=headers, json={'entity_type':'customer','entity_id':customer['id'],'amount':125,'payment_date':'2026-07-04','payment_method':'cash','note':'güncel'}))
rows = ok(client.get('/api/payments?q=V23&sort=date_desc', headers=headers))
assert rows[0]['id'] == p1['id'] and float(rows[0]['amount']) == 125

ok(client.delete(f"/api/payments/{p3['id']}", headers=headers))
summary = ok(client.get('/api/payments/summary', headers=headers))
assert float(summary['supplier_total']) == 0

con = sqlite3.connect(DB)
assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
assert con.execute('SELECT COUNT(*) FROM finance_transactions WHERE reference_type=\'payment\' AND reference_id=?',(p3['id'],)).fetchone()[0] == 0
con.close()
print('ALL_V2_3_PAYMENT_LIST_TESTS_PASSED')
