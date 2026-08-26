from __future__ import annotations

from datetime import date
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from ..activity_log import format_money_tr, log_request_activity
from ..allocation_reconciliation import build_reconciliation_report
from ..config import settings
from ..db import SessionLocal, get_db
from ..payment_allocation_engine import (
    list_payment_allocations,
    manual_allocate_payment_with_retry,
    reallocate_allocation_with_retry,
    reverse_allocation_with_retry,
)
from ..payment_allocation_schemas import (
    AllocationMutationResult,
    AllocationReallocationCreate,
    AllocationReversalCreate,
    AllocationView,
    ManualAllocationCreate,
    ReconciliationView,
)
from ..tenancy import company_id

router = APIRouter(prefix="/payment-allocations", tags=["payment-allocations"])
logger = logging.getLogger(__name__)


def _require_engine() -> None:
    if not settings.payment_allocation_engine_enabled:
        raise HTTPException(409, "Tahsis motoru devre dışı")


def _actor_id(request: Request) -> int:
    return int(request.state.user["id"])


def _allocation_logger(request: Request, cid: int, action: str, verb: str):
    """Tahsis motorunun commit'inden hemen önce, MOTORUN kendi Session'ında
    çağrılan kanca. Uçlar ``SessionLocal`` ile ayrı bir oturum açtığı için
    aktivite kaydı da o oturuma yazılır: aynı transaction garantisi korunur."""

    def _log(db, result) -> None:
        target = (
            f"sipariş #{result.order_id}"
            if result.order_id is not None
            else f"tahakkuk #{result.receivable_charge_id}"
        )
        log_request_activity(
            db, request, cid, action, "payment_allocation",
            result.allocation_id or result.source_allocation_id,
            (
                f"#{result.payment_id} numaralı ödemenin tahsisini {verb} — "
                f"{format_money_tr(result.amount)}, {target}"
            ),
            {
                "operation": result.operation,
                "payment_id": result.payment_id,
                "order_id": result.order_id,
                "receivable_charge_id": result.receivable_charge_id,
                "amount": result.amount,
                "net_amount": result.net_amount,
                "allocation_id": result.allocation_id,
                "source_allocation_id": result.source_allocation_id,
                "reversal_allocation_id": result.reversal_allocation_id,
            },
        )

    return _log


@router.get("/engine-state")
def allocation_engine_state(request: Request) -> dict[str, bool]:
    """Tahsis motorunun AÇIK/KAPALI durumu — salt okuma yapılandırma sinyali.

    Motor kapalıyken tahsis GEÇMİŞİ hiçbir satır üretemez. Bu sinyal olmadan
    arayüz "hiç tahsis yok" ile "özellik kapalı"yı ayırt edemez ve ikisini de
    BOŞ BİR TABLO olarak çizer; kullanıcı eksik veri sanır, ölçüm kapısı da
    boş bir yüzeyi "temiz" sayar. Uç yalnız bayrağı bildirir; hiçbir ödeme
    davranışını değiştirmez ve üretim varsayılanını (kapalı) etkilemez.
    """
    company_id(request)
    return {"enabled": bool(settings.payment_allocation_engine_enabled)}


@router.get(
    "/payments/{payment_id}",
    response_model=list[AllocationView],
)
def allocations_for_payment(
    payment_id: int,
    request: Request,
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    return list_payment_allocations(
        db,
        company_id(request),
        payment_id=payment_id,
        as_of=as_of,
    )


@router.get(
    "/orders/{order_id}",
    response_model=list[AllocationView],
)
def allocations_for_order(
    order_id: int,
    request: Request,
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    return list_payment_allocations(
        db,
        company_id(request),
        order_id=order_id,
        as_of=as_of,
    )


@router.get(
    "/charges/{receivable_charge_id}",
    response_model=list[AllocationView],
)
def allocations_for_charge(
    receivable_charge_id: int,
    request: Request,
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    return list_payment_allocations(
        db,
        company_id(request),
        receivable_charge_id=receivable_charge_id,
        as_of=as_of,
    )


@router.get(
    "/reconciliation",
    response_model=list[ReconciliationView],
)
def reconciliation(
    request: Request,
    db: Session = Depends(get_db),
):
    return [
        row.as_dict()
        for row in build_reconciliation_report(db, company_id(request))
    ]


@router.post(
    "/manual",
    response_model=AllocationMutationResult,
)
def manual_allocation(
    payload: ManualAllocationCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    _require_engine()
    cid = company_id(request)
    try:
        return manual_allocate_payment_with_retry(
            SessionLocal,
            cid,
            payload.payment_id,
            payload.order_id,
            payload.amount,
            payload.effective_date,
            idempotency_key,
            created_by=_actor_id(request),
            note=payload.note,
            receivable_charge_id=payload.receivable_charge_id,
            activity_hook=_allocation_logger(
                request, cid, "allocation.manual", "elle yaptı"
            ),
        ).as_dict()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected manual allocation failure")
        raise HTTPException(500, "Tahsis kaydedilemedi. Lütfen tekrar deneyin.") from exc


@router.post(
    "/{allocation_id}/reversal",
    response_model=AllocationMutationResult,
)
def reversal(
    allocation_id: int,
    payload: AllocationReversalCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    _require_engine()
    cid = company_id(request)
    try:
        return reverse_allocation_with_retry(
            SessionLocal,
            cid,
            allocation_id,
            payload.amount,
            payload.effective_date,
            idempotency_key,
            created_by=_actor_id(request),
            note=payload.note,
            closed_through=settings.payment_allocation_closed_through,
            activity_hook=_allocation_logger(
                request, cid, "allocation.reversal", "ters kayıtla iptal etti"
            ),
        ).as_dict()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Unexpected allocation reversal failure",
            extra={"allocation_id": allocation_id},
        )
        raise HTTPException(500, "Reversal kaydedilemedi. Lütfen tekrar deneyin.") from exc


@router.post(
    "/reallocation",
    response_model=AllocationMutationResult,
)
def reallocation(
    payload: AllocationReallocationCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    _require_engine()
    cid = company_id(request)
    try:
        return reallocate_allocation_with_retry(
            SessionLocal,
            cid,
            payload.allocation_id,
            payload.target_order_id,
            payload.amount,
            payload.effective_date,
            idempotency_key,
            created_by=_actor_id(request),
            note=payload.note,
            closed_through=settings.payment_allocation_closed_through,
            target_receivable_charge_id=payload.target_receivable_charge_id,
            activity_hook=_allocation_logger(
                request, cid, "allocation.reallocation", "yeniden dağıttı"
            ),
        ).as_dict()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Unexpected allocation reallocation failure",
            extra={"allocation_id": payload.allocation_id},
        )
        raise HTTPException(500, "Yeniden tahsis kaydedilemedi. Lütfen tekrar deneyin.") from exc
