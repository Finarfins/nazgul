from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..business_time import business_today
from ..db import get_db
from ..money import money, quantity
from ..pos_contracts import QuickPickItem
from ..tenancy import company_id
from .transactions import _warehouse

router = APIRouter(prefix="/quick-pick", tags=["quick-pick"])


@router.get("", response_model=list[QuickPickItem])
def quick_pick_products(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    days: int = Query(180, ge=1, le=3650),
    customer_id: int | None = Query(None, gt=0),
    warehouse_id: int | None = Query(None, gt=0),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    resolved_warehouse_id = _warehouse(db, cid, warehouse_id)
    today = business_today()
    params: dict[str, object] = {
        "cid": cid,
        "cutoff": (today - timedelta(days=days)).isoformat(),
        "today": today.isoformat(),
        "limit": limit,
        "warehouse_id": resolved_warehouse_id,
    }
    customer_filter = ""
    if customer_id is not None:
        customer_filter = "AND o.customer_id=:customer_id"
        params["customer_id"] = customer_id

    rows = db.execute(
        text(
            f"""
            SELECT p.id AS product_id, p.name AS product_name,
                   p.product_code, p.sale_price AS unit_price,
                   COALESCE(ws.quantity,0) AS current_stock,
                   COUNT(oi.id) AS times_sold,
                   SUM(oi.quantity) AS total_quantity
            FROM orders o
            JOIN order_items oi ON oi.order_id=o.id
            JOIN products p
              ON p.id=oi.product_id
             AND p.company_id=:cid
            LEFT JOIN warehouse_stocks ws
              ON ws.product_id=p.id
             AND ws.company_id=p.company_id
             AND ws.warehouse_id=:warehouse_id
            WHERE o.company_id=:cid
              AND o.order_date>=:cutoff
              AND o.order_date<=:today
              AND COALESCE(o.status,'completed') NOT IN ('cancelled','draft')
              AND COALESCE(p.active,TRUE)=TRUE
              {customer_filter}
            GROUP BY p.id,p.name,p.product_code,p.sale_price,ws.quantity
            ORDER BY times_sold DESC,total_quantity DESC,p.id ASC
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()

    return [
        {
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "product_code": row["product_code"],
            "unit_price": money(row["unit_price"]),
            "current_stock": quantity(row["current_stock"]),
            "times_sold": int(row["times_sold"]),
            "total_quantity": quantity(row["total_quantity"]),
        }
        for row in rows
    ]
