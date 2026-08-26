from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_mutation_audit_records_success_and_early_denials(tmp_path: Path) -> None:
    database = tmp_path / "audit-traceability.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["COOKIE_SECURE"] = "false"

    smoke = r'''
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.auth import audit_logs
from app.db import SessionLocal

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
company_id = login.json()['companies'][0]['id']
csrf = client.cookies.get('yhp_csrf_token')
headers = {'X-Company-ID': str(company_id), 'X-CSRF-Token': csrf, 'X-Request-ID': 'audit-success-1'}
changed = client.post('/api/auth/change-password', headers=headers, json={
    'current_password':'admin123', 'new_password':'AdminRot!2026x'
})
assert changed.status_code == 200, changed.text
headers['Authorization'] = 'Bearer ' + changed.json()['access_token']
assert changed.headers['X-Request-ID'] == 'audit-success-1'

# Missing CSRF is rejected before the router runs, but must still be audited.
denied = client.post('/api/companies', headers={
    'X-Company-ID': str(company_id), 'X-Request-ID': 'audit-denied-1'
}, json={'name':'Should not exist'})
assert denied.status_code == 403, denied.text
assert denied.headers['X-Request-ID'] == 'audit-denied-1'

with SessionLocal() as db:
    rows = db.execute(select(audit_logs).where(
        audit_logs.c.request_id.in_(['audit-success-1', 'audit-denied-1'])
    )).mappings().all()
by_id = {row['request_id']: row for row in rows}
assert by_id['audit-success-1']['outcome'] == 'success', by_id
assert by_id['audit-success-1']['status_code'] == 200
assert by_id['audit-success-1']['auth_source'] == 'cookie'
assert by_id['audit-success-1']['duration_ms'] >= 0
assert by_id['audit-denied-1']['outcome'] == 'denied'
assert by_id['audit-denied-1']['failure_reason'] == 'CSRF_FAILED'
assert by_id['audit-denied-1']['status_code'] == 403
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke], cwd=BACKEND, env=env,
        text=True, capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
