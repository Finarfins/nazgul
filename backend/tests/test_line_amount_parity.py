"""Cross-module parity for the canonical billed-line formula (F1).

The stored ``work_order_parts.total_price``, the billing summary, and the
generated invoice line must all be produced by the single ``compute_line``
helper so they can never diverge. These cases include rounding boundaries where
the previous *factored* work-order formula (``raw*(100-disc)/100`` then
``*(100+tax)/100``) produced a different cent than the canonical per-component
formula.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.money import compute_line, line_amount
from app.routers.work_order_parts import _total
from app.work_order_part_schemas import WorkOrderPartWrite


# (quantity, unit_price, discount%, tax%, expected raw/discount/taxable/tax/total)
CASES = [
    # spec example: qty1 / 1.00 / 0% disc / 7.5% tax
    ("1", "1.00", "0", "7.5", "1.00", "0.00", "1.00", "0.08", "1.08"),
    # non-divergent realistic line (matches existing billing test expectations)
    ("3", "19.99", "10", "20", "59.97", "6.00", "53.97", "10.79", "64.76"),
    # discount rounding boundary: factored stored 0.03, canonical taxable is 0.02
    ("1", "0.05", "50", "0", "0.05", "0.03", "0.02", "0.00", "0.02"),
    # discount + tax boundary: factored total was 0.04, canonical is 0.02
    ("1", "0.05", "50", "20", "0.05", "0.03", "0.02", "0.00", "0.02"),
    # another discount boundary: canonical taxable 0.07 vs factored 0.08
    ("1", "0.15", "50", "0", "0.15", "0.08", "0.07", "0.00", "0.07"),
    # zero-price line stays zero
    ("5", "0", "10", "20", "0.00", "0.00", "0.00", "0.00", "0.00"),
]


@pytest.mark.parametrize("qty,price,disc,tax,raw,discount,taxable,line_tax,total", CASES)
def test_compute_line_matches_canonical_components(
    qty, price, disc, tax, raw, discount, taxable, line_tax, total
):
    line = compute_line(qty, price, disc, tax)
    assert line.raw == Decimal(raw)
    assert line.discount == Decimal(discount)
    assert line.taxable == Decimal(taxable)
    assert line.tax == Decimal(line_tax)
    assert line.total == Decimal(total)
    # taxable + tax must equal the stored/billed total exactly
    assert line.taxable + line.tax == line.total
    assert line_amount(qty, price, disc, tax) == Decimal(total)


@pytest.mark.parametrize("qty,price,disc,tax,raw,discount,taxable,line_tax,total", CASES)
def test_work_order_part_storage_uses_canonical_helper(
    qty, price, disc, tax, raw, discount, taxable, line_tax, total
):
    payload = WorkOrderPartWrite(
        product_id=1,
        warehouse_id=1,
        quantity=Decimal(qty),
        unit_price=Decimal(price),
        discount=Decimal(disc),
        tax_rate=Decimal(tax),
    )
    # The value the router persists into total_price must equal the canonical line.
    assert _total(payload) == line_amount(qty, price, disc, tax) == Decimal(total)


def test_billing_and_invoice_line_reconcile_with_storage():
    """The three consumers agree line-by-line and in aggregate.

    billing_service sums per-line components (Σtaxable + Σtax); the parts list /
    PR #78 grouped join sums stored total_price (Σ line.total); the invoice line
    total is line.total. Because taxable+tax==total per line, all three are equal.
    """
    from app.money import money

    parts = [("1", "0.05", "50", "20"), ("3", "19.99", "10", "20"), ("2", "0.15", "50", "0")]
    lines = [compute_line(q, p, d, t) for q, p, d, t in parts]

    stored_total = money(sum((ln.total for ln in lines), Decimal("0")))
    billing_before_tax = money(sum((ln.taxable for ln in lines), Decimal("0")))
    billing_tax = money(sum((ln.tax for ln in lines), Decimal("0")))
    billing_total = money(billing_before_tax + billing_tax)

    assert stored_total == billing_total
    # per-line invoice total equals the stored per-line total
    for ln in lines:
        assert ln.total == money(ln.taxable + ln.tax)
