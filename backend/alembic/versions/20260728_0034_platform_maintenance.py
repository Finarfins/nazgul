"""Crash-safe canonical platform maintenance singleton.

Revision ID: 20260728_0034
Revises: 20260728_0033
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from alembic import op
import sqlalchemy as sa

revision = "20260728_0034"
down_revision = "20260728_0033"
branch_labels = None
depends_on = None

_TABLE = "platform_maintenance"
_LEGACY_TMP = "platform_maintenance_legacy_tmp"
_CANONICAL_COLUMNS = {
    "id",
    "active",
    "operation_id",
    "operation_kind",
    "owner",
    "started_at",
    "heartbeat_at",
}
_RECOVERY_ID = "legacy-migration-20260728-0034"
_EVENT = "backup.maintenance_legacy_normalized"


def _create_canonical_table() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("operation_id", sa.String(64), nullable=True),
        sa.Column("operation_kind", sa.String(40), nullable=True),
        sa.Column("owner", sa.String(200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_platform_maintenance_singleton"),
    )


def _journal_already_contains(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if (
                    record.get("event") == _EVENT
                    and record.get("operation_id") == _RECOVERY_ID
                ):
                    return True
    except FileNotFoundError:
        return False
    return False


def _append_journal(details: dict[str, object]) -> None:
    data_dir = Path(os.environ.get("SUNGUR_DATA_DIR", "data"))
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        path = data_dir / "restore_journal.jsonl"
        if _journal_already_contains(path):
            return
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": _EVENT,
            "operation_id": _RECOVERY_ID,
            "owner": "alembic-0034",
            "details": details,
        }
        payload = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode()
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        print(f"0034 legacy maintenance journal yazılamadı: {exc}", file=sys.stderr)


def _append_activity(bind: sa.Connection, details: dict[str, object]) -> None:
    inspector = sa.inspect(bind)
    if not inspector.has_table("activity_logs") or not inspector.has_table("companies"):
        return
    company_id = bind.execute(
        sa.text("SELECT id FROM companies ORDER BY id LIMIT 1")
    ).scalar_one_or_none()
    if company_id is None:
        return
    try:
        bind.execute(
            sa.text(
                """INSERT INTO activity_logs
                   (company_id, user_id, action_type, resource_type, resource_id,
                    summary, details, correlation_id, created_at)
                   VALUES (:company_id, NULL, :action_type, 'backup', NULL,
                    :summary, :details, :correlation_id, :created_at)"""
            ),
            {
                "company_id": company_id,
                "action_type": _EVENT,
                "summary": "Eski bakım durumu canonical biçime dönüştürüldü",
                "details": json.dumps(details, ensure_ascii=False, sort_keys=True),
                "correlation_id": _RECOVERY_ID,
                "created_at": datetime.now(timezone.utc),
            },
        )
    except (sa.exc.DBAPIError, sa.exc.StatementError) as exc:
        print(f"0034 legacy maintenance activity audit yazılamadı: {exc}", file=sys.stderr)


def _validate_known_schema(
    inspector: sa.Inspector, table: str
) -> set[str]:
    columns = inspector.get_columns(table)
    names = {str(column["name"]) for column in columns}
    if not {"id", "active"}.issubset(names) or not names.issubset(_CANONICAL_COLUMNS):
        raise RuntimeError(
            f"0034 {table} şeması tanınmıyor; "
            f"beklenen kolonlar {_CANONICAL_COLUMNS}, bulunan kolonlar {names}"
        )
    by_name = {str(column["name"]): column for column in columns}
    if not isinstance(by_name["id"]["type"], sa.Integer):
        raise RuntimeError(f"0034 {table}.id INTEGER değil")
    if not isinstance(by_name["active"]["type"], (sa.Boolean, sa.Integer)):
        raise RuntimeError(f"0034 {table}.active BOOLEAN/INTEGER değil")
    return names


def _active_value(value: object) -> bool:
    if value in (False, 0):
        return False
    if value in (True, 1):
        return True
    raise RuntimeError(f"0034 platform_maintenance.active değeri tanınmıyor: {value!r}")


def _timestamp_value(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError(
                f"0034 platform_maintenance zaman damgası tanınmıyor: {value!r}"
            ) from exc
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise RuntimeError(
        f"0034 platform_maintenance zaman damgası tanınmıyor: {value!r}"
    )


def _read_legacy(
    bind: sa.Connection, inspector: sa.Inspector
) -> tuple[dict[str, object], bool, list[object]]:
    names = _validate_known_schema(inspector, _LEGACY_TMP)
    selected = ", ".join(f'"{name}"' for name in sorted(names))
    rows = bind.execute(
        sa.text(f'SELECT {selected} FROM "{_LEGACY_TMP}" ORDER BY id')
    ).mappings().all()
    singleton = next((dict(row) for row in rows if row["id"] == 1), None)
    discarded_ids = [row["id"] for row in rows if row["id"] != 1]
    if singleton is None:
        singleton = {"id": 1, "active": False}
    active = _active_value(singleton.get("active"))
    operation_id = singleton.get("operation_id")
    operation_kind = singleton.get("operation_kind")
    normalized_active = active and not operation_id
    if normalized_active:
        operation_id = _RECOVERY_ID
        operation_kind = "backup.legacy_maintenance"
    now = datetime.now(timezone.utc)
    canonical = {
        "id": 1,
        "active": active,
        "operation_id": operation_id,
        "operation_kind": operation_kind,
        "owner": singleton.get("owner"),
        "started_at": _timestamp_value(singleton.get("started_at"))
        or (now if active else None),
        "heartbeat_at": _timestamp_value(singleton.get("heartbeat_at"))
        or (now if active else None),
    }
    return canonical, normalized_active, discarded_ids


def _rename_postgresql_constraints(bind: sa.Connection) -> None:
    if bind.dialect.name != "postgresql":
        return
    inspector = sa.inspect(bind)
    constraints = [inspector.get_pk_constraint(_LEGACY_TMP).get("name")]
    constraints.extend(
        check.get("name") for check in inspector.get_check_constraints(_LEGACY_TMP)
    )
    quote = bind.dialect.identifier_preparer.quote
    for name in filter(None, constraints):
        bind.execute(
            sa.text(
                f"ALTER TABLE {quote(_LEGACY_TMP)} "
                f"RENAME CONSTRAINT {quote(str(name))} "
                f"TO {quote(str(name) + '_legacy_tmp')}"
            )
        )


def _validate_canonical_row(
    bind: sa.Connection, expected: dict[str, object]
) -> None:
    inspector = sa.inspect(bind)
    if _validate_known_schema(inspector, _TABLE) != _CANONICAL_COLUMNS:
        raise RuntimeError("0034 canonical platform_maintenance kolonları eksik")
    row = bind.execute(
        sa.text(
            """SELECT id, active, operation_id, operation_kind, owner,
                      started_at, heartbeat_at
               FROM platform_maintenance"""
        )
    ).mappings().one()
    for key in ("id", "active", "operation_id", "operation_kind", "owner"):
        actual = bool(row[key]) if key == "active" else row[key]
        wanted = bool(expected[key]) if key == "active" else expected[key]
        if actual != wanted:
            raise RuntimeError(f"0034 canonical doğrulama hatası: {key}")
    for key in ("started_at", "heartbeat_at"):
        if (row[key] is None) != (expected[key] is None):
            raise RuntimeError(f"0034 canonical doğrulama hatası: {key}")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    has_main = inspector.has_table(_TABLE)
    has_legacy = inspector.has_table(_LEGACY_TMP)

    if not has_legacy and not has_main:
        _create_canonical_table()
        bind.execute(
            sa.text(
                """INSERT INTO platform_maintenance
                   (id, active, operation_id, operation_kind, owner, started_at, heartbeat_at)
                   VALUES (1, false, NULL, NULL, NULL, NULL, NULL)"""
            )
        )
        return

    if not has_legacy:
        _validate_known_schema(inspector, _TABLE)
        op.rename_table(_TABLE, _LEGACY_TMP)
        _rename_postgresql_constraints(bind)

    # A previous attempt may have stopped after rename/create/fill. The temp
    # table is always authoritative; an empty or partial canonical table is
    # never accepted as the source.
    inspector = sa.inspect(bind)
    canonical, normalized_active, discarded_ids = _read_legacy(bind, inspector)
    if inspector.has_table(_TABLE):
        op.drop_table(_TABLE)
    _create_canonical_table()
    bind.execute(
        sa.text(
            """INSERT INTO platform_maintenance
               (id, active, operation_id, operation_kind, owner, started_at, heartbeat_at)
               VALUES (:id, :active, :operation_id, :operation_kind, :owner,
                       :started_at, :heartbeat_at)"""
        ),
        canonical,
    )
    _validate_canonical_row(bind, canonical)
    op.drop_table(_LEGACY_TMP)

    if normalized_active or discarded_ids:
        details = {
            "operation_id": _RECOVERY_ID,
            "active_null_operation_id_normalized": normalized_active,
            "discarded_row_ids": discarded_ids,
            "migration_revision": revision,
        }
        _append_journal(details)
        _append_activity(bind, details)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_LEGACY_TMP):
        op.drop_table(_LEGACY_TMP)
    if inspector.has_table(_TABLE):
        op.drop_table(_TABLE)
