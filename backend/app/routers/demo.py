from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from fastapi import Depends

router = APIRouter(tags=["demo"])


@router.get("/demo/summary")
def demo_summary(request: Request, db: Session = Depends(get_db)):
    if not settings.demo_mode:
        return {"enabled": False}
    cid = int(request.state.company_id)
    tables = {
        "customers": "customers",
        "suppliers": "suppliers",
        "products": "products",
        "sales": "orders",
        "purchases": "purchases",
        "payments": "payments",
        "stock_movements": "stock_movements",
        "finance_transactions": "finance_transactions",
    }
    counts = {}
    for key, table in tables.items():
        counts[key] = int(db.execute(text(f"SELECT COUNT(*) FROM {table} WHERE company_id=:cid"), {"cid": cid}).scalar() or 0)
    return {
        "enabled": True,
        "label": "Dolu Deneme Sürümü",
        "counts": counts,
        "reset_hint": "Demo verisini sıfırlamak için DEMO_SURUMU_KUR.bat dosyasını yeniden çalıştırın.",
    }
