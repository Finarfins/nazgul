from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.database_backup import (
    BackupError,
    create_sqlite_backup,
    restore_sqlite_backup,
    verify_sqlite_backup,
)
from app.reconciliation import (
    capture_numeric_snapshot,
    compare_numeric_snapshots,
    read_snapshot,
    write_snapshot,
)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE payments(
                id INTEGER PRIMARY KEY,
                amount REAL,
                company_id INTEGER NOT NULL
            );
            CREATE TABLE products(
                id INTEGER PRIMARY KEY,
                purchase_price REAL,
                sale_price REAL,
                vat_rate REAL,
                stock REAL,
                units_per_box REAL,
                critical_stock REAL,
                minimum_stock REAL,
                company_id INTEGER NOT NULL
            );
            INSERT INTO payments(id, amount, company_id) VALUES
                (1, 0.1, 1), (2, 0.2, 1), (3, NULL, 1);
            INSERT INTO products(
                id, purchase_price, sale_price, vat_rate, stock,
                units_per_box, critical_stock, minimum_stock, company_id
            ) VALUES (1, 10.125, 20.335, 20, 1.23456, 1, 0.5, 0.25, 1);
            """
        )


def test_sqlite_backup_restore_is_atomic_and_verified(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _database(source)

    artifact = create_sqlite_backup(f"sqlite:///{source.as_posix()}", tmp_path / "backups")
    assert artifact.backup_path.is_file()
    assert artifact.manifest_path.is_file()
    assert verify_sqlite_backup(artifact.backup_path).sha256 == artifact.sha256

    target = tmp_path / "restored.db"
    restore_sqlite_backup(
        artifact.backup_path,
        target,
        expected_sha256=artifact.sha256,
    )
    with closing(sqlite3.connect(target)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 3
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # Restoring over a live target creates a safety copy before atomic replace.
    with closing(sqlite3.connect(target)) as connection:
        connection.execute("DELETE FROM payments")
        connection.commit()
    safety = restore_sqlite_backup(
        artifact.backup_path,
        target,
        expected_sha256=artifact.sha256,
        overwrite=True,
    )
    assert safety and safety.is_file()
    with closing(sqlite3.connect(safety)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
    with closing(sqlite3.connect(target)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 3


def test_corrupt_or_wrong_checksum_backup_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _database(source)
    artifact = create_sqlite_backup(f"sqlite:///{source.as_posix()}", tmp_path / "backups")

    with pytest.raises(BackupError, match="checksum"):
        verify_sqlite_backup(artifact.backup_path, "0" * 64)

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not-a-sqlite-database")
    with pytest.raises(BackupError, match="bütünlük"):
        verify_sqlite_backup(corrupt)


def test_numeric_reconciliation_detects_financial_drift(tmp_path: Path) -> None:
    database = tmp_path / "reconciliation.db"
    _database(database)
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        before = capture_numeric_snapshot(engine)
        output = tmp_path / "before.json"
        write_snapshot(before, output)
        loaded = read_snapshot(output)
        assert loaded["sha256"] == before["sha256"]
        assert compare_numeric_snapshots(before, capture_numeric_snapshot(engine)) == []

        with engine.begin() as connection:
            connection.execute(text("UPDATE payments SET amount=amount+1 WHERE id=1"))
        differences = compare_numeric_snapshots(before, capture_numeric_snapshot(engine))
        assert any(
            item.table == "payments" and item.column == "amount" and item.field == "sum"
            for item in differences
        )
    finally:
        engine.dispose()


def test_snapshot_checksum_tampering_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "reconciliation.db"
    _database(database)
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        snapshot = capture_numeric_snapshot(engine)
    finally:
        engine.dispose()
    path = tmp_path / "snapshot.json"
    write_snapshot(snapshot, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["columns"][0]["sum"] = "999999.99"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        read_snapshot(path)
