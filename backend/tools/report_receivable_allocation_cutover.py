#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.allocation_reconciliation import build_reconciliation_report  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, ".2f")
    raise TypeError(f"JSON'a çevrilemeyen değer: {type(value).__name__}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tahsis cutover mutabakatını salt-okunur JSON olarak üretir."
    )
    parser.add_argument("--company-id", required=True, type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with SessionLocal() as db:
        rows = [
            row.as_dict()
            for row in build_reconciliation_report(db, args.company_id)
        ]
        db.rollback()
    payload = json.dumps(
        {"company_id": args.company_id, "rows": rows},
        ensure_ascii=False,
        indent=2,
        default=_json_default,
    )
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
