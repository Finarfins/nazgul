"""V3 Parça Supersession (part number supersession) — Increment 1.

When an OEM replaces a part number, old lookups must resolve to the current
part. One row per superseded part: old_product_id -> new_product_id, per
company. The UNIQUE(company_id, old_product_id) constraint guarantees a part
can only be superseded by one successor, which keeps the resolution chain a
simple linked list (A -> B -> C); cycle prevention is enforced at the API
layer at create time and guarded again at resolve time.

Revision ID: 20260724_0018
Revises: 20260724_0017

NOTE (single-head coordination): chained on develop's head 0017 (harman
vadeli). If develop's Alembic head advances before merge, re-point
``down_revision`` so the tree keeps a single head.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260724_0018"
down_revision = "20260724_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("part_supersessions"):
        op.create_table(
            "part_supersessions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("old_product_id", sa.Integer(), nullable=False),
            sa.Column("new_product_id", sa.Integer(), nullable=False),
            sa.Column("note", sa.Text()),
            sa.Column("created_by", sa.Integer()),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            # A part points at itself only through a data bug; reject in DDL too.
            sa.CheckConstraint(
                "old_product_id <> new_product_id",
                name="ck_part_supersessions_not_self",
            ),
            # One supersession per old part keeps the chain a linked list.
            sa.UniqueConstraint(
                "company_id", "old_product_id",
                name="uq_part_supersessions_company_old",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["old_product_id"], ["products.id"]),
            sa.ForeignKeyConstraint(["new_product_id"], ["products.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["app_users.id"]),
        )
        op.create_index(
            "ix_part_supersessions_company_old",
            "part_supersessions", ["company_id", "old_product_id"],
        )
        op.create_index(
            "ix_part_supersessions_company_new",
            "part_supersessions", ["company_id", "new_product_id"],
        )


def downgrade() -> None:
    op.drop_table("part_supersessions")
