"""Persistence and dispatch orchestration for the notification outbox.

Tasarım kaynağı: ``TASARIM_SERVIS_HATIRLATMA_BILDIRIM_F0.md`` rev4.

Bu modülün taşıdığı dört pazarlıksız invariant:

* **Kapalı geçiş tablosu (§2.3).** ``_ALLOWED_TRANSITIONS`` dışında bir durum
  geçişi yoktur; katalog dışı geçiş ``ValueError`` ile reddedilir.
* **Lease yükleminin beş koşulu veritabanındadır (§2.4).** ``AWAITING_APPROVAL``
  ya da silahsız bir satır SQL seviyesinde elenir; uygulama filtresine
  güvenilmez.
* **Rıza her denemede yeniden okunur (§3.3).** Satırdaki anlık görüntü denetim
  kanıtıdır, karar verici değildir. Provider çağrısından hemen önceki okuma
  gönderimin linearization point'idir.
* **Dış çağrı boyunca veritabanı kilidi tutulmaz (§3.3).** Eşzamanlılık lease +
  ``lock_token`` CAS'i ile sağlanır; claim commit edilir, sonra provider
  çağrılır.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, and_, bindparam, insert, or_, select, text, update
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..config import settings
from . import consents as consent_service
from .provider import (
    NotificationProvider,
    NotificationResult,
    get_notification_provider,
)
from .render import canonical_payload, content_hash as compute_content_hash
from .schema import (
    AUTH_SYSTEM_TYPES,
    AWAITING_APPROVAL,
    CANCELLED,
    COMPLIANCE_CHECK_UNAVAILABLE,
    CONSENT_REQUIRED_CHANNELS,
    DELIVERED,
    DISPATCHABLE_STATUSES,
    FAILED,
    NONE_STATUS,
    PENDING,
    PROCESSING,
    REJECTED,
    RETRY_SCHEDULED,
    SENT,
    SIMULATED,
    TERMINAL_STATUSES,
    VERIFICATION_TTL_HOURS,
    notifications,
)


_LEASE_MINUTES = 5
_MAX_BACKOFF_SECONDS = 6 * 60 * 60  # §2.7 üst sınır: 6 saat
_JITTER_RATIO = 0.2

_ERROR_MESSAGES = {
    "AUTH": "Bildirim sağlayıcısı kimlik doğrulaması başarısız.",
    "NETWORK": "Bildirim sağlayıcısına ulaşılamadı.",
    "VALIDATION": "Bildirim içeriği sağlayıcı tarafından reddedildi.",
    "UNKNOWN": "Bildirim gönderilemedi.",
}
_ERROR_CLASS_NAMES = {
    "AUTH": frozenset({
        "authenticationerror",
        "authorizationerror",
        "forbiddenerror",
        "permissionerror",
        # smtplib.SMTPAuthenticationError'ın MRO'su OSError'a kadar iner ve
        # buradaki hiçbir jenerik adla kesişmez; SMTP sınıfları açıkça
        # sayılmazsa kimlik hatası "UNKNOWN" olarak kaydedilir.
        "smtpauthenticationerror",
        "unauthorizederror",
    }),
    "NETWORK": frozenset({
        "connectionerror",
        "networkerror",
        "requesterror",
        "smtpconnecterror",
        "smtpheloerror",
        "smtpserverdisconnected",
        "timeouterror",
    }),
    "VALIDATION": frozenset({
        "smtpdataerror",
        "smtpnotsupportederror",
        "smtprecipientsrefused",
        "smtpsenderrefused",
        "typeerror",
        "validationerror",
        "valueerror",
    }),
}
# §2.7 + AK-3: kalıcı hata yeniden denenmez. "550 böyle bir kullanıcı yok" ya
# da eksik gövde kendiliğinden düzelmez; 5 denemeyi 6 saate yaymak yalnız
# doğrulama bağlantısının 24 saatlik ömrünü yer. ``AUTH`` retry'da KALIR:
# operatör SMTP parolasını düzeltince kuyruk kendiliğinden akmalıdır.
_NON_RETRYABLE_ERROR_CODES: frozenset[str] = frozenset({"VALIDATION"})
logger = logging.getLogger("yerel_hesap.notifications")

# Ham ``text()`` SQL'de zaman damgaları AÇIKÇA tiplenir. Aksi hâlde sürücünün
# kendi datetime adaptörü devreye girer ve SQLite'ta ofsetli bir metin
# ("...+00:00") yazılır; Core ile derlenen karşılaştırmalar ise ofsetsiz bir
# literal üretir. İki format string olarak karşılaştırıldığında lease yüklemi
# sessizce yanlış sonuç verir — satır hiç seçilmez. #169 aynı nedenle aynı
# deseni kullanır.
_TS = DateTime(timezone=True)


def _ts(*names: str) -> list[Any]:
    return [bindparam(name, type_=_TS) for name in names]


# ---------------------------------------------------------------------------
# §2.3 Kapalı geçiş tablosu
# ---------------------------------------------------------------------------
# Anahtar: kaynak durum. Değer: o durumdan gidilebilecek durumların TAM kümesi.
# Burada yazmayan bir geçiş yoktur. Yeni bir geçiş eklemek, bu tabloya ve ilgili
# uca açık bir satır eklemek demektir.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    AWAITING_APPROVAL: frozenset({PENDING, CANCELLED, AWAITING_APPROVAL}),
    PENDING: frozenset({PROCESSING, CANCELLED, AWAITING_APPROVAL}),
    # RETRY_SCHEDULED -> RETRY_SCHEDULED: operatörün "backoff'u bekleme, şimdi
    # dene" işlemi. §2.6 tablosu bu durumu saymamıştı (yalnız FAILED/NONE'ı
    # sayıyordu) ama RETRY_SCHEDULED de terminal olmayan bir başarısız
    # durumdur ve aynı kapılardan (onay + silahlı + rıza engeli yok) geçer;
    # tek etkisi ``next_attempt_at``'in öne çekilmesidir.
    RETRY_SCHEDULED: frozenset(
        {PROCESSING, CANCELLED, AWAITING_APPROVAL, RETRY_SCHEDULED}
    ),
    PROCESSING: frozenset(
        {
            SENT,
            DELIVERED,
            SIMULATED,
            RETRY_SCHEDULED,
            FAILED,
            NONE_STATUS,
            REJECTED,
            COMPLIANCE_CHECK_UNAVAILABLE,
            # §2.5 adım 7: dispatch öncesi yeniden doğrulama başarısızsa
            # gönderim yapılmaz ve satır onaya geri döner.
            AWAITING_APPROVAL,
        }
    ),
    FAILED: frozenset({RETRY_SCHEDULED, CANCELLED}),
    NONE_STATUS: frozenset({RETRY_SCHEDULED, CANCELLED}),
    COMPLIANCE_CHECK_UNAVAILABLE: frozenset({RETRY_SCHEDULED, REJECTED, CANCELLED}),
    # Terminaller: çıkış yok.
    SENT: frozenset(),
    DELIVERED: frozenset(),
    SIMULATED: frozenset(),
    CANCELLED: frozenset(),
    REJECTED: frozenset(),
}


class NotificationNotFoundError(LookupError):
    """The requested notification does not exist in the active tenant."""


class NotificationBusyError(RuntimeError):
    """Another dispatcher currently owns the notification lease."""


class NotificationNotRetryableError(RuntimeError):
    """The notification is terminal or otherwise cannot be retried."""


class NotificationApprovalError(RuntimeError):
    """Onay reddedildi: bayat hash, dört-göz ihlali ya da uygunsuz durum."""


class InvalidTransitionError(ValueError):
    """Kapalı geçiş tablosunda bulunmayan bir durum geçişi denendi."""


def assert_transition(source: str, target: str) -> None:
    """§2.3 kapalı geçiş tablosunun tek doğrulama kapısı."""
    if source not in _ALLOWED_TRANSITIONS:
        raise InvalidTransitionError(f"Bilinmeyen bildirim durumu: {source}")
    if target not in _ALLOWED_TRANSITIONS[source]:
        raise InvalidTransitionError(
            f"İzin verilmeyen bildirim geçişi: {source} -> {target}"
        )


def _classify_provider_exception(exc: Exception) -> str:
    """Classify by exception type only; provider messages may contain secrets."""
    class_names = {base.__name__.lower() for base in type(exc).__mro__}
    for code in ("AUTH", "NETWORK", "VALIDATION"):
        if class_names & _ERROR_CLASS_NAMES[code]:
            return code
    return "UNKNOWN"


def build_notification_payload(event: dict[str, Any]) -> str:
    """Serialize a stable, Unicode-safe TEXT payload for both databases."""
    return json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _notification_row(db: Session, company_id: int, notification_id: int) -> dict:
    row = db.execute(
        select(notifications).where(
            notifications.c.id == notification_id,
            notifications.c.company_id == company_id,
        )
    ).mappings().first()
    if row is None:
        raise NotificationNotFoundError
    return dict(row)


def backoff_delay_seconds(
    attempt_count: int, *, rng: random.Random | None = None
) -> int:
    """§2.7 üstel backoff: ``min(2^n * 60s, 6 saat)`` ± %20 jitter.

    Jitter sunucu tarafında üretilir; deterministik test için ``rng``
    enjekte edilebilir.
    """
    generator = rng or random
    base = min(2 ** max(0, attempt_count) * 60, _MAX_BACKOFF_SECONDS)
    jitter = base * _JITTER_RATIO
    value = base + generator.uniform(-jitter, jitter)
    # Üst sınır jitter'dan sonra da korunur.
    return max(1, min(int(value), _MAX_BACKOFF_SECONDS))


# ---------------------------------------------------------------------------
# Kuyruğa alma
# ---------------------------------------------------------------------------
def enqueue_notification(
    db: Session,
    *,
    company_id: int,
    type_: str,
    channel: str,
    recipient: str,
    template: str,
    payload_dict: dict[str, Any],
    dedupe_key: str | None = None,
    created_by: int | None = None,
    message_class: str | None = None,
    template_id: int | None = None,
    template_version: int | None = None,
    content_hash: str | None = None,
    consent: dict[str, Any] | None = None,
    rule_id: int | None = None,
    armed: bool = False,
    approved_by: int | None = None,
    approval_mode: str | None = None,
    return_created: bool = False,
) -> int | tuple[int, bool]:
    """Persist one outbox row without contacting an external provider.

    The caller owns the source transaction. Delivery is deliberately deferred to
    ``send_notification`` so a rolled-back domain transaction can never emit an
    irreversible external message. A tenant-scoped dedupe key makes repeated
    domain events return the original outbox row.

    §2.3 geçiş 1 ve 2: onaysız yol ``AWAITING_APPROVAL``, açıkça silahlanmış
    yol ``PENDING`` üretir. Silahlanma onay bilgisi olmadan kabul edilmez —
    ``dispatch_armed`` tek başına dispatch için yeterli değildir (§2.4).
    """
    if armed and (approved_by is None or approval_mode is None):
        raise NotificationApprovalError(
            "Silahlı kuyruğa alma onaylayan kullanıcı ve onay modu gerektirir"
        )

    normalized_dedupe = (dedupe_key or "").strip()[:255] or None
    now = utcnow()
    status = PENDING if armed else AWAITING_APPROVAL
    values = {
        "company_id": company_id,
        "type": type_,
        "channel": channel,
        "recipient": recipient,
        "template": template,
        "payload": build_notification_payload(payload_dict),
        "dedupe_key": normalized_dedupe,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "created_by": created_by,
        "message_class": message_class,
        "template_id": template_id,
        "template_version": template_version,
        "content_hash": content_hash,
        "rule_id": rule_id,
        "dispatch_armed": bool(armed),
        "approved_by": approved_by,
        "approved_at": now if armed else None,
        "approval_mode": approval_mode,
        # Silahsız satırda NULL kalır; ``NULL <= :now`` iki veritabanında da
        # yanlış döndüğü için lease yükleminden ayrıca elenir (§2.4).
        "next_attempt_at": now if armed else None,
        "consent_id": (consent or {}).get("consent_id"),
        "consent_version": (consent or {}).get("consent_version"),
        "consent_checked_at": (consent or {}).get("checked_at"),
        "consent_decision": (consent or {}).get("decision"),
        "consent_decision_reason": (consent or {}).get("reason"),
    }
    bilinmeyen = sorted(set(values) - set(_ENQUEUE_SUTUNLARI) - {"company_id"})
    if bilinmeyen:
        raise ValueError(f"enqueue_notification bilinmeyen sütun aldı: {bilinmeyen}")

    if normalized_dedupe is None:
        # Sözlüğü olduğu gibi yaymak yerine AÇIK demetten kuruyoruz: hangi
        # sütunların yazıldığı — ve `company_id`nin isteğin kiracısına bağlı
        # olduğu — statik olarak görülebilsin.
        yazilacak = {ad: values[ad] for ad in _ENQUEUE_SUTUNLARI if ad in values}
        result = db.execute(
            insert(notifications).values(company_id=company_id, **yazilacak)
        )
        notification_id = int(result.inserted_primary_key[0])
        return (notification_id, True) if return_created else notification_id

    columns = ", ".join(values)
    placeholders = ", ".join(f":{name}" for name in values)
    # PostgreSQL 16 and the supported SQLite both implement this conflict form.
    # Unlike a nested SAVEPOINT, it remains part of the caller's
    # outer transaction on SQLite, so rolling back the source change also rolls
    # back the outbox row. Concurrent producers still fail closed to one row.
    inserted_id = db.execute(
        text(
            f"""
            INSERT INTO notifications ({columns})
            VALUES ({placeholders})
            ON CONFLICT (company_id, dedupe_key) DO NOTHING
            RETURNING id
            """
        ).bindparams(
            *_ts(
                "created_at",
                "updated_at",
                "approved_at",
                "next_attempt_at",
                "consent_checked_at",
            )
        ),
        values,
    ).scalar_one_or_none()
    if inserted_id is not None:
        notification_id = int(inserted_id)
        return (notification_id, True) if return_created else notification_id

    existing_id = db.execute(
        select(notifications.c.id).where(
            notifications.c.company_id == company_id,
            notifications.c.dedupe_key == normalized_dedupe,
        )
    ).scalar_one()
    notification_id = int(existing_id)
    return (notification_id, False) if return_created else notification_id


# ---------------------------------------------------------------------------
# §2.5 Onay — CAS + dört-göz
# ---------------------------------------------------------------------------
def approve_notification(
    db: Session,
    *,
    company_id: int,
    notification_id: int,
    approver_id: int,
    seen_hash: str,
) -> dict[str, Any]:
    """``AWAITING_APPROVAL`` → ``PENDING`` (geçiş 3).

    Üç kapı birlikte uygulanır:

    * **Bayat önizleme.** Kullanıcının gördüğü hash CAS koşulundadır; içerik
      onay ile tıklama arasında değiştiyse onay reddedilir (§2.5 adım 6).
    * **Dört-göz.** Batch'i oluşturan kendi batch'ini onaylayamaz (§6).
    * **Durum.** Yalnız ``AWAITING_APPROVAL`` onaylanabilir.
    """
    row = _notification_row(db, company_id, notification_id)
    status = str(row["status"])
    if status != AWAITING_APPROVAL:
        raise NotificationApprovalError("Bu bildirim onay bekleyen durumda değil")
    assert_transition(status, PENDING)

    stored_hash = row.get("content_hash")
    if not stored_hash:
        raise NotificationApprovalError(
            "İçerik özeti olmayan bildirim bu uçtan onaylanamaz"
        )
    if stored_hash != seen_hash:
        raise NotificationApprovalError(
            "Önizleme güncel değil: içerik değişmiş, yeniden inceleyin"
        )
    created_by = row.get("created_by")
    if created_by is not None and int(created_by) == int(approver_id):
        raise NotificationApprovalError(
            "Kendi oluşturduğunuz gönderimi onaylayamazsınız"
        )

    now = utcnow()
    updated = db.execute(
        update(notifications)
        .where(
            notifications.c.id == notification_id,
            notifications.c.company_id == company_id,
            notifications.c.status == AWAITING_APPROVAL,
            # CAS: onay ile UPDATE arasında içerik değişmişse satır alınmaz.
            notifications.c.content_hash == seen_hash,
            # Dört-göz güvencesi VERİTABANI yolunda da: yukarıdaki Python
            # kontrolü okuma anına bakar; okuma ile UPDATE arasında
            # ``created_by`` onaylayana dönüşürse (TOCTOU) bu koşul satırı
            # düşürür. CHECK constraint gerekmez — koşul CAS'in parçasıdır.
            or_(
                notifications.c.created_by.is_(None),
                notifications.c.created_by != approver_id,
            ),
        )
        .values(
            status=PENDING,
            dispatch_armed=True,
            approved_by=approver_id,
            approved_at=now,
            approval_mode="MANUAL",
            next_attempt_at=now,
            updated_at=now,
        )
    )
    if updated.rowcount != 1:
        raise NotificationApprovalError("Onay sırasında bildirim değişti")
    return _notification_row(db, company_id, notification_id)


def cancel_notification(
    db: Session, *, company_id: int, notification_id: int
) -> dict[str, Any]:
    """Geçiş 4/14: bekleyen satırı iptal eder. ``PROCESSING`` iptal edilemez."""
    row = _notification_row(db, company_id, notification_id)
    status = str(row["status"])
    if status == PROCESSING:
        raise NotificationBusyError("Gönderimi başlamış bildirim iptal edilemez")
    assert_transition(status, CANCELLED)

    now = utcnow()
    updated = db.execute(
        update(notifications)
        .where(
            notifications.c.id == notification_id,
            notifications.c.company_id == company_id,
            notifications.c.status == status,
        )
        .values(
            status=CANCELLED, dispatch_armed=False, next_attempt_at=None, updated_at=now
        )
    )
    if updated.rowcount != 1:
        raise NotificationBusyError("İptal sırasında bildirim değişti")
    return _notification_row(db, company_id, notification_id)


def invalidate_approval(
    db: Session, *, company_id: int, notification_id: int, new_content_hash: str
) -> dict[str, Any]:
    """Geçiş 5/6: içerik veya alıcı değişti → önceki onay geçersiz (§2.5).

    Satır ``AWAITING_APPROVAL``'a döner ve onay üçlüsü temizlenir. Yeniden
    değerlendirme doğrudan re-arm DEĞİLDİR; yeni ve açık bir onay gerekir.
    """
    row = _notification_row(db, company_id, notification_id)
    status = str(row["status"])
    if status == PROCESSING:
        raise NotificationBusyError("Gönderimi başlamış bildirim değiştirilemez")
    assert_transition(status, AWAITING_APPROVAL)

    now = utcnow()
    db.execute(
        update(notifications)
        .where(
            notifications.c.id == notification_id,
            notifications.c.company_id == company_id,
            notifications.c.status == status,
        )
        .values(
            status=AWAITING_APPROVAL,
            content_hash=new_content_hash,
            dispatch_armed=False,
            approved_by=None,
            approved_at=None,
            approval_mode=None,
            next_attempt_at=None,
            updated_at=now,
        )
    )
    return _notification_row(db, company_id, notification_id)


# ---------------------------------------------------------------------------
# §2.9 Disarm — kural kapatıldığında
# ---------------------------------------------------------------------------
def disarm_rule_notifications(
    db: Session, *, company_id: int, rule_id: int
) -> int:
    """Kuralı kapatan işlemin AYNI transaction'ında çalışır; commit ETMEZ.

    Yalnız henüz başlamamış (``PENDING``/``RETRY_SCHEDULED``) satırlar disarm
    edilir. ``PROCESSING`` satırlara dokunulmaz: onlar için provider çağrısı
    başlamış olabilir ve §3.3'teki "başlamış dış işlem geri alınamaz" sınırı
    burada aynen geçerlidir.

    Filtre ``rule_id`` **ve** ``company_id`` taşır; yalnız ``rule_id`` ile
    yazmak kiracı sınırını aşma riski taşır ve kabul edilmez.

    Dönen değer etkilenen satır sayısıdır — arayüzde "kuyruktakiler de
    durduruldu (N satır)" olarak gösterilir ve denetim kaydına yazılır.
    """
    now = utcnow()
    result = db.execute(
        text(
            """UPDATE notifications
                  SET dispatch_armed = :disarmed,
                      disarmed_at = :now,
                      updated_at = :now
                WHERE company_id = :cid
                  AND rule_id = :rule_id
                  AND status IN ('PENDING', 'RETRY_SCHEDULED')"""
        ).bindparams(*_ts("now")),
        {"disarmed": False, "now": now, "cid": company_id, "rule_id": rule_id},
    )
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# §2.4 Lease
# ---------------------------------------------------------------------------
def _claim_notification(
    db: Session,
    *,
    company_id: int,
    notification_id: int,
    allow_expired_reclaim: bool = False,
) -> tuple[dict[str, Any], str]:
    """Atomically lease one dispatchable row and commit the claim.

    Yüklemin beş koşulu (§2.4) veritabanındadır:

    * ``status IN ('PENDING', 'RETRY_SCHEDULED')``
    * ``approved_at IS NOT NULL``
    * ``approved_by IS NOT NULL``
    * ``next_attempt_at <= now``
    * ``dispatch_armed = TRUE``

    ``AWAITING_APPROVAL`` ya da disarm edilmiş bir satır bu yüklemi hiçbir
    zaman geçemez.
    """
    now = utcnow()
    lock_token = uuid4().hex
    lease_until = now + timedelta(minutes=_LEASE_MINUTES)
    claimable = and_(
        notifications.c.status.in_(tuple(DISPATCHABLE_STATUSES)),
        notifications.c.approved_at.isnot(None),
        notifications.c.approved_by.isnot(None),
        notifications.c.next_attempt_at.isnot(None),
        notifications.c.next_attempt_at <= now,
        notifications.c.dispatch_armed.is_(True),
    )
    if allow_expired_reclaim:
        # Süresi dolmuş lease yalnız sağlayıcı gerçekten idempotent ise geri
        # alınır (§2.8); aksi hâlde otomatik geri alma çift gönderim demektir.
        claimable = or_(
            claimable,
            and_(
                notifications.c.status == PROCESSING,
                notifications.c.dispatch_armed.is_(True),
                or_(
                    notifications.c.locked_until.is_(None),
                    notifications.c.locked_until <= now,
                ),
            ),
        )
    claimed = db.execute(
        update(notifications)
        .where(
            notifications.c.id == notification_id,
            notifications.c.company_id == company_id,
            claimable,
        )
        .values(
            status=PROCESSING,
            attempt_count=notifications.c.attempt_count + 1,
            last_attempt_at=now,
            locked_until=lease_until,
            lock_token=lock_token,
            updated_at=now,
        )
    )
    if claimed.rowcount == 1:
        row = db.execute(
            select(notifications).where(
                notifications.c.id == notification_id,
                notifications.c.company_id == company_id,
                notifications.c.status == PROCESSING,
                notifications.c.lock_token == lock_token,
            )
        ).mappings().one()
        db.commit()
        return dict(row), lock_token

    db.rollback()
    current = _notification_row(db, company_id, notification_id)
    status = str(current.get("status") or "")
    armed = bool(current.get("dispatch_armed"))
    approved = current.get("approved_at") is not None
    db.rollback()
    if status in TERMINAL_STATUSES:
        raise NotificationNotRetryableError("Kesinleşmiş bildirim yeniden gönderilemez")
    if status == PROCESSING:
        raise NotificationBusyError("Bildirim başka bir işlem tarafından gönderiliyor")
    if status == AWAITING_APPROVAL or not approved:
        raise NotificationNotRetryableError("Bildirim onaylanmadan gönderilemez")
    if not armed:
        raise NotificationNotRetryableError(
            "Bildirim durduruldu: gönderim için yeniden onay gerekir"
        )
    raise NotificationNotRetryableError("Bildirim yeniden gönderilebilir durumda değil")


# ---------------------------------------------------------------------------
# §3.3 + §2.5 adım 7: gönderim öncesi son kapılar
# ---------------------------------------------------------------------------
def _verify_content_hash(row: dict[str, Any], payload: dict[str, Any]) -> bool:
    """§2.5 adım 7 — saklanan payload'ın hash'i yeniden hesaplanır.

    ``content_hash`` boş olan satırlar ham seam kayıtlarıdır (şablon boru
    hattından geçmemiş, ör. e-Fatura tarafı). Bunlarda doğrulanacak bir özet
    yoktur; ``message_class`` da boş olmalıdır, aksi hâlde satır şablon
    yolundan gelmiş ama özeti kaybetmiş demektir ve fail-closed reddedilir.
    """
    stored = row.get("content_hash")
    if not stored:
        return row.get("message_class") in (None, "")

    recomputed = compute_content_hash(
        canonical_payload(
            message_class=str(row.get("message_class") or ""),
            channel=str(row.get("channel") or ""),
            recipient=str(row.get("recipient") or ""),
            template_id=row.get("template_id"),
            template_version=row.get("template_version"),
            subject=payload.get("subject"),
            body=str(payload.get("body") or ""),
            metadata=payload.get("metadata") or {},
        )
    )
    return recomputed == stored


def _consent_gate(
    db: Session, row: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Provider çağrısından hemen önceki SON rıza okuması (§3.3).

    Bu okuma gönderimin linearization point'idir: o anda commit edilmiş bir
    iptal gönderimi engeller. Gönderim başladıktan sonra gelen iptal ise devam
    eden dış işlemi geri alamaz — bu sınır bilinçlidir ve dokümantedir.

    Satırdaki rıza anlık görüntüsüne **bakılmaz**; her deneme yeniden okur.
    """
    channel = str(row.get("channel") or "").strip().upper()
    if channel not in CONSENT_REQUIRED_CHANNELS:
        return None

    # F0-email sistem şeridi: kimlik doğrulama mesajı hesabın SAHİBİNE, onun
    # kendi eylemi üzerine gider. Rıza kaydının tarafı DB seviyesinde yalnız
    # CUSTOMER/SUPPLIER olabildiği için bu satırların bir rıza kimliği yoktur;
    # taraf araması yapılırsa TARGET_MISSING ile terminal REJECTED olurlar.
    # Muafiyet KAPALI bir tip kümesiyle sınırlıdır (§AUTH_SYSTEM_TYPES) ve
    # taraf aramasından ÖNCE uygulanır.
    if str(row.get("type") or "") in AUTH_SYSTEM_TYPES:
        return None

    party_type = payload.get("party_type")
    party_id = payload.get("party_id")
    if not party_type or party_id is None:
        return {
            "allowed": False,
            "decision": "BLOCKED",
            "reason": "TARGET_MISSING",
            "message": "Bildirimin izin sahibi tarafı belirlenemedi",
            "consent_id": None,
            "consent_version": None,
            "checked_at": utcnow(),
        }

    return consent_service.evaluate_consent(
        db,
        company_id=int(row["company_id"]),
        party_type=str(party_type),
        party_id=int(party_id),
        channel=channel,
        recipient=str(row.get("recipient") or ""),
    )


# ``_finalize`` UPDATE'inin yazabileceği sütunlar — AÇIK ve TAM liste.
# ``company_id`` BİLEREK YOK: bir kiracı tablosunda satırın sahibini
# değiştirmek çapraz kiracı yazımıdır. Liste modül düzeyinde bir demet olduğu
# için bu özellik statik analizle GÖRÜLEBİLİR; çağıranların iyi niyetine
# bağlı değildir.
_FINALIZE_SUTUNLARI = (
    "approval_mode",
    "approved_at",
    "approved_by",
    "consent_checked_at",
    "consent_decision",
    "consent_decision_reason",
    "consent_id",
    "consent_version",
    "dispatch_armed",
    "external_id",
    "last_error",
    "last_error_code",
    "lock_token",
    "locked_until",
    "next_attempt_at",
)


# ``enqueue_notification`` INSERT'inin yazdığı sütunlar — AÇIK ve TAM liste.
# `_FINALIZE_SUTUNLARI` ile aynı desen ve aynı gerekçe: kiracıya yazan bir
# ifadenin sütun kümesi STATİK olarak görülebilir olmalı. Buradaki fark, o
# listede `company_id`nin bilerek YOK olması, burada ise bilerek VAR olması —
# INSERT satırın sahibini BELİRLER. `company_id` bu demette DE yok: açık
# anahtar olarak veriliyor ki isteğin kiracısına bağlı olduğu statik olarak
# GÖRÜLEBİLSİN — bir yayılımın içinde kaybolmasın.
#
# Sözlüğün kendisi kalıyor: `ON CONFLICT` yolundaki `text()` sorgusu sütun ve
# yer tutucu listesini ondan üretiyor ve bağlama parametresi olarak veriyor.
# Çözülemez olan o sözlüktür; Core INSERT artık ona değil bu demete dayanıyor.
_ENQUEUE_SUTUNLARI = (
    "type",
    "channel",
    "recipient",
    "template",
    "payload",
    "dedupe_key",
    "status",
    "created_at",
    "updated_at",
    "created_by",
    "message_class",
    "template_id",
    "template_version",
    "content_hash",
    "rule_id",
    "dispatch_armed",
    "approved_by",
    "approved_at",
    "approval_mode",
    "next_attempt_at",
    "consent_id",
    "consent_version",
    "consent_checked_at",
    "consent_decision",
    "consent_decision_reason",
)


def _finalize(
    db: Session,
    *,
    company_id: int,
    notification_id: int,
    lock_token: str,
    source_status: str,
    target_status: str,
    values: dict[str, Any],
) -> None:
    """Tek ``UPDATE`` ile sonuç yazımı; lease sahipliği CAS koşulundadır.

    Yazılabilir sütunlar ``_FINALIZE_SUTUNLARI`` ile SINIRLI. Eskiden çağırandan
    gelen sözlük olduğu gibi yayılıyordu (``**values``); bu, "hangi sütunların
    yazıldığı" sorusunu her çağıranın davranışına bağlıyordu ve statik olarak
    görülemiyordu. Açık liste, ``company_id``nin buradan ASLA yazılamayacağını
    yapısal olarak gösterir — kiracı tablosunda satırın sahibini değiştirmek,
    tam olarak engellenmek istenen çapraz kiracı yazımıdır.
    """
    assert_transition(source_status, target_status)
    bilinmeyen = sorted(set(values) - set(_FINALIZE_SUTUNLARI))
    if bilinmeyen:
        raise ValueError(
            f"_finalize yazamayacağı sütun aldı: {bilinmeyen}"
        )
    yazilacak = {ad: values[ad] for ad in _FINALIZE_SUTUNLARI if ad in values}
    updated = db.execute(
        update(notifications)
        .where(
            notifications.c.id == notification_id,
            notifications.c.company_id == company_id,
            notifications.c.lock_token == lock_token,
            notifications.c.status == PROCESSING,
        )
        .values(status=target_status, updated_at=utcnow(), **yazilacak)
    )
    if updated.rowcount != 1:
        db.rollback()
        raise NotificationBusyError("Bildirim gönderim sahipliği değişti")
    db.commit()


def send_notification(
    db: Session,
    *,
    company_id: int,
    notification_id: int,
    provider: NotificationProvider | None = None,
    rng: random.Random | None = None,
) -> NotificationResult:
    """Claim, dispatch and finalize a tenant-scoped outbox row.

    The claim is committed before the provider is called; **dış çağrı boyunca
    hiçbir veritabanı kilidi tutulmaz** (§3.3). A stable provider idempotency
    key derived from the immutable outbox identity is included in the provider
    payload, allowing future adapters to deduplicate ambiguous retries.
    """
    selected_provider = provider or get_notification_provider(settings)
    row, lock_token = _claim_notification(
        db,
        company_id=company_id,
        notification_id=notification_id,
        allow_expired_reclaim=selected_provider.supports_idempotency,
    )
    payload = json.loads(row["payload"])

    # --- Adım 7: içerik bütünlüğü (§2.5) ---
    if not _verify_content_hash(row, payload):
        _finalize(
            db,
            company_id=company_id,
            notification_id=notification_id,
            lock_token=lock_token,
            source_status=PROCESSING,
            target_status=AWAITING_APPROVAL,
            values={
                "dispatch_armed": False,
                "approved_by": None,
                "approved_at": None,
                "approval_mode": None,
                "next_attempt_at": None,
                "locked_until": None,
                "lock_token": None,
                "last_error": "İçerik özeti doğrulanamadı, yeniden onay gerekiyor",
                "last_error_code": "VALIDATION",
            },
        )
        return NotificationResult(
            status="FAILED",
            message="İçerik değişmiş: gönderim durduruldu, yeniden onay gerekiyor",
        )

    # --- Linearization point: son rıza okuması (§3.3) ---
    decision = _consent_gate(db, row, payload)
    if decision is not None and not decision["allowed"]:
        _finalize(
            db,
            company_id=company_id,
            notification_id=notification_id,
            lock_token=lock_token,
            source_status=PROCESSING,
            target_status=REJECTED,
            values={
                "dispatch_armed": False,
                "next_attempt_at": None,
                "locked_until": None,
                "lock_token": None,
                "consent_id": decision.get("consent_id"),
                "consent_version": decision.get("consent_version"),
                "consent_checked_at": decision.get("checked_at"),
                "consent_decision": "BLOCKED",
                "consent_decision_reason": decision.get("reason"),
                "last_error": decision.get("message"),
            },
        )
        return NotificationResult(status="FAILED", message=decision.get("message"))

    # Rıza SELECT'inin açtığı okuma transaction'ı burada KAPATILIR: sonuç
    # ``decision`` yerel değişkeninde, dış sağlayıcı çağrısı boyunca bağlantı
    # üzerinde açık transaction (dolayısıyla tutulmuş snapshot/kilit) yoktur
    # (§3.3 "dış çağrı boyunca DB kilidi tutulmaz"). Sonuç yazımı ``_finalize``
    # içinde yeni ve kısa bir transaction'da, lock_token CAS'i ile yapılır.
    db.rollback()

    try:
        notification = {
            **row,
            "payload": payload,
            "provider_idempotency_key": f"notification:{company_id}:{notification_id}",
        }
        result = selected_provider.send(notification)
        if result.status == "FAILED":
            safe_message = _ERROR_MESSAGES["UNKNOWN"]
            result = result.model_copy(update={"message": safe_message})
            last_error, error_code = safe_message, "UNKNOWN"
        else:
            last_error, error_code = None, None
    except NotImplementedError:
        result = NotificationResult(
            status="NONE",
            message="Bildirim sağlayıcısı yapılandırılmamış",
        )
        last_error, error_code = None, None
    except Exception as exc:  # noqa: BLE001 - sınıflandırılıp maskelenir
        error_code = _classify_provider_exception(exc)
        safe_message = _ERROR_MESSAGES[error_code]
        logger.error(
            "Bildirim sağlayıcısı çağrısı başarısız (exception_type=%s)",
            type(exc).__name__,
            extra={
                "company_id": company_id,
                "notification_id": notification_id,
                "notification_error_code": error_code,
            },
        )
        result = NotificationResult(status="FAILED", message=safe_message)
        last_error = safe_message

    values: dict[str, Any] = {
        "external_id": result.external_id,
        "last_error": last_error,
        "last_error_code": error_code,
        "locked_until": None,
        "lock_token": None,
        "consent_id": (decision or {}).get("consent_id"),
        "consent_version": (decision or {}).get("consent_version"),
        "consent_checked_at": (decision or {}).get("checked_at"),
        "consent_decision": (decision or {}).get("decision"),
        "consent_decision_reason": (decision or {}).get("reason"),
    }

    # ``_claim_notification`` sayacı zaten artırmış ve ARTIRILMIŞ satırı
    # döndürmüştür; burada tekrar +1 eklemek off-by-one yapar ve satırı
    # max_attempts'e bir deneme ERKEN öldürür.
    attempt_count = int(row.get("attempt_count") or 0)
    max_attempts = int(row.get("max_attempts") or 5)
    target = result.status

    if (
        target == FAILED
        and error_code not in _NON_RETRYABLE_ERROR_CODES
        and attempt_count < max_attempts
    ):
        # §2.6/§2.7: attempt_count, next_attempt_at ve last_error_code aynı
        # UPDATE içinde atomik yazılır.
        target = RETRY_SCHEDULED
        values["next_attempt_at"] = utcnow() + timedelta(
            seconds=backoff_delay_seconds(attempt_count, rng=rng)
        )
    elif target == FAILED:
        values["next_attempt_at"] = None
    else:
        values["next_attempt_at"] = None

    _finalize(
        db,
        company_id=company_id,
        notification_id=notification_id,
        lock_token=lock_token,
        source_status=PROCESSING,
        target_status=target,
        values=values,
    )
    return result


# ---------------------------------------------------------------------------
# §2.6 Elle retry
# ---------------------------------------------------------------------------
def schedule_retry(
    db: Session, *, company_id: int, notification_id: int
) -> dict[str, Any]:
    """Geçiş 15: yalnız terminal olmayan başarısız durumlar.

    Retry **onay üretmez ve onay taşımaz**: ``approved_by``/``approved_at``
    alanlarına dokunmaz ve yüklem ``approved_at IS NOT NULL`` şartlıdır.
    Rıza ile bastırılmış satır retry edilemez — retry rızayı aşamaz.
    """
    row = _notification_row(db, company_id, notification_id)
    status = str(row["status"])

    if status in TERMINAL_STATUSES:
        raise NotificationNotRetryableError("Kesinleşmiş bildirim yeniden gönderilemez")
    if status == PROCESSING:
        raise NotificationBusyError("Bildirim başka bir işlem tarafından gönderiliyor")
    if status == AWAITING_APPROVAL:
        raise NotificationNotRetryableError("Önce bildirimin onaylanması gerekir")
    if str(row.get("consent_decision") or "") == "BLOCKED":
        raise NotificationNotRetryableError(
            "İzin engeli olan bildirim yeniden gönderilemez"
        )
    if row.get("approved_at") is None or row.get("approved_by") is None:
        raise NotificationNotRetryableError("Bildirim onaylanmadan gönderilemez")
    if not bool(row.get("dispatch_armed")):
        raise NotificationNotRetryableError(
            "Bildirim durduruldu: gönderim için yeniden onay gerekir"
        )

    assert_transition(status, RETRY_SCHEDULED)
    now = utcnow()
    updated = db.execute(
        update(notifications)
        .where(
            notifications.c.id == notification_id,
            notifications.c.company_id == company_id,
            notifications.c.status == status,
        )
        .values(status=RETRY_SCHEDULED, next_attempt_at=now, updated_at=now)
    )
    if updated.rowcount != 1:
        raise NotificationBusyError("Yeniden deneme sırasında bildirim değişti")
    return _notification_row(db, company_id, notification_id)


# ---------------------------------------------------------------------------
# §2.9 Metrikler — PENDING yalnız armed
# ---------------------------------------------------------------------------
def outbox_counters(db: Session, *, company_id: int) -> dict[str, int]:
    """Panel sayaçları.

    ``pending`` **yalnız** ``dispatch_armed = TRUE`` satırları sayar; disarm
    edilmişler ayrı ``disarmed`` metriğidir. İkisini karıştırmak, hiçbiri
    gönderilmeyecek satırları "kuyrukta bekliyor" diye göstermek olurdu.

    ``sent`` **yalnız** ``SENT`` + ``DELIVERED``'dır; ``SIMULATED`` ayrı bir
    metriktir ve hiçbir teslimat başarı oranının payına ya da paydasına girmez.
    Simülasyon dışarı hiçbir şey göndermez — onu "gönderildi" saymak raporu
    yalan söyler hâle getirirdi.

    F3 geçiş notu: gerçek sağlayıcı devreye alındığında eski ``SIMULATED``
    satırlar terminal denetim kaydı olarak yerinde kalır; hiçbir koşulda
    ``SENT``'e çevrilmez ve yeniden gönderilmez (yeniden gönderim = yeni satır
    + yeni onay).
    """
    row = db.execute(
        text(
            """SELECT
                 SUM(CASE WHEN status IN ('PENDING','RETRY_SCHEDULED')
                           AND dispatch_armed = :armed THEN 1 ELSE 0 END) AS pending,
                 SUM(CASE WHEN status IN ('PENDING','RETRY_SCHEDULED')
                           AND dispatch_armed = :disarmed THEN 1 ELSE 0 END)
                     AS disarmed,
                 SUM(CASE WHEN status = 'AWAITING_APPROVAL' THEN 1 ELSE 0 END)
                     AS awaiting_approval,
                 SUM(CASE WHEN status IN ('SENT','DELIVERED')
                          THEN 1 ELSE 0 END) AS sent,
                 SUM(CASE WHEN status = 'SIMULATED' THEN 1 ELSE 0 END)
                     AS simulated,
                 SUM(CASE WHEN status IN ('FAILED','NONE') THEN 1 ELSE 0 END)
                     AS failed,
                 SUM(CASE WHEN status IN ('CANCELLED','REJECTED') THEN 1 ELSE 0 END)
                     AS stopped
               FROM notifications
              WHERE company_id = :cid"""
        ),
        {"cid": company_id, "armed": True, "disarmed": False},
    ).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


# ---------------------------------------------------------------------------
# F0-email drenajı — cron/systemd tetikli toplu gönderim seçicileri
# ---------------------------------------------------------------------------
def claimable_notification_ids(
    db: Session,
    *,
    company_id: int,
    limit: int,
    now: Any | None = None,
) -> list[tuple[int, int]]:
    """Gönderilebilir satırların ``(company_id, id)`` listesi.

    Yüklem ``_claim_notification``'ınkiyle **birebir** aynıdır (§2.4'ün beş
    koşulu). Seçici gevşek olursa betik boşa claim denemesi yapar ve satır
    ``NotificationNotRetryableError`` ile gürültü üretir; sıkı olursa satır
    hiç görünmez. Bu yüzden iki yüklem birlikte değiştirilir.

    Sıra ``next_attempt_at, id``: en uzun bekleyen önce gider. Eşzamanlı
    çalışan iki drenaj aynı listeyi görebilir; çakışma lease + ``lock_token``
    CAS'i ile çözülür, seçici bir kilit DEĞİLDİR.
    """
    moment = now or utcnow()
    conditions = [
        notifications.c.status.in_(tuple(DISPATCHABLE_STATUSES)),
        notifications.c.approved_at.isnot(None),
        notifications.c.approved_by.isnot(None),
        notifications.c.next_attempt_at.isnot(None),
        notifications.c.next_attempt_at <= moment,
        notifications.c.dispatch_armed.is_(True),
        notifications.c.company_id == company_id,
    ]
    rows = db.execute(
        select(notifications.c.company_id, notifications.c.id)
        .where(and_(*conditions))
        .order_by(notifications.c.next_attempt_at, notifications.c.id)
        .limit(limit)
    ).all()
    return [(int(row[0]), int(row[1])) for row in rows]


def cancel_expired_verification_notifications(
    db: Session,
    *,
    company_id: int,
    now: Any | None = None,
) -> int:
    """Süresi dolmuş doğrulama satırlarını iptal eder; commit ETMEZ.

    Doğrulama bağlantısı ``VERIFICATION_TTL_HOURS`` sonra ölür
    (``email_verification.TOKEN_LIFETIME_HOURS`` aynı sabittir). Kuyrukta bu
    süreyi aşmış bir satırı postalamak, kullanıcıyı "geçersiz bağlantı"
    ekranına koşan bir mail üretmektir; gönderim yerine satır kapatılır.

    Yalnız henüz başlamamış satırlara dokunulur: ``PENDING``/
    ``RETRY_SCHEDULED`` → ``CANCELLED`` geçişleri kapalı tabloda tanımlıdır.
    ``PROCESSING`` satırlara dokunulmaz (§3.3), eski ``NONE`` satırlar ise
    denetim kaydı olarak yerinde bırakılır.
    """
    moment = now or utcnow()
    cutoff = moment - timedelta(hours=VERIFICATION_TTL_HOURS)
    assert_transition(PENDING, CANCELLED)
    assert_transition(RETRY_SCHEDULED, CANCELLED)
    parameters: dict[str, Any] = {
        "cancelled": CANCELLED,
        "disarmed": False,
        "now": moment,
        "cutoff": cutoff,
        "reason": "Doğrulama bağlantısının süresi doldu; gönderim yapılmadı",
        "type": "EMAIL_VERIFICATION",
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
                   AND created_at < :cutoff
                   AND company_id = :cid"""
        ).bindparams(*_ts("now", "cutoff")),
        parameters,
    )
    return int(result.rowcount or 0)


def stuck_processing_count(
    db: Session, *, company_id: int, now: Any | None = None
) -> int:
    """Lease'i dolmuş ``PROCESSING`` satır sayısı (AK-6 görünürlüğü).

    SMTP idempotent olmadığı için bu satırlar otomatik geri alınMAZ: geri
    almak çift mail demektir. Sayı drenaj betiğinin stderr çıktısına yazılır ki
    operatör "gönderiliyor" rozetinde takılı kalmış satırları görebilsin.

    Sayım her zaman tek kiracıyla sınırlıdır: ``--company A`` ile çalışan bir
    operatör B'nin satırlarını saymamalıdır — sayı da bir kiracı bilgisidir.
    """
    moment = now or utcnow()
    return int(
        db.execute(
            text(
                """SELECT COUNT(*) FROM notifications
                    WHERE status = 'PROCESSING'
                      AND locked_until IS NOT NULL
                      AND locked_until < :now
                      AND company_id = :cid"""
            ).bindparams(*_ts("now")),
            {"now": moment, "cid": company_id},
        ).scalar_one()
        or 0
    )
