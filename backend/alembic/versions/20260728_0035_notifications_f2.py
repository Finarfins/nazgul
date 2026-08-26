"""Bildirim F2: normalize vade tarihi ve hatırlatma kuralları.

Revision ID: 20260728_0035
Revises: 20260728_0034
"""

from alembic import op
import sqlalchemy as sa

revision = "20260728_0035"
down_revision = "20260728_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    order_columns = {column["name"] for column in inspector.get_columns("orders")}
    if "due_date_normalized" not in order_columns:
        with op.batch_alter_table("orders") as batch:
            batch.add_column(sa.Column("due_date_normalized", sa.Date(), nullable=True))

    # Only strict ISO dates are accepted. Invalid legacy strings remain NULL and
    # therefore cannot accidentally trigger a reminder.
    if bind.dialect.name == "postgresql":
        op.execute(
            """UPDATE orders
                  SET due_date_normalized = CAST(due_date AS DATE)
                WHERE due_date ~ '^\\d{4}-\\d{2}-\\d{2}$'
                  AND pg_input_is_valid(due_date, 'date')"""
        )
    else:
        op.execute(
            """UPDATE orders
                  SET due_date_normalized = due_date
                WHERE due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                  AND date(due_date) = due_date"""
        )
    order_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("orders")}
    if "ix_orders_company_term_due_normalized" not in order_indexes:
        op.create_index(
            "ix_orders_company_term_due_normalized",
            "orders",
            ["company_id", "payment_term", "due_date_normalized"],
        )

    if not sa.inspect(bind).has_table("notification_rules"):
        op.create_table(
            "notification_rules",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(60), nullable=False),
            sa.Column("trigger_type", sa.String(40), nullable=False),
            sa.Column("channel", sa.String(30), nullable=False),
            sa.Column("template_id", sa.Integer(), nullable=False),
            sa.Column("offset_days", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("send_window_start", sa.Time(), nullable=True),
            sa.Column("send_window_end", sa.Time(), nullable=True),
            sa.Column("max_per_run", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("scope_filter", sa.Text(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("approved_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint(
                "trigger_type IN ('SERVICE_DUE','HARVEST_DUE_SOON','HARVEST_OVERDUE')",
                name="ck_notification_rules_trigger",
            ),
            sa.CheckConstraint("channel IN ('SMS','WHATSAPP','EMAIL')", name="ck_notification_rules_channel"),
            sa.CheckConstraint("max_per_run BETWEEN 1 AND 50", name="ck_notification_rules_limit"),
            sa.UniqueConstraint("company_id", "code", name="uq_notification_rules_company_code"),
        )
    rule_indexes = {
        index["name"]
        for index in sa.inspect(bind).get_indexes("notification_rules")
    }
    if "ix_notification_rules_company_enabled" not in rule_indexes:
        op.create_index(
            "ix_notification_rules_company_enabled",
            "notification_rules",
            ["company_id", "is_enabled"],
        )


def downgrade() -> None:
    op.drop_index("ix_notification_rules_company_enabled", table_name="notification_rules")
    op.drop_table("notification_rules")
    op.drop_index("ix_orders_company_term_due_normalized", table_name="orders")
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("due_date_normalized")
