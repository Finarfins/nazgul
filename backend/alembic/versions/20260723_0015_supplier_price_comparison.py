"""V3 Purchase Comparison Engine - supplier prices, FX rates, price history.

Designs the full engine (Increments A/B/C) in one migration; Increment A only
surfaces a subset in the API/UI. All monetary values stay NUMERIC (never float)
and every business table is tenant-scoped by company_id, mirroring the existing
V3 tables (machines, work_orders).

Tables
------
- supplier_product_prices : one manual price row per (company, supplier, product)
  in a chosen currency. B-fields (moq, lead_time_days, discount_percent) and
  supplier_stock are created now but left unused by Increment A.
- exchange_rates : GLOBAL TCMB daily rates (currency -> TRY). Objective
  reference data shared by all tenants, so intentionally NOT company-scoped.
- company_exchange_rate_overrides : per-company manual override of a currency's
  TRY rate; when present it wins over the TCMB row for that tenant.
- supplier_price_history : dated observations (manual or purchase-derived) that
  back the future price graph (Increment B). Captured now; not surfaced in A.

Revision ID: 20260723_0015
Revises: 20260722_0014
"""

from alembic import op
import sqlalchemy as sa


revision = "20260723_0015"
down_revision = "20260722_0014"
branch_labels = None
depends_on = None

CURRENCIES = ("TRY", "EUR", "USD")
PRICE_SOURCES = ("MANUAL", "PURCHASE")


def _currency_check(column: str, name: str) -> sa.CheckConstraint:
    allowed = ",".join(f"'{code}'" for code in CURRENCIES)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=name)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("supplier_product_prices"):
        op.create_table(
            "supplier_product_prices",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("supplier_id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            # Unit price in `currency`; 4 dp keeps foreign-currency unit prices
            # precise before normalisation to TRY.
            sa.Column("price", sa.Numeric(18, 4), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False, server_default="TRY"),
            # --- Increment B fields (designed now, unused by A) ---
            sa.Column("moq", sa.Numeric(18, 4)),
            sa.Column("lead_time_days", sa.Integer()),
            sa.Column("discount_percent", sa.Numeric(9, 4)),
            sa.Column("supplier_stock", sa.Numeric(18, 4)),
            # ------------------------------------------------------
            sa.Column("note", sa.Text()),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by", sa.Integer()),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            _currency_check("currency", "ck_supplier_prices_currency"),
            sa.CheckConstraint("price >= 0", name="ck_supplier_prices_price_nonneg"),
            sa.CheckConstraint(
                "discount_percent IS NULL OR (discount_percent >= 0 AND discount_percent <= 100)",
                name="ck_supplier_prices_discount_range",
            ),
            sa.UniqueConstraint(
                "company_id", "supplier_id", "product_id",
                name="uq_supplier_prices_company_supplier_product",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["app_users.id"]),
        )
        op.create_index(
            "ix_supplier_prices_company_product",
            "supplier_product_prices", ["company_id", "product_id"],
        )
        op.create_index(
            "ix_supplier_prices_company_supplier",
            "supplier_product_prices", ["company_id", "supplier_id"],
        )

    # Platform-global TCMB public reference data. Company-specific rate
    # overrides belong in ``company_exchange_rate_overrides`` below.
    if not inspector.has_table("exchange_rates"):
        op.create_table(
            "exchange_rates",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("rate_date", sa.Date(), nullable=False),
            # 1 unit of `currency` = rate_to_try TRY. 6 dp for FX precision.
            sa.Column("rate_to_try", sa.Numeric(18, 6), nullable=False),
            sa.Column("source", sa.String(length=20), nullable=False, server_default="TCMB"),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            _currency_check("currency", "ck_exchange_rates_currency"),
            sa.CheckConstraint("rate_to_try > 0", name="ck_exchange_rates_positive"),
            sa.UniqueConstraint(
                "currency", "rate_date", "source", name="uq_exchange_rates_ccy_date_source",
            ),
        )
        op.create_index(
            "ix_exchange_rates_currency_date",
            "exchange_rates", ["currency", "rate_date"],
        )

    if not inspector.has_table("company_exchange_rate_overrides"):
        op.create_table(
            "company_exchange_rate_overrides",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("rate_to_try", sa.Numeric(18, 6), nullable=False),
            sa.Column("note", sa.Text()),
            sa.Column("updated_by", sa.Integer()),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            _currency_check("currency", "ck_fx_override_currency"),
            sa.CheckConstraint("rate_to_try > 0", name="ck_fx_override_positive"),
            sa.UniqueConstraint(
                "company_id", "currency", name="uq_fx_override_company_currency",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["updated_by"], ["app_users.id"]),
        )

    if not inspector.has_table("supplier_price_history"):
        sources = ",".join(f"'{s}'" for s in PRICE_SOURCES)
        op.create_table(
            "supplier_price_history",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("supplier_id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("price", sa.Numeric(18, 4), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False, server_default="TRY"),
            # Normalised snapshot at capture time; nullable when no rate was known.
            sa.Column("price_in_try", sa.Numeric(18, 4)),
            sa.Column("source", sa.String(length=20), nullable=False),
            sa.Column("note", sa.Text()),
            sa.Column(
                "captured_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            _currency_check("currency", "ck_price_history_currency"),
            sa.CheckConstraint(f"source IN ({sources})", name="ck_price_history_source"),
            sa.CheckConstraint("price >= 0", name="ck_price_history_price_nonneg"),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        )
        op.create_index(
            "ix_price_history_company_product_time",
            "supplier_price_history", ["company_id", "product_id", "captured_at"],
        )
        op.create_index(
            "ix_price_history_company_supplier_product",
            "supplier_price_history", ["company_id", "supplier_id", "product_id"],
        )


def downgrade() -> None:
    op.drop_table("supplier_price_history")
    op.drop_table("company_exchange_rate_overrides")
    op.drop_table("exchange_rates")
    op.drop_table("supplier_product_prices")
