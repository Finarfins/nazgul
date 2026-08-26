"""Purchase engine B/C — discount tiers, VAT flag, reorder policy, draft idempotency.

Adds the four schema deltas the B/C design note requires:

- ``supplier_price_discount_tiers`` : quantity -> discount ladder for one manual
  supplier price. The flat ``supplier_product_prices.discount_percent`` stays and
  is the fallback when no tier matches the evaluated quantity.
- ``supplier_product_prices.price_includes_vat`` : per-record VAT contract. The
  application-wide ``unit_price`` contract is VAT-INCLUSIVE, so the default is
  true; a supplier quoting net prices is switched off on the record itself.
- ``products.reorder_point`` / ``products.target_stock`` : nullable manual
  overrides. NULL means "derive from demand"; 0 is a real override and must not
  be confused with NULL.
- ``purchase_draft_idempotency`` : replay guard for grouped reorder drafts,
  mirroring ``pos_idempotency``.

Revision ID: 20260726_0024
Revises: 20260725_0023
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_0024"
down_revision = "20260725_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("supplier_price_discount_tiers"):
        op.create_table(
            "supplier_price_discount_tiers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("supplier_price_id", sa.Integer(), nullable=False),
            # The ladder step: this discount applies from `min_quantity` upwards
            # until a higher step takes over. Quantity scale matches every other
            # quantity column (NUMERIC(18,4)).
            sa.Column("min_quantity", sa.Numeric(18, 4), nullable=False),
            sa.Column("discount_percent", sa.Numeric(9, 4), nullable=False),
            sa.CheckConstraint("min_quantity > 0", name="ck_price_tier_min_quantity_positive"),
            sa.CheckConstraint(
                "discount_percent >= 0 AND discount_percent <= 100",
                name="ck_price_tier_discount_range",
            ),
            sa.UniqueConstraint(
                "company_id", "supplier_price_id", "min_quantity",
                name="uq_price_tier_company_price_quantity",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["supplier_price_id"], ["supplier_product_prices.id"], ondelete="CASCADE",
            ),
        )
        op.create_index(
            "ix_price_tier_company_price",
            "supplier_price_discount_tiers", ["company_id", "supplier_price_id"],
        )

    price_columns = {c["name"] for c in inspector.get_columns("supplier_product_prices")}
    if "price_includes_vat" not in price_columns:
        op.add_column(
            "supplier_product_prices",
            sa.Column(
                "price_includes_vat", sa.Boolean(), nullable=False, server_default=sa.true(),
            ),
        )

    product_columns = {c["name"] for c in inspector.get_columns("products")}
    # Nullable on purpose: NULL = derive from 90-day demand, a value (including 0)
    # = explicit buyer override.
    if "reorder_point" not in product_columns:
        op.add_column("products", sa.Column("reorder_point", sa.Numeric(18, 4)))
    if "target_stock" not in product_columns:
        op.add_column("products", sa.Column("target_stock", sa.Numeric(18, 4)))

    if not inspector.has_table("purchase_draft_idempotency"):
        op.create_table(
            "purchase_draft_idempotency",
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
            # Comma-separated purchase ids created by the original request; a
            # replay re-reads them instead of creating a second set of drafts.
            sa.Column("purchase_ids", sa.Text(), nullable=False),
            sa.Column("created_at", sa.String(length=40), nullable=False),
            sa.PrimaryKeyConstraint(
                "company_id", "idempotency_key", name="pk_purchase_draft_idempotency",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("purchase_draft_idempotency"):
        op.drop_table("purchase_draft_idempotency")

    product_columns = {c["name"] for c in inspector.get_columns("products")}
    if "target_stock" in product_columns:
        op.drop_column("products", "target_stock")
    if "reorder_point" in product_columns:
        op.drop_column("products", "reorder_point")

    price_columns = {c["name"] for c in inspector.get_columns("supplier_product_prices")}
    if "price_includes_vat" in price_columns:
        op.drop_column("supplier_product_prices", "price_includes_vat")

    if inspector.has_table("supplier_price_discount_tiers"):
        op.drop_index(
            "ix_price_tier_company_price", table_name="supplier_price_discount_tiers",
        )
        op.drop_table("supplier_price_discount_tiers")
