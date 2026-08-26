"""Add end-to-end traceability fields to security audit records.

Revision ID: 20260714_0005
Revises: 20260714_0004
Create Date: 2026-07-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260714_0005"
down_revision: Union[str, None] = "20260714_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = {
    "request_id": sa.Column("request_id", sa.String(length=64), nullable=True),
    "outcome": sa.Column("outcome", sa.String(length=20), nullable=False, server_default="success"),
    "duration_ms": sa.Column("duration_ms", sa.Integer(), nullable=True),
    "auth_source": sa.Column("auth_source", sa.String(length=20), nullable=True),
    "user_agent": sa.Column("user_agent", sa.String(length=500), nullable=True),
    "failure_reason": sa.Column("failure_reason", sa.String(length=300), nullable=True),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("security_audit_logs")}
    existing_indexes = {index["name"] for index in inspector.get_indexes("security_audit_logs")}

    missing = [column for name, column in COLUMNS.items() if name not in existing_columns]
    if missing:
        with op.batch_alter_table("security_audit_logs") as batch:
            for column in missing:
                batch.add_column(column)

    # The clean-install baseline imports current metadata and may already create
    # this index. Upgrades from 0004 do not have it.
    if "ix_security_audit_logs_request_id" not in existing_indexes:
        op.create_index(
            "ix_security_audit_logs_request_id",
            "security_audit_logs",
            ["request_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("security_audit_logs")}
    existing_indexes = {index["name"] for index in inspector.get_indexes("security_audit_logs")}
    with op.batch_alter_table("security_audit_logs") as batch:
        if "ix_security_audit_logs_request_id" in existing_indexes:
            batch.drop_index("ix_security_audit_logs_request_id")
        for name in reversed(tuple(COLUMNS)):
            if name in existing_columns:
                batch.drop_column(name)
