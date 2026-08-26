"""Servis İş Emri v2 — FAZ-3: parça rezervasyonu + servis aracı deposu.

Expand-only. Adds:

* ``warehouse_stocks.reserved_quantity`` — the reserved half of a warehouse's
  stock. Availability becomes ``quantity - reserved_quantity``; the physical
  ``quantity`` keeps its existing meaning untouched.
* ``work_order_parts.line_status`` (RESERVED | ISSUED | RETURNED) and
  ``work_order_parts.reserved_quantity``.
* ``companies.service_parts_mode`` (IMMEDIATE | RESERVE) — the reservation
  workflow is a per-company setting and defaults to IMMEDIATE, i.e. exactly the
  v1 behaviour.
* ``warehouses.warehouse_type`` (FIXED | SERVICE_VEHICLE) + ``technician_user_id``
  — a service vehicle is an ordinary warehouse with a label, so the existing
  transfer engine moves parts onto it unchanged. No separate vehicle stock table.
* ``work_order_stock_events`` — the idempotency ledger that makes a retried
  issue/return a no-op instead of a second stock movement.

BACKFILL — the load-bearing decision: every existing ``work_order_parts`` row
becomes ``line_status='ISSUED'`` with ``reserved_quantity=0``. Under v1 adding a
part decremented warehouse stock immediately, so those quantities are ALREADY
out of stock. Backfilling ``reserved_quantity=quantity`` would reserve stock
that is no longer on hand and make every legacy line double-count against
availability.

``warehouse_stocks.reserved_quantity`` starts at 0 for the same reason: no live
reservation exists before this migration.

Revision ID: 20260728_0036
Revises: 20260728_0035
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_0036"
# Stacked on the notifications F2 migration (#181), which landed on develop
# after this branch was numbered; chaining onto it keeps the history one linear
# line instead of two heads off 0034.
down_revision = "20260728_0035"
branch_labels = None
depends_on = None

PART_LINE_STATES = ("RESERVED", "ISSUED", "RETURNED")
SERVICE_PARTS_MODES = ("IMMEDIATE", "RESERVE")
WAREHOUSE_TYPES = ("FIXED", "SERVICE_VEHICLE")


def _quoted(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _columns(inspector, table: str) -> set[str]:
    """Column names, or an empty set when the table is absent.

    Every block below is guarded this way because a migration must survive a
    database that does not (yet) have the table — partially built schemas exist
    in the migration tests, and an unguarded ``get_columns`` raises
    ``NoSuchTableError`` there instead of skipping.
    """
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    stock_columns = _columns(inspector, "warehouse_stocks")
    if stock_columns and "reserved_quantity" not in stock_columns:
        op.add_column(
            "warehouse_stocks",
            sa.Column(
                "reserved_quantity",
                sa.Numeric(18, 4),
                nullable=False,
                server_default="0",
            ),
        )

    part_columns = _columns(inspector, "work_order_parts")
    if part_columns and "line_status" not in part_columns:
        states = _quoted(PART_LINE_STATES)
        with op.batch_alter_table("work_order_parts") as batch:
            batch.add_column(
                sa.Column(
                    "line_status",
                    sa.String(length=20),
                    nullable=False,
                    server_default="ISSUED",
                )
            )
            batch.add_column(
                sa.Column(
                    "reserved_quantity",
                    sa.Numeric(18, 4),
                    nullable=False,
                    server_default="0",
                )
            )
            batch.create_check_constraint(
                "ck_work_order_parts_line_status", f"line_status IN ({states})"
            )
            batch.create_check_constraint(
                "ck_work_order_parts_reserved_nonnegative", "reserved_quantity >= 0"
            )
        # Explicit, even though the server_default already produced it: legacy
        # lines are ISSUED with nothing reserved, because v1 already removed
        # their quantity from stock.
        op.execute(
            "UPDATE work_order_parts SET line_status='ISSUED', reserved_quantity=0"
        )
        op.create_index(
            "ix_work_order_parts_company_status",
            "work_order_parts",
            ["company_id", "work_order_id", "line_status"],
        )

    company_columns = _columns(inspector, "companies")
    if company_columns and "service_parts_mode" not in company_columns:
        modes = _quoted(SERVICE_PARTS_MODES)
        with op.batch_alter_table("companies") as batch:
            batch.add_column(
                sa.Column(
                    "service_parts_mode",
                    sa.String(length=20),
                    nullable=False,
                    server_default="IMMEDIATE",
                )
            )
            batch.create_check_constraint(
                "ck_companies_service_parts_mode", f"service_parts_mode IN ({modes})"
            )

    warehouse_columns = _columns(inspector, "warehouses")
    if warehouse_columns and "warehouse_type" not in warehouse_columns:
        types = _quoted(WAREHOUSE_TYPES)
        with op.batch_alter_table("warehouses") as batch:
            batch.add_column(
                sa.Column(
                    "warehouse_type",
                    sa.String(length=20),
                    nullable=False,
                    server_default="FIXED",
                )
            )
            batch.add_column(sa.Column("technician_user_id", sa.Integer()))
            batch.create_check_constraint(
                "ck_warehouses_type", f"warehouse_type IN ({types})"
            )

    if not inspector.has_table("work_order_stock_events"):
        op.create_table(
            "work_order_stock_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("work_order_id", sa.Integer(), nullable=False),
            sa.Column("part_id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=20), nullable=False),
            sa.Column("idempotency_key", sa.String(length=200), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            # The whole point of the table: a retried issue/return collides here
            # instead of moving stock a second time.
            sa.UniqueConstraint(
                "company_id", "idempotency_key", name="uq_work_order_stock_events_key"
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        )
        op.create_index(
            "ix_work_order_stock_events_company_order",
            "work_order_stock_events",
            ["company_id", "work_order_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("work_order_stock_events"):
        op.drop_table("work_order_stock_events")
    warehouse_columns = _columns(inspector, "warehouses")
    if "warehouse_type" in warehouse_columns:
        with op.batch_alter_table("warehouses") as batch:
            batch.drop_constraint("ck_warehouses_type", type_="check")
            batch.drop_column("technician_user_id")
            batch.drop_column("warehouse_type")
    company_columns = _columns(inspector, "companies")
    if "service_parts_mode" in company_columns:
        with op.batch_alter_table("companies") as batch:
            batch.drop_constraint("ck_companies_service_parts_mode", type_="check")
            batch.drop_column("service_parts_mode")
    part_columns = _columns(inspector, "work_order_parts")
    if "line_status" in part_columns:
        op.drop_index(
            "ix_work_order_parts_company_status", table_name="work_order_parts"
        )
        with op.batch_alter_table("work_order_parts") as batch:
            batch.drop_constraint("ck_work_order_parts_reserved_nonnegative", type_="check")
            batch.drop_constraint("ck_work_order_parts_line_status", type_="check")
            batch.drop_column("reserved_quantity")
            batch.drop_column("line_status")
    stock_columns = _columns(inspector, "warehouse_stocks")
    if "reserved_quantity" in stock_columns:
        op.drop_column("warehouse_stocks", "reserved_quantity")
