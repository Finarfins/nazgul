from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_customer_center_uses_clean_database_and_preserves_tenant_isolation(tmp_path: Path) -> None:
    database = tmp_path / "customer-center.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
import sqlite3
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
if body['user'].get('must_change_password'):
    changed = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'V3-Customer-Center!2026'})
    assert changed.status_code == 200, changed.text
    # Changing the password rotates the session and invalidates every previously
    # issued access token (see test_v2_9_password_change_session_rotation), so the
    # rest of this scenario must continue with the freshly minted one.
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

def ok(response):
    assert response.status_code < 300, (response.status_code, response.text)
    return response.json() if response.content else None

customer = ok(client.post('/api/customers', headers=headers, json={
    'name':'V3 Merkez Müşteri','phone':'05550000000','opening_balance':100,
    'risk_limit':5000,'is_active':True
}))
contact = ok(client.post(f"/api/customers/{customer['id']}/contacts", headers=headers, json={
    'name':'Ayşe Demir','role':'Muhasebe','phone':'05551112233','email':'ayse@example.com',
    'is_primary':True,'notes':'EFT ile ödeme'
}))
task = ok(client.post(f"/api/customers/{customer['id']}/tasks", headers=headers, json={
    'title':'Tahsilat için ara','due_date':'2026-07-20','priority':'high',
    'assigned_to':'Berkay','notes':'Sabah ara'
}))

# Create a completed sale and a partial collection so the list balance query is exercised.
product = ok(client.post('/api/products', headers=headers, json={
    'name':'Cari Liste Ürünü','purchase_price':10,'sale_price':25,'vat_rate':20,
    'stock':10,'unit':'Adet'
}))
warehouse = ok(client.get('/api/warehouses', headers=headers))[0]
order = ok(client.post('/api/orders', headers=headers, json={
    'entity_id':customer['id'],'transaction_date':'2026-07-15','warehouse_id':warehouse['id'],
    'status':'completed','payment_method':'credit','paid_amount':0,'discount_percent':0,
    'items':[{'product_id':product['id'],'quantity':2,'unit_price':25,'vat_rate':20,'discount_percent':0}]
}))
collection = client.post('/api/payments', headers=headers, json={
    'entity_type':'customer','entity_id':customer['id'],'payment_date':'2026-07-15',
    'amount':20,'payment_method':'cash','note':'Kısmi tahsilat'
})
assert collection.status_code == 201, collection.text

listed = ok(client.get('/api/customers', headers=headers, params={'q':'V3 Merkez'}))
assert len(listed) == 1, listed
assert float(listed[0]['current_balance']) == 130.0, listed[0]

detail = ok(client.get(f"/api/customers/{customer['id']}", headers=headers))
assert detail['contacts'] and detail['contacts'][0]['id'] == contact['id']
assert detail['contacts'][0]['is_primary'] in (1, True)
assert detail['tasks'] and detail['tasks'][0]['id'] == task['id']
assert detail['tasks'][0]['status'] == 'open'
assert all(k in detail['summary'] for k in ('current_balance','overdue_amount','risk_usage_percent','last_activity'))

completed = ok(client.patch(f"/api/customers/{customer['id']}/tasks/{task['id']}", headers=headers, json={'status':'completed'}))
assert completed['status'] == 'completed'
detail2 = ok(client.get(f"/api/customers/{customer['id']}", headers=headers))
assert detail2['tasks'][0]['status'] == 'completed'
assert detail2['tasks'][0]['completed_at']

company_b = ok(client.post('/api/companies', headers=headers, json={'name':'V3 Firma B'}))['id']
hb = {**headers, 'X-Company-ID':str(company_b)}
assert client.get(f"/api/customers/{customer['id']}", headers=hb).status_code == 404
assert client.delete(f"/api/customers/{customer['id']}/contacts/{contact['id']}", headers=hb).status_code == 404

ok(client.delete(f"/api/customers/{customer['id']}/contacts/{contact['id']}", headers=headers))
final = ok(client.get(f"/api/customers/{customer['id']}", headers=headers))
assert final['contacts'] == []

con = sqlite3.connect(r''' + repr(database.as_posix()) + r''')
assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
assert {'entity_contacts','entity_tasks'}.issubset({r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")})
con.close()
client.close()
'''
    result = subprocess.run(
        [sys.executable, '-c', smoke], cwd=BACKEND, env=env,
        text=True, capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
