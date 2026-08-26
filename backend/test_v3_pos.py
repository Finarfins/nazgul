from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import required_permission

BACKEND = Path(__file__).resolve().parent


def test_pos_rbac_map() -> None:
    assert required_permission("GET", "/api/pos/lookup") == "read"
    assert required_permission("POST", "/api/pos/sale") == "sales"


def test_pos_lookup_sale_stock_and_tenant_isolation(tmp_path: Path) -> None:
    database = tmp_path / "pos.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.db import SessionLocal
from app.main import app

with TestClient(app) as client:
    original_post = client.post
    pos_request_no = 0
    def post_with_pos_key(url, *args, **kwargs):
        global pos_request_no
        if url == '/api/pos/sale':
            pos_request_no += 1
            kwargs['headers'] = {
                **(kwargs.get('headers') or {}),
                'Idempotency-Key': (kwargs.get('headers') or {}).get(
                    'Idempotency-Key', f'pos-smoke-{pos_request_no}'
                ),
            }
        return original_post(url, *args, **kwargs)
    client.post = post_with_pos_key
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    body = login.json()
    company_a = body['companies'][0]['id']
    headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(company_a)}
    changed = client.post('/api/auth/change-password', headers=headers, json={
        'current_password':'admin123','new_password':'PosQuickSale123!'})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

    product = client.post('/api/products', headers=headers, json={
        'name':'POS Filtre','product_code':'POS-1','barcode':'869000000001',
        'purchase_price':'7.00','sale_price':'12.35','vat_rate':20,'stock':'5','unit':'Adet'})
    assert product.status_code == 201, product.text
    product_id = product.json()['id']
    warehouses = client.get('/api/warehouses', headers=headers).json()
    warehouse_id = next(row['id'] for row in warehouses if row['is_default'])

    hit = client.get('/api/pos/lookup', headers=headers, params={
        'barcode':'8690-0000 0001','warehouse_id':warehouse_id,
        'input_source':'barcode_scanner'})
    assert hit.status_code == 200, hit.text
    assert hit.json()['id'] == product_id
    assert hit.json()['code'] == 'POS-1'
    assert Decimal(str(hit.json()['unit_price'])) == Decimal('12.35')
    assert Decimal(str(hit.json()['stock'])) == Decimal('5')
    assert hit.json()['warehouse_id'] == warehouse_id
    assert client.get('/api/pos/lookup', headers=headers, params={'barcode':'missing'}).status_code == 404

    duplicate = client.post('/api/products', headers=headers, json={
        'name':'POS Filtre Kopya','product_code':'POS-1B','barcode':'8690-0000-0001',
        'purchase_price':'7.00','sale_price':'12.35','vat_rate':20,'stock':'1','unit':'Adet'})
    assert duplicate.status_code == 201, duplicate.text
    ambiguous = client.get('/api/pos/lookup', headers=headers, params={'barcode':'869000000001'})
    assert ambiguous.status_code == 409, ambiguous.text
    assert ambiguous.json()['detail']['code'] == 'AMBIGUOUS_BARCODE'
    assert ambiguous.json()['detail']['candidate_count'] == 2
    with SessionLocal() as db:
        db.execute(text('UPDATE products SET active=FALSE WHERE id=:id'),
                   {'id':duplicate.json()['id']})
        db.commit()

    missing_key = original_post('/api/pos/sale', headers=headers, json={
        'items':[{'product_id':product_id,'quantity':'1','unit_price':'12.35'}],
        'payment_type':'cash','warehouse_id':warehouse_id})
    assert missing_key.status_code == 400, missing_key.text

    sale = client.post('/api/pos/sale', headers=headers, json={
        'items':[{'product_id':product_id,'quantity':'2','unit_price':'12.35'}],
        'payment_type':'cash','note':'Kasa testi','warehouse_id':warehouse_id})
    assert sale.status_code == 201, sale.text
    sale_body = sale.json()
    assert Decimal(str(sale_body['final_total'])) == Decimal('24.70')
    assert Decimal(str(sale_body['paid_amount'])) == Decimal('24.70')
    assert Decimal(str(sale_body['subtotal'])) == Decimal('20.58')
    assert Decimal(str(sale_body['vat_total'])) == Decimal('4.12')

    with SessionLocal() as db:
        order = db.execute(text(
            'SELECT company_id,final_total,payment_method,note FROM orders WHERE id=:id'
        ), {'id':sale_body['sale_id']}).mappings().one()
        assert order['company_id'] == company_a
        assert Decimal(str(order['final_total'])) == Decimal('24.70')
        assert order['payment_method'] == 'cash'
        assert order['note'] == 'Kasa testi'
        assert Decimal(db.execute(text(
            'SELECT quantity FROM warehouse_stocks WHERE company_id=:cid AND product_id=:pid'
        ), {'cid':company_a,'pid':product_id}).scalar_one()) == Decimal('3')
        assert db.execute(text(
            "SELECT COUNT(*) FROM stock_movements WHERE company_id=:cid "
            "AND reference_type='orders' AND reference_id=:id AND warehouse_id=:wid"
        ), {'cid':company_a,'id':sale_body['sale_id'],'wid':warehouse_id}).scalar_one() == 1

    blocked = client.post('/api/pos/sale', headers=headers, json={
        'items':[{'product_id':product_id,'quantity':'4','unit_price':'12.35'}],
        'payment_type':'cash'})
    assert blocked.status_code == 409, blocked.text
    with SessionLocal() as db:
        assert Decimal(db.execute(text(
            'SELECT quantity FROM warehouse_stocks WHERE company_id=:cid AND product_id=:pid'
        ), {'cid':company_a,'pid':product_id}).scalar_one()) == Decimal('3')
        assert db.execute(text(
            'SELECT COUNT(*) FROM orders WHERE company_id=:cid'
        ), {'cid':company_a}).scalar_one() == 1

    company_b_response = client.post('/api/companies', headers=headers, json={'name':'POS Firma B'})
    assert company_b_response.status_code == 201, company_b_response.text
    company_b = company_b_response.json()['id']
    headers_b = {**headers,'X-Company-ID':str(company_b)}
    assert client.get('/api/pos/lookup', headers=headers_b, params={'barcode':'869000000001'}).status_code == 404
    assert client.post('/api/pos/sale', headers=headers_b, json={
        'items':[{'product_id':product_id,'quantity':'1','unit_price':'12.35'}],
        'payment_type':'cash'}).status_code == 404

print('POS_SQLITE_OK')
'''
    completed = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "POS_SQLITE_OK" in completed.stdout


def test_pos_customer_editable_price_and_discounts(tmp_path: Path) -> None:
    """Increment 1: named cari, negotiated unit price, line + document discount."""
    database = tmp_path / "pos-customer.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PAYMENT_ALLOCATION_ENGINE_ENABLED"] = "true"
    completed = subprocess.run(
        [sys.executable, "-c", _CUSTOMER_SMOKE],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert "POS_CUSTOMER_OK" in completed.stdout, completed.stdout + completed.stderr


# Shared, DB-agnostic scenario; test_pos_postgresql.py execs the same script.
_CUSTOMER_SMOKE = r'''
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.db import SessionLocal
from app.main import app

with TestClient(app) as client:
    original_post = client.post
    pos_request_no = 0
    def post_with_pos_key(url, *args, **kwargs):
        global pos_request_no
        if url == '/api/pos/sale':
            pos_request_no += 1
            kwargs['headers'] = {
                **(kwargs.get('headers') or {}),
                'Idempotency-Key': (kwargs.get('headers') or {}).get(
                    'Idempotency-Key', f'pos-customer-{pos_request_no}'
                ),
            }
        return original_post(url, *args, **kwargs)
    client.post = post_with_pos_key
    body = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
    changed = client.post('/api/auth/change-password', headers=headers, json={
        'current_password':'admin123','new_password':'PosCustomer123!'})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

    company_a = client.post('/api/companies', headers=headers, json={'name':'POS Cari Firma A'}).json()['id']
    headers['X-Company-ID'] = str(company_a)

    product = client.post('/api/products', headers=headers, json={
        'name':'POS Yag','product_code':'POS-2','barcode':'869000000002',
        'purchase_price':'40.00','sale_price':'100.00','vat_rate':20,'stock':'50','unit':'Adet'})
    assert product.status_code == 201, product.text
    pid = product.json()['id']
    customer = client.post('/api/customers', headers=headers, json={'name':'Mehmet Çiftçi'})
    assert customer.status_code == 201, customer.text
    customer_id = customer.json()['id']

    def order_customer(sale_id):
        with SessionLocal() as db:
            return db.execute(text(
                'SELECT customer_id FROM orders WHERE id=:id AND company_id=:cid'
            ), {'id': sale_id, 'cid': company_a}).scalar_one()

    def order_count():
        with SessionLocal() as db:
            return db.execute(text(
                'SELECT COUNT(*) FROM orders WHERE company_id=:cid'
            ), {'cid': company_a}).scalar_one()

    # --- Walk-in default is unchanged: the retail system cari ----------------
    walk_in = client.post('/api/pos/sale', headers=headers, json={
        'items':[{'product_id':pid,'quantity':'1','unit_price':'100.00'}],
        'payment_type':'cash'})
    assert walk_in.status_code == 201, walk_in.text
    assert walk_in.json()['customer_name'] == 'Perakende Satış'
    with SessionLocal() as db:
        retail_id = db.execute(text(
            "SELECT id FROM customers WHERE company_id=:cid AND name='Perakende Satış'"
        ), {'cid': company_a}).scalar_one()
    assert order_customer(walk_in.json()['sale_id']) == retail_id

    # --- A named customer books onto that cari ------------------------------
    named = client.post('/api/pos/sale', headers=headers, json={
        'items':[{'product_id':pid,'quantity':'1','unit_price':'100.00'}],
        'payment_type':'cash','customer_id':customer_id})
    assert named.status_code == 201, named.text
    assert named.json()['customer_id'] == customer_id
    assert named.json()['customer_name'] == 'Mehmet Çiftçi'
    assert order_customer(named.json()['sale_id']) == customer_id

    # --- Named customer may pay partially; the remainder stays on cari -----
    partial = client.post('/api/pos/sale', headers=headers, json={
        'items':[{'product_id':pid,'quantity':'1','unit_price':'100.00'}],
        'payment_type':'cash','paid_amount':'90.00','customer_id':customer_id})
    assert partial.status_code == 201, partial.text
    assert Decimal(str(partial.json()['paid_amount'])) == Decimal('90.00')
    assert Decimal(str(partial.json()['remaining_amount'])) == Decimal('10.00')
    with SessionLocal() as db:
        stored = db.execute(text(
            'SELECT paid_amount FROM orders WHERE id=:id AND company_id=:cid'
        ), {'id':partial.json()['sale_id'],'cid':company_a}).scalar_one()
        assert Decimal(str(stored)) == Decimal('90.00')
        payment = db.execute(text(
            "SELECT amount FROM payments WHERE company_id=:cid "
            "AND reference_type='order' AND reference_id=:id"
        ), {'cid':company_a,'id':partial.json()['sale_id']}).scalar_one()
        assert Decimal(str(payment)) == Decimal('90.00')

    anonymous_partial = client.post('/api/pos/sale', headers=headers, json={
        'items':[{'product_id':pid,'quantity':'1','unit_price':'100.00'}],
        'payment_type':'cash','paid_amount':'90.00'})
    assert anonymous_partial.status_code == 400, anonymous_partial.text
    assert 'müşteri seçin' in anonymous_partial.text.lower()

    retail_partial = client.post('/api/pos/sale', headers=headers, json={
        'items':[{'product_id':pid,'quantity':'1','unit_price':'100.00'}],
        'payment_type':'cash','paid_amount':'90.00','customer_id':retail_id})
    assert retail_partial.status_code == 400, retail_partial.text
    assert 'müşteri seçin' in retail_partial.text.lower()

    # --- Network/button retry replays one sale instead of double-writing -----
    retry_headers = {**headers, 'Idempotency-Key':'pos-retry-1'}
    retry_payload = {
        'items':[{'product_id':pid,'quantity':'1','unit_price':'100.00'}],
        'payment_type':'cash','customer_id':customer_id}
    first_retry = client.post('/api/pos/sale', headers=retry_headers, json=retry_payload)
    assert first_retry.status_code == 201, first_retry.text
    after_first_retry = order_count()
    with SessionLocal() as db:
        db.execute(text(
            'UPDATE orders SET paid_amount=0 WHERE id=:id AND company_id=:cid'
        ), {'id':first_retry.json()['sale_id'],'cid':company_a})
        db.commit()
    replay = client.post('/api/pos/sale', headers=retry_headers, json=retry_payload)
    assert replay.status_code == 201, replay.text
    assert replay.json()['sale_id'] == first_retry.json()['sale_id']
    assert Decimal(str(replay.json()['paid_amount'])) == Decimal(str(first_retry.json()['paid_amount']))
    assert Decimal(str(replay.json()['remaining_amount'])) == Decimal(str(first_retry.json()['remaining_amount']))
    assert order_count() == after_first_retry
    changed_retry = client.post('/api/pos/sale', headers=retry_headers, json={
        **retry_payload, 'note':'farkli govde'})
    assert changed_retry.status_code == 409, changed_retry.text
    assert order_count() == after_first_retry

    # --- Credit REQUIRES a customer: nobody to collect from otherwise -------
    before = order_count()
    anonymous_credit = client.post('/api/pos/sale', headers=headers, json={
        'items':[{'product_id':pid,'quantity':'1','unit_price':'100.00'}],
        'payment_type':'credit'})
    assert anonymous_credit.status_code == 400, anonymous_credit.text
    assert order_count() == before, 'rejected credit sale must not write an order'

    on_credit = client.post('/api/pos/sale', headers=headers, json={
        'items':[{'product_id':pid,'quantity':'1','unit_price':'100.00'}],
        'payment_type':'credit','customer_id':customer_id})
    assert on_credit.status_code == 201, on_credit.text
    # Credit leaves the whole amount outstanding on the customer's cari.
    assert Decimal(str(on_credit.json()['paid_amount'])) == Decimal('0')
    assert Decimal(str(on_credit.json()['remaining_amount'])) == Decimal(str(on_credit.json()['final_total']))

    # --- Negotiated price is free in Increment 1, but remains audited --------
    negotiated_payload = {
        'items':[{'product_id':pid,'quantity':'2','unit_price':'50.00'}],
        'payment_type':'cash'}
    negotiated = client.post('/api/pos/sale', headers=headers, json=negotiated_payload)
    assert negotiated.status_code == 201, negotiated.text
    assert order_count() == before + 2
    # 2 x 50 = 100, NOT 2 x 100 (the list price).
    assert Decimal(str(negotiated.json()['final_total'])) == Decimal('100.00'), negotiated.text

    # --- Line discount then document discount, in that order ----------------
    # 2 x 50 = 100 -> line 10% -> 90 -> document 25% -> 67.50
    discounted = client.post('/api/pos/sale', headers=headers, json={
        'items':[{'product_id':pid,'quantity':'2','unit_price':'50.00','discount_percent':'10'}],
        'payment_type':'cash','discount_percent':'25'})
    assert discounted.status_code == 201, discounted.text
    payload = discounted.json()
    assert Decimal(str(payload['grand_total'])) == Decimal('90.00'), payload
    assert Decimal(str(payload['discount_amount'])) == Decimal('22.50'), payload
    assert Decimal(str(payload['final_total'])) == Decimal('67.50'), payload
    # A cash sale is settled in full at the counter.
    assert Decimal(str(payload['paid_amount'])) == Decimal('67.50'), payload

    discounted_partial = client.post('/api/pos/sale', headers=headers, json={
        'items':[{'product_id':pid,'quantity':'2','unit_price':'50.00','discount_percent':'10'}],
        'payment_type':'cash','discount_percent':'25','paid_amount':'60.00',
        'customer_id':customer_id})
    assert discounted_partial.status_code == 201, discounted_partial.text
    combined = discounted_partial.json()
    assert Decimal(str(combined['grand_total'])) == Decimal('90.00'), combined
    assert Decimal(str(combined['discount_amount'])) == Decimal('22.50'), combined
    assert Decimal(str(combined['final_total'])) == Decimal('67.50'), combined
    assert Decimal(str(combined['paid_amount'])) == Decimal('60.00'), combined
    assert Decimal(str(combined['remaining_amount'])) == Decimal('7.50'), combined

    # Zero/overflow prices fail at validation on both SQLite and PostgreSQL.
    for invalid_price in ('0', '100000001'):
        invalid = client.post('/api/pos/sale', headers=headers, json={
            'items':[{'product_id':pid,'quantity':'1','unit_price':invalid_price}],
            'payment_type':'cash'})
        assert invalid.status_code == 422, invalid.text

    # Sales users can also negotiate without approval/reason in Increment 1.
    created_user = client.post('/api/users', headers=headers, json={
        'username':'pos_satis','display_name':'POS Satış','password':'PosUser123!X',
        'role':'satis'})
    assert created_user.status_code == 201, created_user.text
    sales_login = client.post('/api/auth/login', json={
        'username':'pos_satis','password':'PosUser123!X'})
    assert sales_login.status_code == 200, sales_login.text
    sales_headers = {'Authorization':'Bearer '+sales_login.json()['access_token'],
                     'X-Company-ID':str(company_a)}
    rotated = client.post('/api/auth/change-password', headers=sales_headers, json={
        'current_password':'PosUser123!X','new_password':'PosUser456!X'})
    assert rotated.status_code == 200, rotated.text
    sales_headers['Authorization'] = 'Bearer ' + rotated.json()['access_token']
    sales_payload = {
        'items':[{'product_id':pid,'quantity':'1','unit_price':'50.00'}],
        'payment_type':'cash'}
    sales_override = client.post('/api/pos/sale', headers=sales_headers, json=sales_payload)
    assert sales_override.status_code == 201, sales_override.text

    with SessionLocal() as db:
        assert db.execute(text(
            "SELECT COUNT(*) FROM policy_override_logs "
            "WHERE company_id=:cid AND policy_name='pos_price_override'"
        ), {'cid':company_a}).scalar_one() == 4
        assert db.execute(text(
            "SELECT COUNT(*) FROM policy_override_logs "
            "WHERE company_id=:cid AND policy_name='pos_price_override' "
            "AND reason='POS fiyat/iskonto serbest değişikliği'"
        ), {'cid':company_a}).scalar_one() == 4

    # --- Another tenant's customer is invisible -----------------------------
    company_b = client.post('/api/companies', headers=headers, json={'name':'POS Cari Firma B'}).json()['id']
    headers_b = {**headers,'X-Company-ID':str(company_b)}
    foreign = client.post('/api/pos/sale', headers=headers_b, json={
        'items':[{'product_id':pid,'quantity':'1','unit_price':'100.00'}],
        'payment_type':'cash','customer_id':customer_id})
    assert foreign.status_code == 404, foreign.text

print('POS_CUSTOMER_OK')
'''
