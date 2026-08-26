"""§3 rıza (opt-in) yönetimi — KVKK fail-closed kapısı.

Üç sözleşme:

1. **Fail-closed.** Rıza kaydı yoksa, ``REVOKED`` ise, alıcı rıza anındaki
   numaradan farklıysa ya da numara normalize edilemiyorsa gönderim yapılmaz.
   Belirsizlik reddetme anlamına gelir; asla varsayılan-izin değil.
2. **Anlık görüntü karar verici değildir (§3.3).** ``notifications`` satırına
   yazılan ``consent_id``/``consent_version`` yalnız denetim kanıtıdır. Her
   gönderim denemesi — ilk deneme de, her retry de — rızayı yeniden okur.
3. **Append-only olay günlüğü.** Her GRANT/REVOKE ``notification_consent_events``
   tablosuna düşer ve versiyon artar; olay satırları hiçbir uçtan güncellenmez.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import DateTime, bindparam, text
from sqlalchemy.orm import Session

from ..auth import utcnow
from .schema import CONSENT_REQUIRED_CHANNELS


# Ham SQL'de zaman damgaları açıkça tiplenir; gerekçe için bkz. service.py.
_TS = DateTime(timezone=True)


def _ts(*names: str) -> list[Any]:
    return [bindparam(name, type_=_TS) for name in names]


class ConsentError(ValueError):
    """Rıza kaydı işlemi reddedildi."""


PARTY_TYPES: frozenset[str] = frozenset({"CUSTOMER", "SUPPLIER"})
CONSENT_SOURCES: frozenset[str] = frozenset({"FORM", "PHONE", "CONTRACT", "IMPORT"})

# Karar gerekçeleri — ``notifications.consent_decision_reason`` alanına yazılır.
NO_RECORD = "NO_RECORD"
REVOKED = "REVOKED"
RECIPIENT_CHANGED = "RECIPIENT_CHANGED"
RECIPIENT_INVALID = "RECIPIENT_INVALID"
CHANNEL_UNKNOWN = "CHANNEL_UNKNOWN"

_REASON_MESSAGES = {
    NO_RECORD: "Bu kanal için müşteri izni kaydı yok",
    REVOKED: "Müşteri bu kanal için iznini geri çekmiş",
    RECIPIENT_CHANGED: "Alıcı bilgisi izin verilen bilgiden farklı",
    RECIPIENT_INVALID: "Alıcı bilgisi geçerli değil",
    CHANNEL_UNKNOWN: "Bilinmeyen bildirim kanalı",
}


# ---------------------------------------------------------------------------
# Alıcı normalizasyonu
# ---------------------------------------------------------------------------
_DIGITS_RE = re.compile(r"\D+")


def normalize_msisdn(raw: str | None) -> str | None:
    """Serbest metin telefonu E.164'e indirger; başaramazsa ``None``.

    ``customers.phone`` doğrulanmamış serbest metindir ("0532 111 22 33",
    "532-111-22-33", "bilinmiyor" hepsi kabul edilmiştir). Normalize edilemeyen
    bir numara için gönderim kuyruğa **hiç girmez**.
    """
    if not raw:
        return None
    digits = _DIGITS_RE.sub("", str(raw))
    if not digits:
        return None
    # Türkiye: 90 ülke kodu, 10 haneli abone numarası, alan kodu 5 ile başlar.
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("90") and len(digits) == 12:
        subscriber = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        subscriber = digits[1:]
    elif len(digits) == 10:
        subscriber = digits
    else:
        return None
    if not subscriber.startswith("5"):
        return None
    return f"+90{subscriber}"


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def normalize_recipient(raw: str | None, channel: str) -> str | None:
    """Kanala göre alıcıyı normalize eder; belirsizse ``None`` (fail-closed)."""
    normalized_channel = (channel or "").strip().upper()
    if normalized_channel in {"SMS", "WHATSAPP"}:
        return normalize_msisdn(raw)
    if normalized_channel == "EMAIL":
        value = (raw or "").strip().lower()
        return value if _EMAIL_RE.match(value) else None
    return None


# ---------------------------------------------------------------------------
# Okuma
# ---------------------------------------------------------------------------
_CONSENT_COLUMNS = """id, company_id, party_type, party_id, channel, status,
    granted_at, revoked_at, source, source_ref, recipient_snapshot, version,
    created_by, created_at, updated_at"""


def get_consent(
    db: Session,
    *,
    company_id: int,
    party_type: str,
    party_id: int,
    channel: str,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            f"""SELECT {_CONSENT_COLUMNS} FROM notification_consents
                WHERE company_id = :cid
                  AND party_type = :party_type
                  AND party_id = :party_id
                  AND channel = :channel"""
        ),
        {
            "cid": company_id,
            "party_type": party_type,
            "party_id": party_id,
            "channel": channel,
        },
    ).mappings().first()
    return dict(row) if row else None


def list_consents(
    db: Session, *, company_id: int, party_type: str, party_id: int
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            f"""SELECT {_CONSENT_COLUMNS} FROM notification_consents
                WHERE company_id = :cid
                  AND party_type = :party_type
                  AND party_id = :party_id
                ORDER BY channel"""
        ),
        {"cid": company_id, "party_type": party_type, "party_id": party_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def list_consent_events(
    db: Session, *, company_id: int, consent_id: int, limit: int = 50
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """SELECT id, company_id, consent_id, action, old_status, new_status,
                      user_id, reason, created_at
                 FROM notification_consent_events
                WHERE company_id = :cid AND consent_id = :consent_id
                ORDER BY id DESC
                LIMIT :limit"""
        ),
        {"cid": company_id, "consent_id": consent_id, "limit": limit},
    ).mappings().all()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Değerlendirme — §3.3 fail-closed kapısı
# ---------------------------------------------------------------------------
def evaluate_consent(
    db: Session,
    *,
    company_id: int,
    party_type: str,
    party_id: int,
    channel: str,
    recipient: str,
) -> dict[str, Any]:
    """Gönderime izin var mı? Karar + kanıt döndürür, istisna yükseltmez.

    Bu fonksiyon iki noktada çağrılır (§3.3): üreticide (kuyruğa girmeden) ve
    ``send_notification`` içinde provider çağrısından **hemen önce**. İkinci
    çağrı gönderimin *linearization point*'idir.
    """
    normalized_channel = (channel or "").strip().upper()
    if normalized_channel not in CONSENT_REQUIRED_CHANNELS:
        # Bilinmeyen kanal fail-closed reddedilir. Rıza kapısından muaf kanallar
        # (ör. EINVOICE_PROVIDER) buraya hiç uğramaz; muafiyet çağıran tarafta
        # açık bir karardır, burada sessiz bir kaçış değil.
        return _decision(False, CHANNEL_UNKNOWN, None, None)

    normalized_recipient = normalize_recipient(recipient, normalized_channel)
    if not normalized_recipient:
        return _decision(False, RECIPIENT_INVALID, None, None)

    consent = get_consent(
        db,
        company_id=company_id,
        party_type=party_type,
        party_id=party_id,
        channel=normalized_channel,
    )
    if consent is None:
        return _decision(False, NO_RECORD, None, None)

    consent_id = int(consent["id"])
    version = int(consent["version"])
    if str(consent["status"]).upper() != "GRANTED":
        return _decision(False, REVOKED, consent_id, version)

    snapshot = normalize_recipient(consent.get("recipient_snapshot"), normalized_channel)
    if snapshot is not None and snapshot != normalized_recipient:
        # Numara değişmişse eski rıza yeni numarayı kapsamaz.
        return _decision(False, RECIPIENT_CHANGED, consent_id, version)

    return _decision(True, None, consent_id, version)


def _decision(
    allowed: bool, reason: str | None, consent_id: int | None, version: int | None
) -> dict[str, Any]:
    return {
        "allowed": allowed,
        "decision": "ALLOWED" if allowed else "BLOCKED",
        "reason": reason,
        "message": _REASON_MESSAGES.get(reason or "", "") if reason else None,
        "consent_id": consent_id,
        "consent_version": version,
        "checked_at": utcnow(),
    }


# ---------------------------------------------------------------------------
# Yazma — versiyon artışı + append-only olay
# ---------------------------------------------------------------------------
def set_consent(
    db: Session,
    *,
    company_id: int,
    party_type: str,
    party_id: int,
    channel: str,
    granted: bool,
    source: str,
    source_ref: str | None,
    recipient: str | None,
    user_id: int | None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Rızayı verir ya da geri çeker; olay günlüğüne yazar.

    Çağıranın transaction'ında çalışır ve **commit etmez** — #169'un aynı
    sözleşmesi: işlem düşerse rıza değişikliği de düşer.
    """
    normalized_party = (party_type or "").strip().upper()
    if normalized_party not in PARTY_TYPES:
        raise ConsentError("Geçersiz taraf tipi")
    normalized_channel = (channel or "").strip().upper()
    if normalized_channel not in CONSENT_REQUIRED_CHANNELS:
        raise ConsentError("Geçersiz bildirim kanalı")
    normalized_source = (source or "").strip().upper()
    if normalized_source not in CONSENT_SOURCES:
        raise ConsentError("Geçersiz izin kaynağı")

    snapshot: str | None = None
    if granted:
        snapshot = normalize_recipient(recipient, normalized_channel)
        if not snapshot:
            # İzin, kapsadığı alıcı bilgisi olmadan kaydedilemez: aksi hâlde
            # "kime izin verildi" sorusunun cevabı olmaz.
            raise ConsentError(
                "İzin kaydı için geçerli bir telefon/e-posta gereklidir"
            )

    now = utcnow()
    existing = get_consent(
        db,
        company_id=company_id,
        party_type=normalized_party,
        party_id=party_id,
        channel=normalized_channel,
    )
    new_status = "GRANTED" if granted else "REVOKED"

    if existing is None:
        consent_id = int(
            db.execute(
                text(
                    """INSERT INTO notification_consents
                       (company_id, party_type, party_id, channel, status,
                        granted_at, revoked_at, source, source_ref,
                        recipient_snapshot, version, created_by,
                        created_at, updated_at)
                       VALUES (:cid, :party_type, :party_id, :channel, :status,
                        :granted_at, :revoked_at, :source, :source_ref,
                        :snapshot, 1, :user_id, :now, :now)
                       RETURNING id"""
                ).bindparams(*_ts("now", "granted_at", "revoked_at")),
                {
                    "cid": company_id,
                    "party_type": normalized_party,
                    "party_id": party_id,
                    "channel": normalized_channel,
                    "status": new_status,
                    "granted_at": now if granted else None,
                    "revoked_at": None if granted else now,
                    "source": normalized_source,
                    "source_ref": source_ref,
                    "snapshot": snapshot,
                    "user_id": user_id,
                    "now": now,
                },
            ).scalar_one()
        )
        old_status = None
        version = 1
    else:
        consent_id = int(existing["id"])
        old_status = str(existing["status"])
        version = int(existing["version"]) + 1
        db.execute(
            text(
                # Korunacak alanlar KOLONUN KENDİSİNDEN okunur, uygulamaya
                # okunup geri yazılmaz: ham SELECT sonucu SQLite'ta metin,
                # PostgreSQL'de datetime döner ve geri bind edilirse iki
                # veritabanı farklı davranır. Geri çekmede ``granted_at`` ve
                # anlık görüntü yerinde kalır — hangi numaraya ne zaman izin
                # verilmiş olduğu denetimde görünmeye devam etmelidir.
                """UPDATE notification_consents
                      SET status = :status,
                          granted_at = CASE WHEN :granted THEN :now
                                            ELSE granted_at END,
                          revoked_at = CASE WHEN :granted THEN NULL
                                            ELSE :now END,
                          source = :source,
                          source_ref = :source_ref,
                          recipient_snapshot = CASE WHEN :granted THEN :snapshot
                                                    ELSE recipient_snapshot END,
                          version = :version,
                          updated_at = :now
                    WHERE company_id = :cid AND id = :consent_id"""
            ).bindparams(*_ts("now")),
            {
                "status": new_status,
                "granted": granted,
                "source": normalized_source,
                "source_ref": source_ref,
                "snapshot": snapshot,
                "version": version,
                "now": now,
                "cid": company_id,
                "consent_id": consent_id,
            },
        )

    db.execute(
        text(
            """INSERT INTO notification_consent_events
               (company_id, consent_id, action, old_status, new_status,
                user_id, reason, created_at)
               VALUES (:cid, :consent_id, :action, :old_status, :new_status,
                :user_id, :reason, :now)"""
        ).bindparams(*_ts("now")),
        {
            "cid": company_id,
            "consent_id": consent_id,
            "action": "GRANT" if granted else "REVOKE",
            "old_status": old_status,
            "new_status": new_status,
            "user_id": user_id,
            "reason": reason,
            "now": now,
        },
    )

    return {
        "id": consent_id,
        "status": new_status,
        "version": version,
        "channel": normalized_channel,
        "recipient_snapshot": snapshot,
    }
