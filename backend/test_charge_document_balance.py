"""The cari balance must include posted late-fee/service-fee charge documents.

``entity_detail`` (the customer/supplier detail card) and the ``/customers``
and ``/suppliers`` list projections historically read balance as
``opening_balance + SUM(orders.final_total) - SUM(payments)`` and never touched
``receivable_charge_documents``. Posted service and late fees therefore stayed
invisible on the cari card even though ``receivables_engine`` counted them.

These smokes pin the fix and, crucially, the double-counting analysis behind it:
a payment allocated to a charge is a ``payments`` row *and* a
``payment_allocations`` row, so the balance adds the charge GROSS (never
gross-minus-applied) -- the applied amount is already subtracted by
``SUM(payments)``. The parity assertion (`entity_detail balance ==
sum(calculate_net_receivables remaining)`) holds for any fee amount precisely
because of that.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def _run(database_url: str, body: str, marker: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    # Charges only exist with the allocation ledger switched on in production
    # (RECEIVABLE_CHARGE_ALLOCATION requires PAYMENT_ALLOCATION_ENGINE). Run the
    # ledger path so receivables reconcile payments through allocations instead
    # of the legacy FIFO-of-unlinked-payments path, which predates charges.
    env["PAYMENT_ALLOCATION_ENGINE_ENABLED"] = "true"
    env["RECEIVABLE_CHARGE_ALLOCATION_ENABLED"] = "true"
    result = subprocess.run(
        [sys.executable, "-c", _FIXTURE + body],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert marker in result.stdout, result.stdout + "\n" + result.stderr


def run_parity_smoke(database_url: str) -> None:
    _run(database_url, _PARITY, "CHARGE_BALANCE_PARITY_OK")


def run_reversal_smoke(database_url: str) -> None:
    _run(database_url, _REVERSAL, "CHARGE_BALANCE_REVERSAL_OK")


def run_tenant_smoke(database_url: str) -> None:
    _run(database_url, _TENANT, "CHARGE_BALANCE_TENANT_OK")


def run_overdue_date_smoke(database_url: str) -> None:
    _run(database_url, _OVERDUE_DATE, "CHARGE_OVERDUE_DATE_OK")


def run_snapshot_shape_smoke(database_url: str) -> None:
    _run(database_url, _SNAPSHOT_SHAPE, "CHARGE_SNAPSHOT_SHAPE_OK")


_FIXTURE = r'''
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import text

import app.main  # noqa: F401  (runs migrations + bootstrap seeding)
from app.db import SessionLocal
from app.entity_detail import entity_detail
from app.money import money
from app.receivables_engine import calculate_net_receivables
from app.routers.customers import list_customers


def dec(value):
    return Decimal(str(value))


def req(cid):
    return SimpleNamespace(state=SimpleNamespace(company_id=cid))


def days(offset):
    return (date.today() + timedelta(days=offset)).isoformat()


def engine_overdue(db, cid, customer_id):
    """Overdue as the receivables engine sees it: remaining of every document
    whose engine ``due_date`` is already past."""
    return sum(
        (
            doc.remaining
            for doc in calculate_net_receivables(db, cid, date.today())
            if doc.customer_id == customer_id and doc.due_date < date.today()
        ),
        Decimal('0'),
    )


def list_row(db, cid, customer_id):
    # The list projection is called directly (not over HTTP) for the same reason
    # entity_detail is: the tenant scope is the only request state it reads.
    # ``limit`` must be passed explicitly -- the default is a FastAPI Query
    # object, not an int, outside the dependency injector.
    rows = list_customers(req(cid), q='', sort='name_asc', active='all',
                          limit=2000, db=db)
    return next(row for row in rows if row['id'] == customer_id)


def assert_all_surfaces_overdue(db, cid, customer_id, expected, label):
    """The engine, the cari detail card and the cari list must agree to the cent.

    The whole point of the shared due-date rule is that these three cannot
    disagree, so every due-date scenario is asserted on all three at once.
    """
    detail = entity_detail('customer', customer_id, req(cid), db)
    from_engine = engine_overdue(db, cid, customer_id)
    from_list = money(list_row(db, cid, customer_id)['overdue_amount'])
    from_detail = detail['summary']['overdue_amount']
    assert from_engine == expected, (label, 'engine', from_engine, expected)
    assert from_detail == expected, (label, 'detail', from_detail, expected)
    assert from_list == expected, (label, 'list', from_list, expected)
    return detail


def net_total(db, cid, customer_id):
    # Scope to one customer: PG twins share a database, so a company-wide sum
    # would fold in other tests' receivables.
    return sum(
        (
            doc.remaining
            for doc in calculate_net_receivables(db, cid, date.today())
            if doc.customer_id == customer_id
        ),
        Decimal("0"),
    )


def make_company(db, name):
    return int(db.execute(
        text("""INSERT INTO companies(name,is_active,created_at)
        VALUES(:name,:active,:now) RETURNING id"""),
        {"name": name, "active": True, "now": "2026-01-01 00:00:00+00:00"},
    ).scalar_one())


def make_customer(db, cid, name, *, opening=0):
    return int(db.execute(
        text("""INSERT INTO customers(name,company_id,opening_balance)
        VALUES(:name,:cid,:opening) RETURNING id"""),
        {"name": name, "cid": cid, "opening": opening},
    ).scalar_one())


def make_order(db, cid, customer_id, *, total, paid=0, due_date="2026-03-31",
               order_date="2026-01-05", status="completed"):
    return int(db.execute(
        text("""INSERT INTO orders(
            customer_id,order_date,due_date,final_total,status,paid_amount,
            payment_term,payment_method,company_id
        ) VALUES(
            :customer,:order_date,:due_date,:total,:status,:paid,
            'HARMAN_VADELI','credit',:cid
        ) RETURNING id"""),
        {
            "customer": customer_id, "order_date": order_date, "due_date": due_date,
            "total": total, "status": status, "paid": paid, "cid": cid,
        },
    ).scalar_one())


def make_late_fee(db, cid, order_id, customer_id, *, gross, period_end="2026-03-31",
                  period_start=None, due=None, status="posted", reversal_of=None,
                  posted=True, period_id=None):
    # Shape the row the way the charge engine actually writes it. A late fee's
    # due_date_snapshot is the ORIGINAL ORDER's due date, and
    # late_fee_charge_engine.py:200 rejects an accrual period starting on or
    # before it, so due_date_snapshot < period_start <= period_end is guaranteed
    # for every posted late fee. A fixture that inverts this ordering tests a
    # document the engine can never produce -- it violates the engine contract,
    # so the assert below keeps every caller honest.
    if period_start is None:
        period_start = period_end
    if due is None:
        due = (date.fromisoformat(period_start) - timedelta(days=1)).isoformat()
    assert due < period_start <= period_end, (due, period_start, period_end)
    # A reversal document shares the original's charge period (the unique
    # (company,order,period,kind) key forbids a second period row for it).
    if period_id is None:
        period_id = int(db.execute(
            text("""INSERT INTO receivable_charge_periods(
                company_id,order_id,installment_id,period_start,period_end,charge_kind
            ) VALUES(:cid,:order_id,NULL,:ps,:pe,'late_fee') RETURNING id"""),
            {"cid": cid, "order_id": order_id, "ps": period_start, "pe": period_end},
        ).scalar_one())
    revision = int(db.execute(
        text("""SELECT COALESCE(MAX(revision_no),0)+1
        FROM receivable_charge_documents
        WHERE company_id=:cid AND charge_period_id=:pid"""),
        {"cid": cid, "pid": period_id},
    ).scalar_one())
    return int(db.execute(
        text("""INSERT INTO receivable_charge_documents(
            company_id,charge_period_id,order_id,customer_id,charge_type,
            period_start,period_end,due_date_snapshot,principal_basis,
            annual_rate_snapshot,day_count_basis_snapshot,grace_days_snapshot,
            tax_mode_snapshot,vat_rate_snapshot,calculation_snapshot,
            net_amount,vat_amount,gross_amount,status,calculation_fingerprint,
            revision_no,reversal_of_document_id,posted_at
        ) VALUES(
            :cid,:pid,:order_id,:customer_id,'late_fee',
            :ps,:pe,:due,1000,30,365,0,'NO_VAT',0,'{}',
            :gross,0,:gross,:status,'fixture',:revision,:reversal_of,:posted_at
        ) RETURNING id"""),
        {
            "cid": cid, "pid": period_id, "order_id": order_id,
            "customer_id": customer_id, "ps": period_start, "pe": period_end,
            "due": due,
            "gross": gross, "status": status, "revision": revision,
            "reversal_of": reversal_of,
            "posted_at": "2026-06-30 00:00:00+00:00" if posted else None,
        },
    ).scalar_one())


def make_payment(db, cid, customer_id, amount, *, reference_type=None,
                 reference_id=None, payment_date="2026-07-01"):
    return int(db.execute(
        text("""INSERT INTO payments(
            entity_type,entity_id,amount,payment_date,company_id,payment_method,
            reference_type,reference_id
        ) VALUES('customer',:customer,:amount,:pd,:cid,'cash',:rt,:rid)
        RETURNING id"""),
        {
            "customer": customer_id, "amount": amount, "pd": payment_date,
            "cid": cid, "rt": reference_type, "rid": reference_id,
        },
    ).scalar_one())


def make_allocation(db, cid, payment_id, amount, *, order_id=None, charge_id=None,
                    effective_date="2026-07-01"):
    db.execute(
        text("""INSERT INTO payment_allocations(
            company_id,payment_id,order_id,receivable_charge_id,amount,
            allocation_type,effective_date
        ) VALUES(:cid,:pid,:order_id,:charge_id,:amount,'manual',:ed)"""),
        {
            "cid": cid, "pid": payment_id, "order_id": order_id,
            "charge_id": charge_id, "amount": amount, "ed": effective_date,
        },
    )
'''


# --------------------------------------------------------------------------- #
_PARITY = r'''
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as c:
    login = c.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'}).json()
    cid = login['companies'][0]['id']
    uid = login['user']['id']
    h = {'Authorization': 'Bearer ' + login['access_token'], 'X-Company-ID': str(cid)}
    changed = c.post('/api/auth/change-password', headers=h,
                     json={'current_password': 'admin123', 'new_password': 'ChargeBal123!'})
    h['Authorization'] = 'Bearer ' + changed.json()['access_token']
    # A real service fee: complete a labour-only work order.
    customer = c.post('/api/customers', headers=h,
                      json={'name': 'Servis+Gecikme Cari', 'payment_term_days': 5}).json()
    customer_id = customer['id']
    machine = c.post('/api/machines', headers=h,
                     json={'customer_id': customer_id, 'brand': 'S', 'model': 'R',
                           'serial_number': 'CB-1'}).json()
    wo = c.post('/api/work-orders', headers=h,
                json={'machine_id': machine['id'], 'customer_id': customer_id,
                      'technician_id': uid, 'actual_hours': '2', 'labor_rate': '100',
                      'warranty_type': 'NONE'}).json()
    wid = wo['id']
    assert c.patch(f'/api/work-orders/{wid}/status', headers=h,
                   json={'status': 'IN_PROGRESS'}).status_code == 200
    assert c.patch(f'/api/work-orders/{wid}/status', headers=h,
                   json={'status': 'COMPLETED'}).status_code == 200

with SessionLocal() as db:
    service_id = int(db.execute(
        text("""SELECT id FROM receivable_charge_documents
        WHERE company_id=:cid AND work_order_id=:wid AND status='posted'"""),
        {"cid": cid, "wid": wid},
    ).scalar_one())
    service_gross = dec(db.execute(
        text("SELECT gross_amount FROM receivable_charge_documents WHERE id=:i"),
        {"i": service_id},
    ).scalar_one())
    # The work order stamps period_end with the Istanbul business date of
    # completion; on a UTC CI runner near midnight that can land AFTER the UTC
    # date.today() the engine reads, dropping the fee from both balance and net
    # -- but its 50 payment stays in payment_total, breaking parity by 50. Pin
    # period_end into the past so the fee is a live receivable regardless of the
    # runner's clock. due_date_snapshot (completion + 5d, future) is left as-is,
    # so the service fee is still not overdue.
    db.execute(
        text("""UPDATE receivable_charge_documents
        SET period_start='2026-03-01',period_end='2026-03-31'
        WHERE id=:i AND company_id=:cid"""),
        {"i": service_id, "cid": cid},
    )

    # A sale order paid down to 600 remaining.
    order_id = make_order(db, cid, customer_id, total=1000, paid=400)
    op = make_payment(db, cid, customer_id, 400, reference_type='order', reference_id=order_id)
    make_allocation(db, cid, op, 400, order_id=order_id)

    # A late fee (gross 300) paid down to 200 remaining.
    late_id = make_late_fee(db, cid, order_id, customer_id, gross=300)
    lp = make_payment(db, cid, customer_id, 100, reference_type='late_fee', reference_id=late_id)
    make_allocation(db, cid, lp, 100, charge_id=late_id)

    # The service fee partially paid (50).
    sp = make_payment(db, cid, customer_id, 50, reference_type='late_fee', reference_id=service_id)
    make_allocation(db, cid, sp, 50, charge_id=service_id)
    db.commit()

with SessionLocal() as db:
    detail = entity_detail('customer', customer_id, req(cid), db)
    summary = detail['summary']
    balance = summary['current_balance']
    net = net_total(db, cid, customer_id)

    # The core invariant: the cari card balance equals the receivables engine net.
    assert balance == net, (balance, net)

    # The order-only balance (pre-fix) would have been 1000 - 550 = 450; the
    # charges lift it, so the regression is real and closed.
    assert balance == dec('750') + service_gross, (balance, service_gross)

    docs = {d.document_type: d for d in calculate_net_receivables(db, cid, date.today())}
    assert docs['sale'].remaining == dec('600'), docs['sale'].remaining
    assert docs['late_fee'].remaining == dec('200'), docs['late_fee'].remaining
    assert docs['service_fee'].remaining == service_gross - dec('50')

    # Overdue: order (600) + late fee (200); the service fee's due date
    # (completion + the customer's 5 payment-term days) is in the future, so it
    # must NOT be counted as overdue. Unifying the due-date rule must not have
    # moved service fees -- they were already keyed on due_date_snapshot.
    assert_all_surfaces_overdue(db, cid, customer_id, dec('800'),
                                'service_fee due in the future')

    # The detail card exposes the live charges as a separate labelled breakdown.
    charge_docs = {d['charge_type']: d for d in detail['charge_documents']}
    assert set(charge_docs) == {'service_fee', 'late_fee'}, charge_docs
    assert charge_docs['late_fee']['remaining'] == dec('200')
    assert charge_docs['service_fee']['remaining'] == service_gross - dec('50')

    # A service fee carries the work order that produced it, so the cari card
    # can open the service detail; the charge's own id belongs to
    # receivable_charge_documents and must never be used as a document id. A
    # late fee has no work order, so the row stays non-navigable.
    assert charge_docs['service_fee']['work_order_id'] == wid, charge_docs
    assert charge_docs['late_fee']['work_order_id'] is None, charge_docs

# Move the service fee's own vade into the past and nothing else: it must now be
# overdue on all three surfaces, for its remaining (gross - the 50 allocated).
with SessionLocal() as db:
    db.execute(
        text("""UPDATE receivable_charge_documents
        SET due_date_snapshot=:due WHERE id=:i AND company_id=:cid"""),
        {"due": days(-10), "i": service_id, "cid": cid},
    )
    db.commit()

with SessionLocal() as db:
    assert_all_surfaces_overdue(db, cid, customer_id,
                                dec('800') + service_gross - dec('50'),
                                'service_fee due in the past')

print('CHARGE_BALANCE_PARITY_OK')
'''


# --------------------------------------------------------------------------- #
_REVERSAL = r'''
with SessionLocal() as db:
    cid = make_company(db, 'Reversal Tenant')
    customer_id = make_customer(db, cid, 'Terslenen Servis')
    order_id = make_order(db, cid, customer_id, total=0, status='cancelled')
    original = make_late_fee(db, cid, order_id, customer_id, gross=3000)
    db.commit()

with SessionLocal() as db:
    detail = entity_detail('customer', customer_id, req(cid), db)
    assert detail['summary']['current_balance'] == dec('3000'), detail['summary']
    assert net_total(db, cid, customer_id) == dec('3000')

# Reverse the charge: the reversal document carries -3000 and the original flips
# to 'reversed'. Balance and receivables net must both collapse to zero.
with SessionLocal() as db:
    period_id = int(db.execute(
        text("SELECT charge_period_id FROM receivable_charge_documents WHERE id=:i"),
        {"i": original},
    ).scalar_one())
    make_late_fee(db, cid, order_id, customer_id, gross=-3000, status='posted',
                  reversal_of=original, period_id=period_id)
    db.execute(
        text("UPDATE receivable_charge_documents SET status='reversed' WHERE id=:i"),
        {"i": original},
    )
    db.commit()

with SessionLocal() as db:
    detail = entity_detail('customer', customer_id, req(cid), db)
    assert detail['summary']['current_balance'] == dec('0'), detail['summary']
    assert net_total(db, cid, customer_id) == dec('0')
    # A fully reversed charge is not a live debt: no breakdown row remains.
    assert detail['charge_documents'] == [], detail['charge_documents']

print('CHARGE_BALANCE_REVERSAL_OK')
'''


# --------------------------------------------------------------------------- #
_TENANT = r'''
from fastapi import HTTPException

with SessionLocal() as db:
    cid_a = make_company(db, 'Tenant A')
    cid_b = make_company(db, 'Tenant B')
    customer_a = make_customer(db, cid_a, 'A Cari')
    order_a = make_order(db, cid_a, customer_a, total=0, status='cancelled')
    charge_a = make_late_fee(db, cid_a, order_a, customer_a, gross=500)

    customer_b = make_customer(db, cid_b, 'B Cari')
    order_b = make_order(db, cid_b, customer_b, total=0, status='cancelled')
    charge_b = make_late_fee(db, cid_b, order_b, customer_b, gross=9999)
    db.commit()

with SessionLocal() as db:
    detail_a = entity_detail('customer', customer_a, req(cid_a), db)
    # Only tenant A's charge is counted; B's 9999 never leaks in.
    assert detail_a['summary']['current_balance'] == dec('500'), detail_a['summary']
    assert net_total(db, cid_a, customer_a) == dec('500')

    # The other tenant's receivables are disjoint.
    ids_b = {d.id for d in calculate_net_receivables(db, cid_b, date.today())}
    assert charge_a not in ids_b and charge_b in ids_b, ids_b

    # Reading A's customer under tenant B's scope is a 404, never a cross-tenant read.
    try:
        entity_detail('customer', customer_a, req(cid_b), db)
        raise AssertionError('cross-tenant entity_detail did not 404')
    except HTTPException as exc:
        assert exc.status_code == 404, exc.status_code

print('CHARGE_BALANCE_TENANT_OK')
'''


# --------------------------------------------------------------------------- #
_OVERDUE_DATE = r'''
with SessionLocal() as db:
    cid = make_company(db, 'Overdue Date Tenant')
    customer_id = make_customer(db, cid, 'Vade Farki Cari')
    order_id = make_order(db, cid, customer_id, total=0, status='cancelled')
    # A late fee in the shape the charge engine really produces: the order fell
    # due on 2026-02-15, interest accrued over 2026-03-01..2026-03-31. The fee's
    # vade is period_end -- due_date_snapshot is the ORDER's due date, not the
    # fee's (see receivables_engine.charge_due_date_sql). The earlier cari query
    # keyed the overdue column on due_date_snapshot, so it aged the fee from the
    # wrong date and drifted away from the engine.
    make_late_fee(db, cid, order_id, customer_id, gross=1000,
                  period_start='2026-03-01', period_end='2026-03-31',
                  due='2026-02-15')
    db.commit()

with SessionLocal() as db:
    # The cari card AND the cari list must match the engine to the cent. This
    # three-surface parity is the real regression guard: the overdue rule now
    # has exactly one definition and all three read it from there.
    detail = assert_all_surfaces_overdue(db, cid, customer_id, dec('1000'),
                                         'late_fee overdue on period_end')
    # The full charge is still on the balance (period_end<=today).
    assert detail['summary']['current_balance'] == dec('1000'), detail['summary']
    assert money(list_row(db, cid, customer_id)['current_balance']) == dec('1000')
    # The engine's due_date for a late fee is period_end, never the snapshot.
    charge = next(
        d for d in calculate_net_receivables(db, cid, date.today())
        if d.customer_id == customer_id
    )
    assert charge.due_date == date(2026, 3, 31), charge.due_date

print('CHARGE_OVERDUE_DATE_OK')
'''


# --------------------------------------------------------------------------- #
# Why charge_due_date_sql cannot be collapsed into
# COALESCE(due_date_snapshot,period_end). Proven against the real charge engine,
# not a hand-written fixture: for a late fee due_date_snapshot is the ORIGINAL
# ORDER's due date, it is NOT NULL, and it is always strictly EARLIER than the
# accrual period -- so it is not a vade of the fee and a COALESCE would read the
# wrong column for every late fee in the table.
_SNAPSHOT_SHAPE = r'''
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.late_fee_charge_engine import create_late_fee_draft, post_late_fee_document

# Driven through the engine API instead of HTTP on purpose: the PostgreSQL twin
# runs every smoke in this file against ONE database, so a login-based smoke
# would depend on whichever admin password an earlier smoke left behind.
with SessionLocal() as db:
    cid = make_company(db, 'Tahakkuk Sekli Tenant')
    actor_id = int(db.execute(
        text("SELECT id FROM app_users WHERE username='admin'")
    ).scalar_one())
    customer_id = make_customer(db, cid, 'Tahakkuk Sekli Cari')
    # The order falls due on 2026-01-01; interest accrues only AFTER that.
    order_id = make_order(db, cid, customer_id, total=1000, paid=0,
                          order_date='2025-12-01', due_date='2026-01-01')
    db.execute(
        text("""INSERT INTO late_fee_policies(
            company_id,annual_rate,day_count_basis,grace_days,tax_mode,
            vat_rate,effective_from,active
        ) VALUES(:cid,73,365,0,'NO_VAT',0,'2025-01-01',:active)"""),
        {"cid": cid, "active": True},
    )
    db.commit()

# The engine refuses an accrual period starting on or before the order's due
# date. This 422 is exactly what guarantees the ordering asserted below.
with SessionLocal() as db:
    try:
        create_late_fee_draft(db, cid, order_id, date(2026, 1, 1), date(2026, 1, 10),
                              'shape-too-early', created_by=actor_id)
        raise AssertionError('engine accepted a period starting on the order due date')
    except HTTPException as exc:
        assert exc.status_code == 422, exc.status_code
    db.rollback()

with SessionLocal() as db:
    draft = create_late_fee_draft(db, cid, order_id, date(2026, 1, 2), date(2026, 1, 10),
                                  'shape-draft', created_by=actor_id)
    charge_id = int(draft['id'])
    posted = post_late_fee_document(db, cid, charge_id, 'shape-post', actor_id=actor_id)
    assert posted['status'] == 'posted', posted['status']

with SessionLocal() as db:
    row = db.execute(
        text("""SELECT charge_type,due_date_snapshot,period_start,period_end
        FROM receivable_charge_documents WHERE id=:i AND company_id=:cid"""),
        {"i": charge_id, "cid": cid},
    ).mappings().one()
    snapshot = date.fromisoformat(str(row['due_date_snapshot'])[:10])
    period_start = date.fromisoformat(str(row['period_start'])[:10])
    period_end = date.fromisoformat(str(row['period_end'])[:10])
    assert row['charge_type'] == 'late_fee', row['charge_type']
    # The engine stamped the ORDER's due date, not a due date of the fee.
    assert snapshot == date(2026, 1, 1), snapshot
    assert snapshot < period_start <= period_end, (snapshot, period_start, period_end)

    # NOT NULL, so a COALESCE would never fall through to period_end: it would
    # simply read the order due date for every late fee.
    try:
        db.execute(
            text("""UPDATE receivable_charge_documents
            SET due_date_snapshot=NULL WHERE id=:i AND company_id=:cid"""),
            {"i": charge_id, "cid": cid},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
    else:
        raise AssertionError('due_date_snapshot is nullable; revisit charge_due_date_sql')

print('CHARGE_SNAPSHOT_SHAPE_OK')
'''


def _sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'charge-balance.db').as_posix()}"


def test_charge_documents_make_balance_match_receivables(tmp_path: Path) -> None:
    run_parity_smoke(_sqlite_url(tmp_path))


def test_reversed_charge_nets_out_of_balance(tmp_path: Path) -> None:
    run_reversal_smoke(_sqlite_url(tmp_path))


def test_charge_balance_is_tenant_scoped(tmp_path: Path) -> None:
    run_tenant_smoke(_sqlite_url(tmp_path))


def test_late_fee_overdue_uses_period_end(tmp_path: Path) -> None:
    run_overdue_date_smoke(_sqlite_url(tmp_path))


def test_late_fee_due_date_snapshot_is_the_order_due_date(tmp_path: Path) -> None:
    run_snapshot_shape_smoke(_sqlite_url(tmp_path))
