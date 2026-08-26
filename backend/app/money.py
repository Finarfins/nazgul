from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Final

ZERO: Final = Decimal("0")
ZERO_MONEY: Final = Decimal("0.00")
ZERO_QUANTITY: Final = Decimal("0.0000")
HUNDRED: Final = Decimal("100")
MONEY_QUANTUM: Final = Decimal("0.01")
QUANTITY_QUANTUM: Final = Decimal("0.0001")
PERCENT_QUANTUM: Final = Decimal("0.0001")
PERSISTED_PERCENT_QUANTUM: Final = Decimal("0.01")


def decimal_value(value: object, *, field_name: str = "Sayısal değer") -> Decimal:
    """Convert input without ever passing through binary floating-point math."""
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} geçersiz") from exc
    if not result.is_finite():
        raise ValueError(f"{field_name} sonlu bir sayı olmalıdır")
    return result


def money(value: object) -> Decimal:
    return decimal_value(value, field_name="Tutar").quantize(
        MONEY_QUANTUM, rounding=ROUND_HALF_UP
    )


def quantity(value: object) -> Decimal:
    return decimal_value(value, field_name="Miktar").quantize(
        QUANTITY_QUANTUM, rounding=ROUND_HALF_UP
    )


def percentage(value: object) -> Decimal:
    return decimal_value(value, field_name="Yüzde").quantize(
        PERCENT_QUANTUM, rounding=ROUND_HALF_UP
    )


def persisted_percentage(value: object) -> Decimal:
    """Normalize document percentages to their NUMERIC(18,2) storage contract."""
    return decimal_value(value, field_name="Yüzde").quantize(
        PERSISTED_PERCENT_QUANTUM, rounding=ROUND_HALF_UP
    )


@dataclass(frozen=True)
class LineAmounts:
    """The fully-resolved money breakdown of a single billed line."""

    raw: Decimal
    discount: Decimal
    taxable: Decimal
    tax: Decimal
    total: Decimal


@dataclass(frozen=True)
class VatInclusiveDiscountedLine:
    """One VAT-inclusive line after its document-discount share is applied."""

    subtotal: Decimal
    tax: Decimal
    total: Decimal
    document_discount: Decimal


def compute_line(
    quantity_value: object,
    unit_price_value: object,
    discount_percent: object = ZERO,
    tax_rate: object = ZERO,
) -> LineAmounts:
    """Canonical per-line money math — the single source of truth for a billed line.

    Every billed line (work-order part storage, the billing summary, and the
    generated invoice line) must go through this helper so the stored
    ``work_order_parts.total_price`` is always identical to the invoice line and
    the billing total. Rounding happens per component (never as a single folded
    factor), so the result is stable across SQLite and PostgreSQL::

        raw      = round(quantity * unit_price)
        discount = round(raw * discount_percent / 100)
        taxable  = raw - discount
        tax      = round(taxable * tax_rate / 100)
        total    = taxable + tax
    """
    raw = money(quantity(quantity_value) * money(unit_price_value))
    discount = money(raw * percentage(discount_percent) / HUNDRED)
    taxable = money(raw - discount)
    tax = money(taxable * percentage(tax_rate) / HUNDRED)
    total = money(taxable + tax)
    return LineAmounts(raw=raw, discount=discount, taxable=taxable, tax=tax, total=total)


def line_amount(
    quantity_value: object,
    unit_price_value: object,
    discount_percent: object = ZERO,
    tax_rate: object = ZERO,
) -> Decimal:
    """Return only the stored/billed total for a single line (see :func:`compute_line`)."""
    return compute_line(quantity_value, unit_price_value, discount_percent, tax_rate).total


def distribute_amount(amount: object, weights: Sequence[object]) -> list[Decimal]:
    """Split ``amount`` across ``weights`` proportionally, cents summing exactly.

    Largest-remainder (Hamilton) apportionment: each share is floored to the
    cent, then the leftover cents are handed to the entries with the largest
    fractional remainder (ties broken by larger weight, then lower index). The
    returned shares therefore always sum to ``money(amount)`` exactly and the
    result is deterministic — used to allocate a document-level discount across
    invoice lines so ``Σ line totals == header grand_total``. With a zero amount
    or non-positive weight total every share is ``0.00``.
    """
    totals = [money(ZERO) for _ in weights]
    target = money(amount)
    values = [decimal_value(weight) for weight in weights]
    weight_sum = sum(values, ZERO)
    if not values or target == ZERO_MONEY or weight_sum <= ZERO:
        return totals

    raw_shares = [value * target / weight_sum for value in values]
    floored = [share.quantize(MONEY_QUANTUM, rounding=ROUND_DOWN) for share in raw_shares]
    leftover = int(
        ((target - sum(floored, ZERO)) / MONEY_QUANTUM).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )
    order = sorted(
        range(len(values)),
        key=lambda i: (raw_shares[i] - floored[i], values[i], -i),
        reverse=True,
    )
    shares = list(floored)
    for step in range(leftover):
        shares[order[step % len(order)]] += MONEY_QUANTUM
    return shares


def allocate_vat_inclusive_document_discount(
    line_totals: Sequence[object],
    tax_rates: Sequence[object],
    discount_amount: object,
) -> list[VatInclusiveDiscountedLine]:
    """Allocate a header discount and re-split VAT with cent-exact parity.

    The project-wide VAT-inclusive document contract is ROUND_HALF_UP at every
    money boundary. Header discounts use largest-remainder allocation so the
    returned line totals always equal the rounded header total exactly.
    """
    if len(line_totals) != len(tax_rates):
        raise ValueError("Satır toplamı ve KDV oranı sayıları eşit olmalıdır")
    totals = [money(value) for value in line_totals]
    shares = distribute_amount(discount_amount, totals)
    result: list[VatInclusiveDiscountedLine] = []
    for total, rate, share in zip(totals, tax_rates, shares, strict=True):
        discounted_total = money(total - share)
        pct = percentage(rate)
        subtotal = (
            money(discounted_total / (Decimal("1") + pct / HUNDRED))
            if pct else discounted_total
        )
        tax = money(discounted_total - subtotal)
        result.append(VatInclusiveDiscountedLine(subtotal, tax, discounted_total, share))
    if money(sum((line.total for line in result), ZERO)) != money(
        sum(totals, ZERO) - money(discount_amount)
    ):
        raise ValueError("Satır toplamları belge toplamıyla mutabık değil")
    return result


def apply_global_discount(
    subtotal: object,
    vat_total: object,
    gross_total: object,
    discount_percent: object,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Apply a document-level discount without corrupting the VAT base.

    Unit prices in this project are VAT-inclusive. A document-level discount
    therefore has to reduce both the taxable base and VAT proportionally. The
    final VAT value is derived from the rounded final total so the accounting
    invariant is always true::

        discounted_subtotal + discounted_vat == final_total

    Returns ``(discounted_subtotal, discounted_vat, discount_amount,
    final_total)``.
    """

    original_subtotal = money(subtotal)
    original_vat = money(vat_total)
    original_gross = money(gross_total)
    pct = percentage(discount_percent)

    if pct < ZERO or pct > HUNDRED:
        raise ValueError("İndirim yüzdesi 0 ile 100 arasında olmalıdır")

    discount_amount = money(original_gross * pct / HUNDRED)
    final_total = money(original_gross - discount_amount)
    if original_gross == ZERO_MONEY:
        return ZERO_MONEY, ZERO_MONEY, discount_amount, final_total

    factor = (HUNDRED - pct) / HUNDRED
    discounted_subtotal = money(original_subtotal * factor)
    # Derive VAT from the final rounded total to prevent one-cent drift.
    discounted_vat = money(final_total - discounted_subtotal)

    if discounted_vat < ZERO_MONEY:
        raise ValueError("İndirim sonrası KDV toplamı negatif olamaz")
    if money(discounted_subtotal + discounted_vat) != final_total:
        raise ValueError("Belge toplamı muhasebe mutabakatını sağlamıyor")

    return discounted_subtotal, discounted_vat, discount_amount, final_total


def apply_global_discount_amount(
    subtotal: object,
    vat_total: object,
    gross_total: object,
    discount_value: object,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Apply an exact document discount while preserving the VAT split."""

    original_subtotal = money(subtotal)
    original_vat = money(vat_total)
    original_gross = money(gross_total)
    discount_amount = money(discount_value)
    if discount_amount < ZERO_MONEY or discount_amount > original_gross:
        raise ValueError("Belge iskontosu 0 ile belge toplamı arasında olmalıdır")

    final_total = money(original_gross - discount_amount)
    if original_gross == ZERO_MONEY:
        return ZERO_MONEY, ZERO_MONEY, discount_amount, final_total

    factor = final_total / original_gross
    discounted_subtotal = money(original_subtotal * factor)
    discounted_vat = money(final_total - discounted_subtotal)
    if discounted_vat < ZERO_MONEY:
        raise ValueError("İndirim sonrası KDV toplamı negatif olamaz")
    if money(discounted_subtotal + discounted_vat) != final_total:
        raise ValueError("Belge toplamı muhasebe mutabakatını sağlamıyor")
    return discounted_subtotal, discounted_vat, discount_amount, final_total
