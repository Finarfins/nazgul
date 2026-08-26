from __future__ import annotations

import os
from decimal import Decimal

import pytest


@pytest.mark.postgresql
def test_purchase_dashboard_aggregates_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the dashboard aggregates on real PostgreSQL.

    The SQLite twin proves the arithmetic; this one guards the parts only a real
    dialect can break:

    * the sums run over NUMERIC(18,2)/(18,4) columns rather than SQLite's float
      affinity, so the kurus must match to the cent on both engines;
    * the month bucket is ``substr`` over the normalised-date CASE expression,
      and the supplier/product breakdowns ``ORDER BY`` an output alias under
      PostgreSQL's strict GROUP BY;
    * ``COUNT(DISTINCT ...)`` and the ``CASE WHEN COUNT(DISTINCT product_id)=1``
      product-id resolution behave the same as on SQLite;
    * the ``draft`` exclusion survives the ``COALESCE(status,'completed')``
      comparison against a real VARCHAR column.
    """
    database_url = os.environ.get("SUPPLIER_PRICES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SUPPLIER_PRICES_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        body = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
        headers = {'Authorization':'Bearer ' + body['access_token'],
                   'X-Company-ID':str(body['companies'][0]['id'])}
        changed = client.post('/api/auth/change-password', headers=headers, json={
            'current_password':'admin123', 'new_password':'PgDashboard123!'})
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

        def ok(response, expected=200):
            assert response.status_code == expected, response.text
            return response.json()

        alfa = ok(client.post('/api/suppliers', headers=headers, json={'name':'PG Alfa'}), 201)['id']
        beta = ok(client.post('/api/suppliers', headers=headers, json={'name':'PG Beta'}), 201)['id']
        yag = ok(client.post('/api/products', headers=headers, json={
            'name':'PG Motor Yagi','category':'Yag','purchase_price':'1',
            'sale_price':'10','vat_rate':20}), 201)['id']
        filtre = ok(client.post('/api/products', headers=headers, json={
            'name':'PG Filtre','category':'Filtre','purchase_price':'1',
            'sale_price':'10','vat_rate':20}), 201)['id']

        def purchase(entity, when, items, **extra):
            payload = {'entity_id':entity,'transaction_date':when,'items':items}
            payload.update(extra)
            return ok(client.post('/api/purchases', headers=headers, json=payload), 201)

        purchase(alfa, '2026-05-10', [
            {'product_id':yag,'quantity':'10','unit_price':'100.55','vat_rate':20}])
        purchase(beta, '2026-05-20', [
            {'product_id':filtre,'quantity':'3','unit_price':'200.25','vat_rate':20}])
        # 10% document discount: stored lines and header both become 450.00.
        discounted = purchase(alfa, '2026-06-05', [
            {'product_id':yag,'quantity':'2','unit_price':'250','vat_rate':20}],
            discount_percent='10')
        assert Decimal(str(discounted['final_total'])) == Decimal('450.00')
        # The reorder engine's own output must never count as spend.
        purchase(alfa, '2026-06-15', [
            {'product_id':yag,'quantity':'100','unit_price':'100','vat_rate':20}], status='draft')

        data = ok(client.get('/api/purchase-comparison/spend-analytics', headers=headers))
        totals = data['totals']
        # 1005.50 + 600.75 + 450.00 — to the cent, no float drift.
        assert Decimal(totals['total_try']) == Decimal('2056.25')
        assert totals['purchase_count'] == 3
        assert totals['supplier_count'] == 2
        # Stored lines include the document discount and reconcile to headers.
        assert Decimal(totals['line_total_try']) == Decimal('2056.25')

        assert data['monthly'] == [
            {'month':'2026-05','total_try':'1606.25','purchase_count':2},
            {'month':'2026-06','total_try':'450.00','purchase_count':1},
        ]

        suppliers = {row['supplier_id']: row for row in data['by_supplier']}
        assert Decimal(suppliers[alfa]['total_try']) == Decimal('1455.50')
        assert suppliers[alfa]['purchase_count'] == 2
        assert suppliers[alfa]['last_purchase_date'] == '2026-06-05'
        assert Decimal(suppliers[beta]['total_try']) == Decimal('600.75')
        # The largest supplier sorts first via the output-alias ORDER BY.
        assert data['by_supplier'][0]['supplier_id'] == alfa

        products = {row['product_name']: row for row in data['by_product']}
        assert Decimal(products['PG Motor Yagi']['total_try']) == Decimal('1455.50')
        assert Decimal(products['PG Motor Yagi']['quantity']) == Decimal('12')
        assert products['PG Motor Yagi']['product_id'] == yag
        categories = {row['category']: row for row in data['by_category']}
        assert Decimal(categories['Yag']['total_try']) == Decimal('1455.50')
        assert Decimal(categories['Filtre']['total_try']) == Decimal('600.75')

        window = ok(client.get('/api/purchase-comparison/spend-analytics', headers=headers,
                               params={'date_from':'2026-06-01'}))
        assert Decimal(window['totals']['total_try']) == Decimal('450.00')
        assert [row['month'] for row in window['monthly']] == ['2026-06']

        # Scorecard over the same tenant: the cheaper quote wins the contest.
        for supplier_id, price in ((alfa, '90'), (beta, '110')):
            assert client.post('/api/supplier-prices', headers=headers, json={
                'supplier_id':supplier_id,'product_id':filtre,'price':price}).status_code == 201
        card = ok(client.get('/api/purchase-comparison/supplier-scorecard', headers=headers))
        rows = {row['supplier_id']: row for row in card['suppliers']}
        assert card['comparable_product_count'] == 1
        assert rows[alfa]['best_count'] == 1 and rows[alfa]['worst_count'] == 0
        assert rows[beta]['best_count'] == 0 and rows[beta]['worst_count'] == 1
        assert card['truncated'] is False
