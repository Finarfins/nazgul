"""Harman V1: tenant-scoped harvest calendar and automatic due dates.

Revision ID: 20260726_0026
Revises: 20260726_0025
"""

from datetime import date

from alembic import op
import sqlalchemy as sa

from app.harvest_scheduling import resolve_harvest_due_date


revision = "20260726_0026"
down_revision = "20260726_0025"
branch_labels = None
depends_on = None


def _backfill_harman_due_dates(bind) -> None:
    legacy_rows = bind.execute(
        sa.text(
            """SELECT id,company_id,customer_id,order_date
            FROM orders
            WHERE payment_term='HARMAN_VADELI' AND due_date IS NULL
            ORDER BY company_id,id"""
        )
    ).mappings().all()
    if not legacy_rows:
        print("Harman due-date backfill: 0 legacy NULL record")
        return

    resolutions: list[tuple[dict, object]] = []
    failures: list[str] = []
    for row_mapping in legacy_rows:
        row = dict(row_mapping)
        try:
            transaction_date = date.fromisoformat(str(row["order_date"])[:10])
            product_ids = [
                int(product_id)
                for product_id in bind.execute(
                    sa.text(
                        """SELECT product_id FROM order_items
                        WHERE order_id=:order_id AND product_id IS NOT NULL
                        ORDER BY id"""
                    ),
                    {"order_id": row["id"]},
                ).scalars()
            ]
            resolution = resolve_harvest_due_date(
                bind,
                company_id=int(row["company_id"]),
                customer_id=int(row["customer_id"]),
                transaction_date=transaction_date,
                product_ids=product_ids,
            )
            resolutions.append((row, resolution))
        except Exception as exc:
            reason = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
            failures.append(
                f"company_id={row['company_id']} order_id={row['id']}: {reason}"
            )

    if failures:
        report = "\n".join(failures)
        raise RuntimeError(
            "HARMAN_VADELI legacy NULL due_date kayıtları otomatik "
            "çözülemedi; migration durduruldu:\n"
            f"{report}"
        )

    for row, resolution in resolutions:
        bind.execute(
            sa.text(
                """UPDATE orders
                SET due_date=:due_date,
                    harvest_calendar_id=:calendar_id,
                    due_date_source='automatic',
                    harvest_resolution_snapshot=:snapshot
                WHERE id=:order_id AND company_id=:cid
                  AND payment_term='HARMAN_VADELI' AND due_date IS NULL"""
            ),
            {
                "due_date": resolution.due_date.isoformat(),
                "calendar_id": resolution.calendar_id,
                "snapshot": resolution.snapshot,
                "order_id": row["id"],
                "cid": row["company_id"],
            },
        )
    print(f"Harman due-date backfill: {len(resolutions)} legacy NULL record resolved")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("harvest_regions"):
        op.create_table(
            "harvest_regions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column(
                "active", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.UniqueConstraint(
                "company_id", "code", name="uq_harvest_regions_company_code"
            ),
            sa.UniqueConstraint(
                "company_id", "id", name="uq_harvest_regions_company_id"
            ),
        )
        op.create_index(
            "ix_harvest_regions_company_active",
            "harvest_regions",
            ["company_id", "active", "name"],
        )

    customer_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("customers")
    }
    customer_unique_constraints = {
        constraint["name"]
        for constraint in sa.inspect(bind).get_unique_constraints("customers")
    }
    customer_needs_region = "harvest_region_id" not in customer_columns
    customer_needs_tenant_key = (
        "uq_customers_company_id" not in customer_unique_constraints
    )
    if customer_needs_region or customer_needs_tenant_key:
        with op.batch_alter_table("customers") as batch:
            if customer_needs_region:
                batch.add_column(sa.Column("harvest_region_id", sa.Integer()))
                batch.create_foreign_key(
                    "fk_customers_harvest_region",
                    "harvest_regions",
                    ["company_id", "harvest_region_id"],
                    ["company_id", "id"],
                )
            if customer_needs_tenant_key:
                batch.create_unique_constraint(
                    "uq_customers_company_id", ["company_id", "id"]
                )
    customer_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("customers")
    }
    if "ix_customers_company_harvest_region" not in customer_indexes:
        op.create_index(
            "ix_customers_company_harvest_region",
            "customers",
            ["company_id", "harvest_region_id"],
        )

    if not sa.inspect(bind).has_table("harvest_calendars"):
        op.create_table(
            "harvest_calendars",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("season_code", sa.String(length=60), nullable=False),
            sa.Column("name", sa.String(length=180), nullable=False),
            sa.Column("season_year", sa.Integer(), nullable=False),
            sa.Column("region_id", sa.Integer()),
            sa.Column("product_category", sa.String(length=160)),
            sa.Column("due_date", sa.Date(), nullable=False),
            sa.Column(
                "active", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "season_year >= 2000 AND season_year <= 2200",
                name="ck_harvest_calendar_year",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "region_id"],
                ["harvest_regions.company_id", "harvest_regions.id"],
            ),
            sa.UniqueConstraint(
                "company_id", "id", name="uq_harvest_calendars_company_id"
            ),
        )
        op.create_index(
            "ix_harvest_calendars_resolution",
            "harvest_calendars",
            ["company_id", "season_code", "due_date", "active"],
        )
        op.create_index(
            "ix_harvest_calendars_scope",
            "harvest_calendars",
            ["company_id", "region_id", "product_category", "due_date"],
        )

    if not sa.inspect(bind).has_table("harvest_due_rules"):
        op.create_table(
            "harvest_due_rules",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer()),
            sa.Column("region_id", sa.Integer()),
            sa.Column("product_category", sa.String(length=160)),
            sa.Column("season_code", sa.String(length=60), nullable=False),
            sa.Column(
                "priority", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("effective_from", sa.Date()),
            sa.Column("effective_to", sa.Date()),
            sa.Column(
                "active", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
            sa.CheckConstraint(
                "effective_from IS NULL OR effective_to IS NULL "
                "OR effective_to >= effective_from",
                name="ck_harvest_due_rule_effective_range",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(
                ["company_id", "customer_id"],
                ["customers.company_id", "customers.id"],
            ),
            sa.ForeignKeyConstraint(
                ["company_id", "region_id"],
                ["harvest_regions.company_id", "harvest_regions.id"],
            ),
            sa.UniqueConstraint(
                "company_id", "id", name="uq_harvest_due_rules_company_id"
            ),
        )
        op.create_index(
            "ix_harvest_due_rules_resolution",
            "harvest_due_rules",
            [
                "company_id",
                "active",
                "customer_id",
                "region_id",
                "product_category",
                "priority",
            ],
        )

    order_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("orders")
    }
    additions = {
        "harvest_calendar_id": sa.Column("harvest_calendar_id", sa.Integer()),
        "due_date_source": sa.Column(
            "due_date_source",
            sa.String(length=20),
            nullable=False,
            server_default="legacy",
        ),
        "due_date_override_reason": sa.Column(
            "due_date_override_reason", sa.String(length=500)
        ),
        "due_date_overridden_by": sa.Column(
            "due_date_overridden_by", sa.Integer()
        ),
        "due_date_overridden_at": sa.Column(
            "due_date_overridden_at", sa.DateTime(timezone=True)
        ),
        "harvest_resolution_snapshot": sa.Column(
            "harvest_resolution_snapshot", sa.Text()
        ),
    }
    missing = [column for name, column in additions.items() if name not in order_columns]
    if missing:
        with op.batch_alter_table("orders") as batch:
            for column in missing:
                batch.add_column(column)
            if "harvest_calendar_id" not in order_columns:
                batch.create_foreign_key(
                    "fk_orders_harvest_calendar",
                    "harvest_calendars",
                    ["company_id", "harvest_calendar_id"],
                    ["company_id", "id"],
                )
            if "due_date_source" not in order_columns:
                batch.create_check_constraint(
                    "ck_orders_due_date_source",
                    "due_date_source IN ('automatic','manual','legacy')",
                )
    order_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("orders")
    }
    if "ix_orders_company_harvest_calendar" not in order_indexes:
        op.create_index(
            "ix_orders_company_harvest_calendar",
            "orders",
            ["company_id", "harvest_calendar_id"],
        )
    _backfill_harman_due_dates(bind)
    order_checks = {
        constraint["name"]
        for constraint in sa.inspect(bind).get_check_constraints("orders")
    }
    if "ck_orders_harman_due_date_required" not in order_checks:
        with op.batch_alter_table("orders") as batch:
            batch.create_check_constraint(
                "ck_orders_harman_due_date_required",
                "payment_term != 'HARMAN_VADELI' OR due_date IS NOT NULL",
            )


def downgrade() -> None:
    raise RuntimeError(
        "Harman sezon/vade migration downgrade is intentionally disabled"
    )
