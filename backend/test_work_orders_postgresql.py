from __future__ import annotations

import os

import pytest


@pytest.mark.postgresql
def test_work_order_postgresql_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("WORK_ORDERS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("WORK_ORDERS_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
        assert login.status_code == 200, login.text
        body = login.json()
        headers = {
            'Authorization':'Bearer ' + body['access_token'],
            'X-Company-ID':str(body['companies'][0]['id']),
        }
        changed = client.post('/api/auth/change-password', headers=headers, json={
            'current_password':'admin123', 'new_password':'WorkOrdersPg123!'
        })
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
        customer = client.post('/api/customers', headers=headers, json={'name':'PostgreSQL Servis'})
        assert customer.status_code == 201, customer.text
        machine = client.post('/api/machines', headers=headers, json={
            'customer_id':customer.json()['id'], 'brand':'PG', 'model':'PG-100',
            'serial_number':'PG-WO-MACHINE', 'chassis_number':'PG-WO-CHASSIS',
        })
        assert machine.status_code == 201, machine.text
        work_order = client.post('/api/work-orders', headers=headers, json={
            'machine_id':machine.json()['id'], 'customer_id':customer.json()['id'],
            'technician_id':body['user']['id'],
            'complaint':'PostgreSQL roundtrip', 'priority':'URGENT',
            'actual_hours':'2.50', 'labor_rate':'400.00',
        })
        assert work_order.status_code == 201, work_order.text
        created = work_order.json()
        assert created['technician_id'] == body['user']['id']
        assert created['created_by'] == body['user']['id']
        assert created['total_labor_cost'] == 1000
        history = client.get(f"/api/history/work_order/{created['id']}", headers=headers)
        assert history.status_code == 200, history.text
        assert [entry['action'] for entry in history.json()].count('create') == 1
        transitioned = client.patch(
            f"/api/work-orders/{created['id']}/status",
            headers=headers,
            json={'status':'IN_PROGRESS'},
        )
        assert transitioned.status_code == 200, transitioned.text
        assert transitioned.json()['status'] == 'IN_PROGRESS'
        assert transitioned.json()['started_at'] is not None
        completed = client.patch(
            f"/api/work-orders/{created['id']}/status",
            headers=headers,
            json={'status':'COMPLETED'},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()['completed_at'] is not None
        delivered = client.patch(
            f"/api/work-orders/{created['id']}/status",
            headers=headers,
            json={'status':'DELIVERED'},
        )
        assert delivered.status_code == 200, delivered.text
        assert delivered.json()['delivered_at'] is not None
        immutable = client.put(
            f"/api/work-orders/{created['id']}",
            headers=headers,
            json={
                'machine_id':machine.json()['id'],
                'customer_id':customer.json()['id'],
                'technician_id':body['user']['id'],
            },
        )
        assert immutable.status_code == 409, immutable.text
