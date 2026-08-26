from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.auth import required_permission
from app.routers.work_orders import ALLOWED_TRANSITIONS


BACKEND = Path(__file__).resolve().parent


def test_work_order_transition_graph_is_explicit_and_terminal() -> None:
    assert ALLOWED_TRANSITIONS == {
        "SCHEDULED": {"OPEN", "CANCELLED"},
        "OPEN": {"IN_PROGRESS", "CANCELLED"},
        "IN_PROGRESS": {"WAITING_PARTS", "WAITING_CUSTOMER", "COMPLETED", "CANCELLED"},
        "WAITING_PARTS": {"IN_PROGRESS", "WAITING_CUSTOMER", "CANCELLED"},
        "WAITING_CUSTOMER": {"IN_PROGRESS", "WAITING_PARTS", "CANCELLED"},
        "COMPLETED": {"DELIVERED", "IN_PROGRESS"},
        "DELIVERED": set(),
        "CANCELLED": set(),
    }
    # SCHEDULED is an initial state only: no state may transition INTO it.
    assert all("SCHEDULED" not in targets for targets in ALLOWED_TRANSITIONS.values())
    assert required_permission("GET", "/api/work-orders") == "read"
    assert required_permission("POST", "/api/work-orders") == "sales"
    assert required_permission("PATCH", "/api/work-orders/1/status") == "sales"
    assert required_permission("GET", "/api/technician-profiles") == "read"
    assert required_permission("POST", "/api/technician-profiles") == "sales"
    assert required_permission("POST", "/api/machines/1/transfer-ownership") == "machines"
    assert required_permission("POST", "/api/machines/1/hour-readings") == "machines"


def test_work_order_crud_search_workflow_audit_and_tenancy(tmp_path: Path) -> None:
    database = tmp_path / "work-orders.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.db import engine
from app.main import app

with TestClient(app) as client:
    inspector = inspect(engine)
    assert 'work_orders' in inspector.get_table_names()
    columns = {column['name'] for column in inspector.get_columns('work_orders')}
    assert columns == {
        'id', 'company_id', 'machine_id', 'customer_id', 'technician_id', 'work_order_no',
        'opened_at', 'scheduled_date', 'started_at', 'closed_at', 'completed_at',
        'delivered_at',
        'cancelled_at', 'status', 'priority', 'complaint', 'diagnosis',
        'repair_summary', 'technician_notes', 'estimated_hours', 'actual_hours',
        'labor_rate', 'total_labor_cost', 'warranty', 'warranty_type',
        'warranty_percent', 'created_by', 'created_at', 'updated_at',
    }
    unique_constraints = {
        tuple(constraint['column_names'])
        for constraint in inspector.get_unique_constraints('work_orders')
    }
    assert ('company_id', 'work_order_no') in unique_constraints

    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    body = login.json()
    company_a = body['companies'][0]['id']
    headers_a = {
        'Authorization': 'Bearer ' + body['access_token'],
        'X-Company-ID': str(company_a),
    }
    changed = client.post('/api/auth/change-password', headers=headers_a, json={
        'current_password':'admin123', 'new_password':'WorkOrders123!'
    })
    assert changed.status_code == 200, changed.text
    headers_a['Authorization'] = 'Bearer ' + changed.json()['access_token']

    customer_a = client.post('/api/customers', headers=headers_a, json={'name':'Servis Müşterisi A'})
    assert customer_a.status_code == 201, customer_a.text
    customer_a_id = customer_a.json()['id']
    machine_a = client.post('/api/machines', headers=headers_a, json={
        'customer_id':customer_a_id, 'brand':'Sungur', 'model':'Servis 100',
        'serial_number':'WO-MACHINE-A', 'chassis_number':'WO-CHASSIS-A',
    })
    assert machine_a.status_code == 201, machine_a.text
    machine_a_id = machine_a.json()['id']

    payload = {
        'machine_id':machine_a_id,
        'customer_id':customer_a_id,
        'technician_id':body['user']['id'],
        'opened_at':'2026-07-17T09:30:00Z',
        'priority':'HIGH',
        'complaint':'Hidrolik basıncı düşük',
        'diagnosis':'Pompa kontrol edilecek',
        'repair_summary':None,
        'technician_notes':'İlk inceleme tamamlandı',
        'estimated_hours':'4.50',
        'actual_hours':'0',
        'labor_rate':'600.00',
        'warranty':True,
    }
    created = client.post('/api/work-orders', headers=headers_a, json=payload)
    assert created.status_code == 201, created.text
    work_order = created.json()
    work_order_id = work_order['id']
    assert work_order['work_order_no'].startswith('ISE-')
    assert work_order['status'] == 'OPEN'
    assert work_order['company_id'] == company_a
    assert work_order['customer_id'] == customer_a_id
    assert work_order['technician_id'] == body['user']['id']
    assert work_order['created_by'] == body['user']['id']
    assert work_order['technician_username'] == 'admin'
    assert work_order['created_by_username'] == 'admin'
    assert work_order['total_labor_cost'] == 0

    duplicate_no = client.post('/api/work-orders', headers=headers_a, json={
        **payload, 'work_order_no':work_order['work_order_no']
    })
    assert duplicate_no.status_code == 409, duplicate_no.text

    listed = client.get('/api/work-orders', headers=headers_a, params={
        'status':'OPEN', 'technician':'admin', 'customer':'Servis Müşterisi',
        'machine':'Servis 100', 'date_from':'2026-07-01T00:00:00Z',
        'date_to':'2026-07-31T23:59:59Z', 'q':'Hidrolik', 'page':1, 'page_size':1,
    })
    assert listed.status_code == 200, listed.text
    page = listed.json()
    assert page['page'] == 1 and page['page_size'] == 1 and page['total'] == 1
    assert page['pages'] == 1 and page['items'][0]['id'] == work_order_id

    detail = client.get(f'/api/work-orders/{work_order_id}', headers=headers_a)
    assert detail.status_code == 200, detail.text

    # Sunucu-tarafı machine_id filtresi: yalnızca bu makinenin iş emirleri.
    by_machine = client.get('/api/work-orders', headers=headers_a, params={'machine_id':machine_a_id})
    assert by_machine.status_code == 200, by_machine.text
    assert {item['id'] for item in by_machine.json()['items']} == {work_order_id}
    assert all(item['machine_id'] == machine_a_id for item in by_machine.json()['items'])
    empty_machine = client.get('/api/work-orders', headers=headers_a, params={'machine_id':machine_a_id + 9999})
    assert empty_machine.status_code == 200 and empty_machine.json()['total'] == 0
    assert client.get('/api/work-orders', headers=headers_a, params={'machine_id':0}).status_code == 422

    # /technicians literal yolu int {work_order_id} parametresine yakalanmamalı;
    # read yetkisi yeterli olmalı ve firmanın aktif kullanıcılarını döndürmeli.
    technicians = client.get('/api/work-orders/technicians', headers=headers_a)
    assert technicians.status_code == 200, technicians.text
    assert any(
        tech['id'] == body['user']['id'] and tech['username'] == 'admin'
        for tech in technicians.json()
    )

    payload.update({
        'diagnosis':'Hidrolik pompa arızalı',
        'repair_summary':'Pompa değişimi planlandı',
        'actual_hours':'1.25',
    })
    updated = client.put(f'/api/work-orders/{work_order_id}', headers=headers_a, json=payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()['diagnosis'] == 'Hidrolik pompa arızalı'
    assert updated.json()['status'] == 'OPEN'
    assert updated.json()['total_labor_cost'] == 750

    invalid = client.patch(
        f'/api/work-orders/{work_order_id}/status', headers=headers_a,
        json={'status':'COMPLETED'},
    )
    assert invalid.status_code == 409, invalid.text
    for next_status in ('IN_PROGRESS', 'WAITING_PARTS', 'WAITING_CUSTOMER', 'IN_PROGRESS', 'COMPLETED'):
        transitioned = client.patch(
            f'/api/work-orders/{work_order_id}/status', headers=headers_a,
            json={'status':next_status},
        )
        assert transitioned.status_code == 200, (next_status, transitioned.text)
        assert transitioned.json()['status'] == next_status
        if next_status == 'IN_PROGRESS':
            assert transitioned.json()['started_at'] is not None
    assert transitioned.json()['closed_at'] is not None
    assert transitioned.json()['completed_at'] is not None
    delivered = client.patch(
        f'/api/work-orders/{work_order_id}/status', headers=headers_a,
        json={'status':'DELIVERED'},
    )
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()['delivered_at'] is not None
    immutable = client.put(f'/api/work-orders/{work_order_id}', headers=headers_a, json=payload)
    assert immutable.status_code == 409, immutable.text
    terminal = client.patch(
        f'/api/work-orders/{work_order_id}/status', headers=headers_a,
        json={'status':'IN_PROGRESS'},
    )
    assert terminal.status_code == 409, terminal.text

    cancellable = client.post('/api/work-orders', headers=headers_a, json=payload)
    assert cancellable.status_code == 201, cancellable.text
    cancelled = client.patch(
        f"/api/work-orders/{cancellable.json()['id']}/status", headers=headers_a,
        json={'status':'CANCELLED'},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()['cancelled_at'] is not None
    assert cancelled.json()['closed_at'] is not None
    assert client.put(
        f"/api/work-orders/{cancellable.json()['id']}", headers=headers_a, json=payload,
    ).status_code == 409

    history = client.get(f'/api/history/work_order/{work_order_id}', headers=headers_a)
    assert history.status_code == 200, history.text
    actions = [entry['action'] for entry in history.json()]
    assert actions.count('create') == 1
    assert actions.count('update') == 1
    assert actions.count('status_change') == 6
    status_entries = [entry for entry in history.json() if entry['action'] == 'status_change']
    assert all('status' in entry['changed_fields']['fields'] for entry in status_entries)

    company_b_response = client.post('/api/companies', headers=headers_a, json={'name':'Servis Firma B'})
    assert company_b_response.status_code == 201, company_b_response.text
    company_b = company_b_response.json()['id']
    headers_b = {**headers_a, 'X-Company-ID':str(company_b)}
    customer_b = client.post('/api/customers', headers=headers_b, json={'name':'Servis Müşterisi B'})
    assert customer_b.status_code == 201, customer_b.text
    machine_b = client.post('/api/machines', headers=headers_b, json={
        'customer_id':customer_b.json()['id'], 'brand':'B', 'model':'B-Model',
        'serial_number':'WO-MACHINE-B', 'chassis_number':'WO-CHASSIS-B',
    })
    assert machine_b.status_code == 201, machine_b.text
    assert client.get(f'/api/work-orders/{work_order_id}', headers=headers_b).status_code == 404
    assert client.patch(
        f'/api/work-orders/{work_order_id}/status', headers=headers_b,
        json={'status':'IN_PROGRESS'},
    ).status_code == 404
    cross_machine = client.post('/api/work-orders', headers=headers_b, json={
        **payload, 'machine_id':machine_a_id, 'customer_id':customer_a_id,
    })
    assert cross_machine.status_code == 404, cross_machine.text
    owner_mismatch = client.post('/api/work-orders', headers=headers_b, json={
        **payload, 'machine_id':machine_b.json()['id'], 'customer_id':customer_a_id,
    })
    assert owner_mismatch.status_code == 409, owner_mismatch.text

    report_user = client.post('/api/users', headers=headers_a, json={
        'username':'work_order_report', 'display_name':'Work Order Report',
        'password':'WorkOrderReport123!', 'role':'rapor',
    })
    assert report_user.status_code == 201, report_user.text
    invalid_technician = client.post('/api/work-orders', headers=headers_b, json={
        **payload,
        'machine_id':machine_b.json()['id'],
        'customer_id':customer_b.json()['id'],
        'technician_id':report_user.json()['id'],
    })
    assert invalid_technician.status_code == 400, invalid_technician.text
    report_login = client.post('/api/auth/login', json={
        'username':'work_order_report', 'password':'WorkOrderReport123!'
    })
    assert report_login.status_code == 200, report_login.text
    report_body = report_login.json()
    report_headers = {
        'Authorization':'Bearer ' + report_body['access_token'],
        'X-Company-ID':str(company_a),
    }
    report_change = client.post('/api/auth/change-password', headers=report_headers, json={
        'current_password':'WorkOrderReport123!', 'new_password':'WorkOrderReport456!'
    })
    assert report_change.status_code == 200, report_change.text
    report_headers['Authorization'] = 'Bearer ' + report_change.json()['access_token']
    assert client.get('/api/work-orders', headers=report_headers).status_code == 200
    denied = client.post('/api/work-orders', headers=report_headers, json=payload)
    assert denied.status_code == 403, denied.text
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
