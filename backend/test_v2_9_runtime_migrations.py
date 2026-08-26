from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent


def _expected_revision() -> str:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head
    return head


def _run_isolated(script: str, database: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )


def test_app_startup_applies_alembic_and_reports_revision(tmp_path: Path) -> None:
    database = tmp_path / "runtime-migrations.db"
    script = r'''
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    health = client.get('/api/health')
    assert health.status_code == 200, health.text
'''
    first = _run_isolated(script, database)
    assert first.returncode == 0, first.stdout + "\n" + first.stderr
    second = _run_isolated(script, database)
    assert second.returncode == 0, second.stdout + "\n" + second.stderr

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            _expected_revision(),
        )


def test_local_sqlite_enables_integrity_and_concurrency_pragmas(tmp_path: Path) -> None:
    database = tmp_path / "sqlite-pragmas.db"
    script = r'''
from app.db import engine
with engine.connect() as connection:
    raw = connection.connection.driver_connection
    assert raw.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    assert raw.execute('PRAGMA busy_timeout').fetchone()[0] >= 5000
    assert raw.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
    assert raw.execute('PRAGMA synchronous').fetchone()[0] in (1, 2)
'''
    result = _run_isolated(script, database)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_irreversible_numeric_migration_refuses_fake_downgrade() -> None:
    migration_path = (
        BACKEND / "alembic" / "versions" / "20260713_0001_numeric_hardening.py"
    )
    spec = importlib.util.spec_from_file_location("numeric_hardening_runtime", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with pytest.raises(RuntimeError, match="geri alınamaz"):
        module.downgrade()


def test_runtime_image_contains_alembic_dependency() -> None:
    requirements = (BACKEND / "requirements.txt").read_text(encoding="utf-8")
    assert "alembic>=" in requirements
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "backend/requirements.txt" in dockerfile
