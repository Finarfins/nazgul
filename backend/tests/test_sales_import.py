"""Focused tests for the BizimHesap sales-report import endpoint.

The endpoint turns a line-based BizimHesap "SATIŞ RAPORU" export into
stock-neutral ``draft`` sale documents, grouped by (customer + day). These
tests pin the two behaviours that are easy to regress: the date parser and the
source-level invariants (tenant scoping, stock-neutral status, Net-based unit
price) that keep the migration safe.
"""
from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from app.routers.imports import _sales_unit_price, _to_iso_date
from app.routers.transactions import _totals
from app.schemas import TransactionCreate, TransactionItem

IMPORTS_ROUTER = Path(__file__).resolve().parents[1] / "app" / "routers" / "imports.py"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("19.05.2023", "2023-05-19"),
        ("1.2.2024", "2024-02-01"),
        ("08/12/2023", "2023-12-08"),
        ("2023-05-19", "2023-05-19"),
        ("2023-05-19T10:00:00", "2023-05-19"),
        ("", None),
        (None, None),
        ("gecersiz", None),
    ],
)
def test_to_iso_date(value, expected) -> None:
    assert _to_iso_date(value) == expected


def _import_sales_source() -> str:
    source = IMPORTS_ROUTER.read_text(encoding="utf-8")
    module = ast.parse(source)
    func = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "import_sales"
    )
    return ast.get_source_segment(source, func) or ""


def test_import_sales_is_stock_neutral_draft() -> None:
    # Stock is migrated at its current level, so replayed sales must NOT move it.
    # draft is outside STOCK_STATUSES, guaranteeing stock-neutral documents.
    source = _import_sales_source()
    assert "status='draft'" in source or 'status="draft"' in source
    assert "completed" not in source


def test_import_sales_is_tenant_scoped() -> None:
    source = _import_sales_source()
    assert "company_id(request)" in source
    assert "company_id=:cid" in source


def test_import_sales_derives_price_via_helper() -> None:
    source = _import_sales_source()
    assert "_sales_unit_price(" in source


# --- Real money math ---------------------------------------------------------
# These exercise the actual calculation instead of inspecting source text: a
# VAT-exclusive Net of 1000 at 20% must produce a 1200 document, not 1000.


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    """Minimal stand-in for the Session lookup ``_totals`` performs."""

    def __init__(self, products):
        self._products = products

    def execute(self, statement, params=None):
        return _FakeResult(self._products)


@pytest.mark.parametrize(
    "net,gross,vat,qty,expected_unit_price",
    [
        # TOPLAM present: used directly as the VAT-inclusive line total.
        (Decimal("1000"), Decimal("1200"), 20, Decimal("1"), Decimal("1200")),
        (Decimal("2166.67"), Decimal("2600"), 20, Decimal("10"), Decimal("260")),
        # TOPLAM missing: VAT is added back onto Net.
        (Decimal("1000"), Decimal("0"), 20, Decimal("1"), Decimal("1200")),
        # Zero-rated lines pass through unchanged.
        (Decimal("180"), Decimal("180"), 0, Decimal("6"), Decimal("30")),
    ],
)
def test_sales_unit_price_is_vat_inclusive(net, gross, vat, qty, expected_unit_price) -> None:
    price = _sales_unit_price(net=net, gross=gross, vat_rate=vat, qty=qty)
    assert price == pytest.approx(expected_unit_price)


def test_imported_line_totals_match_source_document() -> None:
    """Net 1000 + 20% VAT must land as subtotal 1000 / VAT 200 / total 1200."""
    unit_price = _sales_unit_price(
        net=Decimal("1000"), gross=Decimal("1200"), vat_rate=20, qty=Decimal("1")
    )
    payload = TransactionCreate(
        entity_id=1,
        transaction_date="2024-05-19",
        status="draft",
        items=[TransactionItem(product_id=1, quantity=Decimal("1"), unit_price=unit_price, vat_rate=20)],
    )
    db = _FakeDB([{"id": 1, "name": "Test Ürün"}])

    _rows, subtotal, vat_total, gross_total, _discount, final_total = _totals(db, payload, cid=1)

    assert subtotal == Decimal("1000.00")
    assert vat_total == Decimal("200.00")
    assert gross_total == Decimal("1200.00")
    assert final_total == Decimal("1200.00")
