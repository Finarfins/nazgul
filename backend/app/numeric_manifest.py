from __future__ import annotations

"""Canonical precision contract for financial and quantity columns.

The same manifest is consumed by SQLAlchemy model tests, Alembic migrations,
and migration reconciliation tooling. Keeping a single source of truth prevents
new numeric columns from silently escaping production hardening.
"""

MONEY_PRECISION = 18
MONEY_SCALE = 2
QUANTITY_PRECISION = 18
QUANTITY_SCALE = 4

MONEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "customers": ("opening_balance", "risk_limit"),
    "suppliers": ("opening_balance", "risk_limit"),
    "products": ("purchase_price", "sale_price", "vat_rate"),
    "orders": (
        "subtotal", "vat_total", "grand_total", "discount_percent",
        "discount_amount", "final_total", "paid_amount",
    ),
    "order_items": (
        "unit_price", "vat_rate", "line_subtotal", "line_vat", "line_total",
        "discount_percent", "discount_amount",
    ),
    "purchases": (
        "subtotal", "vat_total", "grand_total", "discount_percent",
        "discount_amount", "final_total", "paid_amount",
    ),
    "purchase_items": (
        "unit_price", "vat_rate", "line_subtotal", "line_vat", "line_total",
        "discount_percent", "discount_amount",
    ),
    "payments": ("amount",),
    "quotes": (
        "total", "subtotal", "vat_total", "discount_percent",
        "discount_amount", "grand_total",
    ),
    "quote_items": (
        "unit_price", "vat_rate", "line_total", "discount_percent",
        "discount_amount", "line_subtotal", "line_vat",
    ),
    "returns": ("total", "subtotal", "vat_total"),
    "return_items": ("unit_price", "line_total", "vat_rate", "line_subtotal", "line_vat"),
    "income_expenses": ("amount",),
    "sales_orders": (
        "subtotal", "vat_total", "discount_percent", "discount_amount", "grand_total",
    ),
    "sales_order_items": (
        "unit_price", "vat_rate", "discount_percent", "discount_amount",
        "line_subtotal", "line_vat", "line_total",
    ),
    "delivery_notes": ("total",),
    "delivery_note_items": ("unit_price", "line_total"),
    "finance_accounts": ("opening_balance",),
    "finance_transactions": ("amount",),
    "financial_instruments": ("amount",),
}

QUANTITY_COLUMNS: dict[str, tuple[str, ...]] = {
    "products": ("stock", "units_per_box", "critical_stock", "minimum_stock"),
    "order_items": ("quantity",),
    "purchase_items": ("quantity",),
    "stock_movements": ("quantity",),
    "quote_items": ("quantity",),
    "return_items": ("quantity",),
    "sales_order_items": ("quantity",),
    "delivery_note_items": ("quantity",),
    "warehouse_stocks": ("quantity", "critical_stock"),
    "stock_transfer_items": ("quantity",),
}


def iter_numeric_columns():
    """Yield ``(table, column, precision, scale, kind)`` tuples."""
    for table_name, column_names in MONEY_COLUMNS.items():
        for column_name in column_names:
            yield table_name, column_name, MONEY_PRECISION, MONEY_SCALE, "money"
    for table_name, column_names in QUANTITY_COLUMNS.items():
        for column_name in column_names:
            yield table_name, column_name, QUANTITY_PRECISION, QUANTITY_SCALE, "quantity"
