#!/usr/bin/env python3
"""Export the FastAPI OpenAPI schema deterministically.

``app.main`` migrates the schema and seeds bootstrap data at import time, so a
plain import against the developer database would have side effects. This
script always imports the app against a throwaway SQLite file unless the
caller explicitly overrides DATABASE_URL, making the export safe to run
anywhere (CI drift job, local type generation).

Kullanım:
    python export_openapi.py -o openapi.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", help="Yazılacak dosya (varsayılan stdout)")
    args = parser.parse_args()

    if "OPENAPI_EXPORT_KEEP_DATABASE_URL" not in os.environ:
        tmpdir = tempfile.mkdtemp(prefix="openapi-export-")
        db_path = Path(tmpdir) / "export.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"

    from app.main import app

    schema = app.openapi()
    # Deterministic output: stable key order and a trailing newline so the CI
    # drift check (`git diff --exit-code`) never flags formatting noise.
    payload = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
