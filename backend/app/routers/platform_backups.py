from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text

from ..activity_log import log_activity
from ..backup_errors import BackupError
from ..config import settings
from ..database_backup import (
    apply_backup,
    backup_directory,
    create_platform_backup,
    enforce_retention,
    list_platform_backups,
    read_checksum,
    read_manifest,
    resolve_backup,
    validate_restored_database,
    verify_platform_backup,
)
from ..db import SessionLocal, engine
from ..maintenance import (
    assert_writes_allowed,
    current_operation,
    drain_postgresql_writes,
    mark_recovery_required,
    operation_lock,
    set_maintenance,
)
from ..platform_access import require_platform_operator
from ..restore_journal import safe_append_restore_journal

router = APIRouter(prefix="/platform/backups", tags=["Platform Yedekleri"])


class RestorePayload(BaseModel):
    confirmation: str


def _authorize(request: Request) -> None:
    require_platform_operator(request)


def _log(request: Request, action: str, summary: str, details: dict | None = None) -> None:
    with SessionLocal.begin() as db:
        log_activity(
            db, int(request.state.company_id), int(request.state.user["id"]),
            action, "backup", None, summary, details,
            correlation_id=request.state.request_id,
        )


def _safe_log(request: Request, action: str, summary: str, details: dict) -> None:
    try:
        _log(request, action, summary, details)
    except Exception:
        # The restored snapshot may not contain the caller's former tenant/user.
        # Keep the panel event in the first restored company with a null actor;
        # the external journal remains the immutable cross-restore identity log.
        try:
            with SessionLocal.begin() as db:
                company_id = db.execute(
                    text("SELECT id FROM companies ORDER BY id LIMIT 1")
                ).scalar_one()
                log_activity(
                    db, int(company_id), None, action, "backup", None,
                    summary, details, correlation_id=request.state.request_id,
                )
        except Exception:
            pass


def _http_error(exc: BackupError) -> HTTPException:
    message = str(exc)
    if "disk alanı" in message:
        return HTTPException(status_code=507, detail=message)
    if any(token in message for token in ("uyumsuz", "sürüyor", "boşalmadı")):
        return HTTPException(status_code=409, detail=message)
    if "bulunamadı" in message:
        return HTTPException(status_code=404, detail=message)
    return HTTPException(status_code=400, detail=message)


def _journal(operation: dict, event: str, details: dict) -> None:
    safe_append_restore_journal(
        settings.sungur_data_dir, event,
        operation_id=operation["operation_id"],
        owner=operation["owner"], details=details,
    )


@router.get("")
def list_backups(request: Request):
    _authorize(request)
    return {
        "items": list_platform_backups(settings.sungur_data_dir),
        "active_operation": current_operation(
            settings.database_url, settings.sungur_data_dir
        ),
    }


@router.post("", status_code=201)
def create_backup(request: Request):
    _authorize(request)
    try:
        assert_writes_allowed(engine)
        with operation_lock(
            settings.database_url, settings.sungur_data_dir, "backup.create",
            timeout_seconds=settings.backup_lock_timeout_seconds,
        ):
            item = create_platform_backup(
                settings.database_url, settings.sungur_data_dir,
                retention_count=settings.backup_retention_count,
            )
    except BackupError as exc:
        raise _http_error(exc) from exc
    _log(request, "backup.created", f"Tam veritabanı yedeği oluşturuldu: {item['name']}", item)
    return item


@router.get("/{name}/download")
def download_backup(name: str, request: Request):
    _authorize(request)
    try:
        path = resolve_backup(settings.sungur_data_dir, name)
        verified = {
            "name": path.name, "sha256": read_checksum(path),
            "size_bytes": path.stat().st_size,
        }
    except BackupError as exc:
        raise _http_error(exc) from exc
    _log(request, "backup.downloaded", f"Tam veritabanı yedeği indirildi: {name}", verified)
    response = FileResponse(path, filename=path.name, media_type="application/octet-stream")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/{name}/verify")
def verify_backup(name: str, request: Request):
    _authorize(request)
    try:
        path = resolve_backup(settings.sungur_data_dir, name)
        return {"status": "verified", **verify_platform_backup(settings.database_url, path)}
    except BackupError as exc:
        raise _http_error(exc) from exc


@router.post("/{name}/restore")
def restore_backup(name: str, payload: RestorePayload, request: Request):
    _authorize(request)
    if payload.confirmation != "GERI YUKLE":
        raise HTTPException(status_code=400, detail='Onay metni tam olarak "GERI YUKLE" olmalıdır')
    if engine.dialect.name == "sqlite":
        raise HTTPException(
            status_code=409,
            detail="SQLite'ta geri yükleme yalnız uygulama kapalıyken CLI ile yapılabilir",
        )

    pre_backup: dict | None = None
    database_state = "unchanged"
    rollback_command: str | None = None
    rollback_error: str | None = None
    try:
        with operation_lock(
            settings.database_url, settings.sungur_data_dir, "backup.restore",
            timeout_seconds=settings.backup_lock_timeout_seconds,
        ) as operation:
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
                started_details = {
                    "backup": name, "pre_restore_backup": pre_backup["name"],
                    "rollback_command": rollback_command,
                }
                _journal(operation, "backup.restore_started", started_details)
                _log(request, "backup.restore_started", f"Geri yükleme başlatıldı: {name}", started_details)

                try:
                    apply_backup(settings.database_url, selected_path)
                    database_state = "restored_unverified"
                    set_maintenance(engine, operation, True)
                    counts = validate_restored_database(
                        settings.database_url, read_manifest(selected_path)
                    )
                    database_state = "restored"
                except Exception as restore_exc:
                    rollback_details = {
                        "backup": name, "pre_restore_backup": pre_backup["name"],
                        "cause": str(restore_exc), "rollback_command": rollback_command,
                    }
                    _journal(operation, "backup.restore_rollback_started", rollback_details)
                    _safe_log(
                        request, "backup.restore_rollback_started",
                        f"Ön-yedeğe dönüş başlatıldı: {pre_backup['name']}", rollback_details,
                    )
                    try:
                        apply_backup(settings.database_url, pre_path)
                        set_maintenance(engine, operation, True)
                        validate_restored_database(
                            settings.database_url, read_manifest(pre_path)
                        )
                        database_state = "rolled_back"
                        _journal(operation, "backup.restore_rollback_completed", rollback_details)
                        _safe_log(
                            request, "backup.restore_rollback_completed",
                            f"Ön-yedeğe dönüş tamamlandı: {pre_backup['name']}", rollback_details,
                        )
                    except Exception as rollback_exc:
                        database_state = "rollback_failed"
                        rollback_error = str(rollback_exc)
                        rollback_details["rollback_error"] = rollback_error
                        mark_recovery_required(engine, operation)
                        _journal(operation, "backup.restore_rollback_failed", rollback_details)
                        _safe_log(
                            request, "backup.restore_rollback_failed",
                            f"Ön-yedeğe dönüş başarısız: {pre_backup['name']}", rollback_details,
                        )
                    raise BackupError(str(restore_exc)) from restore_exc

                result = {
                    "status": "completed", "restored_backup": selected,
                    "pre_restore_backup": pre_backup,
                    "revision": read_manifest(selected_path)["alembic_revision"],
                    "table_counts": counts, "database_state": database_state,
                    "rollback_command": rollback_command,
                }
                _journal(operation, "backup.restore_completed", result)
                # Replay both facts into the restored DB while the external
                # journal retains their original ordering independently.
                _safe_log(request, "backup.restore_started", f"Geri yükleme başlatıldı: {name}", started_details)
                _safe_log(request, "backup.restore_completed", f"Geri yükleme tamamlandı: {name}", result)
                enforce_retention(
                    backup_directory(settings.sungur_data_dir),
                    settings.backup_retention_count,
                )
                return result
            finally:
                if database_state != "rollback_failed":
                    set_maintenance(engine, operation, False)
    except BackupError as exc:
        details = {
            "backup": name,
            "error": str(exc),
            "pre_restore_backup": pre_backup["name"] if pre_backup else None,
            "rollback_command": rollback_command,
            "database_state": database_state,
            "rollback_error": rollback_error,
            "recovery_command": (
                f"python -m app.backup_cli maintenance-clear --operation-id "
                f"{operation['operation_id']}"
                if database_state == "rollback_failed" and "operation" in locals()
                else None
            ),
            "operator_action": (
                "Veritabanını inceleyin; doğruladıktan sonra maintenance-clear çalıştırın"
                if database_state == "rollback_failed"
                else None
            ),
        }
        if "operation" in locals():
            _journal(operation, "backup.restore_failed", details)
        _safe_log(request, "backup.restore_failed", f"Geri yükleme başarısız: {name}", details)
        raise HTTPException(status_code=409, detail=details) from exc
