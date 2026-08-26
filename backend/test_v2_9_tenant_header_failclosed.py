from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_invalid_company_header_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "tenant-failclosed.db"
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
bearer = 'Bearer ' + body['access_token']
default_company = body['companies'][0]['id']
changed = client.post('/api/auth/change-password', headers={'Authorization':bearer}, json={
    'current_password':'admin123','new_password':'AdminRot!2026x'
})
assert changed.status_code == 200, changed.text
bearer = 'Bearer ' + changed.json()['access_token']

# A valid, accessible company id continues to work.
ok = client.get('/api/products', headers={'Authorization':bearer, 'X-Company-ID':str(default_company)})
assert ok.status_code == 200, ok.text

# Present-but-malformed selectors must fail closed with 403, never fall back
# to the default company.
for bad in ('abc', '0', '-1', '1.5'):
    resp = client.get('/api/products', headers={'Authorization':bearer, 'X-Company-ID':bad})
    assert resp.status_code == 403, (bad, resp.status_code, resp.text)
    assert resp.json().get('code') == 'COMPANY_ACCESS_DENIED', (bad, resp.text)

# A blank selector is equivalent to sending no header (resolves the default),
# never a silent switch to a different company.
resp = client.get('/api/products', headers={'Authorization':bearer, 'X-Company-ID':' '})
assert resp.status_code == 200, resp.text

# A numerically valid but non-member company id also fails closed.
resp = client.get('/api/products', headers={'Authorization':bearer, 'X-Company-ID':'999999'})
assert resp.status_code == 403, resp.text
assert resp.json().get('code') == 'COMPANY_ACCESS_DENIED', resp.text

# An absent header still resolves the default company.
resp = client.get('/api/products', headers={'Authorization':bearer})
assert resp.status_code == 200, resp.text
client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke], cwd=BACKEND, env=env,
        text=True, capture_output=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
