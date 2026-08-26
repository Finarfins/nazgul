from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
FRONTEND_SRC = BACKEND.parent / "frontend" / "src"


def test_cookie_session_csrf_rotation_and_bearer_compatibility(tmp_path: Path) -> None:
    database = tmp_path / "session-security.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["COOKIE_SECURE"] = "false"
    env["ACCESS_TOKEN_MINUTES"] = "15"
    env["REFRESH_TOKEN_DAYS"] = "14"

    smoke = r'''
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
body = login.json()
assert body['access_token']
set_cookies = login.headers.get_list('set-cookie')
access_cookie = next(value for value in set_cookies if value.startswith('yhp_access_token='))
refresh_cookie = next(value for value in set_cookies if value.startswith('yhp_refresh_token='))
csrf_cookie = next(value for value in set_cookies if value.startswith('yhp_csrf_token='))
assert 'HttpOnly' in access_cookie
assert 'HttpOnly' in refresh_cookie
assert 'HttpOnly' not in csrf_cookie
assert 'SameSite=lax' in access_cookie
assert 'Path=/api/auth' in refresh_cookie
assert client.get('/api/auth/me').status_code == 200

# Unsafe cookie-authenticated requests require the double-submit CSRF header.
blocked = client.post('/api/auth/change-password', json={
    'current_password':'admin123', 'new_password':'AdminRot!2026x'
})
assert blocked.status_code == 403, blocked.text
csrf = client.cookies.get('yhp_csrf_token')
changed = client.post('/api/auth/change-password', headers={'X-CSRF-Token':csrf}, json={
    'current_password':'admin123', 'new_password':'AdminRot!2026x'
})
assert changed.status_code == 200, changed.text

# Every refresh consumes the old token and returns a new family member.
old_refresh = client.cookies.get('yhp_refresh_token')
old_csrf = client.cookies.get('yhp_csrf_token')
refreshed = client.post('/api/auth/refresh', headers={'X-CSRF-Token':old_csrf})
assert refreshed.status_code == 200, refreshed.text
new_refresh = client.cookies.get('yhp_refresh_token')
new_csrf = client.cookies.get('yhp_csrf_token')
assert new_refresh and new_refresh != old_refresh
assert new_csrf and new_csrf != old_csrf

# Replaying the consumed token revokes every still-active token in its family.
replay = TestClient(app)
replay.cookies.set('yhp_refresh_token', old_refresh, domain='testserver.local', path='/api/auth')
replay.cookies.set('yhp_csrf_token', new_csrf, domain='testserver.local', path='/')
replayed = replay.post('/api/auth/refresh', headers={'X-CSRF-Token':new_csrf})
assert replayed.status_code == 401, replayed.text
family_revoked = client.post('/api/auth/refresh', headers={'X-CSRF-Token':new_csrf})
assert family_revoked.status_code == 401, family_revoked.text

# Existing integrations can continue to use the access token as a Bearer token
# without CSRF because the credential is not ambiently attached by a browser.
api_client = TestClient(app)
api_login = api_client.post('/api/auth/login', json={'username':'admin','password':'AdminRot!2026x'})
assert api_login.status_code == 200, api_login.text
bearer = api_login.json()['access_token']
api_client.cookies.clear()
me = api_client.get('/api/auth/me', headers={'Authorization':'Bearer '+bearer})
assert me.status_code == 200, me.text
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_frontend_does_not_persist_access_token() -> None:
    api_source = (FRONTEND_SRC / "api.ts").read_text(encoding="utf-8")
    auth_source = (FRONTEND_SRC / "AuthContext.tsx").read_text(encoding="utf-8")
    combined = api_source + "\n" + auth_source

    assert "localStorage.setItem('yhp_token'" not in combined
    assert 'localStorage.setItem("yhp_token"' not in combined
    assert "Authorization=`Bearer ${token}`" not in combined
    assert "withCredentials:true" in api_source
    assert "X-CSRF-Token" in api_source
    assert "/auth/refresh" in api_source

def test_samesite_none_requires_secure_cookie() -> None:
    from pydantic import ValidationError

    from app.config import Settings

    try:
        Settings(_env_file=None, cookie_samesite="none", cookie_secure=False)
    except ValidationError:
        pass
    else:
        raise AssertionError("SameSite=None, Secure olmadan kabul edilmemelidir")

    settings = Settings(_env_file=None, cookie_samesite="none", cookie_secure=True)
    assert settings.cookie_secure is True
    assert settings.cookie_samesite == "none"



def test_forced_password_change_session_cannot_refresh(tmp_path: Path) -> None:
    database = tmp_path / "forced-password-refresh.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["COOKIE_SECURE"] = "false"

    smoke = r'''
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import SessionLocal
from app.auth import auth_refresh_tokens

client = TestClient(app)
login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
assert login.status_code == 200, login.text
assert login.json()['user']['must_change_password'] is True

# Normal protected endpoints remain blocked while password change is required.
blocked = client.get('/api/products')
assert blocked.status_code == 403, blocked.text
assert blocked.json()['code'] == 'PASSWORD_CHANGE_REQUIRED'

csrf = client.cookies.get('yhp_csrf_token')
refresh = client.post('/api/auth/refresh', headers={'X-CSRF-Token':csrf})
assert refresh.status_code == 403, refresh.text
assert refresh.json()['detail']['code'] == 'PASSWORD_CHANGE_REQUIRED'
cleared = refresh.headers.get_list('set-cookie')
assert any(v.startswith('yhp_refresh_token=') and ('Max-Age=0' in v or 'expires=' in v.lower()) for v in cleared), cleared
assert any(v.startswith('yhp_access_token=') and ('Max-Age=0' in v or 'expires=' in v.lower()) for v in cleared), cleared

with SessionLocal() as db:
    active = db.execute(
        select(auth_refresh_tokens.c.id).where(auth_refresh_tokens.c.revoked_at.is_(None))
    ).all()
assert active == [], active
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
