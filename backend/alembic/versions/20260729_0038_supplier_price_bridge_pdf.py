"""Supplier price bridge PDF profiles.

Revision ID: 20260729_0038
Revises: 20260728_0037
"""

from alembic import op
import sqlalchemy as sa

revision = "20260729_0038"
down_revision = "20260728_0037"
branch_labels = None
depends_on = None


def _column_names(inspector: sa.Inspector, table: str) -> set[str]:
    if not inspector.has_table(table):
        raise RuntimeError(
            "supplier_import_profiles tablosu yok; 20260728_0037 zinciri bozuk"
        )
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = _column_names(inspector, "supplier_import_profiles")
    if not columns:
        return
    if "source_type" not in columns:
        op.add_column(
            "supplier_import_profiles",
            sa.Column(
                "source_type",
                sa.String(8),
                nullable=False,
                server_default="xlsx",
            ),
        )
    if "page_sections" not in columns:
        op.add_column(
            "supplier_import_profiles",
            sa.Column("page_sections", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = _column_names(inspector, "supplier_import_profiles")
    if "page_sections" in columns:
        op.drop_column("supplier_import_profiles", "page_sections")
    if "source_type" in columns:
        op.drop_column("supplier_import_profiles", "source_type")
