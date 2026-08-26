"""Machine ownership transfer + append-only history (Servis v2 FAZ-1).

``machines.customer_id`` stays the compatibility projection every existing
reader keeps using; the history table records each transfer. A transfer is
refused while the machine has a non-terminal work order, because the work-order
customer validation (:func:`app.routers.work_orders._validated_customer_id`)
binds open work orders to the current owner.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..change_history import record_change
from ..db import get_db
from ..machine_service_schemas import MachineOwnershipTransfer
from ..tenancy import company_id
from .machines import (
    _fetch,
    _now,
    _serialize,
    ensure_no_open_work_orders,
    lock_machine_row,
    record_ownership_change,
)

router = APIRouter(prefix="/machines/{machine_id}", tags=["machine-ownership"])

HISTORY_COLUMNS = """h.id,h.company_id,h.machine_id,h.from_customer_id,h.to_customer_id,
    h.transfer_date,h.reason,h.created_by,h.created_at"""


@router.get("/ownership-history")
def list_ownership_history(
    machine_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    exists = db.execute(
        text("SELECT 1 FROM machines WHERE id=:id AND company_id=:cid"),
        {"id": machine_id, "cid": cid},
    ).first()
    if not exists:
        raise HTTPException(404, "Makine bulunamadı")
    rows = db.execute(
        text(
            f"""SELECT {HISTORY_COLUMNS},
            fc.name from_customer_name,tc.name to_customer_name,
            u.username created_by_username,u.display_name created_by_name
            FROM machine_ownership_history h
            LEFT JOIN customers fc ON fc.id=h.from_customer_id AND fc.company_id=h.company_id
            LEFT JOIN customers tc ON tc.id=h.to_customer_id AND tc.company_id=h.company_id
            LEFT JOIN app_users u ON u.id=h.created_by
            WHERE h.machine_id=:machine_id AND h.company_id=:cid
            ORDER BY h.id DESC"""
        ),
        {"machine_id": machine_id, "cid": cid},
    ).mappings().all()
    return [dict(row) for row in rows]


@router.post("/transfer-ownership")
def transfer_ownership(
    machine_id: int,
    payload: MachineOwnershipTransfer,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    # LOCK ORDER (see app.routers.machines): machines first, then work_orders.
    # The locked row is also the authority for from_customer_id below.
    machine = lock_machine_row(db, cid, machine_id)
    customer = db.execute(
        text("SELECT 1 FROM customers WHERE id=:id AND company_id=:cid"),
        {"id": payload.to_customer_id, "cid": cid},
    ).first()
    if not customer:
        raise HTTPException(404, "Müşteri bulunamadı")
    current_owner = machine["customer_id"]
    if current_owner is not None and int(current_owner) == payload.to_customer_id:
        raise HTTPException(409, "Makine zaten bu müşteriye kayıtlı")
    ensure_no_open_work_orders(db, cid, machine_id)

    before = _fetch(db, cid, machine_id)
    user = getattr(request.state, "user", {}) or {}
    record_ownership_change(
        db,
        cid,
        machine_id,
        from_customer_id=int(current_owner) if current_owner is not None else None,
        to_customer_id=payload.to_customer_id,
        transfer_date=payload.transfer_date or utcnow(),
        reason=payload.reason,
        created_by=int(user["id"]),
    )
    db.execute(
        text(
            "UPDATE machines SET customer_id=:customer_id, updated_at=:now "
            "WHERE id=:id AND company_id=:cid"
        ),
        {
            "customer_id": payload.to_customer_id,
            "now": _now(),
            "id": machine_id,
            "cid": cid,
        },
    )
    after = _fetch(db, cid, machine_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="machine",
        entity_id=machine_id,
        action="update",
        before=before,
        after=after,
    )
    db.commit()
    return _serialize(after)
