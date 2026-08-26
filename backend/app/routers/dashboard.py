from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import has_permission
from ..business_time import business_today
from ..db import get_db
from ..money import ZERO_MONEY, money
from ..document_engine import SALES_IMPORT_NOTE, accounting_document_status_sql
from ..tenancy import company_id
from .reports import RESOLVED_PRODUCT_ID_SQL

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

ACTIVE_STATUSES_SQL = "('approved','completed')"
ORDER_ACCOUNTING_STATUS_SQL = accounting_document_status_sql("o.status", "o.note")
ORDER_ACCOUNTING_STATUS_SQL_NO_ALIAS = accounting_document_status_sql("status", "note")


def parse_date(value: object) -> date | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(raw[:19], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _row(db: Session, sql: str, params: dict[str, object]) -> dict:
    result = db.execute(text(sql), params).mappings().first()
    return dict(result or {})


def _rows(db: Session, sql: str, params: dict[str, object]) -> list[dict]:
    return [dict(item) for item in db.execute(text(sql), params).mappings().all()]


@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    user = getattr(request.state, "user", None)
    role = str(user.get("role") or "") if isinstance(user, dict) else ""
    can_view_finance = has_permission(role, "finance")
    today = business_today()
    month_start = today.replace(day=1)
    trend_start = today - timedelta(days=13)
    params = {
        "cid": cid,
        "today": today.isoformat(),
        "month_start": month_start.isoformat(),
        "trend_start": trend_start.isoformat(),
        "sales_import_note": SALES_IMPORT_NOTE,
    }

    summary = _row(
        db,
        f"""
        WITH sales AS (
          SELECT
          COALESCE(SUM(CASE WHEN order_date=:today AND {ORDER_ACCOUNTING_STATUS_SQL} THEN final_total ELSE 0 END),0) AS today_sales,
          COALESCE(SUM(CASE WHEN order_date>=:month_start AND {ORDER_ACCOUNTING_STATUS_SQL} THEN final_total ELSE 0 END),0) AS month_sales,
          COALESCE(SUM(CASE WHEN {ORDER_ACCOUNTING_STATUS_SQL} THEN final_total ELSE 0 END),0) AS active_sales,
          COALESCE(SUM(CASE WHEN due_date IS NOT NULL AND due_date<:today
                            AND {ORDER_ACCOUNTING_STATUS_SQL}
                            AND final_total-COALESCE(paid_amount,0)>0 THEN 1 ELSE 0 END),0) AS overdue_count,
          COALESCE(SUM(CASE WHEN due_date IS NOT NULL AND due_date<:today
                            AND {ORDER_ACCOUNTING_STATUS_SQL}
                            AND final_total-COALESCE(paid_amount,0)>0
                            THEN final_total-COALESCE(paid_amount,0) ELSE 0 END),0) AS overdue_total
          FROM orders o
          WHERE o.company_id=:cid AND {ORDER_ACCOUNTING_STATUS_SQL}
        ), purchases_summary AS (
          SELECT
          COALESCE(SUM(CASE WHEN purchase_date>=:month_start THEN final_total ELSE 0 END),0) AS month_purchases,
          COALESCE(SUM(final_total),0) AS active_purchases
          FROM purchases
          WHERE company_id=:cid AND COALESCE(status,'completed') IN {ACTIVE_STATUSES_SQL}
        ), payment_summary AS (
          SELECT
          COALESCE(SUM(CASE WHEN entity_type='customer' AND payment_date=:today THEN amount ELSE 0 END),0) AS today_collections,
          COALESCE(SUM(CASE WHEN entity_type='customer' AND payment_date>=:month_start THEN amount ELSE 0 END),0) AS month_collections,
          COALESCE(SUM(CASE WHEN entity_type='customer' THEN amount ELSE 0 END),0) AS customer_payments,
          COALESCE(SUM(CASE WHEN entity_type='supplier' THEN amount ELSE 0 END),0) AS supplier_payments
          FROM payments WHERE company_id=:cid
        ), expense_summary AS (
          SELECT COALESCE(SUM(amount),0) AS month_expenses
          FROM income_expenses
          WHERE company_id=:cid AND LOWER(COALESCE(txn_type,''))='expense'
            AND txn_date>=:month_start
        ), product_summary AS (
          SELECT
          COUNT(*) AS product_count,
          COALESCE(SUM(stock * purchase_price),0) AS stock_value,
          COALESCE(SUM(CASE WHEN stock <= CASE
              WHEN COALESCE(critical_stock,0) >= COALESCE(minimum_stock,0)
              THEN COALESCE(critical_stock,0)
              ELSE COALESCE(minimum_stock,0)
          END THEN 1 ELSE 0 END),0) AS critical_stock_count
          FROM products
          WHERE company_id=:cid AND COALESCE(active, TRUE)=TRUE
        ), customer_summary AS (
          SELECT COUNT(*) AS customer_count,
                 COALESCE(SUM(opening_balance),0) AS customer_opening
          FROM customers WHERE company_id=:cid
        ), supplier_summary AS (
          SELECT COALESCE(SUM(opening_balance),0) AS supplier_opening
          FROM suppliers WHERE company_id=:cid
        )
        SELECT sales.today_sales,sales.month_sales,purchases_summary.month_purchases,
               payment_summary.today_collections,payment_summary.month_collections,
               expense_summary.month_expenses,product_summary.product_count,
               product_summary.stock_value,product_summary.critical_stock_count,
               customer_summary.customer_count,
               customer_summary.customer_opening+sales.active_sales-payment_summary.customer_payments AS customer_receivables,
               supplier_summary.supplier_opening+purchases_summary.active_purchases-payment_summary.supplier_payments AS supplier_payables,
               sales.overdue_count,sales.overdue_total
        FROM sales,purchases_summary,payment_summary,expense_summary,
             product_summary,customer_summary,supplier_summary
        """,
        params,
    )

    today_sales = money(summary.get("today_sales"))
    month_sales = money(summary.get("month_sales"))
    month_purchases = money(summary.get("month_purchases"))
    today_collections = money(summary.get("today_collections"))
    month_collections = money(summary.get("month_collections"))
    month_expenses = money(summary.get("month_expenses"))
    month_profit = month_sales - month_purchases - month_expenses

    critical_products = _rows(
        db,
        """
        SELECT id,name,stock,unit,purchase_price,sale_price,critical_stock,minimum_stock,
               oem_number,brand,location
        FROM products
        WHERE company_id=:cid AND COALESCE(active, TRUE)=TRUE
          AND stock <= CASE WHEN COALESCE(critical_stock,0) >= COALESCE(minimum_stock,0)
                           THEN COALESCE(critical_stock,0)
                           ELSE COALESCE(minimum_stock,0) END
        ORDER BY stock ASC, LOWER(name) ASC
        LIMIT 8
        """,
        params,
    )

    supplier_payables = money(summary.get("supplier_payables"))

    finance_accounts = (
        _rows(
            db,
            """
            SELECT a.id,a.name,a.account_type,a.currency,a.opening_balance,a.bank_name,
                   a.opening_balance + COALESCE(SUM(CASE WHEN t.direction='in' THEN t.amount ELSE -t.amount END),0) AS balance
            FROM finance_accounts a
            LEFT JOIN finance_transactions t
              ON t.account_id=a.id AND t.company_id=a.company_id
            WHERE a.company_id=:cid AND COALESCE(a.is_active, TRUE)=TRUE
            GROUP BY a.id,a.name,a.account_type,a.currency,a.opening_balance,a.bank_name
            ORDER BY balance DESC
            LIMIT 8
            """,
            params,
        )
        if can_view_finance
        else []
    )
    cash_bank_total = sum(
        (money(account.get("balance")) for account in finance_accounts),
        ZERO_MONEY,
    )

    overdue_receivables = _rows(
        db,
        f"""
        SELECT o.id,o.customer_id,c.name AS customer_name,o.due_date,o.document_no,
               o.final_total-COALESCE(o.paid_amount,0) AS remaining
        FROM orders o
        JOIN customers c ON c.id=o.customer_id AND c.company_id=o.company_id
        WHERE o.company_id=:cid
         AND {ORDER_ACCOUNTING_STATUS_SQL}
          AND o.due_date IS NOT NULL AND o.due_date<:today
          AND o.final_total-COALESCE(o.paid_amount,0)>0
        ORDER BY o.due_date ASC, remaining DESC
        LIMIT 8
        """,
        params,
    )
    for item in overdue_receivables:
        due = parse_date(item.get("due_date"))
        item["days_overdue"] = (today - due).days if due else 0
        item["remaining"] = round(money(item.get("remaining")), 2)
        item["document_no"] = item.get("document_no") or f"S-{item['id']}"

    recent_sales = _rows(
        db,
        f"""
        SELECT o.id,o.order_date,o.due_date,o.final_total,o.paid_amount,o.status,o.document_no,
               c.id AS customer_id,c.name AS customer_name
        FROM orders o
        JOIN customers c ON c.id=o.customer_id AND c.company_id=o.company_id
         WHERE o.company_id=:cid AND {ORDER_ACCOUNTING_STATUS_SQL}
        ORDER BY o.order_date DESC,o.id DESC
        LIMIT 8
        """,
        params,
    )

    trend_rows = _rows(
        db,
        f"""
        SELECT order_date AS day,COALESCE(SUM(final_total),0) AS total
        FROM orders
         WHERE company_id=:cid AND {ORDER_ACCOUNTING_STATUS_SQL_NO_ALIAS}
          AND order_date>=:trend_start AND order_date<=:today
        GROUP BY order_date
        """,
        params,
    )
    trend_map = {str(item["day"])[:10]: money(item["total"]) for item in trend_rows}
    sales_trend = []
    for offset in range(14):
        day = trend_start + timedelta(days=offset)
        sales_trend.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%d.%m"),
                "total": round(trend_map.get(day.isoformat(), ZERO_MONEY), 2),
            }
        )

    top_products = _rows(
        db,
        f"""
        SELECT oi.product_name AS name,{RESOLVED_PRODUCT_ID_SQL},
               SUM(oi.quantity) AS quantity,SUM(oi.line_total) AS total
        FROM order_items oi
        JOIN orders o ON o.id=oi.order_id
         WHERE o.company_id=:cid AND oi.company_id=:cid AND {ORDER_ACCOUNTING_STATUS_SQL}
        GROUP BY oi.product_name
        ORDER BY total DESC
        LIMIT 5
        """,
        params,
    )

    recent_payments = _rows(
        db,
        """
        SELECT p.id,p.payment_date,p.amount,p.entity_type,p.entity_id,p.payment_method,p.note,
               CASE WHEN p.entity_type='customer' THEN c.name ELSE s.name END AS entity_name
        FROM payments p
        LEFT JOIN customers c
          ON p.entity_type='customer' AND c.id=p.entity_id AND c.company_id=p.company_id
        LEFT JOIN suppliers s
          ON p.entity_type='supplier' AND s.id=p.entity_id AND s.company_id=p.company_id
        WHERE p.company_id=:cid
        ORDER BY p.payment_date DESC,p.id DESC
        LIMIT 5
        """,
        params,
    )
    recent_finance = (
        _rows(
            db,
            """
            SELECT id,account_id,txn_date,direction,amount,category,description
            FROM finance_transactions
            WHERE company_id=:cid
            ORDER BY txn_date DESC,id DESC
            LIMIT 5
            """,
            params,
        )
        if can_view_finance
        else []
    )

    activity: list[dict] = []
    for sale in recent_sales[:5]:
        activity.append(
            {
                "type": "sale",
                "date": sale["order_date"],
                "title": sale["customer_name"],
                "amount": money(sale["final_total"]),
                "id": sale["id"],
                "entity_id": sale.get("customer_id"),
                "entity_type": "customer",
            }
        )
    for payment in recent_payments:
        activity.append(
            {
                "type": "collection"
                if payment["entity_type"] == "customer"
                else "payment",
                "date": payment["payment_date"],
                "title": payment.get("entity_name") or "Cari hareket",
                "amount": money(payment["amount"]),
                "id": payment["id"],
                "entity_id": payment.get("entity_id"),
                "entity_type": payment.get("entity_type"),
            }
        )
    for txn in recent_finance:
        activity.append(
            {
                "type": "finance",
                "date": txn["txn_date"],
                "title": txn.get("description") or txn.get("category") or "Finans hareketi",
                "amount": money(txn["amount"]),
                "id": txn["id"],
                "account_id": txn.get("account_id"),
            }
        )
    activity.sort(
        key=lambda item: (parse_date(item["date"]) or date.min, int(item["id"])),
        reverse=True,
    )

    return {
        "as_of": today.isoformat(),
        "today_sales": round(today_sales, 2),
        "today_collections": round(today_collections, 2),
        "month_sales": round(month_sales, 2),
        "month_collections": round(month_collections, 2),
        "month_purchases": round(month_purchases, 2),
        "month_expenses": round(month_expenses, 2),
        "month_profit": round(month_profit, 2),
        "stock_value": round(money(summary.get("stock_value")), 2),
        # Finans yetkisi olmayan role gerçek bakiye sızmasın diye maskeleniyor.
        "cash_bank_total": round(cash_bank_total, 2) if can_view_finance else 0,
        "customer_receivables": round(money(summary.get("customer_receivables")), 2),
        "supplier_payables": round(supplier_payables, 2),
        "customer_count": int(summary.get("customer_count") or 0),
        "product_count": int(summary.get("product_count") or 0),
        "critical_stock_count": int(summary.get("critical_stock_count") or 0),
        "overdue_count": int(summary.get("overdue_count") or 0),
        "overdue_total": round(money(summary.get("overdue_total")), 2),
        "recent_sales": recent_sales,
        "critical_products": critical_products,
        "overdue_receivables": overdue_receivables,
        "finance_accounts": finance_accounts,
        "sales_trend": sales_trend,
        "top_products": top_products,
        "recent_activity": activity[:10],
    }
