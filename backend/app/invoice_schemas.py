from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, BeforeValidator, Field

_normalize_discount_type = BeforeValidator(
    lambda v: v.upper() if isinstance(v, str) else v
)

DiscountType = Annotated[
    Literal["PERCENT", "FIXED"],
    _normalize_discount_type,
]

class InvoiceGenerateRequest(BaseModel):
    work_order_id: int=Field(gt=0)
    branch_prefix: str=""
    currency: str="TRY"
    exchange_rate: Decimal=Field(default=Decimal("1"),gt=0)
    payment_terms: str|None=None
    notes: str|None=None
    global_discount_type: DiscountType="PERCENT"
    global_discount_value: Decimal=Field(default=Decimal("0"),ge=0)

class InvoiceCancelRequest(BaseModel):
    reason: str=Field(min_length=3,max_length=1000)
