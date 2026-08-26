from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class InvoiceParty(BaseModel):
    id: int
    name: str


class InvoiceMachine(BaseModel):
    id: int
    brand: str
    model: str
    serial_number: str | None = None


class InvoiceWorkOrder(BaseModel):
    id: int
    work_order_no: str
    status: str
    opened_at: str
    completed_at: str | None = None
    delivered_at: str | None = None


class LaborEntry(BaseModel):
    """One billable labor unit. ``line_id`` is absent for the v1 header source."""

    hours: Decimal
    hourly_rate: Decimal
    line_id: int | None = None


class LaborSummary(BaseModel):
    total_hours: Decimal
    labor_rate: Decimal
    labor_total: Decimal
    # Which source produced labor_total: the v1 header, or the approved labor
    # lines. Never both — see billing_service._resolve_labor.
    source: Literal["header", "lines"] = "header"
    line_ids: list[int] = []
    entries: list[LaborEntry] = []


class PartsSummary(BaseModel):
    parts_total: Decimal
    parts_before_tax: Decimal
    parts_tax: Decimal
    parts_discount: Decimal


class InvoiceTotals(BaseModel):
    labor: Decimal
    parts: Decimal
    tax: Decimal
    discount: Decimal
    grand_total: Decimal
    labor_source: Literal["header", "lines"] = "header"
    labor_line_ids: list[int] = []


class WarrantySummary(BaseModel):
    type: Literal["FULL", "PARTIAL", "NONE"]
    coverage_percent: Decimal
    customer_amount: Decimal
    warranty_amount: Decimal
    company_cost: Decimal


class InvoiceSummaryResponse(BaseModel):
    customer: InvoiceParty
    machine: InvoiceMachine
    work_order: InvoiceWorkOrder
    labor: LaborSummary
    parts: PartsSummary
    totals: InvoiceTotals
    warranty: WarrantySummary
    taxes: Decimal
