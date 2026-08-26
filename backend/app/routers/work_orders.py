from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..activity_log import log_request_activity
from ..auth import utcnow
from ..change_history import record_change
from ..db import get_db
from ..document_engine import next_document_no
# Stock restoration on cancellation now goes through work_order_stock.return_part,
# which owns the RESERVED/ISSUED distinction, the movement ledger and the
# idempotency keys. This module no longer touches warehouse stock directly.
from ..money import money, quantity
from ..service_receivable_engine import (
    assert_work_order_receivable_inactive,
    reconcile_service_receivable,
    reverse_service_receivable,
)
from ..tenancy import company_id
from ..work_order_stock import LINE_RETURNED, return_part
from .machines import lock_machine_row
from ..work_order_schemas import (
    WORK_ORDER_STATES,
    WorkOrderCreate,
    WorkOrderReceivableReverse,
    WorkOrderStatusUpdate,
    WorkOrderUpdate,
    ServiceReceivableDocument,
)

router = APIRouter(prefix="/work-orders", tags=["work-orders"])

WORK_ORDER_COLUMNS = """w.id,w.company_id,w.machine_id,w.customer_id,w.technician_id,
    w.work_order_no,w.opened_at,w.scheduled_date,w.started_at,w.closed_at,w.completed_at,
    w.delivered_at,w.cancelled_at,w.status,w.priority,w.complaint,w.diagnosis,
    w.repair_summary,w.technician_notes,w.estimated_hours,w.actual_hours,
    w.labor_rate,w.total_labor_cost,w.warranty,w.warranty_type,w.warranty_percent,
    w.created_by,w.created_at,w.updated_at"""

WORK_ORDER_TOTALS = """w.total_labor_cost labor_total,
    COALESCE(wpt.parts_total,0) parts_total,
    w.total_labor_cost + COALESCE(wpt.parts_total,0) grand_total"""

# Pre-aggregated parts total per work order, LEFT JOINed once instead of the old
# correlated SUM(work_order_parts.total_price) that was evaluated twice per row
# (parts_total and again inside grand_total). Grouped and joined on
# (company_id, work_order_id) so results are identical, at most one row matches
# per work order (no fan-out), and it stays tenant-scoped by company_id.
WORK_ORDER_PARTS_TOTAL_JOIN = """LEFT JOIN (
        SELECT company_id, work_order_id, SUM(total_price) parts_total
        FROM work_order_parts WHERE line_status <> 'RETURNED'
        GROUP BY company_id, work_order_id) wpt
        ON wpt.company_id=w.company_id AND wpt.work_order_id=w.id"""

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    # SCHEDULED is reachable only as an initial state (creation); no transition
    # targets it. It is non-billable and takes no parts until it is opened.
    "SCHEDULED": {"OPEN", "CANCELLED"},
    "OPEN": {"IN_PROGRESS", "CANCELLED"},
    "IN_PROGRESS": {"WAITING_PARTS", "WAITING_CUSTOMER", "COMPLETED", "CANCELLED"},
    "WAITING_PARTS": {"IN_PROGRESS", "WAITING_CUSTOMER", "CANCELLED"},
    "WAITING_CUSTOMER": {"IN_PROGRESS", "WAITING_PARTS", "CANCELLED"},
    "COMPLETED": {"DELIVERED", "IN_PROGRESS"},
    "DELIVERED": set(),
    "CANCELLED": set(),
}
TERMINAL_WORK_ORDER_STATES = frozenset({"DELIVERED", "CANCELLED"})

# Aktivite özetleri panelde doğrudan okunur; durum kodları Türkçeleştirilir.
WORK_ORDER_STATUS_LABELS = {
    # SCHEDULED FAZ-1'de eklendi; etiketi eksik olduğu için panel özeti ham kodu
    # yazıyordu (.get fallback'i sayesinde hata değil, okunabilirlik kaybı).
    "SCHEDULED": "Planlandı",
    "OPEN": "Açık",
    "IN_PROGRESS": "Devam Ediyor",
    "WAITING_PARTS": "Parça Bekliyor",
    "WAITING_CUSTOMER": "Müşteri Bekliyor",
    "COMPLETED": "Tamamlandı",
    "DELIVERED": "Teslim Edildi",
    "CANCELLED": "İptal",
}


def ensure_work_order_mutable(status: str) -> None:
    if status in TERMINAL_WORK_ORDER_STATES:
        raise HTTPException(409, "Bu iş emri artık değiştirilemez.")


def ensure_work_order_unbilled(db: Session, cid: int, work_order_id: int) -> None:
    """Freeze a work order's money and parts once an invoice is issued.

    A work order has at most one invoice (unique ``work_order_id``). While that
    invoice is not cancelled, its stored line totals must not be able to drift
    from the frozen invoice, so reopening and any monetary/part mutation are
    rejected. Callers that also mutate state hold the ``FOR UPDATE`` lock on the
    work order, and invoice generation takes ``FOR SHARE`` of the same row, so
    the check and the issue serialize on PostgreSQL.
    """
    billed = db.execute(
        text(
            "SELECT 1 FROM invoices WHERE company_id=:cid AND work_order_id=:id "
            "AND status<>'CANCELLED'"
        ),
        {"cid": cid, "id": work_order_id},
    ).first()
    if billed:
        raise HTTPException(409, "Faturası kesilmiş iş emri değiştirilemez.")


def _restore_reserved_parts(db: Session, cid: int, work_order_id: int) -> None:
    """Give every part line of a cancelled work order its stock back.

    Runs inside the cancellation's single transaction. The caller holds the
    ``FOR UPDATE`` lock on the work order and has already won the status
    compare-and-set, so this executes once per work order; on top of that every
    line's release/return claims an idempotency key, so even a re-entered
    cancellation cannot return the same line twice.

    Each state gives back exactly what it took:

    * RESERVED -> the claim is dropped; nothing physically left, so no ledger row;
    * ISSUED   -> the quantity goes back and a return movement is written;
    * RETURNED -> already given back, skipped.

    The line is moved to RETURNED with a compare-and-set, so a concurrent
    issue/return on the same line cannot also act on it.
    """
    parts = db.execute(
        text(
            "SELECT id,product_id,warehouse_id,quantity,reserved_quantity,line_status "
            "FROM work_order_parts WHERE company_id=:cid AND work_order_id=:id"
        ),
        {"cid": cid, "id": work_order_id},
    ).mappings().all()
    now = utcnow()
    for part in parts:
        status = str(part["line_status"])
        if status == LINE_RETURNED:
            continue
        moved = db.execute(
            text(
                """UPDATE work_order_parts SET line_status=:returned,reserved_quantity=0,updated_at=:now
                WHERE id=:id AND company_id=:cid AND line_status=:current"""
            ),
            {
                "returned": LINE_RETURNED,
                "current": status,
                "now": now,
                "id": int(part["id"]),
                "cid": cid,
            },
        )
        if not moved.rowcount:
            # Another transaction just issued or returned this line; its own
            # stock handling applies and this cancellation must not double up.
            continue
        return_part(db, cid, work_order_id, dict(part), reason="iş emri iptali")


def _work_order_row(
    db: Session,
    cid: int,
    work_order_id: int,
    *,
    lock: bool = False,
) -> dict[str, Any]:
    lock_clause = " FOR UPDATE OF w" if lock and db.get_bind().dialect.name == "postgresql" else ""
    row = db.execute(
        text(
            f"""SELECT {WORK_ORDER_COLUMNS},{WORK_ORDER_TOTALS},c.name customer_name,
            m.brand machine_brand,m.model machine_model,m.serial_number machine_serial_number,
            t.username technician_username,t.display_name technician_name,
            creator.username created_by_username,creator.display_name created_by_name
            FROM work_orders w
            JOIN customers c ON c.id=w.customer_id AND c.company_id=w.company_id
            JOIN machines m ON m.id=w.machine_id AND m.company_id=w.company_id
            JOIN app_users t ON t.id=w.technician_id
            JOIN app_users creator ON creator.id=w.created_by
            {WORK_ORDER_PARTS_TOTAL_JOIN}
            WHERE w.id=:id AND w.company_id=:cid{lock_clause}"""
        ),
        {"id": work_order_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "İş emri bulunamadı")
    return dict(row)


def _machine_customer(
    db: Session, cid: int, machine_id: int, *, lock: bool = False
) -> int | None:
    """Read a machine's owner, optionally taking the protocol's machine lock.

    ``lock=True`` is mandatory on every path that derives a *written* work-order
    customer from this value: without it a concurrent ownership transfer can
    commit between this read and the write, leaving the work order billed to the
    previous owner. See the LOCK ORDER note in :mod:`app.routers.machines`.
    """
    lock_clause = " FOR UPDATE" if lock and db.get_bind().dialect.name == "postgresql" else ""
    row = db.execute(
        text(
            f"""SELECT customer_id FROM machines
            WHERE id=:id AND company_id=:cid{lock_clause}"""
        ),
        {"id": machine_id, "cid": cid},
    ).first()
    if row is None:
        raise HTTPException(404, "Makine bulunamadı")
    return int(row[0]) if row[0] is not None else None


def _validated_customer_id(
    db: Session,
    cid: int,
    machine_id: int,
    requested_customer_id: int | None,
    *,
    lock_machine: bool = False,
) -> int:
    owner_id = _machine_customer(db, cid, machine_id, lock=lock_machine)
    if owner_id is not None:
        if requested_customer_id is not None and requested_customer_id != owner_id:
            raise HTTPException(409, "Müşteri makinenin sahibiyle eşleşmiyor")
        return owner_id
    if requested_customer_id is None:
        raise HTTPException(400, "Müşterisi olmayan makine için müşteri seçilmelidir")
    exists = db.execute(
        text("SELECT 1 FROM customers WHERE id=:id AND company_id=:cid"),
        {"id": requested_customer_id, "cid": cid},
    ).first()
    if not exists:
        raise HTTPException(400, "Müşteri bulunamadı")
    return requested_customer_id


def _validate_technician(db: Session, cid: int, technician_id: int) -> None:
    exists = db.execute(
        text(
            """SELECT 1 FROM app_users u
            JOIN user_company_memberships membership ON membership.user_id=u.id
            WHERE u.id=:technician_id AND membership.company_id=:cid
              AND u.is_active=TRUE"""
        ),
        {"technician_id": technician_id, "cid": cid},
    ).first()
    if not exists:
        raise HTTPException(400, "Teknisyen bu firmada geçerli bir kullanıcı değil")


def _write_values(payload: WorkOrderCreate | WorkOrderUpdate) -> dict[str, Any]:
    values = payload.model_dump(exclude={"customer_id", "work_order_no", "status", "parts"})
    values["total_labor_cost"] = money(payload.actual_hours * payload.labor_rate)
    return values


def _raise_number_conflict(exc: IntegrityError) -> None:
    raise HTTPException(409, "İş emri numarası bu firmada zaten kayıtlı") from exc


@router.get("")
def list_work_orders(
    request: Request,
    status: str | None = None,
    technician: str = "",
    customer: str = "",
    machine: str = "",
    machine_id: int | None = Query(default=None, gt=0),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    q: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    conditions = ["w.company_id=:cid"]
    params: dict[str, Any] = {
        "cid": cid,
        "technician": f"%{technician.strip()}%",
        "customer": f"%{customer.strip()}%",
        "machine": f"%{machine.strip()}%",
        "q": f"%{q.strip()}%",
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    if status:
        normalized_status = status.strip().upper()
        if normalized_status not in WORK_ORDER_STATES:
            raise HTTPException(400, "Geçersiz iş emri durumu")
        conditions.append("w.status=:status")
        params["status"] = normalized_status
    if machine_id is not None:
        conditions.append("w.machine_id=:machine_id")
        params["machine_id"] = machine_id
    if date_from:
        conditions.append("w.opened_at>=:date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("w.opened_at<=:date_to")
        params["date_to"] = date_to
    conditions.extend(
        [
            "(LOWER(t.username) LIKE LOWER(:technician) OR LOWER(t.display_name) LIKE LOWER(:technician))",
            "LOWER(c.name) LIKE LOWER(:customer)",
            """(LOWER(COALESCE(m.brand,'')) LIKE LOWER(:machine)
            OR LOWER(COALESCE(m.model,'')) LIKE LOWER(:machine)
            OR COALESCE(m.serial_number,'') LIKE :machine
            OR COALESCE(m.chassis_number,'') LIKE :machine)""",
            """(LOWER(w.work_order_no) LIKE LOWER(:q)
            OR LOWER(COALESCE(w.complaint,'')) LIKE LOWER(:q)
            OR LOWER(COALESCE(w.diagnosis,'')) LIKE LOWER(:q)
            OR LOWER(COALESCE(w.repair_summary,'')) LIKE LOWER(:q)
            OR LOWER(COALESCE(w.technician_notes,'')) LIKE LOWER(:q)
            OR LOWER(c.name) LIKE LOWER(:q)
            OR LOWER(COALESCE(m.brand,'')) LIKE LOWER(:q)
            OR LOWER(COALESCE(m.model,'')) LIKE LOWER(:q))""",
        ]
    )
    joins = """FROM work_orders w
        JOIN customers c ON c.id=w.customer_id AND c.company_id=w.company_id
        JOIN machines m ON m.id=w.machine_id AND m.company_id=w.company_id
        JOIN app_users t ON t.id=w.technician_id
        JOIN app_users creator ON creator.id=w.created_by"""
    where_clause = " AND ".join(conditions)
    total = int(
        db.execute(
            text(f"SELECT COUNT(*) {joins} WHERE {where_clause}"), params
        ).scalar_one()
    )
    rows = db.execute(
        text(
            f"""SELECT {WORK_ORDER_COLUMNS},{WORK_ORDER_TOTALS},c.name customer_name,
            m.brand machine_brand,m.model machine_model,m.serial_number machine_serial_number,
            t.username technician_username,t.display_name technician_name,
            creator.username created_by_username,creator.display_name created_by_name
            {joins} {WORK_ORDER_PARTS_TOTAL_JOIN} WHERE {where_clause}
            ORDER BY w.opened_at DESC,w.id DESC LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return {
        "items": [dict(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


@router.get("/technicians")
def list_technicians(request: Request, db: Session = Depends(get_db)):
    """Active users of the requesting company, for the work-order technician
    picker. Read-only and tenant-scoped, so a ``sales`` role (which may create
    work orders but lacks the ``users`` permission that guards ``GET /users``)
    can still resolve technician names. Declared before ``/{work_order_id}`` so
    the literal path is not captured by the int path parameter."""
    cid = company_id(request)
    rows = db.execute(
        text(
            """SELECT u.id,u.username,u.display_name
            FROM app_users u
            JOIN user_company_memberships membership ON membership.user_id=u.id
            WHERE membership.company_id=:cid AND u.is_active=TRUE
            ORDER BY u.display_name"""
        ),
        {"cid": cid},
    ).mappings().all()
    return [dict(row) for row in rows]


@router.get("/{work_order_id}")
def work_order_detail(
    work_order_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    return _work_order_row(db, company_id(request), work_order_id)


@router.post("", status_code=201)
def create_work_order(
    payload: WorkOrderCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    # LOCK ORDER (see app.routers.machines): lock the machine, then derive the
    # customer from that locked read, then insert. Holding the lock across the
    # insert is what stops a concurrent transfer from passing its "no open work
    # order" check and committing a new owner while this row is being written
    # for the old one.
    customer_id = _validated_customer_id(
        db, cid, payload.machine_id, payload.customer_id, lock_machine=True
    )
    _validate_technician(db, cid, payload.technician_id)
    user = getattr(request.state, "user", {}) or {}
    created_by = int(user["id"])
    values = _write_values(payload)
    values["opened_at"] = values["opened_at"] or utcnow()
    work_order_no = payload.work_order_no or next_document_no(
        db, "work_orders", cid, "ISE"
    )
    values.update(
        {
            "cid": cid,
            "customer_id": customer_id,
            "work_order_no": work_order_no,
            # Creation may start OPEN or SCHEDULED (validated by the schema);
            # every other state is reachable only through the status endpoint.
            "status": payload.status,
            "created_by": created_by,
            "now": utcnow(),
        }
    )
    try:
        work_order_id = db.execute(
            text(
                """INSERT INTO work_orders(
                company_id,machine_id,customer_id,technician_id,work_order_no,opened_at,scheduled_date,status,priority,
                complaint,diagnosis,repair_summary,technician_notes,estimated_hours,
                actual_hours,labor_rate,total_labor_cost,warranty,warranty_type,warranty_percent,
                created_by,created_at,updated_at
                ) VALUES(
                :cid,:machine_id,:customer_id,:technician_id,:work_order_no,:opened_at,:scheduled_date,:status,:priority,
                :complaint,:diagnosis,:repair_summary,:technician_notes,:estimated_hours,
                :actual_hours,:labor_rate,:total_labor_cost,:warranty,:warranty_type,:warranty_percent,
                :created_by,:now,:now
                ) RETURNING id"""
            ),
            values,
        ).scalar_one()
        after = _work_order_row(db, cid, int(work_order_id))
        record_change(
            db,
            request,
            company_id=cid,
            entity_type="work_order",
            entity_id=int(work_order_id),
            action="create",
            before=None,
            after=after,
        )
        if payload.parts:
            from .work_order_parts import create_part_in_transaction

            for part in payload.parts:
                create_part_in_transaction(
                    db, request, cid, int(work_order_id), part
                )
        log_request_activity(
            db, request, cid, "work_order.create", "work_order", int(work_order_id),
            (
                f"{work_order_no} iş emrini oluşturdu — makine #{payload.machine_id}"
                f", müşteri #{customer_id}"
            ),
            {"work_order_no": work_order_no, "machine_id": payload.machine_id,
             "customer_id": customer_id, "priority": payload.priority},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        _raise_number_conflict(exc)
    return after


@router.put("/{work_order_id}")
def update_work_order(
    work_order_id: int,
    payload: WorkOrderUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    # LOCK ORDER (see app.routers.machines): the payload's machine is locked
    # BEFORE the work-order row, never after. This path rewrites the work
    # order's customer from the machine's owner, so it needs the machine lock;
    # taking it here keeps the machines -> work_orders order that the transfer
    # paths also follow, instead of inverting it into a deadlock.
    lock_machine_row(db, cid, payload.machine_id)
    # FOR UPDATE the work order before the billed-check so a labor/money mutation
    # serializes against concurrent invoice generation (which takes FOR SHARE of
    # the same row). Without the lock the billed-check could observe "unbilled"
    # while an invoice is being issued, letting the PUT drift the frozen totals.
    before = _work_order_row(db, cid, work_order_id, lock=True)
    ensure_work_order_mutable(str(before["status"]))
    ensure_work_order_unbilled(db, cid, work_order_id)
    customer_id = _validated_customer_id(db, cid, payload.machine_id, payload.customer_id)
    _validate_technician(db, cid, payload.technician_id)
    if str(before["status"]) == "SCHEDULED" and payload.scheduled_date is None:
        raise HTTPException(422, "Planlanmış iş emri için planlanan tarih zorunludur")
    values = _write_values(payload)
    values["opened_at"] = values["opened_at"] or before["opened_at"]
    values.update(
        {"id": work_order_id, "cid": cid, "customer_id": customer_id, "now": utcnow()}
    )
    db.execute(
        text(
            """UPDATE work_orders SET
            machine_id=:machine_id,customer_id=:customer_id,technician_id=:technician_id,
            opened_at=:opened_at,scheduled_date=:scheduled_date,
            priority=:priority,complaint=:complaint,
            diagnosis=:diagnosis,repair_summary=:repair_summary,
            technician_notes=:technician_notes,estimated_hours=:estimated_hours,
            actual_hours=:actual_hours,labor_rate=:labor_rate,
            total_labor_cost=:total_labor_cost,
            warranty=:warranty,warranty_type=:warranty_type,
            warranty_percent=:warranty_percent,updated_at=:now
            WHERE id=:id AND company_id=:cid"""
        ),
        values,
    )
    after = _work_order_row(db, cid, work_order_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="work_order",
        entity_id=work_order_id,
        action="update",
        before=before,
        after=after,
    )
    db.commit()
    return after


@router.patch("/{work_order_id}/status")
def update_work_order_status(
    work_order_id: int,
    payload: WorkOrderStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    before = _work_order_row(db, cid, work_order_id, lock=True)
    current_status = str(before["status"])
    if payload.status not in ALLOWED_TRANSITIONS[current_status]:
        raise HTTPException(
            409,
            f"{current_status} durumundan {payload.status} durumuna geçilemez",
        )
    if current_status == "COMPLETED" and payload.status == "IN_PROGRESS":
        # Reopening a completed work order would unfreeze its parts/labor; refuse
        # while a non-cancelled invoice exists. The FOR UPDATE lock on `before`
        # serializes this against concurrent invoice generation (FOR SHARE).
        ensure_work_order_unbilled(db, cid, work_order_id)
        assert_work_order_receivable_inactive(db, cid, work_order_id)
    now = utcnow()
    timestamps = {
        "started_at": before.get("started_at"),
        "closed_at": before.get("closed_at"),
        "completed_at": before.get("completed_at"),
        "delivered_at": before.get("delivered_at"),
        "cancelled_at": before.get("cancelled_at"),
    }
    if payload.status == "IN_PROGRESS":
        timestamps["started_at"] = timestamps["started_at"] or now
        if current_status == "COMPLETED":
            timestamps["completed_at"] = None
            timestamps["closed_at"] = None
    elif payload.status == "COMPLETED":
        timestamps["completed_at"] = now
        timestamps["closed_at"] = now
    elif payload.status == "DELIVERED":
        timestamps["delivered_at"] = now
        timestamps["closed_at"] = timestamps["closed_at"] or now
    elif payload.status == "CANCELLED":
        timestamps["cancelled_at"] = now
        timestamps["closed_at"] = now
    result = db.execute(
        text(
            """UPDATE work_orders SET status=:status,started_at=:started_at,
            closed_at=:closed_at,completed_at=:completed_at,
            delivered_at=:delivered_at,cancelled_at=:cancelled_at,updated_at=:now
            WHERE id=:id AND company_id=:cid AND status=:current_status"""
        ),
        {
            "id": work_order_id,
            "cid": cid,
            "status": payload.status,
            "current_status": current_status,
            "now": now,
            **timestamps,
        },
    )
    if not result.rowcount:
        db.rollback()
        raise HTTPException(409, "İş emri durumu eş zamanlı olarak değiştirildi")
    if payload.status == "COMPLETED":
        reconcile_service_receivable(
            db,
            cid,
            work_order_id,
            actor_id=int(request.state.user["id"]),
        )
    if payload.status == "CANCELLED":
        # Release reserved part stock and void any recorded labor in the same
        # transaction as the cancellation; if either fails the whole
        # cancellation rolls back. Cancelled work must not leave DRAFT or
        # APPROVED labor behind that a later invoice could pick up.
        try:
            _restore_reserved_parts(db, cid, work_order_id)
            from .work_order_labor_lines import void_labor_lines

            void_labor_lines(db, cid, work_order_id)
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:  # defensive: keep cancellation atomic
            db.rollback()
            raise HTTPException(
                409, "İş emri iptal edilirken stok geri yüklenemedi"
            ) from exc
    after = _work_order_row(db, cid, work_order_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="work_order",
        entity_id=work_order_id,
        action="status_change",
        before=before,
        after=after,
    )
    # İptal ayrı bir olay tipidir: panelde "kim iptal etti" sorusu, sıradan
    # durum ilerlemesinden ayrı filtrelenebilmelidir.
    cancelled = payload.status == "CANCELLED"
    log_request_activity(
        db, request, cid,
        "work_order.cancel" if cancelled else "work_order.status_change",
        "work_order", work_order_id,
        (
            f"{after['work_order_no']} iş emrini iptal etti"
            if cancelled
            else (
                f"{after['work_order_no']} iş emrinin durumunu "
                f"{WORK_ORDER_STATUS_LABELS.get(current_status, current_status)} → "
                f"{WORK_ORDER_STATUS_LABELS.get(payload.status, payload.status)} "
                "olarak değiştirdi"
            )
        ),
        {"status": {"old": current_status, "new": payload.status},
         "work_order_no": after["work_order_no"]},
    )
    db.commit()
    return after


@router.post(
    "/{work_order_id}/receivable/reverse",
    response_model=ServiceReceivableDocument,
)
def reverse_work_order_receivable(
    work_order_id: int,
    payload: WorkOrderReceivableReverse,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    try:
        reversal, was_created = reverse_service_receivable(
            db,
            cid,
            work_order_id,
            idempotency_key,
            actor_id=int(request.state.user["id"]),
            reason=payload.reason,
        )
        if was_created:
            record_change(
                db,
                request,
                company_id=cid,
                entity_type="receivable_charge_document",
                entity_id=int(reversal["id"]),
                action="reverse",
                before=None,
                after=reversal,
            )
            log_request_activity(
                db,
                request,
                cid,
                "work_order.receivable.reverse",
                "work_order",
                work_order_id,
                "İş emri cari borcunu ters kayıtla kapattı",
                {
                    "receivable_document_id": int(reversal["id"]),
                    "reason": payload.reason,
                },
            )
        db.commit()
        return reversal
    except Exception:
        db.rollback()
        raise
