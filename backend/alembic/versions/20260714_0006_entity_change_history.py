"""Add immutable entity before/after change history.

Revision ID: 20260714_0006
Revises: 20260714_0005
"""
from alembic import op
import sqlalchemy as sa
from app.change_history import metadata
revision="20260714_0006"; down_revision="20260714_0005"; branch_labels=None; depends_on=None

def upgrade():
    metadata.create_all(bind=op.get_bind(), checkfirst=True)

def downgrade():
    op.drop_table("entity_change_logs")
