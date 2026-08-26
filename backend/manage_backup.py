from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.database_backup import (
    BackupError,
    create_postgresql_backup,
    create_sqlite_backup,
    restore_sqlite_backup,
    verify_sqlite_backup,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Yerel Hesap güvenli veritabanı yedekleme aracı")
    sub = root.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--database-url", required=True)
    create.add_argument("--destination", required=True, type=Path)

    verify = sub.add_parser("verify-sqlite")
    verify.add_argument("--backup", required=True, type=Path)
    verify.add_argument("--sha256")

    restore = sub.add_parser("restore-sqlite")
    restore.add_argument("--backup", required=True, type=Path)
    restore.add_argument("--target", required=True, type=Path)
    restore.add_argument("--sha256")
    restore.add_argument("--overwrite", action="store_true")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "create":
            if args.database_url.startswith("sqlite"):
                artifact = create_sqlite_backup(args.database_url, args.destination)
                payload = {
                    "status": "ok",
                    "backup": str(artifact.backup_path),
                    "manifest": str(artifact.manifest_path),
                    "sha256": artifact.sha256,
                    "size_bytes": artifact.size_bytes,
                }
            else:
                output = args.destination
                if output.is_dir() or not output.suffix:
                    output.mkdir(parents=True, exist_ok=True)
                    output = output / "yerel_hesap_postgresql.dump"
                path = create_postgresql_backup(args.database_url, output)
                payload = {"status": "ok", "backup": str(path)}
        elif args.command == "verify-sqlite":
            artifact = verify_sqlite_backup(args.backup, args.sha256)
            payload = {
                "status": "ok",
                "backup": str(artifact.backup_path),
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
            }
        else:
            safety = restore_sqlite_backup(
                args.backup,
                args.target,
                expected_sha256=args.sha256,
                overwrite=args.overwrite,
            )
            payload = {
                "status": "ok",
                "target": str(args.target),
                "pre_restore_backup": str(safety) if safety else None,
            }
    except BackupError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
