import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import bindparam, insert, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..company_policies import (
    PolicyOverrideContext,
    override_context,
    policy_override_logs,
)
from ..activity_log import format_money_tr, log_request_activity
from ..business_time import business_today
from ..config import settings
from ..db import get_db
from ..money import ZERO_MONEY, money, percentage, quantity
from ..part_search import normalize_part_identifier
from ..pos_contracts import PosLookupProduct, PosSaleResult
from ..schemas import PosSaleCreate, TransactionCreate, TransactionItem
from ..tenancy import company_id
from .search import _normalized_identifier_sql
from .transactions import _save, _totals
from .transactions import _warehouse

router = APIRouter(prefix="/pos", tags=["pos"])

POS_CUSTOMER_NAME = "Perakende Satış"
POS_PRICE_OVERRIDE_REASON = "POS fiyat/iskonto serbest değişikliği"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _idempotency_key(request: Request) -> str:
    key = (request.headers.get("idempotency-key") or "").strip()
    if not key:
        raise HTTPException(400, "POS satışı için Idempotency-Key zorunludur")
    if len(key) > 255:
        raise HTTPException(400, "Idempotency-Key en fazla 255 karakter olabilir")
    return key


def _request_fingerprint(payload: PosSaleCreate) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _record_pos_price_override(
    db: Session, cid: int, override: PolicyOverrideContext, order_id: int
) -> None:
    """Audit a freely negotiated Increment 1 POS price/discount.

    This is deliberately an audit event rather than a policy override:
    manager approval and a user-entered reason are not required in Increment 1.
    Fast-follow: apply role/limit based approval once that product policy is
    defined.
    """
    db.execute(
        insert(policy_override_logs).values(
            company_id=cid,
            user_id=override.user_id,
            username=override.username,
            policy_name="pos_price_override",
            resource_type="orders",
            resource_id=order_id,
            reason=POS_PRICE_OVERRIDE_REASON,
            request_id=override.request_id,
            created_at=datetime.now(timezone.utc),
        )
    )


def _idempotent_replay(
    db: Session, cid: int, key: str, fingerprint: str
) -> dict | None:
    row = db.execute(
        text(
            "SELECT i.request_fingerprint,o.id,o.customer_id,c.name customer_name,"
            "o.subtotal,o.vat_total,o.grand_total,o.discount_amount,o.final_total,"
            "o.paid_amount FROM pos_idempotency i "
            "JOIN orders o ON o.id=i.order_id AND o.company_id=i.company_id "
            "LEFT JOIN customers c ON c.id=o.customer_id AND c.company_id=o.company_id "
            "WHERE i.company_id=:cid AND i.idempotency_key=:key"
        ),
        {"cid": cid, "key": key},
    ).mappings().first()
    if not row:
        return None
    if row["request_fingerprint"] != fingerprint:
        raise HTTPException(
            409, "Idempotency-Key farklı bir satış gövdesiyle yeniden kullanılamaz"
        )
    final_total = money(row["final_total"])
    if settings.payment_allocation_engine_enabled:
        paid_amount = money(
            db.execute(
                text(
                    "SELECT COALESCE(SUM(amount),0) FROM payments "
                    "WHERE company_id=:cid AND reference_type='order' "
                    "AND reference_id=:order_id"
                ),
                {"cid": cid, "order_id": int(row["id"])},
            ).scalar_one()
        )
    else:
        paid_amount = money(row["paid_amount"])
    return {
        "sale_id": int(row["id"]),
        "customer_id": row["customer_id"],
        "customer_name": row["customer_name"],
        "subtotal": row["subtotal"],
        "vat_total": row["vat_total"],
        "grand_total": row["grand_total"],
        "discount_amount": row["discount_amount"],
        "final_total": final_total,
        "paid_amount": paid_amount,
        "remaining_amount": money(final_total - paid_amount),
    }


def _mapped_retail_customer_id(db: Session, cid: int) -> int | None:
    customer_id = db.execute(
        text(
            "SELECT c.id FROM pos_system_customers p "
            "JOIN customers c ON c.id=p.customer_id AND c.company_id=p.company_id "
            "WHERE p.company_id=:cid"
        ),
        {"cid": cid},
    ).scalar()
    return int(customer_id) if customer_id is not None else None


def _retail_customer_id(db: Session, cid: int) -> int:
    mapped = _mapped_retail_customer_id(db, cid)
    if mapped is not None:
        return mapped

    customer_id = db.execute(
        text(
            "SELECT id FROM customers "
            "WHERE company_id=:cid AND name=:name "
            "AND COALESCE(is_active,TRUE)=TRUE ORDER BY id LIMIT 1"
        ),
        {"cid": cid, "name": POS_CUSTOMER_NAME},
    ).scalar()
    if customer_id is None:
        customer_id = db.execute(
            text(
                "INSERT INTO customers(name,opening_balance,risk_limit,payment_term_days,"
                "notes,is_active,company_id) "
                "VALUES(:name,0,0,0,:notes,TRUE,:cid) RETURNING id"
            ),
            {
                "name": POS_CUSTOMER_NAME,
                "notes": "Hızlı satış işlemleri için sistem carisi",
                "cid": cid,
            },
        ).scalar_one()

    try:
        db.execute(
            text(
                "INSERT INTO pos_system_customers(company_id,customer_id,created_at) "
                "VALUES(:cid,:customer_id,:created_at)"
            ),
            {"cid": cid, "customer_id": int(customer_id), "created_at": _now()},
        )
    except IntegrityError:
        # A concurrent first walk-in sale established the tenant's system cari.
        # Roll back our provisional customer and use the committed winner.
        db.rollback()
        mapped = _mapped_retail_customer_id(db, cid)
        if mapped is not None:
            return mapped
        raise HTTPException(409, "Perakende satış carisi eşzamanlı oluşturulamadı")
    return int(customer_id)


def _resolve_customer(db: Session, cid: int, payload: PosSaleCreate) -> tuple[int, str]:
    """Pick the cari this sale is booked against.

    No ``customer_id`` means a walk-in: the sale lands on the shared
    "Perakende Satış" system cari. A named customer must belong to this company
    and still be active. Credit sales cannot be anonymous.
    """
    if payload.customer_id is None:
        if payload.payment_type == "credit":
            raise HTTPException(400, "Veresiye satış için müşteri seçilmelidir")
        return _retail_customer_id(db, cid), POS_CUSTOMER_NAME

    row = db.execute(
        text(
            "SELECT id,name FROM customers "
            "WHERE id=:id AND company_id=:cid AND COALESCE(is_active,TRUE)=TRUE"
        ),
        {"id": payload.customer_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Müşteri bulunamadı")
    return int(row["id"]), str(row["name"])


@router.get("/lookup", response_model=PosLookupProduct)
def lookup_product(
    request: Request,
    barcode: str = Query(min_length=1, max_length=120),
    input_source: Literal["barcode_scanner"] = "barcode_scanner",
    warehouse_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
):
    del input_source
    cid = company_id(request)
    resolved_warehouse_id = _warehouse(db, cid, warehouse_id)
    normalized_barcode = normalize_part_identifier(barcode)
    if not normalized_barcode:
        raise HTTPException(404, "Barkodla eşleşen ürün bulunamadı")
    barcode_expression = _normalized_identifier_sql(
        "p.barcode", db.get_bind().dialect.name
    )
    rows = db.execute(
        text(
            """SELECT p.id,p.name,p.product_code AS code,
            p.sale_price AS unit_price,COALESCE(ws.quantity,0) stock
            FROM products p
            LEFT JOIN warehouse_stocks ws
              ON ws.product_id=p.id AND ws.company_id=p.company_id
             AND ws.warehouse_id=:warehouse_id
            WHERE p.company_id=:cid AND """
            + barcode_expression
            + """=:barcode
            AND COALESCE(p.active,TRUE)=TRUE ORDER BY p.id"""
        ),
        {
            "cid": cid,
            "barcode": normalized_barcode,
            "warehouse_id": resolved_warehouse_id,
        },
    ).mappings().all()
    if not rows:
        raise HTTPException(404, "Barkodla eşleşen ürün bulunamadı")
    if len(rows) > 1:
        raise HTTPException(
            409,
            detail={
                "code": "AMBIGUOUS_BARCODE",
                "candidate_count": len(rows),
                "message": "Barkod birden fazla aktif ürünle eşleşiyor",
            },
        )
    return {**dict(rows[0]), "warehouse_id": resolved_warehouse_id}


@router.post("/sale", status_code=201, response_model=PosSaleResult)
def create_pos_sale(
    payload: PosSaleCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    idempotency_key = _idempotency_key(request)
    fingerprint = _request_fingerprint(payload)
    replay = _idempotent_replay(db, cid, idempotency_key, fingerprint)
    if replay is not None:
        return replay

    product_ids = [item.product_id for item in payload.items]
    product_rows = db.execute(
        text(
            "SELECT id,vat_rate,sale_price FROM products WHERE company_id=:cid "
            "AND id IN :product_ids AND COALESCE(active,TRUE)=TRUE"
        ).bindparams(bindparam("product_ids", expanding=True)),
        {"cid": cid, "product_ids": product_ids},
    ).mappings().all()
    vat_by_product = {
        int(row["id"]): int(Decimal(str(row["vat_rate"] or 0)))
        for row in product_rows
    }
    price_by_product = {
        int(row["id"]): money(row["sale_price"])
        for row in product_rows
    }
    missing = next(
        (product_id for product_id in product_ids if product_id not in vat_by_product),
        None,
    )
    if missing is not None:
        raise HTTPException(404, "Ürün bulunamadı")

    customer_id, customer_name = _resolve_customer(db, cid, payload)
    override = override_context(request)
    price_or_discount_override = any(
        money(item.unit_price) != price_by_product[item.product_id]
        or percentage(item.discount_percent) > 0
        for item in payload.items
    ) or percentage(payload.discount_percent) > 0 or money(payload.discount_amount or 0) > 0

    transaction_items = [
        TransactionItem(
            product_id=item.product_id,
            quantity=quantity(item.quantity),
            unit_price=(
                money(item.unit_price)
                if price_or_discount_override
                else price_by_product[item.product_id]
            ),
            vat_rate=vat_by_product[item.product_id],
            discount_percent=percentage(item.discount_percent),
        )
        for item in payload.items
    ]
    transaction = TransactionCreate(
        entity_id=customer_id,
        transaction_date=business_today().isoformat(),
        note=payload.note,
        warehouse_id=payload.warehouse_id,
        status="completed",
        payment_method=payload.payment_type,
        paid_amount=ZERO_MONEY,
        discount_percent=percentage(payload.discount_percent),
        discount_amount=payload.discount_amount,
        items=transaction_items,
    )
    totals = _totals(db, transaction, cid)
    final_total = money(totals[-1])
    if payload.payment_type == "credit":
        if payload.paid_amount not in {None, ZERO_MONEY}:
            raise HTTPException(400, "Veresiye satışta alınan tutar ayrı bir ödeme yöntemiyle girilmelidir")
    else:
        requested_paid = final_total if payload.paid_amount is None else money(payload.paid_amount)
        if requested_paid > final_total:
            raise HTTPException(400, "Alınan tutar satış toplamını aşamaz")
        retail_customer_id = _mapped_retail_customer_id(db, cid)
        if requested_paid < final_total and (
            payload.customer_id is None or customer_id == retail_customer_id
        ):
            raise HTTPException(400, "Eksik ödeme için müşteri seçin; kalan tutar bakiyeye yazılacaktır")
        transaction.paid_amount = requested_paid

    result = _save(
        "sale",
        transaction,
        db,
        cid,
        override,
        commit=False,
    )
    if price_or_discount_override:
        _record_pos_price_override(db, cid, override, int(result["id"]))

    try:
        db.execute(
            text(
                "INSERT INTO pos_idempotency("
                "company_id,idempotency_key,request_fingerprint,order_id,created_at"
                ") VALUES(:cid,:key,:fingerprint,:order_id,:created_at)"
            ),
            {
                "cid": cid,
                "key": idempotency_key,
                "fingerprint": fingerprint,
                "order_id": int(result["id"]),
                "created_at": _now(),
            },
        )
    except IntegrityError:
        # The PK is the concurrency boundary. Discard this sale, stock and
        # audit work, then replay the committed winner (or return 409 for a
        # different request body).
        db.rollback()
        replay = _idempotent_replay(db, cid, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        raise HTTPException(409, "Eşzamanlı POS isteği çakıştı; tekrar deneyin")

    # Aktivite paneli kaydı — fişle AYNI transaction içinde, commit'ten hemen
    # önce. Idempotent tekrar oynatma yukarıda erken döndüğü için burada
    # çalışmaz (tekrar oynatma yeni bir olay değildir); satış herhangi bir
    # nedenle düşerse (stok, çakışma, doğrulama) buraya hiç gelinmez.
    log_request_activity(
        db,
        request,
        cid,
        "pos.sale_created",
        "sale",
        int(result["id"]),
        (
            f"POS satışı yaptı — {result['document_no']}, "
            f"{format_money_tr(final_total)}, {len(transaction_items)} kalem"
        ),
        {
            "document_no": result["document_no"],
            "item_count": len(transaction_items),
            "final_total": final_total,
            "payment_type": payload.payment_type,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "price_or_discount_override": price_or_discount_override,
        },
    )
    db.commit()
    return {
        "sale_id": result["id"],
        "customer_id": customer_id,
        "customer_name": customer_name,
        "subtotal": result["subtotal"],
        "vat_total": result["vat_total"],
        "grand_total": result["grand_total"],
        "discount_amount": result["discount_amount"],
        "final_total": result["final_total"],
        "paid_amount": result["paid_amount"],
        "remaining_amount": result["remaining_amount"],
    }
