from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..change_history import record_change
from ..db import get_db
from ..inventory import adjust_warehouse_stock, release_warehouse_reservation
from ..money import line_amount, money, quantity
from ..tenancy import company_id
from ..work_order_part_schemas import WorkOrderPartWrite
from ..work_order_stock import (
    LINE_ISSUED,
    LINE_RESERVED,
    LINE_RETURNED,
    MODE_RESERVE,
    issue_available_part_stock,
    issue_part,
    issue_part_immediately,
    reserve_part,
    return_part,
    service_parts_mode,
)
from .work_orders import ensure_work_order_mutable, ensure_work_order_unbilled

router = APIRouter(prefix="/work-orders/{work_order_id}/parts", tags=["work-order-parts"])


def _require_work_order(
    db: Session,
    cid: int,
    work_order_id: int,
    *,
    mutable: bool = False,
) -> None:
    lock_clause = " FOR UPDATE" if mutable and db.get_bind().dialect.name == "postgresql" else ""
    row = db.execute(
        text(f"SELECT status FROM work_orders WHERE id=:id AND company_id=:cid{lock_clause}"),
        {"id": work_order_id, "cid": cid},
    ).first()
    if not row:
        raise HTTPException(404, "İş emri bulunamadı")
    if mutable:
        ensure_work_order_mutable(str(row[0]))
        if str(row[0]) == "SCHEDULED":
            # A planned work order is not in the workshop yet: no parts (and
            # therefore no stock movement) until it is opened.
            raise HTTPException(409, "Planlanmış iş emrine parça eklenemez; önce iş emrini açın")
        ensure_work_order_unbilled(db, cid, work_order_id)


def _validate_references(db: Session, cid: int, product_id: int, warehouse_id: int) -> None:
    product = db.execute(text("SELECT 1 FROM products WHERE id=:id AND company_id=:cid AND active=TRUE"), {"id": product_id, "cid": cid}).first()
    warehouse = db.execute(text("SELECT 1 FROM warehouses WHERE id=:id AND company_id=:cid AND is_active=TRUE"), {"id": warehouse_id, "cid": cid}).first()
    if not product:
        raise HTTPException(400, "Aktif ürün bulunamadı veya firmaya ait değil")
    if not warehouse:
        raise HTTPException(400, "Depo bulunamadı veya firmaya ait değil")


def _total(payload: WorkOrderPartWrite) -> Decimal:
    # Canonical per-component rounding shared with billing_service and invoice
    # generation, so the stored total_price always equals the invoice line.
    return line_amount(
        payload.quantity, payload.unit_price, payload.discount, payload.tax_rate
    )


def _part(db: Session, cid: int, work_order_id: int, part_id: int) -> dict[str, Any]:
    row = db.execute(text("""SELECT wp.*,p.name product_name,w.name warehouse_name
        FROM work_order_parts wp
        JOIN products p ON p.id=wp.product_id AND p.company_id=wp.company_id
        JOIN warehouses w ON w.id=wp.warehouse_id AND w.company_id=wp.company_id
        WHERE wp.id=:part_id AND wp.work_order_id=:work_order_id AND wp.company_id=:cid"""),
        {"part_id": part_id, "work_order_id": work_order_id, "cid": cid}).mappings().first()
    if not row:
        raise HTTPException(404, "İş emri parçası bulunamadı")
    return dict(row)


def _stock(db: Session, cid: int, warehouse_id: int, product_id: int, delta: Decimal) -> None:
    if delta < 0:
        issue_available_part_stock(db, cid, warehouse_id, product_id, -delta)
        return
    try:
        adjust_warehouse_stock(db, cid, warehouse_id, product_id, delta, allow_negative=False)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _ensure_line_editable(part: dict[str, Any]) -> None:
    """A returned line is terminal: correct it by adding a new line instead."""
    if str(part["line_status"]) == LINE_RETURNED:
        raise HTTPException(409, "İade edilmiş parça satırı değiştirilemez; yeni satır ekleyin")


def _move_reservation(
    db: Session,
    cid: int,
    work_order_id: int,
    before: dict[str, Any],
    payload: WorkOrderPartWrite,
    new_quantity: Decimal,
) -> None:
    """Re-point / resize a reservation, releasing before claiming.

    Releasing first keeps a same-row resize from having to hold two claims at
    once, so shrinking never fails, while growing is still guarded against the
    real availability by ``reserve_part``. Both run inside the request's single
    transaction, so a rejected claim rolls the release back with it.
    """
    old_reserved = quantity(before["reserved_quantity"] or before["quantity"])
    release_warehouse_reservation(
        db, cid, int(before["warehouse_id"]), int(before["product_id"]), old_reserved
    )
    reserve_part(
        db,
        cid,
        work_order_id,
        {"warehouse_id": payload.warehouse_id, "product_id": payload.product_id},
        new_quantity,
    )


@router.get("")
def list_parts(work_order_id: int, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    _require_work_order(db, cid, work_order_id)
    rows = db.execute(text("""SELECT wp.*,p.name product_name,w.name warehouse_name
        FROM work_order_parts wp
        JOIN products p ON p.id=wp.product_id AND p.company_id=wp.company_id
        JOIN warehouses w ON w.id=wp.warehouse_id AND w.company_id=wp.company_id
        WHERE wp.work_order_id=:work_order_id AND wp.company_id=:cid ORDER BY wp.id"""),
        {"work_order_id": work_order_id, "cid": cid}).mappings().all()
    parts = [dict(row) for row in rows]
    return {"items": parts, "parts_total": money(sum((money(row["total_price"]) for row in rows), Decimal("0")))}


def create_part_in_transaction(
    db: Session,
    request: Request,
    cid: int,
    work_order_id: int,
    payload: WorkOrderPartWrite,
) -> dict[str, Any]:
    _require_work_order(db, cid, work_order_id, mutable=True)
    _validate_references(db, cid, payload.product_id, payload.warehouse_id)
    now = utcnow()
    # RESERVE mode only claims availability. IMMEDIATE issues on add, but its
    # stock decrement also respects claims owned by other work orders.
    reserve_mode = service_parts_mode(db, cid) == MODE_RESERVE
    line_quantity = quantity(payload.quantity)
    try:
        part_id = db.execute(text("""INSERT INTO work_order_parts(
            company_id,work_order_id,product_id,warehouse_id,quantity,unit_price,discount,tax_rate,total_price,line_status,reserved_quantity,created_at,updated_at)
            VALUES(:cid,:work_order_id,:product_id,:warehouse_id,:quantity,:unit_price,:discount,:tax_rate,:total_price,:line_status,:reserved_quantity,:now,:now)
            RETURNING id"""), {**payload.model_dump(), "cid": cid, "work_order_id": work_order_id,
            "quantity": line_quantity, "unit_price": money(payload.unit_price), "total_price": _total(payload),
            "line_status": LINE_RESERVED if reserve_mode else LINE_ISSUED,
            "reserved_quantity": line_quantity if reserve_mode else Decimal("0"),
            "now": now}).scalar_one()
        if reserve_mode:
            reserve_part(
                db, cid, work_order_id,
                {"warehouse_id": payload.warehouse_id, "product_id": payload.product_id},
                line_quantity,
            )
        else:
            _stock(db, cid, payload.warehouse_id, payload.product_id, -line_quantity)
            issue_part_immediately(
                db, cid, work_order_id, int(part_id),
                payload.warehouse_id, payload.product_id, line_quantity,
            )
        after = _part(db, cid, work_order_id, int(part_id))
        record_change(db, request, company_id=cid, entity_type="work_order_part", entity_id=int(part_id), action="create", before=None, after=after)
        return after
    except IntegrityError as exc:
        raise HTTPException(409, "Bu ürün ve depo iş emrinde zaten kayıtlı") from exc
    except DataError as exc:
        raise HTTPException(400, "Sayısal değer izin verilen aralığın dışında") from exc


@router.post("", status_code=201)
def create_part(work_order_id: int, payload: WorkOrderPartWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    try:
        after = create_part_in_transaction(db, request, cid, work_order_id, payload)
        db.commit()
        return after
    except HTTPException:
        db.rollback()
        raise


@router.put("/{part_id}")
def update_part(work_order_id: int, part_id: int, payload: WorkOrderPartWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    _require_work_order(db, cid, work_order_id, mutable=True)
    before = _part(db, cid, work_order_id, part_id)
    _ensure_line_editable(before)
    _validate_references(db, cid, payload.product_id, payload.warehouse_id)
    reserved_line = str(before["line_status"]) == LINE_RESERVED
    new_quantity = quantity(payload.quantity)
    try:
        if reserved_line:
            # Reservation edit: move the claim, never the physical stock.
            _move_reservation(db, cid, work_order_id, before, payload, new_quantity)
        elif before["product_id"] == payload.product_id and before["warehouse_id"] == payload.warehouse_id:
            _stock(db, cid, payload.warehouse_id, payload.product_id, quantity(before["quantity"]) - new_quantity)
        else:
            _stock(db, cid, int(before["warehouse_id"]), int(before["product_id"]), quantity(before["quantity"]))
            _stock(db, cid, payload.warehouse_id, payload.product_id, -new_quantity)
        db.execute(text("""UPDATE work_order_parts SET product_id=:product_id,warehouse_id=:warehouse_id,
            quantity=:quantity,unit_price=:unit_price,discount=:discount,tax_rate=:tax_rate,total_price=:total_price,
            reserved_quantity=:reserved_quantity,updated_at=:now
            WHERE id=:part_id AND work_order_id=:work_order_id AND company_id=:cid"""),
            {**payload.model_dump(), "part_id": part_id, "work_order_id": work_order_id, "cid": cid,
             "quantity": new_quantity, "unit_price": money(payload.unit_price), "total_price": _total(payload),
             "reserved_quantity": new_quantity if reserved_line else Decimal("0"), "now": utcnow()})
        after = _part(db, cid, work_order_id, part_id)
        record_change(db, request, company_id=cid, entity_type="work_order_part", entity_id=part_id, action="update", before=before, after=after)
        db.commit()
        return after
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Bu ürün ve depo iş emrinde zaten kayıtlı") from exc
    except DataError as exc:
        db.rollback()
        raise HTTPException(400, "Sayısal değer izin verilen aralığın dışında") from exc


@router.delete("/{part_id}", status_code=204)
def delete_part(work_order_id: int, part_id: int, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    _require_work_order(db, cid, work_order_id, mutable=True)
    before = _part(db, cid, work_order_id, part_id)
    status = str(before["line_status"])
    if status == LINE_RESERVED:
        # Nothing left the warehouse, so only the claim is dropped.
        release_warehouse_reservation(
            db, cid, int(before["warehouse_id"]), int(before["product_id"]),
            quantity(before["reserved_quantity"] or before["quantity"]),
        )
    elif status == LINE_ISSUED:
        _stock(db, cid, int(before["warehouse_id"]), int(before["product_id"]), quantity(before["quantity"]))
    db.execute(text("DELETE FROM work_order_parts WHERE id=:id AND work_order_id=:work_order_id AND company_id=:cid"),
               {"id": part_id, "work_order_id": work_order_id, "cid": cid})
    record_change(db, request, company_id=cid, entity_type="work_order_part", entity_id=part_id, action="delete", before=before, after=None)
    db.commit()


@router.post("/{part_id}/issue")
def issue_reserved_part(
    work_order_id: int, part_id: int, request: Request, db: Session = Depends(get_db)
):
    """RESERVED -> ISSUED: hand the reserved parts to the job.

    The compare-and-set on ``line_status`` is what makes this safe against a
    concurrent second issue and against a concurrent cancellation: whichever
    statement wins moves the line out of RESERVED, and the loser finds
    ``rowcount == 0``.
    """
    cid = company_id(request)
    _require_work_order(db, cid, work_order_id, mutable=True)
    before = _part(db, cid, work_order_id, part_id)
    if str(before["line_status"]) != LINE_RESERVED:
        raise HTTPException(409, "Yalnız rezerve parça satırı çıkışa dönüştürülebilir")
    try:
        result = db.execute(
            text(
                """UPDATE work_order_parts SET line_status=:issued,reserved_quantity=0,updated_at=:now
                WHERE id=:id AND work_order_id=:work_order_id AND company_id=:cid
                  AND line_status=:reserved"""
            ),
            {
                "issued": LINE_ISSUED, "reserved": LINE_RESERVED, "now": utcnow(),
                "id": part_id, "work_order_id": work_order_id, "cid": cid,
            },
        )
        if not result.rowcount:
            db.rollback()
            raise HTTPException(409, "Parça satırı eş zamanlı olarak değiştirildi")
        issue_part(db, cid, work_order_id, before)
        after = _part(db, cid, work_order_id, part_id)
        record_change(db, request, company_id=cid, entity_type="work_order_part",
                      entity_id=part_id, action="issue", before=before, after=after)
        db.commit()
        return after
    except HTTPException:
        db.rollback()
        raise


@router.post("/{part_id}/return")
def return_used_part(
    work_order_id: int, part_id: int, request: Request, db: Session = Depends(get_db)
):
    """RESERVED or ISSUED -> RETURNED. Terminal: correct by adding a new line."""
    cid = company_id(request)
    _require_work_order(db, cid, work_order_id, mutable=True)
    before = _part(db, cid, work_order_id, part_id)
    status = str(before["line_status"])
    if status == LINE_RETURNED:
        raise HTTPException(409, "Parça satırı zaten iade edilmiş")
    try:
        result = db.execute(
            text(
                """UPDATE work_order_parts SET line_status=:returned,reserved_quantity=0,updated_at=:now
                WHERE id=:id AND work_order_id=:work_order_id AND company_id=:cid
                  AND line_status=:current"""
            ),
            {
                "returned": LINE_RETURNED, "current": status, "now": utcnow(),
                "id": part_id, "work_order_id": work_order_id, "cid": cid,
            },
        )
        if not result.rowcount:
            db.rollback()
            raise HTTPException(409, "Parça satırı eş zamanlı olarak değiştirildi")
        return_part(db, cid, work_order_id, before, reason="manuel iade")
        after = _part(db, cid, work_order_id, part_id)
        record_change(db, request, company_id=cid, entity_type="work_order_part",
                      entity_id=part_id, action="return", before=before, after=after)
        db.commit()
        return after
    except HTTPException:
        db.rollback()
        raise
