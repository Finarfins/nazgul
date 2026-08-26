"""Servis İş Emri v2 — FAZ-1: makine/planlama temeli.

Expand-only. Adds:

* ``machine_hour_readings`` — append-only hour-meter readings; the machine's
  ``working_hours`` column becomes a projection of the latest reading.
* ``machine_ownership_history`` — append-only ownership transfers, backfilled
  with one opening row per machine that already has an owner.
* ``technician_profiles`` — optional service metadata for an ``app_users`` row;
  identity stays in ``app_users`` (no second identity).
* ``work_orders.scheduled_date`` + the new ``SCHEDULED`` state in the status
  CHECK constraint.
* ``work_order_attachments`` — photo/signature metadata; files live under
  ``SUNGUR_DATA_DIR`` outside the release directory.

Composite tenant FKs ``(company_id, <row>)`` reference ``(company_id, id)`` on
machines/customers/work_orders, so those parents get a supporting unique index
(their ``id`` PK already guarantees uniqueness; the composite index only exists
for the FK target and cross-tenant integrity at the database level).

Revision ID: 20260727_0029
Revises: 20260727_0028
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0029"
# Stacked on the Harman V2b late-fee charge migration (#166) so the history
# stays a single linear chain; both were authored as 0028 in parallel branches.
down_revision = "20260727_0028"
branch_labels = None
depends_on = None

WORK_ORDER_STATES = (
    "OPEN",
    "SCHEDULED",
    "IN_PROGRESS",
    "WAITING_PARTS",
    "WAITING_CUSTOMER",
    "COMPLETED",
    "DELIVERED",
    "CANCELLED",
)
LEGACY_WORK_ORDER_STATES = tuple(s for s in WORK_ORDER_STATES if s != "SCHEDULED")
READING_SOURCES = ("manual", "work_order")
READING_TYPES = ("normal", "anomaly", "meter_replacement", "correction")
ATTACHMENT_KINDS = ("photo", "signature", "other")


def _states_sql(states: tuple[str, ...]) -> str:
    return ",".join(f"'{state}'" for state in states)


def _ensure_tenant_row_index(inspector, table: str, index_name: str) -> None:
    """Unique ``(company_id, id)`` index so composite tenant FKs can target it."""
    existing = {index["name"] for index in inspector.get_indexes(table)}
    if index_name not in existing:
        op.create_index(index_name, table, ["company_id", "id"], unique=True)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _ensure_tenant_row_index(inspector, "machines", "uq_machines_company_row")
    _ensure_tenant_row_index(inspector, "customers", "uq_customers_company_row")
    _ensure_tenant_row_index(inspector, "work_orders", "uq_work_orders_company_row")

    _add_scheduled_state(bind, inspector)
    _create_machine_hour_readings(bind, inspector)
    _create_machine_ownership_history(bind, inspector)
    _create_technician_profiles(bind, inspector)
    _create_work_order_attachments(bind, inspector)


def _add_scheduled_state(bind, inspector) -> None:
    columns = {column["name"] for column in inspector.get_columns("work_orders")}
    if "scheduled_date" in columns:
        return
    states = _states_sql(WORK_ORDER_STATES)
    if bind.dialect.name == "postgresql":
        op.add_column(
            "work_orders", sa.Column("scheduled_date", sa.DateTime(timezone=True))
        )
        op.drop_constraint("ck_work_orders_status", "work_orders", type_="check")
        op.create_check_constraint(
            "ck_work_orders_status", "work_orders", f"status IN ({states})"
        )
    else:
        # SQLite cannot alter a CHECK in place; batch mode rebuilds the table
        # with the widened state list while copying every row unchanged.
        with op.batch_alter_table("work_orders") as batch:
            batch.add_column(sa.Column("scheduled_date", sa.DateTime(timezone=True)))
            batch.drop_constraint("ck_work_orders_status", type_="check")
            batch.create_check_constraint("ck_work_orders_status", f"status IN ({states})")


def _create_machine_hour_readings(bind, inspector) -> None:
    if inspector.has_table("machine_hour_readings"):
        return
    sources = _states_sql(READING_SOURCES)
    types = _states_sql(READING_TYPES)
    op.create_table(
        "machine_hour_readings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("reading_hours", sa.Numeric(12, 2), nullable=False),
        sa.Column("reading_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column("work_order_id", sa.Integer()),
        sa.Column(
            "reading_type", sa.String(length=30), nullable=False, server_default="normal"
        ),
        sa.Column("reason", sa.Text()),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            f"source IN ({sources})", name="ck_machine_hour_readings_source"
        ),
        sa.CheckConstraint(
            f"reading_type IN ({types})", name="ck_machine_hour_readings_type"
        ),
        sa.CheckConstraint(
            "reading_hours >= 0", name="ck_machine_hour_readings_non_negative"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["company_id", "machine_id"],
            ["machines.company_id", "machines.id"],
            name="fk_machine_hour_readings_machine",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "work_order_id"],
            ["work_orders.company_id", "work_orders.id"],
            name="fk_machine_hour_readings_work_order",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["app_users.id"]),
    )
    op.create_index(
        "ix_machine_hour_readings_company_machine",
        "machine_hour_readings",
        ["company_id", "machine_id", "id"],
    )
    op.create_index(
        "ix_machine_hour_readings_company_work_order",
        "machine_hour_readings",
        ["company_id", "work_order_id"],
    )


def _create_machine_ownership_history(bind, inspector) -> None:
    if inspector.has_table("machine_ownership_history"):
        return
    op.create_table(
        "machine_ownership_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("from_customer_id", sa.Integer()),
        sa.Column("to_customer_id", sa.Integer(), nullable=False),
        sa.Column("transfer_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text()),
        # Nullable only for migration backfill rows; runtime writes always set it.
        sa.Column("created_by", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["company_id", "machine_id"],
            ["machines.company_id", "machines.id"],
            name="fk_machine_ownership_history_machine",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "from_customer_id"],
            ["customers.company_id", "customers.id"],
            name="fk_machine_ownership_history_from_customer",
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "to_customer_id"],
            ["customers.company_id", "customers.id"],
            name="fk_machine_ownership_history_to_customer",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["app_users.id"]),
    )
    op.create_index(
        "ix_machine_ownership_history_company_machine",
        "machine_ownership_history",
        ["company_id", "machine_id", "id"],
    )
    # Opening row per already-owned machine so the chronology starts complete.
    # machines.created_at is a String(40) ISO timestamp; PostgreSQL needs the
    # explicit cast into the timestamptz column, SQLite stores the text as-is.
    transfer_date_sql = (
        "CAST(m.created_at AS timestamp with time zone)"
        if bind.dialect.name == "postgresql"
        else "m.created_at"
    )
    op.execute(
        "INSERT INTO machine_ownership_history("
        "company_id,machine_id,from_customer_id,to_customer_id,transfer_date,"
        "reason,created_by,created_at) "
        "SELECT m.company_id,m.id,NULL,m.customer_id,"
        f"{transfer_date_sql},"
        "'Açılış kaydı (mevcut sahiplik göçü)',NULL,CURRENT_TIMESTAMP "
        "FROM machines m WHERE m.customer_id IS NOT NULL"
    )


def _create_technician_profiles(bind, inspector) -> None:
    if inspector.has_table("technician_profiles"):
        return
    op.create_table(
        "technician_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(length=40)),
        sa.Column("specialty", sa.String(length=160)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "company_id", "user_id", name="uq_technician_profiles_company_user"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
    )
    op.create_index(
        "ix_technician_profiles_company",
        "technician_profiles",
        ["company_id", "active"],
    )


def _create_work_order_attachments(bind, inspector) -> None:
    if inspector.has_table("work_order_attachments"):
        return
    kinds = _states_sql(ATTACHMENT_KINDS)
    op.create_table(
        "work_order_attachments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("work_order_id", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="photo"),
        sa.Column("uploaded_by", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(f"kind IN ({kinds})", name="ck_work_order_attachments_kind"),
        sa.CheckConstraint(
            "file_size > 0", name="ck_work_order_attachments_size_positive"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["company_id", "work_order_id"],
            ["work_orders.company_id", "work_orders.id"],
            name="fk_work_order_attachments_work_order",
        ),
        sa.ForeignKeyConstraint(["uploaded_by"], ["app_users.id"]),
    )
    op.create_index(
        "ix_work_order_attachments_company_work_order",
        "work_order_attachments",
        ["company_id", "work_order_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in (
        "work_order_attachments",
        "technician_profiles",
        "machine_ownership_history",
        "machine_hour_readings",
    ):
        if inspector.has_table(table):
            op.drop_table(table)
    columns = {column["name"] for column in inspector.get_columns("work_orders")}
    if "scheduled_date" in columns:
        states = _states_sql(LEGACY_WORK_ORDER_STATES)
        # Any SCHEDULED rows must leave the state before the CHECK is narrowed.
        op.execute("UPDATE work_orders SET status='OPEN' WHERE status='SCHEDULED'")
        if bind.dialect.name == "postgresql":
            op.drop_constraint("ck_work_orders_status", "work_orders", type_="check")
            op.create_check_constraint(
                "ck_work_orders_status", "work_orders", f"status IN ({states})"
            )
            op.drop_column("work_orders", "scheduled_date")
        else:
            with op.batch_alter_table("work_orders") as batch:
                batch.drop_constraint("ck_work_orders_status", type_="check")
                batch.create_check_constraint(
                    "ck_work_orders_status", f"status IN ({states})"
                )
                batch.drop_column("scheduled_date")
    for table, index in (
        ("work_orders", "uq_work_orders_company_row"),
        ("customers", "uq_customers_company_row"),
        ("machines", "uq_machines_company_row"),
    ):
        existing = {idx["name"] for idx in inspector.get_indexes(table)}
        if index in existing:
            op.drop_index(index, table_name=table)
