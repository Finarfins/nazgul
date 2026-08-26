from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .pos_contracts import MoneyOut


class ManualAllocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_id: int = Field(gt=0)
    # Exactly one target: a sale document or a posted late-fee charge document.
    order_id: int | None = Field(default=None, gt=0)
    receivable_charge_id: int | None = Field(default=None, gt=0)
    amount: Decimal = Field(gt=0)
    effective_date: date
    note: str | None = Field(default=None, max_length=1000)


class AllocationReversalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=0)
    effective_date: date
    note: str | None = Field(default=None, max_length=1000)


class AllocationReallocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allocation_id: int = Field(gt=0)
    target_order_id: int | None = Field(default=None, gt=0)
    target_receivable_charge_id: int | None = Field(default=None, gt=0)
    amount: Decimal = Field(gt=0)
    effective_date: date
    note: str | None = Field(default=None, max_length=1000)


class AllocationView(BaseModel):
    id: int
    payment_id: int
    order_id: int | None
    receivable_charge_id: int | None = None
    amount: MoneyOut
    allocation_type: str
    effective_date: date
    recorded_at: datetime
    created_by: int | None
    reversed_at: datetime | None
    reversed_by: int | None
    reversal_allocation_id: int | None
    reversal_of_allocation_id: int | None
    note: str | None
    net_amount: MoneyOut
    status: str


class AllocationMutationResult(BaseModel):
    operation: str
    payment_id: int
    source_allocation_id: int | None = None
    reversal_allocation_id: int | None = None
    allocation_id: int | None = None
    order_id: int | None
    receivable_charge_id: int | None = None
    amount: MoneyOut
    net_amount: MoneyOut


class ReconciliationView(BaseModel):
    company_id: int
    order_id: int | None
    receivable_charge_id: int | None = None
    customer_id: int
    document_no: str | None
    stored_paid_amount: MoneyOut
    linked_payment_total: MoneyOut
    unlinked_customer_payment_total: MoneyOut
    linked_return_total: MoneyOut
    unlinked_customer_return_total: MoneyOut
    active_allocation_total: MoneyOut
    frozen_residual_amount: MoneyOut
    document_allocation_excess: MoneyOut
    payment_allocation_excess: MoneyOut
    charge_allocation_excess: MoneyOut
    candidate_residual: MoneyOut
    evidence_source: str
    confidence: str
    status: str
    reason: str
