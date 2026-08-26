"""V3.0 service work orders.

Revision ID: 20260717_0009
Revises: 20260716_0008
"""

from alembic import op
import sqlalchemy as sa


revision = "20260717_0009"
down_revision = "20260716_0008"
branch_labels = None
depends_on = None

WORK_ORDER_STATES = (
    "OPEN",
    "IN_PROGRESS",
    "WAITING_PARTS",
    "WAITING_CUSTOMER",
    "COMPLETED",
    "DELIVERED",
    "CANCELLED",
)
WORK_ORDER_PRIORITIES = ("LOW", "NORMAL", "HIGH", "URGENT")


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("work_orders"):
        return
    states = ",".join(f"'{state}'" for state in WORK_ORDER_STATES)
    priorities = ",".join(f"'{priority}'" for priority in WORK_ORDER_PRIORITIES)
    op.create_table(
        "work_orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("technician_id", sa.Integer(), nullable=False),
        sa.Column("work_order_no", sa.String(length=80), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="OPEN"),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="NORMAL"),
        sa.Column("complaint", sa.Text()),
        sa.Column("diagnosis", sa.Text()),
        sa.Column("repair_summary", sa.Text()),
        sa.Column("technician_notes", sa.Text()),
        sa.Column("estimated_hours", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("actual_hours", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("labor_rate", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_labor_cost", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("warranty", sa.Boolean(), nullable=False, server_default=sa.false()),
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
        sa.CheckConstraint(f"status IN ({states})", name="ck_work_orders_status"),
        sa.CheckConstraint(
            f"priority IN ({priorities})",
            name="ck_work_orders_priority",
        ),
        sa.UniqueConstraint("company_id", "work_order_no", name="uq_work_orders_company_no"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["technician_id"], ["app_users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["app_users.id"]),
    )
    op.create_index(
        "ix_work_orders_company_status_opened",
        "work_orders",
        ["company_id", "status", "opened_at"],
    )
    op.create_index(
        "ix_work_orders_company_customer",
        "work_orders",
        ["company_id", "customer_id"],
    )
    op.create_index(
        "ix_work_orders_company_machine",
        "work_orders",
        ["company_id", "machine_id"],
    )
    op.create_index(
        "ix_work_orders_company_technician",
        "work_orders",
        ["company_id", "technician_id"],
    )
    op.create_index(
        "ix_work_orders_company_creator",
        "work_orders",
        ["company_id", "created_by"],
    )


def downgrade() -> None:
    raise RuntimeError("Service work orders downgrade is intentionally disabled")
