"""Servis v2 FAZ-1 — work-order attachments (photo/signature).

Storage-root isolation, path-traversal fail-closed behavior, the streaming size
cap (413), tenant scoping, RBAC, soft delete and the invoice/terminal freeze are
all exercised through the real API. ``run_servis_v2_faz1_attachments_smoke`` is
shared with the PostgreSQL twin.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def run_servis_v2_faz1_attachments_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    with tempfile.TemporaryDirectory(prefix="sungur-data-") as data_dir:
        env["SUNGUR_DATA_DIR"] = data_dir
        # Small cap so the 413 path is cheap to exercise; the endpoint enforces
        # this limit itself while streaming.
        env["MAX_ATTACHMENT_UPLOAD_BYTES"] = "4096"
        completed = subprocess.run(
            [sys.executable, "-c", _SMOKE],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_servis_v2_faz1_attachments_sqlite(tmp_path: Path) -> None:
    run_servis_v2_faz1_attachments_smoke(
        f"sqlite:///{(tmp_path / 'servis-faz1-att.db').as_posix()}"
    )


_SMOKE = r'''
import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import engine
from app.main import app

DATA_DIR = Path(os.environ['SUNGUR_DATA_DIR']).resolve()
ADMIN_PW = 'ServisFaz1!123'


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


with TestClient(app) as client:
    headers, login_body = admin_headers(client)
    company_a = int(headers['X-Company-ID'])
    admin_id = int(login_body['user']['id'])

    customer = client.post('/api/customers', headers=headers, json={'name':'Ek Müşterisi'})
    assert customer.status_code == 201, customer.text
    machine = client.post('/api/machines', headers=headers, json={
        'customer_id':customer.json()['id'], 'brand':'Sungur', 'model':'EK-1',
        'serial_number':'EK-SER-1', 'chassis_number':'EK-CHS-1',
    })
    assert machine.status_code == 201, machine.text
    work_order = client.post('/api/work-orders', headers=headers, json={
        'machine_id':machine.json()['id'], 'technician_id':admin_id,
        'complaint':'Ek testi', 'actual_hours':'1', 'labor_rate':'100',
    })
    assert work_order.status_code == 201, work_order.text
    wo_id = work_order.json()['id']

    # ---- Yükleme --------------------------------------------------------------
    photo_bytes = b'JFIF-fake-image-bytes' * 10
    upload = client.post(f'/api/work-order-attachments/{wo_id}', headers=headers,
                         files={'file':('makine önü.jpg', photo_bytes, 'image/jpeg')},
                         data={'kind':'photo'})
    assert upload.status_code == 201, upload.text
    attachment = upload.json()
    assert attachment['kind'] == 'photo'
    assert attachment['file_size'] == len(photo_bytes)
    assert attachment['file_name'].endswith('.jpg')
    stored_dir = DATA_DIR / 'attachments' / str(company_a) / str(wo_id)
    stored_files = list(stored_dir.iterdir())
    assert len(stored_files) == 1, stored_files

    # Path traversal görünen ad: dosya her koşulda doğru dizinde saklanır.
    traversal = client.post(f'/api/work-order-attachments/{wo_id}', headers=headers,
                            files={'file':('..\\..\\evil.png', b'evil-bytes', 'image/png')},
                            data={'kind':'other'})
    assert traversal.status_code == 201, traversal.text
    assert traversal.json()['file_name'] == 'evil.png'
    assert len(list(stored_dir.iterdir())) == 2
    assert len(list((DATA_DIR / 'attachments').iterdir())) == 1  # yalnız firma dizini
    dot_name = client.post(f'/api/work-order-attachments/{wo_id}', headers=headers,
                           files={'file':('..', b'x', 'application/octet-stream')})
    assert dot_name.status_code == 422, dot_name.text

    bad_kind = client.post(f'/api/work-order-attachments/{wo_id}', headers=headers,
                           files={'file':('a.jpg', b'x', 'image/jpeg')},
                           data={'kind':'video'})
    assert bad_kind.status_code == 422, bad_kind.text
    empty = client.post(f'/api/work-order-attachments/{wo_id}', headers=headers,
                        files={'file':('empty.jpg', b'', 'image/jpeg')})
    assert empty.status_code == 422, empty.text
    oversize = client.post(f'/api/work-order-attachments/{wo_id}', headers=headers,
                           files={'file':('big.jpg', b'x' * 5000, 'image/jpeg')})
    assert oversize.status_code == 413, oversize.text
    # Reddedilen yüklemeler artık dosya bırakmaz.
    assert len(list(stored_dir.iterdir())) == 2

    # ---- İndirme + liste ------------------------------------------------------
    listing = client.get(f'/api/work-order-attachments/{wo_id}', headers=headers)
    assert listing.status_code == 200 and len(listing.json()) == 2, listing.text
    download = client.get(
        f"/api/work-order-attachments/{wo_id}/{attachment['id']}/download", headers=headers)
    assert download.status_code == 200, download.text
    assert download.content == photo_bytes
    assert download.headers['content-type'].startswith('image/jpeg')

    # ---- İmza türü ------------------------------------------------------------
    signature = client.post(f'/api/work-order-attachments/{wo_id}', headers=headers,
                            files={'file':('imza.png', b'signature-bytes', 'image/png')},
                            data={'kind':'signature'})
    assert signature.status_code == 201, signature.text

    # ---- Soft delete ----------------------------------------------------------
    deleted = client.delete(
        f"/api/work-order-attachments/{wo_id}/{attachment['id']}", headers=headers)
    assert deleted.status_code == 204, deleted.text
    assert len(client.get(f'/api/work-order-attachments/{wo_id}', headers=headers).json()) == 2
    gone = client.get(
        f"/api/work-order-attachments/{wo_id}/{attachment['id']}/download", headers=headers)
    assert gone.status_code == 404, gone.text
    # Dosya fiziksel olarak silinmez (FAZ-1 kararı).
    assert len(list(stored_dir.iterdir())) == 3
    again = client.delete(
        f"/api/work-order-attachments/{wo_id}/{attachment['id']}", headers=headers)
    assert again.status_code == 404, again.text

    # ---- Fatura sonrası tam kilit --------------------------------------------
    for status in ('IN_PROGRESS', 'COMPLETED'):
        moved = client.patch(f'/api/work-orders/{wo_id}/status', headers=headers,
                             json={'status':status})
        assert moved.status_code == 200, moved.text
    invoice = client.post('/api/invoices/generate', headers=headers,
                          json={'work_order_id':wo_id})
    assert invoice.status_code == 201, invoice.text
    frozen_upload = client.post(f'/api/work-order-attachments/{wo_id}', headers=headers,
                                files={'file':('gec.jpg', b'late', 'image/jpeg')})
    assert frozen_upload.status_code == 409, frozen_upload.text
    frozen_delete = client.delete(
        f"/api/work-order-attachments/{wo_id}/{signature.json()['id']}", headers=headers)
    assert frozen_delete.status_code == 409, frozen_delete.text
    # Okuma serbest kalır.
    assert client.get(f'/api/work-order-attachments/{wo_id}', headers=headers).status_code == 200

    # ---- Terminal durum -------------------------------------------------------
    cancelled_wo = client.post('/api/work-orders', headers=headers, json={
        'machine_id':machine.json()['id'], 'technician_id':admin_id,
    })
    assert cancelled_wo.status_code == 201, cancelled_wo.text
    cancel = client.patch(f"/api/work-orders/{cancelled_wo.json()['id']}/status",
                          headers=headers, json={'status':'CANCELLED'})
    assert cancel.status_code == 200, cancel.text
    terminal_upload = client.post(
        f"/api/work-order-attachments/{cancelled_wo.json()['id']}", headers=headers,
        files={'file':('iptal.jpg', b'x', 'image/jpeg')})
    assert terminal_upload.status_code == 409, terminal_upload.text

    # ---- Tenant izolasyonu ----------------------------------------------------
    company_b = client.post('/api/companies', headers=headers, json={'name':'Ek Firma B'})
    assert company_b.status_code == 201, company_b.text
    headers_b = {**headers, 'X-Company-ID':str(company_b.json()['id'])}
    assert client.get(f'/api/work-order-attachments/{wo_id}', headers=headers_b).status_code == 404
    assert client.get(
        f"/api/work-order-attachments/{wo_id}/{signature.json()['id']}/download",
        headers=headers_b).status_code == 404
    assert client.post(f'/api/work-order-attachments/{wo_id}', headers=headers_b,
                       files={'file':('b.jpg', b'x', 'image/jpeg')}).status_code == 404

    # ---- RBAC -----------------------------------------------------------------
    report_user = client.post('/api/users', headers=headers, json={
        'username':'ek_rapor', 'display_name':'Ek Rapor',
        'password':'EkRaporTest123!', 'role':'rapor',
    })
    assert report_user.status_code == 201, report_user.text
    rl = client.post('/api/auth/login', json={'username':'ek_rapor','password':'EkRaporTest123!'})
    assert rl.status_code == 200, rl.text
    report_headers = {'Authorization':'Bearer '+rl.json()['access_token'],
                      'X-Company-ID':str(company_a)}
    rc = client.post('/api/auth/change-password', headers=report_headers,
                     json={'current_password':'EkRaporTest123!','new_password':'EkRaporTest456!'})
    assert rc.status_code == 200, rc.text
    report_headers['Authorization'] = 'Bearer '+rc.json()['access_token']
    assert client.get(f'/api/work-order-attachments/{wo_id}', headers=report_headers).status_code == 200
    assert client.post(f'/api/work-order-attachments/{wo_id}', headers=report_headers,
                       files={'file':('r.jpg', b'x', 'image/jpeg')}).status_code == 403

engine.dispose()
'''
