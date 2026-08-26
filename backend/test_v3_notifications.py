"""Notification provider/service/router seam on SQLite.

The end-to-end scenario is a subprocess because DATABASE_URL is bound when the
app package is imported. ``test_notifications_postgresql.py`` executes the same
scenario after pointing it at the real PostgreSQL 16 CI service.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.notifications import (
    NoOpNotificationProvider,
    TwilioNotificationProvider,
    WhatsAppNotificationProvider,
    get_notification_provider,
)


BACKEND = Path(__file__).resolve().parent


def test_notification_provider_factory_is_safe_by_default() -> None:
    default_provider = get_notification_provider(SimpleNamespace())
    assert isinstance(default_provider, NoOpNotificationProvider)
    assert default_provider.supports_idempotency is True
    assert isinstance(
        get_notification_provider(SimpleNamespace(notification_provider="NOOP")),
        NoOpNotificationProvider,
    )
    twilio = get_notification_provider(
        SimpleNamespace(notification_provider="twilio")
    )
    assert isinstance(twilio, TwilioNotificationProvider)
    assert twilio.supports_idempotency is False
    whatsapp = get_notification_provider(
        SimpleNamespace(notification_provider="whatsapp")
    )
    assert isinstance(whatsapp, WhatsAppNotificationProvider)
    assert whatsapp.supports_idempotency is False
    assert isinstance(
        get_notification_provider(SimpleNamespace(notification_provider="unknown")),
        NoOpNotificationProvider,
    )


def test_noop_is_inert_and_wiring_stubs_do_not_call_a_network() -> None:
    result = NoOpNotificationProvider().send({"payload": {"event": "READY"}})
    assert result.status == "NONE"
    assert result.external_id is None
    assert "yapılandırılmamış" in (result.message or "")

    for provider in (TwilioNotificationProvider(), WhatsAppNotificationProvider()):
        with pytest.raises(NotImplementedError, match="şirket entegrasyonu"):
            provider.send({})


def test_notification_seam_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "notifications.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _NOTIFICATION_SMOKE],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "NOTIFICATION_SEAM_OK" in completed.stdout


_NOTIFICATION_SMOKE = r'''
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import DateTime, bindparam, inspect, text

from app.auth import utcnow
from app.db import SessionLocal
from app.main import app
from app.notifications import (
    NotificationBusyError,
    NotificationProvider,
    NotificationResult,
    enqueue_notification,
    send_notification,
)


class FailingProvider(NotificationProvider):
    def send(self, notification):
        assert notification["provider_idempotency_key"].startswith("notification:")
        raise ConnectionError(
            "https://provider.invalid/send?token=secret-provider-token"
        )


class SentProvider(NotificationProvider):
    supports_idempotency = True

    def send(self, notification):
        assert notification["provider_idempotency_key"].startswith("notification:")
        return NotificationResult(status="SENT", external_id="provider-1")


with TestClient(app) as client:
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    cid_a = body["companies"][0]["id"]
    headers_a = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(cid_a),
    }
    changed = client.post(
        "/api/auth/change-password",
        headers=headers_a,
        json={
            "current_password": "admin123",
            "new_password": "NotificationSeam123!",
        },
    )
    assert changed.status_code == 200, changed.text
    headers_a["Authorization"] = "Bearer " + changed.json()["access_token"]

    company_b = client.post(
        "/api/companies",
        headers=headers_a,
        json={"name": "Notification Firma B"},
    )
    assert company_b.status_code == 201, company_b.text
    cid_b = company_b.json()["id"]
    headers_b = {**headers_a, "X-Company-ID": str(cid_b)}

    # Enqueue only persists PENDING rows. No provider is called before the source
    # transaction commits, and a rollback removes the row entirely.
    with SessionLocal() as db:
        rolled_back_id = enqueue_notification(
            db,
            company_id=cid_a,
            type_="ROLLBACK_TEST",
            channel="SMS",
            recipient="+905550001111",
            template="rollback",
            payload_dict={"event": "ROLLBACK_TEST"},
            dedupe_key="rollback-event",
        )
        db.rollback()
        assert db.execute(
            text(
                "SELECT COUNT(*) FROM notifications "
                "WHERE id=:id AND company_id=:cid"
            ),
            {"id": rolled_back_id, "cid": cid_a},
        ).scalar_one() == 0

    with SessionLocal() as db:
        nid_a = enqueue_notification(
            db,
            company_id=cid_a,
            type_="SERVICE_READY",
            channel="SMS",
            recipient="+905551112233",
            template="service-ready",
            payload_dict={"event": "SERVICE_READY", "order_id": 123},
            dedupe_key="service-ready:123",
        )
        duplicate_id = enqueue_notification(
            db,
            company_id=cid_a,
            type_="SERVICE_READY",
            channel="SMS",
            recipient="+905551112233",
            template="service-ready",
            payload_dict={"event": "SERVICE_READY", "order_id": 123},
            dedupe_key="service-ready:123",
        )
        assert duplicate_id == nid_a
        nid_b = enqueue_notification(
            db,
            company_id=cid_b,
            type_="INVOICE_READY",
            channel="WHATSAPP",
            recipient="+905559998877",
            template="invoice-ready",
            payload_dict={"event": "INVOICE_READY", "invoice_id": 456},
            dedupe_key="invoice-ready:456",
        )
        # FAZ-1 iki değişiklik getirir:
        #  1. dispatch onay + silahlanma ister (§2.4), bu yüzden satır açıkça
        #     onaylı kuyruğa alınır;
        #  2. SMS/WHATSAPP/EMAIL kanalları rıza kapısına tabidir (§3.3).
        # Buradaki satırlar sağlayıcı hata maskeleme ve lease semantiğini test
        # eder, gerçek bir müşteri mesajını değil; bu yüzden rıza kapısından
        # muaf olan taşıma kanalı (e-Fatura sağlayıcısı) kullanılır. Outbox'ın
        # genel amaçlı olması tam olarak budur.
        failed_id = enqueue_notification(
            db,
            company_id=cid_a,
            type_="FAILURE_TEST",
            channel="EINVOICE_PROVIDER",
            recipient="+905550000000",
            template="failure",
            payload_dict={"event": "FAILURE_TEST"},
            dedupe_key="failure-test",
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        db.commit()

        pending = db.execute(
            text(
                "SELECT status,attempt_count FROM notifications "
                "WHERE id=:id AND company_id=:cid"
            ),
            {"id": failed_id, "cid": cid_a},
        ).mappings().one()
        assert pending["status"] == "PENDING"
        assert pending["attempt_count"] == 0

        result = send_notification(
            db,
            company_id=cid_a,
            notification_id=failed_id,
            provider=FailingProvider(),
        )
        assert result.status == "FAILED"
        assert result.message == "Bildirim sağlayıcısına ulaşılamadı."
        assert "secret-provider-token" not in (result.message or "")
        failed = db.execute(
            text(
                "SELECT status,last_error,attempt_count,locked_until,lock_token "
                "FROM notifications WHERE id=:id AND company_id=:cid"
            ),
            {"id": failed_id, "cid": cid_a},
        ).mappings().one()
        # max_attempts=5 olduğu için ilk hata RETRY_SCHEDULED'a düşer (§2.7);
        # bu, sonucun FAILED dönmesini değiştirmez.
        assert failed["status"] == "RETRY_SCHEDULED"
        assert failed["last_error"] == "Bildirim sağlayıcısına ulaşılamadı."
        assert "secret-provider-token" not in failed["last_error"]
        assert failed["attempt_count"] == 1
        assert failed["locked_until"] is None
        assert failed["lock_token"] is None

        inspector = inspect(db.get_bind())
        columns = {column["name"] for column in inspector.get_columns("notifications")}
        assert {
            "id", "company_id", "type", "channel", "recipient", "template",
            "payload", "dedupe_key", "status", "external_id", "last_error",
            "attempt_count", "last_attempt_at", "locked_until", "lock_token",
            "created_at", "updated_at",
        } <= columns
        indexes = {index["name"] for index in inspector.get_indexes("notifications")}
        assert "ix_notifications_company_status" in indexes
        assert "ix_notifications_company_locked_until" in indexes
        constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("notifications")
        }
        assert "uq_notifications_company_dedupe" in constraints

        dialect = db.get_bind().dialect.name
        if dialect == "postgresql":
            payload_type = db.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name='notifications' AND column_name='payload'"
                )
            ).scalar_one()
            assert payload_type == "text"
        else:
            payload_type = next(
                row[2]
                for row in db.execute(text("PRAGMA table_info(notifications)")).all()
                if row[1] == "payload"
            )
            assert payload_type.upper() == "TEXT"

    # Existing AppShell alerts keep their original root route. Assert the route
    # table on both engines so the new outbox cannot shadow it. Its legacy SQL
    # compares a text due_date with CURRENT_DATE and is independently PG-unsafe,
    # so exercise that unrelated handler only in the SQLite regression.
    api_paths = app.openapi()["paths"]
    assert "get" in api_paths["/api/notifications"]
    assert "get" in api_paths["/api/notifications/outbox"]
    if dialect == "sqlite":
        alerts = client.get("/api/notifications", headers=headers_a)
        assert alerts.status_code == 200, alerts.text
        assert "count" in alerts.json()

    # FAZ-1: kuyruk sekmelidir. Onaysız satırlar "Bekleyenler"de görünmez;
    # ayrı "Onay Bekleyenler" sekmesindedir (§2.9 metrik/görünüm ayrımı).
    awaiting_a = client.get(
        "/api/notifications/outbox", headers=headers_a, params={"tab": "awaiting"}
    )
    assert awaiting_a.status_code == 200, awaiting_a.text
    awaiting_items = awaiting_a.json()["items"]
    assert nid_a in {item["id"] for item in awaiting_items}
    assert all(item["company_id"] == cid_a for item in awaiting_items)
    service_item = next(item for item in awaiting_items if item["id"] == nid_a)
    # Operasyonel liste alan payload içermez: PII taşımaz.
    assert "payload" not in service_item
    assert service_item["recipient"].endswith("2233")
    assert "+905551112233" not in service_item["recipient"]
    assert service_item["display_state"] == "Onay bekliyor"

    pending_a = client.get(
        "/api/notifications/outbox", headers=headers_a, params={"tab": "pending"}
    )
    assert pending_a.status_code == 200, pending_a.text
    failed_item = next(
        item for item in pending_a.json()["items"] if item["id"] == failed_id
    )
    assert failed_item["last_error"] == "Bildirim sağlayıcısına ulaşılamadı."
    assert "secret-provider-token" not in str(failed_item)

    limited = client.get(
        "/api/notifications/outbox",
        headers=headers_a,
        params={"limit": 1, "tab": "pending"},
    )
    assert limited.status_code == 200, limited.text
    assert [item["id"] for item in limited.json()["items"]] == [failed_id]

    outbox_b = client.get(
        "/api/notifications/outbox", headers=headers_b, params={"tab": "awaiting"}
    )
    assert outbox_b.status_code == 200, outbox_b.text
    assert [item["id"] for item in outbox_b.json()["items"]] == [nid_b]

    # Cross-tenant retry fails closed without disclosing the foreign row.
    foreign_retry = client.post(
        f"/api/notifications/{nid_a}/retry",
        headers=headers_b,
    )
    assert foreign_retry.status_code == 404, foreign_retry.text

    # FAZ-1: /retry yalnız yeniden deneme PLANLAR (§2.6); gönderimi /dispatch
    # başlatır. Bu ayrım, retry'ın sessizce gönderim tetiklemesini engeller.
    retry = client.post(
        f"/api/notifications/{failed_id}/retry",
        headers=headers_a,
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "RETRY_SCHEDULED"

    dispatched = client.post(
        f"/api/notifications/{failed_id}/dispatch",
        headers=headers_a,
    )
    assert dispatched.status_code == 200, dispatched.text
    assert dispatched.json()["status"] == "NONE"
    assert "yapılandırılmamış" in dispatched.json()["message"]

    with SessionLocal() as db:
        retried = db.execute(
            text(
                "SELECT status,last_error,attempt_count FROM notifications "
                "WHERE id=:id AND company_id=:cid"
            ),
            {"id": failed_id, "cid": cid_a},
        ).mappings().one()
        assert retried["status"] == "NONE"
        assert retried["last_error"] is None
        assert retried["attempt_count"] == 2

        terminal_id = enqueue_notification(
            db,
            company_id=cid_a,
            type_="TERMINAL_TEST",
            channel="EINVOICE_PROVIDER",
            recipient="+905550000222",
            template="terminal",
            payload_dict={"event": "TERMINAL_TEST"},
            dedupe_key="terminal-test",
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        db.commit()
        sent = send_notification(
            db,
            company_id=cid_a,
            notification_id=terminal_id,
            provider=SentProvider(),
        )
        assert sent.status == "SENT"

        lease_id = enqueue_notification(
            db,
            company_id=cid_a,
            type_="LEASE_TEST",
            channel="EINVOICE_PROVIDER",
            recipient="+905550000333",
            template="lease",
            payload_dict={"event": "LEASE_TEST"},
            dedupe_key="lease-test",
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        db.commit()
        db.execute(
            text(
                "UPDATE notifications SET status='PROCESSING', "
                "locked_until=:locked_until, lock_token=:lock_token "
                "WHERE id=:id AND company_id=:cid"
            ),
            {
                "id": lease_id,
                "cid": cid_a,
                "locked_until": utcnow() + timedelta(minutes=5),
                "lock_token": "active-worker",
            },
        )
        db.commit()
        try:
            send_notification(
                db,
                company_id=cid_a,
                notification_id=lease_id,
                provider=SentProvider(),
            )
            raise AssertionError("active lease must reject a second dispatcher")
        except NotificationBusyError:
            pass

        db.execute(
            text(
                "UPDATE notifications SET locked_until=:locked_until "
                "WHERE id=:id AND company_id=:cid"
            ).bindparams(
                bindparam("locked_until", type_=DateTime(timezone=True))
            ),
            {
                "id": lease_id,
                "cid": cid_a,
                "locked_until": utcnow() - timedelta(minutes=5),
            },
        )
        db.commit()
        reclaimed = send_notification(
            db,
            company_id=cid_a,
            notification_id=lease_id,
            provider=SentProvider(),
        )
        assert reclaimed.status == "SENT"

    terminal_retry = client.post(
        f"/api/notifications/{terminal_id}/retry",
        headers=headers_a,
    )
    assert terminal_retry.status_code == 409, terminal_retry.text

    created_user = client.post(
        "/api/users",
        headers=headers_a,
        json={
            "username": "notification_reader",
            "display_name": "Notification Reader",
            "password": "NotificationReader123!",
            "role": "satis",
        },
    )
    assert created_user.status_code == 201, created_user.text
    reader_login = client.post(
        "/api/auth/login",
        json={
            "username": "notification_reader",
            "password": "NotificationReader123!",
        },
    )
    assert reader_login.status_code == 200, reader_login.text
    reader_headers = {
        "Authorization": "Bearer " + reader_login.json()["access_token"],
        "X-Company-ID": str(cid_a),
    }
    rotated = client.post(
        "/api/auth/change-password",
        headers=reader_headers,
        json={
            "current_password": "NotificationReader123!",
            "new_password": "NotificationReader456!",
        },
    )
    assert rotated.status_code == 200, rotated.text
    reader_headers["Authorization"] = "Bearer " + rotated.json()["access_token"]
    # FAZ-1 RBAC ayrımı (§6): ``satis`` kuyruğu GÖRÜR ama onaylayamaz,
    # tetikleyemez ve şablon/izin yönetemez. Görme ile gönderme yetkisinin
    # ayrılması bu modülün sabit güvenlik kuralının bir parçasıdır.
    assert client.get(
        "/api/notifications/outbox",
        headers=reader_headers,
    ).status_code == 200
    assert client.post(
        f"/api/notifications/{nid_a}/retry",
        headers=reader_headers,
    ).status_code == 403
    assert client.post(
        f"/api/notifications/{nid_a}/dispatch",
        headers=reader_headers,
    ).status_code == 403
    assert client.post(
        f"/api/notifications/{nid_a}/approve",
        headers=reader_headers,
        json={"seen_hash": "0" * 64},
    ).status_code == 403
    assert client.post(
        "/api/notifications/templates",
        headers=reader_headers,
        json={
            "code": "x",
            "channel": "SMS",
            "name": "x",
            "body": "x",
            "message_class": "SERVICE_TRANSACTIONAL",
        },
    ).status_code == 403

print("NOTIFICATION_SEAM_OK")
'''
