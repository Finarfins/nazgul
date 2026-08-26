"""§2.9 arşiv işi — dış cron/systemd timer'dan çağrılan yönetim komutu.

Uygulama içi zamanlayıcı **kullanılmaz**: çok işçili kurulumda gereksiz
çekişme yaratır ve işin ne zaman çalıştığı operatör için görünmez olur.

Kullanım::

    python -m scripts.archive_disarmed_notifications --dry-run
    python -m scripts.archive_disarmed_notifications --company 1
    python -m scripts.archive_disarmed_notifications --retention-days 120

Saklama süresi dolan disarmed satırlar SİLİNMEZ; ``notifications_archive``
tablosuna taşınır. Alt sınır (30 gün) hem yapılandırma hem servis katmanında
uygulanır; komut alt sınırı aşamaz.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.notifications.archive import (  # noqa: E402
    DEFAULT_BATCH_SIZE,
    RetentionPolicyError,
    archive_all_companies,
    archive_disarmed_notifications,
    resolve_retention_days,
)


def _preview(db, retention_days: int) -> list[tuple[int, int]]:
    """Taşınacak satır sayısını şirket bazında sayar; hiçbir şey yazmaz."""
    from datetime import timedelta

    from app.auth import utcnow

    threshold = utcnow() - timedelta(days=retention_days)
    rows = db.execute(
        text(
            """SELECT company_id, COUNT(*) AS adet
                 FROM notifications
                WHERE dispatch_armed = :disarmed
                  AND status IN ('PENDING', 'RETRY_SCHEDULED')
                  AND disarmed_at IS NOT NULL
                  AND disarmed_at <= :threshold
                GROUP BY company_id
                ORDER BY company_id"""
        ),
        {"disarmed": False, "threshold": threshold},
    ).all()
    return [(int(row[0]), int(row[1])) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", type=int, default=None, help="Tek şirket")
    parser.add_argument("--retention-days", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Yalnız sayar, hiçbir satır taşımaz",
    )
    args = parser.parse_args()

    try:
        retention_days = resolve_retention_days(args.retention_days)
    except RetentionPolicyError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        if args.dry_run:
            preview = _preview(db, retention_days)
            if not preview:
                print(f"Taşınacak satır yok (saklama {retention_days} gün).")
                return 0
            for company_id, count in preview:
                print(f"  şirket {company_id}: {count} satır arşivlenecek")
            print(f"TOPLAM: {sum(count for _, count in preview)} satır (dry-run)")
            return 0

        if args.company is not None:
            moved = archive_disarmed_notifications(
                db,
                company_id=args.company,
                retention_days=retention_days,
                batch_size=args.batch_size,
            )
            print(f"şirket {args.company}: {moved} satır arşivlendi")
            return 0

        results = archive_all_companies(
            db, retention_days=retention_days, batch_size=args.batch_size
        )
        for company_id, moved in sorted(results.items()):
            if moved:
                print(f"  şirket {company_id}: {moved} satır arşivlendi")
        print(f"TOPLAM: {sum(results.values())} satır arşivlendi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
