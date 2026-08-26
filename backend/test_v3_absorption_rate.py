"""V3 Absorption Rate KPI — Increment 1 tests (SQLite).

Covers ``GET /api/analytics/absorption-rate``:

- parts gross profit over both channels (counter ``order_items`` net-of-VAT
  revenue and ``work_order_parts`` discounted/VAT-excluded revenue) minus
  ``products.purchase_price`` COGS, in Decimal;
- service gross profit from billed labour on *completed* work orders, with the
  untracked labour cost reported as ``0.00`` and flagged, never invented;
- overhead sourced from ``income_expenses`` when present and overridable by the
  ``overhead`` query parameter, with the source always stated;
- the rate math, and the ``overhead <= 0`` guard returning ``null`` (no
  ZeroDivisionError);
- multi-tenant isolation and the RBAC mapping.

The PostgreSQL 16 twin lives in test_absorption_rate_postgresql.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import required_permission


BACKEND = Path(__file__).resolve().parent


def test_absorption_rate_rbac_map() -> None:
    # /api/analytics/* is a reporting read: the baseline ``read`` permission is
    # deliberately not enough (see auth.required_permission), so roles without
    # ``reports`` (e.g. ``depo``, ``satis``) cannot reach the KPI.
    assert required_permission("GET", "/api/analytics/absorption-rate") == "reports"


def test_absorption_rate_flow(tmp_path: Path) -> None:
    database = tmp_path / "absorption-rate.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE], env=env, capture_output=True, text=True, cwd=str(BACKEND)
    )
    assert "ABSORPTION_RATE_FLOW_OK" in completed.stdout, completed.stdout + completed.stderr


# Shared, DB-agnostic scenario. test_absorption_rate_postgresql.py runs this
# exact script against PostgreSQL 16 so the GROUP BY / NUMERIC aggregation and
# the timestamp-vs-ISO-date period filters are proven on both engines.
_SMOKE = r'''
from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app


def dec(value):
    return Decimal(str(value))


TODAY = date.today().isoformat()
# Sales and expenses are dated with local ISO days, but work_orders.completed_at
# is stamped in UTC by the status transition. Around local midnight those two
# clocks name different days (UTC+3 here), so a single-day window would drop the
# service side of the KPI depending on the hour the suite happens to run. The
# assertions below are about the aggregation, not about date-boundary handling,
# so the query window spans the neighbouring days; the company is freshly created
# and holds nothing else, which keeps every expected figure exact.
PERIOD_FROM = (date.today() - timedelta(days=1)).isoformat()
PERIOD_TO = (date.today() + timedelta(days=1)).isoformat()

with TestClient(app) as client:
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    uid = login['user']['id']
    headers = {'Authorization':'Bearer '+login['access_token'],
               'X-Company-ID':str(login['companies'][0]['id'])}
    changed = client.post('/api/auth/change-password', headers=headers, json={
        'current_password':'admin123','new_password':'Absorption123!'})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

    # Own company so the run is isolated on the shared PostgreSQL 16 CI database.
    company_a = client.post('/api/companies', headers=headers, json={'name':'Emilim Firma A'}).json()['id']
    headers['X-Company-ID'] = str(company_a)

    customer = client.post('/api/customers', headers=headers, json={'name':'Servis Musterisi'}).json()['id']
    machine = client.post('/api/machines', headers=headers, json={
        'customer_id':customer,'brand':'Sungur','model':'Emilim','serial_number':'ABS-M1'}).json()['id']
    warehouse = client.get('/api/warehouses', headers=headers).json()[0]['id']
    # purchase_price 40 is the only cost this ERP stores per product -> parts COGS.
    product = client.post('/api/products', headers=headers, json={
        'name':'Yedek Parca','purchase_price':'40','sale_price':'120','vat_rate':'20',
        'stock':'1000','unit':'Adet'}).json()['id']

    def rate_call(**params):
        params.setdefault('date_from', PERIOD_FROM)
        params.setdefault('date_to', PERIOD_TO)
        return client.get('/api/analytics/absorption-rate', headers=headers, params=params)

    # --- Empty period: no revenue, no overhead -> rate is undefined, not 0-div.
    empty = rate_call()
    assert empty.status_code == 200, empty.text
    empty_body = empty.json()
    assert dec(empty_body['total_gross_profit_upper_bound']) == Decimal('0')
    assert empty_body['absorption_rate_upper_bound'] is None
    assert empty_body['rate_note']
    assert empty_body['overhead']['source'] == 'income_expenses'
    assert empty_body['period'] == {'date_from':PERIOD_FROM,'date_to':PERIOD_TO}

    # --- Counter parts sale: 10 x 120 VAT-inclusive @20% -> 1000 net revenue,
    #     COGS 10 x 40 = 400 -> counter parts GP 600.
    sale = client.post('/api/orders', headers=headers, json={
        'entity_id':customer,'transaction_date':TODAY,'status':'completed',
        'payment_method':'cash','paid_amount':'1200',
        'items':[{'product_id':product,'quantity':'10','unit_price':'120','vat_rate':20}]})
    assert sale.status_code == 201, sale.text

    # A cancelled sale must never contribute revenue or COGS.
    cancelled = client.post('/api/orders', headers=headers, json={
        'entity_id':customer,'transaction_date':TODAY,'status':'cancelled',
        'payment_method':'cash','paid_amount':'0',
        'items':[{'product_id':product,'quantity':'7','unit_price':'120','vat_rate':20}]})
    assert cancelled.status_code == 201, cancelled.text

    # --- Service job: 2h x 150 = 300 billed labour; part 5 x 80 less 10% =
    #     360 net revenue, COGS 5 x 40 = 200 -> service-parts GP 160.
    work_order = client.post('/api/work-orders', headers=headers, json={
        'machine_id':machine,'customer_id':customer,'technician_id':uid,
        'actual_hours':'2','labor_rate':'150'}).json()['id']
    part = client.post(f'/api/work-orders/{work_order}/parts', headers=headers, json={
        'product_id':product,'warehouse_id':warehouse,'quantity':'5','unit_price':'80',
        'discount':'10','tax_rate':'20'})
    assert part.status_code == 201, part.text

    # An OPEN work order contributes nothing until it is completed.
    open_only = rate_call().json()
    assert dec(open_only['service']['labor_revenue']) == Decimal('0')
    assert open_only['service']['labor_cost_known'] is False
    assert dec(open_only['parts']['work_order_revenue']) == Decimal('0')
    assert dec(open_only['parts']['gross_profit']) == Decimal('600')

    for status in ('IN_PROGRESS','COMPLETED'):
        moved = client.patch(f'/api/work-orders/{work_order}/status', headers=headers,
                             json={'status':status})
        assert moved.status_code == 200, moved.text

    # --- Components, with overhead still untracked --------------------------
    body = rate_call().json()
    assert dec(body['parts']['counter_revenue']) == Decimal('1000')
    assert dec(body['parts']['work_order_revenue']) == Decimal('360')
    assert dec(body['parts']['revenue']) == Decimal('1360')
    assert dec(body['parts']['cogs']) == Decimal('600')
    assert dec(body['parts']['gross_profit']) == Decimal('760')
    assert body['parts']['cogs_source'] == 'products.purchase_price'
    assert dec(body['service']['labor_revenue']) == Decimal('300')
    # No technician cost rate exists in the schema: reported as 0 and flagged.
    assert dec(body['service']['labor_cost']) == Decimal('0')
    assert body['service']['labor_cost_source'] == 'not_tracked'
    assert dec(body['service']['gross_profit_upper_bound']) == Decimal('300')
    assert body['service']['work_order_count'] == 1
    assert dec(body['total_gross_profit_upper_bound']) == Decimal('1060')
    # Overhead is genuinely absent -> rate stays undefined (guarded, no 0-div).
    assert dec(body['overhead']['amount']) == Decimal('0')
    assert body['absorption_rate_upper_bound'] is None
    assert body['rate_note']

    # --- User-supplied overhead --------------------------------------------
    supplied = rate_call(overhead='2120').json()
    assert supplied['overhead']['source'] == 'user_supplied'
    assert dec(supplied['overhead']['amount']) == Decimal('2120')
    assert dec(supplied['absorption_rate_upper_bound']) == Decimal('50')       # 1060 / 2120
    # Labour cost is untracked, so even a fully-specified overhead yields a BOUND:
    assert supplied['is_upper_bound'] is True
    assert 'ÜST SINIR' in supplied['rate_note']

    # An explicit zero overhead is still guarded rather than dividing by zero.
    zero = rate_call(overhead='0').json()
    assert zero['absorption_rate_upper_bound'] is None and zero['rate_note']
    assert rate_call(overhead='-5').status_code == 400
    assert rate_call(overhead='abc').status_code == 400
    assert client.get('/api/analytics/absorption-rate', headers=headers,
                      params={'date_from':'2026-01-31','date_to':'2026-01-01'}).status_code == 400
    assert client.get('/api/analytics/absorption-rate', headers=headers,
                      params={'date_from':'31.01.2026'}).status_code == 400

    # --- Tracked overhead (income_expenses) ---------------------------------
    # No endpoint writes income_expenses yet, so seed it the way the dashboard
    # reads it: txn_type='expense' rows inside the period.
    with SessionLocal() as session:
        for amount, category in (('500.00','Kira'), ('560.00','Elektrik')):
            session.execute(text(
                "INSERT INTO income_expenses (txn_date,txn_type,category,amount,company_id) "
                "VALUES (:d,'expense',:c,:a,:cid)"),
                {'d':TODAY,'c':category,'a':amount,'cid':company_a})
        # Income rows and other companies' expenses must be ignored.
        session.execute(text(
            "INSERT INTO income_expenses (txn_date,txn_type,category,amount,company_id) "
            "VALUES (:d,'income','Faiz','900.00',:cid)"), {'d':TODAY,'cid':company_a})
        session.commit()

    tracked = rate_call().json()
    assert tracked['overhead']['source'] == 'income_expenses'
    assert dec(tracked['overhead']['amount']) == Decimal('1060')
    assert tracked['overhead']['entry_count'] == 2
    assert dec(tracked['absorption_rate_upper_bound']) == Decimal('100')        # 1060 / 1060
    # The query parameter still wins over the tracked figure.
    assert dec(rate_call(overhead='530').json()['absorption_rate_upper_bound']) == Decimal('200')

    # A period with no data at all leaves every component at zero.
    past = client.get('/api/analytics/absorption-rate', headers=headers,
                      params={'date_from':'2019-01-01','date_to':'2019-12-31'}).json()
    assert dec(past['total_gross_profit_upper_bound']) == Decimal('0')
    assert dec(past['overhead']['amount']) == Decimal('0')

    # --- Document-level discount regression (own company, exact figures) ----
    # order_items.line_subtotal carries only the per-line discount; a document
    # discount is applied afterwards and persisted on orders.subtotal. Summing
    # the lines would ignore it and overstate revenue, gross profit and the rate.
    # 10 x 120 VAT-inclusive @20% = 1200 gross -> 25% document discount -> 900
    # final, of which 750.00 is the VAT-excluded subtotal. COGS is unaffected by
    # any discount: 10 x 40 = 400. Parts GP must be 350.00, not the 600.00 a
    # line-sum would produce.
    company_d = client.post('/api/companies', headers=headers, json={'name':'Emilim Firma D'}).json()['id']
    headers_d = {**headers, 'X-Company-ID':str(company_d)}
    customer_d = client.post('/api/customers', headers=headers_d, json={'name':'Indirimli Musteri'}).json()['id']
    product_d = client.post('/api/products', headers=headers_d, json={
        'name':'Indirimli Parca','purchase_price':'40','sale_price':'120','vat_rate':'20',
        'stock':'1000','unit':'Adet'}).json()['id']
    discounted = client.post('/api/orders', headers=headers_d, json={
        'entity_id':customer_d,'transaction_date':TODAY,'status':'completed',
        'payment_method':'cash','paid_amount':'900','discount_percent':'25',
        'items':[{'product_id':product_d,'quantity':'10','unit_price':'120','vat_rate':20}]})
    assert discounted.status_code == 201, discounted.text
    # Sanity-check the write path itself so the expectation is anchored in stored data.
    stored = client.get(f"/api/orders/{discounted.json()['id']}", headers=headers_d).json()['document']
    assert dec(stored['subtotal']) == Decimal('750'), stored
    assert dec(stored['final_total']) == Decimal('900'), stored

    discounted_body = client.get('/api/analytics/absorption-rate', headers=headers_d,
                                 params={'date_from':PERIOD_FROM,'date_to':PERIOD_TO}).json()
    assert dec(discounted_body['parts']['counter_revenue']) == Decimal('750')
    assert dec(discounted_body['parts']['cogs']) == Decimal('400')
    assert dec(discounted_body['parts']['gross_profit']) == Decimal('350')
    assert dec(discounted_body['total_gross_profit_upper_bound']) == Decimal('350')
    # 350 / 1000 -> %35 upper bound; a line-sum revenue would have shown %60.
    assert dec(discounted_body['parts']['revenue']) == Decimal('750')
    with_overhead = client.get('/api/analytics/absorption-rate', headers=headers_d,
                               params={'date_from':PERIOD_FROM,'date_to':PERIOD_TO,
                                       'overhead':'1000'}).json()
    assert dec(with_overhead['absorption_rate_upper_bound']) == Decimal('35')
    assert with_overhead['is_upper_bound'] is True

    # --- Multi-tenant isolation --------------------------------------------
    company_b = client.post('/api/companies', headers=headers, json={'name':'Emilim Firma B'}).json()['id']
    headers_b = {**headers, 'X-Company-ID':str(company_b)}
    isolated = client.get('/api/analytics/absorption-rate', headers=headers_b,
                          params={'date_from':PERIOD_FROM,'date_to':PERIOD_TO})
    assert isolated.status_code == 200, isolated.text
    other = isolated.json()
    assert dec(other['parts']['revenue']) == Decimal('0')
    assert dec(other['parts']['cogs']) == Decimal('0')
    assert dec(other['service']['labor_revenue']) == Decimal('0')
    assert dec(other['overhead']['amount']) == Decimal('0')
    assert other['absorption_rate_upper_bound'] is None

    # --- RBAC: a role without ``reports`` cannot read the KPI ---------------
    created = client.post('/api/users', headers=headers, json={
        'username':'absorption_depo','display_name':'Depo Kullanicisi',
        'password':'DepoInit!2026','role':'depo'})
    assert created.status_code == 201, created.text
    depo_login = client.post('/api/auth/login', json={
        'username':'absorption_depo','password':'DepoInit!2026'})
    assert depo_login.status_code == 200, depo_login.text
    depo_headers = {'Authorization':'Bearer '+depo_login.json()['access_token'],
                    'X-Company-ID':str(company_a)}
    denied = client.get('/api/analytics/absorption-rate', headers=depo_headers,
                        params={'date_from':PERIOD_FROM,'date_to':PERIOD_TO})
    assert denied.status_code == 403, denied.text
    # Unauthenticated access is rejected outright.
    assert client.get('/api/analytics/absorption-rate').status_code in (401, 403)

    print('ABSORPTION_RATE_FLOW_OK')
'''
