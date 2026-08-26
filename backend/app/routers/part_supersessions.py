"""V3 Parça Supersession — Increment 1.

When an OEM replaces a part number, old lookups must resolve to the current
part (real ag-DMS parts-counter behaviour). One manual supersession row per
old part (UNIQUE company_id+old_product_id keeps the chain a linked list),
and :func:`resolve_current_product` follows old -> new repeatedly with a
visited-set + iteration-cap guard so a corrupted chain can never loop forever.

Out of scope (later increments): bulk OEM catalog import, automatic
supersession feeds, price mapping across supersessions.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..db import get_db
from ..tenancy import company_id

router = APIRouter(tags=["part-supersessions"])

# Hard cap on chain hops. Real supersession chains are short (a handful of
# part-number generations); the cap is a belt-and-braces guard on top of the
# visited-set cycle detection. Both guards FAIL CLOSED: resolution never
# silently returns a truncated "current" part.
MAX_CHAIN_HOPS = 20


class SupersessionCycleError(Exception):
    """The stored chain loops back on itself (corrupted data)."""


class SupersessionChainTooLongError(Exception):
    """The stored chain exceeds MAX_CHAIN_HOPS (corrupted or absurd data)."""


class SupersessionCreate(BaseModel):
    old_product_id: int = Field(gt=0)
    new_product_id: int = Field(gt=0)
    note: str | None = None

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


def _product_row(db: Session, cid: int, product_id: int):
    row = db.execute(
        text(
            "SELECT id,name,product_code,barcode FROM products "
            "WHERE id=:id AND company_id=:cid"
        ),
        {"id": product_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Ürün bulunamadı")
    return row


def _next_supersession(db: Session, cid: int, product_id: int) -> int | None:
    value = db.execute(
        text(
            "SELECT new_product_id FROM part_supersessions "
            "WHERE company_id=:cid AND old_product_id=:pid"
        ),
        {"cid": cid, "pid": product_id},
    ).scalar()
    return int(value) if value is not None else None


def resolve_current_product(db: Session, cid: int, product_id: int) -> tuple[int, list[int]]:
    """Follow the supersession chain to the current part — fail closed.

    Returns ``(current_id, superseded_ids)`` where ``superseded_ids`` is the
    ordered list of part ids that were replaced along the way (oldest first,
    excluding the current part). Three outcomes are kept distinct — a broken
    chain NEVER silently resolves to a truncated "current" part:

    - chain ends within MAX_CHAIN_HOPS  -> return the current part,
    - a stored link revisits a part     -> raise SupersessionCycleError,
    - the chain exceeds MAX_CHAIN_HOPS  -> raise SupersessionChainTooLongError.

    A chain of exactly MAX_CHAIN_HOPS links still resolves (the post-loop
    check only rejects when a further link exists beyond the cap).
    """
    visited = {product_id}
    chain = [product_id]
    current = product_id
    for _ in range(MAX_CHAIN_HOPS):
        nxt = _next_supersession(db, cid, current)
        if nxt is None:
            return current, chain[:-1]
        if nxt in visited:
            raise SupersessionCycleError()
        visited.add(nxt)
        chain.append(nxt)
        current = nxt
    if _next_supersession(db, cid, current) is None:
        return current, chain[:-1]
    raise SupersessionChainTooLongError()


@router.post("/part-supersessions", status_code=201)
def create_supersession(payload: SupersessionCreate, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    if payload.old_product_id == payload.new_product_id:
        raise HTTPException(400, "Eski ve yeni parça aynı olamaz")
    _product_row(db, cid, payload.old_product_id)
    _product_row(db, cid, payload.new_product_id)

    # Cycle guard at create time: if the OLD part appears ANYWHERE on the
    # chain walked from the NEW part (not just at its resolved end), this row
    # would close a loop (e.g. C -> A on top of A -> B -> C). Traversal
    # failures fail closed too: a chain that already loops or exceeds the hop
    # cap safely rejects the new record instead of silently admitting it, so
    # cycle attempts beyond MAX_CHAIN_HOPS are also refused.
    try:
        resolved_from_new, superseded_from_new = resolve_current_product(
            db, cid, payload.new_product_id
        )
    except SupersessionCycleError:
        raise HTTPException(409, "Supersession zinciri bozuk: döngü tespit edildi; kayıt eklenemez")
    except SupersessionChainTooLongError:
        raise HTTPException(409, "Supersession zinciri izin verilen uzunluğu aşıyor; kayıt eklenemez")
    if payload.old_product_id in {*superseded_from_new, resolved_from_new}:
        raise HTTPException(409, "Bu kayıt supersession zincirinde döngü oluşturur")

    user = getattr(request.state, "user", {}) or {}
    try:
        result = db.execute(
            text(
                """INSERT INTO part_supersessions(
                company_id,old_product_id,new_product_id,note,created_by,created_at)
                VALUES(:cid,:old_id,:new_id,:note,:created_by,:now) RETURNING id"""
            ),
            {
                "cid": cid,
                "old_id": payload.old_product_id,
                "new_id": payload.new_product_id,
                "note": payload.note,
                "created_by": int(user["id"]) if user.get("id") else None,
                "now": utcnow(),
            },
        )
        supersession_id = int(result.scalar_one())
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Bu parça için zaten bir supersession kaydı var")
    return {"id": supersession_id, **payload.model_dump()}


@router.get("/part-supersessions")
def list_supersessions(request: Request, q: str = "", db: Session = Depends(get_db)):
    cid = company_id(request)
    rows = db.execute(
        text(
            """SELECT s.id,s.old_product_id,s.new_product_id,s.note,s.created_at,
            po.name old_name,COALESCE(po.product_code,'') old_code,
            pn.name new_name,COALESCE(pn.product_code,'') new_code
            FROM part_supersessions s
            JOIN products po ON po.id=s.old_product_id AND po.company_id=s.company_id
            JOIN products pn ON pn.id=s.new_product_id AND pn.company_id=s.company_id
            WHERE s.company_id=:cid AND (
              LOWER(po.name) LIKE LOWER(:q) OR LOWER(pn.name) LIKE LOWER(:q)
              OR COALESCE(po.product_code,'') LIKE :q OR COALESCE(pn.product_code,'') LIKE :q)
            ORDER BY s.id DESC LIMIT 1000"""
        ),
        {"cid": cid, "q": f"%{q}%"},
    ).mappings().all()
    return [dict(row) for row in rows]


@router.delete("/part-supersessions/{supersession_id}", status_code=204)
def delete_supersession(supersession_id: int, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    result = db.execute(
        text("DELETE FROM part_supersessions WHERE id=:id AND company_id=:cid"),
        {"id": supersession_id, "cid": cid},
    )
    if not result.rowcount:
        raise HTTPException(404, "Supersession kaydı bulunamadı")
    db.commit()


@router.get("/products/{product_id}/current")
def current_product(product_id: int, request: Request, db: Session = Depends(get_db)):
    """Resolve a (possibly superseded) part to its current successor.

    Returns the resolved current product plus the ordered chain of superseded
    parts, so the parts counter can show "FLT-100 yerine FLT-240 geçti".
    """
    cid = company_id(request)
    _product_row(db, cid, product_id)  # 404 + tenant scope before resolving
    try:
        current_id, superseded_ids = resolve_current_product(db, cid, product_id)
    except SupersessionCycleError:
        # Fail closed on corrupted data: never answer with a truncated chain.
        raise HTTPException(409, "Supersession zinciri bozuk: döngü tespit edildi")
    except SupersessionChainTooLongError:
        raise HTTPException(409, "Supersession zinciri izin verilen uzunluğu aşıyor")
    current = _product_row(db, cid, current_id)
    supersedes = [dict(_product_row(db, cid, pid)) for pid in superseded_ids]
    resolved = current_id != product_id
    label = None
    if resolved:
        old = supersedes[0]
        old_label = old["product_code"] or old["name"]
        new_label = current["product_code"] or current["name"]
        label = f"{old_label} yerine {new_label} geçti"
    return {
        "requested_product_id": product_id,
        "current": dict(current),
        "resolved": resolved,
        "supersedes": supersedes,
        "note": label,
    }
