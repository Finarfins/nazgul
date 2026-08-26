"""F0-email — PostgreSQL 16 ikizi: eşzamanlı drenaj ve seçici yüklemi.

SQLite üzerinde geçen bir claim testi, PG'de boolean/timestamptz dialect
farkları ve gerçek satır kilitleri yüzünden aynı şeyi kanıtlamaz. Bu dosya
F0-email'in iki iddiasını gerçek PG16'ya karşı doğrular:

* **T12 — eşzamanlı drenaj.** Aynı satırı iki drenaj alamaz: lease sahibi
  gönderirken ikinci süreç ``NotificationBusyError`` alır ve SMTP taşıyıcısı
  tam olarak BİR kez çağrılır. SMTP idempotent olmadığı için
  (``supports_idempotency = False``) bu, çift mailin tek savunmasıdır.
* **Seçici pariteti.** ``claimable_notification_ids`` yüklemi
  ``_claim_notification``'ınkiyle aynı satırları PG'de de seçer; seçici
  gevşerse betik boşa claim eder, sıkışırsa satır hiç görünmez.

Testler ``NOTIFICATIONS_TEST_DATABASE_URL`` / ``APP_TEST_DATABASE_URL``
yoksa atlanır — dosya adı ve marker ``test_notifications_f1_postgresql.py``
presedanını izler.
"""

from __future__ import annotations

import os
import smtplib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.postgresql

VERIFICATION_LINK = "https://erp.ornek.test/eposta-dogrula?token=pg-token"


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
            db.execute(text("SELECT id FROM companies ORDER BY id LIMIT 1")).scalar_one()
        )


class _SmtpConfig:
    """Taşıyıcı yapılandırması — gerçek ``Settings`` yerine dar bir ikiz."""

    smtp_host = "smtp.ornek.test"
    smtp_port = 587
    smtp_username = None
    smtp_password = None
    smtp_from_email = "bildirim@ornek.test"
    smtp_use_tls = True
    notification_provider = "smtp"


def _queue_verification_row(db, company_id: int, *, dedupe: str) -> int:
    """Sistem şeridi satırı: PENDING + silahlı + SYSTEM onaylı."""
    from app.notifications import enqueue_notification
    from app.notifications.schema import SYSTEM_APPROVAL_MODE, SYSTEM_APPROVER_ID

    return int(
        enqueue_notification(
            db,
            company_id=company_id,
            type_="EMAIL_VERIFICATION",
            channel="EMAIL",
            recipient="kullanici@ornek.test",
            template="email_verification",
            payload_dict={
                "subject": "E-posta adresinizi doğrulayın",
                "body": f"Bağlantı:\n{VERIFICATION_LINK}",
                "verification_url": VERIFICATION_LINK,
            },
            dedupe_key=dedupe,
            armed=True,
            approved_by=SYSTEM_APPROVER_ID,
            approval_mode=SYSTEM_APPROVAL_MODE,
        )
    )


# ===========================================================================
# T12 — eşzamanlı drenaj: lease sahibi gönderirken ikinci süreç claim edemez
# ===========================================================================
@pytest.mark.postgresql
def test_t12_concurrent_drain_sends_exactly_once_on_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """İki drenaj aynı satırı yarıştırır; tam olarak bir mail çıkar.

    Yarış "umut ederek" değil, el sıkışarak kurulur: taşıyıcı gönderimin
    ORTASINDA durur (``in_send``), ikinci süreç tam o anda claim dener ve
    lease'in PG'de gerçekten commit edilmiş olduğunu ayrı bir bağlantıdan
    görür. Böylece kaybedenin ``NotificationBusyError`` alması zamanlamaya
    değil, yüklemin kendisine bağlıdır.
    """
    company_id = _bootstrap(monkeypatch)

    from app.db import SessionLocal
    from app.notifications import NotificationBusyError, send_notification
    from app.notifications.provider import SmtpEmailNotificationProvider

    in_send = threading.Event()
    loser_finished = threading.Event()
    sent_lock = threading.Lock()
    sent_messages: list[object] = []

    class _BlockingSMTP:
        """Gönderimin ortasında durur: lease bu sırada sahibinde kalır."""

        def __init__(self, host, port, timeout=None):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def starttls(self):
            return None

        def login(self, username, password):
            return None

        def send_message(self, message):
            with sent_lock:
                sent_messages.append(message)
            in_send.set()
            # Kaybeden denemesini bitirene kadar lease serbest bırakılmaz.
            assert loser_finished.wait(timeout=30), "ikinci süreç hiç denemedi"

    monkeypatch.setattr(smtplib, "SMTP", _BlockingSMTP)

    with SessionLocal() as db:
        notification_id = _queue_verification_row(db, company_id, dedupe="pg-t12")
        db.commit()

    def winner() -> str:
        with SessionLocal() as db:
            return send_notification(
                db,
                company_id=company_id,
                notification_id=notification_id,
                provider=SmtpEmailNotificationProvider(_SmtpConfig()),
            ).status

    def loser() -> str:
        try:
            assert in_send.wait(timeout=30), "birinci süreç gönderime hiç girmedi"
            with SessionLocal() as db:
                # Lease sahibi PROCESSING'de; bu claim yüklemi geçemez.
                send_notification(
                    db,
                    company_id=company_id,
                    notification_id=notification_id,
                    provider=SmtpEmailNotificationProvider(_SmtpConfig()),
                )
            return "BEKLENMEDIK_GONDERIM"
        except NotificationBusyError:
            return "BUSY"
        finally:
            loser_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        winner_future = pool.submit(winner)
        loser_future = pool.submit(loser)
        loser_outcome = loser_future.result(timeout=60)
        winner_outcome = winner_future.result(timeout=60)

    assert winner_outcome == "SENT"
    assert loser_outcome == "BUSY"
    # Asıl iddia: taşıyıcı bir kez çağrıldı — kullanıcı tek mail aldı.
    assert len(sent_messages) == 1

    from sqlalchemy import text

    with SessionLocal() as db:
        row = db.execute(
            text(
                "SELECT status, attempt_count, external_id FROM notifications "
                "WHERE id=:i AND company_id=:cid"
            ),
            {"i": notification_id, "cid": company_id},
        ).mappings().one()
    assert row["status"] == "SENT"
    # Kaybeden claim edemediği için attempt_count'u artıramaz.
    assert int(row["attempt_count"]) == 1
    assert row["external_id"]


# ===========================================================================
# Seçici pariteti — drenaj yüklemi PG'de de claim yüklemiyle aynı satırları verir
# ===========================================================================
@pytest.mark.postgresql
def test_claimable_selector_matches_claim_predicate_on_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silahsız/onaysız/durdurulmuş satırlar PG'de de seçilemez.

    Boolean ve timestamptz karşılaştırmaları SQLite ile aynı sonucu vermelidir;
    aksi hâlde drenaj ya boşa claim eder ya da satırı hiç görmez.
    """
    company_id = _bootstrap(monkeypatch)

    from sqlalchemy import DateTime, bindparam, text

    from app.auth import utcnow
    from app.db import SessionLocal
    from app.notifications import claimable_notification_ids

    ts = DateTime(timezone=True)
    with SessionLocal() as db:
        armed = _queue_verification_row(db, company_id, dedupe="pg-sel-armed")
        unapproved = int(
            db.execute(
                text(
                    "INSERT INTO notifications"
                    "(company_id,type,channel,recipient,template,payload,status,"
                    " created_at,updated_at,dispatch_armed,attempt_count)"
                    " VALUES (:cid,'EMAIL_VERIFICATION','EMAIL','a@b.test','t','{}',"
                    " 'AWAITING_APPROVAL',:now,:now,FALSE,0) RETURNING id"
                ).bindparams(bindparam("now", type_=ts)),
                {"cid": company_id, "now": utcnow()},
            ).scalar_one()
        )
        disarmed = _queue_verification_row(db, company_id, dedupe="pg-sel-disarmed")
        db.commit()
        db.execute(
            text(
                "UPDATE notifications SET dispatch_armed=FALSE "
                "WHERE id=:i AND company_id=:cid"
            ),
            {"i": disarmed, "cid": company_id},
        )
        db.commit()

        selectable = {
            notification_id
            for _company, notification_id in claimable_notification_ids(
                db, limit=100, now=utcnow(), company_id=company_id
            )
        }

    assert armed in selectable
    assert unapproved not in selectable
    assert disarmed not in selectable


# ===========================================================================
# T18 ikizi — drenaj seçicisi kiracıyı aşmaz
# ===========================================================================
@pytest.mark.postgresql
def test_drain_selector_is_tenant_scoped_on_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--company A`` ile çalışan drenaj B'nin satırını göremez."""
    company_id = _bootstrap(monkeypatch)

    from sqlalchemy import text

    from app.auth import utcnow
    from app.db import SessionLocal
    from app.notifications import claimable_notification_ids

    other_company = company_id + 90_100
    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO companies(id,name,is_active,created_at) "
                "VALUES (:id,'F0 PG Diger',TRUE,CURRENT_TIMESTAMP) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": other_company},
        )
        db.commit()
        mine = _queue_verification_row(db, company_id, dedupe="pg-tenant-mine")
        theirs = _queue_verification_row(db, other_company, dedupe="pg-tenant-theirs")
        db.commit()

        scoped = {
            notification_id
            for _company, notification_id in claimable_notification_ids(
                db, limit=100, now=utcnow(), company_id=company_id
            )
        }

    assert mine in scoped
    assert theirs not in scoped
