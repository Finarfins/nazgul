"""Harman V2b: immutable late-fee charge documents.

Revision ID: 20260727_0028
Revises: 20260726_0027
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0028"
down_revision = "20260726_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("receivable_charge_periods"):
        op.create_table(
            "receivable_charge_periods",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("installment_id", sa.Integer()),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("period_end", sa.Date(), nullable=False),
            sa.Column("charge_kind", sa.String(length=30), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "period_end >= period_start",
                name="ck_receivable_charge_period_range",
            ),
            sa.CheckConstraint(
                "charge_kind = 'late_fee'",
                name="ck_receivable_charge_period_kind",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint(
                "company_id",
                "id",
                name="uq_receivable_charge_period_company_id",
            ),
        )
        op.create_index(
            "uq_receivable_charge_period_natural_order",
            "receivable_charge_periods",
            ["company_id", "order_id", "period_start", "period_end", "charge_kind"],
            unique=True,
            postgresql_where=sa.text("installment_id IS NULL"),
            sqlite_where=sa.text("installment_id IS NULL"),
        )
        op.create_index(
            "uq_receivable_charge_period_natural_installment",
            "receivable_charge_periods",
            [
                "company_id",
                "order_id",
                "installment_id",
                "period_start",
                "period_end",
                "charge_kind",
            ],
            unique=True,
            postgresql_where=sa.text("installment_id IS NOT NULL"),
            sqlite_where=sa.text("installment_id IS NOT NULL"),
        )

    if not inspector.has_table("receivable_charge_documents"):
        op.create_table(
            "receivable_charge_documents",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("charge_period_id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("charge_type", sa.String(length=30), nullable=False),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("period_end", sa.Date(), nullable=False),
            sa.Column("due_date_snapshot", sa.Date(), nullable=False),
            sa.Column("principal_basis", sa.Numeric(18, 2), nullable=False),
            sa.Column("annual_rate_snapshot", sa.Numeric(9, 4), nullable=False),
            sa.Column("day_count_basis_snapshot", sa.Integer(), nullable=False),
            sa.Column("grace_days_snapshot", sa.Integer(), nullable=False),
            sa.Column("tax_mode_snapshot", sa.String(length=20), nullable=False),
            sa.Column("vat_rate_snapshot", sa.Numeric(9, 4), nullable=False),
            sa.Column("calculation_snapshot", sa.Text(), nullable=False),
            sa.Column("net_amount", sa.Numeric(18, 2), nullable=False),
            sa.Column("vat_amount", sa.Numeric(18, 2), nullable=False),
            sa.Column("gross_amount", sa.Numeric(18, 2), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("calculation_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("revision_no", sa.Integer(), nullable=False),
            sa.Column("reversal_of_document_id", sa.Integer()),
            sa.Column("created_by", sa.Integer()),
            sa.Column("posted_by", sa.Integer()),
            sa.Column("reversed_by", sa.Integer()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("posted_at", sa.DateTime(timezone=True)),
            sa.Column("reversed_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint(
                "charge_type = 'late_fee'",
                name="ck_receivable_charge_document_type",
            ),
            sa.CheckConstraint(
                "status IN ('draft','posted','reversed')",
                name="ck_receivable_charge_document_status",
            ),
            sa.CheckConstraint(
                "revision_no > 0",
                name="ck_receivable_charge_document_revision",
            ),
            sa.CheckConstraint(
                "period_end >= period_start",
                name="ck_receivable_charge_document_period",
            ),
            sa.CheckConstraint(
                "(reversal_of_document_id IS NULL AND net_amount >= 0 "
                "AND vat_amount >= 0 AND gross_amount >= 0) OR "
                "(reversal_of_document_id IS NOT NULL AND net_amount <= 0 "
                "AND vat_amount <= 0 AND gross_amount <= 0)",
                name="ck_receivable_charge_document_reversal_sign",
            ),
            sa.CheckConstraint(
                "gross_amount = net_amount + vat_amount",
                name="ck_receivable_charge_document_total",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(
                ["company_id", "customer_id"],
                ["customers.company_id", "customers.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "charge_period_id"],
                ["receivable_charge_periods.company_id", "receivable_charge_periods.id"],
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(["created_by"], ["app_users.id"]),
            sa.ForeignKeyConstraint(["posted_by"], ["app_users.id"]),
            sa.ForeignKeyConstraint(["reversed_by"], ["app_users.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "reversal_of_document_id"],
                [
                    "receivable_charge_documents.company_id",
                    "receivable_charge_documents.id",
                ],
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint(
                "company_id",
                "charge_period_id",
                "revision_no",
                name="uq_receivable_charge_document_revision",
            ),
            sa.UniqueConstraint(
                "company_id",
                "id",
                name="uq_receivable_charge_document_company_id",
            ),
        )
        op.create_index(
            "ix_receivable_charge_document_aging",
            "receivable_charge_documents",
            ["company_id", "status", "due_date_snapshot"],
        )
        op.create_index(
            "uq_receivable_charge_document_reversal",
            "receivable_charge_documents",
            ["company_id", "reversal_of_document_id"],
            unique=True,
            postgresql_where=sa.text("reversal_of_document_id IS NOT NULL"),
            sqlite_where=sa.text("reversal_of_document_id IS NOT NULL"),
        )

    if not inspector.has_table("receivable_charge_idempotency"):
        op.create_table(
            "receivable_charge_idempotency",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("operation_type", sa.String(length=40), nullable=False),
            sa.Column("resource_id", sa.String(length=255), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("result_document_id", sa.Integer()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "result_document_id"],
                [
                    "receivable_charge_documents.company_id",
                    "receivable_charge_documents.id",
                ],
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint(
                "company_id",
                "operation_type",
                "resource_id",
                "idempotency_key",
                name="uq_receivable_charge_idempotency_operation",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("receivable_charge_idempotency"):
        op.drop_table("receivable_charge_idempotency")
    if inspector.has_table("receivable_charge_documents"):
        op.drop_table("receivable_charge_documents")
    if inspector.has_table("receivable_charge_periods"):
        op.drop_table("receivable_charge_periods")
