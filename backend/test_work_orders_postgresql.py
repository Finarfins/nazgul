from __future__ import annotations

import os

import pytest

try:
    from tests.pg_ikiz_yardimci import kosu_eki
except ImportError:
    import uuid
    def kosu_eki() -> str:
        return uuid.uuid4().hex[:8]
def _acilisa_cek() -> None:
    """Admin şifresini AÇILIŞ DURUMUNA (`admin123` + `must_change_password`) yaz.

    D2/1B-A ikizlerinden DEVRALINDI. CI dosya başına `reset_schema` çalıştırdığı
    için (`ci.yml:609`) CI ortamında DB durumu paylaşılmaz; bu dikiş yerel/pglens
    paylaşılan veritabanı koşularını korur ve gelecekte reset_schema adımının
    kaldırılmasına karşı savunma sağlar. Tek yönlü bir çare (yalnız teardown)
    dosyayı iyi bir komşu yapar ama KENDİSİNİ korumaz, çünkü şifreyi bozan
    ÖNCEKİ dosya olabilir. Bu yüzden İKİ UÇTAN çağrılır.
    """
    try:
        from tests.pg_ikiz_yardimci import acilisa_cek, kosu_eki
        acilisa_cek()
    except ImportError:
        from sqlalchemy import text as _text
        from app.auth import hash_password
        from app.db import SessionLocal, engine as _eng

        if _eng.dialect.name != "postgresql":
            return
        with SessionLocal() as db:
            if db.execute(_text("SELECT to_regclass('public.app_users')")).scalar() is None:
                return
            db.execute(
                _text(
                    "UPDATE app_users SET password_hash=:h, "
                    "must_change_password=true WHERE username='admin'"
                ),
                {"h": hash_password("admin123")},
            )
            db.commit()


@pytest.fixture(autouse=True)
def _acilis_sifresi():
    _acilisa_cek()
    try:
        yield
    finally:
        _acilisa_cek()


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
        k_ek = kosu_eki()
        customer = client.post('/api/customers', headers=headers, json={'name':'PostgreSQL Servis'})
        assert customer.status_code == 201, customer.text
        machine = client.post('/api/machines', headers=headers, json={
            'customer_id':customer.json()['id'], 'brand':'PG', 'model':'PG-100',
            'serial_number':f'PG-WO-MACHINE-{k_ek}', 'chassis_number':f'PG-WO-CHASSIS-{k_ek}',
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
