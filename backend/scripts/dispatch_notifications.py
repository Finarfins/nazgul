"""Drain the armed notification outbox from cron/systemd.

Bu komut **onay üretmez**: yalnız §2.4 lease yükleminin beş koşulunu zaten
sağlayan satırları gönderir. Onay bekleyen ya da durdurulmuş bir satır bu
betikle asla gönderilemez.

Kayıt akışı commit sonrası tek bir fırsatçı deneme yapar; bu betik o denemenin
emniyet ağıdır: başarısız/atlanmış satırları backoff planına göre devralır.

Örnek (dakikada bir):
    * * * * * python backend/scripts/dispatch_notifications.py --limit 50
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import utcnow  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.notifications.service import (  # noqa: E402
    NotificationBusyError,
    NotificationNotFoundError,
    NotificationNotRetryableError,
    cancel_expired_verification_notifications,
    claimable_notification_ids,
    send_notification,
    stuck_processing_count,
)
from app.tenancy import companies  # noqa: E402

DEFAULT_LIMIT = 50


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", type=int)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.limit < 1:
        print("HATA: --limit en az 1 olmalıdır", file=sys.stderr)
        return 2

    now = utcnow()
    counters: dict[str, int] = {}
    with SessionLocal() as db:
        company_ids = _company_ids(db, args.company)
        expired = sum(
            cancel_expired_verification_notifications(
                db, now=now, company_id=company_id
            )
            for company_id in company_ids
        )
        if args.dry_run:
            db.rollback()
        else:
            db.commit()

        pending: list[tuple[int, int]] = []
        stuck = 0
        for company_id in company_ids:
            stuck += stuck_processing_count(db, now=now, company_id=company_id)
            remaining = args.limit - len(pending)
            if remaining > 0:
                pending.extend(
                    claimable_notification_ids(
                        db, limit=remaining, now=now, company_id=company_id
                    )
                )

        if args.dry_run:
            print(f"süresi dolmuş (iptal edilecek): {expired}")
            print(f"gönderilecek: {len(pending)} satır (dry-run)")
            _report_stuck(stuck)
            return 0

        for company_id, notification_id in pending:
            try:
                result = send_notification(
                    db, company_id=company_id, notification_id=notification_id
                )
                key = result.status
            except NotificationBusyError:
                # Başka bir drenaj ya da operatör aynı satırı almış; lease
                # sahibi işini bitirir, burada yeniden denemek çift claim olur.
                key = "BUSY"
            except NotificationNotRetryableError:
                key = "SKIPPED"
            except NotificationNotFoundError:
                key = "MISSING"
            counters[key] = counters.get(key, 0) + 1

    print(f"süresi dolmuş (iptal): {expired}")
    print(f"denenen: {len(pending)} satır")
    for status in sorted(counters):
        print(f"  {status}: {counters[status]}")
    _report_stuck(stuck)
    return 0


def _company_ids(db, requested_company: int | None) -> list[int]:
    if requested_company is not None:
        return [requested_company]
    return [
        int(company_id)
        for company_id in db.execute(
            select(companies.c.id)
            .where(companies.c.is_active.is_(True))
            .order_by(companies.c.id)
        ).scalars()
    ]


def _report_stuck(count: int) -> None:
    """AK-6: lease'i dolmuş PROCESSING satırları görünür kılınır.

    SMTP idempotent olmadığı için bu satırlar otomatik geri alınmaz — geri
    almak aynı maili iki kez göndermek demektir. Kurtarma bilinçli olarak
    operatör işidir; sessizce beklemesin diye sayı stderr'e yazılır.
    """
    if count:
        print(
            f"UYARI: lease süresi dolmuş {count} PROCESSING satır var; "
            "elle inceleme gerekiyor (otomatik geri alma çift gönderim olurdu)",
            file=sys.stderr,
        )


if __name__ == "__main__":
    raise SystemExit(main())
