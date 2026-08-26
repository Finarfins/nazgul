"""Servis İş Emri v2 — FAZ-2: work order labor lines.

Expand-only. Adds ``work_order_labor_lines``: per-technician labor entries with
their own frozen ``hourly_rate``.

The rate is frozen at insert time (defaulting to the work order header's
``labor_rate``) and never re-derived, so a later header or price change cannot
retroactively move an already-recorded line's amount.

Nothing about the v1 header labor fields changes: ``work_orders.actual_hours``
and ``labor_rate`` stay exactly as they are, and remain the billing source until
a work order has at least one APPROVED line.

Composite tenant FK ``(company_id, work_order_id)`` targets the
``uq_work_orders_company_row`` index created in FAZ-1.

Revision ID: 20260728_0031
Revises: 20260727_0030
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_0031"
# Stacked on the activity-log panel migration (#169) so the history stays a
# single linear chain; both were authored as 0030 in parallel branches.
down_revision = "20260727_0030"
branch_labels = None
depends_on = None

LINE_STATES = ("DRAFT", "APPROVED", "VOID")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("work_order_labor_lines"):
        return
    states = ",".join(f"'{state}'" for state in LINE_STATES)
    op.create_table(
        "work_order_labor_lines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("work_order_id", sa.Integer(), nullable=False),
        sa.Column("technician_user_id", sa.Integer(), nullable=False),
        sa.Column("work_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hours", sa.Numeric(8, 2), nullable=False),
        sa.Column("hourly_rate", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "line_status", sa.String(length=20), nullable=False, server_default="DRAFT"
        ),
        sa.Column("approved_by", sa.Integer()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            f"line_status IN ({states})", name="ck_work_order_labor_lines_status"
        ),
        sa.CheckConstraint("hours > 0", name="ck_work_order_labor_lines_hours_positive"),
        sa.CheckConstraint(
            "hourly_rate >= 0", name="ck_work_order_labor_lines_rate_non_negative"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["company_id", "work_order_id"],
            ["work_orders.company_id", "work_orders.id"],
            name="fk_work_order_labor_lines_work_order",
        ),
        sa.ForeignKeyConstraint(["technician_user_id"], ["app_users.id"]),
        sa.ForeignKeyConstraint(["approved_by"], ["app_users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["app_users.id"]),
    )
    # Billing reads the APPROVED lines of one work order on every invoice
    # preview and issue, so the status is part of the lookup key.
    op.create_index(
        "ix_work_order_labor_lines_company_order_status",
        "work_order_labor_lines",
        ["company_id", "work_order_id", "line_status"],
    )
    op.create_index(
        "ix_work_order_labor_lines_company_technician",
        "work_order_labor_lines",
        ["company_id", "technician_user_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("work_order_labor_lines"):
        op.drop_table("work_order_labor_lines")
