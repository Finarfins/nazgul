"""V3.0 machine cards idempotency store

Records the ``Idempotency-Key`` used for a machine create so that a retried or
replayed POST returns the original machine instead of creating a duplicate. The
`(company_id, idempotency_key)` primary key is per-tenant and gives the
concurrency guarantee (a losing racer hits the PK and is redirected to the
winner). Portable across SQLite and PostgreSQL.

Revision ID: 20260716_0009
Revises: 20260716_0008
Create Date: 2026-07-16
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260716_0009"
down_revision: Union[str, None] = "20260716_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("machine_idempotency"):
        return
    op.create_table(
        "machine_idempotency",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("company_id", "idempotency_key"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("machine_idempotency"):
        op.drop_table("machine_idempotency")
