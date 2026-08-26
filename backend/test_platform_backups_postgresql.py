from __future__ import annotations

import os
import json
import threading

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.backup_errors import BackupError
from app.database_backup import create_platform_backup, restore_platform_backup, verify_platform_backup
from app.runtime_migrations import run_database_migrations
from app.bootstrap_data import seed_bootstrap_data
from app.maintenance import (
    MAINTENANCE_LOCK_KEY,
    acquire_write_transaction_lock,
    drain_postgresql_writes,
    is_maintenance_active,
    maintenance_status,
    operation_lock,
)


def _url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("PostgreSQL twin yalnız PostgreSQL CI işinde çalışır")
    return url


def test_pg_dump_and_pg_restore_smoke(tmp_path) -> None:
    url = _url()
    engine = create_engine(url)
    run_database_migrations(engine)
    seed_bootstrap_data(engine)
    with engine.begin() as connection:
        original = connection.execute(text("SELECT name FROM companies ORDER BY id LIMIT 1")).scalar_one()
    engine.dispose()

    selected = create_platform_backup(url, tmp_path)
    path = tmp_path / "backups" / str(selected["name"])
    assert verify_platform_backup(url, path)["revision"] == selected["revision"]

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("UPDATE companies SET name='RESTORE-SMOKE'"))
    engine.dispose()

    result = restore_platform_backup(url, tmp_path, path)
    assert result["pre_restore_backup"]["name"] != selected["name"]
    assert result["table_counts"]["companies"] >= 1

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            restored = connection.execute(text("SELECT name FROM companies ORDER BY id LIMIT 1")).scalar_one()
    finally:
        engine.dispose()
    assert restored == original


def test_postgresql_advisory_lock_rejects_second_process(tmp_path) -> None:
    url = _url()
    engine = create_engine(url)
    run_database_migrations(engine)
    seed_bootstrap_data(engine)
    engine.dispose()
    with operation_lock(url, tmp_path, "backup.restore"):
        with pytest.raises(BackupError, match="sürüyor"):
            with operation_lock(url, tmp_path, "backup.create"):
                pass


def test_drain_timeout_cancels_before_database_change() -> None:
    url = _url()
    engine = create_engine(url)
    run_database_migrations(engine)
    seed_bootstrap_data(engine)
    with engine.connect() as writer:
        transaction = writer.begin()
        original = writer.execute(
            text("SELECT name FROM companies ORDER BY id LIMIT 1")
        ).scalar_one()
        writer.execute(text("UPDATE companies SET name='UNCOMMITTED-DRAIN'"))
        with pytest.raises(BackupError, match="boşalmadı"):
            drain_postgresql_writes(engine, 0)
        transaction.rollback()
    with engine.connect() as connection:
        current = connection.execute(
            text("SELECT name FROM companies ORDER BY id LIMIT 1")
        ).scalar_one()
    engine.dispose()
    assert current == original


def _set_active_maintenance(engine, operation_id: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """UPDATE platform_maintenance
                   SET active=true, operation_id=:operation_id,
                       operation_kind='backup.restore', owner='crashed-worker',
                       started_at=now(), heartbeat_at=now()
                   WHERE id=1"""
            ),
            {"operation_id": operation_id},
        )


def test_stale_maintenance_without_owner_is_recovered() -> None:
    url = _url()
    engine = create_engine(url)
    run_database_migrations(engine)
    seed_bootstrap_data(engine)
    _set_active_maintenance(engine, "stale-without-owner")
    assert is_maintenance_active(engine) is False
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT active, operation_id FROM platform_maintenance WHERE id=1")
        ).one()
        recovered = connection.execute(
            text(
                """SELECT COUNT(*) FROM activity_logs
                   WHERE action_type='backup.maintenance_recovered'"""
            )
        ).scalar_one()
    engine.dispose()
    assert row == (False, None)
    assert recovered == 1


def test_concurrent_stale_recovery_has_single_winner_and_event() -> None:
    url = _url()
    engine = create_engine(url)
    run_database_migrations(engine)
    seed_bootstrap_data(engine)
    with engine.connect() as connection:
        before = connection.execute(
            text(
                """SELECT COUNT(*) FROM activity_logs
                   WHERE action_type='backup.maintenance_recovered'"""
            )
        ).scalar_one()
    _set_active_maintenance(engine, "concurrent-stale-recovery")
    barrier = threading.Barrier(2)
    results: list[dict] = []

    def recover() -> None:
        worker_engine = create_engine(url)
        try:
            barrier.wait()
            results.append(maintenance_status(worker_engine))
        finally:
            worker_engine.dispose()

    threads = [threading.Thread(target=recover) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(results) == 2
    assert sum(result.get("recovered") is not None for result in results) == 1
    with engine.connect() as connection:
        after = connection.execute(
            text(
                """SELECT COUNT(*) FROM activity_logs
                   WHERE action_type='backup.maintenance_recovered'"""
            )
        ).scalar_one()
        active = connection.execute(
            text("SELECT active FROM platform_maintenance WHERE id=1")
        ).scalar_one()
    engine.dispose()
    assert after - before == 1
    assert active is False


def test_0034_retry_after_pg_rollback_does_not_duplicate_legacy_journal(
    tmp_path, monkeypatch
) -> None:
    url = _url()
    engine = create_engine(url)
    run_database_migrations(engine)
    seed_bootstrap_data(engine)
    command.downgrade(Config("alembic.ini"), "20260728_0034")
    monkeypatch.setenv("SUNGUR_DATA_DIR", str(tmp_path))
    journal = tmp_path / "restore_journal.jsonl"
    prior = {
        "event": "backup.maintenance_legacy_normalized",
        "operation_id": "legacy-migration-20260728-0034",
        "details": {"simulated_pg_rollback": True},
    }
    journal.write_text(json.dumps(prior) + "\n", encoding="utf-8")
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE platform_maintenance"))
        connection.execute(
            text(
                """CREATE TABLE platform_maintenance (
                       id INTEGER PRIMARY KEY,
                       active BOOLEAN NOT NULL DEFAULT false
                   )"""
            )
        )
        connection.execute(
            text("INSERT INTO platform_maintenance (id, active) VALUES (1, true)")
        )
        # Rewind to 0034's predecessor whatever the current head is. Matching on
        # ``WHERE version_num='20260728_0034'`` only worked while 0034 WAS the
        # head: once any migration stacks on top the UPDATE matches nothing, 0034
        # never replays, and this test silently exercises the wrong thing (the
        # legacy platform_maintenance table it just recreated is then left in
        # place for the rest of the file).
        connection.execute(
            text("UPDATE alembic_version SET version_num='20260728_0033'")
        )
    run_database_migrations(engine)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """SELECT active, operation_id, operation_kind
                   FROM platform_maintenance"""
            )
        ).one()
    engine.dispose()
    assert row == (
        True,
        "legacy-migration-20260728-0034",
        "backup.legacy_maintenance",
    )
    records = [
        json.loads(line) for line in journal.read_text("utf-8").splitlines()
    ]
    assert sum(
        record.get("event") == "backup.maintenance_legacy_normalized"
        and record.get("operation_id") == "legacy-migration-20260728-0034"
        for record in records
    ) == 1


def test_terminated_advisory_owner_is_recovered() -> None:
    url = _url()
    engine = create_engine(url)
    run_database_migrations(engine)
    seed_bootstrap_data(engine)
    owner = engine.connect()
    pid = owner.execute(text("SELECT pg_backend_pid()")).scalar_one()
    owner.execute(
        text("SELECT pg_advisory_lock(:key)"), {"key": MAINTENANCE_LOCK_KEY}
    )
    _set_active_maintenance(engine, "terminated-owner")
    with engine.begin() as killer:
        assert killer.execute(
            text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
        ).scalar_one()
    owner.invalidate()
    owner.close()
    assert is_maintenance_active(engine) is False
    engine.dispose()


def test_exclusive_restore_lock_blocks_new_writer_commit() -> None:
    url = _url()
    engine = create_engine(url)
    run_database_migrations(engine)
    seed_bootstrap_data(engine)
    restore = engine.connect()
    restore.execute(
        text("SELECT pg_advisory_lock(:key)"), {"key": MAINTENANCE_LOCK_KEY}
    )
    outcomes: list[str] = []

    def writer() -> None:
        try:
            with engine.begin() as connection:
                acquire_write_transaction_lock(connection)
                connection.execute(text("UPDATE companies SET name=name"))
            outcomes.append("committed")
        except BackupError:
            outcomes.append("maintenance")

    thread = threading.Thread(target=writer)
    thread.start()
    thread.join(5)
    assert outcomes == ["maintenance"]
    restore.execute(
        text("SELECT pg_advisory_unlock(:key)"), {"key": MAINTENANCE_LOCK_KEY}
    )
    restore.close()
    engine.dispose()


def test_session_write_hook_holds_shared_lock_for_transaction() -> None:
    from app.db import SessionLocal

    url = _url()
    engine = create_engine(url)
    run_database_migrations(engine)
    seed_bootstrap_data(engine)
    session = SessionLocal()
    session.execute(text("UPDATE companies SET name=name"))
    contender = engine.connect()
    try:
        assert contender.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": MAINTENANCE_LOCK_KEY},
        ).scalar_one() is False
    finally:
        session.rollback()
    assert contender.execute(
        text("SELECT pg_try_advisory_lock(:key)"),
        {"key": MAINTENANCE_LOCK_KEY},
    ).scalar_one() is True
    contender.execute(
        text("SELECT pg_advisory_unlock(:key)"),
        {"key": MAINTENANCE_LOCK_KEY},
    )
    contender.close()
    session.close()
    engine.dispose()
