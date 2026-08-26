"""V3 Purchase Comparison Engine — Increment A.

Per-supplier price comparison for a product, combining:
  * auto prices derived from the company's own purchase history
    (last unit price + date, average, and count per supplier), and
  * manual reference prices entered by purchasing (any of TRY/EUR/USD).

Every offer is normalised to TRY (override -> global TCMB -> TRY=1) so suppliers
can be ranked cheapest -> priciest with a single best-offer flag. All amounts are
Decimal end to end and serialised as strings, never float. Everything is
tenant-scoped by company_id.

Increment B adds quantity-aware decision support: a discount ladder resolved at
the evaluated order quantity, per-record VAT handling, VAT-exclusive gross
margin, lead time / ETA and an explicit lead-time filter. Increment C adds the
reorder engine: net 90-day demand (sales minus sale returns), open-order aware
triggering, and supplier-grouped DRAFT purchase orders.

Nothing here can send or approve an order. Both write paths hardcode the ``draft``
status, so a human always approves through the normal purchase workflow.
"""

from __future__ import annotations

import hashlib
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..business_time import business_today
from ..change_history import record_change
from ..company_policies import override_context
from ..db import get_db
from ..exchange_rates import (
    NORMALIZED_TRY_QUANTUM,
    ONE,
    resolve_rate_to_try,
    resolve_rates,
    serialize_rate,
    to_try,
)
from ..money import HUNDRED, ZERO, decimal_value, money, percentage, quantity as quantize_quantity
from ..schemas import TransactionCreate, TransactionItem
from ..supplier_price_schemas import (
    ExchangeRateOverrideWrite,
    PurchaseOrderCreate,
    ReorderDraftCreate,
    ReorderPolicyWrite,
    SupplierPriceCreate,
    SupplierPriceFields,
    SupplierPriceHistoryResponse,
    SupplierPriceUpdate,
)
from ..tenancy import company_id

router = APIRouter(tags=["supplier-prices"])


def _s(value: Decimal | None) -> str | None:
    """Serialise a Decimal amount as a string (no float ever reaches JSON)."""
    return None if value is None else str(value)


def _require_product(db: Session, cid: int, product_id: int) -> str:
    row = db.execute(
        text("SELECT name FROM products WHERE id=:id AND company_id=:cid"),
        {"id": product_id, "cid": cid},
    ).first()
    if not row:
        raise HTTPException(404, "Ürün bulunamadı")
    return str(row[0])


def _require_supplier(db: Session, cid: int, supplier_id: int) -> None:
    row = db.execute(
        text("SELECT 1 FROM suppliers WHERE id=:id AND company_id=:cid"),
        {"id": supplier_id, "cid": cid},
    ).first()
    if not row:
        raise HTTPException(400, "Tedarikçi bulunamadı veya firmaya ait değil")


def _manual_prices(db: Session, cid: int, product_ids: list[int]) -> dict[int, dict[int, dict[str, Any]]]:
    """{product_id: {supplier_id: manual row}} for active manual prices."""
    if not product_ids:
        return {}
    placeholders = ",".join(f":p{i}" for i in range(len(product_ids)))
    params: dict[str, Any] = {"cid": cid}
    for i, pid in enumerate(product_ids):
        params[f"p{i}"] = pid
    rows = db.execute(
        text(
            f"""SELECT spp.id,spp.product_id,spp.supplier_id,spp.price,spp.currency,
            spp.note,spp.updated_at,spp.moq,spp.lead_time_days,spp.discount_percent,
            spp.supplier_stock,spp.price_includes_vat,s.name supplier_name
            FROM supplier_product_prices spp
            JOIN suppliers s ON s.id=spp.supplier_id AND s.company_id=spp.company_id
            WHERE spp.company_id=:cid AND spp.is_active=TRUE
              AND spp.product_id IN ({placeholders})"""
        ),
        params,
    ).mappings().all()
    result: dict[int, dict[int, dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(int(row["product_id"]), {})[int(row["supplier_id"])] = dict(row)
    return result


def _purchase_stats(db: Session, cid: int, product_ids: list[int]) -> dict[int, dict[int, dict[str, Any]]]:
    """{product_id: {supplier_id: {count, avg_price, last_price, last_date, name}}}
    derived from the company's own purchase history. Purchases are in TRY."""
    if not product_ids:
        return {}
    placeholders = ",".join(f":p{i}" for i in range(len(product_ids)))
    params: dict[str, Any] = {"cid": cid}
    for i, pid in enumerate(product_ids):
        params[f"p{i}"] = pid
    # last_price uses a correlated pick of the most recent purchase line for the
    # (supplier, product); purchase_date is an ISO string so lexical DESC == most
    # recent. pu.supplier_id / pi.product_id are grouped, so the correlation is
    # valid on PostgreSQL too.
    rows = db.execute(
        text(
            f"""SELECT pu.supplier_id,pi.product_id,s.name supplier_name,
            COUNT(*) purchase_count,
            AVG(pi.unit_price) avg_price,
            MAX(pu.purchase_date) last_date,
            (SELECT pi2.unit_price FROM purchase_items pi2
               JOIN purchases pu2 ON pu2.id=pi2.purchase_id AND pu2.company_id=:cid
               WHERE pu2.supplier_id=pu.supplier_id AND pi2.product_id=pi.product_id
               ORDER BY pu2.purchase_date DESC, pu2.id DESC LIMIT 1) last_price
            FROM purchase_items pi
            JOIN purchases pu ON pu.id=pi.purchase_id AND pu.company_id=:cid
            JOIN suppliers s ON s.id=pu.supplier_id AND s.company_id=:cid
            WHERE pi.product_id IN ({placeholders})
            GROUP BY pu.supplier_id, pi.product_id, s.name"""
        ),
        params,
    ).mappings().all()
    result: dict[int, dict[int, dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(int(row["product_id"]), {})[int(row["supplier_id"])] = dict(row)
    return result


# Default tolerance for surfacing a marginally-pricier-but-better alternative.
DEFAULT_ALT_THRESHOLD = Decimal("5")

# Demand window backing the derived reorder point. Seasonality-aware forecasting
# is a committed follow-up; keeping the whole demand computation inside
# ``_demand_stats`` means only that helper changes when it lands.
DEMAND_WINDOW_DAYS = 90


def _num(value: Any) -> Decimal | None:
    return None if value is None else decimal_value(value, field_name="Değer")


def _price_tiers(db: Session, cid: int, price_ids: list[int]) -> dict[int, list[tuple[Decimal, Decimal]]]:
    """{supplier_price_id: [(min_quantity, discount_percent), ...]} ascending.

    Only loaded when an order quantity is in play; without a quantity the flat
    ``discount_percent`` is the whole contract and no tier query is issued.
    """
    if not price_ids:
        return {}
    placeholders = ",".join(f":t{i}" for i in range(len(price_ids)))
    params: dict[str, Any] = {"cid": cid}
    for i, pid in enumerate(price_ids):
        params[f"t{i}"] = pid
    rows = db.execute(
        text(
            f"""SELECT supplier_price_id,min_quantity,discount_percent
            FROM supplier_price_discount_tiers
            WHERE company_id=:cid AND supplier_price_id IN ({placeholders})
            ORDER BY supplier_price_id, min_quantity"""
        ),
        params,
    ).mappings().all()
    result: dict[int, list[tuple[Decimal, Decimal]]] = {}
    for row in rows:
        result.setdefault(int(row["supplier_price_id"]), []).append(
            (
                decimal_value(row["min_quantity"], field_name="Miktar"),
                decimal_value(row["discount_percent"], field_name="İskonto"),
            )
        )
    return result


def _applied_discount(
    tiers: list[tuple[Decimal, Decimal]],
    order_quantity: Decimal | None,
    flat_discount: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    """(discount_percent, tier_min_quantity) for this order quantity.

    The rung with the highest ``min_quantity`` that the quantity reaches wins.
    With no quantity, or no rung reached, the flat ``discount_percent`` stays in
    force — that is the Increment-B contract and keeps existing callers byte
    identical.
    """
    if order_quantity is None or not tiers:
        return flat_discount, None
    applied: tuple[Decimal, Decimal] | None = None
    for min_quantity, discount in tiers:
        if order_quantity >= min_quantity:
            applied = (min_quantity, discount)
    if applied is None:
        return flat_discount, None
    return applied[1], applied[0]


def _line_cost(unit_cost_try: Decimal | None, order_quantity: Decimal | None) -> Decimal | None:
    """Line cost at the normalised TRY scale (4dp), matching ``to_try``."""
    if unit_cost_try is None or order_quantity is None:
        return None
    return (unit_cost_try * order_quantity).quantize(
        NORMALIZED_TRY_QUANTUM, rounding=ROUND_HALF_UP
    )


def _ceil_to_moq(order_quantity: Decimal, moq: Decimal | None) -> Decimal:
    """Round up to the next whole MOQ multiple. MOQ of 0/NULL rounds nothing."""
    if moq is None or moq <= ZERO:
        return order_quantity
    multiples = (order_quantity / moq).to_integral_value(rounding=ROUND_CEILING)
    if multiples < ONE:
        multiples = ONE
    return quantize_quantity(multiples * moq)


def _build_offers(
    db: Session,
    cid: int,
    product_id: int,
    manual: dict[int, dict[str, Any]],
    purchases: dict[int, dict[str, Any]],
    alt_threshold: Decimal = DEFAULT_ALT_THRESHOLD,
    order_quantity: Decimal | None = None,
    base_quantity: Decimal | None = None,
) -> list[dict[str, Any]]:
    """Merge manual + purchase-derived data into per-supplier offers.

    Increment B ranks by the **effective, TRY-normalised** unit price — the raw
    price after the supplier's discount, converted to TRY — rather than raw
    price, so a headline-cheap but undiscounted offer no longer wins by accident.
    ``to_try`` is the single rounding point (4dp), so a tie stays a tie and the
    ranking never disagrees with the displayed price. The cheapest effective
    offer carries ``best_offer``.

    On top of pure price it surfaces a decision trade-off: at most one *other*
    offer that is only marginally pricier (within ``alt_threshold`` percent of the
    best effective price) yet is materially faster (lower ``lead_time_days``) or
    is in stock while the best offer is not, is flagged ``faster_alternative`` with
    a plain ``alternative_reason`` (``faster`` / ``in_stock`` / ``faster_in_stock``).
    The rule is deliberately transparent — no scoring model.

    Quantity handling (B/C). ``order_quantity`` is the buyer's explicit quantity
    and is used verbatim for every supplier so the comparison stays like-for-like
    (a supplier's MOQ is reported, never silently applied). ``base_quantity`` is
    the reorder path's supplier-independent deficit: each supplier is then
    evaluated at its own achievable quantity — ``max(deficit, MOQ)`` rounded up
    to a whole MOQ multiple. Either way the discount tier AND the ranking are
    computed at that per-supplier quantity, because a ladder or an MOQ can change
    which supplier is actually cheapest. Ranking never uses the list price.
    """
    supplier_ids = set(manual) | set(purchases)
    tiers_by_price: dict[int, list[tuple[Decimal, Decimal]]] = {}
    if order_quantity is not None or base_quantity is not None:
        tiers_by_price = _price_tiers(
            db, cid, [int(row["id"]) for row in manual.values()]
        )
    offers: list[dict[str, Any]] = []
    for supplier_id in supplier_ids:
        m = manual.get(supplier_id)
        p = purchases.get(supplier_id)
        name = (m or p or {}).get("supplier_name")
        manual_price = _num(m["price"]) if m else None
        manual_currency = str(m["currency"]) if m else None
        discount = _num(m["discount_percent"]) if m and m.get("discount_percent") is not None else None
        lead_time = int(m["lead_time_days"]) if m and m.get("lead_time_days") is not None else None
        moq = _num(m["moq"]) if m and m.get("moq") is not None else None
        stock = _num(m["supplier_stock"]) if m and m.get("supplier_stock") is not None else None

        includes_vat = bool(m["price_includes_vat"]) if m and m.get("price_includes_vat") is not None else True

        # Per-supplier evaluated quantity. Explicit buyer quantity wins verbatim;
        # otherwise the reorder deficit is lifted to this supplier's MOQ and
        # rounded up to a whole multiple of it.
        evaluated_quantity: Decimal | None = None
        if order_quantity is not None:
            evaluated_quantity = order_quantity
        elif base_quantity is not None:
            candidate = base_quantity
            if moq is not None and moq > candidate:
                candidate = moq
            evaluated_quantity = _ceil_to_moq(candidate, moq)

        tiers = tiers_by_price.get(int(m["id"]), []) if m else []
        # ``discount_percent`` keeps its Increment-B meaning (the record's flat
        # discount); the quantity-resolved value is reported separately as
        # ``applied_discount_percent`` and is what the effective price uses.
        flat_discount = discount
        discount, tier_min = _applied_discount(tiers, evaluated_quantity, flat_discount)

        rate = resolve_rate_to_try(db, cid, manual_currency or "TRY") if m else None
        manual_in_try = to_try(manual_price, rate) if manual_price is not None else None

        last_price = _num(p["last_price"]) if p and p.get("last_price") is not None else None
        avg_price = _num(p["avg_price"]) if p and p.get("avg_price") is not None else None

        # Effective unit price = raw * (1 - discount%/100). Discount only ever
        # applies to a manual reference price; purchase-derived history is already
        # the net price actually paid, so its effective == raw. ``to_try`` (4dp) is
        # the sole normalisation/rounding point; TRY purchases go through it too so
        # every effective_price_in_try is on one identical scale.
        factor = (HUNDRED - discount) / HUNDRED if discount is not None else None
        effective_price = None
        effective_in_try = None
        if manual_price is not None:
            effective_price = manual_price * factor if factor is not None else manual_price
            effective_in_try = to_try(effective_price, rate)
        elif last_price is not None:
            effective_price = last_price
            effective_in_try = to_try(last_price, ONE)

        # Ranking is by the effective TRY price (None => un-rankable, sorted last).
        # price_in_try keeps its Increment-A meaning — the RAW normalised price
        # (manual reference else last purchase) for backward compatibility; the
        # discounted value lives only in effective_price_in_try.
        rank_try = effective_in_try
        raw_in_try = manual_in_try if manual_price is not None else last_price
        offers.append({
            "supplier_id": supplier_id,
            "supplier_name": name,
            "manual_price_id": int(m["id"]) if m else None,
            "manual_price": _s(manual_price),
            "manual_currency": manual_currency,
            "manual_price_in_try": _s(manual_in_try),
            "last_purchase_price": _s(last_price),
            "avg_purchase_price": _s(avg_price),
            "last_purchase_date": (str(p["last_date"]) if p and p.get("last_date") else None),
            "purchase_count": int(p["purchase_count"]) if p else 0,
            "currency": manual_currency or "TRY",
            # Enriched decision fields (Increment B).
            "discount_percent": _s(flat_discount),
            "effective_price": _s(effective_price),
            "effective_price_in_try": _s(effective_in_try),
            "lead_time_days": lead_time,
            "moq": _s(moq),
            "supplier_stock": _s(stock),
            # Quantity-aware decision fields. ``catalog_unit_cost_try`` is
            # deliberately named "catalog": it is the discounted purchase price
            # only. Freight, customs and other landed-cost components are out of
            # scope and must not be read into this figure.
            "price_includes_vat": includes_vat,
            "evaluated_quantity": _s(evaluated_quantity),
            "applied_discount_percent": _s(discount),
            "applied_tier_min_quantity": _s(tier_min),
            "catalog_unit_cost_try": _s(effective_in_try),
            "catalog_cost_try_total": _s(_line_cost(effective_in_try, evaluated_quantity)),
            "eta_date": (
                (business_today() + timedelta(days=lead_time)).isoformat()
                if lead_time is not None
                else None
            ),
            # price_in_try = RAW normalised (Increment-A contract, unchanged).
            "price_in_try": _s(raw_in_try),
            "updated_at": (str(m["updated_at"]) if m and m.get("updated_at") else None),
            "note": (m.get("note") if m else None),
            "_rank": rank_try,
            "_lead": lead_time,
            "_stock": stock,
            "best_offer": False,
            "faster_alternative": False,
            "alternative_reason": None,
        })
    # Sort by effective TRY ascending; un-rankable (None) last. supplier_id is the
    # final tie-break so equal-priced offers order deterministically.
    offers.sort(key=lambda o: (o["_rank"] is None, o["_rank"] if o["_rank"] is not None else Decimal(0), o["supplier_id"]))
    best = next((o for o in offers if o["_rank"] is not None), None)
    if best is not None:
        best["best_offer"] = True
        _flag_alternative(offers, best, alt_threshold)
    for offer in offers:
        for key in ("_rank", "_lead", "_stock"):
            offer.pop(key, None)
    return offers


def _flag_alternative(offers: list[dict[str, Any]], best: dict[str, Any], threshold: Decimal) -> None:
    """Flag at most one marginally-pricier-but-better alternative to ``best``.

    Qualifies when the offer's effective TRY price is within ``threshold`` percent
    of the best and it is either strictly faster (lower lead time, both known) or
    in stock while the best offer is not. Offers are pre-sorted cheapest-first, so
    the first qualifier is the cheapest such alternative.
    """
    best_rank: Decimal = best["_rank"]
    ceiling = best_rank * (HUNDRED + threshold) / HUNDRED
    best_lead = best["_lead"]
    best_in_stock = best["_stock"] is not None and best["_stock"] > 0
    for offer in offers:
        if offer is best or offer["_rank"] is None or offer["_rank"] > ceiling:
            continue
        faster = best_lead is not None and offer["_lead"] is not None and offer["_lead"] < best_lead
        in_stock = (offer["_stock"] is not None and offer["_stock"] > 0) and not best_in_stock
        if not (faster or in_stock):
            continue
        offer["faster_alternative"] = True
        offer["alternative_reason"] = (
            "faster_in_stock" if faster and in_stock else "faster" if faster else "in_stock"
        )
        break


@router.get("/products/{product_id}/supplier-prices")
def product_supplier_prices(
    product_id: int,
    request: Request,
    alt_threshold: Decimal = Query(default=DEFAULT_ALT_THRESHOLD, ge=0, le=100),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    name = _require_product(db, cid, product_id)
    manual = _manual_prices(db, cid, [product_id]).get(product_id, {})
    purchases = _purchase_stats(db, cid, [product_id]).get(product_id, {})
    offers = _build_offers(db, cid, product_id, manual, purchases, alt_threshold)
    return {
        "product_id": product_id,
        "product_name": name,
        "alt_threshold_percent": str(alt_threshold),
        "rates": {code: serialize_rate(rate) for code, rate in resolve_rates(db, cid).items()},
        "items": offers,
    }


@router.get("/purchase-comparison")
def purchase_comparison(
    request: Request,
    q: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    alt_threshold: Decimal = Query(default=DEFAULT_ALT_THRESHOLD, ge=0, le=100),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    # Candidate products: those with any manual price OR any purchase-item
    # history in this company, optionally filtered by name / code / barcode.
    search = f"%{q.strip()}%"
    where_search = (
        "(LOWER(p.name) LIKE LOWER(:q) OR LOWER(COALESCE(p.product_code,'')) LIKE LOWER(:q) "
        "OR COALESCE(p.barcode,'') LIKE :q)"
    )
    base = f"""FROM products p
        WHERE p.company_id=:cid AND {where_search} AND (
          EXISTS(SELECT 1 FROM supplier_product_prices spp
                 WHERE spp.company_id=:cid AND spp.product_id=p.id AND spp.is_active=TRUE)
          OR EXISTS(SELECT 1 FROM purchase_items pi JOIN purchases pu ON pu.id=pi.purchase_id
                    AND pu.company_id=:cid WHERE pi.product_id=p.id)
        )"""
    params: dict[str, Any] = {"cid": cid, "q": search, "limit": page_size, "offset": (page - 1) * page_size}
    total = int(db.execute(text(f"SELECT COUNT(*) {base}"), params).scalar_one())
    rows = db.execute(
        text(
            f"""SELECT p.id,p.name,p.product_code {base}
            ORDER BY p.name, p.id LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    product_ids = [int(r["id"]) for r in rows]
    manual = _manual_prices(db, cid, product_ids)
    purchases = _purchase_stats(db, cid, product_ids)
    supplier_columns: dict[int, str] = {}
    items: list[dict[str, Any]] = []
    for row in rows:
        pid = int(row["id"])
        offers = _build_offers(db, cid, pid, manual.get(pid, {}), purchases.get(pid, {}), alt_threshold)
        for offer in offers:
            if offer["supplier_name"] is not None:
                supplier_columns[offer["supplier_id"]] = offer["supplier_name"]
        best = next((o for o in offers if o["best_offer"]), None)
        items.append({
            "product_id": pid,
            "product_code": row["product_code"],
            "product_name": row["name"],
            "offers": {o["supplier_id"]: {
                "price_in_try": o["price_in_try"],
                "currency": o["currency"],
                "is_manual": o["manual_price_id"] is not None,
                # Enriched decision fields for the grid cell (Increment B).
                "discount_percent": o["discount_percent"],
                "effective_price_in_try": o["effective_price_in_try"],
                "lead_time_days": o["lead_time_days"],
                "moq": o["moq"],
                "supplier_stock": o["supplier_stock"],
                "best_offer": o["best_offer"],
                "faster_alternative": o["faster_alternative"],
                "alternative_reason": o["alternative_reason"],
            } for o in offers},
            "best_offer": None if best is None else {
                "supplier_id": best["supplier_id"],
                "price_in_try": best["price_in_try"],
                "effective_price_in_try": best["effective_price_in_try"],
            },
        })
    return {
        "suppliers": [{"id": sid, "name": name} for sid, name in sorted(supplier_columns.items(), key=lambda kv: kv[1])],
        "items": items,
        "alt_threshold_percent": str(alt_threshold),
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


# ---- B: quantity-aware analysis (cost, tier, margin, ETA) ------------------
def _product_pricing(db: Session, cid: int, product_ids: list[int]) -> dict[int, dict[str, Decimal]]:
    """{product_id: {sale_price, vat_rate}} for the margin calculation."""
    if not product_ids:
        return {}
    placeholders = ",".join(f":m{i}" for i in range(len(product_ids)))
    params: dict[str, Any] = {"cid": cid}
    for i, pid in enumerate(product_ids):
        params[f"m{i}"] = pid
    rows = db.execute(
        text(
            f"""SELECT id,sale_price,vat_rate FROM products
            WHERE company_id=:cid AND id IN ({placeholders})"""
        ),
        params,
    ).mappings().all()
    return {
        int(row["id"]): {
            "sale_price": decimal_value(row["sale_price"], field_name="Satış fiyatı"),
            "vat_rate": decimal_value(row["vat_rate"], field_name="KDV oranı"),
        }
        for row in rows
    }


def _margin_percent(
    unit_cost_try: Decimal | None,
    pricing: dict[str, Decimal] | None,
    price_includes_vat: bool,
) -> Decimal | None:
    """Gross margin on a VAT-EXCLUSIVE basis, as a percentage.

    The application-wide ``unit_price`` contract is VAT-inclusive, and a supplier
    price is VAT-inclusive unless its ``price_includes_vat`` flag says otherwise.
    Both sides are therefore reduced to net before the comparison so a supplier
    quoting net prices is never made to look more expensive than it is::

        cost_ex = includes_vat ? cost / (1 + vat/100) : cost
        sale_ex = sale_price / (1 + vat/100)
        margin  = (sale_ex - cost_ex) / sale_ex * 100

    Returns None when the cost or a usable sale price is missing — a zero sale
    price would otherwise divide by zero and a fabricated 0% would read as a real
    measurement.
    """
    if unit_cost_try is None or pricing is None:
        return None
    sale_price = pricing["sale_price"]
    if sale_price <= ZERO:
        return None
    vat_factor = ONE + pricing["vat_rate"] / HUNDRED
    cost_ex = unit_cost_try / vat_factor if price_includes_vat else unit_cost_try
    sale_ex = sale_price / vat_factor
    return percentage((sale_ex - cost_ex) / sale_ex * HUNDRED)


@router.get("/purchase-comparison/products/{product_id}/analysis")
def product_purchase_analysis(
    product_id: int,
    request: Request,
    quantity: Decimal | None = Query(default=None, gt=0),
    max_lead_days: int | None = Query(default=None, ge=0),
    alt_threshold: Decimal = Query(default=DEFAULT_ALT_THRESHOLD, ge=0, le=100),
    db: Session = Depends(get_db),
):
    """Supply analysis for one product at a given order quantity.

    Every supplier is priced at ``quantity`` through its own discount ladder and
    ranked on the resulting effective TRY cost — never on the list price, because
    a ladder or an MOQ can change who is actually cheapest. ``max_lead_days``
    filters before ranking and the excluded suppliers are reported rather than
    silently dropped, so a cheap-but-slow offer stays visible as a trade-off.
    """
    cid = company_id(request)
    name = _require_product(db, cid, product_id)
    manual = _manual_prices(db, cid, [product_id]).get(product_id, {})
    purchases = _purchase_stats(db, cid, [product_id]).get(product_id, {})
    offers = _build_offers(db, cid, product_id, manual, purchases, alt_threshold, order_quantity=quantity)
    pricing = _product_pricing(db, cid, [product_id]).get(product_id)

    excluded: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for offer in offers:
        offer["margin_percent"] = _s(
            _margin_percent(
                _num(offer["catalog_unit_cost_try"]), pricing, offer["price_includes_vat"]
            )
        )
        moq = _num(offer["moq"])
        offer["moq_satisfied"] = (
            None if quantity is None or moq is None else bool(quantity >= moq)
        )
        if (
            max_lead_days is not None
            and offer["lead_time_days"] is not None
            and offer["lead_time_days"] > max_lead_days
        ):
            excluded.append({
                "supplier_id": offer["supplier_id"],
                "supplier_name": offer["supplier_name"],
                "lead_time_days": offer["lead_time_days"],
                "reason": "lead_time_exceeds_limit",
            })
            continue
        kept.append(offer)

    # The best-offer flag is recomputed over the surviving set so a lead-time
    # filter cannot leave the recommendation pointing at an excluded supplier.
    recommended = next((o for o in kept if o["effective_price_in_try"] is not None), None)
    for offer in offers:
        offer["recommended"] = False
    if recommended is not None:
        recommended["recommended"] = True
    return {
        "product_id": product_id,
        "product_name": name,
        "quantity": _s(quantity),
        "max_lead_days": max_lead_days,
        "alt_threshold_percent": str(alt_threshold),
        "sale_price": None if pricing is None else _s(pricing["sale_price"]),
        "vat_rate": None if pricing is None else _s(pricing["vat_rate"]),
        "rates": {code: serialize_rate(rate) for code, rate in resolve_rates(db, cid).items()},
        "items": kept,
        "excluded": excluded,
        "recommended_supplier_id": None if recommended is None else recommended["supplier_id"],
    }


# ---- Increment C: one-click PO + auto-reorder ------------------------------
def _product_vat_rate(db: Session, cid: int, product_id: int) -> int:
    row = db.execute(
        text("SELECT vat_rate FROM products WHERE id=:id AND company_id=:cid"),
        {"id": product_id, "cid": cid},
    ).first()
    # _require_product has already guaranteed the row exists and is in-tenant.
    return int(decimal_value(row[0], field_name="KDV oranı"))


def _supplier_offer(db: Session, cid: int, product_id: int, supplier_id: int) -> dict[str, Any] | None:
    """The current comparison offer (Increment B ranking) for one supplier."""
    manual = _manual_prices(db, cid, [product_id]).get(product_id, {})
    purchases = _purchase_stats(db, cid, [product_id]).get(product_id, {})
    offers = _build_offers(db, cid, product_id, manual, purchases)
    return next((o for o in offers if o["supplier_id"] == supplier_id), None)


@router.post("/purchase-orders", status_code=201)
def create_purchase_order(payload: PurchaseOrderCreate, request: Request, db: Session = Depends(get_db)):
    """Create a purchase for one chosen offer via the EXISTING purchase path.

    Reuses ``transactions._save`` — the same engine sales/POS and manual
    purchases use — so tenant scoping, Decimal totals, stock-in and finance sync
    behave identically to a normal purchase. Nothing about the accounting path is
    reimplemented here; this endpoint only resolves sensible defaults (quantity =
    MOQ, unit price = the offer's known TRY price) and hands off.
    """
    from .transactions import _save  # local import avoids any module import cycle

    cid = company_id(request)
    _require_product(db, cid, payload.product_id)
    _require_supplier(db, cid, payload.supplier_id)
    offer = _supplier_offer(db, cid, payload.product_id, payload.supplier_id)

    # Unit price (TRY): an explicit expected price wins, else the supplier's known
    # effective TRY price, else its raw normalised price. No price anywhere -> 422.
    unit_price = payload.expected_price
    if unit_price is None and offer is not None:
        raw = offer.get("effective_price_in_try") or offer.get("price_in_try")
        unit_price = decimal_value(raw, field_name="Fiyat") if raw is not None else None
    if unit_price is None:
        raise HTTPException(422, "Bu tedarikçi için fiyat bulunamadı; beklenen fiyatı girin")

    # Quantity: explicit wins, else the supplier's MOQ, else 1.
    order_qty = payload.quantity
    if order_qty is None:
        moq = offer.get("moq") if offer else None
        order_qty = decimal_value(moq, field_name="Miktar") if moq else Decimal("1")

    tx = TransactionCreate(
        entity_id=payload.supplier_id,
        transaction_date=business_today().isoformat(),
        status="draft",
        note=payload.note or "Otomatik sipariş (fiyat karşılaştırma)",
        items=[TransactionItem(
            product_id=payload.product_id,
            quantity=order_qty,
            unit_price=unit_price,
            vat_rate=_product_vat_rate(db, cid, payload.product_id),
        )],
    )
    result = _save("purchase", tx, db, cid, override_context(request))
    # An explicitly requested quantity is never silently grown to the MOQ: the
    # buyer sees the shortfall reported and decides. Only the reorder-draft flow
    # rounds, and it reports both figures.
    offer_moq = _num(offer.get("moq")) if offer else None
    return {
        "purchase_id": result["id"],
        "supplier_id": payload.supplier_id,
        "product_id": payload.product_id,
        "quantity": _s(order_qty),
        "unit_price": _s(unit_price),
        "moq": _s(offer_moq),
        "moq_satisfied": None if offer_moq is None else bool(order_qty >= offer_moq),
        "status": result["status"],
        "subtotal": _s(result["subtotal"]),
        "vat_total": _s(result["vat_total"]),
        "final_total": _s(result["final_total"]),
    }


# ---- C: demand, open orders and the reorder policy -------------------------
def _sum_by_product(db: Session, sql: str, params: dict[str, Any]) -> dict[int, Decimal]:
    return {
        int(row["product_id"]): decimal_value(row["qty"], field_name="Miktar")
        for row in db.execute(text(sql), params).mappings().all()
    }


def _id_filter(product_ids: list[int], prefix: str, params: dict[str, Any]) -> str:
    placeholders = ",".join(f":{prefix}{i}" for i in range(len(product_ids)))
    for i, pid in enumerate(product_ids):
        params[f"{prefix}{i}"] = pid
    return placeholders


def _demand_stats(
    db: Session, cid: int, product_ids: list[int], window_days: int = DEMAND_WINDOW_DAYS
) -> dict[int, dict[str, Decimal]]:
    """Net daily demand per product over the trailing window.

    Sales returns are held as INDEPENDENT ``returns`` documents (``return_type =
    'sale_return'``, optionally linked to the order via ``source_type='order'`` /
    ``source_id``) and the original ``orders``/``order_items`` rows are left
    untouched. Filtering sales on status alone would therefore count returned
    goods as demand and inflate the reorder point, so returned quantities are
    subtracted here::

        net = max(sold - returned, 0)

    Both linked and unlinked returns count: for forecasting, goods coming back
    are goods coming back. ``purchase_return`` is excluded — that is supply, not
    demand. Draft and cancelled documents are excluded on both sides.

    This helper is the single seam for demand: seasonality-aware forecasting
    replaces its body without touching any caller.
    """
    if not product_ids:
        return {}
    from .transactions import _normalized_date_sql

    window_start = (business_today() - timedelta(days=window_days)).isoformat()
    params: dict[str, Any] = {"cid": cid, "start": window_start}
    placeholders = _id_filter(product_ids, "d", params)
    sold = _sum_by_product(
        db,
        f"""SELECT oi.product_id product_id,COALESCE(SUM(oi.quantity),0) qty
        FROM order_items oi
        JOIN orders o ON o.id=oi.order_id AND o.company_id=:cid
        WHERE oi.company_id=:cid
          AND oi.product_id IN ({placeholders})
          AND COALESCE(o.status,'completed') NOT IN ('draft','cancelled')
          AND {_normalized_date_sql('o.order_date')} >= :start
        GROUP BY oi.product_id""",
        params,
    )
    returned = _sum_by_product(
        db,
        f"""SELECT ri.product_id product_id,COALESCE(SUM(ri.quantity),0) qty
        FROM return_items ri
        JOIN returns r ON r.id=ri.return_id AND r.company_id=:cid
        WHERE ri.company_id=:cid
          AND ri.product_id IN ({placeholders})
          AND r.return_type='sale_return'
          AND COALESCE(r.status,'completed') NOT IN ('draft','cancelled')
          AND {_normalized_date_sql('r.return_date')} >= :start
        GROUP BY ri.product_id""",
        params,
    )
    stats: dict[int, dict[str, Decimal]] = {}
    window = Decimal(window_days)
    for pid in product_ids:
        gross = sold.get(pid, ZERO)
        back = returned.get(pid, ZERO)
        net = gross - back
        if net < ZERO:
            net = ZERO
        stats[pid] = {
            "sold": gross,
            "returned": back,
            "net": net,
            "avg_daily": net / window,
        }
    return stats


def _on_order(db: Session, cid: int, product_ids: list[int]) -> dict[int, Decimal]:
    """Open purchase quantity per product.

    Counted set = purchases that have NOT reached stock and are not cancelled.
    ``STOCK_STATUSES`` is ``{approved, completed}``, so ``draft`` and ``pending``
    are exactly the open ones. The whitelist is deliberate: a terminal status
    added later cannot silently leak into "on order".

    The model has no goods-receipt line, so a partially received order still
    counts its full ordered quantity. That overstates ``on_order`` and therefore
    UNDER-orders rather than double-orders — the safe direction. Responses label
    this as ``on_order_basis = open_document_lines``.
    """
    if not product_ids:
        return {}
    params: dict[str, Any] = {"cid": cid}
    placeholders = _id_filter(product_ids, "o", params)
    return _sum_by_product(
        db,
        f"""SELECT pi.product_id product_id,COALESCE(SUM(pi.quantity),0) qty
        FROM purchase_items pi
        JOIN purchases pu ON pu.id=pi.purchase_id AND pu.company_id=:cid
        WHERE pi.company_id=:cid
          AND pi.product_id IN ({placeholders})
          AND COALESCE(pu.status,'completed') IN ('draft','pending')
        GROUP BY pi.product_id""",
        params,
    )


ON_ORDER_BASIS = "open_document_lines"


def _best_lead_time(offers: list[dict[str, Any]]) -> int | None:
    """Lead time of the cheapest rankable offer, else the shortest known one."""
    best = next((o for o in offers if o["effective_price_in_try"] is not None), None)
    if best is not None and best["lead_time_days"] is not None:
        return int(best["lead_time_days"])
    leads = [int(o["lead_time_days"]) for o in offers if o["lead_time_days"] is not None]
    return min(leads) if leads else None


def _reorder_policy(
    row: dict[str, Any], avg_daily: Decimal, lead_time_days: int | None
) -> tuple[Decimal, str, Decimal]:
    """(reorder_point, source, target_stock) for one product.

    ``products.reorder_point`` NULL means "derive"; a stored value — including
    ``0`` — is an explicit override and wins. Deriving needs both demand and a
    lead time; without them the long-standing ``minimum_stock`` threshold stays
    in force, which is what makes the pre-existing behaviour a strict subset of
    this one::

        derived = avg_daily_demand * lead_time_days + minimum_stock   (safety)
        target  = reorder_point + avg_daily_demand * lead_time_days
    """
    minimum = decimal_value(row["minimum_stock"], field_name="Minimum stok")
    lead = Decimal(lead_time_days) if lead_time_days is not None else None

    if row.get("reorder_point") is not None:
        reorder_point = decimal_value(row["reorder_point"], field_name="Sipariş noktası")
        source = "manual"
    elif lead is not None and avg_daily > ZERO:
        reorder_point = quantize_quantity(avg_daily * lead + minimum)
        source = "90-day-derived"
    else:
        reorder_point = minimum
        source = "minimum_stock-fallback"

    if row.get("target_stock") is not None:
        target = decimal_value(row["target_stock"], field_name="Hedef stok")
    else:
        cycle = avg_daily * lead if lead is not None else ZERO
        target = quantize_quantity(reorder_point + cycle)
    return reorder_point, source, target


def _reorder_candidates(
    db: Session, cid: int, product_ids: list[int] | None = None
) -> list[dict[str, Any]]:
    """Products whose available stock has reached the reorder point.

    Trigger: ``on_hand + on_order <= reorder_point``. ``on_hand`` is
    ``products.stock``, the denormalised sum of ``warehouse_stocks`` kept in sync
    by ``inventory.sync_product_stock`` — one stock source, company-wide, the
    same one the previous threshold used.

    Candidate set = active products that purchasing actually manages: a positive
    ``minimum_stock`` (the long-standing opt-in), an explicit ``reorder_point``,
    or a live manual supplier price. Every supplier is then evaluated at its own
    achievable quantity so ladders and MOQs decide the recommendation, not the
    list price.
    """
    params: dict[str, Any] = {"cid": cid}
    scope = ""
    if product_ids is not None:
        if not product_ids:
            return []
        scope = f" AND p.id IN ({_id_filter(product_ids, 'r', params)})"
    rows = db.execute(
        text(
            f"""SELECT p.id,p.name,p.product_code,p.stock,p.minimum_stock,
            p.reorder_point,p.target_stock
            FROM products p
            WHERE p.company_id=:cid AND p.active=TRUE{scope}
              AND (p.minimum_stock > 0 OR p.reorder_point IS NOT NULL
                   OR EXISTS(SELECT 1 FROM supplier_product_prices spp
                             WHERE spp.company_id=:cid AND spp.product_id=p.id
                               AND spp.is_active=TRUE))
            ORDER BY p.name, p.id"""
        ),
        params,
    ).mappings().all()
    candidate_ids = [int(row["id"]) for row in rows]
    if not candidate_ids:
        return []

    manual = _manual_prices(db, cid, candidate_ids)
    purchases = _purchase_stats(db, cid, candidate_ids)
    demand = _demand_stats(db, cid, candidate_ids)
    on_order = _on_order(db, cid, candidate_ids)

    items: list[dict[str, Any]] = []
    for row in rows:
        pid = int(row["id"])
        stats = demand[pid]
        # First pass without a quantity: the lead time driving the reorder point
        # comes from the current best offer.
        base_offers = _build_offers(db, cid, pid, manual.get(pid, {}), purchases.get(pid, {}))
        lead_time = _best_lead_time(base_offers)
        reorder_point, source, target = _reorder_policy(row, stats["avg_daily"], lead_time)

        on_hand = decimal_value(row["stock"], field_name="Stok")
        open_qty = on_order.get(pid, ZERO)
        available = on_hand + open_qty
        if available > reorder_point:
            continue

        deficit = target - available
        if deficit <= ZERO:
            # Already at target through open orders; nothing left to order even
            # though the reorder point was reached.
            continue

        # Second pass at the deficit: each supplier is now priced at the quantity
        # it would actually have to ship.
        offers = _build_offers(
            db, cid, pid, manual.get(pid, {}), purchases.get(pid, {}), base_quantity=deficit
        )
        best = next((o for o in offers if o["effective_price_in_try"] is not None), None)
        items.append({
            "product_id": pid,
            "product_code": row["product_code"],
            "product_name": row["name"],
            "current_stock": _s(on_hand),
            "on_hand": _s(on_hand),
            "on_order": _s(open_qty),
            "on_order_basis": ON_ORDER_BASIS,
            "available": _s(available),
            "minimum_stock": _s(decimal_value(row["minimum_stock"], field_name="Minimum stok")),
            "reorder_point": _s(reorder_point),
            "reorder_point_source": source,
            "target_stock": _s(target),
            "avg_daily_demand": _s(stats["avg_daily"]),
            "demand_sold": _s(stats["sold"]),
            "demand_returned": _s(stats["returned"]),
            "demand_window_days": DEMAND_WINDOW_DAYS,
            "lead_time_days": lead_time,
            "deficit": _s(deficit),
            "suggested_quantity": _s(_num(best["evaluated_quantity"]) if best else deficit),
            "best_supplier": None if best is None else {
                "supplier_id": best["supplier_id"],
                "supplier_name": best["supplier_name"],
                "effective_price_in_try": best["effective_price_in_try"],
                "catalog_unit_cost_try": best["catalog_unit_cost_try"],
                "catalog_cost_try_total": best["catalog_cost_try_total"],
                "evaluated_quantity": best["evaluated_quantity"],
                "applied_discount_percent": best["applied_discount_percent"],
                "moq": best["moq"],
                "lead_time_days": best["lead_time_days"],
            },
            "suppliers": [{
                "supplier_id": o["supplier_id"],
                "supplier_name": o["supplier_name"],
                "evaluated_quantity": o["evaluated_quantity"],
                "catalog_unit_cost_try": o["catalog_unit_cost_try"],
                "catalog_cost_try_total": o["catalog_cost_try_total"],
                "moq": o["moq"],
                "lead_time_days": o["lead_time_days"],
            } for o in offers],
        })
    return items


@router.get("/purchase-comparison/reorder-suggestions")
def reorder_suggestions(request: Request, db: Session = Depends(get_db)):
    """Products at/below their reorder point, with the best-overall supplier.

    Read-only. The buyer reviews this list and turns the lines they accept into
    grouped draft orders via ``POST /purchase-comparison/reorder-drafts``; the
    system never orders on its own.
    """
    cid = company_id(request)
    return {
        "items": _reorder_candidates(db, cid),
        "demand_window_days": DEMAND_WINDOW_DAYS,
        "on_order_basis": ON_ORDER_BASIS,
    }


@router.put("/purchase-comparison/products/{product_id}/reorder-policy")
def set_reorder_policy(
    product_id: int, payload: ReorderPolicyWrite, request: Request, db: Session = Depends(get_db)
):
    """Set or clear the manual reorder overrides for one product.

    A field that is not sent is left untouched; sending ``null`` clears it back
    to "derive from demand". ``0`` is a real override, so NULL and 0 are never
    conflated — that distinction is the whole point of the nullable columns.
    """
    cid = company_id(request)
    _require_product(db, cid, product_id)
    sent = payload.model_fields_set
    if not sent:
        raise HTTPException(422, "Güncellenecek alan gönderilmedi")
    assignments = ",".join(f"{field}=:{field}" for field in sorted(sent))
    params: dict[str, Any] = {"id": product_id, "cid": cid}
    for field in sent:
        params[field] = getattr(payload, field)
    db.execute(
        text(f"UPDATE products SET {assignments} WHERE id=:id AND company_id=:cid"), params
    )
    db.commit()
    row = db.execute(
        text("SELECT reorder_point,target_stock FROM products WHERE id=:id AND company_id=:cid"),
        {"id": product_id, "cid": cid},
    ).mappings().one()
    return {
        "product_id": product_id,
        "reorder_point": _s(_num(row["reorder_point"])),
        "target_stock": _s(_num(row["target_stock"])),
    }


def _reorder_fingerprint(payload: ReorderDraftCreate) -> str:
    canonical = json.dumps(
        {
            "note": payload.note,
            "lines": sorted(
                [
                    [line.product_id, line.supplier_id, _s(line.quantity)]
                    for line in payload.lines
                ],
                key=lambda entry: (entry[0], entry[1], entry[2] or ""),
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _lock_reorder(db: Session, cid: int) -> None:
    """Serialise reorder-draft creation per tenant (PostgreSQL only).

    Two buyers approving the same suggestion list concurrently would otherwise
    both read the pre-draft ``on_order`` and each create a full set of drafts.
    The transaction-scoped advisory lock makes the second request wait, so it
    re-reads stock and open orders AFTER the first one's drafts exist and skips
    what is already covered. SQLite is single-writer, so this is a no-op there.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:cls, :cid)"),
        {"cls": REORDER_LOCK_CLASS, "cid": cid},
    )


REORDER_LOCK_CLASS = 79500330


def _draft_replay(db: Session, cid: int, key: str, fingerprint: str) -> list[int] | None:
    row = db.execute(
        text(
            """SELECT request_fingerprint,purchase_ids FROM purchase_draft_idempotency
            WHERE company_id=:cid AND idempotency_key=:key"""
        ),
        {"cid": cid, "key": key},
    ).mappings().first()
    if row is None:
        return None
    if str(row["request_fingerprint"]) != fingerprint:
        raise HTTPException(409, "Aynı idempotency anahtarı farklı bir istek için kullanıldı")
    return [int(value) for value in str(row["purchase_ids"]).split(",") if value]


@router.post("/purchase-comparison/reorder-drafts", status_code=201)
def create_reorder_drafts(
    payload: ReorderDraftCreate, request: Request, db: Session = Depends(get_db)
):
    """Turn approved reorder suggestions into supplier-grouped DRAFT purchases.

    One draft purchase per supplier carrying all of that supplier's approved
    lines. The status is hardcoded ``draft`` server-side — the request has no
    status field at all — so this endpoint can never approve, receive or transmit
    an order. A human approves the drafts through the normal purchase workflow.

    Everything runs in one transaction behind a per-tenant advisory lock, and the
    suggestion is re-verified INSIDE that transaction: stock and open orders are
    read again, and any line that no longer needs ordering is skipped and
    reported in ``skipped`` rather than silently dropped.
    """
    from .transactions import _save  # local import avoids any module import cycle

    cid = company_id(request)
    idempotency_key = (request.headers.get("idempotency-key") or "").strip() or None
    fingerprint = _reorder_fingerprint(payload)

    _lock_reorder(db, cid)
    if idempotency_key is not None:
        replay = _draft_replay(db, cid, idempotency_key, fingerprint)
        if replay is not None:
            return {
                "replayed": True,
                "purchases": [{"purchase_id": pid} for pid in replay],
                "skipped": [],
            }

    for line in payload.lines:
        _require_product(db, cid, line.product_id)
        _require_supplier(db, cid, line.supplier_id)

    # Re-verified under the lock: the GET that produced this list is a snapshot.
    requested_ids = sorted({line.product_id for line in payload.lines})
    candidates = {int(item["product_id"]): item for item in _reorder_candidates(db, cid, requested_ids)}

    skipped: list[dict[str, Any]] = []
    grouped: dict[int, list[dict[str, Any]]] = {}
    for line in payload.lines:
        candidate = candidates.get(line.product_id)
        if candidate is None:
            skipped.append({
                "product_id": line.product_id,
                "supplier_id": line.supplier_id,
                "reason": "no_longer_below_reorder_point",
            })
            continue
        offer = next(
            (o for o in candidate["suppliers"] if o["supplier_id"] == line.supplier_id), None
        )
        if offer is None or offer["catalog_unit_cost_try"] is None:
            skipped.append({
                "product_id": line.product_id,
                "supplier_id": line.supplier_id,
                "reason": "no_price_for_supplier",
            })
            continue
        requested = line.quantity if line.quantity is not None else _num(offer["evaluated_quantity"])
        if requested is None or requested <= ZERO:
            skipped.append({
                "product_id": line.product_id,
                "supplier_id": line.supplier_id,
                "reason": "no_quantity",
            })
            continue
        created = _ceil_to_moq(requested, _num(offer["moq"]))
        grouped.setdefault(line.supplier_id, []).append({
            "product_id": line.product_id,
            "requested_quantity": requested,
            "created_quantity": created,
            "unit_price": _num(offer["catalog_unit_cost_try"]),
            "moq": _num(offer["moq"]),
        })

    override = override_context(request)
    created_purchases: list[dict[str, Any]] = []
    for supplier_id in sorted(grouped):
        lines = grouped[supplier_id]
        tx = TransactionCreate(
            entity_id=supplier_id,
            transaction_date=business_today().isoformat(),
            status="draft",  # hardcoded: no request shape can change this
            note=payload.note or "Yeniden sipariş taslağı (öneri onayı)",
            items=[
                TransactionItem(
                    product_id=item["product_id"],
                    quantity=item["created_quantity"],
                    unit_price=item["unit_price"],
                    vat_rate=_product_vat_rate(db, cid, item["product_id"]),
                )
                for item in lines
            ],
        )
        result = _save("purchase", tx, db, cid, override, commit=False)
        created_purchases.append({
            "purchase_id": result["id"],
            "supplier_id": supplier_id,
            "status": result["status"],
            "final_total": _s(result["final_total"]),
            "lines": [{
                "product_id": item["product_id"],
                "requested_quantity": _s(item["requested_quantity"]),
                "created_quantity": _s(item["created_quantity"]),
                "moq": _s(item["moq"]),
                "unit_price": _s(item["unit_price"]),
            } for item in lines],
        })

    if idempotency_key is not None:
        try:
            db.execute(
                text(
                    """INSERT INTO purchase_draft_idempotency(
                    company_id,idempotency_key,request_fingerprint,purchase_ids,created_at)
                    VALUES(:cid,:key,:fingerprint,:ids,:now)"""
                ),
                {
                    "cid": cid,
                    "key": idempotency_key,
                    "fingerprint": fingerprint,
                    "ids": ",".join(str(item["purchase_id"]) for item in created_purchases),
                    "now": utcnow().isoformat(),
                },
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            replay = _draft_replay(db, cid, idempotency_key, fingerprint)
            if replay is not None:
                return {
                    "replayed": True,
                    "purchases": [{"purchase_id": pid} for pid in replay],
                    "skipped": [],
                }
            raise HTTPException(409, "Eşzamanlı taslak isteği çakıştı; tekrar deneyin")
    else:
        db.commit()
    return {"replayed": False, "purchases": created_purchases, "skipped": skipped}


@router.get("/purchase-comparison/dashboard")
def reorder_dashboard(
    request: Request,
    top: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Aggregates behind the reorder dashboard and the supplier comparison chart.

    Backend prepares the numbers; the frontend draws. Totals use each product's
    best offer at its evaluated quantity, so the figure is what the suggested
    drafts would actually cost at catalog prices (freight and other landed-cost
    components excluded, as the field name says).
    """
    cid = company_id(request)
    items = _reorder_candidates(db, cid)
    total_cost = ZERO
    by_supplier: dict[int, dict[str, Any]] = {}
    for item in items:
        best = item["best_supplier"]
        if best is None:
            continue
        line_cost = _num(best["catalog_cost_try_total"]) or ZERO
        total_cost += line_cost
        bucket = by_supplier.setdefault(
            best["supplier_id"],
            {
                "supplier_id": best["supplier_id"],
                "supplier_name": best["supplier_name"],
                "line_count": 0,
                "total_cost_try": ZERO,
                "lead_time_days": best["lead_time_days"],
            },
        )
        bucket["line_count"] += 1
        bucket["total_cost_try"] += line_cost

    largest = sorted(
        items, key=lambda item: (_num(item["deficit"]) or ZERO), reverse=True
    )[:top]
    return {
        "below_reorder_point_count": len(items),
        "unpriced_count": sum(1 for item in items if item["best_supplier"] is None),
        "total_suggested_cost_try": _s(total_cost),
        "demand_window_days": DEMAND_WINDOW_DAYS,
        "on_order_basis": ON_ORDER_BASIS,
        "by_supplier": [
            {**bucket, "total_cost_try": _s(bucket["total_cost_try"])}
            for bucket in sorted(
                by_supplier.values(), key=lambda b: (-b["total_cost_try"], b["supplier_id"])
            )
        ],
        "largest_deficits": [{
            "product_id": item["product_id"],
            "product_name": item["product_name"],
            "deficit": item["deficit"],
            "suggested_quantity": item["suggested_quantity"],
            "best_supplier_id": None if item["best_supplier"] is None else item["best_supplier"]["supplier_id"],
            "catalog_cost_try_total": None if item["best_supplier"] is None else item["best_supplier"]["catalog_cost_try_total"],
        } for item in largest],
    }


# ---- Dashboard aggregates: spend history + comparison scorecard ------------
# Both endpoints are READ-ONLY reporting seams for the purchasing dashboard.
# They add no business rule of their own: the spend figures are sums over the
# company's own purchase documents, and the scorecard is a tally over offers
# produced by the existing ``_build_offers`` engine. They live under
# /api/purchase-comparison so they inherit the ``purchases`` RBAC rule that
# already guards every cost/margin read in this module, and every query is
# scoped by the ``company_id`` resolved from the session.

# Spend excludes drafts and cancellations. This matters more here than
# elsewhere: the reorder engine CREATES draft purchases, so counting them as
# spend would let the dashboard inflate itself with orders nobody approved.
SPEND_EXCLUDED_STATUSES = ("draft", "cancelled")
SPEND_STATUS_SQL = "COALESCE(pu.status,'completed') NOT IN ('draft','cancelled')"


def _spend_scope(
    cid: int, date_from: date | None, date_to: date | None
) -> tuple[str, dict[str, Any], str]:
    """``(extra AND-conditions, params, normalised-date expression)``.

    The tenant predicate is deliberately NOT returned here: every caller spells
    ``WHERE pu.company_id=:cid`` out in its own literal SQL and appends this
    fragment after it. That keeps the scoping visible to the static guard in
    ``tests/test_tenant_scoping_guard.py``, which reads the string literals and
    cannot see a predicate hidden inside an interpolated fragment.

    The date expression is the module-wide one, so a legacy ``DD.MM.YYYY``
    purchase_date is bucketed in the same month as an ISO one instead of
    silently falling out of the range filter.
    """
    from .transactions import _normalized_date_sql  # local import: avoids a cycle

    normalized = _normalized_date_sql("pu.purchase_date")
    conditions = [SPEND_STATUS_SQL]
    params: dict[str, Any] = {"cid": cid}
    if date_from is not None:
        conditions.append(f"{normalized}>=:df")
        params["df"] = date_from.isoformat()
    if date_to is not None:
        conditions.append(f"{normalized}<=:dt")
        params["dt"] = date_to.isoformat()
    return " AND " + " AND ".join(conditions), params, normalized


def _share(part: Decimal, whole: Decimal) -> str | None:
    """``part`` as a percentage of ``whole``; None when there is no base."""
    if whole <= ZERO:
        return None
    return _s(percentage(part / whole * HUNDRED))


def _top_with_other(
    rows: list[dict[str, Any]], total: Decimal, top: int
) -> tuple[list[dict[str, Any]], Decimal]:
    """Keep the ``top`` largest rows and return the remainder as one figure.

    The leftover is derived from the authoritative ``total`` rather than from the
    tail of ``rows``, so it stays correct even when the query itself was capped —
    and a truncated breakdown still reconciles to the reported total instead of
    silently losing the tail and reading as "this is everything".
    """
    kept = rows[:top]
    rest = total - sum((row["_total"] for row in kept), ZERO)
    if rest < ZERO:
        rest = ZERO
    for row in kept:
        row["share_percent"] = _share(row["_total"], total)
        row["total_try"] = _s(row.pop("_total"))
    return kept, rest


@router.get("/purchase-comparison/spend-analytics")
def purchase_spend_analytics(
    request: Request,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    months: int = Query(default=12, ge=1, le=36),
    top: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Purchase spend: monthly trend, supplier, product and category breakdown.

    Every amount is VAT-INCLUSIVE and computed server-side; the frontend only
    formats what it receives. Two different bases are reported on purpose and
    must not be mixed:

      * ``total_try`` / ``monthly`` / ``by_supplier`` use ``purchases.final_total``
        — the authoritative document total, AFTER any document-level discount.
      * ``by_product`` / ``by_category`` use ``purchase_items.line_total``, which
        is per-line and therefore sits BEFORE the document-level discount.

    ``line_total_try`` is returned as the base the line-level shares are taken
    against, so the two never get compared to the wrong denominator.
    """
    cid = company_id(request)
    scope, params, normalized = _spend_scope(cid, date_from, date_to)
    month_expr = f"substr({normalized},1,7)"

    header = db.execute(
        text(
            f"""SELECT COALESCE(SUM(pu.final_total),0) total,COUNT(*) cnt,
            COUNT(DISTINCT pu.supplier_id) suppliers FROM purchases pu
            WHERE pu.company_id=:cid{scope}"""
        ),
        params,
    ).mappings().one()
    total = money(header["total"])
    purchase_count = int(header["cnt"])

    monthly_rows = db.execute(
        text(
            f"""SELECT {month_expr} AS month,COALESCE(SUM(pu.final_total),0) total,
            COUNT(*) cnt FROM purchases pu
            WHERE pu.company_id=:cid{scope}
            GROUP BY {month_expr} ORDER BY month DESC LIMIT :months"""
        ),
        {**params, "months": months},
    ).mappings().all()

    supplier_rows = db.execute(
        text(
            f"""SELECT s.id supplier_id,s.name supplier_name,
            COALESCE(SUM(pu.final_total),0) total,COUNT(*) cnt,
            MAX({normalized}) last_purchase_date
            FROM purchases pu
            JOIN suppliers s ON s.id=pu.supplier_id AND s.company_id=pu.company_id
            WHERE pu.company_id=:cid{scope}
            GROUP BY s.id,s.name ORDER BY total DESC,s.id LIMIT :top"""
        ),
        {**params, "top": top},
    ).mappings().all()
    suppliers = [{
        "supplier_id": int(row["supplier_id"]),
        "supplier_name": row["supplier_name"],
        "purchase_count": int(row["cnt"]),
        "last_purchase_date": None if row["last_purchase_date"] is None else str(row["last_purchase_date"]),
        "_total": money(row["total"]),
    } for row in supplier_rows]
    by_supplier, other_supplier_total = _top_with_other(suppliers, total, top)

    # The line base is summed in SQL rather than from the rows below: the
    # breakdown is capped at ``top`` (a catalogue can hold tens of thousands of
    # distinct product names), so the denominator has to come from the full set.
    line_total = money(db.execute(
        text(
            f"""SELECT COALESCE(SUM(pi.line_total),0) FROM purchase_items pi
            JOIN purchases pu ON pu.id=pi.purchase_id
            WHERE pu.company_id=:cid{scope}"""
        ),
        params,
    ).scalar_one())

    # Rows are grouped by name because purchase_items.product_id is nullable
    # (ad-hoc lines); the catalogue id is exposed only when the whole group maps
    # to exactly one product, so the UI never links a name to an ambiguous record.
    product_rows = db.execute(
        text(
            f"""SELECT pi.product_name,
            CASE WHEN COUNT(DISTINCT pi.product_id)=1 THEN MIN(pi.product_id) END AS product_id,
            COALESCE(SUM(pi.quantity),0) quantity,COALESCE(SUM(pi.line_total),0) total
            FROM purchase_items pi JOIN purchases pu ON pu.id=pi.purchase_id
            WHERE pu.company_id=:cid{scope}
            GROUP BY pi.product_name ORDER BY total DESC,pi.product_name LIMIT :top"""
        ),
        {**params, "top": top},
    ).mappings().all()
    products = [{
        "product_id": None if row["product_id"] is None else int(row["product_id"]),
        "product_name": row["product_name"],
        "quantity": _s(quantize_quantity(row["quantity"])),
        "_total": money(row["total"]),
    } for row in product_rows]
    by_product, other_product_total = _top_with_other(products, line_total, top)

    # Category comes from the product card, so ad-hoc lines (product_id NULL) and
    # products with no category collapse into one explicit "uncategorised" bucket
    # (category=null) instead of disappearing from the breakdown. Unlike the two
    # above this query is not capped: category cardinality is bounded by the
    # catalogue's distinct labels, and whitespace variants of the same label have
    # to be merged BEFORE ranking or a cap would cut the wrong bucket.
    category_rows = db.execute(
        text(
            f"""SELECT p.category,COALESCE(SUM(pi.line_total),0) total
            FROM purchase_items pi JOIN purchases pu ON pu.id=pi.purchase_id
            LEFT JOIN products p ON p.id=pi.product_id AND p.company_id=:cid
            WHERE pu.company_id=:cid{scope}
            GROUP BY p.category"""
        ),
        params,
    ).mappings().all()
    merged: dict[str | None, Decimal] = {}
    for row in category_rows:
        key = (str(row["category"]).strip() or None) if row["category"] is not None else None
        merged[key] = merged.get(key, ZERO) + money(row["total"])
    categories = [
        {"category": key, "_total": value}
        for key, value in sorted(merged.items(), key=lambda kv: (-kv[1], kv[0] or ""))
    ]
    by_category, other_category_total = _top_with_other(categories, line_total, top)

    return {
        "date_from": None if date_from is None else date_from.isoformat(),
        "date_to": None if date_to is None else date_to.isoformat(),
        "excluded_statuses": list(SPEND_EXCLUDED_STATUSES),
        "vat_included": True,
        "totals": {
            "total_try": _s(total),
            "line_total_try": _s(line_total),
            "purchase_count": purchase_count,
            "supplier_count": int(header["suppliers"]),
            "average_purchase_try": _s(money(total / purchase_count)) if purchase_count else None,
        },
        "monthly": [{
            "month": str(row["month"]),
            "total_try": _s(money(row["total"])),
            "purchase_count": int(row["cnt"]),
        } for row in reversed(monthly_rows)],
        # Each breakdown carries both the remainder amount and a decided flag.
        # "Is there a tail worth showing?" is a money comparison, so the server
        # settles it on the Decimal — a client re-deriving it from the string
        # would have to parse money into a float to compare it.
        "by_supplier": by_supplier,
        "other_supplier_total_try": _s(other_supplier_total),
        "has_other_suppliers": other_supplier_total > ZERO,
        "by_product": by_product,
        "other_product_total_try": _s(other_product_total),
        "has_other_products": other_product_total > ZERO,
        "by_category": by_category,
        "other_category_total_try": _s(other_category_total),
        "has_other_categories": other_category_total > ZERO,
    }


@router.get("/purchase-comparison/supplier-scorecard")
def supplier_scorecard(
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
    alt_threshold: Decimal = Query(default=DEFAULT_ALT_THRESHOLD, ge=0, le=100),
    db: Session = Depends(get_db),
):
    """Who wins and who is dearest across the comparison engine's offers.

    A pure tally over ``_build_offers`` — the same offers, ranked the same way,
    that ``GET /purchase-comparison`` returns per product. No quantity is
    supplied, so list prices with their flat discount decide, exactly as on the
    comparison grid's default view.

    ``best_count`` / ``worst_count`` only count products where at least TWO
    suppliers have a rankable price: being the cheapest of one offer is not a
    win, and calling a sole supplier "the most expensive" would be nonsense.
    The scan is bounded by ``limit`` and reports ``truncated`` so a partial
    tally is never mistaken for the whole catalogue.
    """
    cid = company_id(request)
    # The candidate predicate is repeated in full in both statements rather than
    # shared through an interpolated fragment. The duplication is deliberate:
    # ``tests/test_tenant_scoping_guard.py`` reads string literals, so a
    # ``company_id`` hidden behind an f-string placeholder is invisible to it and
    # these two queries would sit outside the guard entirely.
    params: dict[str, Any] = {"cid": cid, "limit": limit}
    candidate_count = int(db.execute(
        text(
            """SELECT COUNT(*) FROM products p
            WHERE p.company_id=:cid AND (
              EXISTS(SELECT 1 FROM supplier_product_prices spp
                     WHERE spp.company_id=:cid AND spp.product_id=p.id AND spp.is_active=TRUE)
              OR EXISTS(SELECT 1 FROM purchase_items pi JOIN purchases pu ON pu.id=pi.purchase_id
                        AND pu.company_id=:cid
                        WHERE pi.company_id=:cid AND pi.product_id=p.id)
            )"""
        ),
        params,
    ).scalar_one())
    rows = db.execute(
        text(
            """SELECT p.id,p.name FROM products p
            WHERE p.company_id=:cid AND (
              EXISTS(SELECT 1 FROM supplier_product_prices spp
                     WHERE spp.company_id=:cid AND spp.product_id=p.id AND spp.is_active=TRUE)
              OR EXISTS(SELECT 1 FROM purchase_items pi JOIN purchases pu ON pu.id=pi.purchase_id
                        AND pu.company_id=:cid
                        WHERE pi.company_id=:cid AND pi.product_id=p.id)
            )
            ORDER BY p.name, p.id LIMIT :limit"""
        ),
        params,
    ).mappings().all()
    product_ids = [int(row["id"]) for row in rows]
    manual = _manual_prices(db, cid, product_ids)
    purchases = _purchase_stats(db, cid, product_ids)

    scores: dict[int, dict[str, Any]] = {}
    comparable = 0
    single_source = 0
    unpriced = 0
    for pid in product_ids:
        offers = _build_offers(
            db, cid, pid, manual.get(pid, {}), purchases.get(pid, {}), alt_threshold
        )
        rankable = [o for o in offers if o["effective_price_in_try"] is not None]
        for offer in offers:
            bucket = scores.setdefault(offer["supplier_id"], {
                "supplier_id": offer["supplier_id"],
                "supplier_name": offer["supplier_name"],
                "product_count": 0,
                "priced_product_count": 0,
                "best_count": 0,
                "worst_count": 0,
                "alternative_count": 0,
            })
            bucket["product_count"] += 1
            if offer["effective_price_in_try"] is not None:
                bucket["priced_product_count"] += 1
            if offer["faster_alternative"]:
                bucket["alternative_count"] += 1
        if not rankable:
            unpriced += 1
            continue
        if len(rankable) < 2:
            single_source += 1
            continue
        comparable += 1
        # ``_build_offers`` returns the rankable offers cheapest-first, so the
        # ends of that list are the cheapest and the dearest supplier.
        scores[rankable[0]["supplier_id"]]["best_count"] += 1
        scores[rankable[-1]["supplier_id"]]["worst_count"] += 1

    return {
        "evaluated_product_count": len(product_ids),
        "candidate_product_count": candidate_count,
        "truncated": candidate_count > len(product_ids),
        "comparable_product_count": comparable,
        "single_source_product_count": single_source,
        "unpriced_product_count": unpriced,
        "alt_threshold_percent": str(alt_threshold),
        "suppliers": sorted(
            scores.values(),
            key=lambda row: (-row["best_count"], -row["priced_product_count"], row["supplier_id"]),
        ),
    }


def _price_row(db: Session, cid: int, price_id: int) -> dict[str, Any]:
    row = db.execute(
        text(
            """SELECT spp.*,s.name supplier_name,p.name product_name
            FROM supplier_product_prices spp
            JOIN suppliers s ON s.id=spp.supplier_id AND s.company_id=spp.company_id
            JOIN products p ON p.id=spp.product_id AND p.company_id=spp.company_id
            WHERE spp.id=:id AND spp.company_id=:cid"""
        ),
        {"id": price_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Tedarikçi fiyatı bulunamadı")
    return dict(row)


def _capture_history(db: Session, cid: int, row: dict[str, Any]) -> None:
    """Append a MANUAL price observation with its TRY snapshot for the graph."""
    price = decimal_value(row["price"], field_name="Fiyat")
    in_try = to_try(price, resolve_rate_to_try(db, cid, str(row["currency"])))
    db.execute(
        text(
            """INSERT INTO supplier_price_history(
            company_id,supplier_id,product_id,price,currency,price_in_try,source,note,captured_at)
            VALUES(:cid,:supplier_id,:product_id,:price,:currency,:price_in_try,'MANUAL',:note,:now)"""
        ),
        {
            "cid": cid, "supplier_id": row["supplier_id"], "product_id": row["product_id"],
            "price": price, "currency": row["currency"],
            "price_in_try": in_try, "note": row.get("note"), "now": utcnow(),
        },
    )


def _replace_tiers(db: Session, cid: int, price_id: int, tiers: list[Any] | None) -> None:
    """Rewrite a price's discount ladder. ``None`` leaves the existing one alone."""
    if tiers is None:
        return
    db.execute(
        text(
            "DELETE FROM supplier_price_discount_tiers "
            "WHERE company_id=:cid AND supplier_price_id=:pid"
        ),
        {"cid": cid, "pid": price_id},
    )
    for tier in tiers:
        db.execute(
            text(
                """INSERT INTO supplier_price_discount_tiers(
                company_id,supplier_price_id,min_quantity,discount_percent)
                VALUES(:cid,:pid,:min_quantity,:discount_percent)"""
            ),
            {
                "cid": cid,
                "pid": price_id,
                "min_quantity": tier.min_quantity,
                "discount_percent": tier.discount_percent,
            },
        )


def _price_payload_params(payload: SupplierPriceFields | SupplierPriceUpdate) -> dict[str, Any]:
    """Column binds for a supplier price write (``tiers`` is stored separately)."""
    data = payload.model_dump()
    data.pop("tiers", None)
    return data


def _history_cursor(captured_at: Any, row_id: int) -> str:
    payload = json.dumps(
        {"captured_at": str(captured_at), "id": row_id}, separators=(",", ":")
    ).encode()
    return urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_history_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(urlsafe_b64decode(padded).decode())
        captured_at = datetime.fromisoformat(str(payload["captured_at"]))
        row_id = int(payload["id"])
        if row_id <= 0:
            raise ValueError
        return captured_at, row_id
    except (
        Base64Error, KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError
    ) as exc:
        raise HTTPException(422, "Geçersiz fiyat geçmişi imleci") from exc


@router.get("/supplier-prices/history", response_model=SupplierPriceHistoryResponse)
def supplier_price_history(
    request: Request,
    supplier_id: int = Query(gt=0),
    product_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=500),
    db: Session = Depends(get_db),
):
    """Return tenant-scoped, newest-first price observations for a supplier."""
    cid = company_id(request)
    _require_supplier(db, cid, supplier_id)
    filters: list[str] = []
    params: dict[str, Any] = {
        "cid": cid, "supplier_id": supplier_id, "fetch_limit": limit + 1
    }
    if product_id is not None:
        filters.append("sph.product_id=:product_id")
        params["product_id"] = product_id
    if cursor is not None:
        cursor_at, cursor_id = _decode_history_cursor(cursor)
        filters.append(
            "(sph.captured_at<:cursor_at OR "
            "(sph.captured_at=:cursor_at AND sph.id<:cursor_id))"
        )
        params.update({"cursor_at": cursor_at, "cursor_id": cursor_id})
    extra_filter = "" if not filters else "AND " + " AND ".join(filters)
    rows = db.execute(
        text(
            f"""SELECT sph.id,sph.product_id,p.name product_name,sph.price,
            sph.currency,sph.price_in_try,sph.source,sph.note,sph.captured_at
            FROM supplier_price_history sph
            JOIN products p ON p.id=sph.product_id AND p.company_id=sph.company_id
            WHERE sph.company_id=:cid AND sph.supplier_id=:supplier_id
            {extra_filter}
            ORDER BY sph.captured_at DESC,sph.id DESC
            LIMIT :fetch_limit"""
        ),
        params,
    ).mappings().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        _history_cursor(page[-1]["captured_at"], int(page[-1]["id"]))
        if has_more and page else None
    )
    return {
        "supplier_id": supplier_id,
        "items": [
            {
                **dict(row),
                "price": _s(row["price"]),
                "price_in_try": _s(row["price_in_try"]),
                "captured_at": str(row["captured_at"]),
            }
            for row in page
        ],
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


@router.post("/supplier-prices", status_code=201)
def create_supplier_price(payload: SupplierPriceCreate, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    _require_product(db, cid, payload.product_id)
    _require_supplier(db, cid, payload.supplier_id)
    user = getattr(request.state, "user", {}) or {}
    now = utcnow()
    try:
        price_id = db.execute(
            text(
                """INSERT INTO supplier_product_prices(
                company_id,supplier_id,product_id,price,currency,moq,lead_time_days,
                discount_percent,supplier_stock,price_includes_vat,note,is_active,
                created_by,created_at,updated_at)
                VALUES(:cid,:supplier_id,:product_id,:price,:currency,:moq,:lead_time_days,
                :discount_percent,:supplier_stock,:price_includes_vat,:note,TRUE,
                :created_by,:now,:now) RETURNING id"""
            ),
            {
                "cid": cid,
                **_price_payload_params(payload),
                "created_by": int(user["id"]) if user.get("id") else None,
                "now": now,
            },
        ).scalar_one()
        _replace_tiers(db, cid, int(price_id), payload.tiers)
        after = _price_row(db, cid, int(price_id))
        _capture_history(db, cid, after)
        record_change(db, request, company_id=cid, entity_type="supplier_price",
                      entity_id=int(price_id), action="create", before=None, after=after)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Bu ürün için bu tedarikçinin fiyatı zaten kayıtlı") from exc
    return _serialize_price(after, _price_tiers(db, cid, [int(price_id)]).get(int(price_id), []))


@router.put("/supplier-prices/{price_id}")
def update_supplier_price(price_id: int, payload: SupplierPriceUpdate, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    before = _price_row(db, cid, price_id)
    sent = payload.model_fields_set
    values = _price_payload_params(payload)
    scalar_fields = sorted(sent - {"tiers"})
    if scalar_fields:
        assignments = ",".join(f"{field}=:{field}" for field in scalar_fields)
        db.execute(
            text(
                f"""UPDATE supplier_product_prices
                SET {assignments},updated_at=:now
                WHERE id=:id AND company_id=:cid"""
            ),
            {
                **{field: values[field] for field in scalar_fields},
                "id": price_id,
                "cid": cid,
                "now": utcnow(),
            },
        )
    if "tiers" in sent:
        _replace_tiers(db, cid, price_id, payload.tiers)
    after = _price_row(db, cid, price_id)
    _capture_history(db, cid, after)
    record_change(db, request, company_id=cid, entity_type="supplier_price",
                  entity_id=price_id, action="update", before=before, after=after)
    db.commit()
    return _serialize_price(after, _price_tiers(db, cid, [int(price_id)]).get(int(price_id), []))


@router.delete("/supplier-prices/{price_id}", status_code=204)
def delete_supplier_price(price_id: int, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    before = _price_row(db, cid, price_id)
    db.execute(
        text(
            "DELETE FROM supplier_price_discount_tiers "
            "WHERE company_id=:cid AND supplier_price_id=:id"
        ),
        {"id": price_id, "cid": cid},
    )
    db.execute(
        text("DELETE FROM supplier_product_prices WHERE id=:id AND company_id=:cid"),
        {"id": price_id, "cid": cid},
    )
    record_change(db, request, company_id=cid, entity_type="supplier_price",
                  entity_id=price_id, action="delete", before=before, after=None)
    db.commit()


def _serialize_price(
    row: dict[str, Any], tiers: list[tuple[Decimal, Decimal]] | None = None
) -> dict[str, Any]:
    out = dict(row)
    for key in ("price", "moq", "discount_percent", "supplier_stock"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    for key in ("created_at", "updated_at"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    if out.get("price_includes_vat") is not None:
        out["price_includes_vat"] = bool(out["price_includes_vat"])
    out["tiers"] = [
        {"min_quantity": str(min_quantity), "discount_percent": str(discount)}
        for min_quantity, discount in (tiers or [])
    ]
    return out


# ---- Exchange rates --------------------------------------------------------
@router.get("/exchange-rates")
def get_exchange_rates(request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    effective = resolve_rates(db, cid)
    overrides = db.execute(
        text("SELECT currency,rate_to_try,note,updated_at FROM company_exchange_rate_overrides WHERE company_id=:cid"),
        {"cid": cid},
    ).mappings().all()
    override_map = {str(r["currency"]): r for r in overrides}
    return {
        "effective": {code: serialize_rate(rate) for code, rate in effective.items()},
        "overrides": {
            str(r["currency"]): {"rate_to_try": str(r["rate_to_try"]), "note": r["note"],
                                 "updated_at": str(r["updated_at"]) if r["updated_at"] else None}
            for r in override_map.values()
        },
    }


@router.put("/exchange-rates/override")
def set_exchange_rate_override(payload: ExchangeRateOverrideWrite, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    user = getattr(request.state, "user", {}) or {}
    now = utcnow()
    existing = db.execute(
        text("SELECT id FROM company_exchange_rate_overrides WHERE company_id=:cid AND currency=:c"),
        {"cid": cid, "c": payload.currency},
    ).first()
    if existing is not None:
        db.execute(
            text("UPDATE company_exchange_rate_overrides SET rate_to_try=:r,note=:note,updated_by=:uid,updated_at=:now WHERE id=:id AND company_id=:cid"),
            {"r": payload.rate_to_try, "note": payload.note, "uid": user.get("id"), "now": now, "id": existing[0], "cid": cid},
        )
    else:
        db.execute(
            text(
                """INSERT INTO company_exchange_rate_overrides(company_id,currency,rate_to_try,note,updated_by,updated_at)
                VALUES(:cid,:c,:r,:note,:uid,:now)"""
            ),
            {"cid": cid, "c": payload.currency, "r": payload.rate_to_try, "note": payload.note, "uid": user.get("id"), "now": now},
        )
    db.commit()
    return {"currency": payload.currency, "rate_to_try": str(payload.rate_to_try)}


@router.post("/exchange-rates/refresh")
def refresh_exchange_rates(request: Request, db: Session = Depends(get_db)):
    """Fetch today's TCMB rates and store them.

    TCMB being unreachable (offline / LAN install) or returning XML we cannot
    parse is NOT an error: the company simply keeps using its manual overrides.
    Both cases return a 200 degraded response ({"updated": {}, "source":
    "MANUAL_OVERRIDE", ...}) instead of a 5xx, so the caller — and any scheduled
    poller — never treats a missing internet connection as a failed request. The
    comparison itself already tolerates a missing rate (price_in_try is null and
    the offer sorts last), so nothing downstream breaks.
    """
    company_id(request)  # tenant guard (writes to a global table, but require auth+company)
    from datetime import date as _date

    from ..exchange_rates import fetch_tcmb_rates, store_rates
    try:
        rates = fetch_tcmb_rates()
    except Exception:  # noqa: BLE001 — any network/XML failure degrades, never fails
        rates = {}
    if not rates:  # unreachable host, empty response, or unparseable XML
        return {
            "updated": {},
            "source": "MANUAL_OVERRIDE",
            "warning": "TCMB unavailable; manual overrides remain active",
        }
    store_rates(db, rates, _date.today())
    db.commit()
    return {
        "updated": {code: str(value) for code, value in rates.items()},
        "source": "TCMB",
    }
