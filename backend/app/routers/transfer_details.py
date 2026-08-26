from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import has_permission
from ..db import get_db
from ..tenancy import company_id

router = APIRouter(prefix="/warehouse-transfers", tags=["Depo Transferleri"])


def _require_stock_permission(request: Request) -> None:
    """Enforce stock authorization for transfer document reads."""
    user = getattr(request.state, "user", None)
    role = str(user.get("role") or "") if isinstance(user, dict) else ""
    if not has_permission(role, "stock"):
        raise HTTPException(403, "Depo transferini görüntülemek için stok yetkiniz yok")


@router.get("/{transfer_id}")
def transfer_detail(
    transfer_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return a tenant-scoped transfer document with products and audit movements."""
    _require_stock_permission(request)
    cid = company_id(request)
    header = db.execute(
        text(
            """SELECT t.id,t.transfer_date,t.note,t.status,t.created_by,t.created_at,
            s.id source_warehouse_id,s.name source_name,
            d.id target_warehouse_id,d.name target_name,
            COUNT(i.id) item_count,COALESCE(SUM(i.quantity),0) total_quantity
            FROM stock_transfers t
            JOIN warehouses s ON s.id=t.source_warehouse_id AND s.company_id=t.company_id
            JOIN warehouses d ON d.id=t.target_warehouse_id AND d.company_id=t.company_id
            LEFT JOIN stock_transfer_items i ON i.transfer_id=t.id AND i.company_id=t.company_id
            WHERE t.id=:tid AND t.company_id=:cid
            GROUP BY t.id,t.transfer_date,t.note,t.status,t.created_by,t.created_at,
            s.id,s.name,d.id,d.name"""
        ),
        {"tid": transfer_id, "cid": cid},
    ).mappings().first()
    if not header:
        raise HTTPException(404, "Depo transferi bulunamadı")

    items = db.execute(
        text(
            """SELECT i.id,i.product_id,p.name product_name,p.product_code,p.unit,i.quantity
            FROM stock_transfer_items i
            JOIN stock_transfers t ON t.id=i.transfer_id AND t.company_id=:cid
            JOIN products p ON p.id=i.product_id AND p.company_id=t.company_id
            WHERE i.transfer_id=:tid AND i.company_id=:cid
            ORDER BY i.id"""
        ),
        {"tid": transfer_id, "cid": cid},
    ).mappings().all()

    movements = db.execute(
        text(
            """SELECT sm.id,sm.product_id,p.name product_name,sm.movement_type,
            sm.quantity,sm.movement_date,sm.warehouse_id,w.name warehouse_name,sm.note
            FROM stock_movements sm
            JOIN products p ON p.id=sm.product_id AND p.company_id=sm.company_id
            LEFT JOIN warehouses w ON w.id=sm.warehouse_id AND w.company_id=sm.company_id
            WHERE sm.company_id=:cid AND sm.reference_type='transfer'
              AND sm.reference_id=:tid
            ORDER BY sm.id"""
        ),
        {"tid": transfer_id, "cid": cid},
    ).mappings().all()

    return {
        "transfer": dict(header),
        "items": [dict(row) for row in items],
        "movements": [dict(row) for row in movements],
    }
