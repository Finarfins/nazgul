from __future__ import annotations

import inspect

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.routers.reports import _date_conditions, _normalized_date_sql, summary


def test_date_sql_supports_sqlite_and_postgresql() -> None:
    sqlite_sql = _normalized_date_sql("order_date", "sqlite")
    assert "substr(order_date,7,4)" in sqlite_sql
    assert "substr(order_date,1,10)" in sqlite_sql

    postgresql_sql = _normalized_date_sql("o.order_date", "postgresql")
    assert "TO_DATE" in postgresql_sql
    assert "DD.MM.YYYY" in postgresql_sql
    assert "SUBSTRING(CAST(o.order_date AS TEXT) FROM 1 FOR 10)" in postgresql_sql

    where, params = _date_conditions(
        "o.order_date",
        company_id_value=7,
        date_from="2026-07-01",
        date_to="2026-07-31",
        dialect_name="postgresql",
    )
    assert "company_id=:cid" in where
    assert "TO_DATE" in where
    assert ">=:df" in where
    assert "<=:dt" in where
    assert params == {"cid": 7, "df": "2026-07-01", "dt": "2026-07-31"}


def test_summary_combines_iso_and_legacy_dates_in_sqlite() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE customers (id INTEGER PRIMARY KEY, company_id INTEGER, name TEXT)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, company_id INTEGER, customer_id INTEGER, order_date TEXT, final_total NUMERIC)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE purchases (id INTEGER PRIMARY KEY, company_id INTEGER, purchase_date TEXT, final_total NUMERIC)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE payments (id INTEGER PRIMARY KEY, company_id INTEGER, payment_date TEXT, entity_type TEXT, amount NUMERIC)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE order_items (id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, product_name TEXT, quantity NUMERIC, line_total NUMERIC)"
        )
        connection.exec_driver_sql(
            "INSERT INTO customers VALUES (1,7,'Müşteri A'),(2,8,'Diğer Firma')"
        )
        connection.exec_driver_sql(
            "INSERT INTO orders VALUES "
            "(1,7,1,'10.07.2026',100),(2,7,1,'2026-07-11',50),"
            "(3,7,1,'30.06.2026',40),(4,8,2,'2026-07-12',999)"
        )
        connection.exec_driver_sql(
            "INSERT INTO purchases VALUES "
            "(1,7,'12.07.2026',30),(2,7,'2026-07-13',20),(3,8,'2026-07-13',500)"
        )
        connection.exec_driver_sql(
            "INSERT INTO payments VALUES "
            "(1,7,'14.07.2026','customer',25),"
            "(2,7,'2026-07-15','supplier',10),"
            "(3,8,'2026-07-15','customer',400)"
        )
        connection.exec_driver_sql(
            "INSERT INTO order_items VALUES "
            "(1,1,11,'Pompa',1,100),(2,2,12,'Filtre',2,50),"
            "(3,3,13,'Eski Ay',1,40),(4,4,14,'Diğer Firma',1,999)"
        )

    request = Request({"type": "http", "method": "GET", "path": "/api/reports/summary", "headers": []})
    request.state.company_id = 7
    with Session(engine) as db:
        result = summary(
            request,
            date_from="2026-07-01",
            date_to="2026-07-31",
            db=db,
        )

    assert float(result["sales_total"]) == 150.0
    assert result["sales_count"] == 2
    assert float(result["purchases_total"]) == 50.0
    assert result["purchases_count"] == 2
    assert float(result["collections"]) == 25.0
    assert float(result["payments"]) == 10.0
    assert float(result["gross_difference"]) == 100.0
    assert result["top_customers"] == [
        {"customer_id": 1, "name": "Müşteri A", "total": 150, "count": 2}
    ]
    assert {row["product_name"] for row in result["top_products"]} == {"Pompa", "Filtre"}
    july = next(row for row in result["monthly_sales"] if row["month"] == "2026-07")
    assert float(july["total"]) == 150.0


def test_top_customer_join_is_tenant_consistent() -> None:
    source = inspect.getsource(summary)
    assert "c.company_id=o.company_id" in source
