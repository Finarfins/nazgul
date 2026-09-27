from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import has_permission
from ..business_time import business_today
from ..db import get_db
from ..money import ZERO_MONEY, money
from ..receivables_engine import ReceivableDocument, calculate_net_receivables
from ..document_engine import SALES_IMPORT_NOTE, accounting_document_status_sql
from ..tenancy import company_id
from .reports import RESOLVED_PRODUCT_ID_SQL

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

ACTIVE_STATUSES_SQL = "('approved','completed')"
ORDER_ACCOUNTING_STATUS_SQL = accounting_document_status_sql("o.status", "o.note")
ORDER_ACCOUNTING_STATUS_SQL_NO_ALIAS = accounting_document_status_sql("status", "note")


def parse_date(value: object) -> date | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(raw[:19], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _row(db: Session, sql: str, params: dict[str, object]) -> dict:
    result = db.execute(text(sql), params).mappings().first()
    return dict(result or {})


def _rows(db: Session, sql: str, params: dict[str, object]) -> list[dict]:
    return [dict(item) for item in db.execute(text(sql), params).mappings().all()]


#: Panonun kritik stok listesi HEP 8 satır gösteriyordu; sınır artık
#: parametre çünkü WhatsApp kanalı da AYNI ölçütü kullanıyor ve orada
#: liste 5'te kesiliyor (`niyet._WA_LISTE_SINIRI`). VARSAYILAN 8 —
#: panonun davranışı DEĞİŞMEDİ.
KRITIK_URUN_SINIRI = 8


def kritik_urunler(db: Session, cid: int, limit: int = KRITIK_URUN_SINIRI):
    """Kritik seviyenin ALTINDAKİ ürünler — `Request` YOK, `cid` AÇIK.

    DIŞARI ALINDI ki pano ile WhatsApp kanalı (`app/whatsapp/yurutucu.py`
    ``kritik_stok``) AYNI ÖLÇÜTÜ kullansın. Ölçüt tek satırda yazılı ve
    ince: eşik `critical_stock` ile `minimum_stock`un BÜYÜĞÜDÜR. İki
    yüzeyin bu seçimi ayrı ayrı yazması, birinin "kritik" dediğine
    ötekinin "normal" demesi demekti.
    """
    return _rows(
        db,
        """
        SELECT id,name,stock,unit,purchase_price,sale_price,critical_stock,minimum_stock,
               oem_number,brand,location
        FROM products
        WHERE company_id=:cid AND COALESCE(active, TRUE)=TRUE
          AND stock <= CASE WHEN COALESCE(critical_stock,0) >= COALESCE(minimum_stock,0)
                           THEN COALESCE(critical_stock,0)
                           ELSE COALESCE(minimum_stock,0) END
        ORDER BY stock ASC, LOWER(name) ASC
        LIMIT :limit
        """,
        {"cid": cid, "limit": limit},
    )



def _gecikmis_alacaklar(
    documents: list[ReceivableDocument], today: date
) -> tuple[Decimal, list[ReceivableDocument]]:
    """Vadesi geçmiş alacaklar: (toplam, liste). Yaşlandırma raporu kâhindir.

    Toplam: ``remaining != 0`` olan her vadesi geçmiş belge, eksiler dahil
    (yaşlandırma yalnız ``remaining == 0``'ı atlar) — kuruşu kuruşuna aynı.
    Liste: önce netle, sonra süz. Terslenen ücret belgesi (+) ile ters kaydı
    (-, ``reversal_of_id``) tek belgede toplanır; net ``> 0`` olanlar listelenir.
    Satış ve ücret belgeleri ayrı id uzaylarındadır, anahtar türü de taşır.
    """
    overdue = [
        document
        for document in documents
        if document.due_date < today and document.remaining != ZERO_MONEY
    ]
    total = sum((document.remaining for document in overdue), ZERO_MONEY)
    groups: dict[tuple[bool, int], list[ReceivableDocument]] = {}
    for document in overdue:
        is_charge = document.document_type != "sale"
        root = document.reversal_of_id if document.reversal_of_id is not None else document.id
        groups.setdefault((is_charge, root), []).append(document)
    netted: list[ReceivableDocument] = []
    for members in groups.values():
        net = sum((member.remaining for member in members), ZERO_MONEY)
        head = next(
            (member for member in members if member.reversal_of_id is None), None
        )
        if head is None or net <= ZERO_MONEY:
            continue
        netted.append(dataclasses.replace(head, remaining=net))
    netted.sort(key=lambda document: (document.due_date, -document.remaining))
    return total, netted


@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    user = getattr(request.state, "user", None)
    role = str(user.get("role") or "") if isinstance(user, dict) else ""
    can_view_finance = has_permission(role, "finance")
    today = business_today()
    month_start = today.replace(day=1)
    trend_start = today - timedelta(days=13)
    params = {
        "cid": cid,
        "today": today.isoformat(),
        "month_start": month_start.isoformat(),
        "trend_start": trend_start.isoformat(),
        "sales_import_note": SALES_IMPORT_NOTE,
    }

    summary = _row(
        db,
        f"""
        WITH sales AS (
          SELECT
          COALESCE(SUM(CASE WHEN order_date=:today AND {ORDER_ACCOUNTING_STATUS_SQL} THEN final_total ELSE 0 END),0) AS today_sales,
          COALESCE(SUM(CASE WHEN order_date>=:month_start AND {ORDER_ACCOUNTING_STATUS_SQL} THEN final_total ELSE 0 END),0) AS month_sales,
          COALESCE(SUM(CASE WHEN {ORDER_ACCOUNTING_STATUS_SQL} THEN final_total ELSE 0 END),0) AS active_sales
          FROM orders o
          WHERE o.company_id=:cid AND {ORDER_ACCOUNTING_STATUS_SQL}
        ), purchases_summary AS (
          SELECT
          COALESCE(SUM(CASE WHEN purchase_date>=:month_start THEN final_total ELSE 0 END),0) AS month_purchases,
          COALESCE(SUM(final_total),0) AS active_purchases
          FROM purchases
          WHERE company_id=:cid AND COALESCE(status,'completed') IN {ACTIVE_STATUSES_SQL}
        ), payment_summary AS (
          SELECT
          COALESCE(SUM(CASE WHEN entity_type='customer' AND payment_date=:today THEN amount ELSE 0 END),0) AS today_collections,
          COALESCE(SUM(CASE WHEN entity_type='customer' AND payment_date>=:month_start THEN amount ELSE 0 END),0) AS month_collections,
          COALESCE(SUM(CASE WHEN entity_type='customer' THEN amount ELSE 0 END),0) AS customer_payments,
          COALESCE(SUM(CASE WHEN entity_type='supplier' THEN amount ELSE 0 END),0) AS supplier_payments
          FROM payments WHERE company_id=:cid
        ), expense_summary AS (
          SELECT COALESCE(SUM(amount),0) AS month_expenses
          FROM income_expenses
          WHERE company_id=:cid AND LOWER(COALESCE(txn_type,''))='expense'
            AND txn_date>=:month_start
        ), product_summary AS (
          SELECT
          COUNT(*) AS product_count,
          COALESCE(SUM(stock * purchase_price),0) AS stock_value,
          COALESCE(SUM(CASE WHEN stock <= CASE
              WHEN COALESCE(critical_stock,0) >= COALESCE(minimum_stock,0)
              THEN COALESCE(critical_stock,0)
              ELSE COALESCE(minimum_stock,0)
          END THEN 1 ELSE 0 END),0) AS critical_stock_count
          FROM products
          WHERE company_id=:cid AND COALESCE(active, TRUE)=TRUE
        ), customer_summary AS (
          SELECT COUNT(*) AS customer_count,
                 COALESCE(SUM(opening_balance),0) AS customer_opening
          FROM customers WHERE company_id=:cid
        ), supplier_summary AS (
          SELECT COALESCE(SUM(opening_balance),0) AS supplier_opening
          FROM suppliers WHERE company_id=:cid
        ), producer_receipt_summary AS (
          -- Kesilmiş müstahsil makbuzları tedarikçi BORCUDUR; NET ödenecek
          -- toplanır, brüt DEĞİL (stopaj/SGK vergi dairesine borçtur).
          -- YALNIZ `issued`: taslak borç doğurmamıştır, `cancelled` doğmuş
          -- borcu kaldırır, `issuing` ise CAS'ın ARA DURUMUDUR.
          SELECT COALESCE(SUM(net_payable),0) AS issued_receipts
          FROM producer_receipts WHERE company_id=:cid AND status='issued'
        ), bounced_check_summary AS (
          -- CS2: karşılıksız/iade çek borç belgesi müşteri ALACAĞIDIR. Çekle
          -- tahsilat `payment_summary`de cariyi zaten düşürdü (Seçenek A);
          -- belge onu geri yazar. Ters kayıt eksi tutarla sıfırlar.
          SELECT COALESCE(SUM(gross_amount),0) AS bounced_checks
          FROM receivable_charge_documents
          WHERE company_id=:cid AND charge_type='bounced_check'
            AND status IN ('posted','reversed') AND posted_at IS NOT NULL
        ), portfolio_summary AS (
          -- CS2 `portfolio_checks`: elde tutulan ALINAN evrak (çek + senet),
          -- henüz paraya dönmemiş: portföyde ya da tahsile verilmiş.
          SELECT COUNT(*) AS portfolio_check_count,
                 COALESCE(SUM(tutar),0) AS portfolio_check_total
          FROM cek_senetler
          WHERE company_id=:cid AND yon='alinan'
            AND portfoy_durumu IN ('portfoyde','tahsile_verildi')
        )
        SELECT sales.today_sales,sales.month_sales,purchases_summary.month_purchases,
               payment_summary.today_collections,payment_summary.month_collections,
               expense_summary.month_expenses,product_summary.product_count,
               product_summary.stock_value,product_summary.critical_stock_count,
               customer_summary.customer_count,
               customer_summary.customer_opening+sales.active_sales-payment_summary.customer_payments
                 +bounced_check_summary.bounced_checks AS customer_receivables,
               portfolio_summary.portfolio_check_count,portfolio_summary.portfolio_check_total,
               supplier_summary.supplier_opening+purchases_summary.active_purchases
                 +producer_receipt_summary.issued_receipts
                 -payment_summary.supplier_payments AS supplier_payables
        FROM sales,purchases_summary,payment_summary,expense_summary,
             product_summary,customer_summary,supplier_summary,
             producer_receipt_summary,bounced_check_summary,portfolio_summary
        """,
        params,
    )

    today_sales = money(summary.get("today_sales"))
    month_sales = money(summary.get("month_sales"))
    month_purchases = money(summary.get("month_purchases"))
    today_collections = money(summary.get("today_collections"))
    month_collections = money(summary.get("month_collections"))
    month_expenses = money(summary.get("month_expenses"))
    month_profit = month_sales - month_purchases - month_expenses

    critical_products = kritik_urunler(db, cid)

    supplier_payables = money(summary.get("supplier_payables"))

    finance_accounts = (
        _rows(
            db,
            """
            SELECT a.id,a.name,a.account_type,a.currency,a.opening_balance,a.bank_name,
                   a.opening_balance + COALESCE(SUM(CASE WHEN t.direction='in' THEN t.amount ELSE -t.amount END),0) AS balance
            FROM finance_accounts a
            LEFT JOIN finance_transactions t
              ON t.account_id=a.id AND t.company_id=a.company_id
            WHERE a.company_id=:cid AND COALESCE(a.is_active, TRUE)=TRUE
            GROUP BY a.id,a.name,a.account_type,a.currency,a.opening_balance,a.bank_name
            ORDER BY balance DESC
            LIMIT 8
            """,
            params,
        )
        if can_view_finance
        else []
    )
    cash_bank_total = sum(
        (money(account.get("balance")) for account in finance_accounts),
        ZERO_MONEY,
    )

    # H107: gecikmiş alacaklar yaşlandırma raporuyla AYNI motordan gelir.
    # `final_total - paid_amount` yalnız siparişe doğrudan bağlı tahsisi
    # görür; defterde FIFO ile kapanan bağsız tahsilatı, iadeyi ve deftere
    # işlenmiş vade farkı / servis / karşılıksız çek belgelerini görmez.
    overdue_total, overdue_documents = _gecikmis_alacaklar(
        calculate_net_receivables(db, cid, today), today
    )
    overdue_count = len(overdue_documents)
    overdue_receivables = [
        {
            "id": document.id,
            "customer_id": document.customer_id,
            "customer_name": document.customer_name,
            "due_date": document.due_date.isoformat(),
            "document_no": document.document_no or f"S-{document.id}",
            "remaining": round(document.remaining, 2),
            "days_overdue": (today - document.due_date).days,
        }
        for document in overdue_documents[:8]
    ]

    recent_sales = _rows(
        db,
        f"""
        SELECT o.id,o.order_date,o.due_date,o.final_total,o.paid_amount,o.status,o.document_no,
               c.id AS customer_id,c.name AS customer_name
        FROM orders o
        JOIN customers c ON c.id=o.customer_id AND c.company_id=o.company_id
         WHERE o.company_id=:cid AND {ORDER_ACCOUNTING_STATUS_SQL}
        ORDER BY o.order_date DESC,o.id DESC
        LIMIT 8
        """,
        params,
    )

    trend_rows = _rows(
        db,
        f"""
        SELECT order_date AS day,COALESCE(SUM(final_total),0) AS total
        FROM orders
         WHERE company_id=:cid AND {ORDER_ACCOUNTING_STATUS_SQL_NO_ALIAS}
          AND order_date>=:trend_start AND order_date<=:today
        GROUP BY order_date
        """,
        params,
    )
    trend_map = {str(item["day"])[:10]: money(item["total"]) for item in trend_rows}
    sales_trend = []
    for offset in range(14):
        day = trend_start + timedelta(days=offset)
        sales_trend.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%d.%m"),
                "total": round(trend_map.get(day.isoformat(), ZERO_MONEY), 2),
            }
        )

    top_products = _rows(
        db,
        f"""
        SELECT oi.product_name AS name,{RESOLVED_PRODUCT_ID_SQL},
               SUM(oi.quantity) AS quantity,SUM(oi.line_total) AS total
        FROM order_items oi
        JOIN orders o ON o.id=oi.order_id
         WHERE o.company_id=:cid AND oi.company_id=:cid AND {ORDER_ACCOUNTING_STATUS_SQL}
        GROUP BY oi.product_name
        ORDER BY total DESC
        LIMIT 5
        """,
        params,
    )

    recent_payments = _rows(
        db,
        """
        SELECT p.id,p.payment_date,p.amount,p.entity_type,p.entity_id,p.payment_method,p.note,
               CASE WHEN p.entity_type='customer' THEN c.name ELSE s.name END AS entity_name
        FROM payments p
        LEFT JOIN customers c
          ON p.entity_type='customer' AND c.id=p.entity_id AND c.company_id=p.company_id
        LEFT JOIN suppliers s
          ON p.entity_type='supplier' AND s.id=p.entity_id AND s.company_id=p.company_id
        WHERE p.company_id=:cid
        ORDER BY p.payment_date DESC,p.id DESC
        LIMIT 5
        """,
        params,
    )
    recent_finance = (
        _rows(
            db,
            """
            SELECT id,account_id,txn_date,direction,amount,category,description
            FROM finance_transactions
            WHERE company_id=:cid
            ORDER BY txn_date DESC,id DESC
            LIMIT 5
            """,
            params,
        )
        if can_view_finance
        else []
    )

    activity: list[dict] = []
    for sale in recent_sales[:5]:
        activity.append(
            {
                "type": "sale",
                "date": sale["order_date"],
                "title": sale["customer_name"],
                "amount": money(sale["final_total"]),
                "id": sale["id"],
                "entity_id": sale.get("customer_id"),
                "entity_type": "customer",
            }
        )
    for payment in recent_payments:
        activity.append(
            {
                "type": "collection"
                if payment["entity_type"] == "customer"
                else "payment",
                "date": payment["payment_date"],
                "title": payment.get("entity_name") or "Cari hareket",
                "amount": money(payment["amount"]),
                "id": payment["id"],
                "entity_id": payment.get("entity_id"),
                "entity_type": payment.get("entity_type"),
            }
        )
    for txn in recent_finance:
        activity.append(
            {
                "type": "finance",
                "date": txn["txn_date"],
                "title": txn.get("description") or txn.get("category") or "Finans hareketi",
                "amount": money(txn["amount"]),
                "id": txn["id"],
                "account_id": txn.get("account_id"),
            }
        )
    activity.sort(
        key=lambda item: (parse_date(item["date"]) or date.min, int(item["id"])),
        reverse=True,
    )

    return {
        "as_of": today.isoformat(),
        "today_sales": round(today_sales, 2),
        "today_collections": round(today_collections, 2),
        "month_sales": round(month_sales, 2),
        "month_collections": round(month_collections, 2),
        "month_purchases": round(month_purchases, 2),
        "month_expenses": round(month_expenses, 2),
        "month_profit": round(month_profit, 2),
        "stock_value": round(money(summary.get("stock_value")), 2),
        # Finans yetkisi olmayan role gerçek bakiye sızmasın diye maskeleniyor.
        "cash_bank_total": round(cash_bank_total, 2) if can_view_finance else 0,
        "customer_receivables": round(money(summary.get("customer_receivables")), 2),
        "supplier_payables": round(supplier_payables, 2),
        # CS2: portföydeki alınan çek/senet (portfoyde + tahsile_verildi).
        "portfolio_checks": {
            "count": int(summary.get("portfolio_check_count") or 0),
            "total": round(money(summary.get("portfolio_check_total")), 2),
        },
        "customer_count": int(summary.get("customer_count") or 0),
        "product_count": int(summary.get("product_count") or 0),
        "critical_stock_count": int(summary.get("critical_stock_count") or 0),
        "overdue_count": overdue_count,
        "overdue_total": round(overdue_total, 2),
        "recent_sales": recent_sales,
        "critical_products": critical_products,
        "overdue_receivables": overdue_receivables,
        "finance_accounts": finance_accounts,
        "sales_trend": sales_trend,
        "top_products": top_products,
        "recent_activity": activity[:10],
    }
