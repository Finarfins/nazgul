"""Notification seam parity and concurrency tests on PostgreSQL 16."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, Lock

import pytest


@pytest.mark.postgresql
def test_notification_seam_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("NOTIFICATIONS_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip(
            "NOTIFICATIONS_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required"
        )
    assert database_url.startswith("postgresql"), (
        "the PostgreSQL twin must not run on another engine: " + database_url
    )
    monkeypatch.setenv("DATABASE_URL", database_url)

    # Import after DATABASE_URL is set so app.db binds the PG16 engine, not the
    # repository's default SQLite URL.
    from test_v3_notifications import _NOTIFICATION_SMOKE

    exec(compile(_NOTIFICATION_SMOKE, "<notification_smoke>", "exec"), {})

    from sqlalchemy import text

    from app.auth import utcnow
    from app.db import SessionLocal
    from app.notifications import (
        NotificationBusyError,
        NotificationProvider,
        NotificationResult,
        enqueue_notification,
        send_notification,
    )

    with SessionLocal() as db:
        company_id = int(db.execute(text("SELECT id FROM companies ORDER BY id LIMIT 1")).scalar_one())

    # Two simultaneous domain handlers must resolve to one tenant-scoped outbox row.
    enqueue_barrier = Barrier(2)

    def concurrent_enqueue() -> int:
        with SessionLocal() as db:
            enqueue_barrier.wait(timeout=10)
            notification_id = enqueue_notification(
                db,
                company_id=company_id,
                type_="CONCURRENT_DEDUPE",
                channel="SMS",
                recipient="+905550003333",
                template="concurrent-dedupe",
                payload_dict={"event": "CONCURRENT_DEDUPE"},
                dedupe_key="concurrent-dedupe:event-1",
            )
            db.commit()
            return notification_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: concurrent_enqueue(), range(2)))
    assert ids[0] == ids[1]
    with SessionLocal() as db:
        assert db.execute(
            text(
                "SELECT COUNT(*) FROM notifications "
                "WHERE company_id=:cid AND dedupe_key=:dedupe"
            ),
            {"cid": company_id, "dedupe": "concurrent-dedupe:event-1"},
        ).scalar_one() == 1

    # A committed lease prevents a second worker from calling the provider while
    # the first worker is still in the external delivery section.
    entered_provider = Event()
    release_provider = Event()
    calls_lock = Lock()
    provider_calls = 0

    class SlowProvider(NotificationProvider):
        def send(self, notification):
            nonlocal provider_calls
            with calls_lock:
                provider_calls += 1
            entered_provider.set()
            assert release_provider.wait(timeout=10)
            return NotificationResult(status="SENT", external_id="slow-provider-1")

    with SessionLocal() as db:
        leased_id = enqueue_notification(
            db,
            company_id=company_id,
            type_="CONCURRENT_SEND",
            # FAZ-1: dispatch onay + silahlanma ister (§2.4) ve SMS rıza
            # kapısına tabidir (§3.3). Bu senaryo lease semantiğini test eder,
            # gerçek bir müşteri mesajını değil.
            channel="EINVOICE_PROVIDER",
            recipient="+905550004444",
            template="concurrent-send",
            payload_dict={"event": "CONCURRENT_SEND"},
            dedupe_key="concurrent-send:event-1",
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        db.commit()

    def first_dispatch() -> str:
        with SessionLocal() as db:
            return send_notification(
                db,
                company_id=company_id,
                notification_id=leased_id,
                provider=SlowProvider(),
            ).status

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first_dispatch)
        assert entered_provider.wait(timeout=10)
        with SessionLocal() as db:
            with pytest.raises(NotificationBusyError):
                send_notification(
                    db,
                    company_id=company_id,
                    notification_id=leased_id,
                    provider=SlowProvider(),
                )
        release_provider.set()
        assert future.result(timeout=10) == "SENT"

    assert provider_calls == 1

    non_idempotent_calls = 0

    class NonIdempotentProvider(NotificationProvider):
        supports_idempotency = False

        def send(self, notification):
            nonlocal non_idempotent_calls
            non_idempotent_calls += 1
            return NotificationResult(status="SENT", external_id="unsafe-provider")

    idempotent_calls = 0

    class IdempotentProvider(NotificationProvider):
        supports_idempotency = True

        def send(self, notification):
            nonlocal idempotent_calls
            idempotent_calls += 1
            return NotificationResult(status="SENT", external_id="safe-provider")

    def expired_lease(dedupe_key: str) -> int:
        with SessionLocal() as db:
            notification_id = enqueue_notification(
                db,
                company_id=company_id,
                type_="EXPIRED_LEASE",
                channel="EINVOICE_PROVIDER",
                recipient="+905550005555",
                template="expired-lease",
                payload_dict={"event": "EXPIRED_LEASE"},
                dedupe_key=dedupe_key,
                armed=True,
                approved_by=1,
                approval_mode="SYSTEM",
            )
            db.commit()
            db.execute(
                text(
                    "UPDATE notifications SET status='PROCESSING', "
                    "attempt_count=1, locked_until=:expired_at, "
                    "lock_token=:lock_token "
                    "WHERE id=:id AND company_id=:cid"
                ),
                {
                    "id": notification_id,
                    "cid": company_id,
                    "expired_at": utcnow() - timedelta(minutes=5),
                    "lock_token": "expired-worker",
                },
            )
            db.commit()
            return notification_id

    unsafe_id = expired_lease("expired-lease:non-idempotent")
    with SessionLocal() as db:
        with pytest.raises(NotificationBusyError):
            send_notification(
                db,
                company_id=company_id,
                notification_id=unsafe_id,
                provider=NonIdempotentProvider(),
            )
    assert non_idempotent_calls == 0

    safe_id = expired_lease("expired-lease:idempotent")
    with SessionLocal() as db:
        assert send_notification(
            db,
            company_id=company_id,
            notification_id=safe_id,
            provider=IdempotentProvider(),
        ).status == "SENT"
    assert idempotent_calls == 1
