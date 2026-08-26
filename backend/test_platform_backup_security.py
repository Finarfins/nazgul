from __future__ import annotations

import multiprocessing
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.backup_errors import BackupError
from app.database_backup import (
    RestoreExecutionError,
    apply_backup_with_rollback,
    resolve_backup,
)
from app.maintenance import current_operation, operation_lock
from app.routers import platform_backups
from app import restore_journal
from app import backup_cli


def _hold_lock(database_url: str, data_dir: str, ready) -> None:
    with operation_lock(database_url, data_dir, "backup.restore"):
        ready.set()
        time.sleep(2)


def test_second_process_backup_is_rejected_by_same_lock_family(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}"
    ready = multiprocessing.Event()
    process = multiprocessing.Process(
        target=_hold_lock, args=(database_url, str(tmp_path), ready)
    )
    process.start()
    try:
        assert ready.wait(5)
        active = current_operation(database_url, tmp_path)
        assert active and active["operation_kind"] == "backup.restore"
        assert active["operation_id"] and active["owner"] and active["started_at"]
        with pytest.raises(BackupError, match="sürüyor"):
            with operation_lock(database_url, tmp_path, "backup.create"):
                pass
    finally:
        process.join(5)
        if process.is_alive():
            process.terminate()


def test_force_maintenance_clear_requires_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(
        backup_cli, "clear_maintenance",
        lambda *_args, **_kwargs: called.append(True),
    )
    with pytest.raises(BackupError, match="--reason"):
        backup_cli._maintenance_clear("force-without-reason", True, "   ")
    assert called == []


def test_force_maintenance_clear_audits_identity_and_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.bootstrap_data import seed_bootstrap_data
    from app.db import engine
    from app.runtime_migrations import run_database_migrations

    run_database_migrations(engine)
    seed_bootstrap_data(engine)

    operation_id = "force-audit-operation"
    monkeypatch.setattr(backup_cli.settings, "sungur_data_dir", str(tmp_path))
    monkeypatch.setenv("SUNGUR_OPERATOR_ID", "immutable-user-42")
    monkeypatch.setattr(
        backup_cli,
        "clear_maintenance",
        lambda *_args, **_kwargs: {
            "cleared": True,
            "operation_id": operation_id,
            "forced": True,
        },
    )
    result = backup_cli._maintenance_clear(
        operation_id, True, "Doğrulanmış rollback sonrası erişimi aç"
    )
    assert result["reason"] == "Doğrulanmış rollback sonrası erişimi aç"
    assert result["caller"]["operator_id"] == "immutable-user-42"
    assert result["caller"]["os_user"]
    assert result["caller"]["hostname"]
    assert result["caller"]["pid"] == os.getpid()

    journal = [
        json.loads(line)
        for line in (tmp_path / "restore_journal.jsonl").read_text("utf-8").splitlines()
    ]
    force_entry = journal[-1]
    assert force_entry["event"] == "backup.maintenance_force_cleared"
    assert force_entry["details"]["reason"] == result["reason"]
    assert force_entry["details"]["caller"] == result["caller"]

    with engine.connect() as connection:
        audit = connection.execute(
            text(
                """SELECT details FROM activity_logs
                   WHERE action_type='backup.maintenance_force_cleared'
                     AND correlation_id=:operation_id
                   ORDER BY id DESC LIMIT 1"""
            ),
            {"operation_id": operation_id},
        ).scalar_one()
    decoded = json.loads(audit)
    assert decoded["reason"] == result["reason"]
    assert decoded["caller"] == result["caller"]


@pytest.mark.parametrize(
    "name",
    ["../secret.dump", "%2e%2e%2fsecret.dump", r"..\secret.dump", "%252e%252e%252fsecret.dump"],
)
def test_backup_name_traversal_is_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(BackupError, match="Geçersiz"):
        resolve_backup(tmp_path, name)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    outside = tmp_path / "outside.dump"
    outside.write_bytes(b"x")
    link = backups / "backup-20260728-120000-20260728_0033.dump"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Bu ortam symlink oluşturmaya izin vermiyor")
    with pytest.raises(BackupError):
        resolve_backup(tmp_path, link.name)


def test_manifest_validation_failure_attempts_automatic_rollback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    selected = tmp_path / "selected.dump"
    pre = tmp_path / "pre.dump"
    applied: list[Path] = []
    rollback_started: list[str] = []

    monkeypatch.setattr(
        "app.database_backup.apply_backup",
        lambda _url, path: applied.append(path),
    )
    validations = iter([BackupError("manifest sayımı uyuşmuyor"), {"companies": 1}])

    def validate(*_args):
        result = next(validations)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("app.database_backup.validate_restored_database", validate)
    monkeypatch.setattr("app.database_backup.read_manifest", lambda _path: {})

    with pytest.raises(RestoreExecutionError) as error:
        apply_backup_with_rollback(
            "sqlite:///unused", selected, pre,
            on_rollback_started=lambda exc: rollback_started.append(str(exc)),
        )
    assert error.value.database_state == "rolled_back"
    assert applied == [selected, pre]
    assert rollback_started == ["manifest sayımı uyuşmuyor"]


def test_database_maintenance_from_second_engine_returns_503() -> None:
    from app.db import engine
    from app.main import app

    second = create_engine(str(engine.url), connect_args={"check_same_thread": False})
    try:
        with second.begin() as connection:
            connection.execute(
                text(
                    """UPDATE platform_maintenance
                       SET active=true, operation_id='other-process',
                           operation_kind='backup.restore', owner='worker:2'
                       WHERE id=1"""
                )
            )
        response = TestClient(app).post("/api/customers", json={})
        assert response.status_code == 503
        assert response.json()["code"] == "RESTORE_MAINTENANCE"
    finally:
        with second.begin() as connection:
            connection.execute(
                text(
                    """UPDATE platform_maintenance
                       SET active=false, operation_id=NULL,
                           operation_kind=NULL, owner=NULL, started_at=NULL
                       WHERE id=1"""
                )
            )
        second.dispose()


def test_rollback_failed_keeps_maintenance_and_returns_recovery_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operation = {
        "operation_id": "rollback-failed-op",
        "owner": "worker:1",
        "started_at": "2026-07-28T12:00:00+00:00",
    }

    @contextmanager
    def fake_lock(*_args, **_kwargs):
        yield operation

    maintenance_calls: list[bool] = []
    applies = iter([BackupError("restore bozuk"), BackupError("rollback bozuk")])

    def fail_apply(*_args, **_kwargs):
        raise next(applies)

    monkeypatch.setattr(platform_backups, "_authorize", lambda _request: None)
    monkeypatch.setattr(
        platform_backups, "engine",
        SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
    )
    monkeypatch.setattr(platform_backups, "operation_lock", fake_lock)
    monkeypatch.setattr(
        platform_backups, "set_maintenance",
        lambda _engine, _operation, active: maintenance_calls.append(active),
    )
    monkeypatch.setattr(platform_backups, "drain_postgresql_writes", lambda *_args: None)
    monkeypatch.setattr(platform_backups, "resolve_backup", lambda *_args: tmp_path / "x.dump")
    monkeypatch.setattr(
        platform_backups, "verify_platform_backup",
        lambda *_args, **_kwargs: {"name": "selected.dump"},
    )
    monkeypatch.setattr(
        platform_backups, "create_platform_backup",
        lambda *_args, **_kwargs: {"name": "pre.dump"},
    )
    monkeypatch.setattr(platform_backups, "apply_backup", fail_apply)
    monkeypatch.setattr(platform_backups, "_journal", lambda *_args: None)
    monkeypatch.setattr(platform_backups, "_log", lambda *_args: None)
    monkeypatch.setattr(platform_backups, "_safe_log", lambda *_args: None)
    marked: list[str] = []
    monkeypatch.setattr(
        platform_backups, "mark_recovery_required",
        lambda _engine, op: marked.append(op["operation_id"]),
    )
    request = SimpleNamespace(
        state=SimpleNamespace(
            company_id=1, user={"id": 1}, request_id="test-request"
        )
    )

    with pytest.raises(HTTPException) as error:
        platform_backups.restore_backup(
            "selected.dump",
            platform_backups.RestorePayload(confirmation="GERI YUKLE"),
            request,
        )
    assert error.value.status_code == 409
    assert error.value.detail["database_state"] == "rollback_failed"
    assert error.value.detail["pre_restore_backup"] == "pre.dump"
    assert error.value.detail["rollback_error"] == "rollback bozuk"
    assert "maintenance-clear" in error.value.detail["recovery_command"]
    assert error.value.detail["operator_action"]
    assert maintenance_calls == [True]
    assert marked == ["rollback-failed-op"]


def test_journal_failure_does_not_replace_primary_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        restore_journal,
        "append_restore_journal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    assert restore_journal.safe_append_restore_journal(
        ".", "backup.restore_failed",
        operation_id="op", owner="worker", details={"error": "primary"},
    ) is False
    assert "disk full" in capsys.readouterr().err
