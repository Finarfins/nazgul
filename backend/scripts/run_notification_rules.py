"""Evaluate enabled notification rules from cron/systemd.

This command only creates approval-waiting rows. It never dispatches messages.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app.notifications.rules import RuleError, run_enabled_rules  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            results = run_enabled_rules(db, company_id=args.company)
            if args.dry_run:
                db.rollback()
            else:
                db.commit()
        except RuleError as exc:
            db.rollback()
            print(f"HATA: {exc}", file=sys.stderr)
            return 2
    for rule_id, count in results.items():
        print(f"kural {rule_id}: {count} onay bekleyen satır")
    print(f"TOPLAM: {sum(results.values())} satır{' (dry-run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
