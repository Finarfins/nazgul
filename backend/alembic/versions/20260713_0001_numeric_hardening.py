"""Harden legacy financial and quantity columns on PostgreSQL.

Revision ID: 20260713_0001
Revises: None
Create Date: 2026-07-13
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Sequence, Union

from alembic import op
from sqlalchemy import Numeric, inspect, text

from app.numeric_manifest import (
    MONEY_COLUMNS,
    MONEY_PRECISION,
    MONEY_SCALE,
    QUANTITY_COLUMNS,
    QUANTITY_PRECISION,
    QUANTITY_SCALE,
)

revision: str = "20260713_0001"
down_revision: Union[str, None] = "20260712_0000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def _is_target_numeric(column_type: object, precision: int, scale: int) -> bool:
    return (
        isinstance(column_type, Numeric)
        and getattr(column_type, "precision", None) == precision
        and getattr(column_type, "scale", None) == scale
    )


def _convert_columns(
    table_name: str,
    column_names: Iterable[str],
    precision: int,
    scale: int,
) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(table_name):
        return
    columns = {column["name"]: column for column in inspector.get_columns(table_name)}
    preparer = bind.dialect.identifier_preparer
    quoted_table = preparer.quote(table_name)
    for column_name in column_names:
        column = columns.get(column_name)
        if column is None or _is_target_numeric(column["type"], precision, scale):
            continue
        quoted_column = preparer.quote(column_name)
        # Preserve NULL values and explicitly round legacy floating-point data.
        bind.execute(
            text(
                f"ALTER TABLE {quoted_table} ALTER COLUMN {quoted_column} "
                f"TYPE NUMERIC({precision},{scale}) USING "
                f"CASE WHEN {quoted_column} IS NULL THEN NULL "
                f"ELSE ROUND({quoted_column}::numeric, {scale}) END"
            )
        )


def upgrade() -> None:
    bind = op.get_bind()
    # SQLite uses dynamic type affinity. Rebuilding dozens of legacy tables would
    # add risk without improving numeric storage guarantees, so the conversion is
    # intentionally performed only on the production PostgreSQL dialect.
    if bind.dialect.name != "postgresql":
        return
    for table_name, columns in MONEY_COLUMNS.items():
        _convert_columns(table_name, columns, MONEY_PRECISION, MONEY_SCALE)
    for table_name, columns in QUANTITY_COLUMNS.items():
        _convert_columns(table_name, columns, QUANTITY_PRECISION, QUANTITY_SCALE)


def downgrade() -> None:
    # Deliberately irreversible: converting exact NUMERIC values back to binary
    # floating point would reintroduce accounting drift and may lose precision.
    # Failing loudly prevents Alembic from falsely marking the revision as
    # downgraded while the database schema actually remains unchanged.
    raise RuntimeError("20260713_0001 migration geri alınamaz; yedekten geri yükleyin")
