"""Append-only machine hour-meter readings (Servis v2 FAZ-1).

``machines.working_hours`` is a projection of the latest accepted reading and
is updated in the same transaction as the insert. Readings are never updated or
deleted. A reading below the current projection fails closed (422) unless the
operator explicitly declares a meter replacement or a correction, both of which
require a reason; every accepted reading is recorded in the audit history.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..change_history import record_change
from ..db import get_db
from ..machine_service_schemas import DECREASE_ALLOWED_TYPES, MachineHourReadingCreate
from ..money import money
from ..tenancy import company_id
from .machines import _now as _machine_now

router = APIRouter(prefix="/machines/{machine_id}/hour-readings", tags=["machine-hour-readings"])

READING_COLUMNS = """r.id,r.company_id,r.machine_id,r.reading_hours,r.reading_date,
    r.source,r.work_order_id,r.reading_type,r.reason,r.created_by,r.created_at"""


def _machine_row(
    db: Session, cid: int, machine_id: int, *, lock: bool = False
) -> dict[str, Any]:
    lock_clause = " FOR UPDATE" if lock and db.get_bind().dialect.name == "postgresql" else ""
    row = db.execute(
        text(
            "SELECT id,customer_id,working_hours FROM machines "
            f"WHERE id=:id AND company_id=:cid{lock_clause}"
        ),
        {"id": machine_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Makine bulunamadı")
    return dict(row)


def _reading_row(db: Session, cid: int, reading_id: int) -> dict[str, Any]:
    row = db.execute(
        text(
            f"""SELECT {READING_COLUMNS},
            u.username created_by_username,u.display_name created_by_name,
            w.work_order_no work_order_no
            FROM machine_hour_readings r
            JOIN app_users u ON u.id=r.created_by
            LEFT JOIN work_orders w ON w.id=r.work_order_id AND w.company_id=r.company_id
            WHERE r.id=:id AND r.company_id=:cid"""
        ),
        {"id": reading_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Sayaç okuması bulunamadı")
    return dict(row)


@router.get("")
def list_hour_readings(
    machine_id: int,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    _machine_row(db, cid, machine_id)
    rows = db.execute(
        text(
            f"""SELECT {READING_COLUMNS},
            u.username created_by_username,u.display_name created_by_name,
            w.work_order_no work_order_no
            FROM machine_hour_readings r
            JOIN app_users u ON u.id=r.created_by
            LEFT JOIN work_orders w ON w.id=r.work_order_id AND w.company_id=r.company_id
            WHERE r.machine_id=:machine_id AND r.company_id=:cid
            ORDER BY r.reading_date DESC,r.id DESC LIMIT :limit OFFSET :offset"""
        ),
        {"machine_id": machine_id, "cid": cid, "limit": limit, "offset": offset},
    ).mappings().all()
    return [dict(row) for row in rows]


@router.post("", status_code=201)
def create_hour_reading(
    machine_id: int,
    payload: MachineHourReadingCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    # LOCK ORDER (see app.routers.machines): machines first, then work_orders —
    # this path locks the machine and only afterwards resolves the optional
    # source work order, so it never inverts the protocol. The lock also
    # serializes concurrent readings for the same machine, so the decrease check
    # always compares against the latest committed projection.
    machine = _machine_row(db, cid, machine_id, lock=True)

    source = "manual"
    if payload.work_order_id is not None:
        work_order = db.execute(
            text(
                "SELECT machine_id FROM work_orders WHERE id=:id AND company_id=:cid"
            ),
            {"id": payload.work_order_id, "cid": cid},
        ).first()
        if not work_order:
            raise HTTPException(404, "İş emri bulunamadı")
        if int(work_order[0]) != machine_id:
            raise HTTPException(409, "İş emri bu makineye ait değil")
        source = "work_order"

    # money() is the shared 2dp ROUND_HALF_UP normalizer; reading_hours is
    # stored as NUMERIC(12,2) like machines.working_hours.
    reading_hours = money(payload.reading_hours)
    current = machine.get("working_hours")
    if current is not None:
        current_hours = money(Decimal(str(current)))
        if reading_hours < current_hours and payload.reading_type not in DECREASE_ALLOWED_TYPES:
            raise HTTPException(
                422,
                "Yeni okuma mevcut sayaç değerinin altında; yalnız sayaç değişimi "
                "veya düzeltme türüyle ve açıklamayla kaydedilebilir",
            )

    user = getattr(request.state, "user", {}) or {}
    now = utcnow()
    reading_id = db.execute(
        text(
            """INSERT INTO machine_hour_readings(
            company_id,machine_id,reading_hours,reading_date,source,work_order_id,
            reading_type,reason,created_by,created_at
            ) VALUES(
            :cid,:machine_id,:reading_hours,:reading_date,:source,:work_order_id,
            :reading_type,:reason,:created_by,:now
            ) RETURNING id"""
        ),
        {
            "cid": cid,
            "machine_id": machine_id,
            "reading_hours": reading_hours,
            "reading_date": payload.reading_date or now,
            "source": source,
            "work_order_id": payload.work_order_id,
            "reading_type": payload.reading_type,
            "reason": payload.reason,
            "created_by": int(user["id"]),
            "now": now,
        },
    ).scalar_one()
    # Projection update in the same transaction: working_hours always mirrors
    # the last accepted reading (including replacements/corrections).
    db.execute(
        text(
            "UPDATE machines SET working_hours=:hours, updated_at=:now "
            "WHERE id=:id AND company_id=:cid"
        ),
        {"hours": reading_hours, "now": _machine_now(), "id": machine_id, "cid": cid},
    )
    after = _reading_row(db, cid, int(reading_id))
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="machine_hour_reading",
        entity_id=int(reading_id),
        action="create",
        before=None,
        after=after,
    )
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="machine",
        entity_id=machine_id,
        action="update",
        before={"id": machine_id, "working_hours": machine.get("working_hours")},
        after={"id": machine_id, "working_hours": reading_hours},
    )
    db.commit()
    return after
