"""Backfill service-fee receivables for historical completed work orders.

Safety contract:
- dry-run unless ``--apply`` is supplied;
- never rewrites a work order that already has any service-fee revision history;
- uses the same invoice-authoritative domain service as the live COMPLETED hook;
- does not adjust customer opening balances.

Run only after a human has checked whether ``customers.opening_balance`` already
contains historical service debt.
"""

from __future__ import annotations

import argparse

from sqlalchemy import text

from app.db import SessionLocal
from app.service_receivable_engine import reconcile_service_receivable


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actor-id", type=int, required=True)
    parser.add_argument("--company-id", type=int)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the backfill. Without this flag the script only reports candidates.",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    conditions = [
        "w.status IN ('COMPLETED','DELIVERED')",
        "w.completed_at IS NOT NULL",
        """NOT EXISTS (
            SELECT 1 FROM receivable_charge_documents d
            WHERE d.company_id=w.company_id AND d.work_order_id=w.id
              AND d.charge_type='service_fee'
        )""",
    ]
    params: dict[str, object] = {}
    if args.company_id is not None:
        conditions.append("w.company_id=:company_id")
        params["company_id"] = args.company_id

    with SessionLocal() as db:
        candidates = db.execute(
            text(
                """SELECT w.company_id,w.id
                FROM work_orders w
                WHERE """
                + " AND ".join(conditions)
                + " ORDER BY w.company_id,w.id"
            ),
            params,
        ).mappings().all()
        print(f"candidates={len(candidates)} apply={args.apply}")
        if not args.apply:
            return 0

        written = skipped = failed = 0
        for candidate in candidates:
            try:
                result = reconcile_service_receivable(
                    db,
                    int(candidate["company_id"]),
                    int(candidate["id"]),
                    actor_id=args.actor_id,
                )
                if result is None:
                    skipped += 1
                else:
                    written += 1
                db.commit()
            except Exception as exc:
                db.rollback()
                failed += 1
                print(
                    "failed "
                    f"company_id={candidate['company_id']} "
                    f"work_order_id={candidate['id']} "
                    f"error={type(exc).__name__}:{exc}"
                )
        print(f"written={written} skipped={skipped} failed={failed}")
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
