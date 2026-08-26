"""Şifre sıfırlama bağlantısının token ve kuyruk katmanı.

``email_verification.py`` ile aynı üç işi yapar: token üret, mesajı bildirim
outbox'ına *sistem şeridi* satırı olarak yaz, commit sonrası tek fırsatçı
gönderim dene. Posta göndermez; taşıyıcı ``notifications/provider.py``.

Doğrulama akışından iki farkı vardır:

* Tokenlar ayrı tabloda (``password_reset_tokens``) durur — bkz. o tablonun
  tanımındaki gerekçe.
* Ömür 24 saat değil 1 saattir. Sıfırlama linki hesabı ele geçirmeye yeter;
  posta kutusunda ne kadar az beklerse o kadar iyi.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from sqlalchemy import insert, text, update
from sqlalchemy.orm import Session

from .auth import password_reset_tokens, token_digest, utcnow
from .config import settings
from .notifications.schema import (
    CANCELLED,
    PENDING,
    RETRY_SCHEDULED,
    SYSTEM_APPROVAL_MODE,
    SYSTEM_APPROVER_ID,
)
from .notifications.service import assert_transition, enqueue_notification

logger = logging.getLogger("yerel_hesap.password_reset")

TOKEN_LIFETIME_HOURS = 1

PASSWORD_RESET_TYPE = "PASSWORD_RESET"
EMAIL_CHANNEL = "EMAIL"

PASSWORD_RESET_SUBJECT = "Şifre sıfırlama bağlantınız"
_SUPERSEDED_REASON = "Yeni sıfırlama bağlantısı üretildi; bu satır gönderilmedi"


def _reset_body(link: str) -> str:
    return (
        "Harman Zamanı şifrenizi sıfırlamak için "
        f"{TOKEN_LIFETIME_HOURS} saat içinde bağlantıyı açın:\n{link}\n\n"
        "Bu isteği siz yapmadıysanız hiçbir şey yapmanıza gerek yok; "
        "şifreniz değişmez."
    )


def _reset_dedupe_key(user_id: int, link: str) -> str:
    return f"password-reset:{user_id}:{token_digest(link)[0:20]}"


def _cancel_superseded_resets(db: Session, user_id: int, *, company_id: int) -> int:
    """Aynı kullanıcının gönderilmemiş sıfırlama satırlarını iptal eder.

    Yeni token üretildiği anda eskisi ölür. Eski satır kuyrukta kalırsa
    kullanıcı iki mail alır ve önce geleni tıklarsa "geçersiz bağlantı" görür.
    Bastırma, tokenı geçersizleyen işlemle **aynı transaction** içindedir.
    """
    assert_transition(PENDING, CANCELLED)
    assert_transition(RETRY_SCHEDULED, CANCELLED)
    result = db.execute(
        text(
            """UPDATE notifications
                   SET status = :cancelled,
                       dispatch_armed = :disarmed,
                       next_attempt_at = NULL,
                       last_error = :reason,
                       updated_at = :now
                 WHERE type = :type
                   AND status IN ('PENDING', 'RETRY_SCHEDULED')
                   AND dedupe_key LIKE :prefix
                   AND company_id = :cid"""
        ),
        {
            "cancelled": CANCELLED,
            "disarmed": False,
            "now": utcnow(),
            "reason": _SUPERSEDED_REASON,
            "type": PASSWORD_RESET_TYPE,
            "prefix": f"password-reset:{user_id}:%",
            "cid": company_id,
        },
    )
    return int(result.rowcount or 0)


def create_reset_token(
    db: Session, user_id: int, *, company_id: int
) -> tuple[str, str]:
    """Yeni tek kullanımlık token üretir; eskisini ve kuyruğunu kapatır."""
    raw_token = secrets.token_urlsafe(48)
    now = utcnow()
    db.execute(
        update(password_reset_tokens)
        .where(
            password_reset_tokens.c.user_id == user_id,
            password_reset_tokens.c.used_at.is_(None),
        )
        .values(used_at=now)
    )
    _cancel_superseded_resets(db, user_id, company_id=company_id)
    db.execute(
        insert(password_reset_tokens).values(
            user_id=user_id,
            token_hash=token_digest(raw_token),
            created_at=now,
            expires_at=now + timedelta(hours=TOKEN_LIFETIME_HOURS),
            used_at=None,
        )
    )
    link = f"{settings.public_app_url.rstrip('/')}/sifre-sifirla?token={raw_token}"
    return raw_token, link


def queue_reset_email(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    email: str,
    link: str,
) -> int:
    """Sıfırlama e-postasını sistem şeridi satırı olarak kuyruğa alır.

    Doğrulama e-postasıyla aynı gerekçelerle ``PENDING`` + silahlı doğar ve
    şablon boru hattından geçmez: gövde kodda, alıcı kullanıcının kendi adresi.
    """
    if settings.environment == "development":
        logger.info("Geliştirme sıfırlama bağlantısı: %s", link)
    return int(
        enqueue_notification(
            db,
            company_id=company_id,
            type_=PASSWORD_RESET_TYPE,
            channel=EMAIL_CHANNEL,
            recipient=email,
            template="password_reset",
            payload_dict={
                "reset_url": link,
                "subject": PASSWORD_RESET_SUBJECT,
                "body": _reset_body(link),
            },
            dedupe_key=_reset_dedupe_key(user_id, link),
            armed=True,
            approved_by=SYSTEM_APPROVER_ID,
            approval_mode=SYSTEM_APPROVAL_MODE,
        )
    )
