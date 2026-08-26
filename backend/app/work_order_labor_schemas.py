"""Servis v2 FAZ-2 request schemas for work order labor lines."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

LABOR_LINE_STATES = {"DRAFT", "APPROVED", "VOID"}

# hours -> NUMERIC(8,2), hourly_rate -> NUMERIC(12,2). Bound to the column
# maxima so oversized input is a clean 422 on both SQLite and PostgreSQL
# instead of a NUMERIC overflow (DataError/500) on PostgreSQL.
MAX_LINE_HOURS = Decimal("999999.99")
MAX_LINE_RATE = Decimal("9999999999.99")


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class LaborLineWrite(BaseModel):
    technician_user_id: int = Field(gt=0)
    work_date: datetime | None = None
    hours: Decimal = Field(gt=0, le=MAX_LINE_HOURS)
    # Omitted on create -> frozen from the work order header's labor_rate.
    # Never re-derived afterwards (see the router's rate-freeze note).
    hourly_rate: Decimal | None = Field(default=None, ge=0, le=MAX_LINE_RATE)
    note: str | None = None

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None) -> str | None:
        return _clean_optional(value)
