from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from ..activity_log import format_money_tr, log_request_activity
from ..db import get_db
from ..late_fee_charge_engine import (
    create_late_fee_draft,
    get_late_fee_document,
    post_late_fee_document,
    reverse_late_fee_document,
)
from ..late_fee_engine import build_late_fee_preview
from ..late_fee_schemas import (
    LateFeeChargeCreate,
    LateFeeChargeDocument,
    LateFeePreviewResponse,
)
from ..tenancy import company_id


router = APIRouter(prefix="/finance/late-fees", tags=["late-fees"])


def _charge_logger(db: Session, request: Request, cid: int, action: str, verb: str):
    """Motorun commit'inden hemen önce, aynı transaction içinde çağrılan kanca."""

    def _log(document: dict) -> None:
        log_request_activity(
            db, request, cid, action, "late_fee_charge", int(document["id"]),
            (
                f"#{document['id']} numaralı vade farkı belgesini {verb} — "
                f"{format_money_tr(document['gross_amount'])}, "
                f"sipariş #{document['order_id']}"
            ),
            {
                "document_id": int(document["id"]),
                "order_id": document.get("order_id"),
                "status": document.get("status"),
                "net_amount": document.get("net_amount"),
                "vat_amount": document.get("vat_amount"),
                "gross_amount": document.get("gross_amount"),
                "period": {
                    "start": document.get("period_start"),
                    "end": document.get("period_end"),
                },
            },
        )

    return _log


@router.get("/preview", response_model=LateFeePreviewResponse)
def late_fee_preview(
    request: Request,
    order_id: int | None = Query(default=None, gt=0),
    customer_id: int | None = Query(default=None, gt=0),
    as_of: date = Query(default_factory=date.today),
    db: Session = Depends(get_db),
):
    return build_late_fee_preview(
        db,
        company_id(request),
        as_of=as_of,
        order_id=order_id,
        customer_id=customer_id,
    )


@router.post("/charges", response_model=LateFeeChargeDocument)
def create_charge(
    payload: LateFeeChargeCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    try:
        return create_late_fee_draft(
            db,
            cid,
            payload.order_id,
            payload.period_start,
            payload.period_end,
            idempotency_key,
            created_by=int(request.state.user["id"]),
            installment_id=payload.installment_id,
            on_created=_charge_logger(
                db, request, cid, "late_fee.draft", "taslak olarak oluşturdu"
            ),
        )
    except Exception:
        db.rollback()
        raise


@router.get("/charges/{document_id}", response_model=LateFeeChargeDocument)
def get_charge(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    return get_late_fee_document(db, company_id(request), document_id)


@router.post("/charges/{document_id}/post", response_model=LateFeeChargeDocument)
def post_charge(
    document_id: int,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    try:
        return post_late_fee_document(
            db,
            cid,
            document_id,
            idempotency_key,
            actor_id=int(request.state.user["id"]),
            on_posted=_charge_logger(db, request, cid, "late_fee.post", "onayladı"),
        )
    except Exception:
        db.rollback()
        raise


@router.post("/charges/{document_id}/reversal", response_model=LateFeeChargeDocument)
def reverse_charge(
    document_id: int,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    try:
        return reverse_late_fee_document(
            db,
            cid,
            document_id,
            idempotency_key,
            actor_id=int(request.state.user["id"]),
            on_reversed=_charge_logger(
                db, request, cid, "late_fee.reversal", "ters kayıtla iptal etti"
            ),
        )
    except Exception:
        db.rollback()
        raise
