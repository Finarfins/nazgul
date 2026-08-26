"""Add tenant-scoped normalized identifier indexes for smart part search.

Revision ID: 20260725_0023
Revises: 20260724_0022
"""

from alembic import op
import sqlalchemy as sa


revision = "20260725_0023"
down_revision = "20260724_0022"
branch_labels = None
depends_on = None

INDEXES = {
    "ix_products_company_oem_normalized": "oem_number",
    "ix_products_company_product_code_normalized": "product_code",
    "ix_products_company_barcode_normalized": "barcode",
    "ix_products_company_alternative_oem_normalized": "alternative_oem",
}


def _normalized_expression(column: str) -> str:
    return (
        f"regexp_replace(UPPER(COALESCE({column},'')),"
        "'[^A-Z0-9]+','','g')"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = sa.inspect(bind)
    if not inspector.has_table("products"):
        return
    columns = {column["name"] for column in inspector.get_columns("products")}
    for index_name, column in INDEXES.items():
        if column not in columns or "company_id" not in columns:
            continue
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} "
            f"ON products (company_id, ({_normalized_expression(column)}))"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for index_name in reversed(INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
