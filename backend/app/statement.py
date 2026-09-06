"""Cari ekstre — bir carinin dönemsel hesap özeti.

Cari 360 ekranı (``GET /api/customers/{id}``) tüm geçmişi tek bakiye olarak
gösterir. Ekstre ise bayinin çiftçiye elden verebileceği **belge**: bir tarih
aralığı, o aralıktan önceki devir (açılış bakiyesi), aralıktaki her hareket ve
her satırın ardından yürüyen bakiye.

Bakiye aritmetiği kasıtlı olarak 360 ucundaki ``current_balance`` ile birebir
aynıdır — belge borç (+), ödeme alacak (−), cari kartındaki
``opening_balance`` ise başlangıç devri::

    bakiye = opening_balance + Σ belge − Σ ödeme

Bu işaret düzeni müşteri ve tedarikçide aynıdır (tedarikçide pozitif bakiye
"biz borçluyuz" demektir). Yön burada çevrilmez: ekstrenin kapanışı 360 ucunun
bakiyesiyle mutabık kalmak zorundadır, iki uç farklı işaret üretirse ekrandaki
bakiye ile elden verilen belge çelişir.

Normal ``draft`` ve bütün ``cancelled`` belgeler hariç tutulur. BizimHesap'tan
aktarılmış stok-nötr satış taslakları ise cari borcu temsil ettiği için müşteri
ekstresine, 360 ekranındaki kuralla aynı şekilde, dahil edilir.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .business_time import business_today
from .document_engine import SALES_IMPORT_NOTE, accounting_document_status_sql
from .money import ZERO_MONEY, money
from .pos_contracts import MoneyOut

# Ekstre bir belgedir, satırları keyfî kırpılmaz; ancak sınırsız bir pencere
# (ör. on yıllık geçmiş) hem yanıtı hem PDF'i şişirir. Satırlar bu sınırla
# bounded okunur, açılış/kapanış ise HER ZAMAN SQL toplamından gelir — yani
# kırpma olsa bile bakiyeler doğru kalır, sadece ``truncated`` işaretlenir.
LINE_LIMIT = 2000

ENTITY_TYPES = ("customer", "supplier")

_CONFIG = {
    "customer": {
        "table": "customers",
        "documents": "orders",
        "entity_fk": "customer_id",
        "date_column": "order_date",
        "document_label": "Satış",
        "payment_label": "Tahsilat",
        "entity_label": "Müşteri",
        "permission": "sales",
        "missing": "Müşteri bulunamadı",
    },
    "supplier": {
        "table": "suppliers",
        "documents": "purchases",
        "entity_fk": "supplier_id",
        "date_column": "purchase_date",
        "document_label": "Alış",
        "payment_label": "Ödeme",
        "entity_label": "Tedarikçi",
        "permission": "purchases",
        "missing": "Tedarikçi bulunamadı",
    },
}

PAYMENT_LABELS = {
    "cash": "Nakit",
    "card": "Kredi Kartı / POS",
    "bank_transfer": "Havale / EFT",
    "check": "Çek",
    "promissory_note": "Senet",
    "credit": "Veresiye",
}


def config(entity_type: str) -> dict:
    if entity_type not in _CONFIG:
        raise HTTPException(400, "Geçersiz cari türü")
    return _CONFIG[entity_type]


class StatementEntity(BaseModel):
    """Ekstre başlığındaki cari kimliği."""

    id: int
    name: str
    owner_name: str | None = None
    tax_number: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None


class StatementLine(BaseModel):
    """Tek bir ekstre hareketi ve o satırdan sonraki yürüyen bakiye."""

    entry_date: str
    kind: str
    label: str
    document_no: str | None = None
    reference_id: int
    debit: MoneyOut
    credit: MoneyOut
    balance: MoneyOut


class Statement(BaseModel):
    entity_type: str
    entity: StatementEntity
    date_from: str
    date_to: str
    opening_balance: MoneyOut
    closing_balance: MoneyOut
    total_debit: MoneyOut
    total_credit: MoneyOut
    line_count: int
    truncated: bool
    lines: list[StatementLine]


def default_period(today: date | None = None) -> tuple[date, date]:
    """Varsayılan dönem: içinde bulunulan ayın başından bugüne."""
    current = today or business_today()
    return current.replace(day=1), current


def resolve_period(date_from: date | None, date_to: date | None) -> tuple[str, str]:
    start, end = default_period()
    resolved_from = date_from or start
    resolved_to = date_to or end
    if resolved_from > resolved_to:
        raise HTTPException(400, "Başlangıç tarihi bitiş tarihinden sonra olamaz")
    return resolved_from.isoformat(), resolved_to.isoformat()


def _entity_row(db: Session, cid: int, entity_type: str, entity_id: int):
    settings = config(entity_type)
    row = db.execute(
        text(f"SELECT * FROM {settings['table']} WHERE id=:id AND company_id=:cid"),
        {"id": entity_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, settings["missing"])
    return row


def _totals(
    db: Session,
    cid: int,
    entity_type: str,
    entity_id: int,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    inclusive_to: bool = True,
) -> tuple[Decimal, Decimal]:
    """Bir tarih penceresindeki belge ve ödeme toplamları (borç, alacak).

    Sınırlar ``None`` verildiğinde pencere o yönde açıktır. ``inclusive_to``
    False ise üst sınır dışlanır; devir hesabı bunu kullanır, çünkü sınır günü
    penceredeki satırlara aittir ve iki tarafta birden sayılmamalıdır.

    Tarihi boş/NULL olan eski kayıtlar ``COALESCE(...,'')`` ile boş dizeye
    düşer; boş dize her ISO tarihinden küçük olduğu için bu satırlar devire
    yazılır ve hiçbir hareket toplamdan kaybolmaz.
    """
    settings = config(entity_type)
    date_column = settings["date_column"]
    params: dict[str, object] = {"id": entity_id, "cid": cid, "etype": entity_type}
    document_status_sql = "COALESCE(d.status,'completed') NOT IN ('draft','cancelled')"
    if entity_type == "customer":
        document_status_sql = accounting_document_status_sql("d.status", "d.note")
        params["sales_import_note"] = SALES_IMPORT_NOTE
    document_window = ""
    payment_window = ""
    if date_from is not None:
        params["date_from"] = date_from
        document_window += f" AND COALESCE(d.{date_column},'')>=:date_from"
        payment_window += " AND COALESCE(p.payment_date,'')>=:date_from"
    if date_to is not None:
        params["date_to"] = date_to
        operator = "<=" if inclusive_to else "<"
        document_window += f" AND COALESCE(d.{date_column},''){operator}:date_to"
        payment_window += f" AND COALESCE(p.payment_date,''){operator}:date_to"

    debit = db.execute(
        text(
            f"""SELECT COALESCE(SUM(d.final_total),0) total
            FROM {settings['documents']} d
            WHERE d.{settings['entity_fk']}=:id AND d.company_id=:cid
               AND {document_status_sql}
              {document_window}"""
        ),
        params,
    ).scalar()
    debit = money(debit) + _makbuz_borcu(
        db, cid, entity_type, entity_id, params, date_from, date_to, inclusive_to
    )
    credit = db.execute(
        text(
            f"""SELECT COALESCE(SUM(p.amount),0) total
            FROM payments p
            WHERE p.entity_type=:etype AND p.entity_id=:id AND p.company_id=:cid
              {payment_window}"""
        ),
        params,
    ).scalar()
    return money(debit), money(credit)


# `issued_at` bir ZAMAN DAMGASIDIR, ötekiler GÜN dizgisi. PostgreSQL'de
# `COALESCE(timestamptz, '')` TİP HATASI verir, SQLite'ta ise sessizce geçer —
# yani pencere karşılaştırmasını ötekilerle aynı biçimde yazmak, kusuru
# YALNIZ üretim diyalektinde patlatırdı (D1'in `_tarih_suzgeci` dersinin
# aynısı). Bu yüzden gün ÖNCE metne indiriliyor: `SUBSTR(CAST(... AS TEXT),1,10)`
# iki diyalektte de 'YYYY-MM-DD' verir (ÖLÇÜLDÜ).
MAKBUZ_GUNU = "SUBSTR(CAST(r.issued_at AS TEXT),1,10)"


def _makbuz_borcu(
    db: Session,
    cid: int,
    entity_type: str,
    entity_id: int,
    params: dict[str, object],
    date_from: str | None,
    date_to: str | None,
    inclusive_to: bool,
) -> Decimal:
    """Kesilmiş müstahsil makbuzlarının NET ÖDENECEK toplamı (borç).

    BRÜT DEĞİL NET: stopaj ve SGK kesintileri çiftçiye DEĞİL vergi
    dairesine borçtur (`tax_liabilities`), yani tedarikçi carisine brütü
    yazmak firmanın çiftçiye olan borcunu kesintiler kadar FAZLA
    gösterirdi.

    YALNIZ `issued`: taslak henüz bir borç doğurmamıştır ve `cancelled`
    doğmuş borcu ORTADAN KALDIRIR. `purchases` tarafındaki
    `NOT IN ('draft','cancelled')` kalıbı burada OLUMLU yazılıyor çünkü
    makbuzun `issuing` ARA DURUMU da borç değildir (CAS'ın kazananı henüz
    belli değil) ve olumsuz liste onu SESSİZCE borç sayardı.
    """
    if entity_type != "supplier":
        return ZERO_MONEY
    pencere = ""
    if date_from is not None:
        pencere += f" AND COALESCE({MAKBUZ_GUNU},'')>=:date_from"
    if date_to is not None:
        operator = "<=" if inclusive_to else "<"
        pencere += f" AND COALESCE({MAKBUZ_GUNU},''){operator}:date_to"
    toplam = db.execute(
        text(
            f"""SELECT COALESCE(SUM(r.net_payable),0) total
            FROM producer_receipts r
            WHERE r.supplier_id=:id AND r.company_id=:cid
              AND r.status='issued'
              {pencere}"""
        ),
        params,
    ).scalar()
    return money(toplam or 0)


def build_statement(
    db: Session,
    cid: int,
    entity_type: str,
    entity_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Statement:
    """Ekstreyi kur: devir + pencere hareketleri + yürüyen bakiye."""
    settings = config(entity_type)
    document_status_sql = "COALESCE(d.status,'completed') NOT IN ('draft','cancelled')"
    if entity_type == "customer":
        document_status_sql = accounting_document_status_sql("d.status", "d.note")
    start, end = resolve_period(date_from, date_to)
    row = _entity_row(db, cid, entity_type, entity_id)

    before_debit, before_credit = _totals(
        db, cid, entity_type, entity_id, date_to=start, inclusive_to=False
    )
    opening = money(money(row["opening_balance"]) + before_debit - before_credit)

    window_debit, window_credit = _totals(
        db, cid, entity_type, entity_id, date_from=start, date_to=end
    )
    closing = money(opening + window_debit - window_credit)

    # SATIRLAR TOPLAMLARLA AYNI KAYNAKTAN GELMELİ: makbuz borcu `_totals`a
    # girip burada GÖRÜNMESEYDİ, ekstrenin yürüyen bakiyesi kendi kapanışını
    # TUTMAZDI ve okuyucu farkı hiçbir satırda göremezdi.
    makbuz_kolu = ""
    if entity_type == "supplier":
        makbuz_kolu = f"""UNION ALL
            SELECT COALESCE({MAKBUZ_GUNU},''), 'producer_receipt',
              r.receipt_no, NULL, r.net_payable, r.id
            FROM producer_receipts r
            WHERE r.supplier_id=:id AND r.company_id=:cid
              AND r.status='issued'
              AND COALESCE({MAKBUZ_GUNU},'')>=:date_from
              AND COALESCE({MAKBUZ_GUNU},'')<=:date_to"""

    rows = db.execute(
        text(
            f"""SELECT COALESCE(d.{settings['date_column']},'') entry_date,
              'document' kind, d.document_no document_no, NULL payment_method,
              d.final_total amount, d.id reference_id
            FROM {settings['documents']} d
            WHERE d.{settings['entity_fk']}=:id AND d.company_id=:cid
               AND {document_status_sql}
              AND COALESCE(d.{settings['date_column']},'')>=:date_from
              AND COALESCE(d.{settings['date_column']},'')<=:date_to
            UNION ALL
            SELECT COALESCE(p.payment_date,''), 'payment', NULL,
              COALESCE(p.payment_method,'cash'), p.amount, p.id
            FROM payments p
            WHERE p.entity_type=:etype AND p.entity_id=:id AND p.company_id=:cid
              AND COALESCE(p.payment_date,'')>=:date_from
              AND COALESCE(p.payment_date,'')<=:date_to
            {makbuz_kolu}
            ORDER BY 1,2,6
            LIMIT :limit"""
        ),
        {
            "id": entity_id,
            "cid": cid,
            "etype": entity_type,
            "date_from": start,
            "date_to": end,
            "limit": LINE_LIMIT + 1,
            "sales_import_note": SALES_IMPORT_NOTE,
        },
    ).mappings().all()

    truncated = len(rows) > LINE_LIMIT
    lines: list[StatementLine] = []
    balance = opening
    for item in rows[:LINE_LIMIT]:
        is_document = item["kind"] in ("document", "producer_receipt")
        amount = money(item["amount"])
        debit = amount if is_document else ZERO_MONEY
        credit = ZERO_MONEY if is_document else amount
        balance = money(balance + debit - credit)
        if item["kind"] == "producer_receipt":
            label = "Müstahsil makbuzu (net)"
        elif is_document:
            label = settings["document_label"]
        else:
            method = item["payment_method"] or "cash"
            label = f"{settings['payment_label']} ({PAYMENT_LABELS.get(method, method)})"
        lines.append(
            StatementLine(
                entry_date=item["entry_date"],
                kind=item["kind"],
                label=label,
                document_no=item["document_no"],
                reference_id=int(item["reference_id"]),
                debit=debit,
                credit=credit,
                balance=balance,
            )
        )

    return Statement(
        entity_type=entity_type,
        entity=StatementEntity(
            id=int(row["id"]),
            name=row["name"],
            owner_name=row["owner_name"],
            tax_number=row["tax_number"],
            address=row["address"],
            phone=row["phone"],
            email=row["email"],
        ),
        date_from=start,
        date_to=end,
        opening_balance=opening,
        closing_balance=closing,
        total_debit=window_debit,
        total_credit=window_credit,
        line_count=len(lines),
        truncated=truncated,
        lines=lines,
    )
