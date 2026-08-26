from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


def append_restore_journal(
    data_dir: str | Path,
    event: str,
    *,
    operation_id: str,
    owner: str,
    details: Mapping[str, object] | None = None,
) -> None:
    path = Path(data_dir).expanduser().resolve() / "restore_journal.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "operation_id": operation_id,
        "owner": owner,
        "details": dict(details or {}),
    }
    line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, line)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def safe_append_restore_journal(*args, **kwargs) -> bool:
    """Journal failures are reported but never replace the primary outcome."""
    try:
        append_restore_journal(*args, **kwargs)
        return True
    except Exception as exc:
        print(f"restore journal yazılamadı: {exc}", file=sys.stderr)
        return False
