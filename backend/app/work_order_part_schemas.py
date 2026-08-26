from decimal import Decimal

from pydantic import BaseModel, Field

# Upper bounds keep every stored value and the derived ``total_price`` inside the
# NUMERIC(18,2)/(18,4)/(9,4) columns, so oversized input is rejected identically
# (HTTP 422) on SQLite and PostgreSQL instead of overflowing the column on
# PostgreSQL. Worst case is quantity*unit_price*(1+tax) = 1e6 * 1e8 * 2 = 2e14,
# well within NUMERIC(18,2).
MAX_QUANTITY = Decimal("1000000")
MAX_UNIT_PRICE = Decimal("100000000")
MAX_RATE = Decimal("100")


class WorkOrderPartWrite(BaseModel):
    product_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, le=MAX_QUANTITY)
    unit_price: Decimal = Field(ge=0, le=MAX_UNIT_PRICE)
    discount: Decimal = Field(default=Decimal("0"), ge=0, le=MAX_RATE)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=MAX_RATE)
