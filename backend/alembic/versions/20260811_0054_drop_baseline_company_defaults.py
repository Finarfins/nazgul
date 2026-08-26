"""Drop silent company 1 defaults from baseline tenant tables.

Revision ID: 20260811_0054
Revises: 20260808_0053
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260811_0054"
down_revision = "20260808_0053"
branch_labels = None
depends_on = None


BASELINE_TENANT_TABLES = (
    "customers",
    "suppliers",
    "products",
    "orders",
    "purchases",
    "payments",
    "stock_movements",
    "quotes",
    "returns",
    "income_expenses",
)


def upgrade() -> None:
    for table_name in BASELINE_TENANT_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "company_id",
                existing_type=sa.Integer(),
                existing_nullable=False,
                server_default=None,
            )


def downgrade() -> None:
    for table_name in BASELINE_TENANT_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "company_id",
                existing_type=sa.Integer(),
                existing_nullable=False,
                server_default=sa.text("1"),
            )
