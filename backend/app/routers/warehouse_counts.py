from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import insert, select, text, update
from sqlalchemy.orm import Session

from ..core_schema import products, stock_movements
from ..db import get_db
from ..inventory import adjust_warehouse_stock, warehouse_stocks, warehouses
from ..money import quantity
from ..tenancy import company_id

router = APIRouter(prefix="/warehouses/counts", tags=["Depo Sayımları"])
_COUNT_NOTE = re.compile(
    r"^Fiziksel sayım \| Sistem: (?P<system>-?[0-9.]+) \| Sayılan: "
    r"(?P<counted>-?[0-9.]+)(?: \| Not: (?P<note>.*))?$"
)


class InventoryCountItem(BaseModel):
    product_id: int = Field(gt=0)
    counted_quantity: Decimal = Field(ge=0)
    critical_stock: Decimal | None = Field(default=None, ge=0)


class InventoryCountCreate(BaseModel):
    warehouse_id: int = Field(gt=0)
    count_date: str
    note: str | None = Field(default=None, max_length=500)
    items: list[InventoryCountItem] = Field(min_length=1, max_length=500)

    @field_validator("count_date")
    @classmethod
    def validate_count_date(cls, value: str) -> str:
        return _iso_date(value, "Sayım tarihi")

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        return value or None

    @model_validator(mode="after")
    def unique_products(self):
        ids = [item.product_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Aynı ürün sayım belgesinde birden fazla satırda bulunamaz")
        return self


def _iso_date(value: str, field_name: str) -> str:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"{field_name} YYYY-AA-GG veya GG.AA.YYYY biçiminde olmalıdır")


def _optional_iso_date(value: str | None, field_name: str) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        return _iso_date(value, field_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _plain_decimal(value: Decimal) -> str:
    text_value = format(quantity(value), "f")
    return text_value.rstrip("0").rstrip(".") if "." in text_value else text_value


def _movement_note(system_quantity: Decimal, counted_quantity: Decimal, note: str | None) -> str:
    value = (
        f"Fiziksel sayım | Sistem: {_plain_decimal(system_quantity)} | "
        f"Sayılan: {_plain_decimal(counted_quantity)}"
    )
    return f"{value} | Not: {note}" if note else value


def _parse_note(value: str | None) -> tuple[Decimal | None, Decimal | None, str | None]:
    match = _COUNT_NOTE.match(value or "")
    if not match:
        return None, None, value
    return (
        quantity(match.group("system")),
        quantity(match.group("counted")),
        match.group("note") or None,
    )


@router.post("", status_code=201)
def create_count(
    payload: InventoryCountCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Apply a multi-line physical count atomically and group its audit movements."""
    cid = company_id(request)
    warehouse = db.execute(
        select(warehouses.c.id, warehouses.c.name).where(
            warehouses.c.id == payload.warehouse_id,
            warehouses.c.company_id == cid,
            warehouses.c.is_active.is_(True),
        )
    ).mappings().first()
    if not warehouse:
        raise HTTPException(404, "Depo bulunamadı")

    product_ids = [item.product_id for item in payload.items]
    product_rows = db.execute(
        select(products.c.id, products.c.name, products.c.unit).where(
            products.c.company_id == cid,
            products.c.id.in_(product_ids),
        )
    ).mappings().all()
    product_map = {int(row["id"]): row for row in product_rows}
    missing = [product_id for product_id in product_ids if product_id not in product_map]
    if missing:
        raise HTTPException(400, "Sayım satırlarından en az biri firmaya ait değil")

    # A count is an absolute observation, not an additive adjustment. Lock the
    # selected rows on PostgreSQL so a concurrent sale/transfer cannot change a
    # quantity after we read it but before the counted target is applied. Ordering
    # locks by product id also keeps overlapping count sessions deadlock-resistant.
    stock_query = (
        select(
            warehouse_stocks.c.product_id,
            warehouse_stocks.c.quantity,
            warehouse_stocks.c.critical_stock,
        )
        .where(
            warehouse_stocks.c.company_id == cid,
            warehouse_stocks.c.warehouse_id == payload.warehouse_id,
            warehouse_stocks.c.product_id.in_(product_ids),
        )
        .order_by(warehouse_stocks.c.product_id)
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        stock_query = stock_query.with_for_update()
    stock_rows = db.execute(stock_query).mappings().all()
    stock_map = {int(row["product_id"]): row for row in stock_rows}

    count_id: int | None = None
    changed_count = 0
    increase_count = 0
    decrease_count = 0
    total_absolute_variance = Decimal("0")
    results: list[dict] = []

    try:
        for item in payload.items:
            existing = stock_map.get(item.product_id)
            system_quantity = quantity(existing["quantity"] if existing else 0)
            counted_quantity = quantity(item.counted_quantity)
            difference = counted_quantity - system_quantity

            if difference:
                adjust_warehouse_stock(
                    db,
                    cid,
                    payload.warehouse_id,
                    item.product_id,
                    difference,
                    allow_negative=False,
                )
                changed_count += 1
                increase_count += 1 if difference > 0 else 0
                decrease_count += 1 if difference < 0 else 0
                total_absolute_variance += abs(difference)
            elif not existing:
                db.execute(
                    insert(warehouse_stocks).values(
                        company_id=cid,
                        warehouse_id=payload.warehouse_id,
                        product_id=item.product_id,
                        quantity=0,
                        critical_stock=item.critical_stock or 0,
                    )
                )

            if item.critical_stock is not None:
                db.execute(
                    update(warehouse_stocks)
                    .where(
                        warehouse_stocks.c.company_id == cid,
                        warehouse_stocks.c.warehouse_id == payload.warehouse_id,
                        warehouse_stocks.c.product_id == item.product_id,
                    )
                    .values(critical_stock=quantity(item.critical_stock))
                )

            movement_id = int(
                db.execute(
                    insert(stock_movements)
                    .values(
                        product_id=item.product_id,
                        movement_type="set",
                        quantity=difference,
                        movement_date=payload.count_date,
                        reference_type="inventory_count",
                        reference_id=count_id,
                        note=_movement_note(system_quantity, counted_quantity, payload.note),
                        company_id=cid,
                        warehouse_id=payload.warehouse_id,
                    )
                    .returning(stock_movements.c.id)
                ).scalar_one()
            )
            if count_id is None:
                count_id = movement_id
                db.execute(
                    update(stock_movements)
                    .where(
                        stock_movements.c.id == movement_id,
                        stock_movements.c.company_id == cid,
                    )
                    .values(reference_id=count_id)
                )

            product = product_map[item.product_id]
            results.append(
                {
                    "product_id": item.product_id,
                    "product_name": product["name"],
                    "unit": product["unit"],
                    "system_quantity": system_quantity,
                    "counted_quantity": counted_quantity,
                    "variance": difference,
                    "critical_stock": item.critical_stock
                    if item.critical_stock is not None
                    else (existing["critical_stock"] if existing else 0),
                }
            )

        db.commit()
        return {
            "id": count_id,
            "warehouse_id": payload.warehouse_id,
            "warehouse_name": warehouse["name"],
            "count_date": payload.count_date,
            "note": payload.note,
            "item_count": len(payload.items),
            "changed_count": changed_count,
            "unchanged_count": len(payload.items) - changed_count,
            "increase_count": increase_count,
            "decrease_count": decrease_count,
            "total_absolute_variance": total_absolute_variance,
            "items": results,
        }
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, "Stok sayımı kaydedilemedi") from exc


@router.get("")
def list_counts(
    request: Request,
    warehouse_id: int | None = Query(default=None, gt=0),
    date_from: str | None = None,
    date_to: str | None = None,
    changed_only: bool = False,
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    normalized_from = _optional_iso_date(date_from, "Başlangıç tarihi")
    normalized_to = _optional_iso_date(date_to, "Bitiş tarihi")
    if normalized_from and normalized_to and normalized_from > normalized_to:
        raise HTTPException(422, "Başlangıç tarihi bitiş tarihinden sonra olamaz")

    if warehouse_id and not db.execute(
        select(warehouses.c.id).where(
            warehouses.c.id == warehouse_id,
            warehouses.c.company_id == cid,
            warehouses.c.is_active.is_(True),
        )
    ).first():
        raise HTTPException(404, "Depo bulunamadı")

    filters = [
        "sm.company_id=:cid",
        "sm.reference_type='inventory_count'",
        "sm.reference_id IS NOT NULL",
    ]
    params: dict[str, object] = {"cid": cid, "limit": limit}
    if warehouse_id:
        filters.append("sm.warehouse_id=:warehouse_id")
        params["warehouse_id"] = warehouse_id
    if normalized_from:
        filters.append("sm.movement_date>=:date_from")
        params["date_from"] = normalized_from
    if normalized_to:
        filters.append("sm.movement_date<=:date_to")
        params["date_to"] = normalized_to

    having = "HAVING SUM(CASE WHEN sm.quantity<>0 THEN 1 ELSE 0 END)>0" if changed_only else ""
    rows = db.execute(
        text(
            f"""SELECT sm.reference_id id,sm.movement_date count_date,
            sm.warehouse_id,w.name warehouse_name,COUNT(*) item_count,
            SUM(CASE WHEN sm.quantity<>0 THEN 1 ELSE 0 END) changed_count,
            SUM(CASE WHEN sm.quantity=0 THEN 1 ELSE 0 END) unchanged_count,
            SUM(CASE WHEN sm.quantity>0 THEN 1 ELSE 0 END) increase_count,
            SUM(CASE WHEN sm.quantity<0 THEN 1 ELSE 0 END) decrease_count,
            COALESCE(SUM(ABS(sm.quantity)),0) total_absolute_variance,
            MAX(sm.id) last_movement_id
            FROM stock_movements sm
            JOIN warehouses w ON w.id=sm.warehouse_id AND w.company_id=sm.company_id
            WHERE {' AND '.join(filters)}
            GROUP BY sm.reference_id,sm.movement_date,sm.warehouse_id,w.name
            {having}
            ORDER BY last_movement_id DESC LIMIT :limit"""
        ),
        params,
    ).mappings().all()
    return [dict(row) for row in rows]


@router.get("/{count_id}")
def count_detail(count_id: int, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    rows = db.execute(
        text(
            """SELECT sm.id,sm.reference_id,sm.movement_date count_date,
            sm.warehouse_id,w.name warehouse_name,sm.product_id,p.name product_name,
            p.product_code,p.unit,sm.quantity variance,sm.note,
            ws.quantity current_quantity,ws.critical_stock
            FROM stock_movements sm
            JOIN warehouses w ON w.id=sm.warehouse_id AND w.company_id=sm.company_id
            JOIN products p ON p.id=sm.product_id AND p.company_id=sm.company_id
            LEFT JOIN warehouse_stocks ws ON ws.company_id=sm.company_id
              AND ws.warehouse_id=sm.warehouse_id AND ws.product_id=sm.product_id
            WHERE sm.company_id=:cid AND sm.reference_type='inventory_count'
              AND sm.reference_id=:count_id
            ORDER BY sm.id"""
        ),
        {"cid": cid, "count_id": count_id},
    ).mappings().all()
    if not rows:
        raise HTTPException(404, "Stok sayımı bulunamadı")

    items = []
    user_note: str | None = None
    for row in rows:
        system_quantity, counted_quantity, note = _parse_note(row["note"])
        if user_note is None:
            user_note = note
        item = dict(row)
        item.pop("note", None)
        item["system_quantity"] = system_quantity
        item["counted_quantity"] = counted_quantity
        items.append(item)

    first = rows[0]
    return {
        "count": {
            "id": count_id,
            "count_date": first["count_date"],
            "warehouse_id": first["warehouse_id"],
            "warehouse_name": first["warehouse_name"],
            "note": user_note,
            "item_count": len(items),
            "changed_count": sum(1 for item in items if quantity(item["variance"]) != 0),
            "total_absolute_variance": sum(
                (abs(quantity(item["variance"])) for item in items), Decimal("0")
            ),
        },
        "items": items,
    }
