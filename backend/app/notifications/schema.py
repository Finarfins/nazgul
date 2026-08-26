"""SQLAlchemy Core definitions for the notification outbox and its F1 tables.

Tasarım kaynağı: ``TASARIM_SERVIS_HATIRLATMA_BILDIRIM_F0.md`` rev4.
ORM yoktur; her tablo Core ``Table`` olarak tanımlanır ve sorgular ``text()``
ya da Core ifadeleriyle, ``company_id`` literal filtresiyle yazılır.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
    text,
)


metadata = MetaData()


# ---------------------------------------------------------------------------
# §2.3 durum kataloğu — kapalı küme
# ---------------------------------------------------------------------------
AWAITING_APPROVAL = "AWAITING_APPROVAL"
PENDING = "PENDING"
PROCESSING = "PROCESSING"
RETRY_SCHEDULED = "RETRY_SCHEDULED"
SENT = "SENT"
DELIVERED = "DELIVERED"
SIMULATED = "SIMULATED"
FAILED = "FAILED"
NONE_STATUS = "NONE"
CANCELLED = "CANCELLED"
REJECTED = "REJECTED"
COMPLIANCE_CHECK_UNAVAILABLE = "COMPLIANCE_CHECK_UNAVAILABLE"

ALL_STATUSES: frozenset[str] = frozenset(
    {
        AWAITING_APPROVAL,
        PENDING,
        PROCESSING,
        RETRY_SCHEDULED,
        SENT,
        DELIVERED,
        SIMULATED,
        FAILED,
        NONE_STATUS,
        CANCELLED,
        REJECTED,
        COMPLIANCE_CHECK_UNAVAILABLE,
    }
)

# §2.3 terminal kümesi. ``SIMULATED`` de terminaldir: simülasyon bir gönderim
# denemesinin sonucudur ve yeniden denenmez.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {SENT, DELIVERED, SIMULATED, CANCELLED, REJECTED}
)

# §2.4 lease yükleminin durum kümesi.
DISPATCHABLE_STATUSES: frozenset[str] = frozenset({PENDING, RETRY_SCHEDULED})

MESSAGE_CLASSES: frozenset[str] = frozenset({"SERVICE_TRANSACTIONAL", "COMMERCIAL"})
CONSENT_REQUIRED_CHANNELS: frozenset[str] = frozenset({"SMS", "WHATSAPP", "EMAIL"})

# F0-email sistem şeridi. Bu tipler kullanıcının kendi hesabına, kendi eylemi
# üzerine giden kimlik doğrulama mesajlarıdır: alıcı bir müşteri/tedarikçi
# değil, hesabın sahibidir. ``notification_consents`` DB seviyesinde yalnız
# CUSTOMER/SUPPLIER kabul ettiği için (0033 ``ck_notification_consents_
# party_type``) bu satırların bir rıza kimliği yoktur; rıza kapısından muaf
# tutulurlar. Küme KAPALIDIR: yeni bir tip eklemek açık bir kod değişikliğidir.
AUTH_SYSTEM_TYPES: frozenset[str] = frozenset(
    {"EMAIL_VERIFICATION", "REGISTRATION_ATTEMPT", "PASSWORD_RESET"}
)

# Sistem şeridinin onay kimliği. Dört-göz onayı COMMERCIAL içerik kararı
# içindir; doğrulama e-postasında onaylanacak bir içerik kararı yoktur (gövde
# kodda, alıcı kullanıcının kendi adresi, tetikleyici kullanıcının kendi
# eylemi). ``approved_by`` FK taşımadığı için sentinel şema değişikliği
# gerektirmez ve §2.4 lease yükleminin beş koşulu değişmeden sağlanır.
SYSTEM_APPROVER_ID = 0
SYSTEM_APPROVAL_MODE = "SYSTEM"

# Doğrulama bağlantısının ömrü. Kuyrukta bu süreyi aşan satır gönderilmez:
# süresi dolmuş bir linki postalamak kullanıcıyı hataya koşan bir mail üretir.
VERIFICATION_TTL_HOURS = 24


notifications = Table(
    "notifications",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("type", String(60), nullable=False),
    Column("channel", String(30), nullable=False),
    Column("recipient", String(255), nullable=False),
    Column("template", String(120), nullable=False),
    # TEXT keeps SQLite/PostgreSQL parity and makes the JSON wire shape explicit.
    Column("payload", Text, nullable=False),
    Column("dedupe_key", String(255)),
    Column("status", String(30), nullable=False, server_default="PENDING"),
    Column("external_id", String(255)),
    Column("last_error", Text),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("last_attempt_at", DateTime(timezone=True)),
    Column("locked_until", DateTime(timezone=True)),
    Column("lock_token", String(64)),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    # --- F1 genişletmesi (§2.2) ---
    Column("next_attempt_at", DateTime(timezone=True)),
    Column("scheduled_for", DateTime(timezone=True)),
    Column("max_attempts", Integer, nullable=False, server_default="5"),
    Column("last_error_code", String(20)),
    Column("created_by", Integer),
    Column("approved_by", Integer),
    Column("approved_at", DateTime(timezone=True)),
    Column("approval_mode", String(20)),
    Column("dispatch_armed", Boolean, nullable=False, server_default=text("0")),
    Column("disarmed_at", DateTime(timezone=True)),
    Column("rule_id", Integer),
    Column("message_class", String(24)),
    Column("template_id", Integer),
    Column("template_version", Integer),
    Column("content_hash", String(64)),
    Column("consent_id", Integer),
    Column("consent_version", Integer),
    Column("consent_checked_at", DateTime(timezone=True)),
    Column("consent_decision", String(20)),
    Column("consent_decision_reason", String(60)),
    # İYS alanları F3'ün konusudur; F1'de hiçbir kod yolu bunlara yazmaz.
    Column("iys_status", String(20)),
    Column("iys_checked_at", DateTime(timezone=True)),
    Column("iys_reference", String(120)),
    Column("iys_source", String(20)),
    Column("compliance_attempt_count", Integer, nullable=False, server_default="0"),
    UniqueConstraint(
        "company_id",
        "dedupe_key",
        name="uq_notifications_company_dedupe",
    ),
)

Index(
    "ix_notifications_company_status",
    notifications.c.company_id,
    notifications.c.status,
)
Index(
    "ix_notifications_company_locked_until",
    notifications.c.company_id,
    notifications.c.locked_until,
)
Index(
    "ix_notifications_dispatch",
    notifications.c.company_id,
    notifications.c.status,
    notifications.c.dispatch_armed,
    notifications.c.next_attempt_at,
)
Index(
    "ix_notifications_company_scheduled",
    notifications.c.company_id,
    notifications.c.scheduled_for,
)
Index(
    "ix_notifications_company_rule",
    notifications.c.company_id,
    notifications.c.rule_id,
    notifications.c.status,
)


notification_templates = Table(
    "notification_templates",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("code", String(60), nullable=False),
    Column("channel", String(30), nullable=False),
    Column("name", String(160), nullable=False),
    Column("body", Text, nullable=False),
    # §4.0: zorunlu, varsayılansız.
    Column("message_class", String(24), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default=text("0")),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_by", Integer),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("updated_by", Integer),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("approved_by", Integer),
    Column("approved_at", DateTime(timezone=True)),
    UniqueConstraint(
        "company_id", "code", "channel", name="uq_notification_templates_code"
    ),
)


notification_consents = Table(
    "notification_consents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("party_type", String(20), nullable=False),
    Column("party_id", Integer, nullable=False),
    Column("channel", String(30), nullable=False),
    Column("status", String(20), nullable=False),
    Column("granted_at", DateTime(timezone=True)),
    Column("revoked_at", DateTime(timezone=True)),
    Column("source", String(30), nullable=False),
    Column("source_ref", Text),
    Column("recipient_snapshot", String(255)),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_by", Integer),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint(
        "company_id",
        "party_type",
        "party_id",
        "channel",
        name="uq_notification_consents_party_channel",
    ),
)


notification_consent_events = Table(
    "notification_consent_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("consent_id", Integer, nullable=False),
    Column("action", String(20), nullable=False),
    Column("old_status", String(20)),
    Column("new_status", String(20), nullable=False),
    Column("user_id", Integer),
    Column("reason", Text),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
)


notification_rules = Table(
    "notification_rules",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("code", String(60), nullable=False),
    Column("trigger_type", String(40), nullable=False),
    Column("channel", String(30), nullable=False),
    Column("template_id", Integer, nullable=False),
    Column("offset_days", Integer, nullable=False, server_default="0"),
    Column("is_enabled", Boolean, nullable=False, server_default=text("0")),
    Column("send_window_start", Time),
    Column("send_window_end", Time),
    Column("max_per_run", Integer, nullable=False, server_default="50"),
    Column("scope_filter", Text, nullable=False),
    Column("created_by", Integer),
    Column("approved_by", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("company_id", "code", name="uq_notification_rules_company_code"),
)

Index(
    "ix_notification_rules_company_enabled",
    notification_rules.c.company_id,
    notification_rules.c.is_enabled,
)
