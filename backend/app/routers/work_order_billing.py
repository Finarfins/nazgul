from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ..billing_service import build_invoice_summary
from ..db import get_db
from ..tenancy import company_id
from ..work_order_billing_schemas import InvoiceSummaryResponse

router = APIRouter(prefix="/work-orders", tags=["work-order-billing"])


@router.get("/{work_order_id}/invoice", response_model=InvoiceSummaryResponse)
def invoice_summary(
    work_order_id: int,
    request: Request,
    global_discount_type: Literal["PERCENT", "FIXED"] = "PERCENT",
    global_discount_value: Decimal = Query(default=Decimal("0"), ge=0),
    db: Session = Depends(get_db),
):
    # HTTP boundary only: resolve the tenant, then delegate the calculation.
    # The optional document discount mirrors POST /invoices/generate, so the
    # preview's grand_total is the invoice's for the same discount (H91).
    return build_invoice_summary(
        db,
        company_id(request),
        work_order_id,
        discount_type=global_discount_type,
        discount_value=global_discount_value,
    )
