from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, PlainSerializer, WithJsonSchema

from .money import decimal_value
from .pos_contracts import MoneyOut


RATE_PATTERN = r"^\d+\.\d{4}$"
DECIMAL_PATTERN = r"^-?\d+(\.\d+)?$"

RateOut = Annotated[
    Decimal,
    PlainSerializer(
        lambda value: format(decimal_value(value), ".4f"),
        return_type=str,
        when_used="json",
    ),
    WithJsonSchema({"type": "string", "pattern": RATE_PATTERN}),
]

ExactDecimalOut = Annotated[
    Decimal,
    PlainSerializer(
        lambda value: format(decimal_value(value), "f"),
        return_type=str,
        when_used="json",
    ),
    WithJsonSchema({"type": "string", "pattern": DECIMAL_PATTERN}),
]


class LateFeePolicySnapshot(BaseModel):
    id: int
    source: str
    annual_rate: RateOut
    day_count_basis: int
    grace_days: int
    tax_mode: str
    vat_rate: RateOut
    effective_from: date
    effective_to: date | None


class PrincipalSegmentView(BaseModel):
    start_date: date
    end_date: date
    days: int
    principal: MoneyOut
    unrounded_interest: ExactDecimalOut


class LateFeeOrderPreview(BaseModel):
    order_id: int
    document_no: str | None
    due_date: date
    interest_start_date: date
    as_of: date
    principal_at_interest_start: MoneyOut
    principal_as_of: MoneyOut
    late_fee_net: MoneyOut
    late_fee_vat: MoneyOut
    late_fee_gross: MoneyOut
    policy: LateFeePolicySnapshot
    segments: list[PrincipalSegmentView]


class LateFeePreviewResponse(BaseModel):
    company_id: int
    customer_id: int
    customer_name: str
    as_of: date
    total_late_fee_net: MoneyOut
    total_late_fee_vat: MoneyOut
    total_late_fee_gross: MoneyOut
    orders: list[LateFeeOrderPreview]


class LateFeeChargeCreate(BaseModel):
    order_id: int
    installment_id: int | None = None
    period_start: date
    period_end: date


class LateFeeChargeDocument(BaseModel):
    id: int
    company_id: int
    charge_period_id: int
    order_id: int
    customer_id: int
    charge_type: str
    period_start: date
    period_end: date
    due_date_snapshot: date
    principal_basis: MoneyOut
    annual_rate_snapshot: RateOut
    day_count_basis_snapshot: int
    grace_days_snapshot: int
    tax_mode_snapshot: str
    vat_rate_snapshot: RateOut
    net_amount: MoneyOut
    vat_amount: MoneyOut
    gross_amount: MoneyOut
    status: str
    calculation_fingerprint: str
    revision_no: int
    reversal_of_document_id: int | None
    created_at: datetime
    posted_at: datetime | None
    reversed_at: datetime | None
