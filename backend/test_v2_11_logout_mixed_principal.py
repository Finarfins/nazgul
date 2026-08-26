from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def test_bearer_logout_does_not_revoke_a_foreign_refresh_family(tmp_path: Path) -> None:
    """F5: a bearer-authenticated logout must not revoke a cookie session.

    require_logout_csrf skips CSRF when a bearer token is present, and logout()
    used to revoke whatever refresh-cookie family was attached — so a bearer
    request (principal A) carrying another session's refresh cookie (principal B)
    could destroy B's refresh session with no CSRF proof. After the fix a bearer
    logout revokes only the bearer access token; a refresh family is revoked only
    by a cookie-authenticated logout with valid CSRF.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'logout.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    smoke = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.db import SessionLocal
from app.config import settings

CSRF=settings.csrf_cookie_name

def nonrevoked_refresh():
    with SessionLocal() as db:
        return db.execute(text("SELECT COUNT(*) FROM auth_refresh_tokens WHERE revoked_at IS NULL")).scalar_one()

PW='LogoutMix123!'
with TestClient(app) as boot:
    login=boot.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json()
    cid=login['companies'][0]['id']
    h={'Authorization':'Bearer '+login['access_token'],'X-Company-ID':str(cid)}
    # change_password revokes all of the user's existing tokens, so families A/B/C below start clean
    boot.post('/api/auth/change-password',headers=h,json={'current_password':'admin123','new_password':PW})

base=nonrevoked_refresh()  # any pre-existing families; assert relative to this

# Session A: a bearer client
clientA=TestClient(app)
la=clientA.post('/api/auth/login',json={'username':'admin','password':PW}).json()
token_a=la['access_token']; cid=la['companies'][0]['id']

# Session B: a cookie client (holds B's refresh-cookie family F_B)
clientB=TestClient(app)
clientB.post('/api/auth/login',json={'username':'admin','password':PW})

assert nonrevoked_refresh()==base+2, ('setup: expected F_A + F_B', nonrevoked_refresh(), base)

# --- Attack: bearer A logs out while carrying B's cookies (CSRF is skipped) ---
resp=clientB.post('/api/auth/logout',headers={'Authorization':'Bearer '+token_a,'X-Company-ID':str(cid)})
assert resp.status_code==204, resp.text

# Both refresh families survive: bearer logout must not revoke a foreign family
assert nonrevoked_refresh()==base+2, ('bearer logout revoked a foreign refresh family', nonrevoked_refresh(), base)
# ...but the bearer access token itself is revoked
me=clientB.get('/api/auth/me',headers={'Authorization':'Bearer '+token_a,'X-Company-ID':str(cid)})
assert me.status_code==401, ('bearer token should be revoked', me.status_code)

# --- Positive: a cookie logout with valid CSRF still revokes ITS OWN family ---
clientC=TestClient(app)
clientC.post('/api/auth/login',json={'username':'admin','password':PW})
assert nonrevoked_refresh()==base+3, ('setup: F_A + F_B + F_C', nonrevoked_refresh(), base)
csrf_c=clientC.cookies.get(CSRF)
assert csrf_c, ('missing csrf cookie',CSRF,dict(clientC.cookies))
r2=clientC.post('/api/auth/logout',headers={'X-CSRF-Token':csrf_c,'X-Company-ID':str(cid)})
assert r2.status_code==204, r2.text
# F_C revoked; F_A and F_B still alive
assert nonrevoked_refresh()==base+2, ('cookie logout should revoke its own family', nonrevoked_refresh(), base)
print('OK')
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=180
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
