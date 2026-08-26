"""Add V3.3 work order warranty billing fields.

Revision ID: 20260717_0011
Revises: 20260717_0010
"""

from alembic import op
import sqlalchemy as sa


revision = "20260717_0011"
down_revision = "20260717_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("work_orders")}
    if "warranty_type" not in columns:
        op.add_column("work_orders", sa.Column("warranty_type", sa.String(20), nullable=False, server_default="NONE"))
    if "warranty_percent" not in columns:
        op.add_column("work_orders", sa.Column("warranty_percent", sa.Numeric(9, 4), nullable=False, server_default="0"))
    op.execute("UPDATE work_orders SET warranty_type='FULL', warranty_percent=100 WHERE warranty=TRUE")


def downgrade() -> None:
    op.drop_column("work_orders", "warranty_percent")
    op.drop_column("work_orders", "warranty_type")
