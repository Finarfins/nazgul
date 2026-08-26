from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine

from app.reconciliation import (
    capture_numeric_snapshot,
    compare_numeric_snapshots,
    read_snapshot,
    write_snapshot,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="REAL/Float -> NUMERIC migration öncesi ve sonrası finansal mutabakat aracı"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot = sub.add_parser("snapshot", help="Veritabanının sayısal mutabakat görüntüsünü al")
    snapshot.add_argument("--database-url", required=True)
    snapshot.add_argument("--output", required=True, type=Path)

    compare = sub.add_parser("compare", help="İki mutabakat görüntüsünü karşılaştır")
    compare.add_argument("--before", required=True, type=Path)
    compare.add_argument("--after", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "snapshot":
        engine = create_engine(args.database_url, pool_pre_ping=True)
        try:
            payload = capture_numeric_snapshot(engine)
            write_snapshot(payload, args.output)
        finally:
            engine.dispose()
        print(
            json.dumps(
                {
                    "status": "ok",
                    "output": str(args.output),
                    "columns": len(payload["columns"]),
                    "sha256": payload["sha256"],
                },
                ensure_ascii=False,
            )
        )
        return 0

    before = read_snapshot(args.before)
    after = read_snapshot(args.after)
    differences = compare_numeric_snapshots(before, after)
    if differences:
        print(
            json.dumps(
                {
                    "status": "mismatch",
                    "differences": [difference.__dict__ for difference in differences],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps({"status": "ok", "message": "Finansal mutabakat eşleşti"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
