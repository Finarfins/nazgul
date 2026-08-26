"""Servis v2 FAZ-3 — work-order part stock transitions.

One place owns the RESERVED → ISSUED → RETURNED lifecycle so the endpoints, the
cancellation path and any future caller move stock the same way:

* every physical movement writes a ``stock_movements`` row (the v1 gap: service
  part consumption never reached the movement ledger), and
* every movement is guarded by an idempotency key, so a retried request — or a
  cancellation re-entered after a partial failure — can never move stock twice.

Reservation itself is not a movement: it only claims availability, so it writes
no ledger row.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import utcnow
from .inventory import (
    adjust_warehouse_stock,
    issue_reserved_stock,
    release_warehouse_reservation,
    reserve_warehouse_stock,
    sync_product_stock,
    warehouse_stocks,
)
from .money import quantity as normalize_quantity

# Part line states. RETURNED is terminal for a line: correcting it means adding
# a new line, exactly like the labor lines in FAZ-2.
LINE_RESERVED = "RESERVED"
LINE_ISSUED = "ISSUED"
LINE_RETURNED = "RETURNED"

# Company setting. IMMEDIATE is the default and reproduces v1 exactly: adding a
# part issues it on the spot.
MODE_IMMEDIATE = "IMMEDIATE"
MODE_RESERVE = "RESERVE"
VALID_SERVICE_PARTS_MODES = {MODE_IMMEDIATE, MODE_RESERVE}

MOVEMENT_REFERENCE_TYPE = "work_order"


def service_parts_mode(db: Session, company_id: int) -> str:
    """Read the company's reservation setting, defaulting to IMMEDIATE."""
    row = db.execute(
        text("SELECT service_parts_mode FROM companies WHERE id=:cid"),
        {"cid": company_id},
    ).first()
    mode = str((row[0] if row else None) or MODE_IMMEDIATE).upper()
    return mode if mode in VALID_SERVICE_PARTS_MODES else MODE_IMMEDIATE


def _claim_event(
    db: Session,
    company_id: int,
    work_order_id: int,
    part_id: int,
    event_type: str,
    idempotency_key: str,
) -> bool:
    """Claim an idempotency key. False means this event already happened.

    The UNIQUE constraint is the actual guard; the savepoint keeps a losing
    insert from poisoning the caller's transaction (the cancellation runs many
    of these in one transaction and must survive an already-applied one).
    """
    try:
        with db.begin_nested():
            db.execute(
                text(
                    """INSERT INTO work_order_stock_events(
                    company_id,work_order_id,part_id,event_type,idempotency_key,created_at
                    ) VALUES(:cid,:work_order_id,:part_id,:event_type,:key,:now)"""
                ),
                {
                    "cid": company_id,
                    "work_order_id": work_order_id,
                    "part_id": part_id,
                    "event_type": event_type,
                    "key": idempotency_key,
                    "now": utcnow(),
                },
            )
    except IntegrityError:
        return False
    return True


def _write_movement(
    db: Session,
    company_id: int,
    *,
    warehouse_id: int,
    product_id: int,
    work_order_id: int,
    signed_quantity: Decimal,
    movement_type: str,
    note: str,
) -> None:
    db.execute(
        text(
            """INSERT INTO stock_movements(
            product_id,movement_type,quantity,movement_date,reference_type,
            reference_id,note,company_id,warehouse_id
            ) VALUES(:product_id,:movement_type,:quantity,:movement_date,
            :reference_type,:reference_id,:note,:company_id,:warehouse_id)"""
        ),
        {
            "product_id": product_id,
            "movement_type": movement_type,
            "quantity": signed_quantity,
            "movement_date": utcnow(),
            "reference_type": MOVEMENT_REFERENCE_TYPE,
            "reference_id": work_order_id,
            "note": note,
            "company_id": company_id,
            "warehouse_id": warehouse_id,
        },
    )


def reserve_part(
    db: Session, company_id: int, work_order_id: int, part: dict, amount: Decimal
) -> None:
    """Claim availability for a part line. Raises 409 when it is not available."""
    reserved = reserve_warehouse_stock(
        db,
        company_id,
        int(part["warehouse_id"]),
        int(part["product_id"]),
        normalize_quantity(amount),
    )
    if not reserved:
        raise HTTPException(
            409, "Depoda yeterli kullanılabilir stok yok (rezerve edilmiş miktar düşüldü)"
        )


def issue_available_part_stock(
    db: Session,
    company_id: int,
    warehouse_id: int,
    product_id: int,
    amount: Decimal,
) -> None:
    """Issue only unreserved stock for an IMMEDIATE work-order part."""
    delta = normalize_quantity(amount)
    quantity_expression = warehouse_stocks.c.quantity - delta
    updated = db.execute(
        update(warehouse_stocks)
        .where(
            warehouse_stocks.c.company_id == company_id,
            warehouse_stocks.c.warehouse_id == warehouse_id,
            warehouse_stocks.c.product_id == product_id,
            warehouse_stocks.c.quantity - warehouse_stocks.c.reserved_quantity >= delta,
        )
        .values(quantity=quantity_expression)
        .returning(warehouse_stocks.c.quantity)
    ).scalar_one_or_none()
    if updated is None:
        raise HTTPException(
            409, "Depoda yeterli kullanılabilir stok yok (rezerve edilmiş miktar düşüldü)"
        )
    sync_product_stock(db, company_id, product_id)


def issue_part(
    db: Session, company_id: int, work_order_id: int, part: dict
) -> bool:
    """RESERVED -> ISSUED: physically remove the reserved quantity.

    Returns False when the idempotency key was already claimed, i.e. the issue
    has already happened and nothing further must move.
    """
    part_id = int(part["id"])
    amount = normalize_quantity(part["reserved_quantity"] or part["quantity"])
    if not _claim_event(
        db, company_id, work_order_id, part_id, "issue", f"wo:{work_order_id}:part:{part_id}:issue"
    ):
        return False
    moved = issue_reserved_stock(
        db, company_id, int(part["warehouse_id"]), int(part["product_id"]), amount
    )
    if not moved:
        raise HTTPException(409, "Rezerve stok çıkışa dönüştürülemedi; stok değişmiş olabilir")
    _write_movement(
        db,
        company_id,
        warehouse_id=int(part["warehouse_id"]),
        product_id=int(part["product_id"]),
        work_order_id=work_order_id,
        signed_quantity=-amount,
        movement_type="out",
        note=f"İş emri #{work_order_id} parça çıkışı",
    )
    return True


def issue_part_immediately(
    db: Session, company_id: int, work_order_id: int, part_id: int,
    warehouse_id: int, product_id: int, amount: Decimal,
) -> None:
    """Write the idempotent ledger entry for the immediate-issue path."""
    if not _claim_event(
        db, company_id, work_order_id, part_id, "issue",
        f"wo:{work_order_id}:part:{part_id}:issue",
    ):
        return
    _write_movement(
        db,
        company_id,
        warehouse_id=warehouse_id,
        product_id=product_id,
        work_order_id=work_order_id,
        signed_quantity=-normalize_quantity(amount),
        movement_type="out",
        note=f"İş emri #{work_order_id} parça çıkışı",
    )


def return_part(
    db: Session, company_id: int, work_order_id: int, part: dict, *, reason: str
) -> bool:
    """Give a part line's stock back, whichever state it is in.

    * RESERVED -> only the claim is dropped; nothing was ever removed, so no
      movement row is written.
    * ISSUED -> the quantity goes back into the warehouse and a return movement
      is recorded.

    Returns False when the idempotency key was already claimed.
    """
    part_id = int(part["id"])
    status = str(part["line_status"])
    if status == LINE_RESERVED:
        amount = normalize_quantity(part["reserved_quantity"] or part["quantity"])
        if not _claim_event(
            db, company_id, work_order_id, part_id, "release",
            f"wo:{work_order_id}:part:{part_id}:release",
        ):
            return False
        release_warehouse_reservation(
            db, company_id, int(part["warehouse_id"]), int(part["product_id"]), amount
        )
        return True

    amount = normalize_quantity(part["quantity"])
    if not _claim_event(
        db, company_id, work_order_id, part_id, "return",
        f"wo:{work_order_id}:part:{part_id}:return",
    ):
        return False
    # allow_negative: a return can only increase stock, and refusing it because
    # of some unrelated negative balance would strand the cancellation.
    adjust_warehouse_stock(
        db,
        company_id,
        int(part["warehouse_id"]),
        int(part["product_id"]),
        amount,
        allow_negative=True,
    )
    _write_movement(
        db,
        company_id,
        warehouse_id=int(part["warehouse_id"]),
        product_id=int(part["product_id"]),
        work_order_id=work_order_id,
        signed_quantity=amount,
        movement_type="in",
        note=f"İş emri #{work_order_id} parça iadesi ({reason})",
    )
    return True
