from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from .work_order_part_schemas import WorkOrderPartWrite
from .late_fee_schemas import ExactDecimalOut
from .pos_contracts import MoneyOut


WORK_ORDER_STATES = {
    "OPEN",
    "SCHEDULED",
    "IN_PROGRESS",
    "WAITING_PARTS",
    "WAITING_CUSTOMER",
    "COMPLETED",
    "DELIVERED",
    "CANCELLED",
}
WORK_ORDER_PRIORITIES = {"LOW", "NORMAL", "HIGH", "URGENT"}
# A new work order may start life planned (SCHEDULED) or open; every other
# state is reachable only through the transition endpoint.
WORK_ORDER_INITIAL_STATES = {"OPEN", "SCHEDULED"}

# Upper bounds keep each stored value and the derived labor_total inside the
# NUMERIC columns, so oversized input is rejected identically (HTTP 422) on
# SQLite and PostgreSQL instead of overflowing the column on PostgreSQL.
# estimated_hours/actual_hours -> NUMERIC(10,2); labor_rate -> NUMERIC(18,2);
# labor_total = actual_hours * labor_rate is stored as NUMERIC(18,2). Worst
# case 1e6 * 1e8 = 1e14, well within NUMERIC(18,2).
MAX_HOURS = Decimal("1000000")
MAX_LABOR_RATE = Decimal("100000000")


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class WorkOrderFields(BaseModel):
    machine_id: int = Field(gt=0)
    customer_id: int | None = Field(default=None, gt=0)
    technician_id: int = Field(gt=0)
    opened_at: datetime | None = None
    scheduled_date: datetime | None = None
    priority: str = "NORMAL"
    complaint: str | None = None
    diagnosis: str | None = None
    repair_summary: str | None = None
    technician_notes: str | None = None
    estimated_hours: Decimal = Field(default=Decimal("0.00"), ge=0, le=MAX_HOURS)
    actual_hours: Decimal = Field(default=Decimal("0.00"), ge=0, le=MAX_HOURS)
    labor_rate: Decimal = Field(default=Decimal("0.00"), ge=0, le=MAX_LABOR_RATE)
    warranty: bool = False
    warranty_type: str | None = None
    warranty_percent: Decimal | None = Field(default=None, ge=0, le=100)

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in WORK_ORDER_PRIORITIES:
            raise ValueError("Geçersiz iş emri önceliği")
        return normalized

    @field_validator("complaint", "diagnosis", "repair_summary", "technician_notes")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @model_validator(mode="after")
    def normalize_warranty(self):
        warranty_type = (self.warranty_type or ("FULL" if self.warranty else "NONE")).strip().upper()
        if warranty_type not in {"FULL", "PARTIAL", "NONE"}:
            raise ValueError("Geçersiz garanti türü")
        if warranty_type == "FULL":
            coverage = Decimal("100")
        elif warranty_type == "NONE":
            coverage = Decimal("0")
        else:
            coverage = self.warranty_percent
            if coverage is None or coverage <= 0 or coverage >= 100:
                raise ValueError("Kısmi garanti oranı 0 ile 100 arasında olmalıdır")
        self.warranty_type = warranty_type
        self.warranty_percent = coverage
        self.warranty = warranty_type != "NONE"
        return self


class WorkOrderCreate(WorkOrderFields):
    work_order_no: str | None = Field(default=None, max_length=80)
    status: str = "OPEN"
    parts: list[WorkOrderPartWrite] = Field(default_factory=list, max_length=500)

    @field_validator("work_order_no")
    @classmethod
    def clean_work_order_no(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("status")
    @classmethod
    def validate_initial_status(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in WORK_ORDER_INITIAL_STATES:
            raise ValueError("Yeni iş emri yalnız OPEN veya SCHEDULED durumuyla açılabilir")
        return normalized

    @model_validator(mode="after")
    def require_scheduled_date(self):
        if self.status == "SCHEDULED" and self.scheduled_date is None:
            raise ValueError("Planlanmış iş emri için planlanan tarih zorunludur")
        return self


class WorkOrderUpdate(WorkOrderFields):
    """Editable fields; status and closure are controlled by the workflow route."""


class WorkOrderStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in WORK_ORDER_STATES:
            raise ValueError("Geçersiz iş emri durumu")
        return normalized


class WorkOrderReceivableReverse(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Ters kayıt gerekçesi zorunludur")
        if len(normalized) > 1000:
            raise ValueError("Ters kayıt gerekçesi en fazla 1000 karakter olabilir")
        return normalized


class ServiceReceivableDocument(BaseModel):
    id: int
    company_id: int
    work_order_id: int
    customer_id: int
    charge_type: str
    period_start: date
    period_end: date
    due_date_snapshot: date
    gross_amount: MoneyOut
    status: str
    calculation_fingerprint: str
    revision_no: int
    reversal_of_document_id: int | None
    currency: str
    exchange_rate: ExactDecimalOut
    created_at: datetime
    posted_at: datetime | None
    reversed_at: datetime | None
