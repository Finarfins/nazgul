from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_login_bruteforce_lockout_and_recovery_on_clean_database(tmp_path: Path) -> None:
    database = tmp_path / "login-rate-limit.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)

    smoke = r'''
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.auth import login_attempts, utcnow
from app.db import SessionLocal
from app.main import app

client = TestClient(app)
credentials = {'username': 'admin', 'password': 'wrong-password'}

# The first five failures are deliberately indistinguishable from ordinary
# invalid credentials. The following request is blocked before password work.
for _ in range(5):
    response = client.post('/api/auth/login', json=credentials)
    assert response.status_code == 401, response.text

blocked = client.post('/api/auth/login', json=credentials)
assert blocked.status_code == 429, blocked.text
assert '15 dakika' in blocked.json()['detail']

# A correct password cannot bypass an active lock.
still_blocked = client.post('/api/auth/login', json={
    'username': 'admin', 'password': 'admin123'
})
assert still_blocked.status_code == 429, still_blocked.text

with SessionLocal() as db:
    row = db.execute(select(login_attempts)).mappings().one()
    assert row['username'] == 'admin'
    assert row['fail_count'] == 5
    assert row['locked_until'] is not None
    db.execute(
        update(login_attempts)
        .where(login_attempts.c.id == row['id'])
        .values(locked_until=utcnow() - timedelta(seconds=1))
    )
    db.commit()

# Once the lock expires, a successful login clears the failure record.
login = client.post('/api/auth/login', json={
    'username': 'admin', 'password': 'admin123'
})
assert login.status_code == 200, login.text

with SessionLocal() as db:
    assert db.execute(select(login_attempts)).first() is None

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
