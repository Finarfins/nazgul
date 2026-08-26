from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_core_routes_on_clean_database(tmp_path: Path) -> None:
    database = tmp_path / "core-routes.db"
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
headers = {
    'Authorization': 'Bearer ' + body['access_token'],
    'X-Company-ID': str(body['companies'][0]['id']),
}
changed = client.post('/api/auth/change-password', headers=headers, json={
    'current_password':'admin123', 'new_password':'AdminRot!2026x'
})
assert changed.status_code == 200, changed.text
headers['Authorization'] = 'Bearer ' + changed.json()['access_token']

for path in (
    '/api/health', '/api/dashboard',
    '/api/customers?sort=balance_desc', '/api/customers?sort=balance_asc',
    '/api/products?sort=stock_asc', '/api/products?sort=price_desc',
    '/api/orders', '/api/warehouses'
):
    response = client.get(path, headers=headers)
    assert response.status_code == 200, (path, response.status_code, response.text)
client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke], cwd=BACKEND, env=env,
        text=True, capture_output=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
