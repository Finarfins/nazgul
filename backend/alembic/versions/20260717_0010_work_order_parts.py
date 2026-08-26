"""Add V3.2 work order used parts.

Revision ID: 20260717_0010
Revises: 20260717_0009
"""

from alembic import op
import sqlalchemy as sa


revision = "20260717_0010"
down_revision = "20260717_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("work_order_parts"):
        return
    op.create_table(
        "work_order_parts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("work_order_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("discount", sa.Numeric(9, 4), nullable=False, server_default="0"),
        sa.Column("tax_rate", sa.Numeric(9, 4), nullable=False, server_default="0"),
        sa.Column("total_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_work_order_parts_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_work_order_parts_unit_price_nonnegative"),
        sa.CheckConstraint("discount >= 0 AND discount <= 100", name="ck_work_order_parts_discount"),
        sa.CheckConstraint("tax_rate >= 0", name="ck_work_order_parts_tax_rate"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.UniqueConstraint("company_id", "work_order_id", "product_id", "warehouse_id", name="uq_work_order_parts_line"),
    )
    op.create_index("ix_work_order_parts_company_work_order", "work_order_parts", ["company_id", "work_order_id"])
    op.create_index("ix_work_order_parts_company_product", "work_order_parts", ["company_id", "product_id"])


def downgrade() -> None:
    op.drop_index("ix_work_order_parts_company_product", table_name="work_order_parts")
    op.drop_index("ix_work_order_parts_company_work_order", table_name="work_order_parts")
    op.drop_table("work_order_parts")
