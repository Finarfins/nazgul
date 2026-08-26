from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SUPPORTED_CURRENCIES = {"TRY", "EUR", "USD"}

# Reference/comparison prices are stored NUMERIC(18,4); bounds keep every stored
# value and its TRY normalisation inside the column identically on SQLite and
# PostgreSQL (HTTP 422 instead of a PG overflow).
MAX_PRICE = Decimal("100000000")      # 1e8, fits NUMERIC(18,4)
MAX_QUANTITY = Decimal("100000000")
MAX_LEAD_DAYS = 100000
MAX_RATE = Decimal("1000000")          # FX rate upper bound, fits NUMERIC(18,6)


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _normalize_currency(value: str) -> str:
    code = (value or "").strip().upper()
    if code not in SUPPORTED_CURRENCIES:
        raise ValueError("Geçersiz para birimi (TRY, EUR veya USD olmalı)")
    return code


class DiscountTier(BaseModel):
    """One rung of a quantity -> discount ladder for a manual supplier price."""

    min_quantity: Decimal = Field(gt=0, le=MAX_QUANTITY)
    discount_percent: Decimal = Field(ge=0, le=100)


class SupplierPriceFields(BaseModel):
    price: Decimal = Field(ge=0, le=MAX_PRICE)
    currency: str = "TRY"
    # Increment B fields — accepted and stored now, not surfaced in A.
    moq: Decimal | None = Field(default=None, ge=0, le=MAX_QUANTITY)
    lead_time_days: int | None = Field(default=None, ge=0, le=MAX_LEAD_DAYS)
    discount_percent: Decimal | None = Field(default=None, ge=0, le=100)
    supplier_stock: Decimal | None = Field(default=None, ge=0, le=MAX_QUANTITY)
    # The application-wide unit_price contract is VAT-INCLUSIVE, so a supplier
    # price defaults to the same reading. Suppliers quoting net prices are
    # switched off per record; the margin calculation honours the flag.
    price_includes_vat: bool = True
    # Quantity discount ladder (B). Omitted -> existing tiers are left untouched;
    # an explicit empty list clears them. The flat ``discount_percent`` remains
    # the fallback when no rung matches the evaluated quantity.
    tiers: list[DiscountTier] | None = None
    note: str | None = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return _normalize_currency(value)

    @field_validator("tiers")
    @classmethod
    def validate_tiers(cls, value: list[DiscountTier] | None) -> list[DiscountTier] | None:
        if value is None:
            return None
        seen: set[Decimal] = set()
        for tier in value:
            if tier.min_quantity in seen:
                raise ValueError("Aynı miktar için birden fazla iskonto kademesi tanımlanamaz")
            seen.add(tier.min_quantity)
        return sorted(value, key=lambda tier: tier.min_quantity)

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class SupplierPriceCreate(SupplierPriceFields):
    supplier_id: int = Field(gt=0)
    product_id: int = Field(gt=0)


class SupplierPriceUpdate(BaseModel):
    """Partial price update; omitted fields keep their stored values."""

    price: Decimal | None = Field(default=None, ge=0, le=MAX_PRICE)
    currency: str | None = None
    moq: Decimal | None = Field(default=None, ge=0, le=MAX_QUANTITY)
    lead_time_days: int | None = Field(default=None, ge=0, le=MAX_LEAD_DAYS)
    discount_percent: Decimal | None = Field(default=None, ge=0, le=100)
    supplier_stock: Decimal | None = Field(default=None, ge=0, le=MAX_QUANTITY)
    price_includes_vat: bool | None = None
    tiers: list[DiscountTier] | None = None
    note: str | None = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_currency(value)

    @field_validator("tiers")
    @classmethod
    def validate_tiers(cls, value: list[DiscountTier] | None) -> list[DiscountTier] | None:
        return SupplierPriceFields.validate_tiers(value)

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @model_validator(mode="after")
    def reject_null_required_columns(self) -> "SupplierPriceUpdate":
        for field in ("price", "currency", "price_includes_vat"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} null olamaz")
        return self


class SupplierPriceHistoryItem(BaseModel):
    id: int
    product_id: int
    product_name: str
    price: str
    currency: str
    price_in_try: str | None = None
    source: str
    note: str | None = None
    captured_at: datetime


class SupplierPriceHistoryResponse(BaseModel):
    supplier_id: int
    items: list[SupplierPriceHistoryItem]
    has_more: bool
    next_cursor: str | None = None


class PurchaseOrderCreate(BaseModel):
    """One-click purchase order (Increment C).

    Turns a chosen comparison offer into a real purchase through the existing
    purchase-creation path. ``quantity`` defaults to the supplier's MOQ (else 1)
    and ``expected_price`` (TRY) overrides the offer's known price when the buyer
    negotiated a different figure. The router always creates a draft, without
    stock/finance side effects until it is approved.
    """

    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(gt=0)
    supplier_id: int = Field(gt=0)
    quantity: Decimal | None = Field(default=None, gt=0, le=MAX_QUANTITY)
    expected_price: Decimal | None = Field(default=None, gt=0, le=MAX_PRICE)
    note: str | None = None

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class ReorderPolicyWrite(BaseModel):
    """Manual reorder overrides for one product.

    A field that is not sent is left untouched; sending ``null`` clears the
    override back to "derive from demand". ``0`` is a real override meaning "do
    not reorder until the shelf is empty" and must never be read as NULL.
    """

    reorder_point: Decimal | None = Field(default=None, ge=0, le=MAX_QUANTITY)
    target_stock: Decimal | None = Field(default=None, ge=0, le=MAX_QUANTITY)


class ReorderDraftLine(BaseModel):
    """One human-approved reorder line: which product, from which supplier."""

    product_id: int = Field(gt=0)
    supplier_id: int = Field(gt=0)
    quantity: Decimal | None = Field(default=None, gt=0, le=MAX_QUANTITY)


class ReorderDraftCreate(BaseModel):
    """Grouped reorder drafts.

    There is deliberately NO ``status`` field: the server hardcodes ``draft``, so
    no request shape can make this endpoint approve, receive or send an order to
    a supplier. Approval stays in the normal authorised purchase workflow.
    """

    model_config = ConfigDict(extra="forbid")

    lines: list[ReorderDraftLine] = Field(min_length=1, max_length=500)
    note: str | None = None

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class ExchangeRateOverrideWrite(BaseModel):
    currency: str
    rate_to_try: Decimal = Field(gt=0, le=MAX_RATE)
    note: str | None = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        code = _normalize_currency(value)
        if code == "TRY":
            raise ValueError("TRY için kur tanımlanamaz (her zaman 1)")
        return code

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None) -> str | None:
        return _clean_optional(value)
