from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, TypeVar

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..change_history import record_change
from ..db import get_db
from ..exchange_rates import resolve_rate_to_try, to_try
from ..money import money, quantity
from ..supplier_price_schemas import (
    MAX_LEAD_DAYS,
    MAX_PRICE,
    MAX_QUANTITY,
    SUPPORTED_CURRENCIES,
)
from ..tenancy import company_id
from .imports import _cell, _map, _num, _read_tabular_upload

router = APIRouter(prefix="/supplier-price-lists", tags=["supplier-price-import"])

ALIASES = {
    "barcode": ["Barkod", "Barcode", "EAN", "GTIN"],
    "price": ["Fiyat", "Birim Fiyat", "Price", "Unit Price"],
    "currency": ["Para Birimi", "Döviz", "Currency"],
    "moq": ["MOQ", "Minimum Sipariş", "Minimum Sipariş Miktarı"],
    "lead_time_days": ["Teslim Süresi", "Teslim Süresi Gün", "Lead Time", "Lead Time Days"],
}

# A price-list import runs inside an interactive API request. Keeping a stricter
# business-row cap than the generic import parser prevents an authorized user from
# monopolising API workers with tens of thousands of price/history writes.
MAX_SUPPLIER_PRICE_IMPORT_ROWS = 10_000
QUERY_CHUNK_SIZE = 500  # below SQLite's bind limit; also keeps PG statements small

T = TypeVar("T")


def _chunks(values: list[T], size: int = QUERY_CHUNK_SIZE) -> Iterable[list[T]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _optional_quantity(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    parsed = quantity(_num(value))
    if parsed < 0 or parsed > MAX_QUANTITY:
        raise ValueError("MOQ geçersiz")
    return parsed


def _optional_lead_days(value: Any) -> int | None:
    if value in (None, ""):
        return None
    parsed = _num(value)
    integral = parsed.to_integral_value()
    if parsed != integral or integral < 0 or integral > MAX_LEAD_DAYS:
        raise ValueError("Teslim süresi geçersiz")
    return int(integral)


def _products_by_barcode(db: Session, cid: int, barcodes: list[str]) -> dict[str, list[int]]:
    """Resolve tenant-owned active products in bounded set-based queries."""
    unique_barcodes = list(dict.fromkeys(barcodes))
    result: dict[str, list[int]] = {}
    statement = text(
        """SELECT id,barcode FROM products
        WHERE company_id=:cid AND barcode IN :barcodes
          AND COALESCE(active,TRUE)=TRUE"""
    ).bindparams(bindparam("barcodes", expanding=True))
    for chunk in _chunks(unique_barcodes):
        rows = db.execute(statement, {"cid": cid, "barcodes": chunk}).mappings().all()
        for row in rows:
            result.setdefault(str(row["barcode"]), []).append(int(row["id"]))
    return result


def _existing_prices(
    db: Session,
    cid: int,
    supplier_id: int,
    product_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Load existing supplier prices without an N+1 lookup per imported row."""
    unique_ids = list(dict.fromkeys(product_ids))
    result: dict[int, dict[str, Any]] = {}
    statement = text(
        """SELECT id,product_id,note FROM supplier_product_prices
        WHERE company_id=:cid AND supplier_id=:supplier_id
          AND product_id IN :product_ids"""
    ).bindparams(bindparam("product_ids", expanding=True))
    for chunk in _chunks(unique_ids):
        rows = db.execute(
            statement,
            {"cid": cid, "supplier_id": supplier_id, "product_ids": chunk},
        ).mappings().all()
        for row in rows:
            result[int(row["product_id"])] = dict(row)
    return result


@router.post("/import")
async def import_supplier_price_list(
    request: Request,
    supplier_id: int = Form(..., gt=0),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    supplier = db.execute(
        text("SELECT id FROM suppliers WHERE id=:id AND company_id=:cid"),
        {"id": supplier_id, "cid": cid},
    ).first()
    if not supplier:
        raise HTTPException(404, "Tedarikçi bulunamadı veya firmaya ait değil")

    headers, rows = await _read_tabular_upload(file)
    if len(rows) > MAX_SUPPLIER_PRICE_IMPORT_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"Tedarikçi fiyat listesi en fazla {MAX_SUPPLIER_PRICE_IMPORT_ROWS} veri satırı içerebilir.",
        )
    mapping = _map(headers, ALIASES)
    if "barcode" not in mapping or "price" not in mapping:
        raise HTTPException(400, "Barkod ve Fiyat sütunları zorunludur.")

    candidates: list[dict[str, Any]] = []
    unmatched_barcodes: list[str] = []
    invalid_prices = 0
    invalid_rows: list[dict[str, Any]] = []
    foreign_currencies: set[str] = set()

    for line_no, row in enumerate(rows, start=2):
        barcode = str(_cell(row, mapping, "barcode", "") or "").strip()
        raw_price = _cell(row, mapping, "price")
        if not barcode:
            invalid_rows.append({"row": line_no, "message": "Barkod boş."})
            continue
        if raw_price is None or (
            isinstance(raw_price, str) and not raw_price.strip()
        ):
            invalid_prices += 1
            invalid_rows.append(
                {"row": line_no, "barcode": barcode, "message": "Fiyat boş."}
            )
            continue
        try:
            price = money(_num(raw_price))
            if price < 0 or price > MAX_PRICE:
                raise ValueError("Fiyat geçersiz")
        except ValueError:
            invalid_prices += 1
            invalid_rows.append({"row": line_no, "barcode": barcode, "message": "Fiyat geçersiz."})
            continue

        currency = str(_cell(row, mapping, "currency", "TRY") or "TRY").strip().upper()
        if currency not in SUPPORTED_CURRENCIES:
            raise HTTPException(400, f"{line_no}. satırda para birimi geçersiz (TRY, EUR veya USD olmalı).")
        try:
            moq = _optional_quantity(_cell(row, mapping, "moq"))
            lead_time_days = _optional_lead_days(_cell(row, mapping, "lead_time_days"))
        except ValueError as exc:
            invalid_rows.append({"row": line_no, "barcode": barcode, "message": str(exc)})
            continue

        candidates.append(
            {
                "line_no": line_no,
                "barcode": barcode,
                "price": price,
                "currency": currency,
                "moq": moq,
                "lead_time_days": lead_time_days,
            }
        )

    products = _products_by_barcode(db, cid, [item["barcode"] for item in candidates])

    # A supplier price list should contain one effective row per product. Legacy
    # files sometimes repeat a barcode; preserve the previous last-row-wins
    # behaviour while avoiding duplicate inserts/history writes.
    prepared_by_product: dict[int, dict[str, Any]] = {}
    duplicate_rows = 0
    for item in candidates:
        product_ids = products.get(item["barcode"], [])
        if not product_ids:
            unmatched_barcodes.append(item["barcode"])
            continue
        if len(product_ids) > 1:
            invalid_rows.append(
                {
                    "row": item["line_no"],
                    "barcode": item["barcode"],
                    "message": "Barkod birden fazla ürüne ait.",
                }
            )
            continue
        product_id = product_ids[0]
        if product_id in prepared_by_product:
            duplicate_rows += 1
        item["product_id"] = product_id
        prepared_by_product[product_id] = item
        if item["currency"] != "TRY":
            foreign_currencies.add(item["currency"])

    prepared = list(prepared_by_product.values())
    existing = _existing_prices(db, cid, supplier_id, [item["product_id"] for item in prepared])
    rate_by_currency = {
        currency: resolve_rate_to_try(db, cid, currency)
        for currency in {item["currency"] for item in prepared}
    }

    inserted = updated = 0
    user = getattr(request.state, "user", {}) or {}
    now = utcnow()
    insert_rows: list[dict[str, Any]] = []
    update_rows: list[dict[str, Any]] = []
    history_rows: list[dict[str, Any]] = []

    for item in prepared:
        existing_row = existing.get(item["product_id"])
        common = {
            "cid": cid,
            "supplier_id": supplier_id,
            "product_id": item["product_id"],
            "price": item["price"],
            "currency": item["currency"],
            "moq": item["moq"],
            "lead_time_days": item["lead_time_days"],
            "now": now,
        }
        if existing_row is None:
            insert_rows.append({**common, "created_by": user.get("id")})
            note = None
            inserted += 1
        else:
            update_rows.append({**common, "id": int(existing_row["id"])})
            note = existing_row.get("note")
            updated += 1
        history_rows.append(
            {
                "cid": cid,
                "supplier_id": supplier_id,
                "product_id": item["product_id"],
                "price": item["price"],
                "currency": item["currency"],
                "price_in_try": to_try(item["price"], rate_by_currency[item["currency"]]),
                "note": note,
                "now": now,
            }
        )

    try:
        if insert_rows:
            db.execute(
                text(
                    """INSERT INTO supplier_product_prices(
                    company_id,supplier_id,product_id,price,currency,moq,
                    lead_time_days,is_active,created_by,created_at,updated_at)
                    VALUES(:cid,:supplier_id,:product_id,:price,:currency,:moq,
                    :lead_time_days,TRUE,:created_by,:now,:now)"""
                ),
                insert_rows,
            )
        if update_rows:
            db.execute(
                text(
                    """UPDATE supplier_product_prices
                    SET price=:price,currency=:currency,moq=:moq,
                        lead_time_days=:lead_time_days,is_active=TRUE,updated_at=:now
                    WHERE id=:id AND company_id=:cid"""
                ),
                update_rows,
            )
        if history_rows:
            db.execute(
                text(
                    """INSERT INTO supplier_price_history(
                    company_id,supplier_id,product_id,price,currency,price_in_try,source,note,captured_at)
                    VALUES(:cid,:supplier_id,:product_id,:price,:currency,:price_in_try,'MANUAL',:note,:now)"""
                ),
                history_rows,
            )

        audit_summary = {
            "file_name": (file.filename or "")[:255],
            "processed_rows": len(rows),
            "matched": len(prepared),
            "inserted": inserted,
            "updated": updated,
            "unmatched_count": len(unmatched_barcodes),
            "invalid_row_count": len(invalid_rows),
            "duplicate_product_rows": duplicate_rows,
        }
        record_change(
            db,
            request,
            company_id=cid,
            entity_type="supplier_price_import",
            entity_id=supplier_id,
            action="import",
            before=None,
            after=audit_summary,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409,
            "Fiyat listesi eşzamanlı değiştirildi. Güncel veriyi alıp tekrar deneyin.",
        ) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(400, "Tedarikçi fiyat listesi içe aktarılamadı.") from exc

    return {
        "processed_rows": len(rows),
        "matched": len(prepared),
        "inserted": inserted,
        "updated": updated,
        "duplicate_product_rows": duplicate_rows,
        "unmatched_count": len(unmatched_barcodes),
        "unmatched_barcodes": unmatched_barcodes,
        "invalid_price_count": invalid_prices,
        "invalid_rows": invalid_rows,
        "foreign_currency_rows": sum(1 for item in prepared if item["currency"] != "TRY"),
        "currencies_requiring_rate": sorted(foreign_currencies),
    }
