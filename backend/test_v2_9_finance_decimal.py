from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_finance_payment_and_instrument_flow_uses_decimal_amounts(tmp_path: Path) -> None:
    database = tmp_path / "finance-decimal.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    script = r'''
from decimal import Decimal
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
headers = {
    'Authorization': 'Bearer ' + body['access_token'],
    'X-Company-ID': str(body['companies'][0]['id']),
}
password_change = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert password_change.status_code == 200, password_change.text
headers['Authorization'] = 'Bearer ' + password_change.json()['access_token']
customer = client.post('/api/customers', headers=headers, json={'name':'Finans Decimal Müşterisi'})
assert customer.status_code == 201, customer.text
customer_id = customer.json()['id']
accounts = client.get('/api/finance/accounts', headers=headers)
assert accounts.status_code == 200 and len(accounts.json()) >= 2, accounts.text
cash = next(x for x in accounts.json() if x['account_type'] == 'cash')
bank = next(x for x in accounts.json() if x['account_type'] == 'bank')

manual = client.post('/api/finance/transactions', headers=headers, json={
    'account_id':cash['id'],'txn_date':'2026-07-13','direction':'in',
    'amount':'1000.10','category':'capital','payment_method':'cash',
    'description':'Decimal sermaye',
})
assert manual.status_code == 201, manual.text

payment = client.post('/api/payments', headers=headers, json={
    'entity_type':'customer','entity_id':customer_id,'amount':'0.10',
    'payment_date':'2026-07-13','note':'Ondalık tahsilat',
    'payment_method':'cash','account_id':cash['id'],
})
assert payment.status_code == 201, payment.text
payment_id = payment.json()['id']

updated = client.put(f'/api/payments/{payment_id}', headers=headers, json={
    'entity_type':'customer','entity_id':customer_id,'amount':'0.20',
    'payment_date':'2026-07-13','note':'Ondalık tahsilat güncel',
    'payment_method':'bank_transfer','account_id':bank['id'],
})
assert updated.status_code == 200, updated.text
transactions = client.get('/api/finance/transactions', headers=headers)
assert transactions.status_code == 200, transactions.text
linked = [x for x in transactions.json() if x.get('reference_type') == 'payment' and x.get('reference_id') == payment_id]
assert len(linked) == 1, linked
assert linked[0]['account_id'] == bank['id'], linked
assert Decimal(str(linked[0]['amount'])) == Decimal('0.20'), linked

instrument = client.post('/api/finance/instruments', headers=headers, json={
    'instrument_type':'check','direction':'received','entity_type':'customer',
    'entity_id':customer_id,'amount':'750.25','issue_date':'2026-07-13',
    'due_date':'2026-08-01','serial_no':'DEC-001','bank_name':'Test Bank',
})
assert instrument.status_code == 201, instrument.text
instrument_id = instrument.json()['id']
collected = client.put(f'/api/finance/instruments/{instrument_id}/status', headers=headers, json={
    'status':'collected','account_id':bank['id'],'transaction_date':'2026-08-01',
})
assert collected.status_code == 200, collected.text
summary = client.get('/api/finance/summary', headers=headers)
assert summary.status_code == 200, summary.text
assert Decimal(str(summary.json()['totals']['cash'])) >= Decimal('1000.10')
client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
