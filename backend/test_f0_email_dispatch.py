"""F0-email — SMTP taşıyıcısı, sistem şeridi ve drenaj seçicileri.

Test numaraları F0 tasarım raporundaki matristen gelir (T1-T18). HTTP
seviyesindeki ikizler ``test_f0_email_registration.py`` içindedir; PostgreSQL
parite dosyası ``test_notifications_f1_postgresql.py``'nin kapsadığı lease/CAS
yüklemi bu dosyada SQLite üzerinde doğrulanır.
"""

from __future__ import annotations

import smtplib
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.auth import utcnow
from app.config import Settings
from app.notifications.content_gate import ContentRejected, assert_content_allowed
from app.notifications.provider import (
    NoOpNotificationProvider,
    SmtpEmailNotificationProvider,
    get_notification_provider,
)
from app.notifications.schema import (
    AUTH_SYSTEM_TYPES,
    SYSTEM_APPROVAL_MODE,
    SYSTEM_APPROVER_ID,
    VERIFICATION_TTL_HOURS,
    metadata as notification_metadata,
    notifications,
)
from app.notifications.service import (
    NotificationBusyError,
    cancel_expired_verification_notifications,
    claimable_notification_ids,
    enqueue_notification,
    send_notification,
    stuck_processing_count,
)

VERIFICATION_LINK = "https://erp.ornek.test/eposta-dogrula?token=ornek-token"
VERIFICATION_BODY = (
    "Sungur Tarım ERP hesabınızı doğrulamak için 24 saat içinde bağlantıyı "
    f"açın:\n{VERIFICATION_LINK}"
)


# ---------------------------------------------------------------------------
# Sahte SMTP: ağa çıkmaz, çağrı sırasını kaydeder.
# ---------------------------------------------------------------------------
class _FakeSMTP:
    instances: list["_FakeSMTP"] = []
    failure: BaseException | None = None

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls: list[object] = []
        self.messages: list[object] = []
        type(self).instances.append(self)

    def __enter__(self) -> "_FakeSMTP":
        self.calls.append("open")
        return self

    def __exit__(self, *_exc) -> bool:
        self.calls.append("close")
        return False

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, username, password) -> None:
        self.calls.append(("login", username, password))

    def send_message(self, message) -> None:
        self.calls.append("send")
        self.messages.append(message)
        if type(self).failure is not None:
            raise type(self).failure


class _ExplodingSMTP:
    """Kurulması bile testi düşürür: 'ağa çıkmadı' iddiasının kanıtı."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("SMTP bağlantısı kurulmamalıydı")


class _SmtpConfig:
    def __init__(self, **overrides):
        self.smtp_host = "smtp.ornek.test"
        self.smtp_port = 587
        self.smtp_username = None
        self.smtp_password = None
        self.smtp_from_email = "bildirim@ornek.test"
        self.smtp_from_name = None
        self.smtp_use_tls = True
        self.notification_provider = "smtp"
        for key, value in overrides.items():
            setattr(self, key, value)


@pytest.fixture()
def fake_smtp(monkeypatch: pytest.MonkeyPatch):
    _FakeSMTP.instances = []
    _FakeSMTP.failure = None
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    yield _FakeSMTP
    _FakeSMTP.instances = []
    _FakeSMTP.failure = None


@pytest.fixture()
def no_network(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(smtplib, "SMTP", _ExplodingSMTP)


@pytest.fixture()
def db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'f0_email.db').as_posix()}")
    notification_metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _queue_auth_row(
    db: Session,
    *,
    company_id: int = 1,
    type_: str = "EMAIL_VERIFICATION",
    recipient: str = "kullanici@ornek.test",
    subject: str = "E-posta adresinizi doğrulayın",
    body: str = VERIFICATION_BODY,
    dedupe: str | None = None,
    extra_payload: dict | None = None,
) -> int:
    payload = {"subject": subject, "body": body, "verification_url": VERIFICATION_LINK}
    payload.update(extra_payload or {})
    notification_id = enqueue_notification(
        db,
        company_id=company_id,
        type_=type_,
        channel="EMAIL",
        recipient=recipient,
        template="email_verification",
        payload_dict=payload,
        dedupe_key=dedupe,
        armed=True,
        approved_by=SYSTEM_APPROVER_ID,
        approval_mode=SYSTEM_APPROVAL_MODE,
    )
    db.commit()
    return int(notification_id)


def _row(db: Session, notification_id: int) -> dict:
    db.rollback()
    return dict(
        db.execute(
            select(notifications).where(notifications.c.id == notification_id)
        ).mappings().one()
    )


# ===========================================================================
# T1 — SMTP yok: ağ çağrısı yok, satır NONE
# ===========================================================================
def test_t1_without_smtp_no_network_call_and_status_none(
    db: Session, no_network
) -> None:
    notification_id = _queue_auth_row(db)
    provider = SmtpEmailNotificationProvider(_SmtpConfig(smtp_host=None))

    result = send_notification(
        db, company_id=1, notification_id=notification_id, provider=provider
    )

    assert result.status == "NONE"
    row = _row(db, notification_id)
    assert row["status"] == "NONE"
    assert row["external_id"] is None
    # Varsayılan fabrika hiçbir koşulda SMTP seçmez.
    assert isinstance(get_notification_provider(object()), NoOpNotificationProvider)


# ===========================================================================
# T2 — mutlu yol: starttls → login → send, kararlı Message-ID
# ===========================================================================
def test_t2_happy_path_orders_tls_login_send(db: Session, fake_smtp) -> None:
    notification_id = _queue_auth_row(db)
    provider = SmtpEmailNotificationProvider(
        _SmtpConfig(smtp_username="kullanici", smtp_password="parola")
    )

    result = send_notification(
        db, company_id=1, notification_id=notification_id, provider=provider
    )

    assert result.status == "SENT"
    client = fake_smtp.instances[0]
    assert client.calls == [
        "open",
        "starttls",
        ("login", "kullanici", "parola"),
        "send",
        "close",
    ]
    assert client.timeout == 15
    message = client.messages[0]
    assert message["To"] == "kullanici@ornek.test"
    assert message["From"] == "bildirim@ornek.test"
    assert message["Subject"] == "E-posta adresinizi doğrulayın"
    assert VERIFICATION_LINK in message.get_content()

    row = _row(db, notification_id)
    assert row["status"] == "SENT"
    assert row["attempt_count"] == 1
    assert row["last_error"] is None
    assert row["last_error_code"] is None
    # Message-ID outbox kimliğinden türer; denemeler arası sabittir.
    assert row["external_id"] == f"<notification-1-{notification_id}@ornek.test>"
    assert result.external_id == row["external_id"]


def test_t2b_secret_password_and_display_name_are_used(db: Session, fake_smtp) -> None:
    notification_id = _queue_auth_row(db)
    provider = SmtpEmailNotificationProvider(
        _SmtpConfig(
            smtp_username="kullanici",
            smtp_password=SecretStr("parola"),
            smtp_from_name="Sungur Tarım ERP",
        )
    )

    send_notification(
        db, company_id=1, notification_id=notification_id, provider=provider
    )

    client = fake_smtp.instances[0]
    assert ("login", "kullanici", "parola") in client.calls
    assert client.messages[0]["From"] == "Sungur Tarım ERP <bildirim@ornek.test>"


# ===========================================================================
# T3 — TLS kapalı ve kullanıcı adı yok: starttls/login çağrılmaz
# ===========================================================================
def test_t3_without_tls_and_credentials_skips_starttls_and_login(
    db: Session, fake_smtp
) -> None:
    notification_id = _queue_auth_row(db)
    provider = SmtpEmailNotificationProvider(_SmtpConfig(smtp_use_tls=False))

    send_notification(
        db, company_id=1, notification_id=notification_id, provider=provider
    )

    assert fake_smtp.instances[0].calls == ["open", "send", "close"]


# ===========================================================================
# T4 — kimlik hatası: AUTH sınıfı, retry planlanır, sunucu metni sızmaz
# ===========================================================================
def test_t4_auth_error_is_classified_masked_and_retried(db: Session, fake_smtp) -> None:
    notification_id = _queue_auth_row(db)
    fake_smtp.failure = smtplib.SMTPAuthenticationError(
        535, b"5.7.8 gizli-sunucu-detayi"
    )
    provider = SmtpEmailNotificationProvider(_SmtpConfig())

    result = send_notification(
        db, company_id=1, notification_id=notification_id, provider=provider
    )

    assert result.status == "FAILED"
    row = _row(db, notification_id)
    assert row["status"] == "RETRY_SCHEDULED"
    assert row["last_error_code"] == "AUTH"
    assert row["last_error"] == "Bildirim sağlayıcısı kimlik doğrulaması başarısız."
    assert row["next_attempt_at"] is not None
    assert "gizli-sunucu-detayi" not in str(row)
    assert "gizli-sunucu-detayi" not in (result.message or "")


def test_t4b_smtp_credentials_are_redacted_from_logs(
    db: Session, fake_smtp, caplog: pytest.LogCaptureFixture
) -> None:
    password = "smtp-gizli-parola"
    username = "smtp-gizli-kullanici"
    notification_id = _queue_auth_row(db)
    provider = SmtpEmailNotificationProvider(
        _SmtpConfig(
            smtp_username=username,
            smtp_password=SecretStr(password),
        )
    )
    fake_smtp.failure = RuntimeError(f"kimlik bilgileri: {username}/{password}")

    with caplog.at_level("ERROR"):
        send_notification(
            db, company_id=1, notification_id=notification_id, provider=provider
        )

    assert password not in caplog.text
    assert username not in caplog.text
    assert "***" in caplog.text


# ===========================================================================
# T5 — ağ hatası: NETWORK sınıfı, backoff planlanır
# ===========================================================================
def test_t5_network_error_is_classified_and_backed_off(db: Session, fake_smtp) -> None:
    notification_id = _queue_auth_row(db)
    fake_smtp.failure = smtplib.SMTPServerDisconnected("baglanti koptu")
    provider = SmtpEmailNotificationProvider(_SmtpConfig())

    send_notification(
        db, company_id=1, notification_id=notification_id, provider=provider
    )

    row = _row(db, notification_id)
    assert row["status"] == "RETRY_SCHEDULED"
    assert row["last_error_code"] == "NETWORK"
    assert row["last_error"] == "Bildirim sağlayıcısına ulaşılamadı."
    assert row["next_attempt_at"] is not None


# ===========================================================================
# T6 — kalıcı ret (AK-3): VALIDATION retry ÜRETMEZ
# ===========================================================================
def test_t6_permanent_rejection_is_terminal_without_retry(
    db: Session, fake_smtp
) -> None:
    notification_id = _queue_auth_row(db)
    fake_smtp.failure = smtplib.SMTPRecipientsRefused({})
    provider = SmtpEmailNotificationProvider(_SmtpConfig())

    send_notification(
        db, company_id=1, notification_id=notification_id, provider=provider
    )

    row = _row(db, notification_id)
    assert row["status"] == "FAILED"
    assert row["last_error_code"] == "VALIDATION"
    assert row["next_attempt_at"] is None
    assert row["attempt_count"] == 1  # 5 denemeye yayılmadı


def test_t6b_missing_body_is_validation_not_retried(db: Session, fake_smtp) -> None:
    notification_id = _queue_auth_row(db, subject="Konu", body="")
    provider = SmtpEmailNotificationProvider(_SmtpConfig())

    send_notification(
        db, company_id=1, notification_id=notification_id, provider=provider
    )

    row = _row(db, notification_id)
    assert row["status"] == "FAILED"
    assert row["last_error_code"] == "VALIDATION"
    assert row["next_attempt_at"] is None
    assert fake_smtp.instances == []  # gövde yoksa bağlantı bile açılmaz


# ===========================================================================
# T7 — max_attempts tükenişi: off-by-one yok
# ===========================================================================
def test_t7_retry_budget_is_exhausted_exactly(db: Session, fake_smtp) -> None:
    notification_id = _queue_auth_row(db)
    db.execute(
        update(notifications)
        .where(notifications.c.id == notification_id)
        .values(max_attempts=2)
    )
    db.commit()
    fake_smtp.failure = smtplib.SMTPServerDisconnected("kopuk")
    provider = SmtpEmailNotificationProvider(_SmtpConfig())

    send_notification(
        db, company_id=1, notification_id=notification_id, provider=provider
    )
    assert _row(db, notification_id)["status"] == "RETRY_SCHEDULED"

    # Backoff'u beklemeden ikinci denemeyi zorla.
    db.execute(
        update(notifications)
        .where(notifications.c.id == notification_id)
        .values(next_attempt_at=utcnow())
    )
    db.commit()
    send_notification(
        db, company_id=1, notification_id=notification_id, provider=provider
    )

    row = _row(db, notification_id)
    assert row["status"] == "FAILED"
    assert row["attempt_count"] == 2
    assert row["next_attempt_at"] is None


# ===========================================================================
# T10 — rıza muafiyeti + regresyon kilidi
# ===========================================================================
@pytest.mark.parametrize("auth_type", sorted(AUTH_SYSTEM_TYPES))
def test_t10_auth_lane_is_exempt_from_consent_gate(
    db: Session, fake_smtp, auth_type: str
) -> None:
    notification_id = _queue_auth_row(db, type_=auth_type, dedupe=f"d-{auth_type}")
    provider = SmtpEmailNotificationProvider(_SmtpConfig())

    result = send_notification(
        db, company_id=1, notification_id=notification_id, provider=provider
    )

    assert result.status == "SENT"
    row = _row(db, notification_id)
    assert row["status"] == "SENT"
    assert row["consent_decision"] is None


def test_t10b_non_auth_email_row_is_still_blocked(db: Session, no_network) -> None:
    """Muafiyet KAPALI kümeyle sınırlıdır: sıradan EMAIL satırı hâlâ elenir."""
    notification_id = _queue_auth_row(db, type_="SERVICE_REMINDER")
    provider = SmtpEmailNotificationProvider(_SmtpConfig())

    result = send_notification(
        db, company_id=1, notification_id=notification_id, provider=provider
    )

    assert result.status == "FAILED"
    row = _row(db, notification_id)
    assert row["status"] == "REJECTED"
    assert row["consent_decision"] == "BLOCKED"
    assert row["consent_decision_reason"] == "TARGET_MISSING"


# ===========================================================================
# T11 — şablon-sızma kilidi: doğrulama gövdesi içerik kapısından GEÇEMEZ
# ===========================================================================
def test_t11_verification_body_cannot_enter_template_pipeline() -> None:
    with pytest.raises(ContentRejected):
        assert_content_allowed(VERIFICATION_BODY, channel="EMAIL")


# ===========================================================================
# T12 — eşzamanlı drenaj: ikinci claim meşgul/yeniden-gönderilemez hatası alır
# ===========================================================================
def test_t12_second_dispatcher_cannot_claim_the_same_row(
    tmp_path: Path, fake_smtp
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'race.db').as_posix()}")
    notification_metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    first, second = factory(), factory()
    try:
        notification_id = _queue_auth_row(first)
        provider = SmtpEmailNotificationProvider(_SmtpConfig())
        assert (
            send_notification(
                first, company_id=1, notification_id=notification_id, provider=provider
            ).status
            == "SENT"
        )
        with pytest.raises(Exception) as excinfo:
            send_notification(
                second, company_id=1, notification_id=notification_id, provider=provider
            )
        assert isinstance(excinfo.value, (NotificationBusyError, RuntimeError))
        assert len(fake_smtp.instances) == 1  # tek gerçek gönderim
    finally:
        first.close()
        second.close()
        engine.dispose()


# ===========================================================================
# T13 — TTL: süresi dolmuş doğrulama satırı gönderilmez, iptal edilir
# ===========================================================================
def test_t13_expired_verification_row_is_cancelled_not_sent(
    db: Session, no_network
) -> None:
    stale = _queue_auth_row(db, dedupe="stale")
    fresh = _queue_auth_row(db, dedupe="fresh")
    db.execute(
        update(notifications)
        .where(notifications.c.id == stale)
        .values(created_at=utcnow() - timedelta(hours=VERIFICATION_TTL_HOURS + 1))
    )
    db.commit()

    cancelled = cancel_expired_verification_notifications(db, company_id=1)
    db.commit()

    assert cancelled == 1
    assert _row(db, stale)["status"] == "CANCELLED"
    assert _row(db, stale)["dispatch_armed"] in (0, False)
    assert _row(db, fresh)["status"] == "PENDING"
    assert [
        nid
        for _, nid in claimable_notification_ids(db, limit=10, company_id=1)
    ] == [fresh]


# ===========================================================================
# T18 — kiracı izolasyonu: seçici ve iptal başka şirkete uzanmaz
# ===========================================================================
def test_t18_selectors_are_tenant_scoped(db: Session) -> None:
    first = _queue_auth_row(db, company_id=1, dedupe="c1")
    second = _queue_auth_row(db, company_id=2, dedupe="c2")
    db.execute(
        update(notifications).values(
            created_at=utcnow() - timedelta(hours=VERIFICATION_TTL_HOURS + 1)
        )
    )
    db.commit()

    assert claimable_notification_ids(db, limit=10, company_id=1) == [(1, first)]
    assert claimable_notification_ids(db, limit=10, company_id=2) == [(2, second)]

    assert cancel_expired_verification_notifications(db, company_id=1) == 1
    db.commit()
    assert _row(db, first)["status"] == "CANCELLED"
    assert _row(db, second)["status"] == "PENDING"


# ===========================================================================
# Seçici yüklemi claim ile birebir aynı olmalı
# ===========================================================================
def test_claimable_predicate_matches_claim_predicate(db: Session) -> None:
    armed = _queue_auth_row(db, dedupe="armed")
    unapproved = enqueue_notification(
        db,
        company_id=1,
        type_="EMAIL_VERIFICATION",
        channel="EMAIL",
        recipient="bekleyen@ornek.test",
        template="email_verification",
        payload_dict={"subject": "K", "body": "G"},
        dedupe_key="awaiting",
    )
    future = _queue_auth_row(db, dedupe="future")
    disarmed = _queue_auth_row(db, dedupe="disarmed")
    db.execute(
        update(notifications)
        .where(notifications.c.id == future)
        .values(next_attempt_at=utcnow() + timedelta(hours=1))
    )
    db.execute(
        update(notifications)
        .where(notifications.c.id == disarmed)
        .values(dispatch_armed=False)
    )
    db.commit()

    selected = [
        nid
        for _, nid in claimable_notification_ids(db, limit=10, company_id=1)
    ]
    assert selected == [armed]
    assert unapproved not in selected


def test_stuck_processing_rows_are_counted(db: Session) -> None:
    notification_id = _queue_auth_row(db)
    assert stuck_processing_count(db, company_id=1) == 0
    db.execute(
        update(notifications)
        .where(notifications.c.id == notification_id)
        .values(status="PROCESSING", locked_until=utcnow() - timedelta(minutes=10))
    )
    db.commit()
    assert stuck_processing_count(db, company_id=1) == 1


def test_stuck_processing_count_is_tenant_scoped(db: Session) -> None:
    """AK-6 sayısı da bir kiracı bilgisidir: ``--company A`` B'yi saymaz."""
    mine = _queue_auth_row(db, company_id=1, dedupe="stuck-mine")
    theirs = _queue_auth_row(db, company_id=2, dedupe="stuck-theirs")
    stale = utcnow() - timedelta(minutes=10)
    db.execute(
        update(notifications)
        .where(notifications.c.id.in_([mine, theirs]))
        .values(status="PROCESSING", locked_until=stale)
    )
    db.commit()

    assert stuck_processing_count(db, company_id=1) == 1
    assert stuck_processing_count(db, company_id=2) == 1
    assert stuck_processing_count(db, company_id=3) == 0


# ===========================================================================
# T17 — config fail-closed (AK-4)
# ===========================================================================
_PRODUCTION_BASE = {
    "environment": "production",
    "cookie_secure": True,
    "bootstrap_admin_password": "GuvenliBootstrap!2026",
    "trusted_proxy_cidrs": "172.18.0.0/16",
    "turnstile_secret_key": "test-secret",
    "smtp_host": "smtp.ornek.test",
    "smtp_from_email": "bildirim@ornek.test",
    "public_app_url": "https://erp.ornek.test",
    "notification_provider": "smtp",
}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"smtp_host": None}, "SMTP_HOST ve SMTP_FROM_EMAIL zorunludur"),
        ({"smtp_from_email": ""}, "SMTP_HOST ve SMTP_FROM_EMAIL zorunludur"),
        (
            {"public_app_url": "http://erp.ornek.test"},
            "PUBLIC_APP_URL https:// ile başlamalıdır",
        ),
        (
            {"public_app_url": "https://localhost:5173"},
            "PUBLIC_APP_URL localhost olamaz",
        ),
        (
            {"notification_provider": "noop"},
            "NOTIFICATION_PROVIDER=smtp zorunludur",
        ),
    ],
)
def test_t17_production_requires_delivery_configuration(
    overrides: dict, expected: str
) -> None:
    with pytest.raises(Exception, match=expected):
        Settings(**{**_PRODUCTION_BASE, **overrides})


def test_t17b_credentials_require_tls_in_every_environment() -> None:
    with pytest.raises(
        Exception, match="SMTP_USERNAME tanımlıyken SMTP_USE_TLS=false kullanılamaz"
    ):
        Settings(environment="test", smtp_username="kullanici", smtp_use_tls=False)


def test_t17c_valid_production_configuration_is_accepted() -> None:
    settings = Settings(**_PRODUCTION_BASE)
    assert settings.is_production
    assert settings.smtp_use_tls is True


def test_t17d_smtp_password_is_secret_in_repr_and_dump() -> None:
    password = "smtp-repr-gizli-parola"
    settings = Settings(environment="test", smtp_password=password)

    assert settings.smtp_password is not None
    assert settings.smtp_password.get_secret_value() == password
    assert password not in repr(settings)
    assert password not in str(settings.model_dump())
    assert password not in settings.model_dump_json()


@pytest.mark.parametrize("port", [0, 65536])
def test_t17e_smtp_port_must_be_in_valid_range(port: int) -> None:
    with pytest.raises(Exception, match="SMTP_PORT 1 ile 65535 arasında olmalıdır"):
        Settings(environment="test", smtp_port=port)


def test_smtp_provider_is_selectable_by_name() -> None:
    provider = get_notification_provider(_SmtpConfig())
    assert isinstance(provider, SmtpEmailNotificationProvider)
    assert provider.supports_idempotency is False
