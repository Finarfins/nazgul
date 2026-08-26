"""Persistent receivable payment-allocation foundation.

Revision ID: 20260726_0025
Revises: 20260726_0024
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_0025"
down_revision = "20260726_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("payment_allocations"):
        op.create_table(
            "payment_allocations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("payment_id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("amount", sa.Numeric(18, 2), nullable=False),
            sa.Column("allocation_type", sa.String(length=30), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=False),
            sa.Column(
                "recorded_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("created_by", sa.Integer()),
            sa.Column("reversed_at", sa.DateTime(timezone=True)),
            sa.Column("reversed_by", sa.Integer()),
            sa.Column("reversal_allocation_id", sa.Integer()),
            sa.Column("reversal_of_allocation_id", sa.Integer()),
            sa.Column("note", sa.Text()),
            sa.CheckConstraint("amount > 0", name="ck_payment_allocation_amount_positive"),
            sa.CheckConstraint(
                "allocation_type IN ('manual','fifo','document_create','provider')",
                name="ck_payment_allocation_type",
            ),
            sa.CheckConstraint(
                "reversal_allocation_id IS NULL OR reversal_allocation_id <> id",
                name="ck_payment_allocation_not_self_reversed",
            ),
            sa.CheckConstraint(
                "reversal_of_allocation_id IS NULL OR reversal_of_allocation_id <> id",
                name="ck_payment_allocation_not_self_reversal",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(
                ["reversal_allocation_id"],
                ["payment_allocations.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["reversal_of_allocation_id"],
                ["payment_allocations.id"],
                ondelete="RESTRICT",
            ),
        )
        op.create_index(
            "ix_payment_allocations_company_payment",
            "payment_allocations",
            ["company_id", "payment_id"],
        )
        op.create_index(
            "ix_payment_allocations_company_order",
            "payment_allocations",
            ["company_id", "order_id"],
        )
        op.create_index(
            "ix_payment_allocations_company_order_effective",
            "payment_allocations",
            ["company_id", "order_id", "effective_date"],
        )
        op.create_index(
            "ix_payment_allocations_company_payment_effective",
            "payment_allocations",
            ["company_id", "payment_id", "effective_date"],
        )
        op.create_index(
            "ix_payment_allocations_active_order",
            "payment_allocations",
            ["company_id", "order_id"],
            unique=False,
            postgresql_where=sa.text("reversed_at IS NULL"),
            sqlite_where=sa.text("reversed_at IS NULL"),
        )

    if not inspector.has_table("payment_idempotency"):
        op.create_table(
            "payment_idempotency",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("operation_type", sa.String(length=40), nullable=False),
            sa.Column("resource_type", sa.String(length=40), nullable=False),
            sa.Column("resource_id", sa.String(length=255), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("result_snapshot", sa.Text()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint(
                "status IN ('processing','completed','failed')",
                name="ck_payment_idempotency_status",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.UniqueConstraint(
                "company_id",
                "operation_type",
                "resource_type",
                "resource_id",
                "idempotency_key",
                name="uq_payment_idempotency_operation",
            ),
        )
        op.create_index(
            "ix_payment_idempotency_company_resource",
            "payment_idempotency",
            ["company_id", "resource_type", "resource_id"],
        )

    if not inspector.has_table("receivable_legacy_residuals"):
        op.create_table(
            "receivable_legacy_residuals",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("amount", sa.Numeric(18, 2), nullable=False),
            sa.Column(
                "cutover_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("evidence_source", sa.String(length=80), nullable=False),
            sa.Column("reviewed_by", sa.Integer()),
            sa.Column("reviewed_at", sa.DateTime(timezone=True)),
            sa.Column("note", sa.Text()),
            sa.CheckConstraint(
                "status = 'quarantined' OR amount >= 0",
                name="ck_receivable_residual_confirmed_nonnegative",
            ),
            sa.CheckConstraint(
                "status IN ('confirmed','quarantined','resolved')",
                name="ck_receivable_residual_status",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint(
                "company_id", "order_id", name="uq_receivable_residual_company_order",
            ),
        )
        op.create_index(
            "ix_receivable_residual_company_status",
            "receivable_legacy_residuals",
            ["company_id", "status"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("receivable_legacy_residuals"):
        op.drop_table("receivable_legacy_residuals")
    if inspector.has_table("payment_idempotency"):
        op.drop_table("payment_idempotency")
    if inspector.has_table("payment_allocations"):
        op.drop_table("payment_allocations")
