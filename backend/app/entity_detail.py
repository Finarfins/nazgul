"""Shared cari (customer/supplier) detail projection.

``GET /api/customers/{id}`` and ``GET /api/finance/suppliers/{id}`` return the
same document/payment/product/CRM envelope over two different table families, so
the query set lives here once and each router calls it with its own entity type.

This module used to be ``entity_detail_accuracy`` and installed itself by
rewriting ``route.endpoint``/``route.dependant.call`` on already-registered
routes from ``app.routers.__init__``. That patch made the shipped behaviour of
both endpoints invisible at their own ``@router.get`` definition -- the bodies in
``customers.py``/``finance.py`` were dead code that still read as authoritative.
The routers now call :func:`entity_detail` directly; the response envelope, the
``LIMIT`` bounds and the two 404 messages are unchanged.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from .business_time import business_today
from .crm import list_contacts, list_notes, list_tasks
from .document_engine import SALES_IMPORT_NOTE, accounting_document_status_sql
from .money import HUNDRED, ZERO_MONEY, money
from .receivables_engine import charge_due_date_sql
from .tenancy import company_id

_PREVIEW_LIMIT = 8
_PRODUCT_LIMIT = 50
# Belge sekmesi sayfa boyutu. Kart özeti (``_PREVIEW_LIMIT``) bilinçli olarak
# kısa kalır; TÜM geçmişi ``entity_documents`` sayfalayarak verir.
_DOCUMENT_PAGE_SIZE = 50
_DOCUMENT_PAGE_MAX = 200

_ENTITY_CONFIG: dict[str, dict[str, str]] = {
    "customer": {
        "entity_table": "customers",
        "document_table": "orders",
        "entity_fk": "customer_id",
        "document_date": "order_date",
        "item_table": "order_items",
        "item_fk": "order_id",
        "not_found": "Müşteri bulunamadı",
    },
    "supplier": {
        "entity_table": "suppliers",
        "document_table": "purchases",
        "entity_fk": "supplier_id",
        "document_date": "purchase_date",
        "item_table": "purchase_items",
        "item_fk": "purchase_id",
        "not_found": "Tedarikçi bulunamadı",
    },
}


def entity_detail(
    entity_type: str,
    entity_id: int,
    request: Request,
    db: Session,
) -> dict[str, Any]:
    config = _ENTITY_CONFIG[entity_type]
    cid = company_id(request)
    today_date = business_today()
    today = today_date.isoformat()
    document_status_sql = "COALESCE(status,'completed') NOT IN ('draft','cancelled')"
    aliased_document_status_sql = (
        "COALESCE(document.status,'completed') NOT IN ('draft','cancelled')"
    )
    if entity_type == "customer":
        document_status_sql = accounting_document_status_sql()
        aliased_document_status_sql = accounting_document_status_sql(
            "document.status", "document.note"
        )

    entity = db.execute(
        text(
            f"SELECT * FROM {config['entity_table']} "
            "WHERE id=:id AND company_id=:cid"
        ),
        {"id": entity_id, "cid": cid},
    ).mappings().first()
    if not entity:
        raise HTTPException(404, config["not_found"])

    # Sıralama BELGE TARİHİNE göredir, kayıt sırasına göre değil. Eskiden
    # ``ORDER BY id DESC`` idi; id ancak belgeler işlendikçe girildiğinde tarihle
    # aynı sırayı verir. Geçmişi içe aktarılmış bir caride id sırası Excel satır
    # sırasıdır: kart 2025 satışını 2026 satışının üstünde gösteriyordu. Daha
    # kötüsü ``LIMIT`` en büyük id'leri tutuyor, yani gerçekten yeni bir satış
    # listeden tamamen düşebiliyordu. id yalnız aynı gün içindeki belgeleri
    # ayırmak için ikincil anahtar olarak kaldı.
    documents = db.execute(
        text(
            f"""SELECT id,document_no,{config['document_date']} transaction_date,
            final_total,paid_amount,due_date,COALESCE(status,'completed') status
            FROM {config['document_table']}
            WHERE {config['entity_fk']}=:id AND company_id=:cid
            ORDER BY {config['document_date']} DESC,id DESC LIMIT :limit"""
        ),
        {"id": entity_id, "cid": cid, "limit": _PREVIEW_LIMIT},
    ).mappings().all()

    payments = db.execute(
        text(
            """SELECT id,payment_date,amount,note,
            COALESCE(payment_method,'cash') payment_method,reference_type,reference_id
            FROM payments
            WHERE entity_type=:entity_type AND entity_id=:id AND company_id=:cid
            ORDER BY payment_date DESC,id DESC LIMIT :limit"""
        ),
        {
            "entity_type": entity_type,
            "id": entity_id,
            "cid": cid,
            "limit": _PREVIEW_LIMIT,
        },
    ).mappings().all()

    products = db.execute(
        text(
            f"""SELECT item.product_id,item.product_name,SUM(item.quantity) quantity,
            SUM(item.line_total) total
            FROM {config['item_table']} item
            JOIN {config['document_table']} document
               ON document.id=item.{config['item_fk']}
              AND document.company_id=:cid
            WHERE document.{config['entity_fk']}=:id
               AND {aliased_document_status_sql}
            GROUP BY item.product_id,item.product_name
            ORDER BY total DESC LIMIT :limit"""
        ),
        {
            "id": entity_id,
            "cid": cid,
            "limit": _PRODUCT_LIMIT,
            "sales_import_note": SALES_IMPORT_NOTE,
        },
    ).mappings().all()

    document_summary = db.execute(
        text(
            f"""SELECT
            COALESCE(SUM(CASE
              WHEN {document_status_sql}
              THEN final_total ELSE 0 END),0) document_total,
            COALESCE(SUM(CASE
              WHEN due_date IS NOT NULL AND due_date<>'' AND due_date<:today
               AND {document_status_sql}
              THEN CASE
                WHEN COALESCE(final_total,0)-COALESCE(paid_amount,0)>0
                THEN COALESCE(final_total,0)-COALESCE(paid_amount,0)
                ELSE 0 END
              ELSE 0 END),0) overdue_amount,
            COALESCE(SUM(CASE
              WHEN {document_status_sql}
              THEN 1 ELSE 0 END),0) document_count,
            -- Önizlemedeki ``documents`` listesi duruma göre SÜZÜLMEZ, bu yüzden
            -- "30 belgenin 8'i" sayacı muhasebe süzgeçli ``document_count``
            -- olamaz; ikisi karıştırılırsa kullanıcıya olmayan bir eksik gösterilir.
            COUNT(*) document_count_all,
            MAX(CASE
              WHEN {document_status_sql}
              THEN {config['document_date']} ELSE NULL END) last_activity
            FROM {config['document_table']}
            WHERE {config['entity_fk']}=:id AND company_id=:cid"""
        ),
        {
            "id": entity_id,
            "cid": cid,
            "today": today,
            "sales_import_note": SALES_IMPORT_NOTE,
        },
    ).mappings().one()

    payment_total = money(
        db.execute(
            text(
                """SELECT COALESCE(SUM(amount),0) FROM payments
                WHERE entity_type=:entity_type AND entity_id=:id AND company_id=:cid"""
            ),
            {"entity_type": entity_type, "id": entity_id, "cid": cid},
        ).scalar()
    )
    document_total = money(document_summary["document_total"])
    overdue_amount = money(document_summary["overdue_amount"])
    # Posted late-fee/service-fee charge documents are receivables that never
    # touched the ``orders`` table, so the order-only ``document_total`` misses
    # them. Mirror ``receivables_engine._late_fee_receivables`` exactly: same
    # tenant scope, same status/posted/period filter, reversals net out because
    # a reversal document carries the negative counter-gross. The GROSS is added
    # here (not gross-minus-applied) because any payment allocated to a charge is
    # already a ``payments`` row inside ``payment_total`` -- subtracting the
    # applied amount as well would count that payment twice.
    #
    # Overdue uses the SAME due date the engine does, from the one shared rule
    # in ``receivables_engine.charge_due_date_sql`` (service_fee is due on its
    # own snapshot, late_fee on period_end -- see that docstring for why the
    # column cannot be collapsed). The ``period_end<=:as_of`` filter below is a
    # different question ("is this document in scope for this as-of date") and
    # stays as it was. Allocations are clamped to ``effective_date<=as_of`` so
    # the applied amount matches the engine's ledger view at the same as-of date.
    charge_total = ZERO_MONEY
    charge_overdue = ZERO_MONEY
    if entity_type == "customer":
        charge_row = db.execute(
            text(
                f"""SELECT
                COALESCE(SUM(d.gross_amount),0) charge_total,
                COALESCE(SUM(CASE WHEN {charge_due_date_sql('d')}<:as_of
                  THEN d.gross_amount-COALESCE(a.applied,0) ELSE 0 END),0) charge_overdue
                FROM receivable_charge_documents d
                LEFT JOIN (
                  SELECT receivable_charge_id,COALESCE(SUM(
                    CASE WHEN reversal_of_allocation_id IS NULL
                         THEN amount ELSE -amount END),0) applied
                  FROM payment_allocations
                  WHERE company_id=:cid AND receivable_charge_id IS NOT NULL
                    AND effective_date<=:as_of
                  GROUP BY receivable_charge_id
                ) a ON a.receivable_charge_id=d.id
                WHERE d.company_id=:cid AND d.customer_id=:id
                  AND d.charge_type IN ('late_fee','service_fee')
                  AND d.status IN ('posted','reversed')
                  AND d.posted_at IS NOT NULL
                  AND d.period_end<=:as_of"""
            ),
            {"id": entity_id, "cid": cid, "as_of": today_date},
        ).mappings().one()
        charge_total = money(charge_row["charge_total"])
        charge_overdue = money(charge_row["charge_overdue"])
        charge_documents = _charge_documents(db, cid, entity_id, today_date)
    else:
        charge_documents = []
    balance = (
        money(entity["opening_balance"])
        + document_total
        + charge_total
        - payment_total
    )
    overdue_amount = money(overdue_amount + charge_overdue)
    risk = money(entity.get("risk_limit"))
    summary = {
        "document_total": document_total,
        "payment_total": payment_total,
        "current_balance": balance,
        "document_count": int(document_summary["document_count"] or 0),
        "overdue_amount": overdue_amount,
        "risk_limit": risk,
        "risk_available": risk - balance if risk > 0 else None,
        "risk_exceeded": risk > 0 and balance > risk,
        "risk_usage_percent": round((balance / risk) * HUNDRED, 1) if risk > 0 else 0,
        "last_activity": document_summary["last_activity"],
        # Karttaki liste yalnız bir ÖNİZLEMEDİR. Arayüz "30 belgenin 8'i"
        # diyebilsin ve kullanıcı geri kalanın kaybolmadığını görsün diye toplam
        # buradan taşınır. Bu alan olmadan kısalan liste sessiz veri kaybı gibi
        # görünüyordu: 30 satışı olan bir caride yalnız en yeni 8'i çıkıyor,
        # 2024–2025 geçmişi hiçbir ekranda görünmüyordu.
        "document_count_all": int(document_summary["document_count_all"] or 0),
        "document_preview_limit": _PREVIEW_LIMIT,
    }

    result: dict[str, Any] = {
        entity_type: dict(entity),
        "entity": dict(entity),
        "entity_type": entity_type,
        "documents": [dict(row) for row in documents],
        "payments": [dict(row) for row in payments],
        "products": [dict(row) for row in products],
        "notes": list_notes(db, cid, entity_type, entity_id),
        "contacts": list_contacts(db, cid, entity_type, entity_id),
        "tasks": list_tasks(db, cid, entity_type, entity_id),
        "summary": summary,
    }
    if entity_type == "customer":
        result["sales"] = result["documents"]
        result["charge_documents"] = charge_documents
    return result


def entity_documents(
    entity_type: str,
    entity_id: int,
    request: Request,
    db: Session,
    *,
    offset: int = 0,
    limit: int = _DOCUMENT_PAGE_SIZE,
    year: str | None = None,
) -> dict[str, Any]:
    """Carinin TÜM satış/alış belgelerini sayfalayarak döndürür.

    ``entity_detail`` içindeki liste bilinçli olarak kısa bir önizlemedir; kart
    açılışını hafif tutar. Ama tek kaynak o olduğu sürece eski geçmiş uygulamada
    HİÇBİR YERDE görünmüyordu — 30 satışı olan bir caride yalnız en yeni 8'i
    listeleniyor, 2024–2025 belgeleri kullanıcı için yok hükmünde kalıyordu.
    Bu uç, o geçmişi sayfalayarak verir.

    Sıralama ``entity_detail`` ile AYNI olmalıdır (belge tarihi, sonra id);
    ayrışırsa önizlemenin ilk satırı ile sekmenin ilk satırı farklı belge olur
    ve liste "atlıyor" gibi görünür.

    ``years`` her zaman TÜM geçmiş üzerinden hesaplanır; ``year`` süzgeci onu
    daraltmaz. Arayüz yıl başlıklarını (yıl · adet · tutar) tek istekle çizip
    yalnız açılan yılın belgelerini çeker: 42 belgelik bir cari kartı alt alta
    uzamak yerine dört satırlık bir özetle açılır.
    """
    config = _ENTITY_CONFIG.get(entity_type)
    if config is None:
        raise HTTPException(404, "Bilinmeyen cari tipi")
    cid = company_id(request)
    limit = max(1, min(int(limit), _DOCUMENT_PAGE_MAX))
    offset = max(0, int(offset))

    exists = db.execute(
        text(
            f"SELECT 1 FROM {config['entity_table']} "
            "WHERE id=:id AND company_id=:cid"
        ),
        {"id": entity_id, "cid": cid},
    ).scalar()
    if not exists:
        raise HTTPException(404, config["not_found"])

    # Yıl, tarihin ilk dört hanesinden okunur. ``CAST(... AS VARCHAR)`` hem
    # SQLite'ta (metin tarih) hem PostgreSQL'de (date/varchar) aynı sonucu verir;
    # EXTRACT(YEAR ...) SQLite'ta yoktur, strftime da PostgreSQL'de.
    yil_ifadesi = f"SUBSTR(CAST({config['document_date']} AS VARCHAR),1,4)"
    kosul = f"{config['entity_fk']}=:id AND company_id=:cid"
    parametreler: dict[str, Any] = {"id": entity_id, "cid": cid}
    if year:
        kosul += f" AND {yil_ifadesi}=:year"
        parametreler["year"] = str(year)[:4]

    yillar = db.execute(
        text(
            f"""SELECT {yil_ifadesi} yil,COUNT(*) adet,
            COALESCE(SUM(final_total),0) tutar
            FROM {config['document_table']}
            WHERE {config['entity_fk']}=:id AND company_id=:cid
            GROUP BY {yil_ifadesi} ORDER BY yil DESC"""
        ),
        {"id": entity_id, "cid": cid},
    ).mappings().all()

    total = int(
        db.execute(
            text(f"SELECT COUNT(*) FROM {config['document_table']} WHERE {kosul}"),
            parametreler,
        ).scalar()
        or 0
    )
    rows = db.execute(
        text(
            f"""SELECT id,document_no,{config['document_date']} transaction_date,
            final_total,paid_amount,due_date,COALESCE(status,'completed') status
            FROM {config['document_table']}
            WHERE {kosul}
            ORDER BY {config['document_date']} DESC,id DESC
            LIMIT :limit OFFSET :offset"""
        ),
        {**parametreler, "limit": limit, "offset": offset},
    ).mappings().all()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(rows) < total,
        "year": str(year)[:4] if year else None,
        "years": [
            {
                "year": str(row["yil"]),
                "count": int(row["adet"] or 0),
                "total": money(row["tutar"]),
            }
            for row in yillar
            if row["yil"]
        ],
    }


def _charge_documents(
    db: Session,
    cid: int,
    customer_id: int,
    as_of: date,
) -> list[dict[str, Any]]:
    """Currently-open late-fee/service-fee charge documents for the cari card.

    Only live charges are listed (posted originals, reversed and reversal
    documents excluded), so the breakdown shows what the customer still owes on
    fees. The ``document_no`` label and ``remaining`` math mirror
    ``receivables_engine._late_fee_receivables`` so the detail card agrees with
    the receivables screens.
    """
    rows = db.execute(
        text(
            """SELECT d.id,d.charge_type,d.period_end,d.due_date_snapshot,
            d.gross_amount,d.revision_no,d.work_order_id,w.work_order_no,
            COALESCE(a.applied,0) applied
            FROM receivable_charge_documents d
            LEFT JOIN work_orders w
              ON w.id=d.work_order_id AND w.company_id=d.company_id
            LEFT JOIN (
              SELECT receivable_charge_id,COALESCE(SUM(
                CASE WHEN reversal_of_allocation_id IS NULL
                     THEN amount ELSE -amount END),0) applied
              FROM payment_allocations
              WHERE company_id=:cid AND receivable_charge_id IS NOT NULL
                AND effective_date<=:as_of
              GROUP BY receivable_charge_id
            ) a ON a.receivable_charge_id=d.id
            WHERE d.company_id=:cid AND d.customer_id=:id
              AND d.charge_type IN ('late_fee','service_fee')
              AND d.status='posted' AND d.reversal_of_document_id IS NULL
              AND d.posted_at IS NOT NULL AND d.period_end<=:as_of
              AND d.gross_amount-COALESCE(a.applied,0)>0
            ORDER BY d.due_date_snapshot DESC,d.id DESC
            LIMIT :limit"""
        ),
        {"id": customer_id, "cid": cid, "as_of": as_of, "limit": _PREVIEW_LIMIT},
    ).mappings().all()
    documents: list[dict[str, Any]] = []
    for row in rows:
        charge_type = str(row["charge_type"])
        gross = money(row["gross_amount"])
        applied = money(row["applied"])
        remaining = money(gross - applied)
        if remaining <= ZERO_MONEY:
            continue
        if charge_type == "service_fee":
            work_order_no = row["work_order_no"] or f"SE-{row['work_order_id']}"
            document_no = f"{work_order_no}-R{int(row['revision_no'])}"
        else:
            document_no = f"VF-{int(row['id'])}-R{int(row['revision_no'])}"
        documents.append(
            {
                "id": int(row["id"]),
                "charge_type": charge_type,
                "document_no": document_no,
                "transaction_date": str(row["period_end"]),
                "due_date": str(row["due_date_snapshot"]),
                "gross_amount": gross,
                "applied": applied,
                "remaining": remaining,
                # The originating work order, so the cari card can open the
                # service detail. NULL for late fees, which have no work order.
                "work_order_id": (
                    int(row["work_order_id"])
                    if row["work_order_id"] is not None
                    else None
                ),
            }
        )
    return documents


