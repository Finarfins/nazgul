from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_new_user_must_change_initial_password(tmp_path: Path) -> None:
    database = tmp_path / "new-user-reset.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)

    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
admin_headers = {'Authorization':'Bearer '+body['access_token'],'X-Company-ID':str(body['companies'][0]['id'])}
changed = client.post('/api/auth/change-password', headers=admin_headers, json={
    'current_password':'admin123','new_password':'AdminRot!2026x'
})
assert changed.status_code == 200, changed.text
admin_headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

created = client.post('/api/users', headers=admin_headers, json={
    'username':'depo_kullanici','display_name':'Depo Kullanıcı',
    'password':'InitialPass!23','role':'depo',
})
assert created.status_code == 201, created.text

# The new account is flagged for a forced password change.
user_login = client.post('/api/auth/login', json={'username':'depo_kullanici','password':'InitialPass!23'})
assert user_login.status_code == 200, user_login.text
assert user_login.json()['user']['must_change_password'] is True, user_login.text
user_body = user_login.json()
user_headers = {'Authorization':'Bearer '+user_body['access_token'],'X-Company-ID':str(user_body['companies'][0]['id'])}

# Protected endpoints are blocked until the initial password is rotated.
blocked = client.get('/api/products', headers=user_headers)
assert blocked.status_code == 403, blocked.text
assert blocked.json()['code'] == 'PASSWORD_CHANGE_REQUIRED', blocked.text

rotated = client.post('/api/auth/change-password', headers=user_headers, json={
    'current_password':'InitialPass!23','new_password':'DepoRot!2026xy'
})
assert rotated.status_code == 200, rotated.text
user_headers['Authorization'] = 'Bearer ' + rotated.json()['access_token']

allowed = client.get('/api/products', headers=user_headers)
assert allowed.status_code == 200, allowed.text
client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke], cwd=BACKEND, env=env,
        text=True, capture_output=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
