from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest


@pytest.mark.postgresql
def test_purchase_engine_bc_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the B/C engine on real PostgreSQL.

    Guards the dialect-sensitive parts that SQLite cannot prove:

    * the discount ladder and the ranking are computed at the evaluated quantity
      over NUMERIC(18,4) columns — the tier boundary must land on the same rung
      and the effective price on the same kurus as on SQLite;
    * the margin divides NUMERIC values (VAT-exclusive on both sides) rather
      than PostgreSQL integers;
    * net demand joins ``order_items`` and ``return_items`` with grouped
      aggregates, and ``on_order`` filters purchase status with PostgreSQL's
      strict GROUP BY and boolean handling;
    * grouped reorder drafts run inside one transaction that also takes
      ``pg_advisory_xact_lock`` and writes the idempotency row — the SQLite path
      skips the lock entirely, so this is the only place it is executed;
    * drafts remain stock-neutral against a real NUMERIC stock column.
    """
    database_url = os.environ.get("SUPPLIER_PRICES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SUPPLIER_PRICES_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from datetime import date

    from sqlalchemy import text
    from fastapi.testclient import TestClient

    from app.db import engine
    from app.main import app

    with TestClient(app) as client:
        body = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
        cid = body['companies'][0]['id']
        headers = {'Authorization':'Bearer ' + body['access_token'], 'X-Company-ID':str(cid)}
        changed = client.post('/api/auth/change-password', headers=headers, json={
            'current_password':'admin123', 'new_password':'EngineBCPg123!'})
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
        today = date.today().isoformat()

        def set_stock(value: str) -> None:
            # Go through the stock endpoint: ``products.stock`` is a denormalised
            # summary of ``warehouse_stocks``, so writing it directly would leave
            # the warehouse rows behind and the sale below would be refused.
            resp = client.post(f'/api/products/{product}/stock', headers=headers,
                               json={'mode':'set','quantity':value,'movement_date':today})
            assert resp.status_code in (200, 201), resp.text

        def stock_of(product_id: int) -> Decimal:
            with engine.connect() as conn:
                return Decimal(str(conn.execute(
                    text("SELECT stock FROM products WHERE id=:id AND company_id=:cid"),
                    {"id": product_id, "cid": cid}).scalar_one()))

        supplier_a = client.post('/api/suppliers', headers=headers, json={'name':'PG Ted A'}).json()['id']
        supplier_b = client.post('/api/suppliers', headers=headers, json={'name':'PG Ted B'}).json()['id']
        product = client.post('/api/products', headers=headers, json={
            'name':'PG Enjektor BC','purchase_price':'1','sale_price':'150','vat_rate':20}).json()['id']

        # A: 100 with a 50+ -> 10% rung. B: 95 flat. Below 50 units B wins; at 60
        # A's rung makes it 90 and it takes over.
        assert client.post('/api/supplier-prices', headers=headers, json={
            'supplier_id':supplier_a,'product_id':product,'price':'100','currency':'TRY',
            'moq':'10','lead_time_days':5,
            'tiers':[{'min_quantity':'50','discount_percent':'10'}]}).status_code == 201
        assert client.post('/api/supplier-prices', headers=headers, json={
            'supplier_id':supplier_b,'product_id':product,'price':'95','currency':'TRY',
            'moq':'10','lead_time_days':40}).status_code == 201

        small = client.get(f'/api/purchase-comparison/products/{product}/analysis',
                           headers=headers, params={'quantity':'10'})
        assert small.status_code == 200, small.text
        assert small.json()['recommended_supplier_id'] == supplier_b

        big = client.get(f'/api/purchase-comparison/products/{product}/analysis',
                         headers=headers, params={'quantity':'60'})
        assert big.status_code == 200, big.text
        rows = {row['supplier_id']: row for row in big.json()['items']}
        assert Decimal(rows[supplier_a]['applied_discount_percent']) == Decimal('10')
        assert Decimal(rows[supplier_a]['catalog_unit_cost_try']) == Decimal('90')
        assert Decimal(rows[supplier_a]['catalog_cost_try_total']) == Decimal('5400')
        # 150 incl 20% VAT -> 125 net; 90 incl VAT -> 75 net; margin 40%.
        assert Decimal(rows[supplier_a]['margin_percent']) == Decimal('40')
        assert big.json()['recommended_supplier_id'] == supplier_a
        # The lead-time filter excludes loudly instead of dropping silently.
        limited = client.get(f'/api/purchase-comparison/products/{product}/analysis',
                             headers=headers, params={'quantity':'60','max_lead_days':10})
        assert [row['supplier_id'] for row in limited.json()['excluded']] == [supplier_b]

        # Net demand over NUMERIC aggregates: 180 sold - 90 returned = 90 in the
        # window -> 1/day. The lead time comes from the current best offer, which
        # at no stated quantity is B (95 TRY, 40 days), so the derived reorder
        # point is 1 * 40 + minimum_stock(0) = 40 and the target one cycle above.
        customer = client.post('/api/customers', headers=headers, json={'name':'PG Musteri'}).json()['id']
        set_stock('400')
        sale = client.post('/api/orders', headers=headers, json={
            'entity_id':customer,'transaction_date':today,
            'items':[{'product_id':product,'quantity':'180','unit_price':'150','vat_rate':20}]})
        assert sale.status_code == 201, sale.text
        ret = client.post('/api/workflow/sale_return', headers=headers, json={
            'entity_id':customer,'document_date':today,'status':'completed',
            'source_type':'order','source_id':sale.json()['id'],
            'items':[{'product_id':product,'quantity':'90','unit_price':'150','vat_rate':20}]})
        assert ret.status_code in (200, 201), ret.text

        set_stock('4')
        sug = client.get('/api/purchase-comparison/reorder-suggestions', headers=headers)
        assert sug.status_code == 200, sug.text
        entry = next(row for row in sug.json()['items'] if row['product_id'] == product)
        assert entry['reorder_point_source'] == '90-day-derived'
        assert Decimal(entry['demand_sold']) == Decimal('180')
        assert Decimal(entry['demand_returned']) == Decimal('90')
        assert Decimal(entry['avg_daily_demand']) == Decimal('1')
        assert Decimal(entry['reorder_point']) == Decimal('40')
        assert Decimal(entry['target_stock']) == Decimal('80')
        assert entry['on_order_basis'] == 'open_document_lines'

        # Grouped drafts: one transaction, advisory lock, MOQ rounding reported.
        before = stock_of(product)
        body_lines = {'lines':[{'product_id':product,'supplier_id':supplier_a,'quantity':'12'}]}
        drafts = client.post('/api/purchase-comparison/reorder-drafts',
                             headers={**headers, 'Idempotency-Key':'pg-reorder-1'}, json=body_lines)
        assert drafts.status_code == 201, drafts.text
        payload = drafts.json()
        assert payload['replayed'] is False
        line = payload['purchases'][0]['lines'][0]
        assert Decimal(line['requested_quantity']) == Decimal('12')
        assert Decimal(line['created_quantity']) == Decimal('20')   # ceil(12/10)*10
        assert payload['purchases'][0]['status'] == 'draft'
        assert stock_of(product) == before

        def reorder_fixture(name: str) -> int:
            pid = client.post('/api/products', headers=headers, json={
                'name':name,'purchase_price':'1','sale_price':'10','stock':'0'}).json()['id']
            policy = client.put(
                f'/api/purchase-comparison/products/{pid}/reorder-policy',
                headers=headers, json={'reorder_point':'5','target_stock':'10'})
            assert policy.status_code == 200, policy.text
            price = client.post('/api/supplier-prices', headers=headers, json={
                'supplier_id':supplier_a,'product_id':pid,'price':'5','currency':'TRY'})
            assert price.status_code == 201, price.text
            return pid

        def parallel_posts(payload: dict, request_headers: dict[str, str]) -> list:
            start = Barrier(2)

            def post_once():
                local_client = TestClient(app)
                try:
                    start.wait(timeout=10)
                    return local_client.post(
                        '/api/purchase-comparison/reorder-drafts',
                        headers=request_headers,
                        json=payload,
                    )
                finally:
                    local_client.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(post_once) for _ in range(2)]
                return [future.result(timeout=30) for future in futures]

        # Real simultaneous requests: the advisory lock serialises the tenant,
        # the second request revalidates after the first draft and creates none.
        locked_product = reorder_fixture('PG Paralel Kilit')
        locked_body = {'lines':[{
            'product_id':locked_product,'supplier_id':supplier_a,
        }]}
        locked = parallel_posts(locked_body, headers)
        assert [response.status_code for response in locked] == [201, 201]
        assert sum(bool(response.json()['purchases']) for response in locked) == 1

        # Force the fallback boundary by removing only the advisory lock in this
        # test. Both requests pass the replay read together; the UNIQUE key then
        # chooses one transaction and the loser must replay, never leak a 500.
        from app.routers import supplier_prices as supplier_price_router

        race = Barrier(2)
        original_lock_reorder = supplier_price_router._lock_reorder
        monkeypatch.setattr(
            supplier_price_router,
            "_lock_reorder",
            lambda _db, _cid: race.wait(timeout=10),
        )
        raced_product = reorder_fixture('PG Paralel Idempotency')
        raced_body = {'lines':[{
            'product_id':raced_product,'supplier_id':supplier_a,
        }]}
        raced = parallel_posts(
            raced_body,
            {**headers, 'Idempotency-Key':'pg-parallel-reorder'},
        )
        assert [response.status_code for response in raced] == [201, 201]
        assert sum(response.json()['replayed'] is False for response in raced) == 1
        assert sum(response.json()['replayed'] is True for response in raced) == 1
        assert len({
            row['purchase_id']
            for response in raced
            for row in response.json()['purchases']
        }) == 1
        monkeypatch.setattr(
            supplier_price_router,
            "_lock_reorder",
            original_lock_reorder,
        )

        # Replay under the same key returns the same drafts; a different body
        # under that key is refused.
        again = client.post('/api/purchase-comparison/reorder-drafts',
                            headers={**headers, 'Idempotency-Key':'pg-reorder-1'}, json=body_lines)
        assert again.status_code == 201, again.text
        assert again.json()['replayed'] is True
        assert [row['purchase_id'] for row in again.json()['purchases']] == \
               [row['purchase_id'] for row in payload['purchases']]
        conflict = client.post('/api/purchase-comparison/reorder-drafts',
                               headers={**headers, 'Idempotency-Key':'pg-reorder-1'},
                               json={'lines':[{'product_id':product,'supplier_id':supplier_b}]})
        assert conflict.status_code == 409, conflict.text

        # The open draft counts as on_order (so the next run does not re-order
        # what is already coming) while real stock stays untouched.
        repeat = client.get('/api/purchase-comparison/reorder-suggestions', headers=headers)
        again_entry = next(row for row in repeat.json()['items'] if row['product_id'] == product)
        assert Decimal(again_entry['on_order']) == Decimal('20')
        assert Decimal(again_entry['on_hand']) == Decimal('4')
        assert Decimal(again_entry['available']) == Decimal('24')
        assert Decimal(again_entry['deficit']) == Decimal('56')   # target 80 - available 24
        assert stock_of(product) == before

        # Dashboard aggregates survive the PostgreSQL NUMERIC round trip.
        dash = client.get('/api/purchase-comparison/dashboard', headers=headers)
        assert dash.status_code == 200, dash.text
        assert dash.json()['on_order_basis'] == 'open_document_lines'
