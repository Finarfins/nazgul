from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_auth_user_audit_and_logout_on_clean_database(tmp_path: Path) -> None:
    database = tmp_path / "auth-audit.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)

    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
assert client.get('/api/customers').status_code == 401

login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
headers = {
    'Authorization': 'Bearer ' + body['access_token'],
    'X-Company-ID': str(body['companies'][0]['id']),
}

changed = client.post('/api/auth/change-password', headers=headers, json={
    'current_password':'admin123', 'new_password':'AdminRot!2026x'
})
assert changed.status_code == 200, changed.text
headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
assert client.get('/api/auth/me', headers=headers).status_code == 200

created = client.post('/api/users', headers=headers, json={
    'username':'depo_test', 'display_name':'Depo Test',
    'password':'DepoTest123!', 'role':'depo'
})
assert created.status_code == 201, created.text
users = client.get('/api/users', headers=headers)
assert users.status_code == 200, users.text
assert any(row['username'] == 'depo_test' for row in users.json())

customer = client.post('/api/customers', headers=headers, json={
    'name':'Audit Müşterisi', 'opening_balance':0, 'risk_limit':0,
    'payment_term_days':0, 'is_active':True
})
assert customer.status_code == 201, customer.text

audit = client.get('/api/audit', headers=headers)
assert audit.status_code == 200, audit.text
assert any(row.get('path') == '/api/customers' and row.get('action') == 'POST' for row in audit.json())

csrf_token = client.cookies.get('yhp_csrf_token')
logout = client.post(
    '/api/auth/logout',
    headers={**headers, 'x-csrf-token': csrf_token} if csrf_token else headers,
)
assert logout.status_code == 204, logout.text
assert client.get('/api/auth/me', headers=headers).status_code == 401
client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke], cwd=BACKEND, env=env,
        text=True, capture_output=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
