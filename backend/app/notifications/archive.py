"""§2.9 disarmed satır arşivi — silme değil, soğuk saklamaya taşıma.

Durdurulmuş bir bildirim "gönderilmesi planlanmış ama durdurulmuş mesaj"
kaydıdır ve KVKK/uyum denetiminde "neden gönderilmedi" sorusunun kanıtıdır.
Bu yüzden saklama süresi dolduğunda **silinmez**, ``notifications_archive``
tablosuna taşınır.

İşin pazarlıksız sözleşmesi:

* **Sıra:** önce ``INSERT INTO notifications_archive``, sonra ``DELETE`` —
  **tek transaction içinde**. INSERT başarısızsa DELETE çalışmaz; kaynak satır
  kopyası kalıcı olarak yazılmadan asla kaldırılmaz.
* **Tenant-safe:** her ifade ``company_id = :cid`` literal filtresi taşır;
  şirketler arası toplu iş yapılmaz, şirket şirket ilerlenir.
* **Partili ve idempotent:** sabit parti boyu, ``id`` sırasına göre. İş yarıda
  kesilirse yeniden çalıştırma güvenlidir — taşınmış satır kaynakta yoktur.
* **Kapsam:** yalnız ``dispatch_armed = FALSE``, ``status IN ('PENDING',
  'RETRY_SCHEDULED')`` ve saklama eşiğini geçmiş satırlar. ``PROCESSING``
  hiçbir koşulda arşivlenmez.
* **Geri taşıma yolu yoktur.** Arşiv tablosu trigger ile UPDATE/DELETE'e
  kapalıdır; yeniden gönderilecek bir mesaj için yeni satır üretilir.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import DateTime, bindparam, text
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..config import settings


DEFAULT_BATCH_SIZE = 500

# §2.9: politika ayarı denetim izini pratikte kapatmak için kullanılamasın diye
# alt sınır uygulanır.
MIN_RETENTION_DAYS = 30
DEFAULT_RETENTION_DAYS = 90

ARCHIVE_REASON = "Kural kapatıldı: saklama süresi doldu"

# ``notifications`` ve ``notifications_archive`` ortak kolonları. Arşiv üçlüsü
# ayrıca yazılır. Liste elle tutulur ki şemaya eklenen bir kolon sessizce
# arşive taşınmadan kalmasın — test bu listeyi şema ile karşılaştırır.
_COMMON_COLUMNS = (
    "id", "company_id", "type", "channel", "recipient", "template", "payload",
    "dedupe_key", "status", "external_id", "last_error", "attempt_count",
    "last_attempt_at", "locked_until", "lock_token", "created_at", "updated_at",
    "next_attempt_at", "scheduled_for", "max_attempts", "last_error_code",
    "created_by", "approved_by", "approved_at", "approval_mode",
    "dispatch_armed", "disarmed_at", "rule_id", "message_class", "template_id",
    "template_version", "content_hash", "consent_id", "consent_version",
    "consent_checked_at", "consent_decision", "consent_decision_reason",
    "iys_status", "iys_checked_at", "iys_reference", "iys_source",
    "compliance_attempt_count",
)


class RetentionPolicyError(ValueError):
    """Saklama süresi alt sınırın altına indirilemez."""


def resolve_retention_days(value: int | None = None) -> int:
    """Şirket politikası parametresi + alt sınır kontrolü.

    Alt sınır **hem burada hem yapılandırma katmanında** uygulanır; tek bir
    katmanın atlanması saklama süresini sıfıra indiremez.
    """
    raw = value if value is not None else getattr(
        settings, "notification_disarmed_retention_days", DEFAULT_RETENTION_DAYS
    )
    try:
        days = int(raw)
    except (TypeError, ValueError) as exc:
        raise RetentionPolicyError("Saklama süresi sayı olmalıdır") from exc
    if days < MIN_RETENTION_DAYS:
        raise RetentionPolicyError(
            f"Saklama süresi {MIN_RETENTION_DAYS} günden kısa olamaz"
        )
    return days


def _selection_sql(limit_clause: str = "") -> str:
    """Arşivlenecek satırların seçim yüklemi.

    Üç koşul da SQL'dedir: silahsız + eşik + ``PROCESSING`` değil.
    """
    return f"""
        SELECT id FROM notifications
         WHERE company_id = :cid
           AND dispatch_armed = :disarmed
           AND status IN ('PENDING', 'RETRY_SCHEDULED')
           AND status <> 'PROCESSING'
           AND disarmed_at IS NOT NULL
           AND disarmed_at <= :threshold
         ORDER BY id
         {limit_clause}
    """


def archive_disarmed_notifications(
    db: Session,
    *,
    company_id: int,
    retention_days: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    archived_by: str = "SYSTEM",
) -> int:
    """Tek şirket için saklama süresi dolmuş disarmed satırları arşivler.

    Dönen değer taşınan satır sayısıdır. Çağıran transaction'ı yönetir;
    fonksiyon her parti için ``commit`` eder, böylece uzun bir iş yarıda
    kesilse bile tamamlanan partiler kalıcıdır ve yeniden çalıştırma
    kaldığı yerden devam eder.
    """
    days = resolve_retention_days(retention_days)
    threshold = utcnow() - timedelta(days=days)
    moved = 0

    while True:
        ids = [
            int(row[0])
            for row in db.execute(
                text(_selection_sql("LIMIT :limit")).bindparams(
                    bindparam("threshold", type_=DateTime(timezone=True))
                ),
                {
                    "cid": company_id,
                    "disarmed": False,
                    "threshold": threshold,
                    "limit": batch_size,
                },
            ).all()
        ]
        if not ids:
            break

        columns = ", ".join(_COMMON_COLUMNS)
        placeholders = ", ".join(f":id_{index}" for index in range(len(ids)))
        params: dict[str, Any] = {
            f"id_{index}": value for index, value in enumerate(ids)
        }
        params.update(
            {
                "cid": company_id,
                "now": utcnow(),
                "archived_by": archived_by,
                "reason": ARCHIVE_REASON,
            }
        )

        # 1) Kopya kalıcı olarak yazılır.
        db.execute(
            text(
                f"""INSERT INTO notifications_archive
                    ({columns}, archived_at, archived_by, archive_reason)
                    SELECT {columns}, :now, :archived_by, :reason
                      FROM notifications
                     WHERE company_id = :cid AND id IN ({placeholders})"""
            ).bindparams(bindparam("now", type_=DateTime(timezone=True))),
            params,
        )
        # 2) Ancak ondan sonra kaynak kaldırılır — aynı transaction içinde.
        deleted = db.execute(
            text(
                f"""DELETE FROM notifications
                     WHERE company_id = :cid AND id IN ({placeholders})"""
            ),
            params,
        )
        if int(deleted.rowcount or 0) != len(ids):
            # Beklenen satır sayısı taşınmadıysa parti geri alınır; yarım
            # taşınmış bir parti bırakmaktansa hiç taşımamak yeğdir.
            db.rollback()
            raise RuntimeError(
                "Arşivleme tutarsızlığı: taşınan ve silinen satır sayısı farklı"
            )
        db.commit()
        moved += len(ids)

        if len(ids) < batch_size:
            break

    return moved


def archive_all_companies(
    db: Session,
    *,
    retention_days: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[int, int]:
    """Her şirket için ayrı ayrı arşivler; şirketler arası toplu iş yapılmaz."""
    company_ids = [
        int(row[0])
        for row in db.execute(
            text("SELECT DISTINCT company_id FROM notifications ORDER BY company_id")
        ).all()
    ]
    return {
        company_id: archive_disarmed_notifications(
            db,
            company_id=company_id,
            retention_days=retention_days,
            batch_size=batch_size,
        )
        for company_id in company_ids
    }
