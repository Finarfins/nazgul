from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import required_permission


BACKEND = Path(__file__).resolve().parent


def test_supplier_price_rbac_map() -> None:
    # Comparison reads now expose supplier cost, discount tiers and margin (B/C
    # engine), so they moved from the baseline read permission to ``purchases``.
    # FX reads stay on ``read`` — a published TCMB rate is not buying data.
    # Manual price + FX writes were always a purchasing concern.
    assert required_permission("GET", "/api/products/1/supplier-prices") == "purchases"
    assert required_permission("GET", "/api/supplier-prices/history") == "purchases"
    assert required_permission("GET", "/api/purchase-comparison") == "purchases"
    assert required_permission("GET", "/api/exchange-rates") == "read"
    assert required_permission("POST", "/api/supplier-prices") == "purchases"
    assert required_permission("PUT", "/api/supplier-prices/1") == "purchases"
    assert required_permission("DELETE", "/api/supplier-prices/1") == "purchases"
    assert required_permission("PUT", "/api/exchange-rates/override") == "purchases"
    assert required_permission("POST", "/api/exchange-rates/refresh") == "purchases"


def test_supplier_price_comparison_flow(tmp_path: Path) -> None:
    database = tmp_path / "supplier-prices.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from decimal import Decimal
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    body = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    company_a = body['companies'][0]['id']
    headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(company_a)}
    changed = client.post('/api/auth/change-password', headers=headers, json={
        'current_password':'admin123','new_password':'SupplierPrices123!'})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

    supplier_a = client.post('/api/suppliers', headers=headers, json={'name':'Tedarikci A'}).json()['id']
    supplier_b = client.post('/api/suppliers', headers=headers, json={'name':'Tedarikci B'}).json()['id']
    product = client.post('/api/products', headers=headers, json={'name':'Yag Filtresi','purchase_price':'10'}).json()['id']

    # Two purchases from supplier A -> last=120 (latest date), avg=110, count=2.
    for date, price in (('2026-07-01', '100'), ('2026-07-10', '120')):
        resp = client.post('/api/purchases', headers=headers, json={
            'entity_id':supplier_a,'transaction_date':date,
            'items':[{'product_id':product,'quantity':'5','unit_price':price,'vat_rate':20}]})
        assert resp.status_code == 201, resp.text

    # Manual price from supplier B in EUR + FX override 35 -> 105 TRY.
    manual = client.post('/api/supplier-prices', headers=headers, json={
        'supplier_id':supplier_b,'product_id':product,'price':'3','currency':'EUR'})
    assert manual.status_code == 201, manual.text
    manual_id = manual.json()['id']
    assert client.put('/api/exchange-rates/override', headers=headers,
                      json={'currency':'EUR','rate_to_try':'35'}).status_code == 200

    # Duplicate manual price for the same supplier+product is rejected.
    dup = client.post('/api/supplier-prices', headers=headers, json={
        'supplier_id':supplier_b,'product_id':product,'price':'4','currency':'EUR'})
    assert dup.status_code == 409, dup.text

    agg = client.get(f'/api/products/{product}/supplier-prices', headers=headers)
    assert agg.status_code == 200, agg.text
    payload = agg.json()
    assert payload['rates']['EUR'] == '35'
    by_supplier = {row['supplier_id']: row for row in payload['items']}
    a = by_supplier[supplier_a]; b = by_supplier[supplier_b]
    assert Decimal(a['last_purchase_price']) == Decimal('120')
    assert Decimal(a['avg_purchase_price']) == Decimal('110')
    assert a['purchase_count'] == 2
    assert a['last_purchase_date'] == '2026-07-10'
    assert a['manual_price'] is None
    assert Decimal(a['price_in_try']) == Decimal('120')
    assert b['manual_currency'] == 'EUR' and Decimal(b['manual_price']) == Decimal('3')
    assert Decimal(b['manual_price_in_try']) == Decimal('105')
    assert Decimal(b['price_in_try']) == Decimal('105')
    # Cheapest by TRY is supplier B (105 < 120) and carries the best-offer flag,
    # and the list is sorted cheapest -> priciest.
    assert b['best_offer'] is True and a['best_offer'] is False
    assert [row['supplier_id'] for row in payload['items']] == [supplier_b, supplier_a]

    # Manual change captured in price history (source MANUAL).
    updated = client.put(f'/api/supplier-prices/{manual_id}', headers=headers,
                         json={'price':'4','currency':'EUR'})
    assert updated.status_code == 200, updated.text
    observations = client.get('/api/supplier-prices/history', headers=headers,
                              params={'supplier_id':supplier_b,'limit':1})
    assert observations.status_code == 200, observations.text
    first_page = observations.json()
    assert first_page['has_more'] is True and first_page['next_cursor']
    second_page = client.get('/api/supplier-prices/history', headers=headers, params={
        'supplier_id':supplier_b,'limit':1,'cursor':first_page['next_cursor']})
    assert second_page.status_code == 200, second_page.text
    assert second_page.json()['has_more'] is False
    observed = first_page['items'] + second_page.json()['items']
    assert [Decimal(row['price']) for row in observed] == [Decimal('4'), Decimal('3')]
    assert all(row['product_id'] == product and row['source'] == 'MANUAL' for row in observed)
    assert all(row['product_name'] == 'Yag Filtresi' for row in observed)
    assert client.get('/api/supplier-prices/history', headers=headers, params={
        'supplier_id':supplier_b,'cursor':'not-a-cursor'}).status_code == 422
    history = client.get(f'/api/products/{product}/supplier-prices', headers=headers).json()
    assert Decimal(next(r for r in history['items'] if r['supplier_id']==supplier_b)['manual_price']) == Decimal('4')

    # After bumping supplier B to 4 EUR (=140 TRY), supplier A (120 TRY last
    # purchase) is now the cheaper offer — the FX-normalised change flips the
    # best offer. The compare grid reflects this.
    grid = client.get('/api/purchase-comparison', headers=headers, params={'q':'Filtre'})
    assert grid.status_code == 200, grid.text
    grid_body = grid.json()
    assert {s['id'] for s in grid_body['suppliers']} == {supplier_a, supplier_b}
    item = next(row for row in grid_body['items'] if row['product_id']==product)
    assert item['best_offer']['supplier_id'] == supplier_a
    assert Decimal(item['offers'][str(supplier_b)]['price_in_try']) == Decimal('140')

    # Tenant isolation: a second company cannot read or mutate company A rows.
    company_b = client.post('/api/companies', headers=headers, json={'name':'Firma B'}).json()['id']
    headers_b = {**headers, 'X-Company-ID':str(company_b)}
    foreign_history = client.get('/api/supplier-prices/history', headers=headers_b,
                                 params={'supplier_id':supplier_b})
    assert foreign_history.status_code == 400
    assert client.get(f'/api/products/{product}/supplier-prices', headers=headers_b).status_code == 404
    assert client.put(f'/api/supplier-prices/{manual_id}', headers=headers_b,
                      json={'price':'9','currency':'TRY'}).status_code == 404
    assert client.delete(f'/api/supplier-prices/{manual_id}', headers=headers_b).status_code == 404

    # Tenant-specific FX overrides may share the same currency but neither
    # tenant may read or overwrite the other's value.
    override_b = client.put('/api/exchange-rates/override', headers=headers_b,
                            json={'currency':'EUR','rate_to_try':'99'})
    assert override_b.status_code == 200, override_b.text
    update_b = client.put('/api/exchange-rates/override', headers=headers_b,
                          json={'currency':'EUR','rate_to_try':'101'})
    assert update_b.status_code == 200, update_b.text
    rates_a = client.get('/api/exchange-rates', headers=headers).json()['effective']
    rates_b = client.get('/api/exchange-rates', headers=headers_b).json()['effective']
    assert Decimal(rates_a['EUR']) == Decimal('35')
    assert Decimal(rates_b['EUR']) == Decimal('101')

    print('SUPPLIER_PRICE_FLOW_OK')
'''
    completed = subprocess.run(
        [sys.executable, "-c", smoke], env=env, capture_output=True, text=True, cwd=str(BACKEND)
    )
    assert "SUPPLIER_PRICE_FLOW_OK" in completed.stdout, completed.stdout + completed.stderr


def test_purchase_engine_b_decision_flow(tmp_path: Path) -> None:
    """Increment B: effective (discounted+FX) ranking and the speed/stock flag."""
    database = tmp_path / "purchase-engine-b.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from decimal import Decimal
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    body = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
    changed = client.post('/api/auth/change-password', headers=headers, json={
        'current_password':'admin123','new_password':'EngineBravo123!'})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

    def sup(name): return client.post('/api/suppliers', headers=headers, json={'name':name}).json()['id']
    a, b, c = sup('A'), sup('B'), sup('C')
    product = client.post('/api/products', headers=headers, json={'name':'Krank','purchase_price':'1'}).json()['id']
    assert client.put('/api/exchange-rates/override', headers=headers,
                      json={'currency':'EUR','rate_to_try':'35'}).status_code == 200

    # A: 100 TRY, no discount, slow (lead 20), out of stock.
    client.post('/api/supplier-prices', headers=headers, json={
        'supplier_id':a,'product_id':product,'price':'100','currency':'TRY',
        'lead_time_days':20,'supplier_stock':'0','moq':'10'})
    # B: 103 TRY (within 5% of 100), fast (lead 5), in stock -> alternative.
    client.post('/api/supplier-prices', headers=headers, json={
        'supplier_id':b,'product_id':product,'price':'103','currency':'TRY',
        'lead_time_days':5,'supplier_stock':'50'})
    # C: 3 EUR w/ 10% discount -> 2.7 EUR -> 94.5 TRY effective (cheapest overall).
    client.post('/api/supplier-prices', headers=headers, json={
        'supplier_id':c,'product_id':product,'price':'3','currency':'EUR','discount_percent':'10'})

    payload = client.get(f'/api/products/{product}/supplier-prices', headers=headers).json()
    rows = {r['supplier_id']: r for r in payload['items']}
    ra, rb, rc = rows[a], rows[b], rows[c]

    # Effective (discount + FX) math to the kurus.
    assert Decimal(rc['discount_percent']) == Decimal('10')
    assert Decimal(rc['effective_price']) == Decimal('2.7')
    assert Decimal(rc['effective_price_in_try']) == Decimal('94.5')
    # Un-discounted manual keeps effective == raw.
    assert Decimal(ra['effective_price_in_try']) == Decimal('100')

    # Backward compat: price_in_try stays the RAW normalised price (3 EUR*35=105),
    # NOT the discounted effective (94.5). The two must diverge when a discount
    # exists; the discounted value lives only in effective_price_in_try.
    assert Decimal(rc['price_in_try']) == Decimal('105')
    assert Decimal(rc['price_in_try']) != Decimal(rc['effective_price_in_try'])
    # Without a discount the two stay equal.
    assert Decimal(ra['price_in_try']) == Decimal(ra['effective_price_in_try'])

    # Ranking is by effective TRY: C (94.5) < A (100) < B (103).
    assert [r['supplier_id'] for r in payload['items']] == [c, a, b]
    assert rc['best_offer'] is True and ra['best_offer'] is False and rb['best_offer'] is False

    # C is cheapest so no alternative is flagged against it (A/B are >5% pricier).
    assert all(r['faster_alternative'] is False for r in payload['items'])

    # Narrow the field to A vs B (drop C) by tightening threshold interplay:
    # raise C's price above the window so A becomes best, B the alternative.
    cid_price = client.get(f'/api/products/{product}/supplier-prices', headers=headers).json()
    c_price_id = next(r['manual_price_id'] for r in cid_price['items'] if r['supplier_id']==c)
    client.put(f'/api/supplier-prices/{c_price_id}', headers=headers,
               json={'price':'10','currency':'EUR'})  # 350 TRY, out of the running

    p2 = client.get(f'/api/products/{product}/supplier-prices', headers=headers).json()
    r2 = {r['supplier_id']: r for r in p2['items']}
    assert r2[a]['best_offer'] is True                 # 100 cheapest effective
    assert r2[b]['faster_alternative'] is True         # 103 within 5%, faster + in stock
    assert r2[b]['alternative_reason'] == 'faster_in_stock'
    assert r2[a]['faster_alternative'] is False

    # Threshold is configurable: with a 1% window, 103 is out of range -> no flag.
    tight = client.get(f'/api/products/{product}/supplier-prices', headers=headers,
                       params={'alt_threshold':'1'}).json()
    assert all(r['faster_alternative'] is False for r in tight['items'])

    # Grid surfaces the enriched cell fields + the alternative badge.
    grid = client.get('/api/purchase-comparison', headers=headers, params={'q':'Krank'}).json()
    item = next(r for r in grid['items'] if r['product_id']==product)
    assert item['best_offer']['supplier_id'] == a
    cell_b = item['offers'][str(b)]
    assert cell_b['faster_alternative'] is True and cell_b['lead_time_days'] == 5
    assert Decimal(cell_b['supplier_stock']) == Decimal('50')

    print('PURCHASE_ENGINE_B_OK')
'''
    completed = subprocess.run(
        [sys.executable, "-c", smoke], env=env, capture_output=True, text=True, cwd=str(BACKEND)
    )
    assert "PURCHASE_ENGINE_B_OK" in completed.stdout, completed.stdout + completed.stderr
