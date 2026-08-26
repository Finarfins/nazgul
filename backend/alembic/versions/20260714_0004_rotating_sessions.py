"""Add rotating refresh sessions for HttpOnly cookie authentication.

Revision ID: 20260714_0004
Revises: 20260713_0003
Create Date: 2026-07-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260714_0004"
down_revision: Union[str, None] = "20260713_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("auth_refresh_tokens"):
        op.create_table(
            "auth_refresh_tokens",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("family_id", sa.String(length=64), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True)),
            sa.Column("revoked_at", sa.DateTime(timezone=True)),
            sa.Column("replaced_by_hash", sa.String(length=64)),
            sa.Column("created_ip", sa.String(length=80)),
            sa.Column("user_agent", sa.String(length=500)),
            sa.UniqueConstraint("token_hash", name="uq_auth_refresh_tokens_token_hash"),
        )
        op.create_index(
            "ix_auth_refresh_tokens_user_id",
            "auth_refresh_tokens",
            ["user_id"],
        )
        op.create_index(
            "ix_auth_refresh_tokens_family_id",
            "auth_refresh_tokens",
            ["family_id"],
        )
        op.create_index(
            "ix_auth_refresh_tokens_token_hash",
            "auth_refresh_tokens",
            ["token_hash"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("auth_refresh_tokens"):
        indexes = {index["name"] for index in inspector.get_indexes("auth_refresh_tokens")}
        for name in (
            "ix_auth_refresh_tokens_token_hash",
            "ix_auth_refresh_tokens_family_id",
            "ix_auth_refresh_tokens_user_id",
        ):
            if name in indexes:
                op.drop_index(name, table_name="auth_refresh_tokens")
        op.drop_table("auth_refresh_tokens")
