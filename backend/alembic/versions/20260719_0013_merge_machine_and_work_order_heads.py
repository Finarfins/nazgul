"""merge_machine_and_work_order_heads

Revision ID: 20260719_0013
Revises: 20260716_0009, 20260718_0012
Create Date: 2026-07-18 21:40:27.821136
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260719_0013'
down_revision: Union[str, None] = ('20260716_0009', '20260718_0012')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op head join: unifies the two parallel 0009 branches (machine idempotency
    # and work orders) into a single head. No schema mutation is required.
    pass


def downgrade() -> None:
    # Reverting the merge simply restores the two independent heads.
    pass
