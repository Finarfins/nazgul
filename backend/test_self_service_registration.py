from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_registration_verification_login_and_tenant_isolation(tmp_path: Path) -> None:
    database = tmp_path / "self-service.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["ENVIRONMENT"] = "test"

    scenario = r'''
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update

from app.auth import auth_rate_limits, email_verification_tokens, token_digest, users, utcnow
from app.db import SessionLocal
from app.main import app
from app.notifications.schema import notifications
from app.tenancy import companies, memberships
from time import monotonic
import app.routers.auth as auth_router

client = TestClient(app, raise_server_exceptions=False)
hash_durations = []
real_hash_password = auth_router.hash_password
def measured_hash(password):
    started = monotonic()
    result = real_hash_password(password)
    hash_durations.append(monotonic() - started)
    return result
auth_router.hash_password = measured_hash

def clear_limits():
    with SessionLocal.begin() as db:
        db.execute(delete(auth_rate_limits))

def register(email, company):
    response = client.post("/api/auth/register", json={
        "company_name": company,
        "email": email,
        "password": "GuvenliParola!2026",
        "password_confirmation": "GuvenliParola!2026",
        "display_name": "Kurucu Kullanıcı",
        "phone": "5361234567",
        "terms_accepted": True,
    })
    clear_limits()
    return response

def token_for(email):
    import json
    from urllib.parse import parse_qs, urlparse
    with SessionLocal() as db:
        payload = db.execute(
            select(notifications.c.payload)
            .where(notifications.c.recipient == email)
            .order_by(notifications.c.id.desc())
            .limit(1)
        ).scalar_one()
    link = json.loads(payload)["verification_url"]
    return parse_qs(urlparse(link).query)["token"][0]

started = monotonic()
first = register("ilk@example.com", "Birinci Firma")
new_duration = monotonic() - started
new_hash_duration = hash_durations[-1]
assert first.status_code == 200, first.text
assert first.json() == {"message": "Doğrulama e-postası gönderildi."}
first_token = token_for("ilk@example.com")

with SessionLocal() as db:
    stored = db.execute(
        select(email_verification_tokens.c.token_hash)
        .join(users, users.c.id == email_verification_tokens.c.user_id)
        .where(users.c.email == "ilk@example.com")
    ).scalar_one()
    assert stored == token_digest(first_token)
    assert stored != first_token
    assert db.execute(
        select(func.count()).select_from(notifications).where(
            notifications.c.recipient == "ilk@example.com"
        )
    ).scalar_one() == 1

blocked = client.post("/api/auth/login", json={
    "username": "ilk@example.com", "password": "GuvenliParola!2026"
})
assert blocked.status_code == 403, blocked.text
assert blocked.json()["detail"]["code"] == "EMAIL_VERIFICATION_REQUIRED"

resent = client.post("/api/auth/resend-verification", json={"email": "ilk@example.com"})
assert resent.status_code == 200, resent.text
resent_token = token_for("ilk@example.com")
assert resent_token != first_token
assert client.get("/api/auth/verify-email", params={"token": first_token}).status_code == 400

verified = client.get("/api/auth/verify-email", params={"token": resent_token})
assert verified.status_code == 200, verified.text
reused = client.get("/api/auth/verify-email", params={"token": resent_token})
assert reused.status_code == 400, reused.text

login = client.post("/api/auth/login", json={
    "username": "ilk@example.com", "password": "GuvenliParola!2026"
})
assert login.status_code == 200, login.text
assert [company["name"] for company in login.json()["companies"]] == ["Birinci Firma"]

started = monotonic()
duplicate = register("ilk@example.com", "Kopya Firma")
existing_duration = monotonic() - started
existing_hash_duration = hash_durations[-1]
assert duplicate.status_code == 200, duplicate.text
assert duplicate.json() == first.json()
assert abs(new_duration - existing_duration) < 0.12, (new_duration, existing_duration)
assert new_hash_duration > 0 and existing_hash_duration > 0
print(
    f"HASH_NEW_MS={new_hash_duration * 1000:.2f} "
    f"HASH_EXISTING_MS={existing_hash_duration * 1000:.2f} "
    f"HASH_CALLS={len(hash_durations)}"
)
with SessionLocal() as db:
    assert db.execute(select(func.count()).select_from(companies).where(
        companies.c.name == "Kopya Firma"
    )).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(users).where(
        users.c.email == "ilk@example.com"
    )).scalar_one() == 1
    assert db.execute(select(func.count()).select_from(notifications).where(
        notifications.c.recipient == "ilk@example.com",
        notifications.c.type == "REGISTRATION_ATTEMPT",
    )).scalar_one() == 1

second = register("ikinci@example.com", "İkinci Firma")
assert second.status_code == 200, second.text
second_token = token_for("ikinci@example.com")
assert client.get("/api/auth/verify-email", params={"token": second_token}).status_code == 200
second_login = client.post("/api/auth/login", json={
    "username": "ikinci@example.com", "password": "GuvenliParola!2026"
})
assert second_login.status_code == 200, second_login.text
assert [company["name"] for company in second_login.json()["companies"]] == ["İkinci Firma"]

first_company_id = login.json()["companies"][0]["id"]
second_company_id = second_login.json()["companies"][0]["id"]
with SessionLocal() as db:
    assert db.execute(select(companies.c.tax_number).where(
        companies.c.id == first_company_id
    )).scalar_one() is None
customer = {"phone": None, "email": None, "address": None, "tax_number": None,
            "opening_balance": 0, "owner_name": None, "risk_limit": 0,
            "payment_term_days": 0, "notes": None, "is_active": True}
first_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
second_headers = {"Authorization": f"Bearer {second_login.json()['access_token']}"}
ayse = client.post("/api/customers", json={**customer, "name": "Ayşe Test"}, headers=first_headers)
mehmet = client.post("/api/customers", json={**customer, "name": "Mehmet Test"}, headers=second_headers)
assert ayse.status_code == 201, ayse.text
assert mehmet.status_code == 201, mehmet.text
listed = client.get("/api/customers", headers=first_headers)
assert [item["name"] for item in listed.json()] == ["Ayşe Test"]
cross_header = client.get("/api/customers", headers={
    **first_headers, "X-Company-ID": str(second_company_id)
})
assert cross_header.status_code == 403, cross_header.text
cross_detail = client.get(f"/api/customers/{mehmet.json()['id']}", headers=first_headers)
assert cross_detail.status_code in {403, 404}, cross_detail.text
assert "Mehmet Test" not in cross_detail.text
settings_update = client.put("/api/company-settings", headers=first_headers, json={
    "negative_stock_policy": "block",
    "credit_limit_policy": "block",
    "tax_number": "1234567891",
})
assert settings_update.status_code == 200, settings_update.text
assert settings_update.json()["warning"] == "VKN doğrulanamadı, ayarlardan düzeltebilirsiniz."
with SessionLocal() as db:
    assert db.execute(select(companies.c.tax_number).where(
        companies.c.id == first_company_id
    )).scalar_one() == "1234567891"

expired = register("expired@example.com", "Süresi Dolan Firma")
assert expired.status_code == 200, expired.text
expired_token = token_for("expired@example.com")
with SessionLocal.begin() as db:
    db.execute(update(email_verification_tokens).where(
        email_verification_tokens.c.token_hash == token_digest(expired_token)
    ).values(expires_at=utcnow()-timedelta(seconds=1)))
assert client.get("/api/auth/verify-email", params={"token": expired_token}).status_code == 400

import app.routers.auth as auth_router
original = auth_router.create_verification_token
auth_router.create_verification_token = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced"))
failed = register("rollback@example.com", "Rollback Firma")
auth_router.create_verification_token = original
assert failed.status_code == 500, failed.text
with SessionLocal() as db:
    assert db.execute(select(func.count()).select_from(companies).where(
        companies.c.name == "Rollback Firma"
    )).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(users).where(
        users.c.email == "rollback@example.com"
    )).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(memberships).join(
        users, users.c.id == memberships.c.user_id
    ).where(users.c.email == "rollback@example.com")).scalar_one() == 0

client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", scenario],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    print(result.stdout.strip())


def test_registration_rate_limit(tmp_path: Path) -> None:
    database = tmp_path / "rate-limit.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["ENVIRONMENT"] = "test"
    scenario = r'''
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
for index in range(5):
    response = client.post("/api/auth/register", json={
        "company_name": f"Firma {index}",
        "email": f"user{index}@example.com",
        "password": "GuvenliParola!2026",
        "password_confirmation": "GuvenliParola!2026",
        "display_name": "Kurucu Kullanıcı",
        "phone": "5361234567",
        "terms_accepted": True,
    })
    assert response.status_code == 200, response.text
limited = client.post("/api/auth/register", json={
    "company_name": "Altıncı Firma",
    "email": "user5@example.com",
    "password": "GuvenliParola!2026",
    "password_confirmation": "GuvenliParola!2026",
    "display_name": "Kurucu Kullanıcı",
    "phone": "5361234567",
    "terms_accepted": True,
})
assert limited.status_code == 429, limited.text
client.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", scenario],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    print(result.stdout.strip())


def test_registration_email_rate_limit(tmp_path: Path) -> None:
    database = tmp_path / "email-rate-limit.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["ENVIRONMENT"] = "test"
    scenario = r'''
from fastapi.testclient import TestClient
from sqlalchemy import delete
from app.auth import auth_rate_limits
from app.db import SessionLocal
from app.main import app

payload = {
    "company_name": "Ortak Firma",
    "email": "ortak@example.com",
    "password": "GuvenliParola!2026",
    "password_confirmation": "GuvenliParola!2026",
    "display_name": "Kurucu Kullanıcı",
    "phone": "5361234567",
    "terms_accepted": True,
}
with TestClient(app) as client:
    for index in range(5):
        response = client.post(
            "/api/auth/register",
            json={**payload, "company_name": f"Ortak Firma {index}"},
            headers={"X-Forwarded-For": f"198.51.100.{index + 1}"},
        )
        assert response.status_code == 200, response.text
        with SessionLocal.begin() as db:
            db.execute(delete(auth_rate_limits).where(auth_rate_limits.c.action == "register"))
    limited = client.post(
        "/api/auth/register",
        json=payload,
        headers={"X-Forwarded-For": "198.51.100.99"},
    )
    assert limited.status_code == 429, limited.text
    print(f"EMAIL_RATE_LIMIT status={limited.status_code} body={limited.json()}")
'''
    result = subprocess.run(
        [sys.executable, "-c", scenario],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    print(result.stdout.strip())


def test_register_endpoint_turnstile_invalid_valid_and_production_startup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "turnstile.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["ENVIRONMENT"] = "test"
    scenario = r'''
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.config import Settings
from app.main import app
from app.routers import auth

class Response:
    def __init__(self, success):
        self.success = success
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return None
    def read(self):
        return b'{"success": true}' if self.success else b'{"success": false}'

payload = {
    "company_name": "Turnstile Firma",
    "email": "turnstile@example.com",
    "password": "GuvenliParola!2026",
    "password_confirmation": "GuvenliParola!2026",
    "display_name": "Kurucu Kullanıcı",
    "phone": "5361234567",
    "terms_accepted": True,
    "turnstile_token": "token",
}
auth.settings.turnstile_secret_key = "test-secret"
with TestClient(app) as client:
    auth.urlrequest.urlopen = lambda *_args, **_kwargs: Response(False)
    invalid = client.post("/api/auth/register", json=payload)
    assert invalid.status_code == 400, invalid.text
    print(f"TURNSTILE_INVALID status={invalid.status_code} body={invalid.json()}")

    auth.urlrequest.urlopen = lambda *_args, **_kwargs: Response(True)
    valid = client.post("/api/auth/register", json=payload)
    assert valid.status_code == 200, valid.text
    print(f"TURNSTILE_VALID status={valid.status_code} body={valid.json()}")

try:
    Settings(
        environment="production",
        cookie_secure=True,
        bootstrap_admin_password="GuvenliBootstrap!2026",
        trusted_proxy_cidrs="172.18.0.0/16",
        turnstile_secret_key="",
    )
except ValidationError as exc:
    assert "Production ortamında TURNSTILE_SECRET_KEY zorunludur" in str(exc)
    print("PRODUCTION_STARTUP_ERROR=" + str(exc).replace("\n", " | "))
else:
    raise AssertionError("Production Turnstile anahtarı olmadan başlamamalı")
'''
    result = subprocess.run(
        [sys.executable, "-c", scenario],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    print(result.stdout.strip())
