"""Şubeler Arası Parça Transferi — Increment 1 database guards.

The cross-branch transfer feature itself already exists (``stock_transfers`` +
``stock_transfer_items``, written by ``POST /warehouses/transfers`` through
``inventory.adjust_warehouse_stock``). This migration adds NO new table; it
hardens and indexes the existing one:

* CHECK ``source_warehouse_id <> target_warehouse_id`` — a self-transfer is
  already rejected by the API (400), but the invariant belongs in the schema
  too so a bulk import or a future code path cannot write a nonsense document.
* Index ``(company_id, transfer_date)`` on ``stock_transfers`` and
  ``(product_id, transfer_id)`` on ``stock_transfer_items`` — the transfer
  history list and per-product transfer lookups are company-scoped and would
  otherwise scan the ledger.

Dialect note: PostgreSQL gets the CHECK with a plain ``ALTER TABLE ... ADD
CONSTRAINT``. SQLite cannot add a CHECK to an existing table without a full
table rebuild, and rebuilding a live transfer ledger to duplicate a guard the
API already enforces is not a trade worth making; SQLite keeps the API-layer
guard only. ``test_stock_transfer_postgresql.py`` asserts the constraint is
really live on PostgreSQL 16.

Revision ID: 20260724_0020
Revises: 20260724_0018

NOTE (single-head coordination): chained on develop's head 0018 (part
supersessions). If develop's Alembic head advances before merge, re-point
``down_revision`` so the tree keeps a single head.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260724_0020"
down_revision = "20260724_0018"
branch_labels = None
depends_on = None

CHECK_NAME = "ck_stock_transfers_source_not_target"
TRANSFER_INDEX = "ix_stock_transfers_company_date"
ITEM_INDEX = "ix_stock_transfer_items_product"


def _index_names(inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("stock_transfers"):
        # Fresh databases build these tables from inventory.metadata at startup;
        # there is nothing to harden yet on this pass.
        return

    if bind.dialect.name == "postgresql":
        existing = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("stock_transfers")
        }
        if CHECK_NAME not in existing:
            op.execute(
                sa.text(
                    f"ALTER TABLE stock_transfers ADD CONSTRAINT {CHECK_NAME} "
                    "CHECK (source_warehouse_id <> target_warehouse_id)"
                )
            )

    if TRANSFER_INDEX not in _index_names(inspector, "stock_transfers"):
        op.create_index(
            TRANSFER_INDEX, "stock_transfers", ["company_id", "transfer_date"]
        )

    if inspector.has_table("stock_transfer_items") and ITEM_INDEX not in _index_names(
        inspector, "stock_transfer_items"
    ):
        op.create_index(
            ITEM_INDEX, "stock_transfer_items", ["product_id", "transfer_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("stock_transfers"):
        return

    if inspector.has_table("stock_transfer_items") and ITEM_INDEX in _index_names(
        inspector, "stock_transfer_items"
    ):
        op.drop_index(ITEM_INDEX, table_name="stock_transfer_items")
    if TRANSFER_INDEX in _index_names(inspector, "stock_transfers"):
        op.drop_index(TRANSFER_INDEX, table_name="stock_transfers")
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(f"ALTER TABLE stock_transfers DROP CONSTRAINT IF EXISTS {CHECK_NAME}")
        )
