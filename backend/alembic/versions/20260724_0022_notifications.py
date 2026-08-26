"""Tenant-scoped notification outbox and provider seam.

The payload is JSON encoded into TEXT deliberately: the application keeps one
wire/storage shape across SQLite and PostgreSQL 16. Delivery is claimed only
after the source transaction commits, and tenant-scoped dedupe plus a lease
protect retries from duplicate dispatch.

Revision ID: 20260724_0022
Revises: 20260724_0021
"""

from alembic import op
import sqlalchemy as sa


revision = "20260724_0022"
down_revision = "20260724_0021"
branch_labels = None
depends_on = None

STATUS_INDEX = "ix_notifications_company_status"
LEASE_INDEX = "ix_notifications_company_locked_until"
DEDUPE_CONSTRAINT = "uq_notifications_company_dedupe"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("notifications"):
        op.create_table(
            "notifications",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("type", sa.String(length=60), nullable=False),
            sa.Column("channel", sa.String(length=30), nullable=False),
            sa.Column("recipient", sa.String(length=255), nullable=False),
            sa.Column("template", sa.String(length=120), nullable=False),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column("dedupe_key", sa.String(length=255)),
            sa.Column(
                "status",
                sa.String(length=30),
                nullable=False,
                server_default="PENDING",
            ),
            sa.Column("external_id", sa.String(length=255)),
            sa.Column("last_error", sa.Text()),
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
            sa.Column("locked_until", sa.DateTime(timezone=True)),
            sa.Column("lock_token", sa.String(length=64)),
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
            sa.UniqueConstraint(
                "company_id",
                "dedupe_key",
                name=DEDUPE_CONSTRAINT,
            ),
        )
        inspector = sa.inspect(bind)

    indexes = {index["name"] for index in inspector.get_indexes("notifications")}
    if STATUS_INDEX not in indexes:
        op.create_index(
            STATUS_INDEX,
            "notifications",
            ["company_id", "status"],
        )
    if LEASE_INDEX not in indexes:
        op.create_index(
            LEASE_INDEX,
            "notifications",
            ["company_id", "locked_until"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("notifications"):
        return
    indexes = {index["name"] for index in inspector.get_indexes("notifications")}
    if LEASE_INDEX in indexes:
        op.drop_index(LEASE_INDEX, table_name="notifications")
    if STATUS_INDEX in indexes:
        op.drop_index(STATUS_INDEX, table_name="notifications")
    op.drop_table("notifications")
