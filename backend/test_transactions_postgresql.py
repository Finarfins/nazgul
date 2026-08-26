import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.routers.transactions import _list


@pytest.fixture()
def postgres_engine():
    database_url = os.getenv("TRANSACTIONS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TRANSACTIONS_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("TRANSACTIONS_TEST_DATABASE_URL must point to PostgreSQL")

    admin_engine = create_engine(database_url, pool_pre_ping=True)
    schema = f"transactions_test_{uuid4().hex}"
    quoted_schema = admin_engine.dialect.identifier_preparer.quote(schema)
    with admin_engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))

    engine = create_engine(
        database_url,
        connect_args={"options": f"-csearch_path={schema}"},
        pool_pre_ping=True,
    )
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {quoted_schema} CASCADE"))
        admin_engine.dispose()


def test_order_list_normalizes_dates_filters_and_isolates_tenant(postgres_engine):
    with postgres_engine.begin() as connection:
        statements = (
            "CREATE TABLE customers (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL)",
            "CREATE TABLE suppliers (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL)",
            "CREATE TABLE warehouses (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL)",
            """CREATE TABLE orders (
                id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL,
                customer_id INTEGER NOT NULL, order_date TEXT NOT NULL,
                document_no TEXT, subtotal NUMERIC(18,2), vat_total NUMERIC(18,2),
                final_total NUMERIC(18,2), paid_amount NUMERIC(18,2),
                status TEXT, payment_method TEXT, warehouse_id INTEGER
            )""",
            "CREATE TABLE order_items (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL)",
            """CREATE TABLE purchases (
                id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL,
                supplier_id INTEGER NOT NULL, purchase_date TEXT NOT NULL,
                document_no TEXT, subtotal NUMERIC(18,2), vat_total NUMERIC(18,2),
                final_total NUMERIC(18,2), paid_amount NUMERIC(18,2),
                status TEXT, payment_method TEXT, warehouse_id INTEGER
            )""",
            "CREATE TABLE purchase_items (id INTEGER PRIMARY KEY, purchase_id INTEGER NOT NULL)",
            "INSERT INTO customers VALUES (1,10,'Tenant 10'),(2,20,'Tenant 20')",
            "INSERT INTO suppliers VALUES (11,10,'Supplier 10'),(12,20,'Supplier 20')",
            "INSERT INTO warehouses VALUES (1,10,'Main'),(2,20,'Other')",
            """INSERT INTO orders VALUES
                (1,10,1,'12.07.2026','S-1',100,20,120,0,'completed','credit',1),
                (2,10,1,'2026-07-13T09:30:00','S-2',200,40,240,0,'completed','credit',1),
                (3,20,2,'2026-07-14','S-3',300,60,360,0,'completed','credit',2)""",
            "INSERT INTO order_items VALUES (1,1),(2,2),(3,3)",
            """INSERT INTO purchases VALUES
                (11,10,11,'2026-07-13','A-1',50,10,60,0,'completed','credit',1),
                (12,20,12,'2026-07-14','A-2',70,14,84,0,'completed','credit',2)""",
            "INSERT INTO purchase_items VALUES (11,11),(12,12)",
        )
        for statement in statements:
            connection.execute(text(statement))

    request = SimpleNamespace(state=SimpleNamespace(company_id=10))
    with Session(postgres_engine) as session:
        rows = _list(
            "sale", request, "", None, "2026-07-12", "2026-07-13", "date_asc", 100, session
        )
        purchase_rows = _list(
            "purchase", request, "", None, None, None, "date_asc", 100, session
        )

    assert [row["id"] for row in rows] == [1, 2]
    assert [row["normalized_date"].isoformat() for row in rows] == [
        "2026-07-12",
        "2026-07-13",
    ]
    assert all(row["customer_name"] == "Tenant 10" for row in rows)
    assert all(row["entity_id"] == 1 for row in rows)
    assert [row["id"] for row in purchase_rows] == [11]
    assert purchase_rows[0]["supplier_name"] == "Supplier 10"
    assert purchase_rows[0]["entity_id"] == 11
