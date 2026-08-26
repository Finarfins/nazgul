"""Create the complete ERP schema for clean installations.

Revision ID: 20260712_0000
Revises: None
Create Date: 2026-07-14
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app import auth, change_history, core_schema, finance_engine, inventory, tenancy, workflow

revision: str = "20260712_0000"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_crm_tables(bind) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "entity_notes", metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("created_by", sa.String(length=160)),
    )
    sa.Index("idx_entity_notes_lookup", metadata.tables["entity_notes"].c.company_id,
             metadata.tables["entity_notes"].c.entity_type,
             metadata.tables["entity_notes"].c.entity_id,
             metadata.tables["entity_notes"].c.created_at)
    sa.Table(
        "entity_contacts", metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=120)),
        sa.Column("phone", sa.String(length=80)),
        sa.Column("email", sa.String(length=240)),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    sa.Index("idx_entity_contacts_lookup", metadata.tables["entity_contacts"].c.company_id,
             metadata.tables["entity_contacts"].c.entity_type,
             metadata.tables["entity_contacts"].c.entity_id,
             metadata.tables["entity_contacts"].c.is_primary)
    sa.Table(
        "entity_tasks", metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("due_date", sa.String(length=40)),
        sa.Column("priority", sa.String(length=30), nullable=False, server_default="normal"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="open"),
        sa.Column("assigned_to", sa.String(length=160)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("completed_at", sa.String(length=40)),
    )
    sa.Index("idx_entity_tasks_lookup", metadata.tables["entity_tasks"].c.company_id,
             metadata.tables["entity_tasks"].c.entity_type,
             metadata.tables["entity_tasks"].c.entity_id,
             metadata.tables["entity_tasks"].c.status,
             metadata.tables["entity_tasks"].c.due_date)
    metadata.create_all(bind=bind, checkfirst=True)


def upgrade() -> None:
    bind = op.get_bind()
    # Metadata groups remain split by bounded context, but Alembic is now the
    # only component allowed to execute their DDL.
    for metadata in (
        auth.metadata,
        change_history.metadata,
        tenancy.metadata,
        core_schema.metadata,
        inventory.metadata,
        finance_engine.metadata,
        workflow.metadata,
    ):
        metadata.create_all(bind=bind, checkfirst=True)
    _create_crm_tables(bind)

    legacy = sa.MetaData()
    schema_migrations = sa.Table(
        "schema_migrations", legacy,
        sa.Column("version", sa.String(length=80), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.String(length=40), nullable=False),
    )
    legacy.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Baseline migration geri alınamaz; yedekten geri yükleyin")
