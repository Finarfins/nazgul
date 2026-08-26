"""F0-email — kayıt/doğrulama akışının HTTP seviyesindeki ikizleri.

``test_f0_email_dispatch.py`` motoru ve taşıyıcıyı doğrular; bu dosya uçların
davranışını doğrular: yutulan gönderim hatası kayıt akışını bozmaz, yanıt
gövdesi hesap var/yok ayrımı sızdırmaz, yeni token eskisinin kuyruk satırını
bastırır ve drenaj betiği kuyruğu boşaltır.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
SCRIPT = BACKEND / "scripts" / "dispatch_notifications.py"

_FAKE_SMTP = r'''
import smtplib

class FakeSMTP:
    sent = []
    failure = None

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, username, password):
        pass

    def send_message(self, message):
        if FakeSMTP.failure is not None:
            raise FakeSMTP.failure
        FakeSMTP.sent.append(message)

smtplib.SMTP = FakeSMTP
'''


def _env(database: Path, **extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["ENVIRONMENT"] = "test"
    env["PUBLIC_APP_URL"] = "https://erp.ornek.test"
    # Windows konsolu cp1254'e düşerse Türkçe çıktı çözülemez ve test gerçek
    # sonucu değil kodlama hatasını raporlar.
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def _spawn(command: list[str], env: dict[str, str], timeout: int = 180):
    return subprocess.run(
        command,
        cwd=BACKEND,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )


def _run(scenario: str, env: dict[str, str], timeout: int = 180):
    completed = _spawn([sys.executable, "-c", scenario], env, timeout)
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    return completed


# ===========================================================================
# T8 (yutulan except) + T9 (enumeration paritesi) + AK-7 + T14 + T15 + T16
# ===========================================================================
def test_registration_survives_dispatch_failure_and_keeps_queue_truthful(
    tmp_path: Path,
) -> None:
    database = tmp_path / "f0-email-http.db"
    scenario = (
        _FAKE_SMTP
        + r'''
import json
import smtplib
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth import auth_rate_limits
from app.db import SessionLocal
from app.main import app
from app.notifications.schema import notifications

client = TestClient(app, raise_server_exceptions=False)

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

def rows(email, type_=None):
    with SessionLocal() as db:
        query = select(notifications).where(notifications.c.recipient == email)
        if type_:
            query = query.where(notifications.c.type == type_)
        return [
            dict(row)
            for row in db.execute(query.order_by(notifications.c.id)).mappings()
        ]

# --- 1) Taşıyıcı patlıyor: kayıt yine 200, satır gerçeği söylüyor (T8) ---
smtplib.SMTP.failure = smtplib.SMTPServerDisconnected("baglanti koptu")
first = register("ilk@ornek.test", "Birinci Firma")
assert first.status_code == 200, first.text
assert first.json() == {"message": "Doğrulama e-postası gönderildi."}

queued = rows("ilk@ornek.test", "EMAIL_VERIFICATION")
assert len(queued) == 1, queued
row = queued[0]
assert row["status"] == "RETRY_SCHEDULED", row["status"]
assert row["attempt_count"] == 1
assert row["last_error"] == "Bildirim sağlayıcısına ulaşılamadı."
assert row["last_error_code"] == "NETWORK"
assert row["next_attempt_at"] is not None
assert bool(row["dispatch_armed"]) is True
assert row["approval_mode"] == "SYSTEM"
assert row["message_class"] is None and row["content_hash"] is None
# Sunucu metni ne yanıta ne satıra sızar.
assert "baglanti koptu" not in first.text
assert "baglanti koptu" not in json.dumps(row, default=str)

# --- 2) Enumeration paritesi (T9) ---
duplicate = register("ilk@ornek.test", "Kopya Firma")
assert duplicate.status_code == 200, duplicate.text
assert duplicate.json() == first.json()

attempts = rows("ilk@ornek.test", "REGISTRATION_ATTEMPT")
assert len(attempts) == 1, attempts

# --- 3) AK-7: aynı gün ikinci deneme yeni satır ÜRETMEZ ---
again = register("ilk@ornek.test", "Kopya Firma 2")
assert again.json() == first.json()
attempts = rows("ilk@ornek.test", "REGISTRATION_ATTEMPT")
assert len(attempts) == 1, attempts
assert attempts[0]["dedupe_key"].startswith("registration-attempt:")

# --- 4) T14: yeni token eski gönderilmemiş satırı bastırır ---
resent = client.post("/api/auth/resend-verification", json={"email": "ilk@ornek.test"})
assert resent.status_code == 200, resent.text
verifications = rows("ilk@ornek.test", "EMAIL_VERIFICATION")
assert len(verifications) == 2, verifications
assert verifications[0]["status"] == "CANCELLED"
assert bool(verifications[0]["dispatch_armed"]) is False
assert verifications[1]["status"] in {"RETRY_SCHEDULED", "FAILED"}

# --- 5) T16: resend hız limiti ---
codes = []
for _ in range(6):
    codes.append(
        client.post(
            "/api/auth/resend-verification", json={"email": "ilk@ornek.test"}
        ).status_code
    )
assert 429 in codes, codes

# --- 6) Taşıyıcı düzelince gönderim gerçekleşir ---
smtplib.SMTP.failure = None
clear_limits()
second = register("ikinci@ornek.test", "İkinci Firma")
assert second.status_code == 200, second.text
delivered = rows("ikinci@ornek.test", "EMAIL_VERIFICATION")[0]
assert delivered["status"] == "SENT", delivered
assert delivered["external_id"].startswith("<notification-")
assert len(smtplib.SMTP.sent) == 1
message = smtplib.SMTP.sent[0]
assert message["To"] == "ikinci@ornek.test"
assert "https://erp.ornek.test/eposta-dogrula?token=" in message.get_content()

# --- 7) T15: link tek kullanımlık ve çalışıyor ---
link = json.loads(delivered["payload"])["verification_url"]
token = link.split("token=", 1)[1]
assert client.get("/api/auth/verify-email", params={"token": token}).status_code == 200
assert client.get("/api/auth/verify-email", params={"token": token}).status_code == 400

client.close()
print("F0_EMAIL_HTTP_OK")
'''
    )
    completed = _run(
        scenario,
        _env(
            database,
            NOTIFICATION_PROVIDER="smtp",
            SMTP_HOST="smtp.ornek.test",
            SMTP_FROM_EMAIL="bildirim@ornek.test",
        ),
    )
    assert "F0_EMAIL_HTTP_OK" in completed.stdout


# ===========================================================================
# T1 — SMTP yapılandırılmamış: ağa çıkılmaz, satır kayıp değil
# ===========================================================================
def test_registration_without_smtp_never_opens_a_connection(tmp_path: Path) -> None:
    database = tmp_path / "f0-email-nosmtp.db"
    scenario = r'''
import smtplib
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.notifications.schema import notifications

class Exploding:
    def __init__(self, *args, **kwargs):
        raise AssertionError("SMTP bağlantısı kurulmamalıydı")

smtplib.SMTP = Exploding

client = TestClient(app, raise_server_exceptions=False)
response = client.post("/api/auth/register", json={
    "company_name": "SMTP'siz Firma",
    "email": "smtpsiz@ornek.test",
    "password": "GuvenliParola!2026",
    "password_confirmation": "GuvenliParola!2026",
    "display_name": "Kurucu Kullanıcı",
    "phone": "5361234567",
    "terms_accepted": True,
})
assert response.status_code == 200, response.text
assert response.json() == {"message": "Doğrulama e-postası gönderildi."}

with SessionLocal() as db:
    row = dict(db.execute(
        select(notifications).where(notifications.c.recipient == "smtpsiz@ornek.test")
    ).mappings().one())
assert row["status"] == "NONE", row
assert row["last_error"] is None
assert row["attempt_count"] == 1
client.close()
print("F0_EMAIL_NOSMTP_OK")
'''
    completed = _run(scenario, _env(database))
    assert "F0_EMAIL_NOSMTP_OK" in completed.stdout


# ===========================================================================
# Drenaj betiği: kuyruğu boşaltır, süresi dolmuşu iptal eder
# ===========================================================================
def test_dispatch_script_drains_queue_and_cancels_expired(tmp_path: Path) -> None:
    database = tmp_path / "f0-email-drain.db"
    seed = r'''
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from app.auth import auth_rate_limits, utcnow
from app.db import SessionLocal
from app.main import app
from app.notifications.schema import notifications
import app.routers.auth as auth_router

# Fırsatçı deneme kapatılır: satır kuyrukta kalsın ki drenajı ölçebilelim.
auth_router.deliver_now = lambda *args, **kwargs: None

client = TestClient(app, raise_server_exceptions=False)
for email, company in (("taze@ornek.test", "Taze"), ("bayat@ornek.test", "Bayat")):
    response = client.post("/api/auth/register", json={
        "company_name": company,
        "email": email,
        "password": "GuvenliParola!2026",
        "password_confirmation": "GuvenliParola!2026",
        "display_name": "Kurucu Kullanıcı",
        "phone": "5361234567",
        "terms_accepted": True,
    })
    assert response.status_code == 200, response.text
    with SessionLocal.begin() as db:
        db.execute(delete(auth_rate_limits))

with SessionLocal.begin() as db:
    db.execute(
        update(notifications)
        .where(notifications.c.recipient == "bayat@ornek.test")
        .values(created_at=utcnow() - timedelta(hours=25))
    )

with SessionLocal() as db:
    statuses = dict(
        db.execute(select(notifications.c.recipient, notifications.c.status)).all()
    )
assert statuses == {
    "taze@ornek.test": "PENDING",
    "bayat@ornek.test": "PENDING",
}, statuses
client.close()
print("SEED_OK")
'''
    env = _env(database, NOTIFICATION_PROVIDER="simulation")
    assert "SEED_OK" in _run(seed, env).stdout

    dry = _spawn([sys.executable, str(SCRIPT), "--dry-run"], env, timeout=120)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "süresi dolmuş (iptal edilecek): 1" in dry.stdout
    assert "(dry-run)" in dry.stdout

    live = _spawn([sys.executable, str(SCRIPT), "--limit", "10"], env, timeout=120)
    assert live.returncode == 0, live.stdout + live.stderr
    assert "süresi dolmuş (iptal): 1" in live.stdout
    assert "SIMULATED: 1" in live.stdout

    verify = r'''
from sqlalchemy import select
from app.db import SessionLocal
from app.notifications.schema import notifications

with SessionLocal() as db:
    rows = dict(db.execute(
        select(notifications.c.recipient, notifications.c.status)
    ).all())
assert rows == {
    "taze@ornek.test": "SIMULATED",
    "bayat@ornek.test": "CANCELLED",
}, rows
print("DRAIN_OK")
'''
    assert "DRAIN_OK" in _run(verify, env).stdout


def test_no_bulk_dispatch_http_endpoint_exists() -> None:
    """Toplu drenaj bilinçli olarak yalnız CLI'dır; HTTP ucu açılmadı."""
    router_source = (BACKEND / "app" / "routers" / "notifications.py").read_text(
        encoding="utf-8"
    )
    assert "claimable_notification_ids" not in router_source
    assert "/dispatch-all" not in router_source
