from __future__ import annotations

from starlette.requests import Request

from app.routers.dashboard import dashboard


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class RecordingSession:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, params):
        sql = str(statement)
        self.calls.append((sql, dict(params)))
        if len(self.calls) == 1:
            return Rows([{
                "today_sales": 0,
                "month_sales": 0,
                "active_sales": 0,
                "overdue_count": 0,
                "overdue_total": 0,
                "month_purchases": 0,
                "active_purchases": 0,
                "today_collections": 0,
                "month_collections": 0,
                "customer_payments": 0,
                "supplier_payments": 0,
                "month_expenses": 0,
                "product_count": 0,
                "stock_value": 0,
                "critical_stock_count": 0,
                "customer_count": 0,
                "customer_opening": 0,
                "supplier_opening": 0,
                "customer_receivables": 0,
                "supplier_payables": 0,
            }])
        return Rows([])


def test_dashboard_uses_one_summary_query_and_nine_queries_total() -> None:
    request = Request({"type": "http", "method": "GET", "path": "/api/dashboard", "headers": []})
    request.state.company_id = 7
    request.state.user = {"role": "admin"}
    db = RecordingSession()

    result = dashboard(request, db=db)

    assert len(db.calls) == 9
    summary_sql, summary_params = db.calls[0]
    assert "WITH sales AS" in summary_sql
    assert "purchases_summary AS" in summary_sql
    assert "payment_summary AS" in summary_sql
    assert "expense_summary AS" in summary_sql
    assert "product_summary AS" in summary_sql
    assert "customer_summary AS" in summary_sql
    assert "supplier_summary AS" in summary_sql
    assert summary_params["cid"] == 7
    assert result["today_sales"] == 0
    assert result["month_sales"] == 0
    assert result["customer_receivables"] == 0
    assert result["supplier_payables"] == 0
    assert len(result["sales_trend"]) == 14
    assert result["recent_activity"] == []
