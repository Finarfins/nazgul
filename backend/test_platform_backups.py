from __future__ import annotations

import sqlite3
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.database_backup import BackupError, application_head, create_platform_backup, list_platform_backups, restore_platform_backup, verify_platform_backup
from app.platform_access import is_platform_operator, require_platform_operator
from app.activity_log import ACTION_TYPES
from app.routers import platform_backups

# Derived, not hard-coded: verify_platform_backup compares the backup's revision
# against the application's alembic head, so pinning a literal here makes every
# future migration fail this file even though the backup logic is unchanged.
# (develop pinned "20260728_0035" here; FAZ-3 adds 0036 and the next branch will
# add another — the derived value is the only form that survives that.)
HEAD = application_head()


def database(path: Path, revision: str = HEAD, marker: str = "before") -> str:
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE alembic_version(version_num TEXT PRIMARY KEY);"
            "CREATE TABLE companies(id INTEGER PRIMARY KEY, name TEXT);"
            "CREATE TABLE app_users(id INTEGER PRIMARY KEY, username TEXT);"
            "CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT);"
            "CREATE TABLE orders(id INTEGER PRIMARY KEY);"
            "CREATE TABLE activity_logs(id INTEGER PRIMARY KEY);"
            "CREATE TABLE platform_maintenance(id INTEGER PRIMARY KEY, active BOOLEAN);"
            "CREATE TABLE marker(value TEXT);"
        )
        db.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        db.execute("INSERT INTO companies VALUES (1, 'Sungur')")
        db.execute("INSERT INTO app_users VALUES (1, 'admin')")
        db.execute("INSERT INTO marker VALUES (?)", (marker,))
        db.execute("INSERT INTO platform_maintenance VALUES (1, 0)")
    return f"sqlite:///{path.as_posix()}"


def test_operator_requires_admin_and_explicit_allow_list() -> None:
    admin = {"id": 7, "username": "operator@example.com", "role": "admin"}
    assert is_platform_operator(admin, "7")
    assert not is_platform_operator(admin, "operator@example.com")
    assert not is_platform_operator(admin, "someone-else")
    assert not is_platform_operator({**admin, "role": "yonetici"}, "7")


def test_non_operator_admin_is_rejected_by_api_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.platform_access.settings.sungur_platform_operators", "someone-else")
    request = SimpleNamespace(
        state=SimpleNamespace(user={"id": 7, "username": "admin", "role": "admin"})
    )
    with pytest.raises(HTTPException) as error:
        require_platform_operator(request)
    assert error.value.status_code == 403


def test_create_list_sha256_and_retention(tmp_path: Path) -> None:
    url = database(tmp_path / "db.sqlite")
    first = create_platform_backup(url, tmp_path, retention_count=2)
    second = create_platform_backup(url, tmp_path, retention_count=2)
    third = create_platform_backup(url, tmp_path, retention_count=2)
    items = list_platform_backups(tmp_path)
    assert len(items) == 2
    assert first["name"] not in {item["name"] for item in items}
    assert {second["name"], third["name"]} == {item["name"] for item in items}
    assert verify_platform_backup(url, tmp_path / "backups" / str(third["name"]))["sha256"] == third["sha256"]


def test_revision_mismatch_is_rejected(tmp_path: Path) -> None:
    current_url = database(tmp_path / "current.sqlite")
    old_url = database(tmp_path / "old.sqlite", "20260727_0030")
    old = create_platform_backup(old_url, tmp_path / "old-data")
    with pytest.raises(BackupError, match="uyumsuz"):
        verify_platform_backup(current_url, tmp_path / "old-data" / "backups" / str(old["name"]))


def test_restore_creates_prebackup_and_verifies_counts(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    url = database(path, marker="selected")
    selected = create_platform_backup(url, tmp_path)
    db = sqlite3.connect(path)
    try:
        db.execute("UPDATE marker SET value='current'")
        db.commit()
    finally:
        db.close()
    result = restore_platform_backup(url, tmp_path, tmp_path / "backups" / str(selected["name"]))
    assert result["pre_restore_backup"]["name"] != selected["name"]
    assert result["revision"] == HEAD
    assert result["table_counts"]["companies"] == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM marker").fetchone()[0] == "selected"


def test_sqlite_restore_runs_end_to_end_only_through_offline_cli(tmp_path: Path) -> None:
    path = tmp_path / "offline.sqlite"
    url = database(path, marker="selected")
    selected = create_platform_backup(url, tmp_path)
    db = sqlite3.connect(path)
    try:
        db.execute("UPDATE marker SET value='changed'")
        db.commit()
    finally:
        db.close()
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": url,
            "SUNGUR_DATA_DIR": str(tmp_path),
            "AUTO_MIGRATE": "false",
            "PYTHONPATH": str(Path(__file__).resolve().parent),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "app.backup_cli", "restore", str(selected["name"])],
        cwd=Path(__file__).resolve().parent,
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    db = sqlite3.connect(path)
    try:
        assert db.execute("SELECT value FROM marker").fetchone()[0] == "selected"
    finally:
        db.close()


def test_manifest_missing_is_rejected_before_restore_changes_database(tmp_path: Path) -> None:
    path = tmp_path / "manifest-required.sqlite"
    url = database(path, marker="selected")
    selected = create_platform_backup(url, tmp_path)
    selected_path = tmp_path / "backups" / str(selected["name"])
    selected_path.with_suffix(selected_path.suffix + ".manifest.json").unlink()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE marker SET value='current'")
        db.commit()
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": url,
        "SUNGUR_DATA_DIR": str(tmp_path),
        "AUTO_MIGRATE": "false",
        "PYTHONPATH": str(Path(__file__).resolve().parent),
    })
    completed = subprocess.run(
        [sys.executable, "-m", "app.backup_cli", "restore", str(selected["name"])],
        cwd=Path(__file__).resolve().parent,
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 2
    assert "manifest" in completed.stdout.lower()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM marker").fetchone()[0] == "current"


def test_wrong_confirmation_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_backups, "_authorize", lambda _request: None)
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True

    with pytest.raises(HTTPException) as error:
        platform_backups.restore_backup(
            "backup-20260728-120000-20260728_0033.dump",
            platform_backups.RestorePayload(confirmation="GERİ YÜKLE"),
            object(),
        )
    assert error.value.status_code == 400
    assert not called


def test_sqlite_http_restore_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_backups, "_authorize", lambda _request: None)
    with pytest.raises(HTTPException) as error:
        platform_backups.restore_backup(
            "backup-20260728-120000-20260728_0033.dump",
            platform_backups.RestorePayload(confirmation="GERI YUKLE"),
            object(),
        )
    assert error.value.status_code == 409
    assert "yalnız uygulama kapalıyken CLI" in str(error.value.detail)


def test_backup_activity_catalog_is_complete() -> None:
    assert {
        "backup.created",
        "backup.downloaded",
        "backup.restore_started",
        "backup.restore_completed",
        "backup.restore_failed",
        "backup.restore_rollback_started",
        "backup.restore_rollback_completed",
        "backup.restore_rollback_failed",
        "backup.maintenance_recovered",
    } <= ACTION_TYPES.keys()
