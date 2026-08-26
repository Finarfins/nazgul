from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, text


BACKEND = Path(__file__).resolve().parent


def _alembic(database_url: str, data_dir: Path, revision: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["SUNGUR_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("variant", ["empty", "multiple", "active_null_operation"])
def test_0034_rebuilds_legacy_maintenance_to_canonical(
    tmp_path: Path, variant: str
) -> None:
    database = tmp_path / f"{variant}.sqlite"
    database_url = f"sqlite:///{database.as_posix()}"
    data_dir = tmp_path / f"{variant}-data"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """CREATE TABLE platform_maintenance (
                       id INTEGER PRIMARY KEY,
                       active BOOLEAN NOT NULL DEFAULT 0
                   )"""
            )
        )
        connection.execute(
            text(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) VALUES ('20260728_0033')"
            )
        )
        if variant == "multiple":
            connection.execute(
                text(
                    """INSERT INTO platform_maintenance (id, active)
                       VALUES (1, 0), (2, 1), (9, 0)"""
                )
            )
        elif variant == "active_null_operation":
            connection.execute(
                text("INSERT INTO platform_maintenance (id, active) VALUES (1, 1)")
            )
            connection.execute(text("CREATE TABLE companies (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO companies (id) VALUES (1)"))
            connection.execute(
                text(
                    """CREATE TABLE activity_logs (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           company_id INTEGER NOT NULL, user_id INTEGER,
                           action_type VARCHAR NOT NULL, resource_type VARCHAR NOT NULL,
                           resource_id INTEGER, summary TEXT NOT NULL, details TEXT,
                           correlation_id VARCHAR, created_at DATETIME NOT NULL
                       )"""
                )
            )
    engine.dispose()

    _alembic(database_url, data_dir, "20260728_0034")
    engine = create_engine(database_url)
    try:
        columns = inspect(engine).get_columns("platform_maintenance")
        assert [column["name"] for column in columns] == [
            "id",
            "active",
            "operation_id",
            "operation_kind",
            "owner",
            "started_at",
            "heartbeat_at",
        ]
        checks = inspect(engine).get_check_constraints("platform_maintenance")
        assert any(check["name"] == "ck_platform_maintenance_singleton" for check in checks)
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM platform_maintenance")
            ).mappings().one()
        assert row["id"] == 1
        if variant == "active_null_operation":
            assert bool(row["active"]) is True
            assert row["operation_id"] == "legacy-migration-20260728-0034"
            assert row["operation_kind"] == "backup.legacy_maintenance"
            from app.maintenance import clear_maintenance, maintenance_status

            assert bool(maintenance_status(engine, recover_stale=False)["active"]) is True
            cleared = clear_maintenance(
                engine, str(row["operation_id"]), force=True
            )
            assert cleared["cleared"] is True
            with engine.connect() as connection:
                activity = connection.execute(
                    text(
                        """SELECT details FROM activity_logs
                           WHERE action_type='backup.maintenance_legacy_normalized'"""
                    )
                ).scalar_one()
            assert json.loads(activity)["operation_id"] == row["operation_id"]
        else:
            assert bool(row["active"]) is False
    finally:
        engine.dispose()

    if variant in {"multiple", "active_null_operation"}:
        entries = [
            json.loads(line)
            for line in (data_dir / "restore_journal.jsonl").read_text("utf-8").splitlines()
        ]
        event = entries[-1]
        assert event["event"] == "backup.maintenance_legacy_normalized"
        if variant == "multiple":
            assert event["details"]["discarded_row_ids"] == [2, 9]
        else:
            assert event["details"]["active_null_operation_id_normalized"] is True


def test_0034_rejects_unknown_legacy_schema(tmp_path: Path) -> None:
    database = tmp_path / "unknown.sqlite"
    database_url = f"sqlite:///{database.as_posix()}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """CREATE TABLE platform_maintenance (
                       id INTEGER PRIMARY KEY, enabled TEXT, mystery TEXT
                   )"""
            )
        )
        connection.execute(
            text(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) VALUES ('20260728_0033')"
            )
        )
    engine.dispose()
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["SUNGUR_DATA_DIR"] = str(tmp_path / "data")
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode != 0
    assert "şeması tanınmıyor" in completed.stderr


def _create_legacy_source(connection, table: str) -> None:
    connection.execute(
        text(
            f"""CREATE TABLE {table} (
                   id INTEGER PRIMARY KEY,
                   active BOOLEAN NOT NULL DEFAULT 0
               )"""
        )
    )
    connection.execute(
        text(f"INSERT INTO {table} (id, active) VALUES (1, 1)")
    )


def _create_empty_canonical(connection, *, fill: bool) -> None:
    connection.execute(
        text(
            """CREATE TABLE platform_maintenance (
                   id INTEGER PRIMARY KEY,
                   active BOOLEAN NOT NULL DEFAULT 0,
                   operation_id VARCHAR(64),
                   operation_kind VARCHAR(40),
                   owner VARCHAR(200),
                   started_at DATETIME,
                   heartbeat_at DATETIME,
                   CONSTRAINT ck_platform_maintenance_singleton CHECK (id = 1)
               )"""
        )
    )
    if fill:
        connection.execute(
            text(
                """INSERT INTO platform_maintenance
                   (id, active, operation_id, operation_kind)
                   VALUES (1, 1, 'legacy-migration-20260728-0034',
                           'backup.legacy_maintenance')"""
            )
        )


@pytest.mark.parametrize(
    "cut_point",
    ["rename_after", "create_after", "fill_after", "drop_before"],
)
def test_0034_restart_uses_temp_as_authoritative_source(
    tmp_path: Path, cut_point: str
) -> None:
    database = tmp_path / f"restart-{cut_point}.sqlite"
    database_url = f"sqlite:///{database.as_posix()}"
    data_dir = tmp_path / f"restart-{cut_point}-data"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) VALUES ('20260728_0033')"
            )
        )
        _create_legacy_source(connection, "platform_maintenance_legacy_tmp")
        if cut_point in {"create_after", "fill_after", "drop_before"}:
            _create_empty_canonical(
                connection, fill=cut_point in {"fill_after", "drop_before"}
            )
    engine.dispose()

    _alembic(database_url, data_dir, "20260728_0034")
    engine = create_engine(database_url)
    try:
        assert not inspect(engine).has_table("platform_maintenance_legacy_tmp")
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """SELECT active, operation_id, operation_kind
                       FROM platform_maintenance"""
                )
            ).one()
        assert bool(row.active) is True
        assert row.operation_id == "legacy-migration-20260728-0034"
        assert row.operation_kind == "backup.legacy_maintenance"
    finally:
        engine.dispose()


def test_0034_legacy_journal_is_idempotent_on_retry(tmp_path: Path) -> None:
    database = tmp_path / "journal-retry.sqlite"
    database_url = f"sqlite:///{database.as_posix()}"
    data_dir = tmp_path / "journal-retry-data"
    data_dir.mkdir()
    journal = data_dir / "restore_journal.jsonl"
    prior = {
        "event": "backup.maintenance_legacy_normalized",
        "operation_id": "legacy-migration-20260728-0034",
        "details": {"simulated_rolled_back_attempt": True},
    }
    journal.write_text(json.dumps(prior) + "\n", encoding="utf-8")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) VALUES ('20260728_0033')"
            )
        )
        _create_legacy_source(connection, "platform_maintenance")
    engine.dispose()

    _alembic(database_url, data_dir, "20260728_0034")
    records = [
        json.loads(line) for line in journal.read_text("utf-8").splitlines()
    ]
    matching = [
        record
        for record in records
        if record.get("event") == "backup.maintenance_legacy_normalized"
        and record.get("operation_id") == "legacy-migration-20260728-0034"
    ]
    assert len(matching) == 1
