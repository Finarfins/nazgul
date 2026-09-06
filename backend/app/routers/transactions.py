from datetime import date, timedelta
from decimal import Decimal
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import bindparam, text, select
from sqlalchemy.orm import Session

from ..money import (
    HUNDRED,
    ZERO_MONEY,
    apply_global_discount,
    apply_global_discount_amount,
    allocate_vat_inclusive_document_discount,
    money,
    percentage,
    persisted_percentage,
    quantity,
)
from ..activity_log import diff_details, format_money_tr, log_request_activity
from ..auth import has_permission, utcnow
from ..business_time import business_today
from ..change_history import record_change
from ..harvest_scheduling import resolve_harvest_due_date
from ..movement_references import validate_payment_reference
from ..receivables_engine import calculate_receivables
from ..service_receivable_engine import service_receivable_net_total
from ..config import settings
from ..payment_allocation_engine import allocate_payment, ensure_charge_allocation_enabled

from ..db import get_db
from ..parti_defteri import (
    _hareket_notu,
    _parti_ac,
    _parti_geri_al,
    _parti_tuket,
)
from ..schemas import TransactionCreate
from ..tenancy import branches, company_id
from ..inventory import warehouses, default_warehouse, adjust_warehouse_stock
from ..finance_engine import sync_payment_finance, remove_payment_finance, validate_payment_account
from ..company_policies import (
    PolicyOverrideContext,
    enforce_known_violation,
    get_policy_settings,
    negative_stock_allowed,
    override_context,
    record_policy_overrides,
    stock_policy_message,
)
from ..document_engine import (
    DOCUMENT_STATUSES,
    PAYMENT_METHODS,
    PAYMENT_TERMS,
    SALES_IMPORT_NOTE,
    STOCK_STATUSES,
    next_document_no,
)

router = APIRouter(tags=["transactions"])
logger = logging.getLogger(__name__)

# Aktivite özetlerinde gösterilen belge alanları. Denetim kaydı bütün satırın
# kopyası değil, kararın özüdür.
SALE_AUDIT_FIELDS = (
    "document_no",
    "customer_id",
    "order_date",
    "final_total",
    "status",
    "payment_method",
    "payment_term",
    "due_date",
    "note",
)


def _customer_name(db: Session, cid: int, customer_id: int | None) -> str:
    """Aktivite özeti için müşteri adı; bulunamazsa panelde boş kalmaz."""
    if customer_id is None:
        return "bilinmiyor"
    row = db.execute(
        text("SELECT name FROM customers WHERE id=:id AND company_id=:cid"),
        {"id": int(customer_id), "cid": cid},
    ).first()
    return str(row[0]) if row and row[0] else f"#{customer_id}"


def _config(kind: str):
    if kind == "sale":
        return {
            "head": "orders",
            "items": "order_items",
            "entity": "customers",
            "entity_id": "customer_id",
            "date": "order_date",
            "item_fk": "order_id",
            "stock_sign": -1,
            "prefix": "SAT",
        }
    return {
        "head": "purchases",
        "items": "purchase_items",
        "entity": "suppliers",
        "entity_id": "supplier_id",
        "date": "purchase_date",
        "item_fk": "purchase_id",
        "stock_sign": 1,
        "prefix": "ALI",
    }


def _warehouse(db: Session, cid: int, wid: int | None) -> int:
    wid = wid or default_warehouse(db, cid)
    exists = db.execute(
        select(warehouses.c.id).where(
            warehouses.c.id == wid,
            warehouses.c.company_id == cid,
            warehouses.c.is_active == True,
        )
    ).first()
    if not exists:
        raise HTTPException(400, "Depo bulunamadı")
    return int(wid)


def _branch(db: Session, cid: int, bid: int | None) -> int | None:
    """Şube AYNI firmaya ait olmalı.

    ``branches`` ile ``orders``/``purchases`` arasında BİLEŞİK yabancı anahtar
    yok (bkz. tenancy.py), yani veritabanı çapraz kiracı bağı engellemiyor.
    Kontrol atlanırsa B firmasının belgesi A firmasının şubesine referans verir.
    Yabancı kimlik 404 döner: 403 "var ama sana kapalı" bilgisini sızdırırdı.
    """
    if bid is None:
        return None
    exists = db.execute(
        select(branches.c.id).where(
            branches.c.id == bid,
            branches.c.company_id == cid,
        )
    ).first()
    if not exists:
        raise HTTPException(404, "Şube bulunamadı")
    return int(bid)


def _credit_exposure(
    db: Session,
    *,
    cid: int,
    customer_id: int,
    final_total: Decimal,
    paid_amount: Decimal,
    status: str,
    transaction_id: int | None,
) -> tuple[Decimal, Decimal, Decimal]:
    lock_suffix = " FOR UPDATE" if db.get_bind().dialect.name == "postgresql" else ""
    customer = db.execute(
        text(
            "SELECT opening_balance,risk_limit FROM customers "
            "WHERE id=:id AND company_id=:cid" + lock_suffix
        ),
        {"id": customer_id, "cid": cid},
    ).mappings().first()
    if not customer:
        raise HTTPException(400, "Cari bulunamadı")

    common = {"cid": cid, "customer_id": customer_id, "current_id": transaction_id}
    current_orders = money(
        db.execute(
            text(
                "SELECT COALESCE(SUM(final_total),0) FROM orders "
                "WHERE company_id=:cid AND customer_id=:customer_id "
                "AND COALESCE(status,'completed') NOT IN ('draft','cancelled')"
            ),
            common,
        ).scalar()
    )
    current_payments = money(
        db.execute(
            text(
                "SELECT COALESCE(SUM(amount),0) FROM payments "
                "WHERE company_id=:cid AND entity_type='customer' AND entity_id=:customer_id"
            ),
            common,
        ).scalar()
    )
    current_service_receivables = service_receivable_net_total(
        db, cid, customer_id
    )
    other_orders = money(
        db.execute(
            text(
                "SELECT COALESCE(SUM(final_total),0) FROM orders "
                "WHERE company_id=:cid AND customer_id=:customer_id "
                "AND COALESCE(status,'completed') NOT IN ('draft','cancelled') "
                "AND (CAST(:current_id AS INTEGER) IS NULL OR id<>CAST(:current_id AS INTEGER))"
            ),
            common,
        ).scalar()
    )
    other_payments = money(
        db.execute(
            text(
                "SELECT COALESCE(SUM(amount),0) FROM payments "
                "WHERE company_id=:cid AND entity_type='customer' AND entity_id=:customer_id "
                "AND (CAST(:current_id AS INTEGER) IS NULL OR NOT (reference_type='order' AND reference_id=CAST(:current_id AS INTEGER)))"
            ),
            common,
        ).scalar()
    )
    opening = money(customer["opening_balance"])
    risk_limit = money(customer["risk_limit"])
    current_balance = money(
        opening + current_orders + current_service_receivables - current_payments
    )
    new_outstanding = (
        money(final_total - paid_amount)
        if status not in {"draft", "cancelled"}
        else ZERO_MONEY
    )
    projected_balance = money(
        opening
        + other_orders
        + current_service_receivables
        - other_payments
        + new_outstanding
    )
    return risk_limit, current_balance, projected_balance


def _totals(db: Session, payload: TransactionCreate, cid: int):
    product_ids = list(dict.fromkeys(int(item.product_id) for item in payload.items))
    products = {}
    if product_ids:
        statement = text(
            "SELECT id,name FROM products WHERE company_id=:cid AND id IN :product_ids"
        ).bindparams(bindparam("product_ids", expanding=True))
        products = {
            int(row["id"]): row
            for row in db.execute(
                statement,
                {"cid": cid, "product_ids": product_ids},
            ).mappings().all()
        }

    rows = []
    subtotal = vat_total = gross_total = ZERO_MONEY
    for item in payload.items:
        product = products.get(int(item.product_id))
        if not product:
            raise HTTPException(400, f"Ürün bulunamadı: {item.product_id}")

        raw_total = money(quantity(item.quantity) * money(item.unit_price))
        line_discount = money(
            raw_total * persisted_percentage(item.discount_percent) / HUNDRED
        )
        line_total = money(raw_total - line_discount)
        line_subtotal = (
            money(line_total / (Decimal("1") + Decimal(item.vat_rate) / HUNDRED))
            if item.vat_rate
            else line_total
        )
        line_vat = money(line_total - line_subtotal)
        rows.append((product, item, line_subtotal, line_vat, line_total, line_discount))
        subtotal += line_subtotal
        vat_total += line_vat
        gross_total += line_total

    if payload.discount_amount is None:
        subtotal_after_discount, vat_after_discount, global_discount, final_total = (
            apply_global_discount(
                subtotal,
                vat_total,
                gross_total,
                persisted_percentage(payload.discount_percent),
            )
        )
    else:
        if money(payload.discount_amount) > money(gross_total):
            raise HTTPException(
                400,
                "Belge iskontosu belge toplamını aşamaz; fiyat farkı için iskonto kullanılamaz",
            )
        subtotal_after_discount, vat_after_discount, global_discount, final_total = (
            apply_global_discount_amount(
                subtotal,
                vat_total,
                gross_total,
                payload.discount_amount,
            )
        )
        payload.discount_percent = (
            percentage(global_discount * HUNDRED / gross_total)
            if gross_total
            else percentage(0)
        )

    # Belge iskontosu başlıkta tek başına bırakılırsa satır bazlı ürün/KDV
    # raporları belge başlığıyla mutabık kalmaz. Kesin tutarı iskonto öncesi
    # satır brütlerine largest-remainder yöntemiyle dağıt; böylece kuruşlar
    # dahil hem satır toplamı hem satır KDV toplamı başlığa eşit olur.
    allocated_rows = allocate_vat_inclusive_document_discount(
        [row[4] for row in rows],
        [row[1].vat_rate for row in rows],
        global_discount,
    )
    discounted_rows = []
    for row, allocated in zip(rows, allocated_rows, strict=True):
        product, item, _line_subtotal, _line_vat, line_total, line_discount = row
        discounted_rows.append(
            (
                product,
                item,
                allocated.subtotal,
                allocated.tax,
                allocated.total,
                money(line_discount + allocated.document_discount),
            )
        )

    subtotal_after_discount = money(sum((row[2] for row in discounted_rows), ZERO_MONEY))
    vat_after_discount = money(sum((row[3] for row in discounted_rows), ZERO_MONEY))
    if money(sum((row[4] for row in discounted_rows), ZERO_MONEY)) != final_total:
        raise ValueError("Satır toplamları belge toplamıyla mutabık değil")
    if money(subtotal_after_discount + vat_after_discount) != final_total:
        raise ValueError("Satır KDV toplamı belge başlığıyla mutabık değil")
    return (
        discounted_rows,
        subtotal_after_discount,
        vat_after_discount,
        money(gross_total),
        global_discount,
        final_total,
    )


def _sync_payment(kind: str, transaction_id: int, payload: TransactionCreate, db: Session, cid: int):
    reference_type = "order" if kind == "sale" else "purchase"
    entity_type = "customer" if kind == "sale" else "supplier"
    validate_payment_reference(
        db,
        cid,
        entity_type,
        payload.entity_id,
        reference_type,
        transaction_id,
    )
    old_payments = db.execute(
        text(
            "SELECT id FROM payments WHERE company_id=:cid "
            "AND reference_type=:rt AND reference_id=:rid ORDER BY id"
            + (" FOR UPDATE" if db.get_bind().dialect.name == "postgresql" else "")
        ),
        {"cid": cid, "rt": reference_type, "rid": transaction_id},
    ).all()
    if settings.payment_allocation_engine_enabled and kind == "sale":
        # A document payment that already carries a charge allocation must not
        # be rewritten while the V2c write flag is off: the bulk path cannot
        # re-derive that target, so it reports the V2b guard instead.
        charge_allocated = db.execute(
            text(
                """SELECT 1 FROM payment_allocations
                WHERE company_id=:cid AND receivable_charge_id IS NOT NULL
                  AND payment_id IN (
                    SELECT id FROM payments WHERE company_id=:cid
                      AND reference_type=:rt AND reference_id=:rid
                )
                LIMIT 1"""
            ),
            {"cid": cid, "rt": reference_type, "rid": transaction_id},
        ).first()
        if charge_allocated:
            ensure_charge_allocation_enabled()
        allocated = db.execute(
            text(
                """SELECT 1 FROM payment_allocations
                WHERE company_id=:cid AND payment_id IN (
                    SELECT id FROM payments WHERE company_id=:cid
                      AND reference_type=:rt AND reference_id=:rid
                )
                LIMIT 1"""
            ),
            {"cid": cid, "rt": reference_type, "rid": transaction_id},
        ).first()
        if allocated:
            raise HTTPException(
                409,
                "Tahsisli satış ödemesi reversal tamamlanmadan değiştirilemez",
            )
    for old_payment in old_payments:
        remove_payment_finance(db, cid, int(old_payment[0]))
    db.execute(
        text("DELETE FROM payments WHERE company_id=:cid AND reference_type=:rt AND reference_id=:rid"),
        {"cid": cid, "rt": reference_type, "rid": transaction_id},
    )
    if payload.status not in STOCK_STATUSES or payload.paid_amount <= 0:
        return

    allocations = list(payload.payment_allocations)
    if not allocations:
        allocations = [type("Allocation", (), {
            "payment_method": payload.payment_method,
            "amount": payload.paid_amount,
            "account_id": payload.payment_account_id,
        })()]

    for allocation in allocations:
        if allocation.payment_method not in PAYMENT_METHODS or allocation.payment_method == "credit":
            raise HTTPException(400, "Ödeme dağılımında geçersiz yöntem")
        validate_payment_account(db, cid, allocation.payment_method, allocation.account_id)
        result = db.execute(
            text(
                """INSERT INTO payments(
                    entity_type,entity_id,amount,payment_date,note,company_id,
                    payment_method,reference_type,reference_id,account_id
                ) VALUES(:et,:eid,:amount,:d,:note,:cid,:pm,:rt,:rid,:account_id)
                RETURNING id"""
            ),
            {
                "et": entity_type,
                "eid": payload.entity_id,
                "amount": allocation.amount,
                "d": payload.transaction_date,
                "note": f"Otomatik belge ödemesi #{transaction_id}",
                "cid": cid,
                "pm": allocation.payment_method,
                "rt": reference_type,
                "rid": transaction_id,
                "account_id": allocation.account_id,
            },
        )
        payment_id = int(result.scalar_one())
        if settings.payment_allocation_engine_enabled and kind == "sale":
            allocate_payment(
                db,
                cid,
                payment_id,
                f"sale:{transaction_id}:payment:{payment_id}",
                commit=False,
            )
        sync_payment_finance(
            db, cid, payment_id, entity_type, allocation.amount,
            payload.transaction_date, allocation.payment_method,
            f"Otomatik belge ödemesi #{transaction_id}", allocation.account_id,
        )


def _lock_document_payments(
    db: Session,
    cid: int,
    reference_type: str,
    transaction_id: int,
):
    return db.execute(
        text(
            "SELECT id FROM payments WHERE company_id=:cid "
            "AND reference_type=:rt AND reference_id=:rid ORDER BY id"
            + (" FOR UPDATE" if db.get_bind().dialect.name == "postgresql" else "")
        ),
        {"cid": cid, "rt": reference_type, "rid": transaction_id},
    ).all()


def _derived_due_date(
    payload: TransactionCreate, db: Session, cid: int, final_total: Decimal
) -> str | None:
    """Bakiye bırakan bir satışın vadesini müşterinin ANLAŞILMIŞ vadesinden türet.

    NEDEN: Yaşlandırma raporunun ön kabulü şudur — bakiyesi olan bir satış bir
    ALACAKTIR. Vade NULL kalırsa ``receivables_engine`` onu hiç göstermez
    (``due_date IS NOT NULL AND due_date<>''``) ve ``payment_allocation_engine``
    belge kapsamına almaz; yani gerçek bir alacak hem rapordan hem normal
    tahsis akışından DÜŞER.

    KURAL UYDURULMADI: ``service_receivable_engine._source_snapshot`` servis
    alacağı için zaten ``belge tarihi + customers.payment_term_days``
    kullanıyor. ``payment_term_days`` NOT NULL, varsayılanı 0; anlaşılmış
    vadesi olmayan müşteride sonuç "bugün ödenmeli" olur — NULL'dan farklı
    olarak yaşlandırmanın kovaya koyabildiği geçerli bir vade.

    YALNIZ BAKİYE BIRAKAN satışta türetilir. Tamamı ödenmiş satışın alacağı
    yoktur; ona vade yazmak onu tahsis defterine sokar ve defter ``paid_amount``
    ile uyumsuz kalır. Ayrım ölçülerek doğrulandı (bkz. test_v3_pos).

    Vade türetilemiyorsa (müşteri satırı yok, tarih okunamıyor) None döner;
    burada uydurma bir tarih üretilmez.
    """
    if not payload.entity_id:
        return None
    if money(payload.paid_amount) >= money(final_total):
        return None
    term_days = db.execute(
        text(
            "SELECT COALESCE(payment_term_days,0) FROM customers "
            "WHERE company_id=:cid AND id=:entity_id"
        ),
        {"cid": cid, "entity_id": int(payload.entity_id)},
    ).scalar()
    if term_days is None:
        return None
    try:
        document_date = date.fromisoformat(str(payload.transaction_date)[:10])
    except ValueError:
        return None
    return (document_date + timedelta(days=int(term_days))).isoformat()


def _due_date_values(
    payload: TransactionCreate,
    db: Session,
    cid: int,
    *,
    old: dict | None,
    request: Request | None,
    final_total: Decimal,
) -> dict[str, object]:
    old_due = (
        str(old.get("due_date"))[:10]
        if old and old.get("due_date")
        else None
    )
    old_source = str(old.get("due_date_source") or "legacy") if old else "legacy"
    approved = bool(old and old.get("status") in STOCK_STATUSES)

    def require_finance_override() -> None:
        user = getattr(request.state, "user", {}) if request is not None else {}
        if not has_permission(str(user.get("role") or ""), "finance"):
            raise HTTPException(
                403,
                "Onaylı satışın vadesini değiştirmek için finans yetkisi gerekir",
            )
        if not payload.due_date_override_reason:
            raise HTTPException(
                422,
                "Onaylı satışın vade değişikliği için gerekçe zorunludur",
            )

    if approved and str(old.get("payment_term") or "PESIN") != payload.payment_term:
        require_finance_override()

    if payload.payment_term != "HARMAN_VADELI":
        if approved and (payload.due_date or None) != (old_due or None):
            require_finance_override()
        # Yetki kontrolü İSTENEN değere göre yapılır (yukarıda); türetme ondan
        # SONRA gelir, böylece finans yetkisi sözleşmesi değişmez.
        resolved_due = payload.due_date
        resolved_source = "manual" if payload.due_date else "legacy"
        if not resolved_due:
            derived = _derived_due_date(payload, db, cid, final_total)
            if derived:
                # 'automatic' zaten "sistemin türettiği vade" anlamında
                # kullanılıyor (HARMAN_VADELI takvimden çözdüğünde).
                # ck_orders_due_date_source yalnız automatic/manual/legacy
                # kabul ediyor; yeni bir kaynak değeri GÖÇ gerektirirdi.
                resolved_due, resolved_source = derived, "automatic"
        return {
            "due_date": resolved_due,
            "harvest_calendar_id": None,
            "due_date_source": resolved_source,
            "due_date_override_reason": payload.due_date_override_reason,
            "due_date_overridden_by": None,
            "due_date_overridden_at": None,
            "harvest_resolution_snapshot": None,
        }

    calendar_was_sent = "harvest_calendar_id" in payload.model_fields_set
    manual_changed = (
        payload.due_date is not None
        and (old is None or payload.due_date != old_due)
    )
    calendar_changed = (
        calendar_was_sent
        and payload.harvest_calendar_id is not None
        and (
            old is None
            or int(old.get("harvest_calendar_id") or 0)
            != payload.harvest_calendar_id
        )
    )
    if approved and (manual_changed or calendar_changed):
        require_finance_override()

    if payload.due_date is not None:
        if old is not None and payload.due_date == old_due and not calendar_changed:
            return {
                "due_date": old_due,
                "harvest_calendar_id": old.get("harvest_calendar_id"),
                "due_date_source": old_source,
                "due_date_override_reason": old.get("due_date_override_reason"),
                "due_date_overridden_by": old.get("due_date_overridden_by"),
                "due_date_overridden_at": old.get("due_date_overridden_at"),
                "harvest_resolution_snapshot": old.get(
                    "harvest_resolution_snapshot"
                ),
            }
        user = getattr(request.state, "user", {}) if request is not None else {}
        reason = (
            payload.due_date_override_reason
            or "Kullanıcı tarafından manuel vade tarihi girildi"
        )
        snapshot = json.dumps(
            {
                "version": 1,
                "mode": "manual_due_date",
                "customer_id": payload.entity_id,
                "transaction_date": payload.transaction_date,
                "product_ids": sorted(
                    {int(item.product_id) for item in payload.items}
                ),
                "due_date": payload.due_date,
                "reason": reason,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "due_date": payload.due_date,
            "harvest_calendar_id": None,
            "due_date_source": "manual",
            "due_date_override_reason": reason,
            "due_date_overridden_by": user.get("id"),
            "due_date_overridden_at": utcnow(),
            "harvest_resolution_snapshot": snapshot,
        }

    old_is_harman = bool(
        old
        and str(old.get("payment_term") or "PESIN") == "HARMAN_VADELI"
    )
    if approved and old_is_harman and old_due is not None and not calendar_changed:
        return {
            "due_date": old_due,
            "harvest_calendar_id": old.get("harvest_calendar_id"),
            "due_date_source": old_source,
            "due_date_override_reason": old.get("due_date_override_reason"),
            "due_date_overridden_by": old.get("due_date_overridden_by"),
            "due_date_overridden_at": old.get("due_date_overridden_at"),
            "harvest_resolution_snapshot": old.get(
                "harvest_resolution_snapshot"
            ),
        }

    resolution = resolve_harvest_due_date(
        db,
        company_id=cid,
        customer_id=payload.entity_id,
        transaction_date=date.fromisoformat(payload.transaction_date),
        product_ids=[int(item.product_id) for item in payload.items],
        explicit_calendar_id=payload.harvest_calendar_id,
    )
    user = getattr(request.state, "user", {}) if request is not None else {}
    explicit_selection = payload.harvest_calendar_id is not None
    return {
        "due_date": resolution.due_date.isoformat(),
        "harvest_calendar_id": resolution.calendar_id,
        "due_date_source": "automatic",
        "due_date_override_reason": (
            payload.due_date_override_reason
            or "Kullanıcı tarafından sezon takvimi seçildi"
            if explicit_selection
            else None
        ),
        "due_date_overridden_by": user.get("id") if explicit_selection else None,
        "due_date_overridden_at": utcnow() if explicit_selection else None,
        "harvest_resolution_snapshot": resolution.snapshot,
    }


def _save(
    kind: str,
    payload: TransactionCreate,
    db: Session,
    cid: int,
    override: PolicyOverrideContext,
    transaction_id: int | None = None,
    *,
    commit: bool = True,
    request: Request | None = None,
):
    config = _config(kind)
    stored_paid_amount = (
        ZERO_MONEY
        if settings.payment_allocation_engine_enabled and kind == "sale"
        else payload.paid_amount
    )
    if payload.status not in DOCUMENT_STATUSES:
        raise HTTPException(400, "Geçersiz belge durumu")
    if payload.payment_method not in PAYMENT_METHODS:
        raise HTTPException(400, "Geçersiz ödeme yöntemi")
    if (
        settings.payment_allocation_engine_enabled
        and kind == "sale"
        and transaction_id is not None
    ):
        # Global sıra: var olan payment satırları belge/stock satırlarından önce.
        _lock_document_payments(db, cid, "order", transaction_id)
    if kind == "sale" and payload.payment_term not in PAYMENT_TERMS:
        raise HTTPException(400, "Geçersiz ödeme vadesi")
    # payment_term (harman vadesi) lives only on the sales ``orders`` table; the
    # ``purchases`` table has no such column, so the fragments stay empty there.
    term_col = (
        """,payment_term,harvest_calendar_id,due_date_source,
        due_date_override_reason,due_date_overridden_by,due_date_overridden_at,
        harvest_resolution_snapshot"""
        if kind == "sale"
        else ""
    )
    term_val = (
        """,:payment_term,:harvest_calendar_id,:due_date_source,
        :due_date_override_reason,:due_date_overridden_by,
        :due_date_overridden_at,:harvest_resolution_snapshot"""
        if kind == "sale"
        else ""
    )
    term_set = (
        """,payment_term=:payment_term,
        harvest_calendar_id=:harvest_calendar_id,
        due_date_source=:due_date_source,
        due_date_override_reason=:due_date_override_reason,
        due_date_overridden_by=:due_date_overridden_by,
        due_date_overridden_at=:due_date_overridden_at,
        harvest_resolution_snapshot=:harvest_resolution_snapshot"""
        if kind == "sale"
        else ""
    )
    normalized_due_col = ",due_date_normalized" if kind == "sale" else ""
    normalized_due_val = ",:due_date_normalized" if kind == "sale" else ""
    normalized_due_set = (
        ",due_date_normalized=:due_date_normalized" if kind == "sale" else ""
    )

    warehouse_id = _warehouse(db, cid, payload.warehouse_id)
    branch_id = _branch(db, cid, payload.branch_id)
    entity_exists = db.execute(
        text(f"SELECT id FROM {config['entity']} WHERE id=:id AND company_id=:cid"),
        {"id": payload.entity_id, "cid": cid},
    ).first()
    if not entity_exists:
        raise HTTPException(400, "Cari bulunamadı")

    rows, subtotal, vat_total, gross_total, discount_amount, final_total = _totals(db, payload, cid)
    policies = get_policy_settings(db, cid)
    override_policies: set[str] = set()
    if payload.paid_amount > final_total:
        raise HTTPException(400, "Ödenen tutar belge toplamını aşamaz")

    if payload.document_no:
        duplicate = db.execute(
            text(f"SELECT id FROM {config['head']} WHERE company_id=:cid AND LOWER(document_no)=LOWER(:doc) AND (CAST(:current_id AS INTEGER) IS NULL OR id<>CAST(:current_id AS INTEGER)) LIMIT 1"),
            {"cid": cid, "doc": payload.document_no, "current_id": transaction_id},
        ).first()
        if duplicate:
            raise HTTPException(409, "Bu belge numarası daha önce kullanılmış")

    try:
        if kind == "sale":
            risk_limit, current_balance, projected_balance = _credit_exposure(
                db,
                cid=cid,
                customer_id=payload.entity_id,
                final_total=final_total,
                paid_amount=payload.paid_amount,
                status=payload.status,
                transaction_id=transaction_id,
            )
            if (
                risk_limit > ZERO_MONEY
                and projected_balance > risk_limit
                and projected_balance > current_balance
            ):
                overridden = enforce_known_violation(
                    mode=policies.credit_limit_policy,
                    context=override,
                    blocked_message=(
                        f"Müşteri risk limiti aşılıyor. Limit: {risk_limit:.2f}, "
                        f"işlem sonrası bakiye: {projected_balance:.2f}."
                    ),
                    override_message=(
                        f"Müşteri risk limiti aşılıyor. Limit: {risk_limit:.2f}, "
                        f"işlem sonrası bakiye: {projected_balance:.2f}. "
                        "Yönetici onayıyla devam etmek için gerekçe girin."
                    ),
                )
                if overridden:
                    override_policies.add("credit_limit")

        document_no = payload.document_no
        old: dict | None = None
        old_audit: dict | None = None
        if transaction_id:
            old = db.execute(
                text(
                    f"SELECT *,COALESCE(warehouse_id,:dw) resolved_warehouse_id,"
                    f"COALESCE(status,'completed') resolved_status "
                    f"FROM {config['head']} WHERE id=:id AND company_id=:cid"
                ),
                {"id": transaction_id, "cid": cid, "dw": default_warehouse(db, cid)},
            ).mappings().first()
            if not old:
                raise HTTPException(404, "Belge bulunamadı")
            old = dict(old)
            old_audit = {
                key: value
                for key, value in old.items()
                if key not in {"resolved_warehouse_id", "resolved_status"}
            }
            if (
                kind == "sale"
                and old.get("note") == SALES_IMPORT_NOTE
                and payload.status in STOCK_STATUSES
            ):
                raise HTTPException(
                    409,
                    "Bu belge içe aktarılmış geçmiş satıştır; onaylanırsa stok çift "
                    "düşer. İçe aktarılan satışlar taslak olarak kalır.",
                )

            old_items = db.execute(
                text(f"SELECT product_id,quantity FROM {config['items']} WHERE {config['item_fk']}=:id"),
                {"id": transaction_id},
            ).mappings().all()
            if old["resolved_status"] in STOCK_STATUSES:
                for item in old_items:
                    reverse_delta = -config["stock_sign"] * quantity(item["quantity"])
                    allow_negative = (
                        negative_stock_allowed(policies.negative_stock_policy, override)
                        if reverse_delta < 0
                        else False
                    )
                    new_stock = adjust_warehouse_stock(
                        db,
                        cid,
                        int(old["resolved_warehouse_id"]),
                        int(item["product_id"]),
                        reverse_delta,
                        allow_negative=allow_negative,
                    )
                    if new_stock < 0 and policies.negative_stock_policy == "manager_override":
                        override_policies.add("negative_stock")
            db.execute(
                text(f"DELETE FROM {config['items']} WHERE {config['item_fk']}=:id"),
                {"id": transaction_id},
            )
            # PARTİ BORCU HAREKETLERDEN ÖNCE DÜŞÜLÜR (1B-A): aşağıdaki DELETE
            # satırları yok ettikten sonra hangi partiden ne kadar düşüleceği
            # OKUNAMAZ. Sıra zorunludur, üslup değil.
            _parti_geri_al(
                db, cid, reference_type=config["head"], reference_id=transaction_id
            )
            # Eski belgeye ait stok hareketleri, yeni kalemler eklenmeden önce temizlenir.
            # Aksi halde stok toplamı doğru olsa bile hareket raporu mükerrer görünür.
            db.execute(
                text("""DELETE FROM stock_movements
                WHERE company_id=:cid AND reference_type=:rt AND reference_id=:rid"""),
                {"cid": cid, "rt": config["head"], "rid": transaction_id},
            )
            document_no = document_no or old["document_no"] or next_document_no(
                db, config["head"], cid, config["prefix"]
            )
            due_values = (
                _due_date_values(
                    payload,
                    db,
                    cid,
                    final_total=final_total,
                    old=old,
                    request=request,
                )
                if kind == "sale"
                else {"due_date": payload.due_date}
            )
            db.execute(
                text(
                    f"""UPDATE {config['head']} SET
                    {config['entity_id']}=:entity,{config['date']}=:d,
                    subtotal=:sub,vat_total=:vat,grand_total=:gross,
                    discount_percent=:discount_percent,discount_amount=:discount_amount,
                    final_total=:final,document_no=:document_no,due_date=:due_date
                    {normalized_due_set},
                    note=:note,warehouse_id=:warehouse_id,branch_id=:branch_id,
                    status=:status,payment_method=:payment_method,paid_amount=:paid_amount
                    {term_set}
                    WHERE id=:id AND company_id=:cid"""
                ),
                {
                    "entity": payload.entity_id,
                    "d": payload.transaction_date,
                    "sub": subtotal,
                    "vat": vat_total,
                    "gross": gross_total,
                    "discount_percent": persisted_percentage(payload.discount_percent),
                    "discount_amount": discount_amount,
                    "final": final_total,
                    "document_no": document_no,
                    "due_date": due_values["due_date"],
                    "due_date_normalized": due_values["due_date"],
                    "note": payload.note,
                    "warehouse_id": warehouse_id,
                    "branch_id": branch_id,
                    "status": payload.status,
                    "payment_method": payload.payment_method,
                    "payment_term": payload.payment_term,
                    **due_values,
                    "paid_amount": stored_paid_amount,
                    "id": transaction_id,
                    "cid": cid,
                },
            )
        else:
            document_no = document_no or next_document_no(db, config["head"], cid, config["prefix"])
            due_values = (
                _due_date_values(
                    payload,
                    db,
                    cid,
                    final_total=final_total,
                    old=None,
                    request=request,
                )
                if kind == "sale"
                else {"due_date": payload.due_date}
            )
            result = db.execute(
                text(
                    f"""INSERT INTO {config['head']}(
                    {config['entity_id']},{config['date']},subtotal,vat_total,grand_total,
                    discount_percent,discount_amount,final_total,document_no,due_date
                    {normalized_due_col},note,
                    company_id,warehouse_id,branch_id,status,payment_method,paid_amount{term_col}
                    ) VALUES(
                    :entity,:d,:sub,:vat,:gross,:discount_percent,:discount_amount,:final,
                    :document_no,:due_date{normalized_due_val},:note,:cid,:warehouse_id,
                    :branch_id,:status,
                    :payment_method,:paid_amount{term_val}) RETURNING id"""
                ),
                {
                    "entity": payload.entity_id,
                    "d": payload.transaction_date,
                    "sub": subtotal,
                    "vat": vat_total,
                    "gross": gross_total,
                    "discount_percent": persisted_percentage(payload.discount_percent),
                    "discount_amount": discount_amount,
                    "final": final_total,
                    "document_no": document_no,
                    "due_date": due_values["due_date"],
                    "due_date_normalized": due_values["due_date"],
                    "note": payload.note,
                    "cid": cid,
                    "warehouse_id": warehouse_id,
                    "branch_id": branch_id,
                    "status": payload.status,
                    "payment_method": payload.payment_method,
                    "payment_term": payload.payment_term,
                    **due_values,
                    "paid_amount": stored_paid_amount,
                },
            )
            transaction_id = int(result.scalar_one())

        apply_stock = payload.status in STOCK_STATUSES
        for product, item, line_subtotal, line_vat, line_total, line_discount in rows:
            if apply_stock:
                stock_delta = config["stock_sign"] * item.quantity
                allow_negative = (
                    negative_stock_allowed(policies.negative_stock_policy, override)
                    if stock_delta < 0
                    else False
                )
                new_stock = adjust_warehouse_stock(
                    db,
                    cid,
                    warehouse_id,
                    int(product["id"]),
                    stock_delta,
                    allow_negative=allow_negative,
                )
                if new_stock < 0 and policies.negative_stock_policy == "manager_override":
                    override_policies.add("negative_stock")
            # PARTİ SÜTUNLARI YALNIZ ALIŞ KALEMİNDE VAR (göç 20260908_0073);
            # `order_items` onları TAŞIMAZ. Parça `kind`ten geliyor ve `kind`
            # KAPALI bir kümedir (`_config` yalnız 'sale'/'purchase' bilir) —
            # istekten gelen hiçbir değer metne girmiyor, ikisi de BAĞLI
            # PARAMETRE. Aynı dosyadaki `term_col`/`normalized_due_col`in
            # kalıbı.
            lot_col = ",lot_code,expiry_date" if kind == "purchase" else ""
            lot_val = ",:lot_code,:expiry_date" if kind == "purchase" else ""
            lot_params = (
                {"lot_code": item.lot_code, "expiry_date": item.expiry_date}
                if kind == "purchase"
                else {}
            )
            db.execute(
                text(
                    f"""INSERT INTO {config['items']}(
                    company_id,{config['item_fk']},product_id,product_name,quantity,unit_price,
                    vat_rate,line_subtotal,line_vat,line_total,discount_percent,discount_amount
                    {lot_col}
                    ) VALUES(:cid,:transaction_id,:product_id,:product_name,:quantity,:unit_price,
                    :vat_rate,:line_subtotal,:line_vat,:line_total,:discount_percent,:discount_amount
                    {lot_val})"""
                ),
                {
                    **lot_params,
                    # Satır KENDİ kiracısını taşır (1. dilim, 20260812_0058);
                    # ebeveynden türetilebilir olması yetmez.
                    "cid": cid,
                    "transaction_id": transaction_id,
                    "product_id": product["id"],
                    "product_name": product["name"],
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "vat_rate": item.vat_rate,
                    "line_subtotal": line_subtotal,
                    "line_vat": line_vat,
                    "line_total": line_total,
                    "discount_percent": persisted_percentage(item.discount_percent),
                    "discount_amount": line_discount,
                },
            )
            if apply_stock:
                # PARTİ DEFTERİ HAREKETTEN ÖNCE (1B-A): hareket `lot_id`yi
                # TAŞIYACAK ve bileşik yabancı anahtar hedefin VAR olmasını
                # şart koşuyor. Kod boşsa satır bugünkü davranışını korur ve
                # `lot_id` NULL kalır — "bu hareket parti öncesindendir"
                # cümlesi 0067'de zaten DOĞRUYDU.
                lot_id = None
                if kind == "purchase" and item.lot_code:
                    lot_id = _parti_ac(
                        db,
                        cid,
                        product_id=int(product["id"]),
                        warehouse_id=warehouse_id,
                        lot_code=item.lot_code,
                        expiry_date=item.expiry_date,
                        miktar=item.quantity,
                    )
                # SATIŞ PARTİ TÜKETİR (1B-B). Yön `stock_sign`tan OKUNUYOR,
                # `kind == "sale"` DİYE YAZILMADI: aynı çıkarma kuralı
                # `workflow.py`nin irsaliye ve alış iadesi yollarında da
                # geçerlidir ve tür adına bağlanmak, üçüncü bir çıkış yolu
                # eklendiği gün onu SESSİZCE dışarıda bırakırdı.
                #
                # SIRA: `adjust_warehouse_stock` YUKARIDA çağrıldı; tüketim
                # onun ARDINDADIR. Tersi olsaydı stok koruması belgeyi
                # reddetse bile parti ZATEN düşülmüş olurdu. Eşzamanlılığın
                # nasıl korunduğu (`FOR UPDATE` DEĞİL, korumalı atomik
                # UPDATE) `parti_defteri._parti_tuket` başlığında ÖLÇÜLDÜ.
                paylar: list[tuple[int | None, Decimal]] = [
                    (lot_id, config["stock_sign"] * item.quantity)
                ]
                suresi_gecmisler: frozenset[int] = frozenset()
                defter_bosaldi = False
                if config["stock_sign"] < 0:
                    tuketim = _parti_tuket(
                        db,
                        cid,
                        product_id=int(product["id"]),
                        warehouse_id=warehouse_id,
                        miktar=item.quantity,
                        bugun=business_today(),
                        suresi_gecmise_izin=payload.allow_expired_lots,
                    )
                    defter_bosaldi = tuketim.defter_bosaldi
                    if tuketim.dagitim:
                        paylar = [(kimlik, -pay) for kimlik, pay in tuketim.dagitim]
                        suresi_gecmisler = tuketim.suresi_gecmis_kimlikler
                # BİR PAY = BİR HAREKET SATIRI. Tek satıra toplamak, hangi
                # partiden ne kadar çıktığını GERİ ALINAMAZ yapardı: geri
                # alma yolu (`_parti_geri_al`) borcu HAREKETLERDEN okuyor ve
                # tek satır iki partinin borcunu tek `lot_id`ye yıkardı.
                # Geri alma yine REFERANSTAN anahtarlandığı için N satır onu
                # bozmaz — hepsi aynı `(reference_type, reference_id)`de.
                for hareket_lot, hareket_miktar in paylar:
                    db.execute(
                        text(
                            """INSERT INTO stock_movements(
                            product_id,movement_type,quantity,movement_date,reference_type,
                            reference_id,note,company_id,warehouse_id,lot_id
                            ) VALUES(:product_id,:movement_type,:quantity,:movement_date,
                            :reference_type,:reference_id,:note,:company_id,:warehouse_id,:lot_id)"""
                        ),
                        {
                            "product_id": product["id"],
                            "movement_type": kind,
                            "quantity": hareket_miktar,
                            "movement_date": payload.transaction_date,
                            "reference_type": config["head"],
                            "reference_id": transaction_id,
                            "note": _hareket_notu(
                                kind,
                                int(transaction_id),
                                hareket_lot in suresi_gecmisler,
                                defter_bosaldi,
                            ),
                            "company_id": cid,
                            "warehouse_id": warehouse_id,
                            "lot_id": hareket_lot,
                        },
                    )
            if kind == "purchase" and apply_stock:
                db.execute(
                    text("UPDATE products SET purchase_price=:price WHERE id=:id AND company_id=:cid"),
                    {"price": item.unit_price, "id": product["id"], "cid": cid},
                )

        _sync_payment(kind, int(transaction_id), payload, db, cid)
        record_policy_overrides(
            db,
            company_id=cid,
            context=override,
            policy_names=override_policies,
            resource_type=config["head"],
            resource_id=int(transaction_id),
        )
        if kind == "sale" and request is not None:
            after = db.execute(
                text(
                    "SELECT * FROM orders WHERE id=:id AND company_id=:cid"
                ),
                {"id": transaction_id, "cid": cid},
            ).mappings().one()
            record_change(
                db,
                request,
                company_id=cid,
                entity_type="order",
                entity_id=int(transaction_id),
                action="update" if old is not None else "create",
                before=old_audit,
                after=after,
            )
            # Aktivite paneli kaydı — belgeyle AYNI transaction içinde. Bu satır
            # yazılamazsa satış da kaydedilmez; satış düşerse bu satır da düşer.
            log_request_activity(
                db,
                request,
                cid,
                "sale.update" if old is not None else "sale.create",
                "sale",
                int(transaction_id),
                (
                    f"{document_no} satışını "
                    f"{'güncelledi' if old is not None else 'oluşturdu'} — "
                    f"{format_money_tr(final_total)}, müşteri: "
                    f"{_customer_name(db, cid, payload.entity_id)}"
                ),
                {
                    "changes": diff_details(old_audit, dict(after), SALE_AUDIT_FIELDS),
                    "document_no": document_no,
                    "final_total": final_total,
                    "status": payload.status,
                },
            )
        if commit:
            db.commit()
        else:
            db.flush()
        return {
            "id": transaction_id,
            "document_no": document_no,
            "subtotal": subtotal,
            "vat_total": vat_total,
            "grand_total": gross_total,
            "discount_amount": discount_amount,
            "final_total": final_total,
            "paid_amount": payload.paid_amount,
            "remaining_amount": money(final_total - payload.paid_amount),
            "warehouse_id": warehouse_id,
            "status": payload.status,
            "policy_overrides": sorted(override_policies),
        }
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        if "yetersiz stok" in str(exc).lower():
            raise HTTPException(409, stock_policy_message(policies.negative_stock_policy)) from exc
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Unexpected transaction save failure", extra={"kind": kind, "transaction_id": transaction_id})
        raise HTTPException(500, "İşlem tamamlanamadı. Lütfen tekrar deneyin.") from exc


TRANSACTION_SORTS = {
    "date_desc": "normalized_date DESC, h.id DESC",
    "date_asc": "normalized_date ASC, h.id ASC",
    "total_desc": "h.final_total DESC, h.id DESC",
    "total_asc": "h.final_total ASC, h.id ASC",
    "entity_asc": "LOWER(e.name) ASC, h.id DESC",
    "entity_desc": "LOWER(e.name) DESC, h.id DESC",
}

def _normalized_date_sql(date_column: str) -> str:
    """Return a SQLite/PostgreSQL compatible ISO date text expression."""
    return f"""CASE
        WHEN {date_column} LIKE '__.__.____'
            THEN substr({date_column}, 7, 4) || '-' || substr({date_column}, 4, 2) || '-' || substr({date_column}, 1, 2)
        ELSE substr({date_column}, 1, 10)
    END"""


def _list(kind: str, request: Request, q: str, status: str | None, date_from: str | None, date_to: str | None, sort: str, limit: int, db: Session):
    cid = company_id(request)
    config = _config(kind)
    entity_name = "customer_name" if kind == "sale" else "supplier_name"
    date_col = f"h.{config['date']}"
    normalized_date = _normalized_date_sql(date_col)
    sql = f"""SELECT h.id,h.{config['entity_id']} AS entity_id,
    e.name {entity_name},{date_col} transaction_date,
    {normalized_date} normalized_date,
    h.document_no,h.subtotal,h.vat_total,h.final_total,h.paid_amount,
    (h.final_total-COALESCE(h.paid_amount,0)) remaining_amount,
    COALESCE(h.status,'completed') status,COALESCE(h.payment_method,'credit') payment_method,
    COUNT(i.id) item_count,w.name warehouse_name
    FROM {config['head']} h
    JOIN {config['entity']} e ON e.id=h.{config['entity_id']} AND e.company_id=h.company_id
    LEFT JOIN {config['items']} i ON i.{config['item_fk']}=h.id
    LEFT JOIN warehouses w ON w.id=h.warehouse_id AND w.company_id=h.company_id
    WHERE h.company_id=:cid AND (
      LOWER(e.name) LIKE LOWER(:q) OR CAST(h.id AS TEXT) LIKE :q OR
      COALESCE(h.document_no,'') LIKE :q
    )"""
    params = {"cid": cid, "q": f"%{q}%", "limit": limit}
    if status:
        sql += " AND h.status=:status"
        params["status"] = status
    if date_from:
        sql += f" AND {normalized_date}>=:date_from"
        params["date_from"] = date_from
    if date_to:
        sql += f" AND {normalized_date}<=:date_to"
        params["date_to"] = date_to
    order = TRANSACTION_SORTS.get(sort, TRANSACTION_SORTS["date_desc"])
    sql += f" GROUP BY h.id,e.name,w.name ORDER BY {order} LIMIT :limit"
    rows = db.execute(text(sql), params).mappings().all()
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        raw = item.get("normalized_date")
        # The SQL yields an ISO date *text*; return a real date object so callers
        # (and JSON serialization) get a consistent type across SQLite/PostgreSQL.
        if isinstance(raw, str) and len(raw) >= 10:
            try:
                item["normalized_date"] = date.fromisoformat(raw[:10])
            except ValueError:
                pass
        result.append(item)
    return result


@router.get("/orders")
def orders(request: Request, q: str = "", status: str | None = None, date_from: str | None = None, date_to: str | None = None, sort: str = "date_desc", limit: int = Query(300, le=1000), db: Session = Depends(get_db)):
    return _list("sale", request, q, status, date_from, date_to, sort, limit, db)


@router.get("/purchases")
def purchases(request: Request, q: str = "", status: str | None = None, date_from: str | None = None, date_to: str | None = None, sort: str = "date_desc", limit: int = Query(300, le=1000), db: Session = Depends(get_db)):
    return _list("purchase", request, q, status, date_from, date_to, sort, limit, db)


RECEIVABLE_STATUSES = {"ACIK", "VADESI_GECTI", "ODENDI"}


def _receivable_status(outstanding: Decimal, due_date: date, today: date) -> str:
    if outstanding <= ZERO_MONEY:
        return "ODENDI"
    if due_date < today:
        return "VADESI_GECTI"
    return "ACIK"


@router.get("/receivables")
def receivables(
    request: Request,
    term: str = "harman",
    status: str | None = None,
    due_before: str | None = None,
    sort: str = "due_asc",
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """List harman-vadeli (harvest-term) customer receivables by due date.

    The shared receivables engine applies linked movements directly and allocates
    unlinked customer credits FIFO by oldest due date.
    """
    cid = company_id(request)
    today = business_today()
    if status is not None and status not in RECEIVABLE_STATUSES:
        raise HTTPException(400, "Geçersiz alacak durumu")
    if term not in {"harman", "harman_vadeli", "HARMAN_VADELI"}:
        raise HTTPException(400, "Geçersiz vade türü")
    if sort not in {"due_asc", "due_desc"}:
        sort = "due_asc"
    cutoff: date | None = None
    if due_before:
        try:
            cutoff = date.fromisoformat(due_before)
        except ValueError as exc:
            raise HTTPException(400, "Geçersiz vade tarihi") from exc
    documents = calculate_receivables(
        db,
        cid,
        today,
        payment_term="HARMAN_VADELI",
    )
    if cutoff is not None:
        documents = [item for item in documents if item.due_date <= cutoff]
    documents.sort(
        key=lambda item: (item.due_date, item.id),
        reverse=sort == "due_desc",
    )
    documents = documents[:limit]

    total_outstanding = ZERO_MONEY
    total_overdue = ZERO_MONEY
    result: list[dict] = []
    for document in documents:
        outstanding = document.remaining
        row_status = _receivable_status(outstanding, document.due_date, today)
        if row_status != "ODENDI":
            total_outstanding += outstanding
        if row_status == "VADESI_GECTI":
            total_overdue += outstanding
        if status and row_status != status:
            continue
        result.append(
            {
                "id": document.id,
                "customer_id": document.customer_id,
                "customer_name": document.customer_name,
                "transaction_date": document.transaction_date,
                "document_no": document.document_no,
                "due_date": document.due_date.isoformat(),
                "total": document.total,
                "paid": document.applied,
                "outstanding": outstanding,
                "status": row_status,
            }
        )
    return {
        "receivables": result,
        "summary": {
            "count": len(result),
            "total_outstanding": total_outstanding,
            "total_overdue": total_overdue,
        },
    }


@router.post("/orders", status_code=201)
def create_order(payload: TransactionCreate, request: Request, db: Session = Depends(get_db)):
    return _save(
        "sale",
        payload,
        db,
        company_id(request),
        override_context(request),
        request=request,
    )


@router.post("/purchases", status_code=201)
def create_purchase(payload: TransactionCreate, request: Request, db: Session = Depends(get_db)):
    return _save("purchase", payload, db, company_id(request), override_context(request))


@router.put("/orders/{transaction_id}")
def update_order(transaction_id: int, payload: TransactionCreate, request: Request, db: Session = Depends(get_db)):
    return _save(
        "sale",
        payload,
        db,
        company_id(request),
        override_context(request),
        transaction_id,
        request=request,
    )


@router.put("/purchases/{transaction_id}")
def update_purchase(transaction_id: int, payload: TransactionCreate, request: Request, db: Session = Depends(get_db)):
    return _save("purchase", payload, db, company_id(request), override_context(request), transaction_id)


@router.get("/orders/last-sale-price")
def last_sale_price(
    request: Request,
    customer_id: int = Query(..., ge=1),
    product_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    row = db.execute(
        text(
            """SELECT oi.unit_price,oi.discount_percent,o.order_date,o.document_no,o.id order_id
            FROM order_items oi
            JOIN orders o ON o.id=oi.order_id AND o.company_id=:cid
            WHERE oi.company_id=:cid AND o.company_id=:cid
              AND o.customer_id=:customer_id AND oi.product_id=:product_id
              AND COALESCE(o.status,'completed') NOT IN ('draft','cancelled')
            ORDER BY o.order_date DESC,o.id DESC,oi.id DESC LIMIT 1"""
        ),
        {"cid": cid, "customer_id": customer_id, "product_id": product_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Bu müşteri için önceki satış bulunamadı")
    return dict(row)


@router.get("/purchases/last-purchase-price")
def last_purchase_price(
    request: Request,
    supplier_id: int = Query(..., ge=1),
    product_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    row = db.execute(
        text(
            """SELECT pi.unit_price,pi.discount_percent,p.purchase_date,p.document_no,p.id purchase_id
            FROM purchase_items pi
            JOIN purchases p ON p.id=pi.purchase_id AND p.company_id=:cid
            WHERE pi.company_id=:cid AND p.company_id=:cid
              AND p.supplier_id=:supplier_id AND pi.product_id=:product_id
              AND COALESCE(p.status,'completed') NOT IN ('draft','cancelled')
            ORDER BY p.purchase_date DESC,p.id DESC,pi.id DESC LIMIT 1"""
        ),
        {"cid": cid, "supplier_id": supplier_id, "product_id": product_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Bu tedarikçi için önceki alış bulunamadı")
    return dict(row)


@router.get("/{kind}/{transaction_id}")
def detail(kind: str, transaction_id: int, request: Request, db: Session = Depends(get_db)):
    if kind not in ("orders", "purchases"):
        raise HTTPException(404)
    sale = kind == "orders"
    config = _config("sale" if sale else "purchase")
    entity_name = "customer_name" if sale else "supplier_name"
    cid = company_id(request)
    document = db.execute(
        text(
            f"""SELECT h.*,e.name {entity_name},h.{config['date']} transaction_date,
            h.{config['entity_id']} entity_id,w.name warehouse_name
            FROM {config['head']} h
            JOIN {config['entity']} e ON e.id=h.{config['entity_id']} AND e.company_id=h.company_id
            LEFT JOIN warehouses w ON w.id=h.warehouse_id AND w.company_id=h.company_id
            WHERE h.id=:id AND h.company_id=:cid"""
        ),
        {"id": transaction_id, "cid": cid},
    ).mappings().first()
    if not document:
        raise HTTPException(404, "Belge bulunamadı")
    lines = db.execute(
        text(
            f"""SELECT id,product_id,product_name,quantity,unit_price,vat_rate,
            line_total,discount_percent,discount_amount
            FROM {config['items']} WHERE {config['item_fk']}=:id ORDER BY id"""
        ),
        {"id": transaction_id},
    ).mappings().all()
    reference_type = "order" if sale else "purchase"
    payments = db.execute(
        text("""SELECT id,payment_method,amount,payment_date,note
        FROM payments WHERE company_id=:cid AND reference_type=:rt AND reference_id=:rid
        ORDER BY id"""),
        {"cid": cid, "rt": reference_type, "rid": transaction_id},
    ).mappings().all()
    return {"document": dict(document), "items": [dict(line) for line in lines], "payments": [dict(payment) for payment in payments]}


@router.delete("/{kind}/{transaction_id}", status_code=204)
def delete_transaction(kind: str, transaction_id: int, request: Request, db: Session = Depends(get_db)):
    if kind not in ("orders", "purchases"):
        raise HTTPException(404)
    sale = kind == "orders"
    config = _config("sale" if sale else "purchase")
    cid = company_id(request)
    payment_rows = _lock_document_payments(
        db,
        cid,
        "order" if sale else "purchase",
        transaction_id,
    )
    policies = get_policy_settings(db, cid)
    override = override_context(request)
    override_policies: set[str] = set()
    head = db.execute(
        text(
            f"SELECT id,COALESCE(warehouse_id,:dw) warehouse_id,"
            f"COALESCE(status,'completed') status FROM {config['head']} "
            f"WHERE id=:id AND company_id=:cid"
        ),
        {"id": transaction_id, "cid": cid, "dw": default_warehouse(db, cid)},
    ).mappings().first()
    if not head:
        raise HTTPException(404)
    # Silinen belgenin özeti, satırlar kaybolmadan ÖNCE okunur; aktivite kaydı
    # aynı transaction'ın sonunda bu anlık görüntüyle yazılır.
    cancelled = (
        db.execute(
            text(
                """SELECT document_no, customer_id, final_total, status, order_date
                   FROM orders WHERE id=:id AND company_id=:cid"""
            ),
            {"id": transaction_id, "cid": cid},
        ).mappings().first()
        if sale
        else None
    )
    # POS fişinin ayrı bir iptal ucu yoktur; iptal bu uçtan geçer. Kaynağı
    # ``pos_idempotency`` üzerinden kesin ayırt edip POS olayını ayrı tipte
    # yazıyoruz, böylece panelde "POS iptali" tek başına filtrelenebiliyor.
    from_pos = bool(
        cancelled is not None
        and db.execute(
            text(
                "SELECT 1 FROM pos_idempotency "
                "WHERE company_id=:cid AND order_id=:id LIMIT 1"
            ),
            {"cid": cid, "id": transaction_id},
        ).first()
    )
    lines = db.execute(
        text(f"SELECT product_id,quantity FROM {config['items']} WHERE {config['item_fk']}=:id"),
        {"id": transaction_id},
    ).mappings().all()
    try:
        if head["status"] in STOCK_STATUSES:
            for line in lines:
                reverse_delta = -config["stock_sign"] * quantity(line["quantity"])
                allow_negative = (
                    negative_stock_allowed(policies.negative_stock_policy, override)
                    if reverse_delta < 0
                    else False
                )
                new_stock = adjust_warehouse_stock(
                    db,
                    cid,
                    int(head["warehouse_id"]),
                    int(line["product_id"]),
                    reverse_delta,
                    allow_negative=allow_negative,
                )
                if new_stock < 0 and policies.negative_stock_policy == "manager_override":
                    override_policies.add("negative_stock")
        # Belgeye bağlı otomatik ödeme silinmeden önce kasa/banka/POS hareketi kaldırılır.
        if settings.payment_allocation_engine_enabled and sale and payment_rows:
            allocated = db.execute(
                text(
                    """SELECT 1 FROM payment_allocations
                    WHERE company_id=:cid AND payment_id IN (
                        SELECT id FROM payments WHERE company_id=:cid
                          AND reference_type='order' AND reference_id=:rid
                    ) LIMIT 1"""
                ),
                {"cid": cid, "rid": transaction_id},
            ).first()
            if allocated:
                raise HTTPException(
                    409,
                    "Tahsisli satış reversal tamamlanmadan silinemez",
                )
        for payment_row in payment_rows:
            remove_payment_finance(db, cid, int(payment_row[0]))
        db.execute(
            text("DELETE FROM payments WHERE company_id=:cid AND reference_type=:rt AND reference_id=:rid"),
            {
                "cid": cid,
                "rt": "order" if sale else "purchase",
                "rid": transaction_id,
            },
        )
        # PARTİ BORCU HAREKETLERDEN ÖNCE (1B-A) — güncelleme yolundaki ikizin
        # aynı gerekçesi: DELETE'ten sonra okunacak satır kalmaz.
        _parti_geri_al(
            db, cid, reference_type=config["head"], reference_id=transaction_id
        )
        db.execute(
            text("""DELETE FROM stock_movements
            WHERE company_id=:cid AND reference_type=:rt AND reference_id=:rid"""),
            {"cid": cid, "rt": config["head"], "rid": transaction_id},
        )
        db.execute(
            text(f"DELETE FROM {config['items']} WHERE {config['item_fk']}=:id"),
            {"id": transaction_id},
        )
        db.execute(
            text(f"DELETE FROM {config['head']} WHERE id=:id AND company_id=:cid"),
            {"id": transaction_id, "cid": cid},
        )
        record_policy_overrides(
            db,
            company_id=cid,
            context=override,
            policy_names=override_policies,
            resource_type=f"{config['head']}:delete",
            resource_id=transaction_id,
        )
        if cancelled is not None:
            document_label = cancelled["document_no"] or f"#{transaction_id}"
            log_request_activity(
                db,
                request,
                cid,
                "pos.sale_cancelled" if from_pos else "sale.cancel",
                "sale",
                transaction_id,
                (
                    f"POS satışını iptal etti — {document_label}, "
                    f"{format_money_tr(cancelled['final_total'])}"
                    if from_pos
                    else (
                        f"{document_label} satışını iptal etti — "
                        f"{format_money_tr(cancelled['final_total'])}, "
                        f"müşteri: {_customer_name(db, cid, cancelled['customer_id'])}"
                    )
                ),
                {
                    "cancelled": {key: cancelled[key] for key in cancelled.keys()},
                    "origin": "pos" if from_pos else "sales",
                },
            )
        db.commit()
    # KAYDETME YOLUNDA ZATEN VARDI, SİLME YOLUNDA YOKTU — ÖLÇÜLDÜ. Bu dal
    # olmadan gövdenin içinde ADIYLA atılan her 4xx, aşağıdaki `except
    # Exception` tarafından yutulup 500'e çevriliyordu. Yalnız bu PR'ın
    # `LOT_MIKTARI_EKSIYE_DUSER` 409'unu değil, 0058'den beri orada duran
    # "Tahsisli satış reversal tamamlanmadan silinemez" 409'unu da: ikisi de
    # operatöre NE YAPACAĞINI söyleyen redlerdi ve 500 hiçbir şey söylemiyor.
    # `save_transaction`ın dalıyla BİREBİR aynı (rollback + raise).
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        if "yetersiz stok" in str(exc).lower():
            raise HTTPException(409, stock_policy_message(policies.negative_stock_policy)) from exc
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Unexpected transaction delete failure", extra={"kind": kind, "transaction_id": transaction_id})
        raise HTTPException(500, "Belge silinemedi. Lütfen tekrar deneyin.") from exc
