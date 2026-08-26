"""Servis v2 FAZ-1 request schemas: hour readings, ownership transfer,
technician profiles. Kept beside :mod:`app.work_order_schemas` so the service
domain's validation rules live together."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from .schemas import MAX_MACHINE_WORKING_HOURS

READING_SOURCES = {"manual", "work_order"}
READING_TYPES = {"normal", "anomaly", "meter_replacement", "correction"}
# Reading types that may legitimately record a value below the current meter
# projection (a swapped meter, or a correction of an earlier typo). Everything
# else fails closed on a decrease.
DECREASE_ALLOWED_TYPES = {"meter_replacement", "correction"}
# Reading types that are meaningless without an operator explanation.
REASON_REQUIRED_TYPES = {"anomaly", "meter_replacement", "correction"}


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class MachineHourReadingCreate(BaseModel):
    # Bound to NUMERIC(12,2) so oversized input 422s identically on SQLite and
    # PostgreSQL instead of overflowing the column on PostgreSQL.
    reading_hours: Decimal = Field(ge=0, le=MAX_MACHINE_WORKING_HOURS)
    reading_date: datetime | None = None
    reading_type: str = "normal"
    work_order_id: int | None = Field(default=None, gt=0)
    reason: str | None = None

    @field_validator("reading_type")
    @classmethod
    def validate_reading_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in READING_TYPES:
            raise ValueError("Geçersiz sayaç okuma türü")
        return normalized

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @model_validator(mode="after")
    def require_reason(self):
        if self.reading_type in REASON_REQUIRED_TYPES and not self.reason:
            raise ValueError(
                "Bu okuma türü için açıklama (reason) zorunludur"
            )
        return self


class MachineOwnershipTransfer(BaseModel):
    to_customer_id: int = Field(gt=0)
    transfer_date: datetime | None = None
    reason: str | None = None

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class TechnicianProfileFields(BaseModel):
    phone: str | None = Field(default=None, max_length=40)
    specialty: str | None = Field(default=None, max_length=160)
    active: bool = True

    @field_validator("phone", "specialty")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class TechnicianProfileCreate(TechnicianProfileFields):
    user_id: int = Field(gt=0)


class TechnicianProfileUpdate(TechnicianProfileFields):
    """Full replacement of the mutable profile fields; user_id is immutable."""
