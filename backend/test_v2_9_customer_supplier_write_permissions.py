from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_read_only_role_cannot_mutate_customers_or_suppliers(tmp_path: Path) -> None:
    database = tmp_path / "customer-supplier-permissions.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
admin_login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert admin_login.status_code == 200, admin_login.text
admin_body = admin_login.json()
admin_headers = {
    'Authorization': 'Bearer ' + admin_body['access_token'],
    'X-Company-ID': str(admin_body['companies'][0]['id']),
}
changed = client.post('/api/auth/change-password', headers=admin_headers, json={
    'current_password':'admin123', 'new_password':'AdminRot!2026x'
})
assert changed.status_code == 200, changed.text
admin_headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

created = client.post('/api/users', headers=admin_headers, json={
    'username':'readonly_report', 'display_name':'Readonly Report',
    'password':'ReportInit!23', 'role':'rapor'
})
assert created.status_code == 201, created.text

report_login = client.post('/api/auth/login', json={'username':'readonly_report','password':'ReportInit!23'})
assert report_login.status_code == 200, report_login.text
report_body = report_login.json()
report_headers = {
    'Authorization': 'Bearer ' + report_body['access_token'],
    'X-Company-ID': str(report_body['companies'][0]['id']),
}

# Admin-created accounts must complete the forced initial password rotation
# before they can reach protected endpoints.
report_rotate = client.post('/api/auth/change-password', headers=report_headers, json={
    'current_password':'ReportInit!23', 'new_password':'ReportRot!2026x'
})
assert report_rotate.status_code == 200, report_rotate.text
report_headers['Authorization'] = 'Bearer ' + report_rotate.json()['access_token']

# Baseline read access remains available.
assert client.get('/api/customers', headers=report_headers).status_code == 200
assert client.get('/api/suppliers', headers=report_headers).status_code == 200

customer_payload = {'name':'Yetkisiz Müşteri','opening_balance':0}
supplier_payload = {'name':'Yetkisiz Tedarikçi','opening_balance':0}
for method, path, payload in [
    ('post', '/api/customers', customer_payload),
    ('put', '/api/customers/1', customer_payload),
    ('delete', '/api/customers/1', None),
    ('post', '/api/suppliers', supplier_payload),
    ('put', '/api/suppliers/1', supplier_payload),
    ('delete', '/api/suppliers/1', None),
]:
    response = getattr(client, method)(path, headers=report_headers, **({'json': payload} if payload is not None else {}))
    assert response.status_code == 403, (method, path, response.status_code, response.text)

client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
