from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from ..business_time import business_today
from ..db import get_db
from ..money import money
from ..document_engine import SALES_IMPORT_NOTE, accounting_document_status_sql
from ..receivables_engine import (
    calculate_net_receivables,
    normalized_date_sql,
)
from ..tenancy import company_id

router = APIRouter(prefix="/reports", tags=["reports"])

# Top-product rows are grouped by product_name because order_items.product_id is
# nullable (ad-hoc lines). Expose the catalogue id only when the whole group maps
# to exactly one product, so the UI never links a name to an ambiguous record.
RESOLVED_PRODUCT_ID_SQL = (
    "CASE WHEN COUNT(DISTINCT oi.product_id)=1 THEN MIN(oi.product_id) END AS product_id"
)

AGING_BUCKETS = (
    "not_due",
    "days_1_30",
    "days_31_60",
    "days_61_90",
    "days_90_plus",
)


class ReceivableAgingDocument(BaseModel):
    id: int
    document_type: str
    document_no: str | None
    due_date: str
    remaining: str


class ReceivableAgingCustomer(BaseModel):
    customer_id: int
    customer_name: str
    not_due: str
    days_1_30: str
    days_31_60: str
    days_61_90: str
    days_90_plus: str
    total: str
    documents: list[ReceivableAgingDocument]


class ReceivableAgingTotals(BaseModel):
    not_due: str
    days_1_30: str
    days_31_60: str
    days_61_90: str
    days_90_plus: str
    total: str


class ReceivableAgingResponse(BaseModel):
    as_of: str
    customers: list[ReceivableAgingCustomer]
    totals: ReceivableAgingTotals


def _aging_bucket(due_date: date, as_of: date) -> str:
    overdue_days = (as_of - due_date).days
    if overdue_days <= 0:
        return "not_due"
    if overdue_days <= 30:
        return "days_1_30"
    if overdue_days <= 60:
        return "days_31_60"
    if overdue_days <= 90:
        return "days_61_90"
    return "days_90_plus"


def _money_string(value: object) -> str:
    return format(money(value), "f")


def _empty_aging_totals() -> dict[str, Decimal]:
    return {bucket: money(0) for bucket in AGING_BUCKETS}


def _normalized_date_sql(date_column: str, dialect_name: str) -> str:
    return normalized_date_sql(date_column, dialect_name)


def _has_column(db: Session, table_name: str, column_name: str) -> bool:
    bind = db.get_bind()
    if bind is None:
        return False
    table_metadata = inspect(bind).get_columns(table_name)
    return any(item.get("name") == column_name for item in table_metadata)


def _orders_accounting_status_sql(db: Session, *, alias: str = "") -> str:
    has_status = _has_column(db, "orders", "status")
    if not has_status:
        return "1=1"
    has_note = _has_column(db, "orders", "note")
    status_column = f"{alias}status"
    note_column = f"{alias}note"
    if has_note:
        return accounting_document_status_sql(status_column, note_column)
    return f"COALESCE({status_column}, 'completed') NOT IN ('cancelled')"


def _date_conditions(
    date_column: str,
    *,
    company_id_value: int,
    date_from: str | None,
    date_to: str | None,
    dialect_name: str = "sqlite",
) -> tuple[str, dict[str, Any]]:
    """Build a tenant/date predicate that supports current and legacy dates."""
    normalized = _normalized_date_sql(date_column, dialect_name)
    conditions = ["company_id=:cid"]
    params: dict[str, Any] = {"cid": company_id_value}
    if date_from:
        conditions.append(f"{normalized}>=:df")
        params["df"] = date_from
    if date_to:
        conditions.append(f"{normalized}<=:dt")
        params["dt"] = date_to
    return " WHERE " + " AND ".join(conditions), params


def _summary_row(
    db: Session,
    table: Literal["orders", "purchases"],
    where_clause: str,
    params: dict[str, Any],
) -> RowMapping:
    query = text(
        f"SELECT COALESCE(SUM(final_total),0) total,COUNT(*) count "
        f"FROM {table}{where_clause}"
    )
    return db.execute(query, params).mappings().one()


@router.get("/summary")
def summary(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    dialect_name = db.bind.dialect.name if db.bind is not None else "sqlite"
    sales_where, sales_params = _date_conditions(
        "order_date",
        company_id_value=cid,
        date_from=date_from,
        date_to=date_to,
        dialect_name=dialect_name,
    )
    orders_accounting_status_sql = _orders_accounting_status_sql(db)
    orders_accounting_status_sql_with_alias = _orders_accounting_status_sql(db, alias="o.")
    sales_params["sales_import_note"] = SALES_IMPORT_NOTE
    sales_where = f"""{sales_where} AND {orders_accounting_status_sql}"""
    purchase_where, purchase_params = _date_conditions(
        "purchase_date",
        company_id_value=cid,
        date_from=date_from,
        date_to=date_to,
        dialect_name=dialect_name,
    )
    payment_where, payment_params = _date_conditions(
        "payment_date",
        company_id_value=cid,
        date_from=date_from,
        date_to=date_to,
        dialect_name=dialect_name,
    )

    sales = _summary_row(
        db,
        "orders",
        sales_where,
        sales_params,
    )
    purchases = _summary_row(db, "purchases", purchase_where, purchase_params)
    payment_rows = db.execute(
        text(
            "SELECT entity_type,COALESCE(SUM(amount),0) total "
            f"FROM payments{payment_where} GROUP BY entity_type"
        ),
        payment_params,
    ).mappings().all()
    payments_by_entity = {row["entity_type"]: row["total"] for row in payment_rows}

    normalized_order_date = _normalized_date_sql("o.order_date", dialect_name)
    order_conditions = [
        "o.company_id=:cid",
        orders_accounting_status_sql_with_alias,
    ]
    order_params: dict[str, Any] = {"cid": cid}
    if date_from:
        order_conditions.append(f"{normalized_order_date}>=:df")
        order_params["df"] = date_from
    if date_to:
        order_conditions.append(f"{normalized_order_date}<=:dt")
        order_params["dt"] = date_to
    order_params["sales_import_note"] = SALES_IMPORT_NOTE
    order_where = " WHERE " + " AND ".join(order_conditions)

    top_customers = db.execute(
        text(
            "SELECT c.id AS customer_id,c.name,SUM(o.final_total) total,COUNT(o.id) count "
            "FROM orders o JOIN customers c "
            "ON c.id=o.customer_id AND c.company_id=o.company_id"
            f"{order_where} GROUP BY c.id,c.name ORDER BY total DESC LIMIT 10"
        ),
        order_params,
    ).mappings().all()
    top_products = db.execute(
        text(
            f"SELECT oi.product_name,{RESOLVED_PRODUCT_ID_SQL},"
            "SUM(oi.quantity) quantity,SUM(oi.line_total) total "
            "FROM order_items oi JOIN orders o ON o.id=oi.order_id"
            f"{order_where} GROUP BY oi.product_name ORDER BY total DESC LIMIT 10"
        ),
        order_params,
    ).mappings().all()

    normalized_month_date = _normalized_date_sql("order_date", dialect_name)
    if dialect_name == "sqlite":
        month_expression = f"substr({normalized_month_date},1,7)"
    else:
        month_expression = f"SUBSTRING({normalized_month_date} FROM 1 FOR 7)"
    monthly = db.execute(
        text(
            f"SELECT {month_expression} AS month,SUM(final_total) total "
            "FROM orders WHERE company_id=:cid "
            f"AND {orders_accounting_status_sql} "
            f"GROUP BY {month_expression} ORDER BY month DESC LIMIT 12"
        ),
        {"cid": cid, "sales_import_note": SALES_IMPORT_NOTE},
    ).mappings().all()

    return {
        "sales_total": sales["total"],
        "sales_count": sales["count"],
        "purchases_total": purchases["total"],
        "purchases_count": purchases["count"],
        "collections": payments_by_entity.get("customer", 0),
        "payments": payments_by_entity.get("supplier", 0),
        "gross_difference": money(sales["total"]) - money(purchases["total"]),
        "top_customers": [dict(row) for row in top_customers],
        "top_products": [dict(row) for row in top_products],
        "monthly_sales": list(reversed([dict(row) for row in monthly])),
    }


@router.get("/receivables-aging", response_model=ReceivableAgingResponse)
def receivables_aging(
    request: Request,
    as_of: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    report_date = as_of or business_today()
    documents = calculate_net_receivables(db, cid, report_date)

    customers: dict[int, dict[str, Any]] = {}
    grand_totals = _empty_aging_totals()
    grand_total = money(0)
    for document in documents:
        if document.remaining == 0:
            continue
        bucket = _aging_bucket(document.due_date, report_date)
        customer = customers.setdefault(
            document.customer_id,
            {
                "customer_id": document.customer_id,
                "customer_name": document.customer_name,
                "buckets": _empty_aging_totals(),
                "total": money(0),
                "documents": [],
            },
        )
        customer["buckets"][bucket] += document.remaining
        customer["total"] += document.remaining
        customer["documents"].append(
            {
                "id": document.id,
                "document_type": document.document_type,
                "document_no": document.document_no,
                "due_date": document.due_date.isoformat(),
                "remaining": _money_string(document.remaining),
            }
        )
        grand_totals[bucket] += document.remaining
        grand_total += document.remaining

    customer_rows = [
        {
            "customer_id": customer["customer_id"],
            "customer_name": customer["customer_name"],
            **{
                bucket: _money_string(customer["buckets"][bucket])
                for bucket in AGING_BUCKETS
            },
            "total": _money_string(customer["total"]),
            "documents": customer["documents"],
        }
        for customer in customers.values()
    ]
    return {
        "as_of": report_date.isoformat(),
        "customers": customer_rows,
        "totals": {
            **{
                bucket: _money_string(grand_totals[bucket])
                for bucket in AGING_BUCKETS
            },
            "total": _money_string(grand_total),
        },
    }
