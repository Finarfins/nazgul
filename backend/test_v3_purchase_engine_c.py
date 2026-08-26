from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import required_permission


BACKEND = Path(__file__).resolve().parent


def test_purchase_engine_c_rbac_map() -> None:
    # One-click PO is a purchasing write. The reorder suggestion was a baseline
    # read until the B/C engine started returning supplier cost and margin with
    # it; disclosing buying data to every read role is not acceptable, so it is
    # now gated on ``purchases`` too (see test_v3_purchase_engine_bc.py).
    assert required_permission("POST", "/api/purchase-orders") == "purchases"
    assert required_permission("GET", "/api/purchase-comparison/reorder-suggestions") == "purchases"


def test_purchase_engine_c_flow(tmp_path: Path) -> None:
    """Increment C: one-click PO creates draft-only purchases, and reorder
    suggestions surface low-stock products ranked by best supplier."""
    database = tmp_path / "purchase-engine-c.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.db import engine
from app.main import app


def product_stock(product_id, cid):
    with engine.connect() as conn:
        return conn.execute(text("SELECT stock FROM products WHERE id=:id AND company_id=:cid"),
                            {"id": product_id, "cid": cid}).scalar_one()


def set_min_stock(product_id, cid, minimum, stock):
    with engine.begin() as conn:
        conn.execute(text("UPDATE products SET minimum_stock=:m, stock=:s WHERE id=:id AND company_id=:cid"),
                     {"m": minimum, "s": stock, "id": product_id, "cid": cid})


with TestClient(app) as client:
    body = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    cid = body['companies'][0]['id']
    headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(cid)}
    changed = client.post('/api/auth/change-password', headers=headers, json={
        'current_password':'admin123','new_password':'EngineCharlie123!'})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

    supplier = client.post('/api/suppliers', headers=headers, json={'name':'Tedarikci C'}).json()['id']
    other = client.post('/api/suppliers', headers=headers, json={'name':'Tedarikci D'}).json()['id']
    product = client.post('/api/products', headers=headers, json={'name':'Enjektor','purchase_price':'1'}).json()['id']

    # Manual offer: 100 TRY, MOQ 10. Best (and only) offer for this supplier.
    assert client.post('/api/supplier-prices', headers=headers, json={
        'supplier_id':supplier,'product_id':product,'price':'100','currency':'TRY','moq':'10'}).status_code == 201

    # --- One-click PO, defaults: quantity = MOQ (10), price = 100 TRY, draft. ---
    stock_before = Decimal(str(product_stock(product, cid)))
    po = client.post('/api/purchase-orders', headers=headers,
                     json={'product_id':product,'supplier_id':supplier})
    assert po.status_code == 201, po.text
    po_body = po.json()
    assert po_body['status'] == 'draft'
    assert Decimal(po_body['quantity']) == Decimal('10')
    assert Decimal(po_body['unit_price']) == Decimal('100')
    # Transaction prices are VAT-inclusive: final = qty * price, VAT backed out.
    assert Decimal(po_body['final_total']) == Decimal('1000')    # 10 * 100
    assert Decimal(po_body['subtotal']) == Decimal('833.33')
    assert Decimal(po_body['vat_total']) == Decimal('166.67')
    # Draft => no stock-in yet, exactly like a normal draft purchase.
    assert Decimal(str(product_stock(product, cid))) == stock_before
    # The purchase really exists via the normal listing path, scoped to company.
    listed = client.get('/api/purchases', headers=headers).json()
    assert any(row['id'] == po_body['purchase_id'] for row in listed)

    # --- Security boundary: one-click PO cannot approve/complete itself. ---
    stock_before = Decimal(str(product_stock(product, cid)))
    unsafe = client.post('/api/purchase-orders', headers=headers, json={
        'product_id':product,'supplier_id':supplier,'quantity':'4',
        'expected_price':'90','status':'completed'})
    assert unsafe.status_code == 422, unsafe.text
    assert Decimal(str(product_stock(product, cid))) == stock_before

    # A zero negotiated price is not a valid purchase-order price.
    zero_price = client.post('/api/purchase-orders', headers=headers, json={
        'product_id':product,'supplier_id':supplier,'expected_price':'0'})
    assert zero_price.status_code == 422, zero_price.text

    # No price known and no expected_price given -> 422 (cannot guess a price).
    noprice = client.post('/api/purchase-orders', headers=headers,
                          json={'product_id':product,'supplier_id':other})
    assert noprice.status_code == 422, noprice.text

    # --- Reorder suggestions: low-stock product ranked by best supplier. ---
    # Two suppliers, cheaper one should win the ranking.
    cheap = client.post('/api/suppliers', headers=headers, json={'name':'Ucuz'}).json()['id']
    low = client.post('/api/products', headers=headers, json={'name':'Yakit Pompasi','purchase_price':'1'}).json()['id']
    healthy = client.post('/api/products', headers=headers, json={'name':'Bol Stoklu','purchase_price':'1'}).json()['id']
    client.post('/api/supplier-prices', headers=headers, json={
        'supplier_id':supplier,'product_id':low,'price':'200','currency':'TRY','moq':'6'})
    client.post('/api/supplier-prices', headers=headers, json={
        'supplier_id':cheap,'product_id':low,'price':'150','currency':'TRY'})
    client.post('/api/supplier-prices', headers=headers, json={
        'supplier_id':cheap,'product_id':healthy,'price':'10','currency':'TRY'})
    set_min_stock(low, cid, '10', '2')       # below threshold -> suggested
    set_min_stock(healthy, cid, '5', '99')   # above threshold -> excluded

    sug = client.get('/api/purchase-comparison/reorder-suggestions', headers=headers)
    assert sug.status_code == 200, sug.text
    items = {row['product_id']: row for row in sug.json()['items']}
    assert low in items and healthy not in items
    entry = items[low]
    assert Decimal(entry['current_stock']) == Decimal('2')
    assert Decimal(entry['minimum_stock']) == Decimal('10')
    # Best supplier is the cheaper one (150 < 200) via effective-price ranking.
    assert entry['best_supplier']['supplier_id'] == cheap
    assert Decimal(entry['best_supplier']['effective_price_in_try']) == Decimal('150')
    # Suggested qty = max(deficit 8, best-supplier MOQ none) -> 8.
    assert Decimal(entry['suggested_quantity']) == Decimal('8')

    # --- Multi-tenant isolation: another company cannot order company A rows. ---
    company_b = client.post('/api/companies', headers=headers, json={'name':'Firma C2'}).json()['id']
    headers_b = {**headers, 'X-Company-ID':str(company_b)}
    cross = client.post('/api/purchase-orders', headers=headers_b,
                        json={'product_id':product,'supplier_id':supplier,'expected_price':'50'})
    assert cross.status_code == 404, cross.text
    # And company B sees no reorder suggestions from company A.
    assert client.get('/api/purchase-comparison/reorder-suggestions', headers=headers_b).json()['items'] == []

    print('PURCHASE_ENGINE_C_OK')
'''
    completed = subprocess.run(
        [sys.executable, "-c", smoke], env=env, capture_output=True, text=True, cwd=str(BACKEND)
    )
    assert "PURCHASE_ENGINE_C_OK" in completed.stdout, completed.stdout + completed.stderr
