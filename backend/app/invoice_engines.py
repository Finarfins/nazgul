from dataclasses import dataclass
from decimal import Decimal
from .money import HUNDRED, money, percentage

@dataclass(frozen=True)
class TaxResult:
    taxable: Decimal; tax: Decimal; total: Decimal

class TaxEngine:
    @staticmethod
    def calculate(amount: object, rate: object, *, exempt: bool=False) -> TaxResult:
        taxable=money(amount); pct=Decimal("0") if exempt else percentage(rate)
        tax=money(taxable*pct/HUNDRED)
        return TaxResult(taxable,tax,money(taxable+tax))

@dataclass(frozen=True)
class DiscountResult:
    original: Decimal; discount: Decimal; net: Decimal

class DiscountEngine:
    @staticmethod
    def calculate(amount: object, value: object=0, *, kind: str="PERCENT") -> DiscountResult:
        original=money(amount); normalized=kind.upper()
        discount=money(original*percentage(value)/HUNDRED) if normalized=="PERCENT" else money(value)
        if normalized not in {"PERCENT","FIXED"} or discount<0 or discount>original:
            raise ValueError("Geçersiz indirim")
        return DiscountResult(original,discount,money(original-discount))
