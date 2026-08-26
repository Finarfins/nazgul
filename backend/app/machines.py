"""V3.0 Machine Cards ("Makine kartları") schema.

A machine belongs to exactly one company and may optionally be linked to a
customer of that same company. Chassis and serial numbers are unique per company
*when present* (NULL values never collide), enforced with partial unique indexes
that both SQLite and PostgreSQL honour. There is intentionally no hard-delete
workflow: machines are deactivated via ``is_active`` instead.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    true,
)

metadata = MetaData()

machines = Table(
    "machines",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False),
    Column("customer_id", Integer, nullable=True),
    Column("brand", String(160), nullable=False),
    Column("manufacturer", String(160)),
    Column("model", String(160), nullable=False),
    Column("variant", String(160)),
    Column("serial_number", String(120)),
    Column("chassis_number", String(120)),
    Column("registration_number", String(120)),
    Column("engine_number", String(120)),
    Column("model_year", Integer),
    Column("working_hours", Numeric(12, 2)),
    Column("status", String(40), nullable=False, server_default="active"),
    Column("notes", Text),
    Column("is_active", Boolean, nullable=False, server_default=true()),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)

# Tenant-scoped lookup indexes.
Index("ix_machines_company", machines.c.company_id)
Index("ix_machines_company_customer", machines.c.company_id, machines.c.customer_id)

# Chassis/serial are unique per company only when a value is present. A partial
# unique index gives the DB-level guarantee that survives concurrent inserts,
# while leaving multiple NULL identifiers allowed. Both dialects support the
# ``WHERE ... IS NOT NULL`` predicate.
Index(
    "uq_machines_company_chassis",
    machines.c.company_id,
    machines.c.chassis_number,
    unique=True,
    sqlite_where=machines.c.chassis_number.isnot(None),
    postgresql_where=machines.c.chassis_number.isnot(None),
)
Index(
    "uq_machines_company_serial",
    machines.c.company_id,
    machines.c.serial_number,
    unique=True,
    sqlite_where=machines.c.serial_number.isnot(None),
    postgresql_where=machines.c.serial_number.isnot(None),
)
