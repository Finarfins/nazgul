"""Servis v2 FAZ-1 — hour readings, ownership history, technician profiles and
the SCHEDULED work-order state.

Subprocess smoke in the established V3 style: a fresh database per test, the
whole API exercised through TestClient with real auth/RBAC/tenancy middleware.
``run_servis_v2_faz1_smoke`` is shared with the PostgreSQL twin
(:mod:`test_servis_v2_faz1_postgresql`).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def run_servis_v2_faz1_smoke(database_url: str) -> None:
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


def test_servis_v2_faz1_sqlite(tmp_path: Path) -> None:
    run_servis_v2_faz1_smoke(f"sqlite:///{(tmp_path / 'servis-faz1.db').as_posix()}")


def test_ownership_backfill_on_populated_database(tmp_path: Path) -> None:
    """Downgrade -1 then upgrade head on a database that already has machines:
    exercises the SQLite batch table rebuild with data present and proves the
    ownership backfill creates the opening row for pre-existing machines."""
    database = tmp_path / "servis-faz1-backfill.db"
    database_url = f"sqlite:///{database.as_posix()}"
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)

    seed = r'''
from fastapi.testclient import TestClient
from app.db import engine
from app.main import app

with TestClient(app) as client:
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {'Authorization':'Bearer '+body['access_token'],
               'X-Company-ID':str(body['companies'][0]['id'])}
    changed = client.post('/api/auth/change-password', headers=headers,
                          json={'current_password':'admin123','new_password':'BackfillPw123!'})
    assert changed.status_code == 200, changed.text
    headers['Authorization'] = 'Bearer '+changed.json()['access_token']
    customer = client.post('/api/customers', headers=headers, json={'name':'Backfill Müşterisi'})
    assert customer.status_code == 201, customer.text
    machine = client.post('/api/machines', headers=headers, json={
        'customer_id':customer.json()['id'], 'brand':'Backfill', 'model':'BF-1',
        'serial_number':'BF-SER-1', 'chassis_number':'BF-CHS-1',
        'working_hours':'500.00',
    })
    assert machine.status_code == 201, machine.text
    # A SCHEDULED work order must be re-based to OPEN by the downgrade before
    # the CHECK constraint narrows.
    scheduled = client.post('/api/work-orders', headers=headers, json={
        'machine_id':machine.json()['id'], 'technician_id':body['user']['id'],
        'status':'SCHEDULED', 'scheduled_date':'2026-08-01T09:00:00Z',
    })
    assert scheduled.status_code == 201, scheduled.text
engine.dispose()
'''
    completed = subprocess.run(
        [sys.executable, "-c", seed], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr

    # Konumsal ``-1`` yerine AÇIK hedef revizyon: bu dosya FAZ-1 migration'ının
    # (20260727_0029) downgrade + backfill davranışını doğrular, "head'in bir
    # altı"nı değil. FAZ-1'in üstüne başka bir migration yığıldığı anda ``-1``
    # sessizce yanlış revizyonu geri alır ve buradaki iddialar anlamsızlaşır.
    # 20260727_0028 = FAZ-1'in bir altı, yani hedef her zaman doğru.
    for direction in (["downgrade", "20260727_0028"], ["upgrade", "head"]):
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", *direction], cwd=BACKEND, env=env,
            capture_output=True, text=True, timeout=300,
        )
        assert completed.returncode == 0, (
            " ".join(direction) + "\n" + completed.stdout + "\n" + completed.stderr
        )

    verify = r'''
from sqlalchemy import create_engine, text
import os
engine = create_engine(os.environ["DATABASE_URL"])
with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT h.from_customer_id, h.to_customer_id, h.reason, m.customer_id "
        "FROM machine_ownership_history h "
        "JOIN machines m ON m.id=h.machine_id AND m.company_id=h.company_id"
    )).all()
    assert len(rows) == 1, rows
    from_id, to_id, reason, current = rows[0]
    assert from_id is None and int(to_id) == int(current)
    assert "göçü" in reason  # backfill marker, not a runtime row
    status = conn.execute(text("SELECT status FROM work_orders")).scalar_one()
    assert status == "OPEN", status  # downgrade re-based SCHEDULED
    scheduled_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(work_orders)"))]
    assert "scheduled_date" in scheduled_cols
engine.dispose()
'''
    completed = subprocess.run(
        [sys.executable, "-c", verify], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


_SMOKE = r'''
from fastapi.testclient import TestClient

from app.db import engine
from app.main import app

ADMIN_PW = 'ServisFaz1!123'


def admin_headers(client):
    """Resilient login for shared-DB PostgreSQL runs: the bootstrap password may
    already have been rotated by an earlier smoke in the same file."""
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    if login.status_code == 200:
        body = login.json()
        headers = {'Authorization':'Bearer '+body['access_token'],
                   'X-Company-ID':str(body['companies'][0]['id'])}
        changed = client.post('/api/auth/change-password', headers=headers,
                              json={'current_password':'admin123','new_password':ADMIN_PW})
        assert changed.status_code == 200, changed.text
        headers['Authorization'] = 'Bearer '+changed.json()['access_token']
        return headers, changed.json().get('user') or body['user'], body
    login = client.post('/api/auth/login', json={'username':'admin','password':ADMIN_PW})
    assert login.status_code == 200, login.text
    body = login.json()
    headers = {'Authorization':'Bearer '+body['access_token'],
               'X-Company-ID':str(body['companies'][0]['id'])}
    return headers, body['user'], body


with TestClient(app) as client:
    headers, admin_user, login_body = admin_headers(client)
    company_a = int(headers['X-Company-ID'])
    admin_id = int(admin_user['id'])

    customer = client.post('/api/customers', headers=headers, json={'name':'Servis F1 Müşteri 1'})
    assert customer.status_code == 201, customer.text
    customer_id = customer.json()['id']
    machine = client.post('/api/machines', headers=headers, json={
        'customer_id':customer_id, 'brand':'Sungur', 'model':'F1-100',
        'serial_number':'F1-SER-1', 'chassis_number':'F1-CHS-1',
        'working_hours':'100.00',
    })
    assert machine.status_code == 201, machine.text
    machine_id = machine.json()['id']

    # ---- Sayaç okumaları -----------------------------------------------------
    reading = client.post(f'/api/machines/{machine_id}/hour-readings', headers=headers,
                          json={'reading_hours':'120.50'})
    assert reading.status_code == 201, reading.text
    assert reading.json()['source'] == 'manual'
    assert reading.json()['reading_type'] == 'normal'
    detail = client.get(f'/api/machines/{machine_id}', headers=headers)
    assert str(detail.json()['working_hours']) in ('120.5', '120.50'), detail.text

    # Düşüş: normal türle fail-closed.
    decrease = client.post(f'/api/machines/{machine_id}/hour-readings', headers=headers,
                           json={'reading_hours':'80'})
    assert decrease.status_code == 422, decrease.text
    # Düzeltme türü açıklama olmadan kabul edilmez (şema 422).
    no_reason = client.post(f'/api/machines/{machine_id}/hour-readings', headers=headers,
                            json={'reading_hours':'80', 'reading_type':'correction'})
    assert no_reason.status_code == 422, no_reason.text
    corrected = client.post(f'/api/machines/{machine_id}/hour-readings', headers=headers,
                            json={'reading_hours':'110.00', 'reading_type':'correction',
                                  'reason':'Yanlış giriş düzeltmesi'})
    assert corrected.status_code == 201, corrected.text
    detail = client.get(f'/api/machines/{machine_id}', headers=headers)
    assert str(detail.json()['working_hours']) in ('110', '110.0', '110.00'), detail.text
    # Sayaç değişimi türü de düşüşe izin verir.
    replaced = client.post(f'/api/machines/{machine_id}/hour-readings', headers=headers,
                           json={'reading_hours':'5.00', 'reading_type':'meter_replacement',
                                 'reason':'Sayaç değişti'})
    assert replaced.status_code == 201, replaced.text
    # Anomali: açıklama zorunlu, artış serbest.
    anomaly_missing = client.post(f'/api/machines/{machine_id}/hour-readings', headers=headers,
                                  json={'reading_hours':'400', 'reading_type':'anomaly'})
    assert anomaly_missing.status_code == 422, anomaly_missing.text
    anomaly = client.post(f'/api/machines/{machine_id}/hour-readings', headers=headers,
                          json={'reading_hours':'400.00', 'reading_type':'anomaly',
                                'reason':'Beklenmedik sıçrama'})
    assert anomaly.status_code == 201, anomaly.text
    # NUMERIC(12,2) üst sınırı iki lehçede de 422.
    too_big = client.post(f'/api/machines/{machine_id}/hour-readings', headers=headers,
                          json={'reading_hours':'100000000000'})
    assert too_big.status_code == 422, too_big.text
    # Geçersiz tür.
    bad_type = client.post(f'/api/machines/{machine_id}/hour-readings', headers=headers,
                           json={'reading_hours':'500', 'reading_type':'guess'})
    assert bad_type.status_code == 422, bad_type.text

    # İş emri kaynaklı okuma: iş emri aynı makineye ait olmalı.
    work_order = client.post('/api/work-orders', headers=headers, json={
        'machine_id':machine_id, 'technician_id':admin_id, 'complaint':'Sayaç testi',
    })
    assert work_order.status_code == 201, work_order.text
    wo_id = work_order.json()['id']
    wo_reading = client.post(f'/api/machines/{machine_id}/hour-readings', headers=headers,
                             json={'reading_hours':'405.00', 'work_order_id':wo_id})
    assert wo_reading.status_code == 201, wo_reading.text
    assert wo_reading.json()['source'] == 'work_order'
    assert wo_reading.json()['work_order_no'] == work_order.json()['work_order_no']
    other_machine = client.post('/api/machines', headers=headers, json={
        'brand':'Sungur', 'model':'F1-200', 'serial_number':'F1-SER-2',
    })
    assert other_machine.status_code == 201, other_machine.text
    other_machine_id = other_machine.json()['id']
    mismatch = client.post(f'/api/machines/{other_machine_id}/hour-readings', headers=headers,
                           json={'reading_hours':'10', 'work_order_id':wo_id})
    assert mismatch.status_code == 409, mismatch.text

    listing = client.get(f'/api/machines/{machine_id}/hour-readings', headers=headers)
    assert listing.status_code == 200, listing.text
    readings = listing.json()
    assert len(readings) == 5, readings
    # Audit: okuma create + makine projection update kayıtları.
    history = client.get(f"/api/history/machine_hour_reading/{reading.json()['id']}", headers=headers)
    assert history.status_code == 200, history.text
    assert [entry['action'] for entry in history.json()] == ['create']

    # ---- Sahiplik tarihçesi ---------------------------------------------------
    ownership = client.get(f'/api/machines/{machine_id}/ownership-history', headers=headers)
    assert ownership.status_code == 200, ownership.text
    opening_rows = ownership.json()
    assert len(opening_rows) == 1, opening_rows
    assert opening_rows[0]['from_customer_id'] is None
    assert opening_rows[0]['to_customer_id'] == customer_id

    customer2 = client.post('/api/customers', headers=headers, json={'name':'Servis F1 Müşteri 2'})
    assert customer2.status_code == 201, customer2.text
    customer2_id = customer2.json()['id']

    # Açık iş emri varken transfer reddedilir.
    blocked = client.post(f'/api/machines/{machine_id}/transfer-ownership', headers=headers,
                          json={'to_customer_id':customer2_id})
    assert blocked.status_code == 409, blocked.text
    cancelled = client.patch(f'/api/work-orders/{wo_id}/status', headers=headers,
                             json={'status':'CANCELLED'})
    assert cancelled.status_code == 200, cancelled.text
    transferred = client.post(f'/api/machines/{machine_id}/transfer-ownership', headers=headers,
                              json={'to_customer_id':customer2_id, 'reason':'Satış'})
    assert transferred.status_code == 200, transferred.text
    assert transferred.json()['customer_id'] == customer2_id
    same_again = client.post(f'/api/machines/{machine_id}/transfer-ownership', headers=headers,
                             json={'to_customer_id':customer2_id})
    assert same_again.status_code == 409, same_again.text
    unknown = client.post(f'/api/machines/{machine_id}/transfer-ownership', headers=headers,
                          json={'to_customer_id':999999})
    assert unknown.status_code == 404, unknown.text
    ownership = client.get(f'/api/machines/{machine_id}/ownership-history', headers=headers)
    rows = ownership.json()
    assert len(rows) == 2, rows
    assert rows[0]['from_customer_id'] == customer_id
    assert rows[0]['to_customer_id'] == customer2_id
    assert rows[0]['reason'] == 'Satış'

    # Makine kartı (PUT) üzerinden sahip değişimi de tarihçeye yazılır.
    put_payload = {
        'customer_id':customer_id, 'brand':'Sungur', 'model':'F1-100',
        'serial_number':'F1-SER-1', 'chassis_number':'F1-CHS-1',
        'working_hours':'405.00', 'status':'active', 'is_active':True,
    }
    updated = client.put(f'/api/machines/{machine_id}', headers=headers, json=put_payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()['customer_id'] == customer_id
    ownership = client.get(f'/api/machines/{machine_id}/ownership-history', headers=headers)
    assert len(ownership.json()) == 3, ownership.text
    # Sahibi olmayan makineye transfer: from NULL satırı.
    orphan_transfer = client.post(f'/api/machines/{other_machine_id}/transfer-ownership',
                                  headers=headers, json={'to_customer_id':customer_id})
    assert orphan_transfer.status_code == 200, orphan_transfer.text
    orphan_rows = client.get(f'/api/machines/{other_machine_id}/ownership-history', headers=headers).json()
    assert len(orphan_rows) == 1 and orphan_rows[0]['from_customer_id'] is None

    # ---- Teknisyen profilleri -------------------------------------------------
    profile = client.post('/api/technician-profiles', headers=headers,
                          json={'user_id':admin_id, 'phone':'0555 111 22 33',
                                'specialty':'Hidrolik'})
    assert profile.status_code == 201, profile.text
    assert profile.json()['username'] == 'admin'
    duplicate = client.post('/api/technician-profiles', headers=headers,
                            json={'user_id':admin_id})
    assert duplicate.status_code == 409, duplicate.text
    ghost = client.post('/api/technician-profiles', headers=headers,
                        json={'user_id':999999})
    assert ghost.status_code == 400, ghost.text
    updated_profile = client.put(f"/api/technician-profiles/{profile.json()['id']}",
                                 headers=headers,
                                 json={'phone':'0555 999 88 77', 'specialty':'Motor',
                                       'active':False})
    assert updated_profile.status_code == 200, updated_profile.text
    assert updated_profile.json()['active'] is False
    profiles = client.get('/api/technician-profiles', headers=headers, params={'active':'all'})
    assert profiles.status_code == 200 and len(profiles.json()) == 1
    assert client.get('/api/technician-profiles', headers=headers).json() == []
    # Profil opsiyoneldir: profili pasif olsa da teknisyen atanabilir (v1 davranışı).
    still_assignable = client.post('/api/work-orders', headers=headers, json={
        'machine_id':other_machine_id, 'technician_id':admin_id,
    })
    assert still_assignable.status_code == 201, still_assignable.text
    assignable_wo_id = still_assignable.json()['id']

    # ---- SCHEDULED durumu -----------------------------------------------------
    missing_date = client.post('/api/work-orders', headers=headers, json={
        'machine_id':machine_id, 'technician_id':admin_id, 'status':'SCHEDULED',
    })
    assert missing_date.status_code == 422, missing_date.text
    bad_initial = client.post('/api/work-orders', headers=headers, json={
        'machine_id':machine_id, 'technician_id':admin_id, 'status':'COMPLETED',
    })
    assert bad_initial.status_code == 422, bad_initial.text
    scheduled = client.post('/api/work-orders', headers=headers, json={
        'machine_id':machine_id, 'technician_id':admin_id, 'status':'SCHEDULED',
        'scheduled_date':'2026-08-01T09:00:00Z', 'complaint':'Periyodik bakım',
    })
    assert scheduled.status_code == 201, scheduled.text
    scheduled_id = scheduled.json()['id']
    assert scheduled.json()['status'] == 'SCHEDULED'
    assert scheduled.json()['scheduled_date'] is not None

    # Planlanmış iş emri: parça eklenemez, faturalanamaz.
    warehouses = client.get('/api/warehouses', headers=headers)
    warehouse_id = (warehouses.json() or [{}])[0].get('id')
    product = client.post('/api/products', headers=headers,
                          json={'name':'F1 Parça', 'price':'10', 'stock':'5'})
    part_blocked = client.post(f'/api/work-orders/{scheduled_id}/parts', headers=headers,
                               json={'product_id':(product.json() or {}).get('id', 1),
                                     'warehouse_id':warehouse_id or 1,
                                     'quantity':'1', 'unit_price':'10'})
    assert part_blocked.status_code == 409, part_blocked.text
    billing_blocked = client.get(f'/api/work-orders/{scheduled_id}/invoice', headers=headers)
    assert billing_blocked.status_code == 409, billing_blocked.text
    invoice_blocked = client.post('/api/invoices/generate', headers=headers,
                                  json={'work_order_id':scheduled_id})
    assert invoice_blocked.status_code == 409, invoice_blocked.text

    # Geçiş grafiği: SCHEDULED -> IN_PROGRESS yok; OPEN -> SCHEDULED yok.
    skip = client.patch(f'/api/work-orders/{scheduled_id}/status', headers=headers,
                        json={'status':'IN_PROGRESS'})
    assert skip.status_code == 409, skip.text
    back_to_scheduled = client.patch(f'/api/work-orders/{assignable_wo_id}/status',
                                     headers=headers, json={'status':'SCHEDULED'})
    assert back_to_scheduled.status_code == 409, back_to_scheduled.text
    # PUT: planlanmış iş emrinde scheduled_date silinemez.
    wo_put = {
        'machine_id':machine_id, 'technician_id':admin_id, 'priority':'NORMAL',
        'complaint':'Periyodik bakım',
    }
    unschedule = client.put(f'/api/work-orders/{scheduled_id}', headers=headers, json=wo_put)
    assert unschedule.status_code == 422, unschedule.text
    reschedule = client.put(f'/api/work-orders/{scheduled_id}', headers=headers,
                            json={**wo_put, 'scheduled_date':'2026-08-02T10:00:00Z'})
    assert reschedule.status_code == 200, reschedule.text
    opened = client.patch(f'/api/work-orders/{scheduled_id}/status', headers=headers,
                          json={'status':'OPEN'})
    assert opened.status_code == 200, opened.text
    assert opened.json()['status'] == 'OPEN'
    # İkinci planlı iş emri iptal yolunu doğrular.
    scheduled2 = client.post('/api/work-orders', headers=headers, json={
        'machine_id':machine_id, 'technician_id':admin_id, 'status':'SCHEDULED',
        'scheduled_date':'2026-08-03T09:00:00Z',
    })
    assert scheduled2.status_code == 201, scheduled2.text
    cancelled2 = client.patch(f"/api/work-orders/{scheduled2.json()['id']}/status",
                              headers=headers, json={'status':'CANCELLED'})
    assert cancelled2.status_code == 200, cancelled2.text
    assert cancelled2.json()['cancelled_at'] is not None
    by_status = client.get('/api/work-orders', headers=headers, params={'status':'SCHEDULED'})
    assert by_status.status_code == 200, by_status.text
    assert all(item['status'] == 'SCHEDULED' for item in by_status.json()['items'])

    # ---- Tenant izolasyonu ----------------------------------------------------
    company_b = client.post('/api/companies', headers=headers, json={'name':'Servis F1 Firma B'})
    assert company_b.status_code == 201, company_b.text
    headers_b = {**headers, 'X-Company-ID':str(company_b.json()['id'])}
    assert client.get(f'/api/machines/{machine_id}/hour-readings', headers=headers_b).status_code == 404
    assert client.post(f'/api/machines/{machine_id}/hour-readings', headers=headers_b,
                       json={'reading_hours':'1'}).status_code == 404
    assert client.get(f'/api/machines/{machine_id}/ownership-history', headers=headers_b).status_code == 404
    assert client.post(f'/api/machines/{machine_id}/transfer-ownership', headers=headers_b,
                       json={'to_customer_id':customer_id}).status_code == 404
    # B firmasının müşterisine A'dan transfer: müşteri A kapsamında yok -> 404.
    customer_b = client.post('/api/customers', headers=headers_b, json={'name':'B Müşterisi'})
    assert customer_b.status_code == 201, customer_b.text
    cross = client.post(f'/api/machines/{machine_id}/transfer-ownership', headers=headers,
                        json={'to_customer_id':customer_b.json()['id']})
    assert cross.status_code == 404, cross.text
    assert client.get('/api/technician-profiles', headers=headers_b).json() == []

    # ---- RBAC -----------------------------------------------------------------
    report_user = client.post('/api/users', headers=headers, json={
        'username':'faz1_rapor', 'display_name':'Faz1 Rapor',
        'password':'Faz1Rapor123!', 'role':'rapor',
    })
    assert report_user.status_code == 201, report_user.text
    rl = client.post('/api/auth/login', json={'username':'faz1_rapor','password':'Faz1Rapor123!'})
    assert rl.status_code == 200, rl.text
    report_headers = {'Authorization':'Bearer '+rl.json()['access_token'],
                      'X-Company-ID':str(company_a)}
    rc = client.post('/api/auth/change-password', headers=report_headers,
                     json={'current_password':'Faz1Rapor123!','new_password':'Faz1Rapor456!'})
    assert rc.status_code == 200, rc.text
    report_headers['Authorization'] = 'Bearer '+rc.json()['access_token']
    assert client.get(f'/api/machines/{machine_id}/hour-readings', headers=report_headers).status_code == 200
    assert client.post(f'/api/machines/{machine_id}/hour-readings', headers=report_headers,
                       json={'reading_hours':'999'}).status_code == 403
    assert client.post(f'/api/machines/{machine_id}/transfer-ownership', headers=report_headers,
                       json={'to_customer_id':customer_id}).status_code == 403
    assert client.get('/api/technician-profiles', headers=report_headers).status_code == 200
    assert client.post('/api/technician-profiles', headers=report_headers,
                       json={'user_id':admin_id}).status_code == 403

engine.dispose()
'''
