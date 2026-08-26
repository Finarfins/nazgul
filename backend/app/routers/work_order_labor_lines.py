"""Work order labor lines (Servis v2 FAZ-2).

Per-technician labor entries that can replace the v1 header labor
(``actual_hours × labor_rate``) as the billing source once they are approved.

Two rules dominate this module:

* **Rate freeze.** ``hourly_rate`` is written when the line is created — from
  the request, or defaulted from the work order header's ``labor_rate`` — and
  is never re-derived from the header or any profile afterwards. Changing the
  header rate later must not move the amount of a line that has already been
  recorded, so the stored rate is the only rate billing ever reads.
* **No double counting.** Approval is what promotes lines to the billing
  source. :func:`app.billing_service.build_invoice_summary` bills either the
  header or the approved lines, never both — see the note there.

Mutations follow the FAZ-1 freeze protocol exactly: they are refused on a
terminal work order, on a work order whose invoice exists (via the central
``ensure_work_order_unbilled`` predicate — there is deliberately no
``is_invoiced`` flag), and on a SCHEDULED work order, which has not started.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..change_history import record_change
from ..db import get_db
from ..money import money, quantity
from ..tenancy import company_id
from ..work_order_labor_schemas import LaborLineWrite
from .work_orders import ensure_work_order_mutable, ensure_work_order_unbilled

router = APIRouter(
    prefix="/work-orders/{work_order_id}/labor-lines", tags=["work-order-labor-lines"]
)

# Approving labor is an authorisation decision about money, so it is limited to
# the administrative roles. The path-prefix middleware only gets us as far as
# ``sales`` (any work-order write), which is why the check is repeated here.
APPROVAL_ROLES = {"admin", "yonetici"}

LINE_COLUMNS = """l.id,l.company_id,l.work_order_id,l.technician_user_id,l.work_date,
    l.hours,l.hourly_rate,l.line_status,l.approved_by,l.approved_at,l.note,
    l.created_by,l.created_at,l.updated_at"""


def _line_total(hours: Any, hourly_rate: Any) -> Decimal:
    """Amount of one labor line: quantity/money normalised, then rounded once.

    The same per-component rounding the parts lines use, so a line's stored
    amount and the invoice item built from it can never drift.
    """
    return money(quantity(hours) * money(hourly_rate))


def _require_mutable_work_order(db: Session, cid: int, work_order_id: int) -> dict[str, Any]:
    lock_clause = " FOR UPDATE" if db.get_bind().dialect.name == "postgresql" else ""
    row = db.execute(
        text(
            "SELECT status,labor_rate FROM work_orders "
            f"WHERE id=:id AND company_id=:cid{lock_clause}"
        ),
        {"id": work_order_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "İş emri bulunamadı")
    status = str(row["status"])
    ensure_work_order_mutable(status)
    if status == "SCHEDULED":
        raise HTTPException(
            409, "Planlanmış iş emrine işçilik eklenemez; önce iş emrini açın"
        )
    ensure_work_order_unbilled(db, cid, work_order_id)
    return dict(row)


def _require_work_order(db: Session, cid: int, work_order_id: int) -> None:
    row = db.execute(
        text("SELECT 1 FROM work_orders WHERE id=:id AND company_id=:cid"),
        {"id": work_order_id, "cid": cid},
    ).first()
    if not row:
        raise HTTPException(404, "İş emri bulunamadı")


def _validate_technician(db: Session, cid: int, technician_id: int) -> None:
    """Same membership rule the work order's own technician must satisfy."""
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


def _line_row(
    db: Session, cid: int, work_order_id: int, line_id: int, *, lock: bool = False
) -> dict[str, Any]:
    lock_clause = " FOR UPDATE OF l" if lock and db.get_bind().dialect.name == "postgresql" else ""
    row = db.execute(
        text(
            f"""SELECT {LINE_COLUMNS},
            u.username technician_username,u.display_name technician_name,
            a.username approved_by_username,a.display_name approved_by_name
            FROM work_order_labor_lines l
            JOIN app_users u ON u.id=l.technician_user_id
            LEFT JOIN app_users a ON a.id=l.approved_by
            WHERE l.id=:id AND l.work_order_id=:work_order_id
              AND l.company_id=:cid{lock_clause}"""
        ),
        {"id": line_id, "work_order_id": work_order_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "İşçilik satırı bulunamadı")
    line = dict(row)
    line["line_total"] = _line_total(line["hours"], line["hourly_rate"])
    return line


def void_labor_lines(db: Session, cid: int, work_order_id: int) -> int:
    """VOID every non-void labor line of a work order.

    Called from the work-order cancellation, inside that single transaction:
    cancelling a work order must not leave DRAFT or APPROVED labor behind that
    could later be billed. Returns the number of rows voided.
    """
    result = db.execute(
        text(
            """UPDATE work_order_labor_lines SET line_status='VOID',updated_at=:now
            WHERE company_id=:cid AND work_order_id=:id AND line_status<>'VOID'"""
        ),
        {"cid": cid, "id": work_order_id, "now": utcnow()},
    )
    return int(result.rowcount or 0)


@router.get("")
def list_labor_lines(
    work_order_id: int,
    request: Request,
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    _require_work_order(db, cid, work_order_id)
    params: dict[str, Any] = {"work_order_id": work_order_id, "cid": cid}
    # Only the optional status filter is dynamic; the tenant predicate stays
    # literal in the statement so the static scoping guard can verify it.
    status_sql = ""
    if status:
        normalized = status.strip().upper()
        if normalized not in {"DRAFT", "APPROVED", "VOID"}:
            raise HTTPException(400, "Geçersiz işçilik satırı durumu")
        status_sql = " AND l.line_status=:status"
        params["status"] = normalized
    rows = db.execute(
        text(
            f"""SELECT {LINE_COLUMNS},
            u.username technician_username,u.display_name technician_name,
            a.username approved_by_username,a.display_name approved_by_name
            FROM work_order_labor_lines l
            JOIN app_users u ON u.id=l.technician_user_id
            LEFT JOIN app_users a ON a.id=l.approved_by
            WHERE l.work_order_id=:work_order_id AND l.company_id=:cid{status_sql}
            ORDER BY l.id"""
        ),
        params,
    ).mappings().all()
    lines = []
    approved_total = Decimal("0")
    approved_count = 0
    for row in rows:
        line = dict(row)
        line["line_total"] = _line_total(line["hours"], line["hourly_rate"])
        if str(line["line_status"]) == "APPROVED":
            approved_total += line["line_total"]
            approved_count += 1
        lines.append(line)
    return {
        "items": lines,
        "approved_total": money(approved_total),
        # Mirrors the billing rule (see build_invoice_summary) so the UI can
        # state which source an invoice would use without re-deciding it. Note
        # this reflects the *filtered* result when a status filter is applied.
        "labor_source": "lines" if approved_count else "header",
    }


@router.post("", status_code=201)
def create_labor_line(
    work_order_id: int,
    payload: LaborLineWrite,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    work_order = _require_mutable_work_order(db, cid, work_order_id)
    _validate_technician(db, cid, payload.technician_user_id)
    # RATE FREEZE: resolved once, here. When the caller omits it the header's
    # current labor_rate is copied into the row; from this point the stored
    # value is the only rate this line will ever be billed at.
    hourly_rate = money(
        payload.hourly_rate if payload.hourly_rate is not None else work_order["labor_rate"]
    )
    now = utcnow()
    user = getattr(request.state, "user", {}) or {}
    try:
        line_id = db.execute(
            text(
                """INSERT INTO work_order_labor_lines(
                company_id,work_order_id,technician_user_id,work_date,hours,hourly_rate,
                line_status,note,created_by,created_at,updated_at
                ) VALUES(
                :cid,:work_order_id,:technician_user_id,:work_date,:hours,:hourly_rate,
                'DRAFT',:note,:created_by,:now,:now
                ) RETURNING id"""
            ),
            {
                "cid": cid,
                "work_order_id": work_order_id,
                "technician_user_id": payload.technician_user_id,
                "work_date": payload.work_date or now,
                "hours": quantity(payload.hours),
                "hourly_rate": hourly_rate,
                "note": payload.note,
                "created_by": int(user["id"]),
                "now": now,
            },
        ).scalar_one()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "İşçilik satırı kaydedilemedi") from exc
    except DataError as exc:
        db.rollback()
        raise HTTPException(400, "Sayısal değer izin verilen aralığın dışında") from exc
    after = _line_row(db, cid, work_order_id, int(line_id))
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="work_order_labor_line",
        entity_id=int(line_id),
        action="create",
        before=None,
        after=after,
    )
    db.commit()
    return after


@router.put("/{line_id}")
def update_labor_line(
    work_order_id: int,
    line_id: int,
    payload: LaborLineWrite,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    _require_mutable_work_order(db, cid, work_order_id)
    before = _line_row(db, cid, work_order_id, line_id, lock=True)
    status = str(before["line_status"])
    if status != "DRAFT":
        # An approved line is a recorded financial fact: correct it by voiding
        # it and adding a replacement, so both remain in the audit trail.
        raise HTTPException(
            409,
            "Yalnız taslak işçilik satırı düzenlenebilir; onaylı satır için "
            "satırı iptal edip yenisini ekleyin",
        )
    _validate_technician(db, cid, payload.technician_user_id)
    # The rate stays frozen unless the caller explicitly sends a new one; it is
    # never silently refreshed from the header.
    hourly_rate = money(
        payload.hourly_rate if payload.hourly_rate is not None else before["hourly_rate"]
    )
    try:
        db.execute(
            text(
                """UPDATE work_order_labor_lines SET
                technician_user_id=:technician_user_id,work_date=:work_date,
                hours=:hours,hourly_rate=:hourly_rate,note=:note,updated_at=:now
                WHERE id=:id AND work_order_id=:work_order_id AND company_id=:cid
                  AND line_status='DRAFT'"""
            ),
            {
                "technician_user_id": payload.technician_user_id,
                "work_date": payload.work_date or before["work_date"],
                "hours": quantity(payload.hours),
                "hourly_rate": hourly_rate,
                "note": payload.note,
                "now": utcnow(),
                "id": line_id,
                "work_order_id": work_order_id,
                "cid": cid,
            },
        )
    except DataError as exc:
        db.rollback()
        raise HTTPException(400, "Sayısal değer izin verilen aralığın dışında") from exc
    after = _line_row(db, cid, work_order_id, line_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="work_order_labor_line",
        entity_id=line_id,
        action="update",
        before=before,
        after=after,
    )
    db.commit()
    return after


@router.post("/{line_id}/approve")
def approve_labor_line(
    work_order_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    user = getattr(request.state, "user", {}) or {}
    if str(user.get("role")) not in APPROVAL_ROLES:
        raise HTTPException(403, "İşçilik satırını onaylama yetkiniz yok")
    _require_mutable_work_order(db, cid, work_order_id)
    before = _line_row(db, cid, work_order_id, line_id, lock=True)
    now = utcnow()
    # Compare-and-set: only a DRAFT row may be promoted, and only once. A
    # concurrent second approval finds rowcount 0 and is rejected instead of
    # overwriting the first approver.
    result = db.execute(
        text(
            """UPDATE work_order_labor_lines
            SET line_status='APPROVED',approved_by=:uid,approved_at=:now,updated_at=:now
            WHERE id=:id AND work_order_id=:work_order_id AND company_id=:cid
              AND line_status='DRAFT'"""
        ),
        {
            "uid": int(user["id"]),
            "now": now,
            "id": line_id,
            "work_order_id": work_order_id,
            "cid": cid,
        },
    )
    if not result.rowcount:
        db.rollback()
        raise HTTPException(
            409, "İşçilik satırı taslak durumda değil veya eş zamanlı olarak değiştirildi"
        )
    after = _line_row(db, cid, work_order_id, line_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="work_order_labor_line",
        entity_id=line_id,
        action="approve",
        before=before,
        after=after,
    )
    db.commit()
    return after


@router.delete("/{line_id}", status_code=204)
def void_labor_line(
    work_order_id: int,
    line_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Soft delete: a line is voided, never removed, so the audit trail holds."""
    cid = company_id(request)
    _require_mutable_work_order(db, cid, work_order_id)
    before = _line_row(db, cid, work_order_id, line_id, lock=True)
    status = str(before["line_status"])
    if status == "VOID":
        raise HTTPException(409, "İşçilik satırı zaten iptal edilmiş")
    if status == "APPROVED" and str(
        (getattr(request.state, "user", {}) or {}).get("role")
    ) not in APPROVAL_ROLES:
        # Voiding approved labor removes money from the invoice, so it needs the
        # same authority that put it there.
        raise HTTPException(403, "Onaylı işçilik satırını iptal etme yetkiniz yok")
    result = db.execute(
        text(
            """UPDATE work_order_labor_lines SET line_status='VOID',updated_at=:now
            WHERE id=:id AND work_order_id=:work_order_id AND company_id=:cid
              AND line_status=:current_status"""
        ),
        {
            "now": utcnow(),
            "id": line_id,
            "work_order_id": work_order_id,
            "cid": cid,
            "current_status": status,
        },
    )
    if not result.rowcount:
        db.rollback()
        raise HTTPException(409, "İşçilik satırı eş zamanlı olarak değiştirildi")
    after = _line_row(db, cid, work_order_id, line_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="work_order_labor_line",
        entity_id=line_id,
        action="void",
        before=before,
        after=after,
    )
    db.commit()
