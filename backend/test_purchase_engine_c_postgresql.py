from __future__ import annotations

import os
from decimal import Decimal

import pytest


@pytest.mark.postgresql
def test_purchase_engine_c_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise Increment C (draft-only one-click PO + reorder suggestions) on PostgreSQL.

    Guards the dialect-sensitive parts: NUMERIC totals remain exact, the one-click
    endpoint cannot cross the approval/receipt boundary, and reorder suggestions
    filter on ``minimum_stock > 0 AND stock <= minimum_stock`` with PostgreSQL
    boolean semantics.
    """
    database_url = os.environ.get("SUPPLIER_PRICES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SUPPLIER_PRICES_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from sqlalchemy import text
    from fastapi.testclient import TestClient

    from app.db import engine
    from app.main import app

    with TestClient(app) as client:
        body = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
        cid = body['companies'][0]['id']
        headers = {'Authorization':'Bearer ' + body['access_token'], 'X-Company-ID':str(cid)}
        changed = client.post('/api/auth/change-password', headers=headers, json={
            'current_password':'admin123', 'new_password':'EngineCharliePg123!'})
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

        supplier = client.post('/api/suppliers', headers=headers, json={'name':'PG Tedarikci C'}).json()['id']
        product = client.post('/api/products', headers=headers, json={'name':'PG Enjektor','purchase_price':'1'}).json()['id']
        assert client.post('/api/supplier-prices', headers=headers, json={
            'supplier_id':supplier,'product_id':product,'price':'100','currency':'TRY','moq':'10'}).status_code == 201

        with engine.connect() as conn:
            stock_before = Decimal(str(conn.execute(
                text("SELECT stock FROM products WHERE id=:id AND company_id=:cid"),
                {"id": product, "cid": cid}).scalar_one()))

        # Defaults: qty = MOQ 10, price 100, status draft. No stock side effect.
        po = client.post('/api/purchase-orders', headers=headers,
                         json={'product_id':product,'supplier_id':supplier})
        assert po.status_code == 201, po.text
        assert Decimal(po.json()['final_total']) == Decimal('1000')  # 10 * 100 (VAT-incl.)
        created = next(row for row in client.get('/api/purchases', headers=headers).json()
                       if row['id'] == po.json()['purchase_id'])
        assert created['status'] == 'draft'
        with engine.connect() as conn:
            after_draft = Decimal(str(conn.execute(
                text("SELECT stock FROM products WHERE id=:id AND company_id=:cid"),
                {"id": product, "cid": cid}).scalar_one()))
        assert after_draft == stock_before

        # Security boundary: one-click PO cannot approve/complete itself.
        unsafe = client.post('/api/purchase-orders', headers=headers, json={
            'product_id':product,'supplier_id':supplier,'quantity':'3','status':'completed'})
        assert unsafe.status_code == 422, unsafe.text
        with engine.connect() as conn:
            after_rejected = Decimal(str(conn.execute(
                text("SELECT stock FROM products WHERE id=:id AND company_id=:cid"),
                {"id": product, "cid": cid}).scalar_one()))
        assert after_rejected == stock_before

        # Reorder suggestions: minimum_stock/stock filter on PostgreSQL NUMERIC.
        with engine.begin() as conn:
            conn.execute(text("UPDATE products SET minimum_stock=:m, stock=:s WHERE id=:id AND company_id=:cid"),
                         {"m": Decimal('50'), "s": Decimal('1'), "id": product, "cid": cid})
        sug = client.get('/api/purchase-comparison/reorder-suggestions', headers=headers)
        assert sug.status_code == 200, sug.text
        entry = next(r for r in sug.json()['items'] if r['product_id'] == product)
        assert entry['best_supplier']['supplier_id'] == supplier
        assert Decimal(entry['best_supplier']['effective_price_in_try']) == Decimal('100')
