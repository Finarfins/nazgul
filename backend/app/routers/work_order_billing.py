from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..billing_service import build_invoice_summary
from ..db import get_db
from ..tenancy import company_id
from ..work_order_billing_schemas import InvoiceSummaryResponse

router = APIRouter(prefix="/work-orders", tags=["work-order-billing"])


@router.get("/{work_order_id}/invoice", response_model=InvoiceSummaryResponse)
def invoice_summary(work_order_id: int, request: Request, db: Session = Depends(get_db)):
    # HTTP boundary only: resolve the tenant, then delegate the calculation.
    return build_invoice_summary(db, company_id(request), work_order_id)
