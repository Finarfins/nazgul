"""product industry fields

Revision ID: 20260715_0007
Revises: 20260714_0006
"""
from alembic import op
import sqlalchemy as sa

revision = "20260715_0007"
down_revision = "20260714_0006"
branch_labels = None
depends_on = None

FIELDS = [
    ("oem_number", sa.String(160)),
    ("alternative_oem", sa.Text()),
    ("brand", sa.String(160)),
    ("manufacturer", sa.String(160)),
    ("compatible_models", sa.Text()),
    ("technical_notes", sa.Text()),
]

def upgrade():
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("products")}
    with op.batch_alter_table("products") as batch:
        for name, typ in FIELDS:
            if name not in existing:
                batch.add_column(sa.Column(name, typ, nullable=True))
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("products")}
    if "ix_products_company_oem" not in indexes:
        op.create_index("ix_products_company_oem", "products", ["company_id", "oem_number"])

def downgrade():
    raise RuntimeError("Product industry fields downgrade is intentionally disabled")
