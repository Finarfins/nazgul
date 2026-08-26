"""Add company stock/credit policies and auditable manager overrides.

Revision ID: 20260713_0003
Revises: 20260713_0002
Create Date: 2026-07-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260713_0003"
down_revision: Union[str, None] = "20260713_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    company_columns = (
        {column["name"] for column in inspector.get_columns("companies")}
        if inspector.has_table("companies")
        else set()
    )
    if "negative_stock_policy" not in company_columns:
        op.add_column(
            "companies",
            sa.Column(
                "negative_stock_policy",
                sa.String(length=30),
                nullable=False,
                server_default="block",
            ),
        )
    if "credit_limit_policy" not in company_columns:
        op.add_column(
            "companies",
            sa.Column(
                "credit_limit_policy",
                sa.String(length=30),
                nullable=False,
                server_default="block",
            ),
        )

    inspector = sa.inspect(bind)
    if not inspector.has_table("policy_override_logs"):
        op.create_table(
            "policy_override_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("username", sa.String(length=80), nullable=False),
            sa.Column("policy_name", sa.String(length=60), nullable=False),
            sa.Column("resource_type", sa.String(length=80), nullable=False),
            sa.Column("resource_id", sa.Integer()),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("request_id", sa.String(length=64)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_policy_override_logs_company_created",
            "policy_override_logs",
            ["company_id", "created_at"],
        )
        op.create_index(
            "ix_policy_override_logs_user",
            "policy_override_logs",
            ["user_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("policy_override_logs"):
        op.drop_index("ix_policy_override_logs_user", table_name="policy_override_logs")
        op.drop_index(
            "ix_policy_override_logs_company_created",
            table_name="policy_override_logs",
        )
        op.drop_table("policy_override_logs")
    company_columns = (
        {column["name"] for column in sa.inspect(bind).get_columns("companies")}
        if sa.inspect(bind).has_table("companies")
        else set()
    )
    if "credit_limit_policy" in company_columns:
        op.drop_column("companies", "credit_limit_policy")
    if "negative_stock_policy" in company_columns:
        op.drop_column("companies", "negative_stock_policy")
