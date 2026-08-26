"""V3.0 machine cards foundation

Creates the ``machines`` table (see :mod:`app.machines`) with tenant-scoped
lookup indexes and per-company partial-unique indexes on chassis/serial numbers.
Portable across SQLite and PostgreSQL.

Revision ID: 20260716_0008
Revises: 20260715_0007
Create Date: 2026-07-16
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.machines import metadata as machines_metadata

revision: str = "20260716_0008"
down_revision: Union[str, None] = "20260715_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("machines"):
        return
    # Single source of truth: the same Table/Index definitions the router relies
    # on. create_all emits the table plus its (partial-unique) indexes for the
    # active dialect.
    machines_metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("machines"):
        op.drop_table("machines")
