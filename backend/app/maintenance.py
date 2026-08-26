from __future__ import annotations

import json
import hashlib
import os
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from sqlalchemy import Engine, create_engine, text

from .backup_errors import BackupError, MaintenanceActiveError

ADVISORY_LOCK_KEY = 0x53554E475552424B
MAINTENANCE_LOCK_KEY = ADVISORY_LOCK_KEY + 1
WRITE_LOCK_MODES = (
    "RowExclusiveLock",
    "ShareRowExclusiveLock",
    "ExclusiveLock",
    "AccessExclusiveLock",
)
_sqlite_runtime_stream = None


def backup_directory(data_dir: str | Path) -> Path:
    path = Path(data_dir).expanduser().resolve() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _lock_stream(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    if path.stat().st_size == 0:
        stream.write(b"\0")
        stream.flush()
    return stream


def _try_file_lock(stream, *, shared: bool = False) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            mode = msvcrt.LK_NBRLCK if shared else msvcrt.LK_NBLCK
            msvcrt.locking(stream.fileno(), mode, 1)
        else:
            import fcntl

            mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            fcntl.flock(stream.fileno(), mode | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, OSError):
        return False


def _unlock_file(stream) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def hold_sqlite_runtime_lock(database_url: str, data_dir: str | Path) -> None:
    """Keep a sentinel locked for the lifetime of an HTTP SQLite process."""
    global _sqlite_runtime_stream
    if not database_url.startswith("sqlite") or _sqlite_runtime_stream is not None:
        return
    identity = hashlib.sha256(database_url.encode("utf-8")).hexdigest()[:16]
    stream = _lock_stream(
        backup_directory(data_dir) / f".application-{identity}.lock"
    )
    if not _try_file_lock(stream, shared=True):
        stream.close()
        raise RuntimeError("Başka bir SQLite uygulama süreci çalışıyor")
    _sqlite_runtime_stream = stream


def assert_sqlite_application_stopped(
    database_url: str, data_dir: str | Path
) -> None:
    identity = hashlib.sha256(database_url.encode("utf-8")).hexdigest()[:16]
    stream = _lock_stream(
        backup_directory(data_dir) / f".application-{identity}.lock"
    )
    try:
        if not _try_file_lock(stream):
            raise BackupError(
                "SQLite geri yükleme için uygulama süreçlerinin tamamı durdurulmalıdır"
            )
        _unlock_file(stream)
    finally:
        stream.close()


def _write_file_metadata(directory: Path, payload: dict | None) -> None:
    path = directory / ".operation.json"
    if payload is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _set_operation_row(connection, payload: dict | None, *, maintenance: bool = False) -> None:
    values = payload or {}
    connection.execute(
        text(
            """UPDATE platform_maintenance
               SET active=:active, operation_id=:operation_id,
                   operation_kind=:operation_kind, owner=:owner,
                   started_at=:started_at, heartbeat_at=:heartbeat_at WHERE id=1"""
        ),
        {
            "active": maintenance,
            "operation_id": values.get("operation_id"),
            "operation_kind": values.get("operation_kind"),
            "owner": values.get("owner"),
            "started_at": (
                datetime.fromisoformat(values["started_at"])
                if isinstance(values.get("started_at"), str)
                else values.get("started_at")
            ),
            "heartbeat_at": datetime.now(timezone.utc) if maintenance else None,
        },
    )
    connection.commit()


@contextmanager
def operation_lock(
    database_url: str,
    data_dir: str | Path,
    operation_kind: str,
    *,
    timeout_seconds: int = 0,
) -> Iterator[dict]:
    """Cross-process mutex shared by create, retention and restore."""
    started = datetime.now(timezone.utc)
    payload = {
        "operation_id": uuid4().hex,
        "operation_kind": operation_kind,
        "owner": _owner(),
        "started_at": started.isoformat(),
    }
    deadline = time.monotonic() + timeout_seconds
    if database_url.startswith("sqlite"):
        directory = backup_directory(data_dir)
        stream = _lock_stream(directory / ".operation.lock")
        while not _try_file_lock(stream):
            if time.monotonic() >= deadline:
                stream.close()
                raise BackupError("Başka bir yedek/geri yükleme işlemi sürüyor")
            time.sleep(0.1)
        _write_file_metadata(directory, payload)
        try:
            yield payload
        finally:
            _write_file_metadata(directory, None)
            _unlock_file(stream)
            stream.close()
        return

    engine = create_engine(database_url)
    connection = engine.connect()
    try:
        while not bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
            ).scalar_one()
        ):
            if time.monotonic() >= deadline:
                raise BackupError("Başka bir yedek/geri yükleme işlemi sürüyor")
            time.sleep(0.1)
        _set_operation_row(connection, payload)
        try:
            payload["_connection"] = connection
            yield payload
        finally:
            if payload.get("_retain_maintenance"):
                # The DB row intentionally remains fail-closed. The session lock
                # is released when this process/request ends; recovery_required
                # rows still require an explicit operator clear.
                connection.close()
            else:
                try:
                    try:
                        _set_operation_row(connection, None)
                    except Exception:
                        connection.rollback()
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
                    )
                finally:
                    connection.close()
    finally:
        engine.dispose()


def current_operation(database_url: str, data_dir: str | Path) -> dict | None:
    if database_url.startswith("sqlite"):
        directory = backup_directory(data_dir)
        path = directory / ".operation.json"
        probe = _lock_stream(directory / ".operation.lock")
        try:
            if _try_file_lock(probe):
                _unlock_file(probe)
                path.unlink(missing_ok=True)
                return None
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                return None
        finally:
            probe.close()
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT operation_id, operation_kind, owner, started_at, heartbeat_at
                       FROM platform_maintenance
                       WHERE id=1 AND operation_id IS NOT NULL"""
                )
            ).mappings().first()
            if not row:
                return None
            return {
                **dict(row),
                "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                "heartbeat_at": (
                    row["heartbeat_at"].isoformat() if row["heartbeat_at"] else None
                ),
            }
    finally:
        engine.dispose()


def set_maintenance(engine: Engine, payload: dict, active: bool) -> None:
    lock_connection = payload.get("_connection")
    if engine.dialect.name == "postgresql" and lock_connection is not None:
        locked = bool(payload.get("_maintenance_locked"))
        if active and not locked:
            lock_connection.execute(
                text("SELECT pg_advisory_lock(:key)"),
                {"key": MAINTENANCE_LOCK_KEY},
            )
            payload["_maintenance_locked"] = True
        elif not active and locked:
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": MAINTENANCE_LOCK_KEY},
            )
            payload["_maintenance_locked"] = False
    try:
        with engine.connect() as connection:
            _set_operation_row(connection, payload if active else None, maintenance=active)
    except Exception:
        if active:
            raise


def mark_recovery_required(engine: Engine, payload: dict) -> None:
    """Keep fail-closed state after both restore and rollback are unverified."""
    operation_id = payload["operation_id"]
    with engine.begin() as connection:
        connection.execute(
            text(
                """UPDATE platform_maintenance
                   SET active=true, operation_kind='backup.restore.recovery_required',
                       heartbeat_at=:heartbeat
                   WHERE id=1 AND operation_id=:operation_id"""
            ),
            {"operation_id": operation_id, "heartbeat": datetime.now(timezone.utc)},
        )
    payload["_retain_maintenance"] = True


def _recover_stale_row(connection, row) -> dict | None:
    operation_id = row["operation_id"]
    if not operation_id or row["operation_kind"] == "backup.restore.recovery_required":
        return None
    acquired = bool(
        connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": MAINTENANCE_LOCK_KEY},
        ).scalar_one()
    )
    if not acquired:
        return None
    try:
        result = connection.execute(
            text(
                """UPDATE platform_maintenance
                   SET active=false, operation_id=NULL, operation_kind=NULL,
                       owner=NULL, started_at=NULL, heartbeat_at=NULL
                   WHERE id=1 AND active=true AND operation_id=:operation_id"""
            ),
            {"operation_id": operation_id},
        )
        if result.rowcount != 1:
            return None
        connection.commit()
        return {
            "operation_id": operation_id,
            "operation_kind": row["operation_kind"],
            "owner": row["owner"],
            "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        }
    finally:
        connection.execute(
            text("SELECT pg_advisory_unlock(:key)"),
            {"key": MAINTENANCE_LOCK_KEY},
        )


def _record_recovery(recovered: dict) -> None:
    from .activity_log import log_activity
    from .config import settings
    from .db import SessionLocal
    from .restore_journal import safe_append_restore_journal

    safe_append_restore_journal(
        settings.sungur_data_dir,
        "backup.maintenance_recovered",
        operation_id=str(recovered["operation_id"]),
        owner="automatic-recovery",
        details=recovered,
    )
    try:
        with SessionLocal.begin() as db:
            company_id = db.execute(
                text("SELECT id FROM companies ORDER BY id LIMIT 1")
            ).scalar_one()
            log_activity(
                db, int(company_id), None, "backup.maintenance_recovered",
                "backup", None, "Sahipsiz bakım durumu otomatik temizlendi",
                recovered,
            )
    except Exception:
        # Journal is authoritative when the restored DB cannot accept activity.
        pass


def maintenance_status(engine: Engine, *, recover_stale: bool = True) -> dict:
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT active, operation_id, operation_kind, owner,
                              started_at, heartbeat_at
                       FROM platform_maintenance WHERE id=1"""
                )
            ).mappings().one()
            if not row["active"]:
                return {"active": False, "recovered": None}
            if engine.dialect.name != "postgresql" or not recover_stale:
                return {"active": True, "recovered": None, **dict(row)}
            recovered = _recover_stale_row(connection, row)
            if recovered:
                _record_recovery(recovered)
                return {"active": False, "recovered": recovered}
            return {"active": True, "recovered": None, **dict(row)}
    except Exception:
        # During pg_restore the table can be briefly unavailable. Writes must
        # fail closed, never slip through that interval.
        return {"active": True, "recovered": None}


def is_maintenance_active(engine: Engine) -> bool:
    return bool(maintenance_status(engine)["active"])


def clear_maintenance(
    engine: Engine, operation_id: str, *, force: bool = False
) -> dict:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """SELECT active, operation_id, operation_kind, owner,
                          started_at, heartbeat_at
                   FROM platform_maintenance WHERE id=1"""
            )
        ).mappings().one()
        if not row["active"] or row["operation_id"] != operation_id:
            raise BackupError("Bakım operation ID eşleşmiyor veya bakım aktif değil")
        if not force:
            if row["operation_kind"] == "backup.restore.recovery_required":
                stale = True
            else:
                acquired = bool(
                    connection.execute(
                        text("SELECT pg_try_advisory_lock(:key)"),
                        {"key": MAINTENANCE_LOCK_KEY},
                    ).scalar_one()
                )
                if not acquired:
                    raise BackupError("Bakım sahibi hâlâ aktif; temizleme reddedildi")
                connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": MAINTENANCE_LOCK_KEY},
                )
                stale = True
            if not stale:
                raise BackupError("Bakım durumu stale değil")
        result = connection.execute(
            text(
                """UPDATE platform_maintenance
                   SET active=false, operation_id=NULL, operation_kind=NULL,
                       owner=NULL, started_at=NULL, heartbeat_at=NULL
                   WHERE id=1 AND operation_id=:operation_id"""
            ),
            {"operation_id": operation_id},
        )
        connection.commit()
        if result.rowcount != 1:
            raise BackupError("Bakım durumu yarış nedeniyle temizlenemedi")
        return {"cleared": True, "operation_id": operation_id, "forced": force}


def acquire_write_transaction_lock(connection) -> None:
    """Acquire the PG shared maintenance lock inside the caller's transaction."""
    if connection.dialect.name != "postgresql":
        return
    acquired = bool(
        connection.execute(
            text("SELECT pg_try_advisory_xact_lock_shared(:key)"),
            {"key": MAINTENANCE_LOCK_KEY},
        ).scalar_one()
    )
    if not acquired:
        raise MaintenanceActiveError(
            "Platform bakım modunda; yazma işlemleri geçici olarak durduruldu"
        )


def drain_postgresql_writes(engine: Engine, timeout_seconds: int) -> None:
    if engine.dialect.name != "postgresql":
        return
    deadline = time.monotonic() + timeout_seconds
    modes = ", ".join(f"'{mode}'" for mode in WRITE_LOCK_MODES)
    query = text(
        f"""SELECT COUNT(DISTINCT l.pid)
            FROM pg_locks l
            JOIN pg_stat_activity a ON a.pid=l.pid
            WHERE a.datname=current_database()
              AND l.pid <> pg_backend_pid()
              AND l.granted
              AND l.mode IN ({modes})"""
    )
    while True:
        with engine.connect() as connection:
            active = int(connection.execute(query).scalar_one())
        if active == 0:
            return
        if time.monotonic() >= deadline:
            raise BackupError(
                f"Yazma transaction'ları {timeout_seconds} saniyede boşalmadı; restore iptal edildi"
            )
        time.sleep(0.2)


def assert_writes_allowed(engine: Engine) -> None:
    if is_maintenance_active(engine):
        raise BackupError("Platform bakım modunda; yazma işlemleri geçici olarak durduruldu")
