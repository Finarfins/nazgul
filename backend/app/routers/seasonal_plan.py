from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..business_time import business_today
from ..db import get_db
from ..money import quantity
from ..tenancy import company_id

router = APIRouter(prefix="/analytics/seasonal-plan", tags=["seasonal-plan"])


def _parse_months(value: str) -> list[int]:
    try:
        months = sorted({int(part.strip()) for part in value.split(",") if part.strip()})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Aylar 1-12 arasında sayı olmalıdır") from exc
    if not months or any(month < 1 or month > 12 for month in months):
        raise HTTPException(status_code=422, detail="En az bir geçerli ay seçin (1-12)")
    return months


@router.get("")
def seasonal_stock_plan(
    request: Request,
    months: str = Query(..., description="Comma-separated month numbers, for example 7,8,9"),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    selected_months = _parse_months(months)
    historical_year = business_today().year - 1
    params: dict[str, object] = {
        "cid": cid,
        "year_start": f"{historical_year:04d}-01-01",
        "next_year_start": f"{historical_year + 1:04d}-01-01",
    }
    month_placeholders = []
    for index, month in enumerate(selected_months):
        key = f"month_{index}"
        params[key] = f"{month:02d}"
        month_placeholders.append(f":{key}")

    rows = db.execute(
        text(
            f"""
            SELECT p.id AS product_id, p.name AS product_name,
                   p.product_code, p.stock AS current_stock,
                   SUM(oi.quantity) AS historical_demand
            FROM orders o
            JOIN order_items oi ON oi.order_id=o.id
            JOIN products p
              ON p.id=oi.product_id
             AND p.company_id=:cid
            WHERE o.company_id=:cid
              AND o.order_date>=:year_start
              AND o.order_date<:next_year_start
              AND substr(o.order_date,6,2) IN ({",".join(month_placeholders)})
              AND COALESCE(o.status,'completed') NOT IN ('cancelled','draft')
              AND COALESCE(p.active,TRUE)=TRUE
            GROUP BY p.id,p.name,p.product_code,p.stock
            ORDER BY historical_demand DESC,p.name ASC,p.id ASC
            """
        ),
        params,
    ).mappings().all()

    items = []
    total_demand = quantity(0)
    total_suggestion = quantity(0)
    for row in rows:
        demand = quantity(row["historical_demand"])
        stock = quantity(row["current_stock"])
        # Increment 1 intentionally uses a transparent baseline:
        # suggested top-up = max(last year's selected-month demand - current stock, 0).
        suggested = quantity(max(demand - stock, Decimal("0")))
        total_demand += demand
        total_suggestion += suggested
        items.append(
            {
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "product_code": row["product_code"],
                "historical_demand": demand,
                "current_stock": stock,
                "suggested_quantity": suggested,
            }
        )

    return {
        "historical_year": historical_year,
        "months": selected_months,
        "formula": "max(historical_demand - current_stock, 0)",
        "total_historical_demand": quantity(total_demand),
        "total_suggested_quantity": quantity(total_suggestion),
        "items": items,
    }
