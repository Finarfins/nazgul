"""Purchase engine B/C — quantity-aware analysis and the reorder engine.

These tests pin the decisions that make the module trustworthy rather than the
plumbing around them:

B — the discount ladder is resolved at the evaluated quantity and can CHANGE
which supplier is cheapest; margin is computed VAT-exclusive and honours the
per-record ``price_includes_vat`` flag; a lead-time limit excludes rather than
silently drops; an explicitly requested quantity is never grown to the MOQ.

C — the trigger is ``on_hand + on_order <= reorder_point``; demand is NET of
sale returns (which are independent ``returns`` documents, so a status filter
alone would over-count); drafts are grouped per supplier, rounded up to the MOQ
with both figures reported, idempotent, re-verified under the lock, and can
never leave ``draft``.

Each scenario runs in its own subprocess against a throwaway SQLite file so the
app's module-level settings singleton binds to that database.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import required_permission


BACKEND = Path(__file__).resolve().parent


def _run(tmp_path: Path, name: str, script: str) -> None:
    database = tmp_path / f"{name}.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, cwd=str(BACKEND)
    )
    assert "ENGINE_BC_OK" in completed.stdout, completed.stdout + completed.stderr


LOGIN = r'''
from decimal import Decimal
from fastapi.testclient import TestClient
from app.main import app

def session(client, password):
    body = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    headers = {'Authorization':'Bearer '+body['access_token'],
               'X-Company-ID':str(body['companies'][0]['id'])}
    changed = client.post('/api/auth/change-password', headers=headers, json={
        'current_password':'admin123','new_password':password})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
    return headers
'''


def test_purchase_engine_bc_rbac_map() -> None:
    """Cost/margin reads are purchasing data, not baseline reads.

    The analysis, reorder and dashboard endpoints disclose supplier cost,
    negotiated discount tiers and gross margin, so they are gated on
    ``purchases`` instead of inheriting the generic GET -> ``read`` rule. FX
    rates stay on ``read``: a published TCMB rate reveals neither cost nor
    margin. The reorder-draft write is asserted explicitly even though a prefix
    rule would also catch it.
    """
    assert required_permission("GET", "/api/purchase-comparison") == "purchases"
    assert required_permission("GET", "/api/purchase-comparison/products/1/analysis") == "purchases"
    assert required_permission("GET", "/api/purchase-comparison/reorder-suggestions") == "purchases"
    assert required_permission("GET", "/api/purchase-comparison/dashboard") == "purchases"
    assert required_permission("GET", "/api/products/1/supplier-prices") == "purchases"
    assert required_permission("POST", "/api/purchase-comparison/reorder-drafts") == "purchases"
    assert required_permission("PUT", "/api/purchase-comparison/products/1/reorder-policy") == "purchases"
    assert required_permission("POST", "/api/purchase-orders") == "purchases"
    # FX reads stay on the baseline read permission; FX writes stay purchasing.
    assert required_permission("GET", "/api/exchange-rates") == "read"
    assert required_permission("PUT", "/api/exchange-rates/override") == "purchases"


def test_purchase_engine_bc_rbac_http(tmp_path: Path) -> None:
    """Purchasing access is enforced by middleware, not only route-map metadata."""
    _run(tmp_path, "engine-bc-rbac", LOGIN + r'''
with TestClient(app) as client:
    admin = session(client, 'RbacAdmin123!')
    supplier = client.post('/api/suppliers', headers=admin, json={'name':'RBAC Tedarikçi'}).json()['id']

    products = {}
    for role in ('depo', 'muhasebe'):
        pid = client.post('/api/products', headers=admin, json={
            'name':'RBAC '+role,'purchase_price':'10','sale_price':'20','stock':'0'}).json()['id']
        products[role] = pid
        assert client.put(f'/api/purchase-comparison/products/{pid}/reorder-policy',
                          headers=admin, json={'reorder_point':'5','target_stock':'10'}).status_code == 200
        assert client.post('/api/supplier-prices', headers=admin, json={
            'supplier_id':supplier,'product_id':pid,'price':'10'}).status_code == 201

    def role_headers(role):
        username = 'rbac_' + role
        initial = 'RoleInitial123!'
        created = client.post('/api/users', headers=admin, json={
            'username':username,'display_name':username,'password':initial,'role':role})
        assert created.status_code == 201, created.text
        login = client.post('/api/auth/login', json={'username':username,'password':initial})
        assert login.status_code == 200, login.text
        body = login.json()
        headers = {'Authorization':'Bearer '+body['access_token'],
                   'X-Company-ID':str(body['companies'][0]['id'])}
        changed = client.post('/api/auth/change-password', headers=headers, json={
            'current_password':initial,'new_password':'RoleRotated123!'})
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
        return headers

    for role in ('satis', 'rapor'):
        headers = role_headers(role)
        assert client.get('/api/purchase-comparison', headers=headers).status_code == 403
        assert client.get(f"/api/products/{products['depo']}/supplier-prices",
                          headers=headers).status_code == 403
        denied = client.post('/api/purchase-comparison/reorder-drafts', headers=headers,
                             json={'lines':[{'product_id':products['depo'],
                                             'supplier_id':supplier}]})
        assert denied.status_code == 403, denied.text

    for role in ('depo', 'muhasebe'):
        headers = role_headers(role)
        assert client.get('/api/purchase-comparison', headers=headers).status_code == 200
        assert client.get(f"/api/products/{products[role]}/supplier-prices",
                          headers=headers).status_code == 200
        allowed = client.post('/api/purchase-comparison/reorder-drafts', headers=headers,
                              json={'lines':[{'product_id':products[role],
                                              'supplier_id':supplier}]})
        assert allowed.status_code == 201, allowed.text

    print('ENGINE_BC_OK')
''')


def test_engine_b_quantity_tier_ranking_and_margin(tmp_path: Path) -> None:
    """A ladder at the evaluated quantity can flip the cheapest supplier."""
    _run(tmp_path, "engine-b", LOGIN + r'''
with TestClient(app) as client:
    h = session(client, 'EngineBravo123!')
    sup = lambda name: client.post('/api/suppliers', headers=h, json={'name':name}).json()['id']
    a, b, c = sup('A'), sup('B'), sup('C')
    product = client.post('/api/products', headers=h, json={
        'name':'Yag Filtresi','purchase_price':'10','sale_price':'150','vat_rate':20}).json()['id']

    # A: 100 TRY list, MOQ 10, fast (5 days), ladder 50+ -> 10%, 100+ -> 20%.
    assert client.post('/api/supplier-prices', headers=h, json={
        'supplier_id':a,'product_id':product,'price':'100','currency':'TRY','moq':'10',
        'lead_time_days':5,
        'tiers':[{'min_quantity':'50','discount_percent':'10'},
                 {'min_quantity':'100','discount_percent':'20'}]}).status_code == 201
    # B: 95 TRY flat, no ladder, very slow (60 days).
    assert client.post('/api/supplier-prices', headers=h, json={
        'supplier_id':b,'product_id':product,'price':'95','currency':'TRY','moq':'10',
        'lead_time_days':60}).status_code == 201
    # C: 120 TRY NET of VAT (price_includes_vat false), medium lead time.
    assert client.post('/api/supplier-prices', headers=h, json={
        'supplier_id':c,'product_id':product,'price':'120','currency':'TRY',
        'lead_time_days':20,'price_includes_vat':False}).status_code == 201
    stored = client.get(f'/api/products/{product}/supplier-prices', headers=h).json()['items']
    c_price = next(row for row in stored if row['supplier_id'] == c)
    updated = client.put(f"/api/supplier-prices/{c_price['manual_price_id']}", headers=h,
                         json={'note':'KDV hariç teklif'})
    assert updated.status_code == 200, updated.text
    assert updated.json()['price_includes_vat'] is False

    def analysis(**params):
        resp = client.get(f'/api/purchase-comparison/products/{product}/analysis',
                          headers=h, params=params)
        assert resp.status_code == 200, resp.text
        return resp.json()

    # q=10: below every rung, so A pays list price and B (95) is cheapest.
    small = analysis(quantity='10')
    rows = {r['supplier_id']: r for r in small['items']}
    assert Decimal(rows[a]['catalog_unit_cost_try']) == Decimal('100')
    assert rows[a]['applied_discount_percent'] is None
    assert small['recommended_supplier_id'] == b

    # q=60: A's first rung (10%) lands -> 90 TRY, cheaper than B's 95. The ladder,
    # not the list price, decides. Hand-computed to the kurus.
    mid = analysis(quantity='60')
    rows = {r['supplier_id']: r for r in mid['items']}
    assert Decimal(rows[a]['applied_discount_percent']) == Decimal('10')
    assert Decimal(rows[a]['applied_tier_min_quantity']) == Decimal('50')
    assert Decimal(rows[a]['catalog_unit_cost_try']) == Decimal('90')
    assert Decimal(rows[a]['catalog_cost_try_total']) == Decimal('5400')   # 90 * 60
    assert mid['recommended_supplier_id'] == a
    # The record's flat discount field keeps its Increment-B meaning (unset here);
    # only applied_discount_percent moves with the quantity.
    assert rows[a]['discount_percent'] is None

    # q=100: the second rung (20%) -> 80 TRY.
    big = analysis(quantity='100')
    rows = {r['supplier_id']: r for r in big['items']}
    assert Decimal(rows[a]['applied_discount_percent']) == Decimal('20')
    assert Decimal(rows[a]['catalog_unit_cost_try']) == Decimal('80')
    assert big['recommended_supplier_id'] == a

    # Margin is VAT-exclusive on BOTH sides and honours price_includes_vat.
    # sale 150 incl 20% VAT -> 125 net.
    #   A at q=100: 80 incl VAT -> 66.6667 net -> (125-66.6667)/125 = 46.6667%
    #   C: 120 is already NET -> (125-120)/125 = 4%
    assert Decimal(rows[a]['margin_percent']) == Decimal('46.6667')
    assert rows[c]['price_includes_vat'] is False
    assert Decimal(rows[c]['margin_percent']) == Decimal('4')

    # A lead-time limit EXCLUDES loudly instead of silently dropping, and the
    # recommendation moves to the fastest survivor rather than pointing at an
    # excluded supplier.
    limited = analysis(quantity='10', max_lead_days=30)
    assert {row['supplier_id'] for row in limited['items']} == {a, c}
    assert [row['supplier_id'] for row in limited['excluded']] == [b]
    assert limited['excluded'][0]['reason'] == 'lead_time_exceeds_limit'
    assert limited['recommended_supplier_id'] == a

    print('ENGINE_BC_OK')
''')


def test_engine_b_one_click_po_keeps_quantity_and_stays_stock_neutral(tmp_path: Path) -> None:
    """An explicit quantity is never silently grown; the draft moves no stock."""
    _run(tmp_path, "engine-b-po", LOGIN + r'''
def stock_of(client, h, pid):
    return Decimal(str(client.get(f'/api/products/{pid}', headers=h).json()['product']['stock']))

with TestClient(app) as client:
    h = session(client, 'OneClick123!')
    supplier = client.post('/api/suppliers', headers=h, json={'name':'A'}).json()['id']
    product = client.post('/api/products', headers=h, json={
        'name':'Kayis','purchase_price':'10','sale_price':'60','vat_rate':20,'stock':'7'}).json()['id']
    assert client.post('/api/supplier-prices', headers=h, json={
        'supplier_id':supplier,'product_id':product,'price':'50','currency':'TRY','moq':'25'}).status_code == 201

    before = stock_of(client, h, product)

    # Explicit quantity below the MOQ is honoured verbatim and the shortfall is
    # reported. Silently ordering 25 units because the supplier prefers it would
    # be the system spending the buyer's money for them.
    resp = client.post('/api/purchase-orders', headers=h, json={
        'product_id':product,'supplier_id':supplier,'quantity':'4'})
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert Decimal(created['quantity']) == Decimal('4')
    assert Decimal(created['moq']) == Decimal('25')
    assert created['moq_satisfied'] is False
    assert created['status'] == 'draft'

    # Omitting the quantity keeps the long-standing MOQ default.
    default_qty = client.post('/api/purchase-orders', headers=h, json={
        'product_id':product,'supplier_id':supplier}).json()
    assert Decimal(default_qty['quantity']) == Decimal('25')

    # Neither draft touched stock (#143 draft-stock guard).
    assert stock_of(client, h, product) == before

    # No request shape can make this endpoint leave draft.
    for status in ('approved', 'completed', 'pending'):
        rejected = client.post('/api/purchase-orders', headers=h, json={
            'product_id':product,'supplier_id':supplier,'quantity':'4','status':status})
        assert rejected.status_code == 422, (status, rejected.text)

    print('ENGINE_BC_OK')
''')


def test_engine_c_reorder_uses_net_demand_and_open_orders(tmp_path: Path) -> None:
    """Reorder point derives from NET demand; open drafts suppress re-ordering."""
    _run(tmp_path, "engine-c-reorder", LOGIN + r'''
from datetime import date
with TestClient(app) as client:
    h = session(client, 'ReorderNet123!')
    supplier = client.post('/api/suppliers', headers=h, json={'name':'A'}).json()['id']
    customer = client.post('/api/customers', headers=h, json={'name':'Musteri'}).json()['id']
    product = client.post('/api/products', headers=h, json={
        'name':'Rulman','purchase_price':'10','sale_price':'60','vat_rate':20,'stock':'400'}).json()['id']
    assert client.post('/api/supplier-prices', headers=h, json={
        'supplier_id':supplier,'product_id':product,'price':'50','currency':'TRY',
        'moq':'10','lead_time_days':10}).status_code == 201

    today = date.today().isoformat()
    # 180 sold, 90 returned -> net 90 over the 90-day window -> 1/day.
    sale = client.post('/api/orders', headers=h, json={
        'entity_id':customer,'transaction_date':today,
        'items':[{'product_id':product,'quantity':'180','unit_price':'60','vat_rate':20}]})
    assert sale.status_code == 201, sale.text
    sale_id = sale.json()['id']
    ret = client.post('/api/workflow/sale_return', headers=h, json={
        'entity_id':customer,'document_date':today,'status':'completed',
        'source_type':'order','source_id':sale_id,
        'items':[{'product_id':product,'quantity':'90','unit_price':'60','vat_rate':20}]})
    assert ret.status_code in (200, 201), ret.text

    def set_stock(value):
        resp = client.post(f'/api/products/{product}/stock', headers=h, json={
            'mode':'set','quantity':value,'movement_date':today})
        assert resp.status_code in (200, 201), resp.text

    def suggestions():
        resp = client.get('/api/purchase-comparison/reorder-suggestions', headers=h)
        assert resp.status_code == 200, resp.text
        return {row['product_id']: row for row in resp.json()['items']}

    # Above the reorder point: no suggestion at all.
    set_stock('15')
    assert product not in suggestions()

    # At/below it: the product surfaces with the derived policy.
    set_stock('8')
    row = suggestions()[product]
    # Derived reorder point = avg_daily * lead_time + minimum_stock (safety)
    #                       = (180-90)/90 * 10 + 0 = 10.
    assert row['reorder_point_source'] == '90-day-derived'
    assert Decimal(row['avg_daily_demand']) == Decimal('1')
    assert Decimal(row['demand_sold']) == Decimal('180')
    assert Decimal(row['demand_returned']) == Decimal('90')
    assert Decimal(row['reorder_point']) == Decimal('10')
    # Without the return subtraction demand would read 2/day and the reorder
    # point would be 20 — the whole point of netting returns off.
    assert Decimal(row['target_stock']) == Decimal('20')      # rp + one lead cycle
    assert row['on_order_basis'] == 'open_document_lines'
    assert Decimal(row['on_hand']) == Decimal('8')
    assert Decimal(row['on_order']) == Decimal('0')
    assert Decimal(row['deficit']) == Decimal('12')           # target 20 - available 8
    assert Decimal(row['suggested_quantity']) == Decimal('20')  # ceil(12/10)*10
    assert row['best_supplier']['supplier_id'] == supplier

    # An explicit override wins over the derived value.
    policy = client.put(f'/api/purchase-comparison/products/{product}/reorder-policy',
                        headers=h, json={'reorder_point':'500','target_stock':'600'})
    assert policy.status_code == 200, policy.text
    row = suggestions()[product]
    assert row['reorder_point_source'] == 'manual'
    assert Decimal(row['reorder_point']) == Decimal('500')
    assert Decimal(row['target_stock']) == Decimal('600')

    # 0 is a REAL override, not "unset": stock 8 would trigger the derived point
    # of 10, but an explicit 0 means "do not reorder until the shelf is empty".
    assert client.put(f'/api/purchase-comparison/products/{product}/reorder-policy',
                      headers=h, json={'reorder_point':'0'}).status_code == 200
    assert product not in suggestions()

    # Clearing back to NULL restores the derived point — NULL != 0.
    cleared = client.put(f'/api/purchase-comparison/products/{product}/reorder-policy',
                         headers=h, json={'reorder_point':None,'target_stock':None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()['reorder_point'] is None
    assert suggestions()[product]['reorder_point_source'] == '90-day-derived'

    # A DRAFT purchase counts as on order and closes the gap, so a second run
    # does not order the same goods twice — while leaving real stock untouched.
    draft = client.post('/api/purchase-orders', headers=h, json={
        'product_id':product,'supplier_id':supplier,'quantity':'20'})
    assert draft.status_code == 201, draft.text
    assert Decimal(str(client.get(f'/api/products/{product}', headers=h).json()['product']['stock'])) == Decimal('8')
    assert product not in suggestions()

    print('ENGINE_BC_OK')
''')


def test_engine_c_grouped_drafts_are_idempotent_and_draft_only(tmp_path: Path) -> None:
    """One draft per supplier, MOQ reported honestly, replay-safe, never sent."""
    _run(tmp_path, "engine-c-drafts", LOGIN + r'''
from datetime import date
with TestClient(app) as client:
    h = session(client, 'GroupDraft123!')
    sup = lambda name: client.post('/api/suppliers', headers=h, json={'name':name}).json()['id']
    a, b = sup('A'), sup('B')
    def product(name, stock):
        pid = client.post('/api/products', headers=h, json={
            'name':name,'purchase_price':'10','sale_price':'60','vat_rate':20,
            'stock':stock}).json()['id']
        assert client.put(f'/api/purchase-comparison/products/{pid}/reorder-policy',
                          headers=h, json={'reorder_point':'50','target_stock':'60'}).status_code == 200
        return pid
    p1, p2, p3 = product('P1','1'), product('P2','2'), product('P3','3')

    # p1 and p2 from supplier A (MOQ 7), p3 from supplier B (no MOQ).
    for pid in (p1, p2):
        assert client.post('/api/supplier-prices', headers=h, json={
            'supplier_id':a,'product_id':pid,'price':'50','currency':'TRY',
            'moq':'7','lead_time_days':5}).status_code == 201
    assert client.post('/api/supplier-prices', headers=h, json={
        'supplier_id':b,'product_id':p3,'price':'40','currency':'TRY',
        'lead_time_days':5}).status_code == 201

    suggestions = client.get('/api/purchase-comparison/reorder-suggestions', headers=h).json()
    assert {row['product_id'] for row in suggestions['items']} == {p1, p2, p3}

    body = {'lines':[{'product_id':p1,'supplier_id':a,'quantity':'12'},
                     {'product_id':p2,'supplier_id':a},
                     {'product_id':p3,'supplier_id':b,'quantity':'5'}]}
    created = client.post('/api/purchase-comparison/reorder-drafts', headers=h, json=body)
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload['replayed'] is False and payload['skipped'] == []

    # One draft per supplier, carrying that supplier's lines.
    assert len(payload['purchases']) == 2
    by_supplier = {row['supplier_id']: row for row in payload['purchases']}
    assert {row['product_id'] for row in by_supplier[a]['lines']} == {p1, p2}
    assert [row['product_id'] for row in by_supplier[b]['lines']] == [p3]
    assert all(row['status'] == 'draft' for row in payload['purchases'])

    # MOQ rounding is reported, never hidden: 12 requested -> 14 created (2x7).
    line = next(row for row in by_supplier[a]['lines'] if row['product_id'] == p1)
    assert Decimal(line['requested_quantity']) == Decimal('12')
    assert Decimal(line['created_quantity']) == Decimal('14')
    # No MOQ -> no rounding and no division by zero.
    line3 = by_supplier[b]['lines'][0]
    assert Decimal(line3['requested_quantity']) == Decimal('5')
    assert Decimal(line3['created_quantity']) == Decimal('5')

    # Drafts are stock-neutral.
    assert Decimal(str(client.get(f'/api/products/{p1}', headers=h).json()['product']['stock'])) == Decimal('1')

    # Replaying the same request with the same key returns the SAME drafts.
    key = {'Idempotency-Key':'reorder-001'}
    first = client.post('/api/purchase-comparison/reorder-drafts', headers={**h, **key}, json=body)
    assert first.status_code == 201, first.text
    first_ids = sorted(row['purchase_id'] for row in first.json()['purchases'])
    again = client.post('/api/purchase-comparison/reorder-drafts', headers={**h, **key}, json=body)
    assert again.status_code == 201, again.text
    assert again.json()['replayed'] is True
    assert sorted(row['purchase_id'] for row in again.json()['purchases']) == first_ids

    # Same key, different body -> refused rather than quietly reusing the drafts.
    conflict = client.post('/api/purchase-comparison/reorder-drafts', headers={**h, **key},
                           json={'lines':[{'product_id':p3,'supplier_id':b,'quantity':'9'}]})
    assert conflict.status_code == 409, conflict.text

    # Re-verification under the lock. p4 starts empty and one draft takes it all
    # the way to target, so approving the same (now stale) suggestion again must
    # skip it loudly instead of ordering a second time.
    p4 = product('P4', '0')
    assert client.post('/api/supplier-prices', headers=h, json={
        'supplier_id':b,'product_id':p4,'price':'40','currency':'TRY',
        'lead_time_days':5}).status_code == 201
    covering = {'lines':[{'product_id':p4,'supplier_id':b}]}
    filled = client.post('/api/purchase-comparison/reorder-drafts', headers=h, json=covering)
    assert filled.status_code == 201, filled.text
    assert Decimal(filled.json()['purchases'][0]['lines'][0]['created_quantity']) == Decimal('60')
    stale = client.post('/api/purchase-comparison/reorder-drafts', headers=h, json=covering)
    assert stale.status_code == 201, stale.text
    assert stale.json()['purchases'] == []
    assert {row['reason'] for row in stale.json()['skipped']} == {'no_longer_below_reorder_point'}

    # The request model has no status field at all: nothing can be approved here.
    for status in ('approved', 'completed', 'pending'):
        rejected = client.post('/api/purchase-comparison/reorder-drafts', headers=h,
                               json={'lines':[{'product_id':p1,'supplier_id':a}],
                                     'status':status})
        assert rejected.status_code == 422, (status, rejected.text)
    rows = client.get('/api/purchases', headers=h).json()
    assert all(row['status'] == 'draft' for row in rows), rows

    print('ENGINE_BC_OK')
''')


def test_engine_bc_tenant_isolation(tmp_path: Path) -> None:
    """Company B never sees company A's tiers, margins or reorder suggestions."""
    _run(tmp_path, "engine-bc-tenant", LOGIN + r'''
with TestClient(app) as client:
    h = session(client, 'TenantBC123!')
    supplier = client.post('/api/suppliers', headers=h, json={'name':'A'}).json()['id']
    product = client.post('/api/products', headers=h, json={
        'name':'Conta','purchase_price':'10','sale_price':'60','vat_rate':20,
        'stock':'0'}).json()['id']
    assert client.put(f'/api/purchase-comparison/products/{product}/reorder-policy',
                      headers=h, json={'reorder_point':'25','target_stock':'30'}).status_code == 200
    assert client.post('/api/supplier-prices', headers=h, json={
        'supplier_id':supplier,'product_id':product,'price':'50','currency':'TRY','moq':'5',
        'tiers':[{'min_quantity':'10','discount_percent':'15'}]}).status_code == 201
    assert len(client.get('/api/purchase-comparison/reorder-suggestions', headers=h).json()['items']) == 1

    other = client.post('/api/companies', headers=h, json={'name':'Firma B'}).json()['id']
    hb = {**h, 'X-Company-ID':str(other)}

    assert client.get(f'/api/purchase-comparison/products/{product}/analysis',
                      headers=hb, params={'quantity':'10'}).status_code == 404
    assert client.get('/api/purchase-comparison/reorder-suggestions', headers=hb).json()['items'] == []
    assert client.get('/api/purchase-comparison/dashboard', headers=hb).json()['below_reorder_point_count'] == 0
    assert client.put(f'/api/purchase-comparison/products/{product}/reorder-policy',
                      headers=hb, json={'reorder_point':'5'}).status_code == 404
    assert client.post('/api/purchase-comparison/reorder-drafts', headers=hb,
                       json={'lines':[{'product_id':product,'supplier_id':supplier}]}).status_code == 404

    # The cross-tenant override attempt above must not have changed anything.
    row = next(r for r in client.get('/api/purchase-comparison/reorder-suggestions',
                                     headers=h).json()['items'] if r['product_id'] == product)
    assert Decimal(row['reorder_point']) == Decimal('25')

    print('ENGINE_BC_OK')
''')
