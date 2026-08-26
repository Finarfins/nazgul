"""Purchase dashboard aggregates — spend analytics and the supplier scorecard.

These endpoints only READ. What the tests pin is therefore not a workflow but
the honesty of the numbers a chart is drawn from:

* draft and cancelled purchases are excluded — the reorder engine CREATES draft
  purchases, so counting them would let the dashboard inflate itself with orders
  nobody approved;
* two bases are reported and never mixed: header ``final_total`` (after the
  document-level discount) drives the totals/trend/supplier breakdown, while
  per-line ``line_total`` drives the product/category breakdown;
* a truncated top-N still reconciles to the reported total through the
  ``other_*_total_try`` remainder;
* the scorecard only calls a supplier best/worst where at least two suppliers
  actually quote the product;
* both endpoints are tenant-scoped and gated on the ``purchases`` permission,
  and both answer an empty tenant with zeros rather than an error.

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
    assert "DASHBOARD_OK" in completed.stdout, completed.stdout + completed.stderr


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

def ok(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()
'''


def test_purchase_dashboard_rbac_map() -> None:
    """Spend history discloses supplier cost, so it is purchasing data.

    Both aggregates sit under ``/api/purchase-comparison`` and therefore inherit
    the rule the rest of the engine already uses, instead of falling through to
    the generic GET -> ``read`` baseline.
    """
    assert required_permission("GET", "/api/purchase-comparison/spend-analytics") == "purchases"
    assert required_permission("GET", "/api/purchase-comparison/supplier-scorecard") == "purchases"


def test_dashboard_queries_are_visible_to_the_tenant_scoping_guard() -> None:
    """Dashboard SQL must remain inside the exact dynamic-SQL review gate."""
    from tests.test_tenant_scoping_guard import (
        DYNAMIC_SQL_FILE_ALLOWLIST,
        _iter_dynamic_text_calls,
        _iter_text_sql,
    )

    source = "backend/app/routers/supplier_prices.py"
    expected_count, expected_fingerprint, reason = DYNAMIC_SQL_FILE_ALLOWLIST[source]
    seen = [
        (argument, fingerprint)
        for rel, _lineno, argument, fingerprint in _iter_dynamic_text_calls()
        if rel == source
    ]

    assert reason.strip()
    assert len(seen) == expected_count
    assert {fingerprint for _argument, fingerprint in seen} == {expected_fingerprint}
    arguments = [argument for argument, _fingerprint in seen]
    literal_sql = [
        sql
        for rel, _lineno, sql in _iter_text_sql()
        if rel == source
    ]
    # Scorecard candidate set remains directly visible to the literal guard.
    assert any(
        sql.startswith("SELECT COUNT(*) FROM products p WHERE p.company_id=:cid")
        for sql in literal_sql
    ), "scorecard sayım sorgusu guard'a görünmüyor"
    assert any(
        sql.startswith("SELECT p.id,p.name FROM products p WHERE p.company_id=:cid")
        for sql in literal_sql
    ), "scorecard aday listesi sorgusu guard'a görünmüyor"
    # Spend analytics — header, monthly, supplier, line base, product, category.
    spend_prefixes = (
        "f'SELECT COALESCE(SUM(pu.final_total),0) total,COUNT(*) cnt",
        "f'SELECT {month_expr} AS month",
        "f'SELECT s.id supplier_id",
        "f'SELECT COALESCE(SUM(pi.line_total),0) FROM purchase_items pi",
        "f'SELECT pi.product_name",
        "f'SELECT p.category",
    )
    for prefix in spend_prefixes:
        matches = [argument for argument in arguments if argument.startswith(prefix)]
        assert len(matches) == 1, f"spend sorgusu guard'a görünmüyor: {prefix}"
        assert "WHERE pu.company_id=:cid" in matches[0]


def test_spend_analytics_totals_bases_and_draft_exclusion(tmp_path: Path) -> None:
    """Hand-computed totals, the two money bases, and the draft exclusion."""
    _run(tmp_path, "dashboard-spend", LOGIN + r'''
with TestClient(app) as client:
    h = session(client, 'SpendDash123!')
    supplier = lambda name: ok(client.post('/api/suppliers', headers=h, json={'name':name}), 201)['id']
    a, b = supplier('Alfa Tedarik'), supplier('Beta Tedarik')
    def product(name, category=None):
        body = {'name':name,'purchase_price':'1','sale_price':'10','vat_rate':20}
        if category is not None:
            body['category'] = category
        return ok(client.post('/api/products', headers=h, json=body), 201)['id']
    yag, filtre, kayis = product('Motor Yagi','Yag'), product('Hidrolik Filtre','Filtre'), product('Kayis')

    def purchase(entity, when, items, **extra):
        body = {'entity_id':entity,'transaction_date':when,'items':items}
        body.update(extra)
        return ok(client.post('/api/purchases', headers=h, json=body), 201)

    # unit_price is VAT-INCLUSIVE, so line_total == quantity * unit_price here.
    purchase(a, '2026-05-10', [{'product_id':yag,'quantity':'10','unit_price':'100','vat_rate':20}])
    purchase(b, '2026-05-20', [{'product_id':filtre,'quantity':'5','unit_price':'200','vat_rate':20}])
    # 300 + 200 = 500 of lines, less a 10% document discount -> final_total 450.
    discounted = purchase(a, '2026-06-05', [
        {'product_id':yag,'quantity':'2','unit_price':'150','vat_rate':20},
        {'product_id':kayis,'quantity':'1','unit_price':'200','vat_rate':20}],
        discount_percent='10')
    assert Decimal(str(discounted['final_total'])) == Decimal('450')
    # A draft is exactly what the reorder engine produces; it must not be spend.
    purchase(a, '2026-06-15', [{'product_id':yag,'quantity':'100','unit_price':'100','vat_rate':20}],
             status='draft')

    data = ok(client.get('/api/purchase-comparison/spend-analytics', headers=h))
    assert data['excluded_statuses'] == ['draft','cancelled']
    assert data['vat_included'] is True

    totals = data['totals']
    # Header base: 1000 + 1000 + 450. The 10000 draft is absent.
    assert Decimal(totals['total_try']) == Decimal('2450')
    assert totals['purchase_count'] == 3
    assert totals['supplier_count'] == 2
    assert Decimal(totals['average_purchase_try']) == Decimal('816.67')
    # Stored lines include the document discount and reconcile to headers.
    assert Decimal(totals['line_total_try']) == Decimal('2450')

    assert data['monthly'] == [
        {'month':'2026-05','total_try':'2000.00','purchase_count':2},
        {'month':'2026-06','total_try':'450.00','purchase_count':1},
    ]

    suppliers = {row['supplier_id']: row for row in data['by_supplier']}
    assert Decimal(suppliers[a]['total_try']) == Decimal('1450')
    assert suppliers[a]['purchase_count'] == 2
    assert suppliers[a]['last_purchase_date'] == '2026-06-05'
    assert Decimal(suppliers[b]['total_try']) == Decimal('1000')
    # Shares are taken against the header base, not the line base.
    assert Decimal(suppliers[b]['share_percent']) == Decimal('40.8163')
    assert Decimal(data['other_supplier_total_try']) == Decimal('0')
    # "Is there a tail?" is decided server-side on the Decimal, so the client
    # never has to parse money to compare it.
    assert data['has_other_suppliers'] is False
    assert data['has_other_products'] is False
    assert data['has_other_categories'] is False

    products = {row['product_name']: row for row in data['by_product']}
    assert Decimal(products['Motor Yagi']['total_try']) == Decimal('1270')
    assert Decimal(products['Motor Yagi']['quantity']) == Decimal('12')
    assert products['Motor Yagi']['product_id'] == yag
    assert Decimal(products['Motor Yagi']['share_percent']) == Decimal('51.8367')
    assert Decimal(products['Kayis']['total_try']) == Decimal('180')

    categories = {row['category']: row for row in data['by_category']}
    assert Decimal(categories['Yag']['total_try']) == Decimal('1270')
    assert Decimal(categories['Filtre']['total_try']) == Decimal('1000')
    # A product with no category is an explicit null bucket, not a dropped row.
    assert Decimal(categories[None]['total_try']) == Decimal('180')

    # The date window filters on the same normalised date the buckets use.
    may = ok(client.get('/api/purchase-comparison/spend-analytics', headers=h,
                        params={'date_from':'2026-05-01','date_to':'2026-05-31'}))
    assert Decimal(may['totals']['total_try']) == Decimal('2000')
    assert [row['month'] for row in may['monthly']] == ['2026-05']

    # top=1 keeps the reconciliation honest: what is cut off is reported.
    trimmed = ok(client.get('/api/purchase-comparison/spend-analytics', headers=h,
                            params={'top':1}))
    assert len(trimmed['by_supplier']) == 1
    assert Decimal(trimmed['by_supplier'][0]['total_try']) == Decimal('1450')
    assert Decimal(trimmed['other_supplier_total_try']) == Decimal('1000')
    assert (Decimal(trimmed['by_supplier'][0]['total_try'])
            + Decimal(trimmed['other_supplier_total_try'])
            == Decimal(trimmed['totals']['total_try']))
    assert Decimal(trimmed['other_product_total_try']) == Decimal('1180')
    assert trimmed['has_other_suppliers'] is True
    assert trimmed['has_other_products'] is True

    print('DASHBOARD_OK')
''')


def test_spend_analytics_empty_tenant_returns_zeros(tmp_path: Path) -> None:
    """A tenant with no purchases gets zeros and empty lists, never an error."""
    _run(tmp_path, "dashboard-empty", LOGIN + r'''
with TestClient(app) as client:
    h = session(client, 'EmptyDash123!')
    data = ok(client.get('/api/purchase-comparison/spend-analytics', headers=h))
    assert Decimal(data['totals']['total_try']) == Decimal('0')
    assert Decimal(data['totals']['line_total_try']) == Decimal('0')
    assert data['totals']['purchase_count'] == 0
    assert data['totals']['supplier_count'] == 0
    # No documents means no meaningful average — null, not a fabricated zero.
    assert data['totals']['average_purchase_try'] is None
    assert data['monthly'] == []
    assert data['by_supplier'] == [] and data['by_product'] == [] and data['by_category'] == []
    assert data['has_other_suppliers'] is False
    assert data['has_other_products'] is False
    assert data['has_other_categories'] is False

    card = ok(client.get('/api/purchase-comparison/supplier-scorecard', headers=h))
    assert card['evaluated_product_count'] == 0
    assert card['candidate_product_count'] == 0
    assert card['truncated'] is False
    assert card['suppliers'] == []

    print('DASHBOARD_OK')
''')


def test_dashboard_aggregates_are_tenant_scoped(tmp_path: Path) -> None:
    """Another company's purchases never leak into either aggregate."""
    _run(tmp_path, "dashboard-tenant", LOGIN + r'''
with TestClient(app) as client:
    HA = session(client, 'TenantDash123!')
    company_b = ok(client.post('/api/companies', headers=HA, json={'name':'Pano Tenant B'}), 201)['id']
    HB = {**HA, 'X-Company-ID':str(company_b)}

    def seed(headers, supplier_name, product_name, price):
        supplier = ok(client.post('/api/suppliers', headers=headers,
                                  json={'name':supplier_name}), 201)['id']
        product = ok(client.post('/api/products', headers=headers, json={
            'name':product_name,'purchase_price':'1','sale_price':'10','vat_rate':20}), 201)['id']
        ok(client.post('/api/supplier-prices', headers=headers, json={
            'supplier_id':supplier,'product_id':product,'price':price}), 201)
        ok(client.post('/api/purchases', headers=headers, json={
            'entity_id':supplier,'transaction_date':'2026-06-01',
            'items':[{'product_id':product,'quantity':'1','unit_price':price,'vat_rate':20}]}), 201)
        return supplier, product

    supplier_a, _ = seed(HA, 'A Tedarik', 'A Urun', '1000')
    supplier_b, _ = seed(HB, 'B Tedarik', 'B Urun', '7000')

    a_data = ok(client.get('/api/purchase-comparison/spend-analytics', headers=HA))
    assert Decimal(a_data['totals']['total_try']) == Decimal('1000')
    assert [row['supplier_id'] for row in a_data['by_supplier']] == [supplier_a]
    assert [row['product_name'] for row in a_data['by_product']] == ['A Urun']

    b_data = ok(client.get('/api/purchase-comparison/spend-analytics', headers=HB))
    assert Decimal(b_data['totals']['total_try']) == Decimal('7000')
    assert [row['supplier_id'] for row in b_data['by_supplier']] == [supplier_b]

    a_card = ok(client.get('/api/purchase-comparison/supplier-scorecard', headers=HA))
    assert a_card['candidate_product_count'] == 1
    assert [row['supplier_id'] for row in a_card['suppliers']] == [supplier_a]
    b_card = ok(client.get('/api/purchase-comparison/supplier-scorecard', headers=HB))
    assert [row['supplier_id'] for row in b_card['suppliers']] == [supplier_b]

    print('DASHBOARD_OK')
''')


def test_supplier_scorecard_counts_only_real_comparisons(tmp_path: Path) -> None:
    """Best/worst are only awarded where two suppliers actually compete."""
    _run(tmp_path, "dashboard-scorecard", LOGIN + r'''
with TestClient(app) as client:
    h = session(client, 'ScoreDash123!')
    supplier = lambda name: ok(client.post('/api/suppliers', headers=h, json={'name':name}), 201)['id']
    a, b, c = supplier('Ucuz'), supplier('Orta'), supplier('Pahali')
    def product(name):
        return ok(client.post('/api/products', headers=h, json={
            'name':name,'purchase_price':'1','sale_price':'500','vat_rate':20}), 201)['id']
    def price(supplier_id, product_id, value):
        ok(client.post('/api/supplier-prices', headers=h, json={
            'supplier_id':supplier_id,'product_id':product_id,'price':value}), 201)

    # Contested twice: A cheapest both times, C dearest both times.
    for name, prices in (('Contested 1', ('100','120','150')), ('Contested 2', ('90','95','130'))):
        pid = product(name)
        for supplier_id, value in zip((a, b, c), prices):
            price(supplier_id, pid, value)
    # Single source: B is neither the winner nor the loser of anything.
    price(b, product('Tek Kaynak'), '999')

    card = ok(client.get('/api/purchase-comparison/supplier-scorecard', headers=h))
    assert card['evaluated_product_count'] == 3
    assert card['candidate_product_count'] == 3
    assert card['truncated'] is False
    assert card['comparable_product_count'] == 2
    assert card['single_source_product_count'] == 1
    assert card['unpriced_product_count'] == 0

    rows = {row['supplier_id']: row for row in card['suppliers']}
    assert rows[a]['best_count'] == 2 and rows[a]['worst_count'] == 0
    assert rows[b]['best_count'] == 0 and rows[b]['worst_count'] == 0
    assert rows[b]['product_count'] == 3 and rows[b]['priced_product_count'] == 3
    assert rows[c]['best_count'] == 0 and rows[c]['worst_count'] == 2
    # Highest best_count first, so the UI can read the winner off the top row.
    assert card['suppliers'][0]['supplier_id'] == a

    # The scan is bounded and says so instead of implying full coverage.
    bounded = ok(client.get('/api/purchase-comparison/supplier-scorecard', headers=h,
                            params={'limit':1}))
    assert bounded['evaluated_product_count'] == 1
    assert bounded['candidate_product_count'] == 3
    assert bounded['truncated'] is True

    print('DASHBOARD_OK')
''')


def test_dashboard_aggregates_require_purchases_permission(tmp_path: Path) -> None:
    """Middleware, not only the route map, keeps cost data from read-only roles."""
    _run(tmp_path, "dashboard-rbac", LOGIN + r'''
with TestClient(app) as client:
    admin = session(client, 'RbacDash123!')

    def role_headers(role):
        username = 'dash_' + role
        initial = 'RoleInitial123!'
        ok(client.post('/api/users', headers=admin, json={
            'username':username,'display_name':username,'password':initial,'role':role}), 201)
        body = ok(client.post('/api/auth/login', json={'username':username,'password':initial}))
        headers = {'Authorization':'Bearer '+body['access_token'],
                   'X-Company-ID':str(body['companies'][0]['id'])}
        changed = ok(client.post('/api/auth/change-password', headers=headers, json={
            'current_password':initial,'new_password':'RoleRotated123!'}))
        headers['Authorization'] = 'Bearer ' + changed['access_token']
        return headers

    for role in ('satis', 'rapor'):
        headers = role_headers(role)
        assert client.get('/api/purchase-comparison/spend-analytics',
                          headers=headers).status_code == 403
        assert client.get('/api/purchase-comparison/supplier-scorecard',
                          headers=headers).status_code == 403

    for role in ('depo', 'muhasebe'):
        headers = role_headers(role)
        assert client.get('/api/purchase-comparison/spend-analytics',
                          headers=headers).status_code == 200
        assert client.get('/api/purchase-comparison/supplier-scorecard',
                          headers=headers).status_code == 200

    print('DASHBOARD_OK')
''')
