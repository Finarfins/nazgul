from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_password_change_revokes_all_credentials_and_issues_new_session(tmp_path: Path) -> None:
    database = tmp_path / "session-rotation.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["COOKIE_SECURE"] = "false"

    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
old_bearer = login.json()['access_token']
old_refresh = client.cookies.get('yhp_refresh_token')

changed = client.post('/api/auth/change-password',
    headers={'Authorization':'Bearer '+old_bearer},
    json={'current_password':'admin123','new_password':'AdminRot!2026x'})
assert changed.status_code == 200, changed.text
new_bearer = changed.json()['access_token']
assert new_bearer and new_bearer != old_bearer, changed.text

# The previously issued access token is revoked everywhere, not just rotated
# on the current client.
stale = TestClient(app)
stale.cookies.clear()
me_old = stale.get('/api/auth/me', headers={'Authorization':'Bearer '+old_bearer})
assert me_old.status_code == 401, me_old.text

# The freshly issued access token is a complete, working session.
fresh = TestClient(app)
fresh.cookies.clear()
me_new = fresh.get('/api/auth/me', headers={'Authorization':'Bearer '+new_bearer})
assert me_new.status_code == 200, me_new.text

# The old refresh family is revoked as well.
replay = TestClient(app)
replay.cookies.set('yhp_refresh_token', old_refresh, domain='testserver.local', path='/api/auth')
replay.cookies.set('yhp_csrf_token', 'x', domain='testserver.local', path='/')
replayed = replay.post('/api/auth/refresh', headers={'X-CSRF-Token':'x'})
assert replayed.status_code == 401, replayed.text
client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke], cwd=BACKEND, env=env,
        text=True, capture_output=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
