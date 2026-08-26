"""Unit tests for money.distribute_amount (F2 discount allocation)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.money import distribute_amount, money


CASES = [
    # (amount, weights, expected shares) — deterministic largest-remainder
    ("10", ["300", "216"], ["5.81", "4.19"]),          # part has the larger remainder
    ("10.00", ["1", "1", "1"], ["3.34", "3.33", "3.33"]),  # tie → lowest index first
    ("7.77", ["333", "333", "333"], ["2.59", "2.59", "2.59"]),
    ("0", ["5", "5"], ["0.00", "0.00"]),
    ("100", ["0", "0"], ["0.00", "0.00"]),             # non-positive weight sum → zeros
]


@pytest.mark.parametrize("amount,weights,expected", CASES)
def test_distribute_amount_is_exact_and_deterministic(amount, weights, expected):
    shares = distribute_amount(amount, [Decimal(w) for w in weights])
    assert shares == [Decimal(e) for e in expected]


@pytest.mark.parametrize("amount,weights,expected", CASES)
def test_shares_sum_to_amount(amount, weights, expected):
    shares = distribute_amount(amount, [Decimal(w) for w in weights])
    total = sum(shares, Decimal("0"))
    if sum(Decimal(w) for w in weights) > 0 and Decimal(amount) != 0:
        assert total == money(amount)
    else:
        assert total == Decimal("0.00")


def test_no_share_exceeds_its_weight_for_valid_discount():
    # a discount never exceeds the billed total, so no allocated share may exceed
    # its line weight (largest-remainder keeps near-full lines from over-allocating)
    weights = [Decimal("0.05"), Decimal("300"), Decimal("216"), Decimal("0.10")]
    shares = distribute_amount(Decimal("500"), weights)
    assert sum(shares, Decimal("0")) == Decimal("500.00")
    for share, weight in zip(shares, weights):
        assert share <= weight


def test_full_discount_allocates_each_full_line():
    weights = [Decimal("300"), Decimal("216")]
    shares = distribute_amount(Decimal("516"), weights)
    assert shares == weights  # 100% discount → each line fully discounted
