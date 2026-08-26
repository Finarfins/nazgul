"""SEC-AUTH-01: get_user_by_token opportunistically sweeps expired auth_tokens.

Presenting an already-expired bearer token must (a) authenticate nobody and
(b) purge every expired row so the short-lived table stays bounded, while a
still-valid lookup stays a read-only hot path that deletes nothing.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_expired_token_presentation_sweeps_all_expired_rows(tmp_path: Path) -> None:
    database = tmp_path / "expired-token-cleanup.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["COOKIE_SECURE"] = "false"

    smoke = r'''
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select
from app.main import app
from app.db import SessionLocal
from app.auth import auth_tokens, get_user_by_token, token_digest, users, utcnow

client = TestClient(app)
login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
assert login.status_code == 200, login.text

with SessionLocal() as db:
    uid = db.execute(select(users.c.id).where(users.c.username == "admin")).scalar()
    assert uid
    now = utcnow()

    def add(raw, expires_at):
        # Insert directly (NOT issue_token, which sweeps expired rows itself).
        db.execute(insert(auth_tokens).values(
            user_id=uid,
            token_hash=token_digest(raw),
            created_at=now - timedelta(hours=2),
            expires_at=expires_at,
        ))

    add("valid-extra-token", now + timedelta(hours=1))
    add("expired-token-a", now - timedelta(hours=1))
    add("expired-token-b", now - timedelta(minutes=1))
    db.commit()

    def expired_count():
        db.expire_all()
        return db.execute(
            select(func.count()).select_from(auth_tokens)
            .where(auth_tokens.c.expires_at <= utcnow())
        ).scalar()

    assert expired_count() == 2, "precondition: two expired rows present"

    # Valid lookup authenticates and must NOT delete anything (read-only path).
    user = get_user_by_token(db, "valid-extra-token")
    assert user and user["username"] == "admin"
    assert expired_count() == 2, "valid lookup must not sweep expired rows"

    # Expired lookup authenticates nobody AND sweeps every expired row.
    assert get_user_by_token(db, "expired-token-a") is None
    assert expired_count() == 0, "expired lookup must purge all expired rows"

    # The still-valid token survived the sweep and still authenticates.
    survivor = get_user_by_token(db, "valid-extra-token")
    assert survivor and survivor["username"] == "admin"

print("OK")
'''
    result = subprocess.run(
        [sys.executable, "-c", smoke],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout
