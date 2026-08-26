"""Add concurrency-safe per-company document counters.

Revision ID: 20260713_0002
Revises: 20260713_0001
Create Date: 2026-07-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260713_0002"
down_revision: Union[str, None] = "20260713_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("document_sequences"):
        return
    op.create_table(
        "document_sequences",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("sequence_key", sa.String(length=160), nullable=False),
        sa.Column("current_value", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("company_id", "sequence_key"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("document_sequences"):
        op.drop_table("document_sequences")
