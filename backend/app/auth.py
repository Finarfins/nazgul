from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    delete,
    insert,
    inspect,
    select,
    text,
    true,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .config import settings

metadata = MetaData()
users = Table(
    "app_users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(80), unique=True, nullable=False, index=True),
    Column(
        "email",
        String(320),
        unique=True,
        nullable=True,
        default=lambda: f"legacy-{secrets.token_hex(12)}@legacy.invalid",
    ),
    Column(
        "email_verified",
        Boolean,
        nullable=True,
        default=True,
        server_default=true(),
    ),
    Column("phone", String(10), nullable=True),
    Column("terms_accepted_at", DateTime(timezone=True), nullable=True),
    Column("display_name", String(160), nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("role", String(40), nullable=False, default="admin"),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_login_at", DateTime(timezone=True), nullable=True),
    Column("must_change_password", Boolean, nullable=False, default=False),
)
auth_tokens = Table(
    "auth_tokens",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("token_hash", String(64), unique=True, nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)
auth_refresh_tokens = Table(
    "auth_refresh_tokens",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("family_id", String(64), nullable=False, index=True),
    Column("token_hash", String(64), unique=True, nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("last_used_at", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("replaced_by_hash", String(64), nullable=True),
    Column("created_ip", String(80), nullable=True),
    Column("user_agent", String(500), nullable=True),
)
audit_logs = Table(
    "security_audit_logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=True, index=True),
    Column("username", String(80), nullable=True),
    Column("action", String(20), nullable=False),
    Column("path", String(500), nullable=False),
    Column("status_code", Integer, nullable=False),
    Column("ip_address", String(80), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("company_id", Integer, nullable=True, index=True),
    Column("request_id", String(64), nullable=True, index=True),
    Column("outcome", String(20), nullable=False, default="success"),
    Column("duration_ms", Integer, nullable=True),
    Column("auth_source", String(20), nullable=True),
    Column("user_agent", String(500), nullable=True),
    Column("failure_reason", String(300), nullable=True),
    # FİRMASIZ SATIR YALNIZ GERÇEK KİMLİK-ÖNCESİ OLAYA KALIR.
    #
    # ``company_id`` bilerek NULL kabul eder: giriş, kayıt, parola sıfırlama ve
    # AUTH_REQUIRED 401'leri hiçbir firmaya ait DEĞİLDİR ve kimlik-öncesi
    # POST'ların denetlenmesi kasıtlıdır (bkz. test_v2_9_public_auth_audit_
    # contract). Bu olaylar tabloda KALIR; ayrı bir tabloya taşınsalardı bir
    # saldırının sınırı geçtiği an tek yerde okunamaz olurdu.
    #
    # Kısıtlanan şey NULL'un ANLAMI: kimliği ÇÖZÜLMÜŞ bir isteğin satırı
    # firmasız olamaz. Kuralın "hiçbir firma İSTENMEMİŞ olmalı" yarısı yazıcıda
    # durur (main.py::_write_security_audit istenen firmayı reddedilen istekte
    # de yazar); şema yalnız SAKLANAN sütunlar arasındaki ilişkiyi zorlayabilir.
    #
    # Göç: 20260812_0059. PostgreSQL'de NOT VALID eklenir — yeni yazımlar tam
    # zorlanır, geçmiş denetim satırları SİLİNMEZ.
    CheckConstraint(
        "company_id IS NOT NULL OR (user_id IS NULL AND username IS NULL)",
        name="ck_security_audit_logs_untenanted_only_preauth",
    ),
)
login_attempts = Table(
    "login_attempts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(80), nullable=False, index=True),
    Column("ip_address", String(80), nullable=False, index=True),
    Column("fail_count", Integer, nullable=False, default=0),
    Column("locked_until", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
email_verification_tokens = Table(
    "email_verification_tokens",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "user_id",
        Integer,
        ForeignKey("app_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("token_hash", String(64), unique=True, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True), nullable=True),
)

# Şifre sıfırlama tokenları doğrulama tokenlarından AYRI durur. Aynı tabloyu
# paylaşsalardı bir e-posta doğrulama linki şifre sıfırlamaya da yarardı;
# doğrulama linki kayıt sırasında üretilir ve sızması hesabın ele geçirilmesi
# anlamına gelmemelidir.
password_reset_tokens = Table(
    "password_reset_tokens",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "user_id",
        Integer,
        ForeignKey("app_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("token_hash", String(64), unique=True, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True), nullable=True),
)
auth_rate_limits = Table(
    "auth_rate_limits",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("action", String(40), nullable=False),
    Column("ip_address", String(80), nullable=False),
    Column("attempted_at", DateTime(timezone=True), nullable=False),
)
Index(
    "ix_auth_rate_limits_action_ip_time",
    auth_rate_limits.c.action,
    auth_rate_limits.c.ip_address,
    auth_rate_limits.c.attempted_at,
)

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},
    # ``machines`` is a dedicated V3 permission for managing machine cards. It is
    # granted to admin (via "*") and yonetici; the future ``service`` role must
    # also receive it when introduced. Other roles keep read-only machine access.
    # ``payments`` covers customer/supplier collections and payments, while
    # ``finance`` covers treasury operations such as cash/bank accounts,
    # transfers and cheque/note management. Sales users need collections but
    # must not gain access to treasury data or operations.
    # Bildirim yetkileri kasıtlı olarak üçe bölünmüştür (tasarım §6):
    # ``notifications`` yalnız görüntüler, ``notifications_approve`` gönderimi
    # onaylar, ``notifications_dispatch`` onaylanmışı tetikler,
    # ``notifications_admin`` şablon/sınıf/izin yönetir. Şablonu değiştirebilen
    # kişinin tek başına toplu gönderim yapamaması bu ayrımdan gelir; onayın
    # kendisi ayrıca dört-göz kuralına tabidir (oluşturan onaylayamaz).
    # Tarla Yönetimi V1 izinleri ÜÇE bölündü (mobil-erp#2 yetki modeli).
    # `supplier_prices.*` ile aynı desen: tek bir "farm" izni okuma ile yazmayı
    # ayıramazdı ve issue "satış/rapor yalnız okur, depo girdi bağlar" diyor.
    #   farm.view    — okuma
    #   farm.manage  — çiftlik/parsel/sezon/faaliyet/hasat/görev yazma
    #   farm.inputs  — faaliyete girdi bağlama (depo; V1'de STOK HAREKETİ YOK)
    "yonetici": {
        "read", "sales", "field_service", "purchases", "payments", "finance", "stock", "reports",
        "users", "machines", "notifications", "notifications_approve",
        "notifications_dispatch", "notifications_admin", "supplier_prices.view",
        "supplier_prices.import", "supplier_prices.apply",
        "supplier_prices.override_block",
        "farm.view", "farm.manage", "farm.inputs",
        # Hayvancılık V1 (mobil-erp#17) — aynı desen:
        #   herd.view   — okuma
        #   herd.manage — hayvan/sürü/doğum/tohumlama/hareket yazma
        #   herd.health — aşı ve sağlık kaydı (veteriner; sürü yapısını
        #                 değiştiremez)
        "herd.view", "herd.manage", "herd.health",
    },
    "muhasebe": {
        "read", "sales", "purchases", "payments", "finance", "reports",
        "notifications", "notifications_approve", "notifications_dispatch",
        "supplier_prices.view", "supplier_prices.import", "supplier_prices.apply",
        "farm.view", "herd.view",
    },
    "satis": {"read", "sales", "field_service", "payments", "notifications", "farm.view", "herd.view"},
    # Depo hayvan sağlık kaydı GİRMEZ, yalnız görür: aşı kaydı veterinerlik
    # sorumluluğu ve `herd.health` ile ayrıldı.
    "depo": {"read", "stock", "purchases", "supplier_prices.view", "farm.view", "farm.inputs", "herd.view"},
    "rapor": {"read", "reports", "farm.view", "herd.view"},
}

# Higher numbers represent stronger administrative authority. This is kept
# separate from feature permissions because being able to use the Users screen
# must not imply being able to create or disable peers/superiors.
ROLE_RANK: dict[str, int] = {
    "admin": 100,
    "yonetici": 80,
    "muhasebe": 60,
    "satis": 40,
    "depo": 40,
    "rapor": 20,
}

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def can_assign_role(actor_role: str, target_role: str) -> bool:
    if actor_role == "admin":
        return target_role in ROLE_RANK
    return ROLE_RANK.get(target_role, 0) < ROLE_RANK.get(actor_role, 0)


def can_manage_role(actor_role: str, target_role: str) -> bool:
    if actor_role == "admin":
        return target_role in ROLE_RANK
    return ROLE_RANK.get(target_role, 0) < ROLE_RANK.get(actor_role, 0)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 210_000)
    return f"pbkdf2_sha256$210000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, expected_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected_hex)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def initialize_auth(engine: Engine) -> None:
    metadata.create_all(engine)
    with engine.begin() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("app_users")}
        if "must_change_password" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE app_users ADD COLUMN must_change_password "
                    "BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        audit_columns = {
            column["name"] for column in inspect(conn).get_columns("security_audit_logs")
        }
        if "company_id" not in audit_columns:
            conn.execute(
                text("ALTER TABLE security_audit_logs ADD COLUMN company_id INTEGER")
            )
        existing = conn.execute(select(users.c.id).limit(1)).first()
        if not existing:
            now = utcnow()
            conn.execute(
                insert(users).values(
                    username="admin",
                    email="legacy-bootstrap-admin@legacy.invalid",
                    email_verified=True,
                    display_name="Sistem Yöneticisi",
                    password_hash=hash_password(
                        settings.effective_bootstrap_admin_password
                    ),
                    role="admin",
                    is_active=True,
                    must_change_password=True,
                    created_at=now,
                )
            )
        else:
            # Upgrade installations that still use the well-known bootstrap password.
            admin = conn.execute(
                select(
                    users.c.id, users.c.password_hash, users.c.must_change_password
                ).where(users.c.username == "admin")
            ).mappings().first()
            if (
                admin
                and verify_password("admin123", admin["password_hash"])
                and not admin["must_change_password"]
            ):
                conn.execute(
                    update(users)
                    .where(users.c.id == admin["id"])
                    .values(must_change_password=True)
                )


MAX_LOGIN_FAILURES = 5
LOGIN_LOCK_MINUTES = 15


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def login_lock_status(db: Session, username: str, ip_address: str) -> datetime | None:
    row = db.execute(
        select(login_attempts).where(
            login_attempts.c.username == username,
            login_attempts.c.ip_address == ip_address,
        )
    ).mappings().first()
    locked_until = _aware(row["locked_until"]) if row else None
    if locked_until and locked_until > utcnow():
        return locked_until
    if row and locked_until:
        db.execute(
            update(login_attempts)
            .where(login_attempts.c.id == row["id"])
            .values(fail_count=0, locked_until=None, updated_at=utcnow())
        )
        db.commit()
    return None


def record_login_failure(db: Session, username: str, ip_address: str) -> datetime | None:
    row = db.execute(
        select(login_attempts).where(
            login_attempts.c.username == username,
            login_attempts.c.ip_address == ip_address,
        )
    ).mappings().first()
    now = utcnow()
    fail_count = int(row["fail_count"] or 0) + 1 if row else 1
    locked_until = (
        now + timedelta(minutes=LOGIN_LOCK_MINUTES)
        if fail_count >= MAX_LOGIN_FAILURES
        else None
    )
    if row:
        db.execute(
            update(login_attempts)
            .where(login_attempts.c.id == row["id"])
            .values(
                fail_count=fail_count,
                locked_until=locked_until,
                updated_at=now,
            )
        )
    else:
        db.execute(
            insert(login_attempts).values(
                username=username,
                ip_address=ip_address,
                fail_count=fail_count,
                locked_until=locked_until,
                updated_at=now,
            )
        )
    db.commit()
    return locked_until


def clear_login_failures(db: Session, username: str, ip_address: str) -> None:
    db.execute(
        delete(login_attempts).where(
            login_attempts.c.username == username,
            login_attempts.c.ip_address == ip_address,
        )
    )
    db.commit()


def authenticate(db: Session, username: str, password: str) -> dict[str, Any] | None:
    identity = username.strip().lower()
    row = db.execute(
        select(users).where(
            (users.c.username == username) | (users.c.email == identity)
        )
    ).mappings().first()
    if not row or not row["is_active"] or not verify_password(password, row["password_hash"]):
        return None
    if not row["email_verified"]:
        return dict(row)
    db.execute(
        update(users).where(users.c.id == row["id"]).values(last_login_at=utcnow())
    )
    db.commit()
    return dict(row)


def issue_token(
    db: Session, user_id: int, lifetime_minutes: int | None = None
) -> tuple[str, datetime]:
    raw = secrets.token_urlsafe(48)
    now = utcnow()
    minutes = lifetime_minutes or settings.access_token_minutes
    expires = now + timedelta(minutes=minutes)
    db.execute(
        insert(auth_tokens).values(
            user_id=user_id,
            token_hash=token_digest(raw),
            created_at=now,
            expires_at=expires,
        )
    )
    # Opportunistic cleanup keeps this short-lived table bounded without a
    # separate scheduler. Active rows are never touched.
    db.execute(delete(auth_tokens).where(auth_tokens.c.expires_at <= now))
    db.commit()
    return raw, expires


def revoke_token(db: Session, token: str) -> None:
    db.execute(delete(auth_tokens).where(auth_tokens.c.token_hash == token_digest(token)))
    db.commit()


def revoke_user_access_tokens(db: Session, user_id: int) -> None:
    db.execute(delete(auth_tokens).where(auth_tokens.c.user_id == user_id))


def get_user_by_token(db: Session, token: str) -> dict[str, Any] | None:
    row = db.execute(
        select(users, auth_tokens.c.expires_at)
        .join(auth_tokens, auth_tokens.c.user_id == users.c.id)
        .where(auth_tokens.c.token_hash == token_digest(token))
    ).mappings().first()
    if not row or not row["is_active"]:
        return None
    now = utcnow()
    expires = _aware(row["expires_at"])
    if not expires or expires <= now:
        # SEC-AUTH-01: the presented token is already expired. Opportunistically
        # sweep every expired row so this short-lived table stays bounded without
        # a scheduler, mirroring issue_token(). Cleanup only runs on this cold
        # path — valid-token requests stay read-only and never write here.
        db.execute(delete(auth_tokens).where(auth_tokens.c.expires_at <= now))
        db.commit()
        return None
    return {
        key: row[key]
        for key in (
            "id",
            "username",
            "display_name",
            "role",
            "is_active",
            "must_change_password",
        )
    }


def issue_refresh_token(
    db: Session,
    user_id: int,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
    family_id: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[str, datetime, str]:
    raw = secrets.token_urlsafe(64)
    now = utcnow()
    family = family_id or secrets.token_hex(32)
    expires = expires_at or (now + timedelta(days=settings.refresh_token_days))
    db.execute(
        insert(auth_refresh_tokens).values(
            user_id=user_id,
            family_id=family,
            token_hash=token_digest(raw),
            created_at=now,
            expires_at=expires,
            created_ip=ip_address,
            user_agent=(user_agent or "")[:500] or None,
        )
    )
    db.execute(
        delete(auth_refresh_tokens).where(auth_refresh_tokens.c.expires_at <= now)
    )
    db.commit()
    return raw, expires, family


def revoke_refresh_family(db: Session, family_id: str) -> None:
    db.execute(
        update(auth_refresh_tokens)
        .where(
            auth_refresh_tokens.c.family_id == family_id,
            auth_refresh_tokens.c.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow())
    )
    db.commit()


def revoke_refresh_token(db: Session, token: str, *, whole_family: bool = True) -> None:
    digest = token_digest(token)
    row = db.execute(
        select(auth_refresh_tokens.c.id, auth_refresh_tokens.c.family_id).where(
            auth_refresh_tokens.c.token_hash == digest
        )
    ).mappings().first()
    if not row:
        return
    if whole_family:
        revoke_refresh_family(db, str(row["family_id"]))
        return
    db.execute(
        update(auth_refresh_tokens)
        .where(auth_refresh_tokens.c.id == row["id"])
        .values(revoked_at=utcnow())
    )
    db.commit()


def revoke_user_refresh_tokens(db: Session, user_id: int) -> None:
    db.execute(
        update(auth_refresh_tokens)
        .where(
            auth_refresh_tokens.c.user_id == user_id,
            auth_refresh_tokens.c.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow())
    )


class RefreshPasswordChangeRequired(Exception):
    """Raised when a forced-password-change session attempts refresh."""


def rotate_refresh_token(
    db: Session,
    token: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, datetime, dict[str, Any]] | None:
    """Rotate a refresh token and revoke its family on reuse.

    A consumed token has ``replaced_by_hash`` set. Presenting it again is a
    strong replay signal, so every still-active token in the same family is
    revoked before returning failure.
    """

    digest = token_digest(token)
    query = select(auth_refresh_tokens).where(
        auth_refresh_tokens.c.token_hash == digest
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    row = db.execute(query).mappings().first()
    if not row:
        return None

    family_id = str(row["family_id"])
    now = utcnow()
    expires = _aware(row["expires_at"])
    if row["replaced_by_hash"] is not None or row["revoked_at"] is not None:
        db.execute(
            update(auth_refresh_tokens)
            .where(
                auth_refresh_tokens.c.family_id == family_id,
                auth_refresh_tokens.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        db.commit()
        return None
    if not expires or expires <= now:
        db.execute(
            update(auth_refresh_tokens)
            .where(auth_refresh_tokens.c.family_id == family_id)
            .values(revoked_at=now)
        )
        db.commit()
        return None

    user = db.execute(
        select(users).where(
            users.c.id == int(row["user_id"]), users.c.is_active.is_(True)
        )
    ).mappings().first()
    if not user:
        db.execute(
            update(auth_refresh_tokens)
            .where(auth_refresh_tokens.c.family_id == family_id)
            .values(revoked_at=now)
        )
        db.commit()
        return None
    if bool(user.get("must_change_password")):
        db.execute(
            update(auth_refresh_tokens)
            .where(
                auth_refresh_tokens.c.family_id == family_id,
                auth_refresh_tokens.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        db.commit()
        raise RefreshPasswordChangeRequired

    new_raw = secrets.token_urlsafe(64)
    new_digest = token_digest(new_raw)
    db.execute(
        update(auth_refresh_tokens)
        .where(auth_refresh_tokens.c.id == row["id"])
        .values(
            last_used_at=now,
            revoked_at=now,
            replaced_by_hash=new_digest,
        )
    )
    db.execute(
        insert(auth_refresh_tokens).values(
            user_id=int(row["user_id"]),
            family_id=family_id,
            token_hash=new_digest,
            created_at=now,
            expires_at=expires,
            created_ip=ip_address,
            user_agent=(user_agent or "")[:500] or None,
        )
    )
    db.commit()
    public_user = {
        key: user[key]
        for key in (
            "id",
            "username",
            "display_name",
            "role",
            "is_active",
            "must_change_password",
        )
    }
    return new_raw, expires, public_user


def issue_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def extract_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def extract_access_token(request: Request) -> tuple[str | None, str | None]:
    bearer = extract_bearer(request)
    if bearer:
        return bearer, "bearer"
    cookie_token = request.cookies.get(settings.access_cookie_name)
    if cookie_token:
        return cookie_token, "cookie"
    return None, None


def csrf_is_valid(request: Request) -> bool:
    cookie_token = request.cookies.get(settings.csrf_cookie_name, "")
    header_token = request.headers.get("x-csrf-token", "")
    return bool(cookie_token and header_token) and hmac.compare_digest(
        cookie_token, header_token
    )


# Tarla Yönetimi V1 uçları (mobil-erp#2). Liste modül seviyesinde: hem izin
# çözümleyici hem testler aynı kaynaktan okusun.
_FARM_PATH_PREFIXES = (
    "/api/farms",
    "/api/farm-parcels",
    # BKÜ kataloğu (göç 20260901_0063). PHI gün sayısının kaydı tarla
    # verisidir; okuması `farm.view`, yazması `farm.manage`. `/api/field` ile
    # BAŞLAMIYOR, yani aşağıdaki saha servis kuralına düşmezdi — ama listede
    # olmasaydı genel `read` iznine düşerdi ve bekleme sürelerini okuma yetkisi
    # olan herkes değiştirebilirdi.
    "/api/plant-protection-products",
    "/api/crop-seasons",
    "/api/field-activities",
    "/api/field-harvest-decision",
    "/api/field-harvests",
    # Kantar fişi (göç 20260904_0069). "/api/field-harvest-tickets" öneki
    # "/api/field-harvests" ile EŞLEŞMEZ ("...harvests" ile "...harvest-t"
    # daha 's'/'t' harfinde ayrılıyor), yani listede olmasaydı yukarıdaki
    # DİKKAT notunun anlattığı yola düşer ve SESSİZCE `field_service` iznine
    # inerdi: fişi saha kullanıcısı yazabilirdi. Okuması `farm.view`,
    # yazması `farm.manage`.
    "/api/field-harvest-tickets",
    "/api/field-tasks",
    "/api/field-dashboard",
    # DİKKAT: buraya eklenmeyen her `/api/field-...` ucu SESSİZCE
    # `field_service` iznine düşer (aşağıdaki `/api/field` kuralı). Bu iki kez
    # yaşandı; `tests/test_farm_management_api.py` artık öneki `/api/field-`
    # olan HER tarla ucunun burada olmasını zorluyor.
    "/api/field-safety",
    # Outbox okuma yuzeyi (acilis kosulu 2). BU LISTEDE OLMAK ZORUNDA:
    # "/api/field-integration-events" de "/api/field" ile BASLIYOR, yani
    # asagidaki genel kural onu SESSIZCE `field_service` iznine baglardi —
    # saha teknisyeni tarla kuyrugunu okur, satis/rapor rolleri okuyamazdi.
    # Kuyruk TARLA verisidir; GET -> `farm.view`.
    "/api/field-integration-events",
)

#: Hayvancılık uçları (mobil-erp#17). Tarla ile AYNI TUZAK burada da var:
#: bu listeye eklenmeyen bir `/api/animal...` ucu, aşağıdaki genel kurallara
#: düşer ve sessizce yanlış izne bağlanır. Tarla modülünde iki kez yaşandı;
#: türetilmiş test bu modülü de tarıyor.
_HERD_PATH_PREFIXES = (
    "/api/animals",
    "/api/animal-groups",
    "/api/animal-vaccinations",
    "/api/animal-breedings",
    "/api/animal-births",
    "/api/animal-weights",
    "/api/animal-movements",
    "/api/milk-yields",
    "/api/herd-dashboard",
    # Zorunlu aşı takvimi (FAZ 4). Salt okunur bir hesap ama yine de bu listede
    # OLMALI: eklenmezse aşağıdaki genel kurallara düşüp sessizce ``read``
    # iznine bağlanırdı — tarla modülünde tam bu tuzağa düşülmüştü
    # (`/api/field-safety`).
    "/api/vaccination-calendar",
    # Döl verimi göstergeleri (FAZ 5). Aynı gerekçe: salt okunur bir hesap ama
    # listede olmazsa sessizce genel ``read`` iznine düşer.
    "/api/herd-fertility",
)

#: Saatlik maliyet oranları (mobil-erp#24). Bu bir PARA TANIMI: oran, geçmiş
#: maliyetin dayanağı ve değiştirmek kâr rakamını değiştirir. Bu yüzden tarla/
#: hayvancılık okuma izinlerine DEĞİL, ``finance``a bağlı — girdi giren depo
#: rolünün oranları görmesi ya da değiştirmesi gerekmiyor.
_COST_RATE_PREFIX = "/api/cost-rates"



#: Session/identity self-service. AUTHENTICATION IS SUFFICIENT for operations
#: on one's own identity and session; authorization gates business data.
#:
#: These three used to be expressed as ``read``, which worked only because every
#: role in the table happens to hold ``read`` — and because an unresolvable role
#: silently fell back to it. Once that fallback closed, expressing "any
#: authenticated principal" as a feature permission locked such an account out of
#: logging out, changing its password and reading its own session, with no
#: recovery path: the frontend can clear local state but cannot revoke the server
#: session or the HttpOnly cookies. The exemption therefore belongs to the ROUTE'S
#: NATURE, not to the caller's role — granting unknown roles a permission would
#: reopen exactly what the fail-closed rule closed.
#:
#: Membership is EXACT, never a prefix: a prefix rule would exempt anything
#: nested under these paths.
#:
#: Enumeration (method recorded in tests/test_self_service_exemption.py):
#:   1. the identity/session surface is the ``/api/auth/`` prefix; removing
#:      PUBLIC_API from it leaves exactly these three, each with no path
#:      parameter and no identifier of another principal;
#:   2. no route outside that prefix qualifies — the only one that touches the
#:      credential helpers at all is ``POST /api/users``, which creates ANOTHER
#:      principal, is tenant-scoped and requires ``users``.
SELF_SERVICE_API: frozenset[str] = frozenset(
    {
        "/api/auth/change-password",
        "/api/auth/logout",
        "/api/auth/me",
    }
)


def required_permission(method: str, path: str) -> str:
    if path.startswith("/api/platform/backups"):
        # The router applies the stronger admin + environment allow-list check.
        # Middleware still requires authentication and CSRF.
        return "read"
    # Kept for the route inventory only. The middleware exempts these paths from
    # the permission gate, so the value below gates nothing for them.
    if path in SELF_SERVICE_API:
        return "read"
    # Offline saha snapshot'ı kişisel cihazda kalıcı veri oluşturur. Bu yüzden
    # güvenli bir GET olsa da genel ``read`` iznine düşemez.
    # TARLA YÖNETİMİ — bu blok /api/field kontrolünden ÖNCE olmak ZORUNDA.
    # "/api/field-activities", "/api/field-harvests", "/api/field-tasks" ve
    # "/api/field-dashboard" hepsi "/api/field" ile BAŞLIYOR; aşağıdaki kural
    # önce çalışsaydı tarla uçları sessizce SAHA SERVİS iznine düşerdi —
    # yani satış/rapor rolleri tarla verisini göremez, buna karşılık saha
    # teknisyeni parsel yazabilirdi. İkisi de yanlış.
    if path.startswith(_COST_RATE_PREFIX):
        return "finance"
    if any(path.startswith(prefix) for prefix in _HERD_PATH_PREFIXES):
        if method in SAFE_METHODS:
            return "herd.view"
        # AŞI VE SAĞLIK KAYDI AYRI İZİN: veteriner ya da sağlık sorumlusu aşı
        # girebilmeli ama hayvan alım/satımı ve sürü yapısını değiştirememeli.
        if path.startswith("/api/animal-vaccinations"):
            return "herd.health"
        return "herd.manage"
    if any(path.startswith(prefix) for prefix in _FARM_PATH_PREFIXES):
        if method in SAFE_METHODS:
            return "farm.view"
        # Girdi bağlama depo rolüne açık; çiftlik/parsel/sezon yönetimi değil.
        if path.startswith("/api/field-activities") and path.endswith("/inputs"):
            return "farm.inputs"
        return "farm.manage"
    if path.startswith("/api/field"):
        return "field_service"
    # Sensitive read endpoints must be checked before the generic read rule.
    # Otherwise every role with the baseline ``read`` permission could list
    # users/audit records or inspect cash and bank accounts by calling the API
    # directly even though the frontend menu is hidden.
    if path.startswith("/api/users") or path.startswith("/api/audit") or path.startswith("/api/history"):
        return "users"
    # Kullanıcı aktivite paneli "kim ne yaptı"yı gösterir; okuması da yazması da
    # yönetim yüzeyidir. ``users`` yetkisi admin + yönetici demektir, router
    # ayrıca rolü açıkça doğrular ve arşivlemeyi admin'e kısar.
    if path.startswith("/api/activity-logs"):
        return "users"
    if path.startswith("/api/payments"):
        return "payments"
    if path.startswith("/api/receivables"):
        return "payments"
    if path.startswith("/api/finance"):
        return "finance"
    if path.startswith("/api/harvest-scheduling"):
        return "finance"
    if path.startswith("/api/payment-allocations/reconciliation"):
        return "finance"
    if path.startswith("/api/payment-allocations"):
        return "read" if method in SAFE_METHODS else "finance"
    # Seasonal stock planning is operational inventory guidance and is safe for
    # every baseline read role; other analytics remain reports-only.
    if path.startswith("/api/analytics/seasonal-plan"):
        return "read"
    if path.startswith("/api/reports") or path.startswith("/api/analytics"):
        return "reports"
    if (
        path.startswith("/api/companies")
        or path.startswith("/api/company-settings")
        or path.startswith("/api/policy-overrides")
    ) and method not in {"GET", "HEAD", "OPTIONS"}:
        return "users"
    if path.startswith("/api/policy-overrides"):
        return "users"
    # Purchase-engine analysis reads expose supplier cost, applied discount tiers
    # and gross margin. That is commercially sensitive buying data, so it is
    # gated on ``purchases`` explicitly instead of inheriting the baseline
    # ``read`` rule below. FX rates stay on ``read``: a published TCMB rate
    # discloses neither cost nor margin.
    if (
        path.startswith("/api/supplier-prices/imports")
        or path.startswith("/api/supplier-prices/profiles")
        or path.startswith("/api/supplier-prices/xrefs")
    ):
        if method in SAFE_METHODS:
            return "supplier_prices.view"
        if path.endswith("/apply") or path.endswith("/revert"):
            return "supplier_prices.apply"
        if "/override" in path:
            return "supplier_prices.override_block"
        return "supplier_prices.import"
    if (
        path.startswith("/api/purchase-comparison")
        or path.startswith("/api/supplier-prices/history")
        or path.endswith("/supplier-prices")
    ):
        return "purchases"
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "read"
    # Machine card writes require the dedicated ``machines`` permission (reads
    # fall through to the baseline ``read`` rule above).
    if path.startswith("/api/machines"):
        return "machines"
    if path.startswith("/api/workflow"):
        return "sales" if "purchase_return" not in path else "purchases"
    if path.startswith("/api/orders"):
        return "sales"
    if path.startswith("/api/pos"):
        return "sales"
    if path.startswith("/api/customers"):
        return "sales"
    if (
        path.startswith("/api/work-orders/")
        and path.endswith("/receivable/reverse")
    ):
        return "finance"
    if path.startswith("/api/work-orders"):
        return "sales"
    # Work-order attachments (photo/signature) live on their own prefix so the
    # upload body-limit override stays scoped to them; they are still a
    # work-order write, so they carry the same permission.
    if path.startswith("/api/work-order-attachments"):
        return "sales"
    # Technician profiles are service metadata managed by the same role that
    # manages work orders today; reads fall through to the baseline ``read``
    # rule above. (A dedicated ``service`` permission is a separate decision.)
    if path.startswith("/api/technician-profiles"):
        return "sales"
    if path.startswith("/api/invoices"):
        return "sales"
    if path.startswith("/api/purchases") or path.startswith("/api/suppliers"):
        return "purchases"
    # Purchase Comparison Engine: manual supplier prices and FX overrides are a
    # purchasing concern. Cost/margin reads were already gated on ``purchases``
    # above; FX reads stay on ``read``, so only writes reach here.
    if (
        path.startswith("/api/supplier-prices")
        or path.startswith("/api/supplier-price-lists")
        or path.startswith("/api/exchange-rates")
        or path.startswith("/api/purchase-comparison")
        # One-click PO and grouped reorder drafts are purchasing writes. Spelled
        # out even though the prefix rules above would also catch them.
        or path.startswith("/api/purchase-orders")
    ):
        return "purchases"
    if path.startswith("/api/products") or path.startswith("/api/warehouses"):
        return "stock"
    # Part supersession writes are a parts/inventory concern, gated like
    # product writes. Reads (list + /products/{id}/current) stay on ``read``.
    if path.startswith("/api/part-supersessions"):
        return "stock"
    if path.startswith("/api/imports/products"):
        return "stock"
    if path.startswith("/api/imports/suppliers"):
        return "purchases"
    if path.startswith("/api/imports/customers"):
        return "sales"
    if path.startswith("/api/imports/sales"):
        return "sales"
    # Toplu tahsilat, tekil tahsilatla aynı yetkiye bağlıdır. Bu satır olmazsa
    # eşleşme kuralı kalmaz ve uç nokta sessizce ``__admin_only__``a düşer:
    # muhasebeci rolü Excel'den tahsilat yükleyemez.
    if path.startswith("/api/imports/payments"):
        return "payments"
    # Retrying an external notification is a management action. The read-only
    # outbox endpoint is handled by the generic safe-method rule above.
    if path.startswith("/api/notifications/"):
        return "users"
    if path.startswith("/api/branches"):
        return "users"
    # Unknown state-changing endpoints are admin-only. This deny-by-default
    # fallback prevents newly added routers from silently inheriting the
    # baseline ``read`` permission.
    return "__admin_only__"


# A role this table cannot resolve grants NOTHING. The fallback used to be
# ``{"read"}``, which made the gate fail OPEN: every route whose required
# permission is ``read`` — 89 of the 326 authenticated routes when this was
# measured — could not be denied by any value ``app_users.role`` is able to
# hold, including the empty string and any role dropped from the table by a
# later release. An input the authorization table cannot resolve is a
# violation, not a permissive default.
#
# Nothing legitimate relied on the old fallback (measured before the change):
# the only role written by bootstrap, self-service registration and the demo
# seed is ``admin``; ``POST /api/users`` rejects a role outside this table with
# 400; and ``app_users.role`` carries no CHECK constraint, so an unknown value
# is reachable only by writing to the database directly.
#
# The gate and the session payload both resolve a role through this one
# function, so the fallback lives in a single place and the two cannot drift —
# a second copy of the literal is how this defect stayed invisible.
def permissions_for(role: str) -> frozenset[str]:
    """Return the permissions a role holds; an unresolvable role holds none."""
    return frozenset(ROLE_PERMISSIONS.get(role, ()))


def has_permission(role: str, permission: str) -> bool:
    allowed = permissions_for(role)
    return "*" in allowed or permission in allowed
