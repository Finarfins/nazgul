"""Servis v2 FAZ-2 — work order labor lines.

Covers CRUD, tenant isolation, RBAC (approval is admin/yonetici only), the
approval compare-and-set, the billed/terminal/SCHEDULED freeze, cancellation
voiding, the rate freeze, and — the locked design criterion — the billing
source priority: an invoice bills EITHER the v1 header labor OR the approved
labor lines, never both.

``run_servis_v2_faz2_labor_smoke`` is shared with the PostgreSQL twin.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def run_servis_v2_faz2_labor_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_servis_v2_faz2_labor_sqlite(tmp_path: Path) -> None:
    run_servis_v2_faz2_labor_smoke(
        f"sqlite:///{(tmp_path / 'servis-faz2-labor.db').as_posix()}"
    )


def test_labor_line_states_and_permission_mapping() -> None:
    from app.auth import required_permission
    from app.routers.work_order_labor_lines import APPROVAL_ROLES
    from app.work_order_labor_schemas import LABOR_LINE_STATES

    assert LABOR_LINE_STATES == {"DRAFT", "APPROVED", "VOID"}
    # Approval is stricter than the path-prefix permission: the middleware only
    # gets us to ``sales``, the handler additionally requires an admin role.
    assert APPROVAL_ROLES == {"admin", "yonetici"}
    assert required_permission("GET", "/api/work-orders/1/labor-lines") == "read"
    assert required_permission("POST", "/api/work-orders/1/labor-lines") == "sales"
    assert (
        required_permission("POST", "/api/work-orders/1/labor-lines/2/approve") == "sales"
    )


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient

from app.db import engine
from app.main import app

ADMIN_PW = 'ServisFaz2!123'


def admin_headers(client):
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    if login.status_code == 200:
        body = login.json()
        headers = {'Authorization':'Bearer '+body['access_token'],
                   'X-Company-ID':str(body['companies'][0]['id'])}
        changed = client.post('/api/auth/change-password', headers=headers,
                              json={'current_password':'admin123','new_password':ADMIN_PW})
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer '+changed.json()['access_token']
        return headers, body
    login = client.post('/api/auth/login', json={'username':'admin','password':ADMIN_PW})
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {'Authorization':'Bearer '+body['access_token'],
               'X-Company-ID':str(body['companies'][0]['id'])}
    return headers, body


def login_as(client, username, password, new_password, company_id):
    created = client.post('/api/auth/login', json={'username':username,'password':password})
    assert created.status_code == 200, created.text
    headers = {'Authorization':'Bearer '+created.json()['access_token'],
               'X-Company-ID':str(company_id)}
    changed = client.post('/api/auth/change-password', headers=headers,
                          json={'current_password':password,'new_password':new_password})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer '+changed.json()['access_token']
    return headers


with TestClient(app) as client:
    headers, body = admin_headers(client)
    company_a = int(headers['X-Company-ID'])
    admin_id = int(body['user']['id'])

    customer = client.post('/api/customers', headers=headers, json={'name':'F2 Müşteri'})
    assert customer.status_code == 201, customer.text
    customer_id = customer.json()['id']
    machine = client.post('/api/machines', headers=headers, json={
        'customer_id':customer_id, 'brand':'Sungur', 'model':'F2-100',
        'serial_number':'F2-SER-1', 'chassis_number':'F2-CHS-1',
    })
    assert machine.status_code == 201, machine.text
    machine_id = machine.json()['id']

    def new_work_order(**overrides):
        payload = {'machine_id':machine_id, 'technician_id':admin_id,
                   'actual_hours':'2', 'labor_rate':'100'}
        payload.update(overrides)
        response = client.post('/api/work-orders', headers=headers, json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    # ---- CRUD + rate freeze ---------------------------------------------------
    wo = new_work_order()
    wo_id = wo['id']
    created = client.post(f'/api/work-orders/{wo_id}/labor-lines', headers=headers,
                          json={'technician_user_id':admin_id, 'hours':'1.50'})
    assert created.status_code == 201, created.text
    line = created.json()
    line_id = line['id']
    assert line['line_status'] == 'DRAFT'
    # Rate defaulted from the work order header (100.00) and frozen on the row.
    assert Decimal(str(line['hourly_rate'])) == Decimal('100.00'), line
    assert Decimal(str(line['line_total'])) == Decimal('150.00'), line

    # Explicit override is honoured.
    override = client.post(f'/api/work-orders/{wo_id}/labor-lines', headers=headers,
                           json={'technician_user_id':admin_id, 'hours':'2',
                                 'hourly_rate':'250.00', 'note':'usta saati'})
    assert override.status_code == 201, override.text
    assert Decimal(str(override.json()['line_total'])) == Decimal('500.00')
    override_id = override.json()['id']

    # Validation: hours must be > 0 and the technician must belong to the company.
    assert client.post(f'/api/work-orders/{wo_id}/labor-lines', headers=headers,
                       json={'technician_user_id':admin_id, 'hours':'0'}).status_code == 422
    assert client.post(f'/api/work-orders/{wo_id}/labor-lines', headers=headers,
                       json={'technician_user_id':999999, 'hours':'1'}).status_code == 400
    assert client.post(f'/api/work-orders/{wo_id}/labor-lines', headers=headers,
                       json={'technician_user_id':admin_id, 'hours':'99999999'}).status_code == 422

    updated = client.put(f'/api/work-orders/{wo_id}/labor-lines/{line_id}', headers=headers,
                         json={'technician_user_id':admin_id, 'hours':'3'})
    assert updated.status_code == 200, updated.text
    # Rate stays frozen across an edit that does not send a new one.
    assert Decimal(str(updated.json()['hourly_rate'])) == Decimal('100.00')
    assert Decimal(str(updated.json()['line_total'])) == Decimal('300.00')

    listed = client.get(f'/api/work-orders/{wo_id}/labor-lines', headers=headers)
    assert listed.status_code == 200, listed.text
    assert len(listed.json()['items']) == 2
    assert listed.json()['labor_source'] == 'header'  # nothing approved yet
    assert Decimal(str(listed.json()['approved_total'])) == Decimal('0')

    # ---- RATE FREEZE vs a later header change --------------------------------
    header_changed = client.put(f'/api/work-orders/{wo_id}', headers=headers, json={
        'machine_id':machine_id, 'technician_id':admin_id,
        'actual_hours':'2', 'labor_rate':'999',
    })
    assert header_changed.status_code == 200, header_changed.text
    after_change = client.get(f'/api/work-orders/{wo_id}/labor-lines', headers=headers).json()
    frozen = {item['id']: Decimal(str(item['hourly_rate'])) for item in after_change['items']}
    assert frozen[line_id] == Decimal('100.00'), frozen
    assert frozen[override_id] == Decimal('250.00'), frozen

    # ---- RBAC: satış rolü taslak açar, onaylayamaz ----------------------------
    sales_user = client.post('/api/users', headers=headers, json={
        'username':'f2_satis', 'display_name':'F2 Satis',
        'password':'F2SatisTest123!', 'role':'satis',
    })
    assert sales_user.status_code == 201, sales_user.text
    sales_headers = login_as(client, 'f2_satis', 'F2SatisTest123!', 'F2SatisTest456!', company_a)
    sales_line = client.post(f'/api/work-orders/{wo_id}/labor-lines', headers=sales_headers,
                             json={'technician_user_id':admin_id, 'hours':'1'})
    assert sales_line.status_code == 201, sales_line.text
    sales_line_id = sales_line.json()['id']
    assert client.put(f'/api/work-orders/{wo_id}/labor-lines/{sales_line_id}',
                      headers=sales_headers,
                      json={'technician_user_id':admin_id, 'hours':'1.25'}).status_code == 200
    denied = client.post(f'/api/work-orders/{wo_id}/labor-lines/{sales_line_id}/approve',
                         headers=sales_headers)
    assert denied.status_code == 403, denied.text
    # Read-only role cannot create at all.
    report_user = client.post('/api/users', headers=headers, json={
        'username':'f2_rapor', 'display_name':'F2 Rapor',
        'password':'F2RaporTest123!', 'role':'rapor',
    })
    assert report_user.status_code == 201, report_user.text
    report_headers = login_as(client, 'f2_rapor', 'F2RaporTest123!', 'F2RaporTest456!', company_a)
    assert client.get(f'/api/work-orders/{wo_id}/labor-lines', headers=report_headers).status_code == 200
    assert client.post(f'/api/work-orders/{wo_id}/labor-lines', headers=report_headers,
                       json={'technician_user_id':admin_id, 'hours':'1'}).status_code == 403

    # ---- Onay + APPROVED satır değiştirilemez --------------------------------
    approved = client.post(f'/api/work-orders/{wo_id}/labor-lines/{sales_line_id}/approve',
                           headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()['line_status'] == 'APPROVED'
    assert approved.json()['approved_by'] == admin_id
    assert approved.json()['approved_at'] is not None
    # Second approval finds no DRAFT row (compare-and-set).
    again = client.post(f'/api/work-orders/{wo_id}/labor-lines/{sales_line_id}/approve',
                        headers=headers)
    assert again.status_code == 409, again.text
    frozen_edit = client.put(f'/api/work-orders/{wo_id}/labor-lines/{sales_line_id}',
                             headers=headers, json={'technician_user_id':admin_id, 'hours':'9'})
    assert frozen_edit.status_code == 409, frozen_edit.text
    # Sales may not void an approved line either.
    assert client.delete(f'/api/work-orders/{wo_id}/labor-lines/{sales_line_id}',
                         headers=sales_headers).status_code == 403

    # ---- VOID + yeniden ekleme (düzeltme yolu) -------------------------------
    voided = client.delete(f'/api/work-orders/{wo_id}/labor-lines/{override_id}', headers=headers)
    assert voided.status_code == 204, voided.text
    assert client.delete(f'/api/work-orders/{wo_id}/labor-lines/{override_id}',
                         headers=headers).status_code == 409
    void_state = client.get(f'/api/work-orders/{wo_id}/labor-lines', headers=headers,
                            params={'status':'VOID'}).json()
    assert [item['id'] for item in void_state['items']] == [override_id]

    history = client.get(f'/api/history/work_order_labor_line/{sales_line_id}', headers=headers)
    assert history.status_code == 200, history.text
    assert 'approve' in [entry['action'] for entry in history.json()]

    # ---- KİLİTLİ KRİTER: fatura kaynak önceliği ------------------------------
    def billing(work_order_id):
        response = client.get(f'/api/work-orders/{work_order_id}/invoice', headers=headers)
        assert response.status_code == 200, response.text
        return response.json()

    def complete(work_order_id):
        for status in ('IN_PROGRESS', 'COMPLETED'):
            response = client.patch(f'/api/work-orders/{work_order_id}/status',
                                    headers=headers, json={'status':status})
            assert response.status_code == 200, response.text

    # (a) hiç onaylı satır yok -> v1 header tutarı (2h x 100 = 200)
    wo_a = new_work_order()
    complete(wo_a['id'])
    summary_a = billing(wo_a['id'])
    assert summary_a['labor']['source'] == 'header', summary_a
    assert Decimal(str(summary_a['totals']['labor'])) == Decimal('200.00'), summary_a
    assert summary_a['totals']['labor_source'] == 'header'
    assert summary_a['totals']['labor_line_ids'] == []

    # (c) yalnız DRAFT satırlar -> yine header; taslaklar hesaba GİRMEZ
    wo_c = new_work_order()
    draft = client.post(f"/api/work-orders/{wo_c['id']}/labor-lines", headers=headers,
                        json={'technician_user_id':admin_id, 'hours':'5', 'hourly_rate':'400'})
    assert draft.status_code == 201, draft.text
    complete(wo_c['id'])
    summary_c = billing(wo_c['id'])
    assert summary_c['labor']['source'] == 'header', summary_c
    assert Decimal(str(summary_c['totals']['labor'])) == Decimal('200.00'), summary_c

    # (b) onaylı satırlar -> YALNIZ satır toplamı; header tutarı faturaya GİRMEZ
    wo_b = new_work_order()
    first = client.post(f"/api/work-orders/{wo_b['id']}/labor-lines", headers=headers,
                        json={'technician_user_id':admin_id, 'hours':'3', 'hourly_rate':'150'})
    second = client.post(f"/api/work-orders/{wo_b['id']}/labor-lines", headers=headers,
                         json={'technician_user_id':admin_id, 'hours':'2', 'hourly_rate':'200'})
    assert first.status_code == 201 and second.status_code == 201
    ignored_draft = client.post(f"/api/work-orders/{wo_b['id']}/labor-lines", headers=headers,
                                json={'technician_user_id':admin_id, 'hours':'7', 'hourly_rate':'900'})
    assert ignored_draft.status_code == 201
    for line_ref in (first, second):
        approve = client.post(
            f"/api/work-orders/{wo_b['id']}/labor-lines/{line_ref.json()['id']}/approve",
            headers=headers)
        assert approve.status_code == 200, approve.text
    complete(wo_b['id'])
    summary_b = billing(wo_b['id'])
    LINES_ONLY = Decimal('850.00')          # 3*150 + 2*200
    HEADER_AMOUNT = Decimal('200.00')       # 2*100, must NOT be added
    assert summary_b['labor']['source'] == 'lines', summary_b
    assert Decimal(str(summary_b['totals']['labor'])) == LINES_ONLY, summary_b
    # The double-count assertion: header + lines would be 1050.00.
    assert Decimal(str(summary_b['totals']['labor'])) != LINES_ONLY + HEADER_AMOUNT
    assert summary_b['totals']['labor_line_ids'] == [first.json()['id'], second.json()['id']]
    assert Decimal(str(summary_b['labor']['total_hours'])) == Decimal('5')

    # ... and the same holds on the issued invoice, not just the preview.
    invoice = client.post('/api/invoices/generate', headers=headers,
                          json={'work_order_id':wo_b['id']})
    assert invoice.status_code == 201, invoice.text
    issued = invoice.json()
    labor_items = [item for item in issued['items'] if item['item_type'] == 'LABOR']
    assert len(labor_items) == 2, labor_items
    labor_sum = sum(Decimal(str(item['total'])) for item in labor_items)
    assert labor_sum == LINES_ONLY, labor_items
    assert labor_sum != LINES_ONLY + HEADER_AMOUNT
    assert issued['totals']['labor_source'] == 'lines'
    assert Decimal(str(issued['totals']['labor'])) == LINES_ONLY

    # Header-source invoice keeps exactly one LABOR item (v1 shape unchanged).
    invoice_a = client.post('/api/invoices/generate', headers=headers,
                            json={'work_order_id':wo_a['id']})
    assert invoice_a.status_code == 201, invoice_a.text
    header_items = [item for item in invoice_a.json()['items'] if item['item_type'] == 'LABOR']
    assert len(header_items) == 1, header_items
    assert Decimal(str(header_items[0]['total'])) == HEADER_AMOUNT
    assert invoice_a.json()['totals']['labor_source'] == 'header'

    # ---- Faturalı iş emrinde satır mutasyonu 409 -----------------------------
    billed_line = client.post(f"/api/work-orders/{wo_b['id']}/labor-lines", headers=headers,
                              json={'technician_user_id':admin_id, 'hours':'1'})
    assert billed_line.status_code == 409, billed_line.text
    assert client.put(
        f"/api/work-orders/{wo_b['id']}/labor-lines/{first.json()['id']}", headers=headers,
        json={'technician_user_id':admin_id, 'hours':'4'}).status_code == 409
    assert client.delete(
        f"/api/work-orders/{wo_b['id']}/labor-lines/{ignored_draft.json()['id']}",
        headers=headers).status_code == 409
    assert client.post(
        f"/api/work-orders/{wo_b['id']}/labor-lines/{ignored_draft.json()['id']}/approve",
        headers=headers).status_code == 409

    # ---- SCHEDULED ve CANCELLED reddi ----------------------------------------
    scheduled = client.post('/api/work-orders', headers=headers, json={
        'machine_id':machine_id, 'technician_id':admin_id, 'status':'SCHEDULED',
        'scheduled_date':'2026-08-01T09:00:00Z',
    })
    assert scheduled.status_code == 201, scheduled.text
    blocked = client.post(f"/api/work-orders/{scheduled.json()['id']}/labor-lines",
                          headers=headers, json={'technician_user_id':admin_id, 'hours':'1'})
    assert blocked.status_code == 409, blocked.text

    # İptal: DRAFT + APPROVED satırlar aynı transaction'da VOID olur.
    wo_cancel = new_work_order()
    draft_line = client.post(f"/api/work-orders/{wo_cancel['id']}/labor-lines", headers=headers,
                             json={'technician_user_id':admin_id, 'hours':'1'})
    approved_line = client.post(f"/api/work-orders/{wo_cancel['id']}/labor-lines", headers=headers,
                                json={'technician_user_id':admin_id, 'hours':'2'})
    assert draft_line.status_code == 201 and approved_line.status_code == 201
    approve = client.post(
        f"/api/work-orders/{wo_cancel['id']}/labor-lines/{approved_line.json()['id']}/approve",
        headers=headers)
    assert approve.status_code == 200, approve.text
    cancelled = client.patch(f"/api/work-orders/{wo_cancel['id']}/status", headers=headers,
                             json={'status':'CANCELLED'})
    assert cancelled.status_code == 200, cancelled.text
    after_cancel = client.get(f"/api/work-orders/{wo_cancel['id']}/labor-lines",
                              headers=headers).json()
    assert [item['line_status'] for item in after_cancel['items']] == ['VOID', 'VOID'], after_cancel
    assert Decimal(str(after_cancel['approved_total'])) == Decimal('0')
    assert client.post(f"/api/work-orders/{wo_cancel['id']}/labor-lines", headers=headers,
                       json={'technician_user_id':admin_id, 'hours':'1'}).status_code == 409

    # ---- Tenant izolasyonu ----------------------------------------------------
    company_b = client.post('/api/companies', headers=headers, json={'name':'F2 Firma B'})
    assert company_b.status_code == 201, company_b.text
    headers_b = {**headers, 'X-Company-ID':str(company_b.json()['id'])}
    assert client.get(f'/api/work-orders/{wo_id}/labor-lines', headers=headers_b).status_code == 404
    assert client.post(f'/api/work-orders/{wo_id}/labor-lines', headers=headers_b,
                       json={'technician_user_id':admin_id, 'hours':'1'}).status_code == 404
    assert client.put(f'/api/work-orders/{wo_id}/labor-lines/{line_id}', headers=headers_b,
                      json={'technician_user_id':admin_id, 'hours':'1'}).status_code == 404
    assert client.post(f'/api/work-orders/{wo_id}/labor-lines/{line_id}/approve',
                       headers=headers_b).status_code == 404
    assert client.delete(f'/api/work-orders/{wo_id}/labor-lines/{line_id}',
                         headers=headers_b).status_code == 404

engine.dispose()
'''
