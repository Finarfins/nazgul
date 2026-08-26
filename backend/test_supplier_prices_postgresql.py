from __future__ import annotations

import os
from decimal import Decimal

import pytest


@pytest.mark.postgresql
def test_supplier_price_comparison_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the Purchase Comparison Engine on real PostgreSQL.

    Guards the dialect-sensitive parts of the aggregation: the GROUP BY with a
    correlated last-price subquery (PostgreSQL enforces functional dependency),
    NUMERIC math for average/normalisation, and boolean is_active filtering.
    """
    database_url = os.environ.get("SUPPLIER_PRICES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SUPPLIER_PRICES_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        body = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
        headers = {
            'Authorization':'Bearer ' + body['access_token'],
            'X-Company-ID':str(body['companies'][0]['id']),
        }
        changed = client.post('/api/auth/change-password', headers=headers, json={
            'current_password':'admin123', 'new_password':'SupplierPricesPg123!'})
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

        supplier_a = client.post('/api/suppliers', headers=headers, json={'name':'PG Tedarikci A'}).json()['id']
        supplier_b = client.post('/api/suppliers', headers=headers, json={'name':'PG Tedarikci B'}).json()['id']
        product = client.post('/api/products', headers=headers, json={'name':'PG Yag Filtresi','purchase_price':'10'}).json()['id']

        for date, price in (('2026-07-01','100'), ('2026-07-10','120')):
            resp = client.post('/api/purchases', headers=headers, json={
                'entity_id':supplier_a,'transaction_date':date,
                'items':[{'product_id':product,'quantity':'5','unit_price':price,'vat_rate':20}]})
            assert resp.status_code == 201, resp.text

        manual = client.post('/api/supplier-prices', headers=headers, json={
            'supplier_id':supplier_b,'product_id':product,'price':'3','currency':'EUR'})
        assert manual.status_code == 201, manual.text
        updated = client.put(f"/api/supplier-prices/{manual.json()['id']}", headers=headers,
                             json={'price':'2','currency':'EUR'})
        assert updated.status_code == 200, updated.text
        first_history = client.get('/api/supplier-prices/history', headers=headers, params={
            'supplier_id':supplier_b,'limit':1}).json()
        assert first_history['has_more'] is True and first_history['next_cursor']
        second_history = client.get('/api/supplier-prices/history', headers=headers, params={
            'supplier_id':supplier_b,'limit':1,'cursor':first_history['next_cursor']})
        assert second_history.status_code == 200, second_history.text
        assert [row['price'] for row in first_history['items'] + second_history.json()['items']] == [
            '2.0000', '3.0000']
        assert client.put('/api/exchange-rates/override', headers=headers,
                          json={'currency':'EUR','rate_to_try':'35'}).status_code == 200

        payload = client.get(f'/api/products/{product}/supplier-prices', headers=headers).json()
        by_supplier = {row['supplier_id']: row for row in payload['items']}
        a = by_supplier[supplier_a]; b = by_supplier[supplier_b]
        assert Decimal(a['last_purchase_price']) == Decimal('120')
        assert Decimal(a['avg_purchase_price']) == Decimal('110')
        assert a['purchase_count'] == 2
        assert a['last_purchase_date'] == '2026-07-10'
        assert Decimal(b['manual_price_in_try']) == Decimal('70')
        # Cheapest-first ranking with the best-offer flag on the TRY-cheapest.
        assert [row['supplier_id'] for row in payload['items']] == [supplier_b, supplier_a]
        assert b['best_offer'] is True and a['best_offer'] is False

        grid = client.get('/api/purchase-comparison', headers=headers, params={'q':'Filtre'})
        assert grid.status_code == 200, grid.text
        assert {s['id'] for s in grid.json()['suppliers']} == {supplier_a, supplier_b}
