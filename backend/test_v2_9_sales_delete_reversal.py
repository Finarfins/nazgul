from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_sale_delete_reverses_stock_customer_payment_and_finance(tmp_path: Path) -> None:
    database = tmp_path / "sale-delete-reversal.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.db import SessionLocal

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
_pw = client.post('/api/auth/change-password', headers=headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert _pw.status_code == 200, _pw.text
headers['Authorization'] = 'Bearer ' + _pw.json()['access_token']

customer = client.post('/api/customers', headers=headers, json={'name':'Silme Test Müşterisi','opening_balance':25,'risk_limit':1000})
assert customer.status_code == 201, customer.text
product = client.post('/api/products', headers=headers, json={'name':'Silme Test Ürünü','purchase_price':50,'sale_price':100,'vat_rate':20,'stock':10,'unit':'Adet'})
assert product.status_code == 201, product.text
warehouse = client.get('/api/warehouses', headers=headers).json()[0]
accounts = client.get('/api/finance/accounts?active_only=true', headers=headers).json()
cash = next(a for a in accounts if a['account_type']=='cash')
pos = next(a for a in accounts if a['account_type']=='pos')

cid = int(body['companies'][0]['id'])
customer_id = int(customer.json()['id'])
product_id = int(product.json()['id'])
warehouse_id = int(warehouse['id'])

with SessionLocal() as db:
    stock_before = Decimal(str(db.execute(text('SELECT quantity FROM warehouse_stocks WHERE company_id=:cid AND warehouse_id=:wid AND product_id=:pid'), {'cid':cid,'wid':warehouse_id,'pid':product_id}).scalar_one()))

before_detail = client.get(f'/api/customers/{customer_id}', headers=headers)
assert before_detail.status_code == 200, before_detail.text
balance_before = Decimal(str(before_detail.json()['summary']['current_balance']))
assert balance_before == Decimal('25.00'), before_detail.json()

order = client.post('/api/orders', headers=headers, json={
  'entity_id':customer_id,'transaction_date':'2026-07-15','warehouse_id':warehouse_id,
  'status':'completed','payment_method':'mixed','paid_amount':120,'discount_percent':0,
  'payment_allocations':[
    {'payment_method':'cash','amount':40,'account_id':cash['id']},
    {'payment_method':'card','amount':80,'account_id':pos['id']},
  ],
  'items':[{'product_id':product_id,'quantity':2,'unit_price':100,'vat_rate':20,'discount_percent':0}],
})
assert order.status_code == 201, order.text
order_id = int(order.json()['id'])

with SessionLocal() as db:
    stock_after_sale = Decimal(str(db.execute(text('SELECT quantity FROM warehouse_stocks WHERE company_id=:cid AND warehouse_id=:wid AND product_id=:pid'), {'cid':cid,'wid':warehouse_id,'pid':product_id}).scalar_one()))
    payments = db.execute(text("SELECT id,financial_transaction_id FROM payments WHERE company_id=:cid AND reference_type='order' AND reference_id=:rid ORDER BY id"), {'cid':cid,'rid':order_id}).all()
    movement_count = db.execute(text("SELECT COUNT(*) FROM stock_movements WHERE company_id=:cid AND reference_type='orders' AND reference_id=:rid"), {'cid':cid,'rid':order_id}).scalar_one()
    assert stock_after_sale == stock_before - Decimal('2'), (stock_before, stock_after_sale)
    assert len(payments) == 2, payments
    assert all(row[1] is not None for row in payments), payments
    finance_ids = [int(row[1]) for row in payments]
    assert movement_count == 1, movement_count

after_sale_detail = client.get(f'/api/customers/{customer_id}', headers=headers)
assert after_sale_detail.status_code == 200, after_sale_detail.text
balance_after_sale = Decimal(str(after_sale_detail.json()['summary']['current_balance']))
assert balance_after_sale == balance_before + Decimal('80.00'), after_sale_detail.json()

removed = client.delete(f'/api/orders/{order_id}', headers=headers)
assert removed.status_code == 204, removed.text

with SessionLocal() as db:
    stock_after_delete = Decimal(str(db.execute(text('SELECT quantity FROM warehouse_stocks WHERE company_id=:cid AND warehouse_id=:wid AND product_id=:pid'), {'cid':cid,'wid':warehouse_id,'pid':product_id}).scalar_one()))
    assert stock_after_delete == stock_before, (stock_before, stock_after_delete)
    assert db.execute(text('SELECT COUNT(*) FROM orders WHERE company_id=:cid AND id=:rid'), {'cid':cid,'rid':order_id}).scalar_one() == 0
    assert db.execute(text("SELECT COUNT(*) FROM payments WHERE company_id=:cid AND reference_type='order' AND reference_id=:rid"), {'cid':cid,'rid':order_id}).scalar_one() == 0
    assert db.execute(text("SELECT COUNT(*) FROM stock_movements WHERE company_id=:cid AND reference_type='orders' AND reference_id=:rid"), {'cid':cid,'rid':order_id}).scalar_one() == 0
    for finance_id in finance_ids:
        assert db.execute(text('SELECT COUNT(*) FROM finance_transactions WHERE company_id=:cid AND id=:fid'), {'cid':cid,'fid':finance_id}).scalar_one() == 0

final_detail = client.get(f'/api/customers/{customer_id}', headers=headers)
assert final_detail.status_code == 200, final_detail.text
assert Decimal(str(final_detail.json()['summary']['current_balance'])) == balance_before, final_detail.json()
client.close()
'''
    result = subprocess.run([sys.executable, "-c", smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
