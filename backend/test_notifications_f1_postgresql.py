"""Bildirimler FAZ-1 — PostgreSQL 16 ikizleri ve eşzamanlılık testleri.

SQLite'ta geçen bir sorgu PostgreSQL'de boolean/timestamp dialect farkları
yüzünden sessizce farklı davranabilir. Bu dosya F1'in en kritik yüklemlerini
gerçek PG16'ya karşı doğrular:

* §2.4 lease yükleminin beş koşulu (kriter 2 / invariant 2)
* §3.3 rıza-iptal yarışı — gönderimin linearization point'i (kriter 5)
* §2.9 disarm atomikliği ve arşiv işinin seçim yüklemi (invariant 1, 3, 4, 5)
* Arşiv değişmezliği trigger'ı (invariant 7)
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest


def _database_url() -> str:
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
    return database_url


def _bootstrap(monkeypatch: pytest.MonkeyPatch) -> int:
    """Şemayı migration'larla kurar ve aktif şirket kimliğini döndürür."""
    monkeypatch.setenv("DATABASE_URL", _database_url())
    import app.main  # noqa: F401  migrationları çalıştırır

    from sqlalchemy import text

    from app.db import SessionLocal

    with SessionLocal() as db:
        return int(
            db.execute(
                text("SELECT id FROM companies ORDER BY id LIMIT 1")
            ).scalar_one()
        )


def _queue(db, company_id: int, **kwargs):
    from app.notifications import enqueue_notification

    params = {
        "type_": "pg-test",
        "channel": "EINVOICE_PROVIDER",
        "recipient": "+905321112233",
        "template": "t",
        "payload_dict": {"event": "T"},
    }
    params.update(kwargs)
    return enqueue_notification(db, company_id=company_id, **params)


# ===========================================================================
# Kriter 2 / invariant 2 — lease yüklemi PG'de de kapalı
# ===========================================================================
@pytest.mark.postgresql
def test_lease_predicate_excludes_unapproved_and_disarmed_on_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = _bootstrap(monkeypatch)

    from sqlalchemy import DateTime, bindparam, text

    from app.auth import utcnow
    from app.db import SessionLocal
    from app.notifications import NotificationNotRetryableError, send_notification

    ts = DateTime(timezone=True)
    with SessionLocal() as db:
        armed = _queue(
            db,
            company_id,
            dedupe_key="pg-lease-armed",
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        unapproved = _queue(db, company_id, dedupe_key="pg-lease-unapproved")
        disarmed = _queue(
            db,
            company_id,
            dedupe_key="pg-lease-disarmed",
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        db.commit()
        db.execute(
            text(
                "UPDATE notifications SET dispatch_armed=FALSE, disarmed_at=:d "
                "WHERE id=:i AND company_id=:cid"
            ).bindparams(bindparam("d", type_=ts)),
            {"d": utcnow(), "i": disarmed, "cid": company_id},
        )
        db.commit()

        # Ham SQL yüklemi: PG'de boolean karşılaştırması ve timestamptz
        # dönüşümü SQLite'takiyle aynı sonucu vermelidir.
        selectable = {
            int(row[0])
            for row in db.execute(
                text(
                    """SELECT id FROM notifications
                        WHERE company_id = :cid
                          AND status IN ('PENDING','RETRY_SCHEDULED')
                          AND approved_at IS NOT NULL
                          AND approved_by IS NOT NULL
                          AND next_attempt_at <= :now
                          AND dispatch_armed = TRUE"""
                ).bindparams(bindparam("now", type_=ts)),
                {"cid": company_id, "now": utcnow()},
            ).all()
        }
        assert armed in selectable
        assert unapproved not in selectable
        assert disarmed not in selectable

        for blocked in (unapproved, disarmed):
            with pytest.raises(NotificationNotRetryableError):
                send_notification(
                    db, company_id=company_id, notification_id=blocked
                )


# ===========================================================================
# Kriter 5 — rıza-iptal yarışı (linearization point)
# ===========================================================================
@pytest.mark.postgresql
def test_committed_revoke_visible_before_dispatch_on_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch'ten ÖNCE commit edilmiş iptal gönderimi engeller (ardışık).

    Bu test sıralamayı zorlamaz — iptal, dispatch başlamadan önce commit
    edilir ve görünürlüğü doğrulanır. Gerçek yarış iddiasını (lease alınmış,
    rıza okuması henüz yapılmamışken gelen iptal) barrier'lı
    ``test_barrier_revoke_between_lease_and_consent_read_on_pg`` taşır.
    """
    company_id = _bootstrap(monkeypatch)

    from sqlalchemy import text

    from app.db import SessionLocal
    from app.notifications import NotificationProvider, send_notification, set_consent

    party_id = 918_273

    with SessionLocal() as db:
        set_consent(
            db,
            company_id=company_id,
            party_type="CUSTOMER",
            party_id=party_id,
            channel="SMS",
            granted=True,
            source="FORM",
            source_ref=None,
            recipient="+905321112233",
            user_id=1,
        )
        notification_id = _queue(
            db,
            company_id,
            channel="SMS",
            dedupe_key="pg-consent-race",
            payload_dict={"party_type": "CUSTOMER", "party_id": party_id},
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        db.commit()

    provider_called: list[str] = []

    class TrackingProvider(NotificationProvider):
        supports_idempotency = True

        def send(self, notification):  # pragma: no cover - çağrılmamalı
            provider_called.append("evet")
            from app.notifications import NotificationResult

            return NotificationResult(status="SENT", external_id="pg-1")

    def revoke() -> None:
        # AYRI bağlantı, AYRI transaction: iptal gerçekten commit edilir.
        with SessionLocal() as other:
            set_consent(
                other,
                company_id=company_id,
                party_type="CUSTOMER",
                party_id=party_id,
                channel="SMS",
                granted=False,
                source="PHONE",
                source_ref=None,
                recipient="+905321112233",
                user_id=1,
                reason="yarış testi",
            )
            other.commit()

    revoke()

    with SessionLocal() as db:
        result = send_notification(
            db,
            company_id=company_id,
            notification_id=notification_id,
            provider=TrackingProvider(),
        )

    assert provider_called == [], "iptalden sonra sağlayıcı çağrılmamalıydı"
    assert result.status == "FAILED"

    with SessionLocal() as db:
        row = db.execute(
            text(
                "SELECT status, consent_decision, consent_decision_reason "
                "FROM notifications WHERE id=:i AND company_id=:cid"
            ),
            {"i": notification_id, "cid": company_id},
        ).mappings().one()
    assert row["status"] == "REJECTED"
    assert row["consent_decision"] == "BLOCKED"
    assert row["consent_decision_reason"] == "REVOKED"


@pytest.mark.postgresql
def test_consent_snapshot_is_not_reused_on_retry_on_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Satırdaki rıza anlık görüntüsü karar verici DEĞİLDİR (§3.3).

    İlk denemede rıza vardır ve satıra yazılır; rıza geri çekildikten sonraki
    deneme, satırdaki 'ALLOWED' anlık görüntüsüne rağmen engellenmelidir.
    """
    company_id = _bootstrap(monkeypatch)

    from sqlalchemy import text

    from app.db import SessionLocal
    from app.notifications import (
        NotificationProvider,
        NotificationResult,
        schedule_retry,
        send_notification,
        set_consent,
    )

    party_id = 918_274

    class FlakyProvider(NotificationProvider):
        supports_idempotency = True

        def send(self, notification):
            raise ConnectionError("gecici")

    with SessionLocal() as db:
        set_consent(
            db,
            company_id=company_id,
            party_type="CUSTOMER",
            party_id=party_id,
            channel="SMS",
            granted=True,
            source="FORM",
            source_ref=None,
            recipient="+905321112233",
            user_id=1,
        )
        notification_id = _queue(
            db,
            company_id,
            channel="SMS",
            dedupe_key="pg-consent-retry",
            payload_dict={"party_type": "CUSTOMER", "party_id": party_id},
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        db.commit()

        first = send_notification(
            db,
            company_id=company_id,
            notification_id=notification_id,
            provider=FlakyProvider(),
        )
        assert first.status == "FAILED"
        snapshot = db.execute(
            text("SELECT consent_decision FROM notifications WHERE id=:i"),
            {"i": notification_id},
        ).scalar_one()
        assert snapshot == "ALLOWED"

        set_consent(
            db,
            company_id=company_id,
            party_type="CUSTOMER",
            party_id=party_id,
            channel="SMS",
            granted=False,
            source="PHONE",
            source_ref=None,
            recipient="+905321112233",
            user_id=1,
        )
        db.commit()

        schedule_retry(
            db, company_id=company_id, notification_id=notification_id
        )
        db.commit()
        second = send_notification(
            db,
            company_id=company_id,
            notification_id=notification_id,
            provider=FlakyProvider(),
        )

    assert second.status == "FAILED"
    with SessionLocal() as db:
        row = db.execute(
            text(
                "SELECT status, consent_decision_reason FROM notifications "
                "WHERE id=:i AND company_id=:cid"
            ),
            {"i": notification_id, "cid": company_id},
        ).mappings().one()
    assert row["status"] == "REJECTED"
    assert row["consent_decision_reason"] == "REVOKED"


# ===========================================================================
# Invariant 1 — disarm atomikliği ve eşzamanlı lease
# ===========================================================================
@pytest.mark.postgresql
def test_disarm_blocks_concurrent_dispatch_on_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disarm commit edildikten sonra hiçbir worker satırı lease edemez."""
    company_id = _bootstrap(monkeypatch)

    from app.db import SessionLocal
    from app.notifications import (
        NotificationNotRetryableError,
        disarm_rule_notifications,
        send_notification,
    )

    with SessionLocal() as db:
        notification_id = _queue(
            db,
            company_id,
            dedupe_key="pg-disarm-race",
            rule_id=4242,
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        db.commit()
        affected = disarm_rule_notifications(
            db, company_id=company_id, rule_id=4242
        )
        assert affected == 1
        db.commit()

    barrier = Barrier(2)

    def try_dispatch() -> str:
        with SessionLocal() as db:
            barrier.wait(timeout=10)
            try:
                send_notification(
                    db, company_id=company_id, notification_id=notification_id
                )
            except NotificationNotRetryableError:
                return "reddedildi"
            return "gonderildi"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result() for future in [pool.submit(try_dispatch) for _ in range(2)]]

    assert outcomes == ["reddedildi", "reddedildi"]


# ===========================================================================
# Invariant 3, 4, 5 — arşiv işi PG'de
# ===========================================================================
@pytest.mark.postgresql
def test_archive_job_is_scoped_expired_only_and_rerunnable_on_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = _bootstrap(monkeypatch)

    from sqlalchemy import DateTime, bindparam, text

    from app.auth import utcnow
    from app.db import SessionLocal
    from app.notifications import archive_disarmed_notifications

    ts = DateTime(timezone=True)
    with SessionLocal() as db:
        expired = _queue(
            db,
            company_id,
            dedupe_key="pg-arch-expired",
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        fresh = _queue(
            db,
            company_id,
            dedupe_key="pg-arch-fresh",
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        db.commit()
        for notification_id, days in ((expired, 200), (fresh, 3)):
            db.execute(
                text(
                    "UPDATE notifications SET dispatch_armed=FALSE, disarmed_at=:d "
                    "WHERE id=:i AND company_id=:cid"
                ).bindparams(bindparam("d", type_=ts)),
                {
                    "d": utcnow() - timedelta(days=days),
                    "i": notification_id,
                    "cid": company_id,
                },
            )
        db.commit()

        moved = archive_disarmed_notifications(
            db, company_id=company_id, retention_days=90
        )
        assert moved == 1

        assert db.execute(
            text("SELECT COUNT(*) FROM notifications WHERE id=:i"), {"i": expired}
        ).scalar_one() == 0
        assert db.execute(
            text("SELECT COUNT(*) FROM notifications WHERE id=:i"), {"i": fresh}
        ).scalar_one() == 1
        assert db.execute(
            text("SELECT COUNT(*) FROM notifications_archive WHERE id=:i"),
            {"i": expired},
        ).scalar_one() == 1

        # Yeniden çalıştırma: kayıpsız ve mükerrersiz.
        assert (
            archive_disarmed_notifications(
                db, company_id=company_id, retention_days=90
            )
            == 0
        )
        assert db.execute(
            text("SELECT COUNT(*) FROM notifications_archive WHERE id=:i"),
            {"i": expired},
        ).scalar_one() == 1


# ===========================================================================
# Invariant 7 — arşiv değişmezliği veritabanı seviyesinde
# ===========================================================================
@pytest.mark.postgresql
def test_archive_rows_cannot_be_updated_or_deleted_on_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = _bootstrap(monkeypatch)

    from sqlalchemy import DateTime, bindparam, text
    from sqlalchemy.exc import DBAPIError, InternalError

    from app.auth import utcnow
    from app.db import SessionLocal
    from app.notifications import archive_disarmed_notifications

    ts = DateTime(timezone=True)
    with SessionLocal() as db:
        notification_id = _queue(
            db,
            company_id,
            dedupe_key="pg-arch-immutable",
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        db.commit()
        db.execute(
            text(
                "UPDATE notifications SET dispatch_armed=FALSE, disarmed_at=:d "
                "WHERE id=:i AND company_id=:cid"
            ).bindparams(bindparam("d", type_=ts)),
            {"d": utcnow() - timedelta(days=400), "i": notification_id, "cid": company_id},
        )
        db.commit()
        assert (
            archive_disarmed_notifications(
                db, company_id=company_id, retention_days=90
            )
            == 1
        )

    for statement in (
        "UPDATE notifications_archive SET status='SENT' WHERE id=:i",
        "DELETE FROM notifications_archive WHERE id=:i",
    ):
        with SessionLocal() as db:
            with pytest.raises((DBAPIError, InternalError)):
                db.execute(text(statement), {"i": notification_id})
                db.commit()
            db.rollback()

    with SessionLocal() as db:
        assert db.execute(
            text("SELECT status FROM notifications_archive WHERE id=:i"),
            {"i": notification_id},
        ).scalar_one() != "SENT"


# ===========================================================================
# PR #175 kapanışları — gerçek barrier'lı yarış + transaction hijyeni
# ===========================================================================
@pytest.mark.postgresql
def test_barrier_revoke_between_lease_and_consent_read_on_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sıralama ZORLANIR: lease commit → (barrier) iptal commit → rıza okuması.

    Worker satırı kiralar ve commit eder; rıza okumasından hemen önce durur.
    İkinci bağlantı iptali commit eder. Worker devam ettiğinde son okuma
    iptali görmek zorundadır: provider çağrısı SIFIR, satır ``REJECTED``.
    Bu, §3.3 linearization point iddiasının kendisidir.
    """
    from threading import Event

    company_id = _bootstrap(monkeypatch)

    from sqlalchemy import text

    from app.db import SessionLocal
    from app.notifications import (
        NotificationProvider,
        NotificationResult,
        send_notification,
        set_consent,
    )
    from app.notifications import consents as consents_module
    from app.notifications import service as service_module

    party_id = 918_275
    worker_at_consent_gate = Event()
    revoke_committed = Event()
    provider_calls: list[str] = []

    with SessionLocal() as db:
        set_consent(
            db,
            company_id=company_id,
            party_type="CUSTOMER",
            party_id=party_id,
            channel="SMS",
            granted=True,
            source="FORM",
            source_ref=None,
            recipient="+905321112233",
            user_id=1,
        )
        notification_id = _queue(
            db,
            company_id,
            channel="SMS",
            dedupe_key="pg-barrier-race",
            payload_dict={"party_type": "CUSTOMER", "party_id": party_id},
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        db.commit()

    real_evaluate = consents_module.evaluate_consent

    def gated_evaluate(*args, **kwargs):
        # Bu nokta send_notification içinde, lease commit edildikten SONRA ve
        # rıza SELECT'inden hemen ÖNCE'dir. Worker burada durdurulur ve iptal
        # commit edilene kadar bekletilir.
        worker_at_consent_gate.set()
        assert revoke_committed.wait(timeout=15), "iptal beklenirken zaman aşımı"
        return real_evaluate(*args, **kwargs)

    class TrackingProvider(NotificationProvider):
        supports_idempotency = True

        def send(self, notification):  # pragma: no cover - çağrılmamalı
            provider_calls.append("çağrıldı")
            return NotificationResult(status="SENT", external_id="asla")

    def worker() -> str:
        with SessionLocal() as db:
            return send_notification(
                db,
                company_id=company_id,
                notification_id=notification_id,
                provider=TrackingProvider(),
            ).status

    monkeypatch.setattr(
        service_module.consent_service, "evaluate_consent", gated_evaluate
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)

        assert worker_at_consent_gate.wait(timeout=15)
        # Worker rıza okumasının eşiğinde durmuşken lease'in gerçekten commit
        # edilmiş olduğunu AYRI bağlantıdan kanıtla.
        with SessionLocal() as other:
            leased_status = other.execute(
                text(
                    "SELECT status FROM notifications "
                    "WHERE id=:i AND company_id=:cid"
                ),
                {"i": notification_id, "cid": company_id},
            ).scalar_one()
            assert leased_status == "PROCESSING"

            set_consent(
                other,
                company_id=company_id,
                party_type="CUSTOMER",
                party_id=party_id,
                channel="SMS",
                granted=False,
                source="PHONE",
                source_ref=None,
                recipient="+905321112233",
                user_id=1,
                reason="barrier yarış testi",
            )
            other.commit()
        revoke_committed.set()

        outcome = future.result(timeout=30)

    assert provider_calls == [], "iptalden sonra sağlayıcı çağrıldı"
    assert outcome == "FAILED"
    with SessionLocal() as db:
        row = db.execute(
            text(
                "SELECT status, consent_decision, consent_decision_reason "
                "FROM notifications WHERE id=:i AND company_id=:cid"
            ),
            {"i": notification_id, "cid": company_id},
        ).mappings().one()
    assert row["status"] == "REJECTED"
    assert row["consent_decision"] == "BLOCKED"
    assert row["consent_decision_reason"] == "REVOKED"


@pytest.mark.postgresql
def test_no_open_transaction_during_provider_call_on_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dış çağrı sırasında bağlantıda açık transaction yoktur (PG kanıtı).

    Hem SQLAlchemy ``in_transaction()`` hem ``pg_stat_activity`` üzerinden:
    worker'ın backend'i provider çağrısı anında 'idle in transaction'
    durumunda OLMAMALIDIR.
    """
    company_id = _bootstrap(monkeypatch)

    from sqlalchemy import text

    from app.db import SessionLocal
    from app.notifications import (
        NotificationProvider,
        NotificationResult,
        send_notification,
        set_consent,
    )

    party_id = 918_276
    with SessionLocal() as db:
        set_consent(
            db,
            company_id=company_id,
            party_type="CUSTOMER",
            party_id=party_id,
            channel="SMS",
            granted=True,
            source="FORM",
            source_ref=None,
            recipient="+905321112233",
            user_id=1,
        )
        notification_id = _queue(
            db,
            company_id,
            channel="SMS",
            dedupe_key="pg-tx-probe",
            payload_dict={"party_type": "CUSTOMER", "party_id": party_id},
            armed=True,
            approved_by=1,
            approval_mode="SYSTEM",
        )
        db.commit()

    observations: dict[str, object] = {}

    with SessionLocal() as db:
        backend_pid = int(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
        db.commit()

        class TransactionProbe(NotificationProvider):
            supports_idempotency = True

            def send(self, notification):
                observations["in_transaction"] = db.in_transaction()
                with SessionLocal() as inspector:
                    observations["pg_state"] = inspector.execute(
                        text(
                            "SELECT state FROM pg_stat_activity WHERE pid = :pid"
                        ),
                        {"pid": backend_pid},
                    ).scalar_one()
                return NotificationResult(status="SENT", external_id="tx-probe")

        result = send_notification(
            db,
            company_id=company_id,
            notification_id=notification_id,
            provider=TransactionProbe(),
        )

    assert result.status == "SENT"
    assert observations["in_transaction"] is False
    assert observations["pg_state"] == "idle", observations
