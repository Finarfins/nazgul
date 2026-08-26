from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sys

import sqlalchemy as sa

from .activity_log import log_activity
from .backup_errors import BackupError
from .config import settings
from .database_backup import (
    apply_backup_with_rollback,
    RestoreExecutionError,
    create_platform_backup,
    resolve_backup,
    verify_platform_backup,
)
from .db import engine
from .maintenance import (
    assert_sqlite_application_stopped,
    assert_writes_allowed,
    clear_maintenance,
    drain_postgresql_writes,
    mark_recovery_required,
    operation_lock,
    set_maintenance,
)
from .restore_journal import safe_append_restore_journal


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Sungur platform veritabanı yedeği")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("create")
    restore = commands.add_parser("restore")
    restore.add_argument("name")
    maintenance_clear = commands.add_parser("maintenance-clear")
    maintenance_clear.add_argument("--operation-id", required=True)
    maintenance_clear.add_argument(
        "--force", action="store_true",
        help="TEHLİKELİ: canlı bakım sahibini doğrulamadan temizler",
    )
    maintenance_clear.add_argument(
        "--reason",
        help="--force kullanıldığında zorunlu işletim gerekçesi",
    )
    return root


def _journal(operation: dict, event: str, details: dict) -> None:
    safe_append_restore_journal(
        settings.sungur_data_dir, event,
        operation_id=operation["operation_id"], owner=operation["owner"], details=details,
    )


def _create() -> dict:
    assert_writes_allowed(engine)
    with operation_lock(
        settings.database_url, settings.sungur_data_dir, "backup.create",
        timeout_seconds=settings.backup_lock_timeout_seconds,
    ):
        return create_platform_backup(
            settings.database_url, settings.sungur_data_dir,
            retention_count=settings.backup_retention_count,
        )


def _restore(name: str) -> dict:
    if engine.dialect.name == "sqlite":
        assert_sqlite_application_stopped(
            settings.database_url, settings.sungur_data_dir
        )
    pre_backup = None
    database_state = "unchanged"
    rollback_command = None
    with operation_lock(
        settings.database_url, settings.sungur_data_dir, "backup.restore",
        timeout_seconds=settings.backup_lock_timeout_seconds,
    ) as operation:
        if engine.dialect.name == "postgresql":
            set_maintenance(engine, operation, True)
        try:
            drain_postgresql_writes(engine, settings.restore_drain_timeout_seconds)
            selected_path = resolve_backup(settings.sungur_data_dir, name)
            selected = verify_platform_backup(
                settings.database_url, selected_path, validate_manifest=True
            )
            pre_backup = create_platform_backup(
                settings.database_url, settings.sungur_data_dir,
                retention_count=settings.backup_retention_count + 1,
            )
            pre_path = resolve_backup(settings.sungur_data_dir, str(pre_backup["name"]))
            verify_platform_backup(settings.database_url, pre_path)
            rollback_command = f"python -m app.backup_cli restore {pre_backup['name']}"
            details = {
                "backup": name, "pre_restore_backup": pre_backup["name"],
                "rollback_command": rollback_command,
            }
            _journal(operation, "backup.restore_started", details)
            engine.dispose()
            after_apply = (
                (lambda: set_maintenance(engine, operation, True))
                if engine.dialect.name == "postgresql"
                else None
            )
            rollback_details = dict(details)
            try:
                counts = apply_backup_with_rollback(
                    settings.database_url, selected_path, pre_path,
                    after_apply=after_apply,
                    on_rollback_started=lambda exc: (
                        rollback_details.update({"cause": str(exc)}),
                        _journal(operation, "backup.restore_rollback_started", rollback_details),
                    ),
                    on_rollback_completed=lambda: _journal(
                        operation, "backup.restore_rollback_completed", rollback_details
                    ),
                    on_rollback_failed=lambda exc: (
                        rollback_details.update({"rollback_error": str(exc)}),
                        _journal(operation, "backup.restore_rollback_failed", rollback_details),
                    ),
                )
            except RestoreExecutionError as exc:
                database_state = exc.database_state
                if database_state == "rollback_failed" and engine.dialect.name == "postgresql":
                    mark_recovery_required(engine, operation)
                raise
            database_state = "restored"
            result = {
                "status": "completed", "restored_backup": selected,
                "pre_restore_backup": pre_backup, "table_counts": counts,
                "database_state": database_state, "rollback_command": rollback_command,
            }
            _journal(operation, "backup.restore_completed", result)
            return result
        except Exception as exc:
            rollback_error = (
                exc.rollback_error if isinstance(exc, RestoreExecutionError) else None
            )
            failure = {
                "backup": name, "error": str(exc),
                "pre_restore_backup": pre_backup["name"] if pre_backup else None,
                "rollback_command": rollback_command, "database_state": database_state,
                "rollback_error": rollback_error,
                "recovery_command": (
                    f"python -m app.backup_cli maintenance-clear --operation-id "
                    f"{operation['operation_id']}"
                    if database_state == "rollback_failed" else None
                ),
                "operator_action": (
                    "Veritabanını inceleyin; doğruladıktan sonra maintenance-clear çalıştırın"
                    if database_state == "rollback_failed" else None
                ),
            }
            _journal(operation, "backup.restore_failed", failure)
            setattr(exc, "details", failure)
            raise
        finally:
            if engine.dialect.name == "postgresql" and database_state != "rollback_failed":
                set_maintenance(engine, operation, False)


def _caller_identity() -> dict[str, object]:
    identity: dict[str, object] = {
        "os_user": getpass.getuser(),
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
    }
    operator_id = os.environ.get("SUNGUR_OPERATOR_ID", "").strip()
    if operator_id:
        identity["operator_id"] = operator_id
    return identity


def _force_clear_activity(operation_id: str, details: dict[str, object]) -> None:
    try:
        with engine.begin() as connection:
            inspector = sa.inspect(connection)
            if not inspector.has_table("activity_logs") or not inspector.has_table("companies"):
                raise RuntimeError("activity_logs veya companies tablosu yok")
            company_id = connection.execute(
                sa.text("SELECT id FROM companies ORDER BY id LIMIT 1")
            ).scalar_one_or_none()
            if company_id is None:
                raise RuntimeError("activity audit için şirket kaydı yok")
            log_activity(
                connection,
                int(company_id),
                None,
                "backup.maintenance_force_cleared",
                "backup",
                None,
                "Platform bakım durumu operatör tarafından zorla temizlendi",
                details,
                correlation_id=operation_id,
            )
    except Exception as exc:
        print(
            f"UYARI: force-clear activity audit yazılamadı; journal kaydı korundu: {exc}",
            file=sys.stderr,
        )


def _maintenance_clear(
    operation_id: str, force: bool, reason: str | None = None
) -> dict:
    clean_reason = (reason or "").strip()
    if force and not clean_reason:
        raise BackupError("--force kullanımı için boş olmayan --reason zorunludur")
    if force:
        print(
            "UYARI: --force canlı bakım sahibini doğrulamadan fail-closed durumu açar",
            file=sys.stderr,
        )
    result = clear_maintenance(engine, operation_id, force=force)
    identity = _caller_identity()
    details = {
        **result,
        "reason": clean_reason if force else None,
        "caller": identity,
    }
    event = (
        "backup.maintenance_force_cleared"
        if force
        else "backup.maintenance_recovered"
    )
    safe_append_restore_journal(
        settings.sungur_data_dir,
        event,
        operation_id=operation_id,
        owner=f"{identity['os_user']}@{identity['hostname']}:{identity['pid']}",
        details=details,
    )
    if force:
        _force_clear_activity(operation_id, details)
    return details


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "create":
            result = _create()
        elif args.command == "restore":
            result = _restore(args.name)
        else:
            result = _maintenance_clear(args.operation_id, args.force, args.reason)
    except BackupError as exc:
        print(json.dumps(
            {"status": "error", "detail": str(exc), **getattr(exc, "details", {})},
            ensure_ascii=False,
        ))
        return 2
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
