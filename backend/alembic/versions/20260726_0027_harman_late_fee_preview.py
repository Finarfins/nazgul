"""Harman V2a: tenant-scoped late-fee policies for read-only previews.

Revision ID: 20260726_0027
Revises: 20260726_0026
"""

from alembic import op
import sqlalchemy as sa


revision = "20260726_0027"
down_revision = "20260726_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("late_fee_policies"):
        return

    op.create_table(
        "late_fee_policies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer()),
        sa.Column("annual_rate", sa.Numeric(9, 4), nullable=False),
        sa.Column(
            "day_count_basis",
            sa.Integer(),
            nullable=False,
            server_default="365",
        ),
        sa.Column(
            "grace_days",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "tax_mode",
            sa.String(length=20),
            nullable=False,
            server_default="NO_VAT",
        ),
        sa.Column(
            "vat_rate",
            sa.Numeric(9, 4),
            nullable=False,
            server_default="0",
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date()),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("created_by", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "annual_rate >= 0",
            name="ck_late_fee_policy_rate_nonnegative",
        ),
        sa.CheckConstraint(
            "day_count_basis = 365",
            name="ck_late_fee_policy_day_count_basis",
        ),
        sa.CheckConstraint(
            "grace_days >= 0",
            name="ck_late_fee_policy_grace_nonnegative",
        ),
        sa.CheckConstraint(
            "tax_mode = 'NO_VAT'",
            name="ck_late_fee_policy_tax_mode",
        ),
        sa.CheckConstraint(
            "vat_rate = 0",
            name="ck_late_fee_policy_vat_rate",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_late_fee_policy_effective_range",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["company_id", "customer_id"],
            ["customers.company_id", "customers.id"],
        ),
        sa.ForeignKeyConstraint(["created_by"], ["app_users.id"]),
        sa.UniqueConstraint(
            "company_id",
            "id",
            name="uq_late_fee_policies_company_id",
        ),
    )
    op.create_index(
        "ix_late_fee_policies_resolution",
        "late_fee_policies",
        [
            "company_id",
            "customer_id",
            "active",
            "effective_from",
            "effective_to",
        ],
    )


def downgrade() -> None:
    op.drop_table("late_fee_policies")
