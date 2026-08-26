from __future__ import annotations

import os
from decimal import Decimal

import pytest


@pytest.mark.postgresql
def test_purchase_engine_b_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    """Increment B decision intelligence on real PostgreSQL.

    Guards the dialect-sensitive parts of the enriched aggregation: the GROUP BY
    with functional dependency (PostgreSQL enforces it), NUMERIC discount + FX
    normalisation, boolean is_active filtering, and that ranking by the effective
    TRY price plus the speed/stock alternative flag behave identically to SQLite.
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
            'current_password':'admin123', 'new_password':'EngineBravoPg123!'})
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

        def sup(name: str) -> int:
            return client.post('/api/suppliers', headers=headers, json={'name':name}).json()['id']

        a, b, c = sup('PG B A'), sup('PG B B'), sup('PG B C')
        product = client.post('/api/products', headers=headers,
                              json={'name':'PG Krank','purchase_price':'1'}).json()['id']
        assert client.put('/api/exchange-rates/override', headers=headers,
                          json={'currency':'EUR','rate_to_try':'35'}).status_code == 200

        # A: 100 TRY, slow, out of stock. B: 103 TRY (<5% pricier), fast + in stock.
        client.post('/api/supplier-prices', headers=headers, json={
            'supplier_id':a,'product_id':product,'price':'100','currency':'TRY',
            'lead_time_days':20,'supplier_stock':'0'})
        client.post('/api/supplier-prices', headers=headers, json={
            'supplier_id':b,'product_id':product,'price':'103','currency':'TRY',
            'lead_time_days':5,'supplier_stock':'50'})
        # C: 3 EUR w/ 10% discount -> 2.7 EUR -> 94.5 TRY effective (cheapest).
        client.post('/api/supplier-prices', headers=headers, json={
            'supplier_id':c,'product_id':product,'price':'3','currency':'EUR','discount_percent':'10'})

        payload = client.get(f'/api/products/{product}/supplier-prices', headers=headers).json()
        rows = {r['supplier_id']: r for r in payload['items']}
        # Effective (discount + FX) math to the kurus.
        assert Decimal(rows[c]['effective_price']) == Decimal('2.7')
        assert Decimal(rows[c]['effective_price_in_try']) == Decimal('94.5')
        assert Decimal(rows[a]['effective_price_in_try']) == Decimal('100')
        # Ranking by effective TRY: C < A < B.
        assert [r['supplier_id'] for r in payload['items']] == [c, a, b]
        assert rows[c]['best_offer'] is True

        # Push C out of the running -> A best, B the speed/stock alternative.
        c_price_id = rows[c]['manual_price_id']
        client.put(f'/api/supplier-prices/{c_price_id}', headers=headers,
                   json={'price':'10','currency':'EUR'})
        after = client.get(f'/api/products/{product}/supplier-prices', headers=headers).json()
        r2 = {r['supplier_id']: r for r in after['items']}
        assert r2[a]['best_offer'] is True
        assert r2[b]['faster_alternative'] is True
        assert r2[b]['alternative_reason'] == 'faster_in_stock'

        grid = client.get('/api/purchase-comparison', headers=headers, params={'q':'Krank'}).json()
        assert {s['id'] for s in grid['suppliers']} == {a, b, c}
        item = next(r for r in grid['items'] if r['product_id']==product)
        assert item['best_offer']['supplier_id'] == a
        assert item['offers'][str(b)]['faster_alternative'] is True
