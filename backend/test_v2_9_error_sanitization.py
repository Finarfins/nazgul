from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_unexpected_transaction_and_payment_errors_are_not_exposed(tmp_path: Path) -> None:
    database = tmp_path / "error-sanitization.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app
from app.routers import transactions, finance

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
changed = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert changed.status_code == 200, changed.text
headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
customer = client.post('/api/customers', headers=headers, json={'name':'Hata Test Müşteri','opening_balance':0})
assert customer.status_code == 201, customer.text
product = client.post('/api/products', headers=headers, json={'name':'Hata Test Ürün','purchase_price':10,'sale_price':20,'vat_rate':20,'stock':5,'unit':'Adet'})
assert product.status_code == 201, product.text
warehouse = client.get('/api/warehouses', headers=headers).json()[0]

original_sync = transactions._sync_payment
def fail_transaction(*args, **kwargs):
    raise RuntimeError('SECRET_SQL users.password_hash constraint uq_private')
transactions._sync_payment = fail_transaction
try:
    response = client.post('/api/orders', headers=headers, json={
      'entity_id':customer.json()['id'],'transaction_date':'2026-07-15','warehouse_id':warehouse['id'],
      'status':'completed','payment_method':'cash','paid_amount':20,'discount_percent':0,
      'items':[{'product_id':product.json()['id'],'quantity':1,'unit_price':20,'vat_rate':20,'discount_percent':0}],
    })
finally:
    transactions._sync_payment = original_sync
assert response.status_code == 500, response.text
assert response.json()['detail'] == 'İşlem tamamlanamadı. Lütfen tekrar deneyin.', response.text
assert 'SECRET_SQL' not in response.text and 'password_hash' not in response.text, response.text

original_finance_sync = finance.sync_payment_finance
def fail_payment(*args, **kwargs):
    raise RuntimeError('SECRET_DATABASE finance_transactions internal_column')
finance.sync_payment_finance = fail_payment
try:
    response = client.post('/api/payments', headers=headers, json={
      'entity_type':'customer','entity_id':customer.json()['id'],'amount':10,
      'payment_date':'2026-07-15','payment_method':'cash','note':'test'
    })
finally:
    finance.sync_payment_finance = original_finance_sync
assert response.status_code == 500, response.text
assert response.json()['detail'] == 'Ödeme kaydedilemedi. Lütfen tekrar deneyin.', response.text
assert 'SECRET_DATABASE' not in response.text and 'internal_column' not in response.text, response.text
client.close()
'''
    result = subprocess.run(
        [sys.executable, '-c', smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
