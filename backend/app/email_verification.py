"""Kayıt/doğrulama e-postalarının içerik ve kuyruk katmanı (F0-email).

Bu modül **posta göndermez**. Üç işi vardır:

1. Tek kullanımlık doğrulama tokenı üretmek ve eskisini geçersizlemek.
2. Gönderilecek mesajın içeriğini (konu + gövde) üretip bildirim outbox'ına
   *sistem şeridi* satırı olarak yazmak.
3. Kaynak transaction commit edildikten sonra tek bir **fırsatçı** gönderim
   denemesi tetiklemek.

SMTP taşıyıcısı ``notifications/provider.py`` içindedir; ``smtplib`` importu
bu modülde yoktur. Şablonlar da DB'den gelmez: ``notification_templates``
boru hattı §5.5 içerik kapısına bağlıdır ve kapı şema/alan adı/URL içeren
metni reddeder — doğrulama mesajı ise tamamen bir bağlantıdır.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import timedelta

from sqlalchemy import insert, text, update
from sqlalchemy.orm import Session

from .auth import email_verification_tokens, token_digest, utcnow
from .config import settings
from .notifications.schema import (
    CANCELLED,
    PENDING,
    RETRY_SCHEDULED,
    SYSTEM_APPROVAL_MODE,
    SYSTEM_APPROVER_ID,
    VERIFICATION_TTL_HOURS,
)
from .notifications.service import (
    NotificationBusyError,
    NotificationNotRetryableError,
    assert_transition,
    enqueue_notification,
    send_notification,
)

logger = logging.getLogger("yerel_hesap.email_verification")

# Token ömrü ile kuyruktaki satırın ömrü aynı sabitten türer: ikisi ayrışırsa
# ya süresi dolmuş link postalanır ya da geçerli satır boşuna iptal edilir.
TOKEN_LIFETIME_HOURS = VERIFICATION_TTL_HOURS

VERIFICATION_TYPE = "EMAIL_VERIFICATION"
REGISTRATION_ATTEMPT_TYPE = "REGISTRATION_ATTEMPT"
EMAIL_CHANNEL = "EMAIL"

VERIFICATION_SUBJECT = "E-posta adresinizi doğrulayın"
EXISTING_ACCOUNT_SUBJECT = "Hesabınızla kayıt denemesi"
EXISTING_ACCOUNT_BODY = (
    "Bu adresle kayıt denemesi yapıldı. Hesabınız zaten var; "
    "giriş yapın veya şifrenizi sıfırlayın."
)
_SUPERSEDED_REASON = "Yeni doğrulama bağlantısı üretildi; bu satır gönderilmedi"


def _verification_body(link: str) -> str:
    return (
        f"Harman Zamanı hesabınızı doğrulamak için {TOKEN_LIFETIME_HOURS} "
        f"saat içinde bağlantıyı açın:\n{link}"
    )


def _verification_dedupe_key(user_id: int, link: str) -> str:
    return f"email-verification:{user_id}:{token_digest(link)[0:20]}"


def _cancel_superseded_verifications(
    db: Session, user_id: int, *, company_id: int
) -> int:
    """Aynı kullanıcının gönderilmemiş doğrulama satırlarını iptal eder.

    Yeni token üretildiği anda eskisi ölür (aşağıdaki ``update``). Eski satır
    kuyrukta kalırsa kullanıcı iki mail alır ve önce geleni tıklarsa "geçersiz
    bağlantı" görür. Bu yüzden bastırma, tokenı geçersizleyen işlemle **aynı
    transaction** içinde yapılır.

    Yalnız henüz başlamamış satırlara dokunulur; ``PROCESSING`` satırın dış
    çağrısı başlamış olabilir (§3.3) ve eski ``NONE`` satırlar denetim kaydı
    olarak yerinde bırakılır.
    """
    assert_transition(PENDING, CANCELLED)
    assert_transition(RETRY_SCHEDULED, CANCELLED)
    parameters: dict[str, object] = {
        "cancelled": CANCELLED,
        "disarmed": False,
        "now": utcnow(),
        "reason": _SUPERSEDED_REASON,
        "type": VERIFICATION_TYPE,
        "prefix": f"email-verification:{user_id}:%",
        "cid": company_id,
    }
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
        parameters,
    )
    return int(result.rowcount or 0)


def create_verification_token(
    db: Session, user_id: int, *, company_id: int
) -> tuple[str, str]:
    """Yeni tek kullanımlık token üretir; eskisini ve kuyruğunu kapatır."""
    raw_token = secrets.token_urlsafe(48)
    now = utcnow()
    db.execute(
        update(email_verification_tokens)
        .where(
            email_verification_tokens.c.user_id == user_id,
            email_verification_tokens.c.used_at.is_(None),
        )
        .values(used_at=now)
    )
    _cancel_superseded_verifications(db, user_id, company_id=company_id)
    db.execute(
        insert(email_verification_tokens).values(
            user_id=user_id,
            token_hash=token_digest(raw_token),
            created_at=now,
            expires_at=now + timedelta(hours=TOKEN_LIFETIME_HOURS),
            used_at=None,
        )
    )
    link = f"{settings.public_app_url.rstrip('/')}/eposta-dogrula?token={raw_token}"
    return raw_token, link


def queue_verification_email(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    email: str,
    link: str,
) -> int:
    """Doğrulama e-postasını sistem şeridi satırı olarak kuyruğa alır.

    Satır ``PENDING`` + silahlı doğar: onaylanacak bir içerik kararı yoktur
    (gövde kodda, alıcı kullanıcının kendi adresi, tetikleyici kullanıcının
    kendi eylemi). ``message_class`` ve ``content_hash`` boştur — satır şablon
    boru hattından geçmemiş bir "ham seam" kaydıdır ve motorun §2.5 adım 7
    kontrolü bu ikisinin birlikte boş olmasını şart koşar.

    Hata alanları **yazılmaz**: hiç denenmemiş bir satıra "gönderilemedi"
    yazmak denetim kaydını yalanlar.
    """
    if settings.environment == "development":
        logger.info("Geliştirme doğrulama bağlantısı: %s", link)
    return int(
        enqueue_notification(
            db,
            company_id=company_id,
            type_=VERIFICATION_TYPE,
            channel=EMAIL_CHANNEL,
            recipient=email,
            template="email_verification",
            payload_dict={
                # ``verification_url`` panel önizlemesi ve testler için ayrıca
                # taşınır; taşıyıcı yalnız subject/body okur.
                "verification_url": link,
                "subject": VERIFICATION_SUBJECT,
                "body": _verification_body(link),
            },
            dedupe_key=_verification_dedupe_key(user_id, link),
            armed=True,
            approved_by=SYSTEM_APPROVER_ID,
            approval_mode=SYSTEM_APPROVAL_MODE,
        )
    )


def queue_existing_account_notice(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    email: str,
) -> int:
    """Var olan hesaba yapılan kayıt denemesi bildirimi.

    Dedupe anahtarı **gün** çözünürlüğündedir: aynı adrese tekrarlanan kayıt
    denemesi günde bir bildirim üretir. Rastgele anahtar dedupe'u tamamen
    kapatır ve bu ucu bir mail amplifikasyon aracına çevirirdi.
    """
    return int(
        enqueue_notification(
            db,
            company_id=company_id,
            type_=REGISTRATION_ATTEMPT_TYPE,
            channel=EMAIL_CHANNEL,
            recipient=email,
            template="existing_account_registration_attempt",
            payload_dict={
                "message": EXISTING_ACCOUNT_BODY,
                "subject": EXISTING_ACCOUNT_SUBJECT,
                "body": EXISTING_ACCOUNT_BODY,
            },
            dedupe_key=(
                f"registration-attempt:{user_id}:{utcnow().date().isoformat()}"
            ),
            armed=True,
            approved_by=SYSTEM_APPROVER_ID,
            approval_mode=SYSTEM_APPROVAL_MODE,
        )
    )


def deliver_now(db: Session, *, company_id: int, notification_id: int) -> None:
    """Commit SONRASI tek fırsatçı gönderim denemesi.

    Bu çağrı bir optimizasyondur, garanti değil: kaynak satır zaten yazılmış
    ve silahlanmıştır; başarısız olursa drenaj betiği devralır. İstisna
    yutulur — kayıt akışı mail yüzünden 500 vermez — ama artık yutulan hiçbir
    şey kaybolmaz: ``send_notification`` sonucu (``status``, ``attempt_count``,
    ``last_error``, ``next_attempt_at``) satıra yazmıştır.
    """
    try:
        send_notification(db, company_id=company_id, notification_id=notification_id)
    except (NotificationBusyError, NotificationNotRetryableError) as exc:
        # Beklenen durumlar: dedupe nedeniyle zaten gönderilmiş bir satır ya da
        # başka bir sürecin lease'i. Bunları ERROR + traceback ile yazmak,
        # normal akışı üretim loglarında arıza gibi gösterirdi.
        logger.info(
            "Fırsatçı gönderim atlandı (#%s): %s", notification_id, exc
        )
    except Exception:
        logger.exception(
            "Bildirim gönderimi denenemedi",
            extra={"company_id": company_id, "notification_id": notification_id},
        )


def notification_payload(raw: str) -> dict:
    """Outbox payload'ını sözlüğe çevirir (panel/test yardımcı fonksiyonu)."""
    return json.loads(raw)
