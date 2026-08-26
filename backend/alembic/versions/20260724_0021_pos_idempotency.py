"""Race-safe POS idempotency and tenant system customer mapping.

The old POS retry guard encoded a truncated hash into ``orders.document_no``.
That field is not uniquely constrained, so two concurrent retries could both
create a sale, stock movement and payment. These dedicated tables make the
concurrency invariant explicit with tenant-scoped primary keys and keep normal
human-readable document numbering intact.

Revision ID: 20260724_0021
Revises: 20260724_0020
Create Date: 2026-07-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260724_0021"
down_revision = "20260724_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("pos_idempotency"):
        op.create_table(
            "pos_idempotency",
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.String(length=40), nullable=False),
            sa.PrimaryKeyConstraint(
                "company_id", "idempotency_key", name="pk_pos_idempotency"
            ),
        )
        op.create_index(
            "ix_pos_idempotency_order",
            "pos_idempotency",
            ["company_id", "order_id"],
            unique=True,
        )

    if not inspector.has_table("pos_system_customers"):
        op.create_table(
            "pos_system_customers",
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.String(length=40), nullable=False),
            sa.PrimaryKeyConstraint("company_id", name="pk_pos_system_customers"),
            sa.UniqueConstraint("customer_id", name="uq_pos_system_customers_customer"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("pos_system_customers"):
        op.drop_table("pos_system_customers")
    if inspector.has_table("pos_idempotency"):
        op.drop_index("ix_pos_idempotency_order", table_name="pos_idempotency")
        op.drop_table("pos_idempotency")
