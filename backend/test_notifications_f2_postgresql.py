"""Real PostgreSQL 16 boundary gate for F2's normalized due date."""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.postgresql


def test_due_date_normalization_boundaries_on_postgresql() -> None:
    url = os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("PostgreSQL test URL yok")
    engine = create_engine(url)
    config = Config("alembic.ini")
    command.upgrade(config, "20260728_0034")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies(id,name,is_active,created_at) "
                "VALUES (91001,'F2 PG',TRUE,CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO customers(id,company_id,name) "
                "VALUES (91001,91001,'F2 PG')"
            )
        )
        values = (None, "", "bozuk", "2026-02-30", "2026-07-27", "2026-07-28", "2026-07-29")
        for index, due_date in enumerate(values, 91001):
            connection.execute(
                text(
                    """INSERT INTO orders
                    (id,customer_id,order_date,subtotal,vat_total,grand_total,
                     discount_percent,discount_amount,final_total,due_date,
                     company_id,status,payment_term)
                    VALUES (:id,91001,'2026-07-01',1,0,1,0,0,1,:due,
                            91001,'approved','PESIN')"""
                ),
                {"id": index, "due": due_date},
            )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT due_date_normalized FROM orders "
                "WHERE company_id=91001 ORDER BY id"
            )
        ).scalars().all()
    assert rows[:4] == [None, None, None, None]
    assert [value.isoformat() for value in rows[4:]] == [
        "2026-07-27",
        "2026-07-28",
        "2026-07-29",
    ]
