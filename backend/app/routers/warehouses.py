from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import insert, select, text, update
from sqlalchemy.orm import Session

from ..activity_log import log_request_activity
from ..auth import utcnow
from ..db import get_db
from ..inventory import (
    adjust_warehouse_stock,
    default_warehouse,
    stock_transfer_items,
    stock_transfers,
    warehouse_stocks,
    warehouses,
)
from ..schemas import CriticalStockUpdate, StockTransferCreate, WarehouseCreate
from ..tenancy import branches, company_id

router = APIRouter(prefix="/warehouses", tags=["Depolar"])
logger = logging.getLogger("yerel_hesap")
WAREHOUSE_FAILED_MESSAGE = "İşlem tamamlanamadı. Lütfen tekrar deneyin."


@router.get("")
def list_warehouses(request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    rows = db.execute(
        text(
            """SELECT w.id,w.name,w.code,w.branch_id,b.name branch_name,
            w.is_default,w.is_active,COUNT(ws.product_id) product_count,
            COALESCE(SUM(ws.quantity),0) total_quantity
            FROM warehouses w
            LEFT JOIN branches b ON b.id=w.branch_id AND b.company_id=w.company_id
            LEFT JOIN warehouse_stocks ws ON ws.warehouse_id=w.id
                AND ws.company_id=w.company_id
            WHERE w.company_id=:cid AND w.is_active=TRUE
            GROUP BY w.id,b.name ORDER BY w.is_default DESC,w.name"""
        ),
        {"cid": cid},
    ).mappings().all()
    return [dict(row) for row in rows]


@router.post("", status_code=201)
def create_warehouse(
    payload: WarehouseCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    if payload.branch_id and not db.execute(
        select(branches.c.id).where(
            branches.c.id == payload.branch_id,
            branches.c.company_id == cid,
        )
    ).first():
        raise HTTPException(400, "Şube bulunamadı")
    if payload.is_default:
        db.execute(
            update(warehouses).where(warehouses.c.company_id == cid).values(is_default=False)
        )
    result = db.execute(
        insert(warehouses).values(
            company_id=cid,
            branch_id=payload.branch_id,
            name=payload.name.strip(),
            code=payload.code,
            is_default=payload.is_default,
            is_active=True,
            created_at=utcnow(),
        )
    )
    warehouse_id = result.inserted_primary_key[0]
    products = db.execute(
        text("SELECT id FROM products WHERE company_id=:cid"), {"cid": cid}
    ).all()
    for (product_id,) in products:
        db.execute(
            insert(warehouse_stocks).values(
                company_id=cid,
                warehouse_id=warehouse_id,
                product_id=product_id,
                quantity=0,
                critical_stock=0,
            )
        )
    db.commit()
    return {"id": warehouse_id}


@router.get("/stock")
def warehouse_stock(
    request: Request,
    warehouse_id: int | None = None,
    q: str = "",
    critical_only: bool = False,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    resolved_id = warehouse_id or default_warehouse(db, cid)
    if not db.execute(
        select(warehouses.c.id).where(
            warehouses.c.id == resolved_id,
            warehouses.c.company_id == cid,
        )
    ).first():
        raise HTTPException(404, "Depo bulunamadı")
    rows = db.execute(
        text(
            """SELECT p.id product_id,p.name,p.product_code,p.barcode,p.unit,
            p.oem_number,p.brand,p.location,
            COALESCE(ws.quantity,0) quantity,
            COALESCE(ws.critical_stock,p.critical_stock,0) critical_stock,
            CASE WHEN COALESCE(ws.quantity,0)<=COALESCE(ws.critical_stock,p.critical_stock,0)
                THEN 1 ELSE 0 END is_critical
            FROM products p
            LEFT JOIN warehouse_stocks ws ON ws.product_id=p.id
                AND ws.warehouse_id=:wid AND ws.company_id=p.company_id
            WHERE p.company_id=:cid AND (
                LOWER(p.name) LIKE LOWER(:q)
                OR COALESCE(p.product_code,'') LIKE :q
                OR COALESCE(p.barcode,'') LIKE :q
                OR LOWER(COALESCE(p.oem_number,'')) LIKE LOWER(:q)
                OR LOWER(COALESCE(p.location,'')) LIKE LOWER(:q)
            ) AND (
                :critical_only=0 OR COALESCE(ws.quantity,0)<=COALESCE(ws.critical_stock,p.critical_stock,0)
            ) ORDER BY is_critical DESC,LOWER(p.name)"""
        ),
        {"wid": resolved_id, "cid": cid, "q": f"%{q}%", "critical_only": 1 if critical_only else 0},
    ).mappings().all()
    return [dict(row) for row in rows]


@router.get("/replenishment")
def replenishment(request: Request, db: Session = Depends(get_db)):
    """Cross-warehouse worklist of products that need action (read-only).

    Classifies each stocked product+warehouse line as negative / zero / critical
    and attaches the last purchase (supplier, date, price) from the most recent
    purchase for that product. Supplier identity is a stable id from the join;
    it is null when the product was never purchased. Tenant-scoped throughout.
    """
    cid = company_id(request)
    rows = db.execute(
        text(
            """SELECT ws.product_id,p.name product_name,p.product_code,p.unit,
            ws.warehouse_id,w.name warehouse_name,
            ws.quantity,COALESCE(ws.critical_stock,0) critical_stock,
            lp.supplier_id last_supplier_id,lp.supplier_name last_supplier_name,
            lp.purchase_date last_purchase_date,lp.unit_price last_purchase_price
            FROM warehouse_stocks ws
            JOIN products p ON p.id=ws.product_id AND p.company_id=ws.company_id
            JOIN warehouses w ON w.id=ws.warehouse_id AND w.company_id=ws.company_id
            LEFT JOIN (
                SELECT pi.product_id,s.id supplier_id,s.name supplier_name,
                pu.purchase_date,pi.unit_price,
                ROW_NUMBER() OVER (
                    PARTITION BY pi.product_id
                    ORDER BY pu.purchase_date DESC,pu.id DESC,pi.id DESC
                ) rn
                FROM purchase_items pi
                JOIN purchases pu ON pu.id=pi.purchase_id AND pu.company_id=:cid
                JOIN suppliers s ON s.id=pu.supplier_id AND s.company_id=pu.company_id
                WHERE pi.company_id=:cid
            ) lp ON lp.product_id=ws.product_id AND lp.rn=1
            WHERE ws.company_id=:cid AND w.is_active=TRUE
              AND (ws.quantity<=0 OR (ws.critical_stock>0 AND ws.quantity<=ws.critical_stock))
            ORDER BY
              CASE WHEN ws.quantity<0 THEN 0 WHEN ws.quantity=0 THEN 1 ELSE 2 END,
              w.name,LOWER(p.name)"""
        ),
        {"cid": cid},
    ).mappings().all()
    return [dict(row) for row in rows]


@router.post("/critical-stock")
def set_critical(
    payload: CriticalStockUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    if not db.execute(
        select(warehouses.c.id).where(
            warehouses.c.id == payload.warehouse_id,
            warehouses.c.company_id == cid,
            warehouses.c.is_active == True,
        )
    ).first():
        raise HTTPException(404, "Depo bulunamadı")
    if not db.execute(
        text("SELECT id FROM products WHERE id=:id AND company_id=:cid"),
        {"id": payload.product_id, "cid": cid},
    ).first():
        raise HTTPException(404, "Ürün bulunamadı")
    row = db.execute(
        select(warehouse_stocks.c.product_id).where(
            warehouse_stocks.c.company_id == cid,
            warehouse_stocks.c.warehouse_id == payload.warehouse_id,
            warehouse_stocks.c.product_id == payload.product_id,
        )
    ).first()
    if row:
        db.execute(
            update(warehouse_stocks)
            .where(
                warehouse_stocks.c.company_id == cid,
                warehouse_stocks.c.warehouse_id == payload.warehouse_id,
                warehouse_stocks.c.product_id == payload.product_id,
            )
            .values(critical_stock=payload.critical_stock)
        )
    else:
        db.execute(
            insert(warehouse_stocks).values(
                company_id=cid,
                warehouse_id=payload.warehouse_id,
                product_id=payload.product_id,
                quantity=0,
                critical_stock=payload.critical_stock,
            )
        )
    db.commit()
    return {"ok": True}


@router.post("/transfers", status_code=201)
def create_transfer(
    payload: StockTransferCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    if payload.source_warehouse_id == payload.target_warehouse_id:
        raise HTTPException(400, "Kaynak ve hedef depo aynı olamaz")
    valid = db.execute(
        select(warehouses.c.id).where(
            warehouses.c.company_id == cid,
            warehouses.c.id.in_(
                [payload.source_warehouse_id, payload.target_warehouse_id]
            ),
        )
    ).all()
    if len(valid) != 2:
        raise HTTPException(400, "Depo bulunamadı")
    # A transfer is a PHYSICAL move between two shelves, not a sale. The company
    # negative-stock policy (allow / manager_override) exists so a sale can go out
    # ahead of the paperwork; it must NOT apply here, because no override can make
    # a branch hand over parts it does not physically hold. Both legs therefore run
    # with allow_negative=False unconditionally: an insufficient source is ALWAYS
    # 409, whatever the policy says.
    try:
        result = db.execute(
            insert(stock_transfers).values(
                company_id=cid,
                transfer_date=payload.transfer_date,
                source_warehouse_id=payload.source_warehouse_id,
                target_warehouse_id=payload.target_warehouse_id,
                note=payload.note,
                status="completed",
                created_by=request.state.user["id"],
                created_at=utcnow(),
            )
        )
        transfer_id = result.inserted_primary_key[0]
        for item in payload.items:
            if not db.execute(
                text("SELECT id FROM products WHERE id=:id AND company_id=:cid"),
                {"id": item.product_id, "cid": cid},
            ).first():
                raise ValueError("Ürün bulunamadı")
            adjust_warehouse_stock(
                db,
                cid,
                payload.source_warehouse_id,
                item.product_id,
                -item.quantity,
                allow_negative=False,
            )
            adjust_warehouse_stock(
                db,
                cid,
                payload.target_warehouse_id,
                item.product_id,
                item.quantity,
            )
            db.execute(
                insert(stock_transfer_items).values(
                    company_id=cid,
                    transfer_id=transfer_id,
                    product_id=item.product_id,
                    quantity=item.quantity,
                )
            )
            for warehouse_id, amount, label in (
                (payload.source_warehouse_id, -item.quantity, "transfer_out"),
                (payload.target_warehouse_id, item.quantity, "transfer_in"),
            ):
                db.execute(
                    text(
                        """INSERT INTO stock_movements(
                        product_id,movement_type,quantity,movement_date,reference_type,
                        reference_id,note,company_id,warehouse_id
                        ) VALUES(
                        :pid,:mt,:q,:d,'transfer',:tid,:note,:cid,:wid)"""
                    ),
                    {
                        "pid": item.product_id,
                        "mt": label,
                        "q": amount,
                        "d": payload.transfer_date,
                        "tid": transfer_id,
                        "note": payload.note or f"Depo transferi #{transfer_id}",
                        "cid": cid,
                        "wid": warehouse_id,
                    },
                )
        log_request_activity(
            db, request, cid, "stock.transfer", "stock", int(transfer_id),
            (
                f"#{transfer_id} numaralı depo transferini oluşturdu — "
                f"{len(payload.items)} kalem, "
                f"depo #{payload.source_warehouse_id} → #{payload.target_warehouse_id}"
            ),
            {
                "transfer_id": int(transfer_id),
                "source_warehouse_id": payload.source_warehouse_id,
                "target_warehouse_id": payload.target_warehouse_id,
                "transfer_date": payload.transfer_date,
                "items": [
                    {"product_id": item.product_id, "quantity": item.quantity}
                    for item in payload.items
                ],
            },
        )
        db.commit()
        # ``policy_overrides`` stays in the payload for response-shape
        # compatibility, but a transfer can never consume a negative-stock
        # override, so it is always empty.
        return {"id": transfer_id, "policy_overrides": []}
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        if "yetersiz stok" in str(exc).lower():
            # Deliberately NOT stock_policy_message(): this refusal is physical,
            # not a policy decision, so the message must not invite a manager
            # override (the frontend opens its override form on the phrase
            # "Yönetici onayıyla", which would loop the user through 409s).
            raise HTTPException(
                409,
                "Kaynak depoda yeterli stok yok; transfer edilemez. "
                f"({exc})",
            ) from exc
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Depo işlemi beklenmeyen hata")
        raise HTTPException(400, WAREHOUSE_FAILED_MESSAGE) from exc


@router.get("/transfers")
def list_transfers(request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    rows = db.execute(
        text(
            """SELECT t.id,t.transfer_date,s.name source_name,d.name target_name,
            t.note,t.status,COUNT(i.id) item_count,
            COALESCE(SUM(i.quantity),0) total_quantity
            FROM stock_transfers t
            JOIN warehouses s ON s.id=t.source_warehouse_id AND s.company_id=t.company_id
            JOIN warehouses d ON d.id=t.target_warehouse_id AND d.company_id=t.company_id
            LEFT JOIN stock_transfer_items i ON i.transfer_id=t.id AND i.company_id=t.company_id
            WHERE t.company_id=:cid GROUP BY t.id,s.name,d.name ORDER BY t.id DESC"""
        ),
        {"cid": cid},
    ).mappings().all()
    return [dict(row) for row in rows]


# Defined LAST so the literal /warehouses/* routes above are matched first;
# otherwise a request to e.g. /warehouses/stock would try to parse "stock" as int.
@router.get("/{warehouse_id}")
def warehouse_detail(warehouse_id: int, request: Request, db: Session = Depends(get_db)):
    """Warehouse detail: header stats + the products stocked in this warehouse.

    Read-only and tenant-scoped. Rows carry a stable product_id for drill-down and
    the most recent movement date for this product in this warehouse. Stock
    condition is left to the client to classify from quantity + critical_stock so
    the SQL stays dialect-neutral.
    """
    cid = company_id(request)
    header = db.execute(
        text(
            """SELECT w.id,w.name,w.code,w.branch_id,b.name branch_name,
            w.is_default,w.is_active
            FROM warehouses w
            LEFT JOIN branches b ON b.id=w.branch_id AND b.company_id=w.company_id
            WHERE w.id=:wid AND w.company_id=:cid"""
        ),
        {"wid": warehouse_id, "cid": cid},
    ).mappings().first()
    if not header:
        raise HTTPException(404, "Depo bulunamadı")
    stats = db.execute(
        text(
            """SELECT COUNT(*) product_count,
            COALESCE(SUM(CASE WHEN ws.quantity<0 THEN 1 ELSE 0 END),0) negative_count,
            COALESCE(SUM(CASE WHEN ws.quantity=0 THEN 1 ELSE 0 END),0) zero_count,
            COALESCE(SUM(CASE WHEN ws.quantity>0 AND ws.critical_stock>0
                AND ws.quantity<=ws.critical_stock THEN 1 ELSE 0 END),0) critical_count,
            COALESCE(SUM(ws.quantity),0) total_quantity
            FROM warehouse_stocks ws
            WHERE ws.warehouse_id=:wid AND ws.company_id=:cid"""
        ),
        {"wid": warehouse_id, "cid": cid},
    ).mappings().one()
    products = db.execute(
        text(
            """SELECT ws.product_id,p.name,p.product_code,p.barcode,p.unit,
            p.oem_number,p.brand,p.location,
            ws.quantity,COALESCE(ws.critical_stock,0) critical_stock,
            (SELECT MAX(sm.movement_date) FROM stock_movements sm
             WHERE sm.product_id=ws.product_id AND sm.warehouse_id=:wid
               AND sm.company_id=:cid) last_movement_date
            FROM warehouse_stocks ws
            JOIN products p ON p.id=ws.product_id AND p.company_id=ws.company_id
            WHERE ws.warehouse_id=:wid AND ws.company_id=:cid
            ORDER BY
              CASE WHEN ws.quantity<0 THEN 0 WHEN ws.quantity=0 THEN 1
                   WHEN ws.critical_stock>0 AND ws.quantity<=ws.critical_stock THEN 2
                   ELSE 3 END,
              LOWER(p.name)"""
        ),
        {"wid": warehouse_id, "cid": cid},
    ).mappings().all()
    return {
        "warehouse": dict(header),
        "stats": dict(stats),
        "products": [dict(row) for row in products],
    }
