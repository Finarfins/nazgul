from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_manager_cannot_create_admin_on_clean_database(tmp_path: Path) -> None:
    database = tmp_path / "role-security.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    script = r'''
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
admin_login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert admin_login.status_code == 200, admin_login.text
admin_body = admin_login.json()
company_id = admin_body['companies'][0]['id']
admin_headers = {
    'Authorization': 'Bearer ' + admin_body['access_token'],
    'X-Company-ID': str(company_id),
}
password_change = client.post('/api/auth/change-password', headers=admin_headers, json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert password_change.status_code == 200, password_change.text
admin_headers['Authorization'] = 'Bearer ' + password_change.json()['access_token']
manager = client.post('/api/users', headers=admin_headers, json={
    'username':'v29_manager','display_name':'V29 Manager',
    'password':'ManagerInit!23','role':'yonetici',
})
assert manager.status_code == 201, manager.text
manager_login = client.post('/api/auth/login', json={
    'username':'v29_manager','password':'ManagerInit!23',
})
assert manager_login.status_code == 200, manager_login.text
manager_headers = {
    'Authorization': 'Bearer ' + manager_login.json()['access_token'],
    'X-Company-ID': str(company_id),
}
# The manager account is admin-created, so it must rotate its initial password
# before its role permissions are evaluated on protected calls.
manager_rotate = client.post('/api/auth/change-password', headers=manager_headers, json={'current_password':'ManagerInit!23','new_password':'ManagerRot!2026x'})
assert manager_rotate.status_code == 200, manager_rotate.text
manager_headers['Authorization'] = 'Bearer ' + manager_rotate.json()['access_token']
escalation = client.post('/api/users', headers=manager_headers, json={
    'username':'illicit_admin','display_name':'Illicit Admin',
    'password':'IllicitInit!23','role':'admin',
})
assert escalation.status_code == 403, escalation.text
peer_manager = client.post('/api/users', headers=manager_headers, json={
    'username':'illicit_manager','display_name':'Illicit Manager',
    'password':'IllicitMgr!2345','role':'yonetici',
})
assert peer_manager.status_code == 403, peer_manager.text
disable_admin = client.patch(
    f"/api/users/{admin_body['user']['id']}/status",
    headers=manager_headers,
    params={'is_active':'false'},
)
assert disable_admin.status_code == 403, disable_admin.text
normal_user = client.post('/api/users', headers=manager_headers, json={
    'username':'v29_reporter','display_name':'V29 Reporter',
    'password':'ReporterInit!23','role':'rapor',
})
assert normal_user.status_code == 201, normal_user.text
client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
