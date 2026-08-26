"""Bildirimler FAZ-1 — tasarım kabul kriterleri (§7.5) ve F1 invariantları.

Her test adı, karşıladığı kriter/invariant numarasını taşır:

* ``test_k<N>_...``  → ``TASARIM_SERVIS_HATIRLATMA_BILDIRIM_F0.md`` §7.5 kriteri
* ``test_i<N>_...``  → F1 build invariantı

Testler SQLite üzerinde çalışır; PostgreSQL ikizleri
``test_notifications_f1_postgresql.py`` içindedir.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import DateTime, bindparam, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.notifications import content_gate, render
from app.notifications.archive import (
    MIN_RETENTION_DAYS,
    RetentionPolicyError,
    archive_disarmed_notifications,
    resolve_retention_days,
)
from app.notifications.provider import (
    NoOpNotificationProvider,
    NotificationProvider,
    NotificationResult,
    SimulationNotificationProvider,
)
from app.notifications.schema import metadata as notification_metadata
from app.notifications.service import (
    _ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    NotificationApprovalError,
    NotificationNotRetryableError,
    approve_notification,
    assert_transition,
    backoff_delay_seconds,
    disarm_rule_notifications,
    enqueue_notification,
    outbox_counters,
    schedule_retry,
    send_notification,
)

BACKEND = Path(__file__).resolve().parent
_TS = DateTime(timezone=True)


# ---------------------------------------------------------------------------
# İzole şema: bu dosya tüm ERP şemasını değil yalnız bildirim tablolarını kurar.
# ---------------------------------------------------------------------------
@pytest.fixture()
def db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'f1.db').as_posix()}")
    notification_metadata.create_all(engine)
    with engine.begin() as conn:
        # Arşiv tablosu migration'da üretilir; birim testler için aynı şekli
        # buradan kurarız (kolon listesi archive._COMMON_COLUMNS ile eşleşir).
        conn.execute(
            text(
                """CREATE TABLE notifications_archive AS
                   SELECT *, NULL AS archived_at, NULL AS archived_by,
                          NULL AS archive_reason
                     FROM notifications WHERE 0"""
            )
        )
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _queue(
    db: Session,
    *,
    company_id: int = 1,
    channel: str = "EINVOICE_PROVIDER",
    armed: bool = False,
    dedupe: str | None = None,
    created_by: int | None = None,
    content_hash: str | None = None,
    message_class: str | None = None,
    payload: dict | None = None,
    rule_id: int | None = None,
) -> int:
    return enqueue_notification(
        db,
        company_id=company_id,
        type_="test",
        channel=channel,
        recipient="+905321112233",
        template="t",
        payload_dict=payload or {"event": "T"},
        dedupe_key=dedupe,
        created_by=created_by,
        content_hash=content_hash,
        message_class=message_class,
        rule_id=rule_id,
        armed=armed,
        approved_by=9 if armed else None,
        approval_mode="SYSTEM" if armed else None,
    )


def _status(db: Session, nid: int) -> str:
    return db.execute(
        text("SELECT status FROM notifications WHERE id=:i"), {"i": nid}
    ).scalar_one()


def _armed(db: Session, nid: int) -> bool:
    return bool(
        db.execute(
            text("SELECT dispatch_armed FROM notifications WHERE id=:i"), {"i": nid}
        ).scalar_one()
    )


def _disarm_at(db: Session, nid: int, *, days_ago: int) -> None:
    from app.auth import utcnow

    db.execute(
        text(
            "UPDATE notifications SET dispatch_armed=:a, disarmed_at=:d WHERE id=:i"
        ).bindparams(bindparam("d", type_=_TS)),
        {"a": False, "d": utcnow() - timedelta(days=days_ago), "i": nid},
    )
    db.commit()


class _Boom(NotificationProvider):
    def send(self, notification):
        raise ConnectionError("secret-token")


# ===========================================================================
# Kriter 1 — kapalı geçiş tablosu
# ===========================================================================
def test_k1_transition_table_is_closed() -> None:
    assert_transition("AWAITING_APPROVAL", "PENDING")
    assert_transition("PROCESSING", "REJECTED")

    for source, target in [
        ("AWAITING_APPROVAL", "PROCESSING"),  # onayı atlayamaz
        ("SENT", "PENDING"),  # terminalden çıkış yok
        ("CANCELLED", "PROCESSING"),
        ("REJECTED", "PENDING"),
        ("PENDING", "SENT"),  # lease atlanamaz
    ]:
        with pytest.raises(InvalidTransitionError):
            assert_transition(source, target)

    with pytest.raises(InvalidTransitionError):
        assert_transition("BILINMEYEN", "PENDING")

    # Terminal durumların çıkışı boş küme olmalı.
    for terminal in ("SENT", "DELIVERED", "SIMULATED", "CANCELLED", "REJECTED"):
        assert _ALLOWED_TRANSITIONS[terminal] == frozenset()


# ===========================================================================
# Kriter 2 / invariant 2 — AWAITING_APPROVAL ve disarmed DB seviyesinde elenir
# ===========================================================================
def test_k2_lease_rejects_unapproved_row(db: Session) -> None:
    nid = _queue(db)
    db.commit()
    assert _status(db, nid) == "AWAITING_APPROVAL"

    with pytest.raises(NotificationNotRetryableError):
        send_notification(db, company_id=1, notification_id=nid)
    assert _status(db, nid) == "AWAITING_APPROVAL"


def test_i2_lease_sql_never_selects_disarmed_or_unapproved(db: Session) -> None:
    """Ham SQL yüklemi: her koşul veritabanında, uygulama filtresinde değil."""
    from app.auth import utcnow

    armed_id = _queue(db, armed=True, dedupe="armed")
    unapproved_id = _queue(db, dedupe="unapproved")
    disarmed_id = _queue(db, armed=True, dedupe="disarmed")
    db.commit()
    _disarm_at(db, disarmed_id, days_ago=0)

    selectable = [
        int(row[0])
        for row in db.execute(
            text(
                """SELECT id FROM notifications
                    WHERE company_id = :cid
                      AND status IN ('PENDING','RETRY_SCHEDULED')
                      AND approved_at IS NOT NULL
                      AND approved_by IS NOT NULL
                      AND next_attempt_at <= :now
                      AND dispatch_armed = :armed"""
            ).bindparams(bindparam("now", type_=_TS)),
            {"cid": 1, "now": utcnow(), "armed": True},
        ).all()
    ]
    assert selectable == [armed_id]
    assert unapproved_id not in selectable
    assert disarmed_id not in selectable

    # Otomatik retry de aynı yüklemden geçer: disarmed satır alınamaz.
    with pytest.raises(NotificationNotRetryableError):
        send_notification(db, company_id=1, notification_id=disarmed_id)


# ===========================================================================
# Kriter 3 / invariant 8 — onay geçersizleşmesi ve yeniden değerlendirme
# ===========================================================================
def test_k3_stale_preview_hash_is_rejected(db: Session) -> None:
    good = "a" * 64
    nid = _queue(db, content_hash=good, created_by=1)
    db.commit()

    with pytest.raises(NotificationApprovalError):
        approve_notification(
            db, company_id=1, notification_id=nid, approver_id=2, seen_hash="b" * 64
        )
    assert _status(db, nid) == "AWAITING_APPROVAL"

    approve_notification(
        db, company_id=1, notification_id=nid, approver_id=2, seen_hash=good
    )
    db.commit()
    assert _status(db, nid) == "PENDING"
    assert _armed(db, nid) is True


def test_k3_four_eyes_creator_cannot_approve_own_row(db: Session) -> None:
    nid = _queue(db, content_hash="c" * 64, created_by=7)
    db.commit()
    with pytest.raises(NotificationApprovalError, match="Kendi oluşturduğunuz"):
        approve_notification(
            db, company_id=1, notification_id=nid, approver_id=7, seen_hash="c" * 64
        )


def test_i8_reevaluation_does_not_carry_old_approval(db: Session) -> None:
    """Disarm sonrası yeniden değerlendirme eski onayı taşımaz."""
    from app.notifications.service import invalidate_approval

    nid = _queue(db, armed=True, content_hash="d" * 64, created_by=1)
    db.commit()
    invalidate_approval(
        db, company_id=1, notification_id=nid, new_content_hash="e" * 64
    )
    db.commit()

    row = db.execute(
        text(
            "SELECT status,dispatch_armed,approved_by,approved_at,content_hash "
            "FROM notifications WHERE id=:i"
        ),
        {"i": nid},
    ).mappings().one()
    assert row["status"] == "AWAITING_APPROVAL"
    assert not row["dispatch_armed"]
    assert row["approved_by"] is None and row["approved_at"] is None
    # Yeni onay YENİ hash ile verilir; eski hash artık geçerli değildir.
    with pytest.raises(NotificationApprovalError):
        approve_notification(
            db, company_id=1, notification_id=nid, approver_id=2, seen_hash="d" * 64
        )


# ===========================================================================
# Kriter 15 — hash kanonik render edilmiş payload'dan
# ===========================================================================
def test_k15_hash_differs_per_amount_and_recipient() -> None:
    from datetime import date
    from decimal import Decimal

    body = "Sayin {musteri_adi}, {vade_tarihi} vadeli {tutar} odemeniz."

    def build(amount: str, recipient: str = "+905321112233") -> dict:
        return render.build_content(
            message_class="SERVICE_TRANSACTIONAL",
            channel="SMS",
            recipient=recipient,
            template_id=1,
            template_version=1,
            body_template=body,
            variables={
                "musteri_adi": "Ayse Kaya",
                "vade_tarihi": date(2026, 8, 12),
                "tutar": Decimal(amount),
            },
        )

    thousand = build("1000")
    ten_thousand = build("10000")
    other_recipient = build("1000", "+905329998877")

    assert thousand["content_hash"] != ten_thousand["content_hash"]
    assert thousand["content_hash"] != other_recipient["content_hash"]
    assert thousand["content_hash"] == build("1000")["content_hash"]
    # Şablon gövdesinin kendisi hash DEĞİLDİR.
    assert render.content_hash(body) != thousand["content_hash"]


def test_k15_dispatch_reverifies_stored_payload(db: Session) -> None:
    """Adım 7: gönderim öncesi saklanan payload'ın hash'i yeniden hesaplanır."""
    content = render.build_content(
        message_class="SERVICE_TRANSACTIONAL",
        channel="EINVOICE_PROVIDER",
        recipient="+905321112233",
        template_id=1,
        template_version=1,
        body_template="Merhaba {musteri_adi}.",
        variables={"musteri_adi": "Ayse"},
    )
    nid = _queue(
        db,
        armed=True,
        content_hash=content["content_hash"],
        message_class="SERVICE_TRANSACTIONAL",
        payload={"body": content["body"], "subject": None, "metadata": {}},
    )
    db.commit()

    # Saklanan gövde bozulur → hash tutmaz → gönderim yapılmaz, onaya döner.
    db.execute(
        text("UPDATE notifications SET payload=:p WHERE id=:i"),
        {"p": json.dumps({"body": "DEGISTIRILDI", "subject": None, "metadata": {}}), "i": nid},
    )
    db.commit()

    result = send_notification(db, company_id=1, notification_id=nid)
    assert result.status == "FAILED"
    assert _status(db, nid) == "AWAITING_APPROVAL"
    assert _armed(db, nid) is False


# ===========================================================================
# Kriter 6 — normalize edilmiş içerik kapısı
# ===========================================================================
@pytest.mark.parametrize(
    "payload",
    [
        "http://ornek.com",
        "https://ornek.com",
        "www.ornek.com",
        "ornek.com.tr",
        "h t t p : / / x",
        "http[:]//x",
        "hxxp://x",
        "HtTp://X",
        "%68%74%74%70://x",
        "%2568ttp://x",
        "&#104;ttp://x",
        "mailto:a@b.com",
        "tel:05321112233",
        "javascript:alert(1)",
        "data:text/html,x",
        "bit.ly/abc",
        "192.0.2.1",
        "0xC0000201",
        "2130706433",
        "htt​p://x",
        '<a href="x">tikla</a>',
        "url(evil)",
        "@import 'x'",
    ],
)
def test_k6_content_gate_rejects_bypass_vectors(payload: str) -> None:
    with pytest.raises(content_gate.ContentRejected):
        content_gate.assert_content_allowed(payload)


@pytest.mark.parametrize(
    "payload",
    [
        "Sayin Ahmet Yilmaz, 12.08.2026 tarihli randevunuz saat 09:30.",
        "Sayin Ayse, 4.500,00 TL tutarindaki odemenizin vadesi yaklasiyor.",
        "Makineniz hazir. Bizi 0532 111 22 33 numarasindan arayabilirsiniz.",
        "Sayın Ayşe Çağrı, iş emriniz tamamlandı. Şükrü Tarım",
    ],
)
def test_k6_content_gate_allows_legitimate_turkish_messages(payload: str) -> None:
    content_gate.assert_content_allowed(payload)


def test_k6_variable_injection_is_blocked_before_substitution() -> None:
    with pytest.raises(render.TemplateRenderError):
        render.render_body(
            "Merhaba {musteri_adi}.", {"musteri_adi": "Ayse http://kotu.com"}
        )


def test_k6_variable_length_limit_errors_instead_of_truncating() -> None:
    with pytest.raises(render.TemplateRenderError, match="en fazla 80"):
        render.render_body("Merhaba {musteri_adi}.", {"musteri_adi": "A" * 81})


# ===========================================================================
# Kriter 7 — backoff: üstel + jitter + üst sınır
# ===========================================================================
def test_k7_backoff_is_exponential_jittered_and_capped() -> None:
    rng = random.Random(1234)
    first = backoff_delay_seconds(1, rng=rng)
    later = backoff_delay_seconds(6, rng=rng)
    assert first < later

    # Jitter ±%20 bandında.
    for attempt in range(1, 8):
        base = min(2**attempt * 60, 6 * 60 * 60)
        value = backoff_delay_seconds(attempt, rng=random.Random(attempt))
        assert 0.8 * base - 1 <= value <= 1.2 * base + 1

    # Üst sınır jitter'dan sonra da korunur.
    for attempt in range(20, 40):
        assert backoff_delay_seconds(attempt, rng=random.Random(attempt)) <= 6 * 60 * 60

    # Deterministik: aynı seed aynı sonuç.
    assert backoff_delay_seconds(3, rng=random.Random(7)) == backoff_delay_seconds(
        3, rng=random.Random(7)
    )


def test_k7_failed_dispatch_schedules_retry_atomically(db: Session) -> None:
    nid = _queue(db, armed=True)
    db.commit()
    result = send_notification(
        db, company_id=1, notification_id=nid, provider=_Boom()
    )
    assert result.status == "FAILED"
    row = db.execute(
        text(
            "SELECT status,attempt_count,next_attempt_at,last_error_code,last_error "
            "FROM notifications WHERE id=:i"
        ),
        {"i": nid},
    ).mappings().one()
    assert row["status"] == "RETRY_SCHEDULED"
    assert row["attempt_count"] == 1
    assert row["next_attempt_at"] is not None
    assert row["last_error_code"] == "NETWORK"
    # Ham sağlayıcı metni sızmaz.
    assert "secret-token" not in str(row["last_error"])


# ===========================================================================
# Kriter 11 — provider idempotency key / duplicate modeli
# ===========================================================================
def test_k11_idempotency_key_is_stable_across_attempts(db: Session) -> None:
    seen: list[str] = []

    class Recorder(NotificationProvider):
        def send(self, notification):
            seen.append(notification["provider_idempotency_key"])
            raise ConnectionError("x")

    nid = _queue(db, armed=True)
    db.commit()
    send_notification(db, company_id=1, notification_id=nid, provider=Recorder())
    schedule_retry(db, company_id=1, notification_id=nid)
    db.commit()
    send_notification(db, company_id=1, notification_id=nid, provider=Recorder())

    assert len(seen) == 2
    # Anahtar attempt_count'a bağlı DEĞİL: değişmez kimlikten türer.
    assert seen[0] == seen[1] == f"notification:1:{nid}"


def test_k11_expired_lease_is_not_auto_reclaimed_without_idempotency(
    db: Session,
) -> None:
    from app.auth import utcnow

    class NonIdempotent(NotificationProvider):
        supports_idempotency = False

        def send(self, notification):  # pragma: no cover - çağrılmamalı
            raise AssertionError("çağrılmamalıydı")

    nid = _queue(db, armed=True)
    db.commit()
    db.execute(
        text(
            "UPDATE notifications SET status='PROCESSING', locked_until=:l, "
            "lock_token='eski' WHERE id=:i"
        ).bindparams(bindparam("l", type_=_TS)),
        {"l": utcnow() - timedelta(minutes=30), "i": nid},
    )
    db.commit()

    from app.notifications.service import NotificationBusyError

    with pytest.raises(NotificationBusyError):
        send_notification(
            db, company_id=1, notification_id=nid, provider=NonIdempotent()
        )


# ===========================================================================
# Kriter 12 / invariant: NoOp asla SENT yazmaz
# ===========================================================================
def test_k12_noop_never_reports_sent(db: Session) -> None:
    nid = _queue(db, armed=True)
    db.commit()
    result = send_notification(
        db, company_id=1, notification_id=nid, provider=NoOpNotificationProvider()
    )
    assert result.status == "NONE"
    assert _status(db, nid) == "NONE"
    assert _status(db, nid) != "SENT"


def test_k12_simulation_provider_uses_distinct_state(db: Session) -> None:
    nid = _queue(db, armed=True)
    db.commit()
    result = send_notification(
        db,
        company_id=1,
        notification_id=nid,
        provider=SimulationNotificationProvider(),
    )
    assert result.status == "SIMULATED"
    assert _status(db, nid) == "SIMULATED"


def test_k12_simulation_requires_explicit_opt_in() -> None:
    from types import SimpleNamespace

    from app.notifications.provider import get_notification_provider

    assert isinstance(
        get_notification_provider(SimpleNamespace()), NoOpNotificationProvider
    )
    assert isinstance(
        get_notification_provider(SimpleNamespace(notification_provider="simulation")),
        SimulationNotificationProvider,
    )


# ===========================================================================
# Kriter 16 / invariant 1,3,4,5,7,9,10 — disarm + arşiv yaşam döngüsü
# ===========================================================================
def test_i1_disarm_is_atomic_with_the_rule_transaction(db: Session) -> None:
    """Kural kapatma rollback olursa disarm da geri alınır."""
    first = _queue(db, armed=True, dedupe="r1", rule_id=42)
    second = _queue(db, armed=True, dedupe="r2", rule_id=42)
    db.commit()

    affected = disarm_rule_notifications(db, company_id=1, rule_id=42)
    assert affected == 2
    db.rollback()  # kuralı kapatan işlem düştü
    assert _armed(db, first) is True
    assert _armed(db, second) is True

    affected = disarm_rule_notifications(db, company_id=1, rule_id=42)
    db.commit()
    assert affected == 2
    assert _armed(db, first) is False


def test_i1_disarm_leaves_processing_rows_untouched(db: Session) -> None:
    processing = _queue(db, armed=True, dedupe="p", rule_id=7)
    pending = _queue(db, armed=True, dedupe="q", rule_id=7)
    db.commit()
    db.execute(
        text("UPDATE notifications SET status='PROCESSING' WHERE id=:i"),
        {"i": processing},
    )
    db.commit()

    assert disarm_rule_notifications(db, company_id=1, rule_id=7) == 1
    db.commit()
    assert _armed(db, processing) is True  # başlamış gönderime dokunulmaz
    assert _armed(db, pending) is False


def test_i1_disarm_is_tenant_scoped(db: Session) -> None:
    mine = _queue(db, company_id=1, armed=True, dedupe="a", rule_id=5)
    theirs = _queue(db, company_id=2, armed=True, dedupe="b", rule_id=5)
    db.commit()

    assert disarm_rule_notifications(db, company_id=1, rule_id=5) == 1
    db.commit()
    assert _armed(db, mine) is False
    assert _armed(db, theirs) is True  # başka kiracının satırı korunur


def test_i9_pending_metric_counts_only_armed_rows(db: Session) -> None:
    armed = _queue(db, armed=True, dedupe="m1")
    disarmed = _queue(db, armed=True, dedupe="m2")
    _queue(db, dedupe="m3")  # AWAITING_APPROVAL
    db.commit()
    _disarm_at(db, disarmed, days_ago=0)

    counters = outbox_counters(db, company_id=1)
    assert counters["pending"] == 1
    assert counters["disarmed"] == 1
    assert counters["awaiting_approval"] == 1
    assert armed  # kullanılmayan değişken uyarısını önler


def test_i3_archive_selection_predicate_is_in_sql(db: Session) -> None:
    from app.notifications.archive import _selection_sql

    sql = _selection_sql()
    assert "dispatch_armed = :disarmed" in sql
    assert "disarmed_at <= :threshold" in sql
    assert "status <> 'PROCESSING'" in sql


def test_i3_i4_archive_moves_only_expired_disarmed_rows_and_is_rerunnable(
    db: Session,
) -> None:
    expired = _queue(db, armed=True, dedupe="e1")
    fresh = _queue(db, armed=True, dedupe="e2")
    still_armed = _queue(db, armed=True, dedupe="e3")
    db.commit()
    _disarm_at(db, expired, days_ago=200)
    _disarm_at(db, fresh, days_ago=5)

    moved = archive_disarmed_notifications(db, company_id=1, retention_days=90)
    assert moved == 1

    remaining = {
        int(row[0])
        for row in db.execute(text("SELECT id FROM notifications")).all()
    }
    assert expired not in remaining
    assert {fresh, still_armed} <= remaining

    archived = db.execute(
        text("SELECT id, archived_by, archive_reason FROM notifications_archive")
    ).mappings().all()
    assert [int(row["id"]) for row in archived] == [expired]
    assert archived[0]["archived_by"] == "SYSTEM"

    # Invariant 4: yeniden çalıştırma kayıpsız ve mükerrersiz.
    assert archive_disarmed_notifications(db, company_id=1, retention_days=90) == 0
    assert (
        db.execute(text("SELECT COUNT(*) FROM notifications_archive")).scalar_one() == 1
    )


def test_i5_archive_is_tenant_isolated(db: Session) -> None:
    mine = _queue(db, company_id=1, armed=True, dedupe="t1")
    theirs = _queue(db, company_id=2, armed=True, dedupe="t2")
    db.commit()
    _disarm_at(db, mine, days_ago=200)
    _disarm_at(db, theirs, days_ago=200)

    assert archive_disarmed_notifications(db, company_id=1, retention_days=90) == 1
    archived = [
        int(row[0])
        for row in db.execute(text("SELECT id FROM notifications_archive")).all()
    ]
    assert archived == [mine]
    assert theirs in {
        int(row[0]) for row in db.execute(text("SELECT id FROM notifications")).all()
    }


def test_i6_retention_floor_enforced_in_service_layer() -> None:
    with pytest.raises(RetentionPolicyError):
        resolve_retention_days(MIN_RETENTION_DAYS - 1)
    with pytest.raises(RetentionPolicyError):
        resolve_retention_days(0)
    assert resolve_retention_days(MIN_RETENTION_DAYS) == MIN_RETENTION_DAYS
    assert resolve_retention_days(120) == 120


def test_i6_retention_floor_enforced_in_config_layer() -> None:
    from app.config import Settings

    with pytest.raises(Exception, match="30 günden kısa olamaz"):
        Settings(notification_disarmed_retention_days=10)
    assert (
        Settings(notification_disarmed_retention_days=45)
        .notification_disarmed_retention_days
        == 45
    )


def test_i7_archive_has_no_update_or_restore_path() -> None:
    """Arşiv tablosuna UPDATE/DELETE eden bir kod yolu bulunmamalıdır."""
    source = (BACKEND / "app").rglob("*.py")
    offenders: list[str] = []
    for path in source:
        text_content = path.read_text(encoding="utf-8")
        for statement in ("UPDATE notifications_archive", "DELETE FROM notifications_archive"):
            if statement in text_content:
                offenders.append(f"{path.name}: {statement}")
    assert offenders == []

    # Kaynağa geri taşıyan bir INSERT de olmamalı.
    for path in (BACKEND / "app").rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        assert "INSERT INTO notifications " not in content.replace(
            "INSERT INTO notifications (", "INSERT INTO notifications ("
        ) or "notifications_archive" not in content


def test_i7_archive_rows_are_immutable_at_database_level(tmp_path: Path) -> None:
    """Migration'ın trigger'ı: arşiv satırı güncellenemez ve silinemez."""
    database = tmp_path / "immutable.db"
    script = f"""
import os
os.environ["DATABASE_URL"] = "sqlite:///{database.as_posix()}"
import app.main  # noqa: F401  migrationları çalıştırır
from sqlalchemy import text
from app.db import SessionLocal

with SessionLocal() as db:
    db.execute(text(
        "INSERT INTO notifications_archive (id, company_id, type, channel, recipient,"
        " template, payload, status, attempt_count, created_at, updated_at,"
        " max_attempts, dispatch_armed, compliance_attempt_count,"
        " archived_at, archived_by, archive_reason)"
        " VALUES (1, 1, 't', 'SMS', 'r', 'tpl', '{{}}', 'PENDING', 0,"
        " '2026-01-01', '2026-01-01', 5, 0, 0, '2026-01-01', 'SYSTEM', 'test')"
    ))
    db.commit()
    for statement in (
        "UPDATE notifications_archive SET status='SENT' WHERE id=1",
        "DELETE FROM notifications_archive WHERE id=1",
    ):
        try:
            db.execute(text(statement))
            db.commit()
            raise SystemExit("ARSIV DEGISTIRILEBILDI: " + statement)
        except SystemExit:
            raise
        except Exception:
            db.rollback()
print("ARCHIVE_IMMUTABLE_OK")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        text=True,
        capture_output=True,
        timeout=300,
        env={**__import__("os").environ, "PYTHONPATH": str(BACKEND)},
    )
    assert "ARCHIVE_IMMUTABLE_OK" in completed.stdout, (
        completed.stdout + completed.stderr
    )


def test_i10_disarmed_rows_display_as_will_not_send(db: Session) -> None:
    from app.routers.notifications import _display_state

    disarmed = {"status": "PENDING", "dispatch_armed": False}
    assert _display_state(disarmed) == "Gönderilmeyecek — kural kapatıldı"
    # Belirsiz rozetler kullanılmaz.
    assert "bekl" not in _display_state(disarmed).lower()
    assert _display_state({"status": "PENDING", "dispatch_armed": True}) != (
        _display_state(disarmed)
    )


# ===========================================================================
# Kriter 8 — actor/zaman audit
# ===========================================================================
def test_k8_approval_records_actor_and_timestamp(db: Session) -> None:
    nid = _queue(db, content_hash="f" * 64, created_by=3)
    db.commit()
    approve_notification(
        db, company_id=1, notification_id=nid, approver_id=4, seen_hash="f" * 64
    )
    db.commit()
    row = db.execute(
        text(
            "SELECT created_by,approved_by,approved_at,approval_mode "
            "FROM notifications WHERE id=:i"
        ),
        {"i": nid},
    ).mappings().one()
    assert row["created_by"] == 3
    assert row["approved_by"] == 4
    assert row["approved_at"] is not None
    assert row["approval_mode"] == "MANUAL"


def test_k8_notification_events_are_in_the_activity_catalog() -> None:
    from app.activity_log import ACTION_TYPES, RESOURCE_TYPES

    for action in (
        "notification.queued",
        "notification.approved",
        "notification.cancelled",
        "notification.sent",
        "notification.failed",
        "notification.consent_granted",
        "notification.consent_revoked",
        "notification.rows_disarmed",
    ):
        assert action in ACTION_TYPES
    for resource in ("notification", "notification_template", "notification_consent"):
        assert resource in RESOURCE_TYPES


# ===========================================================================
# Kriter 9 — mesaj sınıfı zorunlu, sınıf karışmaz
# ===========================================================================
def test_k9_template_requires_explicit_message_class(db: Session) -> None:
    from app.notifications.templates import TemplateError, create_template

    with pytest.raises(TemplateError, match="SERVICE_TRANSACTIONAL veya COMMERCIAL"):
        create_template(
            db,
            company_id=1,
            code="x",
            channel="SMS",
            name="x",
            body="Merhaba.",
            message_class="",
            user_id=1,
        )


def test_k9_message_class_is_not_null_in_database(tmp_path: Path) -> None:
    from app.notifications.schema import notification_templates

    column = notification_templates.c.message_class
    assert column.nullable is False
    assert column.server_default is None  # varsayılan YOK


def test_k9_body_change_deactivates_template_until_reapproved(db: Session) -> None:
    from app.notifications.templates import (
        approve_template,
        create_template,
        update_template,
    )

    # İzole şema fixture'ı templates tablosunu da kurar.
    created = create_template(
        db,
        company_id=1,
        code="service.reminder",
        channel="SMS",
        name="Servis",
        body="Merhaba {musteri_adi}.",
        message_class="SERVICE_TRANSACTIONAL",
        user_id=1,
    )
    db.commit()
    assert created["is_active"] is False or created["is_active"] == 0

    approved = approve_template(
        db, company_id=1, template_id=int(created["id"]), user_id=2
    )
    db.commit()
    assert bool(approved["is_active"]) is True

    updated = update_template(
        db,
        company_id=1,
        template_id=int(created["id"]),
        name=None,
        body="Merhaba {musteri_adi}. Kampanyamiz var.",
        message_class=None,
        user_id=1,
    )
    db.commit()
    assert bool(updated["is_active"]) is False  # yeniden onaya düşer
    assert int(updated["version"]) == int(created["version"]) + 1
    assert updated["approved_by"] is None


# ===========================================================================
# PR #175 inceleme kapanışları
# ===========================================================================
def test_r1_simulated_is_never_counted_as_sent(db: Session) -> None:
    """SIMULATED ayrı metriktir: sent = SENT + DELIVERED, başka hiçbir şey."""
    nid = _queue(db, armed=True)
    db.commit()
    result = send_notification(
        db,
        company_id=1,
        notification_id=nid,
        provider=SimulationNotificationProvider(),
    )
    assert result.status == "SIMULATED"

    counters = outbox_counters(db, company_id=1)
    assert counters["sent"] == 0
    assert counters["simulated"] == 1

    # "Gönderilenler" sekmesinin SQL yüklemi SIMULATED içermez; simüle
    # satırların kendi görünümü vardır.
    from app.routers.notifications import _TAB_FILTERS

    assert "SIMULATED" not in _TAB_FILTERS["sent"]
    assert _TAB_FILTERS["simulated"] == "status = 'SIMULATED'"

    sent_rows = db.execute(
        text(
            f"SELECT id FROM notifications WHERE company_id=1 AND {_TAB_FILTERS['sent']}"
        )
    ).all()
    assert sent_rows == []
    simulated_rows = db.execute(
        text(
            "SELECT id FROM notifications WHERE company_id=1 AND "
            f"{_TAB_FILTERS['simulated']}"
        )
    ).all()
    assert [int(row[0]) for row in simulated_rows] == [nid]


def test_r2_no_open_transaction_during_provider_call(db: Session) -> None:
    """Rıza okuma transaction'ı provider çağrısından önce kapatılır (§3.3)."""
    observed: list[bool] = []

    class TransactionProbe(NotificationProvider):
        supports_idempotency = True

        def send(self, notification):
            observed.append(db.in_transaction())
            return NotificationResult(status="SENT", external_id="probe-1")

    # SMS kanalı: rıza kapısı gerçekten SELECT atar. party bilgisi payload'da
    # yok → kapı fail-closed reddeder... bu senaryo provider'a ulaşmaz. Bu
    # yüzden rıza kapısından muaf kanalla değil, rıza OKUMASI yapan kanalla
    # test etmek için önce izin kaydı açılır.
    from app.notifications import set_consent

    set_consent(
        db,
        company_id=1,
        party_type="CUSTOMER",
        party_id=777,
        channel="SMS",
        granted=True,
        source="FORM",
        source_ref=None,
        recipient="+905321112233",
        user_id=1,
    )
    nid = _queue(
        db,
        channel="SMS",
        armed=True,
        payload={"party_type": "CUSTOMER", "party_id": 777},
    )
    db.commit()

    result = send_notification(
        db, company_id=1, notification_id=nid, provider=TransactionProbe()
    )
    assert result.status == "SENT"
    assert observed == [False], (
        "provider çağrısı sırasında bağlantıda açık transaction bulundu"
    )


def test_r4_attempt_count_reaches_max_attempts_exactly(db: Session) -> None:
    """max_attempts=5 → satır 5. GERÇEK başarısız denemede FAILED olur, 4.'de değil."""
    nid = _queue(db, armed=True)
    db.commit()

    for expected_attempt in range(1, 6):
        result = send_notification(
            db, company_id=1, notification_id=nid, provider=_Boom()
        )
        assert result.status == "FAILED"
        row = db.execute(
            text("SELECT status, attempt_count FROM notifications WHERE id=:i"),
            {"i": nid},
        ).mappings().one()
        assert row["attempt_count"] == expected_attempt
        if expected_attempt < 5:
            assert row["status"] == "RETRY_SCHEDULED", (
                f"{expected_attempt}. denemede erken FAILED: off-by-one"
            )
            # Backoff'u bekletmeden bir sonraki denemeyi mümkün kıl.
            db.execute(
                text(
                    "UPDATE notifications SET next_attempt_at=:n WHERE id=:i"
                ).bindparams(bindparam("n", type_=_TS)),
                {"n": __import__("app.auth", fromlist=["utcnow"]).utcnow(), "i": nid},
            )
            db.commit()
        else:
            assert row["status"] == "FAILED"


def test_r6a_non_converging_encoding_is_rejected() -> None:
    """5 tur decode sonunda hâlâ değişen girdi açıkça reddedilir."""
    # Her turda bir katman açılan 7 katmanlı %-kodlama: 5 turda sabit noktaya
    # ulaşmaz. quote ile üst üste kodlanır.
    from urllib.parse import quote

    payload = "http://x"
    for _ in range(7):
        payload = quote(payload, safe="")
    with pytest.raises(content_gate.ContentRejected) as excinfo:
        content_gate.assert_content_allowed(payload)
    # CHARSET değil ENCODING/decode yolundan reddedilmesi şart değil — kapı
    # fail-closed olduğu sürece hangi kural yakalarsa yakalasın; ama decode
    # fonksiyonunun kendisi de tek başına reddetmeli:
    with pytest.raises(content_gate.ContentRejected) as decode_exc:
        content_gate._decode_layers(payload)
    assert decode_exc.value.category == "ENCODING"
    assert excinfo.value is not None


def test_r6b_bare_domain_rejected_regardless_of_tld() -> None:
    """Çıplak-domain reddi TLD listesinden bağımsızdır: yeni TLD deliği yok."""
    for payload in ("ornek.zzz", "kotu-site.yenitld", "tikla.abcdef"):
        with pytest.raises(content_gate.ContentRejected) as excinfo:
            content_gate.assert_content_allowed(payload)
        assert excinfo.value.category == "DOMAIN", payload
    # Sayısal kalıplar (tarih, saat, tutar) bu desene girmez.
    content_gate.assert_content_allowed("Randevunuz 12.08.2026 saat 09:30.")
    content_gate.assert_content_allowed("Tutar: 4.500,00 TL")


def test_r6c_four_eyes_is_enforced_in_the_cas_where_clause(db: Session) -> None:
    """TOCTOU: okuma anında created_by farklı görünse bile UPDATE anında
    onaylayanla eşleşiyorsa CAS satırı düşürür — güvence DB yolundadır."""
    from app.notifications import service as service_module

    nid = _queue(db, content_hash="a1" * 32, created_by=None)
    db.commit()

    # Python ön-kontrolüne bayat satır göster (created_by=None → geçer),
    # veritabanında ise satır onaylayana ait olsun.
    real_row = service_module._notification_row(db, 1, nid)
    stale = dict(real_row)
    stale["created_by"] = None
    db.execute(
        text("UPDATE notifications SET created_by=:u WHERE id=:i"),
        {"u": 42, "i": nid},
    )
    db.commit()

    original = service_module._notification_row
    service_module._notification_row = lambda *args, **kwargs: dict(stale)
    try:
        with pytest.raises(NotificationApprovalError):
            approve_notification(
                db,
                company_id=1,
                notification_id=nid,
                approver_id=42,
                seen_hash="a1" * 32,
            )
    finally:
        service_module._notification_row = original

    status = db.execute(
        text("SELECT status FROM notifications WHERE id=:i"), {"i": nid}
    ).scalar_one()
    assert status == "AWAITING_APPROVAL"
