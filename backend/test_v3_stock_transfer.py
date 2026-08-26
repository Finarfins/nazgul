"""Şubeler Arası Parça Transferi — Increment 1 tests (SQLite).

The transfer write path itself already existed (``POST /warehouses/transfers``
moving stock through ``inventory.adjust_warehouse_stock``); what it lacked was
proof of the guarantees the parts counter depends on. These tests close that
gap and cover the new cross-branch visibility endpoint:

- ``GET /products/{id}/warehouse-stock`` lists every ACTIVE warehouse (including
  empty ones, which are valid transfer destinations) with Decimal quantities.
- A transfer decrements the source and increments the destination by the same
  amount: the company-wide total is preserved exactly, in Decimal.
- Insufficient source stock -> 409 and NOTHING moves (atomic: the destination is
  not credited, the source is not debited, no transfer document is written).
- Source == destination -> 400.
- Multi-tenant isolation: another company can neither see the per-warehouse
  stock nor transfer between this company's warehouses.
- RBAC: reads on baseline ``read``; the transfer write needs ``stock`` (a
  ``satis`` user is refused with 403).

The PostgreSQL 16 twin lives in test_stock_transfer_postgresql.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import ROLE_PERMISSIONS, has_permission, required_permission


BACKEND = Path(__file__).resolve().parent


def test_stock_transfer_rbac_map() -> None:
    # Cross-branch visibility is a read; moving stock is a ``stock`` write.
    assert required_permission("GET", "/api/products/1/warehouse-stock") == "read"
    assert required_permission("GET", "/api/warehouses/transfers") == "read"
    assert required_permission("POST", "/api/warehouses/transfers") == "stock"
    # The warehouse role may transfer; sales/reporting roles may not.
    assert has_permission("depo", "stock") and has_permission("yonetici", "stock")
    assert not has_permission("satis", "stock")
    assert not has_permission("rapor", "stock")
    assert "stock" not in ROLE_PERMISSIONS["muhasebe"]


def test_stock_transfer_flow(tmp_path: Path) -> None:
    database = tmp_path / "stock-transfer.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(BACKEND),
        timeout=300,
    )
    assert "STOCK_TRANSFER_FLOW_OK" in completed.stdout, completed.stdout + completed.stderr


# Shared, DB-agnostic scenario. test_stock_transfer_postgresql.py runs the exact
# same script against PostgreSQL 16 so SQLite/PG parity is proven, not assumed.
_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from app.main import app


def qty(value):
    return Decimal(str(value))


with TestClient(app) as client:
    body = client.post('/api/auth/login', json={'username':'admin','password':'admin123'}).json()
    headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
    changed = client.post('/api/auth/change-password', headers=headers, json={
        'current_password':'admin123','new_password':'Transfer123!'})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

    # Fresh company so the run is isolated even on the shared CI PG database.
    company_a = client.post('/api/companies', headers=headers, json={'name':'Transfer Firma A'}).json()['id']
    headers['X-Company-ID'] = str(company_a)

    # A new company is provisioned with a default warehouse; add a second branch
    # warehouse and a third that holds nothing (still a valid destination).
    merkez = client.get('/api/warehouses', headers=headers).json()
    assert merkez, 'expected the default warehouse'
    source_id = merkez[0]['id']
    created = client.post('/api/warehouses', headers=headers, json={'name':'Şube Depo'})
    assert created.status_code == 201, created.text
    target_id = created.json()['id']
    empty_id = client.post('/api/warehouses', headers=headers, json={'name':'Boş Depo'}).json()['id']

    product = client.post('/api/products', headers=headers, json={
        'name':'Transfer Filtresi','product_code':'TRF-100','unit':'Adet'})
    assert product.status_code == 201, product.text
    pid = product.json()['id']

    # Seed a fractional quantity so Decimal handling is exercised end to end.
    seeded = client.post(f'/api/products/{pid}/stock', headers=headers, json={
        'mode':'add','warehouse_id':source_id,'quantity':'12.5',
        'movement_date':'2026-07-24','note':'Transfer testi başlangıç stoğu'})
    assert seeded.status_code == 200, seeded.text

    # --- Cross-branch visibility -------------------------------------------
    visibility = client.get(f'/api/products/{pid}/warehouse-stock', headers=headers)
    assert visibility.status_code == 200, visibility.text
    view = visibility.json()
    assert view['product']['id'] == pid and view['product']['product_code'] == 'TRF-100'
    by_warehouse = {row['warehouse_id']: qty(row['quantity']) for row in view['warehouses']}
    # Every ACTIVE warehouse is listed, including the empty one (a destination).
    assert set(by_warehouse) == {source_id, target_id, empty_id}, by_warehouse
    assert by_warehouse[source_id] == Decimal('12.5')
    assert by_warehouse[target_id] == Decimal('0')
    assert by_warehouse[empty_id] == Decimal('0')
    assert qty(view['total_quantity']) == Decimal('12.5')
    assert view['available_warehouse_count'] == 1
    # Warehouses are ordered by quantity so the counter sees the supplier first.
    assert view['warehouses'][0]['warehouse_id'] == source_id
    assert view['warehouses'][0]['warehouse_name']

    def snapshot():
        rows = client.get(f'/api/products/{pid}/warehouse-stock', headers=headers).json()
        return {row['warehouse_id']: qty(row['quantity']) for row in rows['warehouses']}

    def transfer_count():
        return len(client.get('/api/warehouses/transfers', headers=headers).json())

    # --- Source == destination is rejected ----------------------------------
    same = client.post('/api/warehouses/transfers', headers=headers, json={
        'source_warehouse_id':source_id,'target_warehouse_id':source_id,
        'transfer_date':'2026-07-24','items':[{'product_id':pid,'quantity':1}]})
    assert same.status_code == 400, same.text

    # --- Insufficient source stock: 409 and NOTHING moves -------------------
    before = snapshot()
    documents_before = transfer_count()
    short = client.post('/api/warehouses/transfers', headers=headers, json={
        'source_warehouse_id':source_id,'target_warehouse_id':target_id,
        'transfer_date':'2026-07-24','items':[{'product_id':pid,'quantity':'99'}]})
    assert short.status_code == 409, short.text
    assert snapshot() == before, (snapshot(), before)
    assert transfer_count() == documents_before

    # A partially-satisfiable multi-line transfer rolls back COMPLETELY: the
    # first line would fit, the second does not, so neither leg may survive.
    second = client.post('/api/products', headers=headers, json={
        'name':'Transfer Contası','product_code':'TRF-200','unit':'Adet'}).json()['id']
    client.post(f'/api/products/{second}/stock', headers=headers, json={
        'mode':'add','warehouse_id':source_id,'quantity':'1',
        'movement_date':'2026-07-24','note':'İkinci kalem'})
    partial = client.post('/api/warehouses/transfers', headers=headers, json={
        'source_warehouse_id':source_id,'target_warehouse_id':target_id,
        'transfer_date':'2026-07-24','items':[
            {'product_id':pid,'quantity':'1'},
            {'product_id':second,'quantity':'50'}]})
    assert partial.status_code == 409, partial.text
    assert snapshot() == before, (snapshot(), before)
    assert transfer_count() == documents_before

    # --- A successful transfer preserves the company-wide total -------------
    total_before = sum(before.values(), Decimal('0'))
    moved = client.post('/api/warehouses/transfers', headers=headers, json={
        'source_warehouse_id':source_id,'target_warehouse_id':target_id,
        'transfer_date':'2026-07-24','note':'Şubeye sevk',
        'items':[{'product_id':pid,'quantity':'2.5'}]})
    assert moved.status_code == 201, moved.text
    after = snapshot()
    assert after[source_id] == before[source_id] - Decimal('2.5'), after
    assert after[target_id] == before[target_id] + Decimal('2.5'), after
    assert sum(after.values(), Decimal('0')) == total_before
    # The denormalized product total is untouched by an internal move.
    assert qty(client.get(f'/api/products/{pid}/warehouse-stock', headers=headers).json()['total_quantity']) == total_before
    assert qty(client.get(f'/api/products/{pid}', headers=headers).json()['product']['stock']) == total_before

    # The transfer is on the history list and readable as a document.
    history = client.get('/api/warehouses/transfers', headers=headers).json()
    assert len(history) == documents_before + 1
    transfer_id = moved.json()['id']
    document = client.get(f'/api/warehouse-transfers/{transfer_id}', headers=headers)
    assert document.status_code == 200, document.text
    assert [row['product_id'] for row in document.json()['items']] == [pid]
    assert {row['movement_type'] for row in document.json()['movements']} == {'transfer_out','transfer_in'}

    # Two warehouses can now supply the part.
    assert client.get(f'/api/products/{pid}/warehouse-stock', headers=headers).json()['available_warehouse_count'] == 2

    # --- Multi-tenant isolation ---------------------------------------------
    company_b = client.post('/api/companies', headers=headers, json={'name':'Transfer Firma B'}).json()['id']
    headers_b = {**headers, 'X-Company-ID':str(company_b)}
    assert client.get(f'/api/products/{pid}/warehouse-stock', headers=headers_b).status_code == 404
    assert client.get('/api/warehouses/transfers', headers=headers_b).json() == []
    assert client.get(f'/api/warehouse-transfers/{transfer_id}', headers=headers_b).status_code == 404
    # Company B cannot move company A's stock between company A's warehouses.
    foreign = client.post('/api/warehouses/transfers', headers=headers_b, json={
        'source_warehouse_id':source_id,'target_warehouse_id':target_id,
        'transfer_date':'2026-07-24','items':[{'product_id':pid,'quantity':'1'}]})
    assert foreign.status_code == 400, foreign.text
    assert snapshot() == after, (snapshot(), after)

    # --- RBAC: a sales user may look, but may not move stock ----------------
    assert client.post('/api/users', headers=headers, json={
        'username':'transfer_satis','display_name':'Transfer Satis',
        'password':'TransferSatis123!','role':'satis'}).status_code in (200, 201)
    login = client.post('/api/auth/login', json={
        'username':'transfer_satis','password':'TransferSatis123!'}).json()
    sales = {'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(company_a)}
    rotated = client.post('/api/auth/change-password', headers=sales, json={
        'current_password':'TransferSatis123!','new_password':'TransferSatis456!'}).json()
    sales['Authorization'] = 'Bearer ' + rotated['access_token']
    assert client.get(f'/api/products/{pid}/warehouse-stock', headers=sales).status_code == 200
    denied = client.post('/api/warehouses/transfers', headers=sales, json={
        'source_warehouse_id':source_id,'target_warehouse_id':target_id,
        'transfer_date':'2026-07-24','items':[{'product_id':pid,'quantity':'1'}]})
    assert denied.status_code == 403, denied.text
    assert snapshot() == after, (snapshot(), after)

    # --- The negative-stock POLICY must not reach a physical transfer --------
    # A sale may go out ahead of the paperwork under an 'allow' or
    # 'manager_override' policy. A transfer may NOT: no override makes a branch
    # hand over parts it does not physically hold. Under BOTH permissive
    # policies an insufficient source is still 409 with every leg untouched.
    from urllib.parse import quote

    from sqlalchemy import text as _sql_text
    from app.db import engine as _engine

    def ledger():
        # Counted straight from the database, so a rolled-back leg cannot hide
        # behind an API projection.
        with _engine.connect() as connection:
            return {
                'transfers': connection.execute(_sql_text(
                    'SELECT COUNT(*) FROM stock_transfers WHERE company_id=:cid'
                ), {'cid': company_a}).scalar(),
                'items': connection.execute(_sql_text(
                    """SELECT COUNT(*) FROM stock_transfer_items i
                    JOIN stock_transfers t ON t.id=i.transfer_id
                    WHERE t.company_id=:cid"""
                ), {'cid': company_a}).scalar(),
                'movements': connection.execute(_sql_text(
                    """SELECT COUNT(*) FROM stock_movements
                    WHERE company_id=:cid AND reference_type='transfer'"""
                ), {'cid': company_a}).scalar(),
            }

    reason = 'Operasyon müdürü transfer istisnası'
    override_headers = {**headers, 'X-Policy-Override': 'true',
                        'X-Policy-Override-Reason': quote(reason)}
    short_payload = {'source_warehouse_id': source_id, 'target_warehouse_id': target_id,
                     'transfer_date': '2026-07-24',
                     'items': [{'product_id': pid, 'quantity': '9999'}]}

    for policy in ('allow', 'manager_override'):
        applied = client.put('/api/company-settings', headers=headers, json={
            'negative_stock_policy': policy, 'credit_limit_policy': 'block'})
        assert applied.status_code == 200, applied.text
        stock_before, ledger_before = snapshot(), ledger()

        # Plain request, and (for manager_override) a fully valid override that
        # WOULD have been honoured by a sale: both must still be refused.
        attempts = [client.post('/api/warehouses/transfers', headers=headers, json=short_payload)]
        attempts.append(client.post('/api/warehouses/transfers', headers=override_headers, json=short_payload))
        for attempt in attempts:
            assert attempt.status_code == 409, (policy, attempt.status_code, attempt.text)
            # The refusal must not invite an override: the frontend opens its
            # manager-reason form on this phrase and would loop the user on 409s.
            assert 'Yönetici onayıyla' not in attempt.json()['detail'], (policy, attempt.text)

        # No leg moved: warehouse stock, transfer header, items, movements.
        assert snapshot() == stock_before, (policy, snapshot(), stock_before)
        assert ledger() == ledger_before, (policy, ledger(), ledger_before)
        # ...and no negative-stock override was written to the audit log.
        overrides = client.get('/api/policy-overrides', headers=headers).json()
        assert not [row for row in overrides if row['resource_type'] == 'stock_transfers'], overrides

    # A transfer the source CAN cover still succeeds under a permissive policy.
    good = client.post('/api/warehouses/transfers', headers=headers, json={
        'source_warehouse_id': source_id, 'target_warehouse_id': target_id,
        'transfer_date': '2026-07-24', 'items': [{'product_id': pid, 'quantity': '1'}]})
    assert good.status_code == 201, good.text
    assert good.json()['policy_overrides'] == [], good.text

    print('STOCK_TRANSFER_FLOW_OK')
'''
