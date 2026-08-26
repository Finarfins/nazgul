"""Bind machine idempotency keys to a request fingerprint

Adds ``machine_idempotency.request_fingerprint`` so that replaying an
``Idempotency-Key`` with a *different* request body returns 409 instead of
silently replaying the original machine. The column is nullable: rows written
before this migration carry no fingerprint and are treated as a match
(backward compatible). Portable across SQLite and PostgreSQL.

Revision ID: 20260722_0014
Revises: 20260719_0013
Create Date: 2026-07-22
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260722_0014"
down_revision: Union[str, None] = "20260719_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    return any(col["name"] == column for col in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("machine_idempotency"):
        return
    if not _has_column(bind, "machine_idempotency", "request_fingerprint"):
        op.add_column(
            "machine_idempotency",
            sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("machine_idempotency") and _has_column(
        bind, "machine_idempotency", "request_fingerprint"
    ):
        with op.batch_alter_table("machine_idempotency") as batch:
            batch.drop_column("request_fingerprint")
