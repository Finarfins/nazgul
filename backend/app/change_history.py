from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import Column, Index, Integer, MetaData, String, Table, Text, insert, select, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

metadata = MetaData()
entity_change_logs = Table(
    "entity_change_logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("entity_type", String(50), nullable=False),
    Column("entity_id", Integer, nullable=False),
    Column("action", String(20), nullable=False),
    Column("before_json", Text),
    Column("after_json", Text),
    Column("changed_fields_json", Text),
    Column("request_id", String(64)),
    Column("actor_user_id", Integer),
    Column("actor_username", String(160)),
    Column("created_at", String(40), nullable=False),
    Column("restored_from_log_id", Integer),
)
Index(
    "ix_entity_change_lookup",
    entity_change_logs.c.company_id,
    entity_change_logs.c.entity_type,
    entity_change_logs.c.entity_id,
    entity_change_logs.c.id,
)
Index("ix_entity_change_request", entity_change_logs.c.request_id)

ALLOWED_RESTORE = {
    "customer": "customers",
    "supplier": "suppliers",
    "product": "products",
}


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _snapshot(row: Mapping[str, Any] | RowMapping | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _dump(value: Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)


def _changed_fields(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> list[str]:
    before_values = before or {}
    after_values = after or {}
    keys = set(before_values) | set(after_values)
    return sorted(key for key in keys if before_values.get(key) != after_values.get(key))


def record_change(
    db: Session,
    request: Request,
    *,
    company_id: int,
    entity_type: str,
    entity_id: int,
    action: str,
    before: Mapping[str, Any] | RowMapping | None,
    after: Mapping[str, Any] | RowMapping | None,
    restored_from_log_id: int | None = None,
) -> int:
    before_snapshot = _snapshot(before)
    after_snapshot = _snapshot(after)
    changed_fields = _changed_fields(before_snapshot, after_snapshot)
    user = getattr(request.state, "user", {}) or {}
    result = db.execute(
        insert(entity_change_logs).values(
            company_id=company_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before_json=_dump(before_snapshot),
            after_json=_dump(after_snapshot),
            changed_fields_json=_dump({"fields": changed_fields}),
            request_id=getattr(request.state, "request_id", None),
            actor_user_id=user.get("id"),
            actor_username=user.get("username"),
            created_at=(
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            ),
            restored_from_log_id=restored_from_log_id,
        )
    )
    return int(result.inserted_primary_key[0])


def _decode_history_row(row: RowMapping) -> dict[str, Any]:
    item = dict(row)
    for json_key in ("before_json", "after_json", "changed_fields_json"):
        value = item.pop(json_key, None)
        item[json_key.removesuffix("_json")] = json.loads(value) if value else None
    return item


def list_history(
    db: Session,
    company_id: int,
    entity_type: str,
    entity_id: int,
    limit: int = 200,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(limit, 500))
    rows = db.execute(
        select(entity_change_logs)
        .where(
            entity_change_logs.c.company_id == company_id,
            entity_change_logs.c.entity_type == entity_type,
            entity_change_logs.c.entity_id == entity_id,
        )
        .order_by(entity_change_logs.c.id.desc())
        .limit(bounded_limit)
    ).mappings().all()
    return [_decode_history_row(row) for row in rows]


def restore_deleted(
    db: Session,
    request: Request,
    *,
    company_id: int,
    log_id: int,
) -> dict[str, Any]:
    log = db.execute(
        select(entity_change_logs).where(
            entity_change_logs.c.id == log_id,
            entity_change_logs.c.company_id == company_id,
        )
    ).mappings().first()
    if not log or log["action"] != "delete" or not log["before_json"]:
        raise HTTPException(404, "Geri yüklenebilir silme kaydı bulunamadı")

    table_name = ALLOWED_RESTORE.get(log["entity_type"])
    if table_name is None:
        raise HTTPException(409, "Bu kayıt türü otomatik geri yüklemeyi desteklemiyor")

    payload = json.loads(log["before_json"])
    if int(payload.get("company_id", company_id)) != company_id:
        raise HTTPException(403, "Firma kapsamı uyuşmuyor")
    exists = db.execute(
        text(f"SELECT id FROM {table_name} WHERE id=:id AND company_id=:cid"),
        {"id": log["entity_id"], "cid": company_id},
    ).first()
    if exists:
        raise HTTPException(409, "Aynı kimlikte kayıt zaten mevcut")

    columns = list(payload)
    placeholders = ",".join(f":{column}" for column in columns)
    db.execute(
        text(f"INSERT INTO {table_name} ({','.join(columns)}) VALUES ({placeholders})"),
        payload,
    )
    restored = db.execute(
        text(f"SELECT * FROM {table_name} WHERE id=:id AND company_id=:cid"),
        {"id": log["entity_id"], "cid": company_id},
    ).mappings().one()
    record_change(
        db,
        request,
        company_id=company_id,
        entity_type=log["entity_type"],
        entity_id=log["entity_id"],
        action="restore",
        before=None,
        after=restored,
        restored_from_log_id=log_id,
    )
    return dict(restored)
