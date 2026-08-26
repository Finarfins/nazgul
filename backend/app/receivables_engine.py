from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import settings
from .document_engine import SALES_IMPORT_NOTE, accounting_document_status_sql
from .money import ZERO_MONEY, money

logger = logging.getLogger("yerel_hesap")


@dataclass
class ReceivableDocument:
    id: int
    customer_id: int
    customer_name: str
    transaction_date: str
    document_no: str | None
    due_date: date
    total: Decimal
    applied: Decimal
    remaining: Decimal
    document_type: str = "sale"


@dataclass(frozen=True)
class PrincipalChange:
    effective_date: date
    amount: Decimal
    source_type: str
    source_id: int


@dataclass(frozen=True)
class PrincipalTimeline:
    order_id: int
    customer_id: int
    customer_name: str
    document_no: str | None
    order_date: date
    due_date: date
    original_principal: Decimal
    changes: tuple[PrincipalChange, ...]


def parse_receivable_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text_value = str(value or "").strip()[:10]
    for date_format in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text_value, date_format).date()
        except ValueError:
            continue
    raise ValueError("Geçersiz alacak vade tarihi")


def charge_due_date_sql(alias: str = "d") -> str:
    """SQL for a receivable charge document's due date, under table ``alias``.

    ``due_date_snapshot`` is an OVERLOADED column. On a service fee it is the
    charge's own invoice due date. On a late fee it is the due date of the
    ORIGINAL SALE ORDER -- the date interest started accruing -- and not a due
    date of the fee at all (``late_fee_charge_engine.py:427``; the 422 guard at
    ``late_fee_charge_engine.py:200`` refuses a period starting on or before it,
    so every posted late fee satisfies
    ``due_date_snapshot < period_start <= period_end``). A late fee falls due
    when its accrual period closes, i.e. on ``period_end``, so
    ``due_date_snapshot`` MUST NOT enter its overdue math: a flat
    ``COALESCE(due_date_snapshot,period_end)`` would report a fee as not yet due
    over a range that has already been billed. Splitting the overloaded column
    in two is backlogged as separate work.

    The branch is charge-type aware on purpose -- but it lives here ONCE. The
    receivables engine and both cari projections (detail card and list) read the
    rule from this single source, so the three cannot drift apart again.

    ``period_end<=:as_of`` at the call sites answers a different question ("is
    this document in scope for this as-of date") and is deliberately untouched.
    """
    return (
        f"(CASE WHEN {alias}.charge_type='service_fee' "
        f"THEN {alias}.due_date_snapshot ELSE {alias}.period_end END)"
    )


def charge_due_date(row: Any) -> date:
    """Python twin of :func:`charge_due_date_sql` for a charge-document row.

    Kept beside the SQL so the engine and the two cari projections cannot drift
    apart; see there for why the branch is charge-type aware.
    """
    is_service_fee = str(row["charge_type"]) == "service_fee"
    return parse_receivable_date(
        row["due_date_snapshot"] if is_service_fee else row["period_end"]
    )


def normalized_date_sql(date_column: str, dialect_name: str) -> str:
    if dialect_name == "sqlite":
        return (
            f"CASE WHEN {date_column} LIKE '__.__.____%' "
            f"THEN substr({date_column},7,4)||'-'||substr({date_column},4,2)||'-'||substr({date_column},1,2) "
            f"ELSE substr({date_column},1,10) END"
        )
    return (
        f"CASE WHEN CAST({date_column} AS TEXT) LIKE '__.__.____%' "
        f"THEN TO_CHAR(TO_DATE(SUBSTRING(CAST({date_column} AS TEXT) FROM 1 FOR 10),'DD.MM.YYYY'),'YYYY-MM-DD') "
        f"ELSE SUBSTRING(CAST({date_column} AS TEXT) FROM 1 FOR 10) END"
    )


def _indexed(rows: Any, total_column: str = "total") -> dict[tuple[int, int], Decimal]:
    return {
        (int(row["movement_key"]), int(row["entity_id"])): money(row[total_column])
        for row in rows
        if row["movement_key"] is not None and row["entity_id"] is not None
    }


def _customer_totals(rows: Any) -> dict[int, Decimal]:
    return {
        int(row["entity_id"]): money(row["total"])
        for row in rows
        if row["entity_id"] is not None
    }


def _movement_totals(
    db: Session,
    company_id: int,
    as_of: date,
    *,
    include_payments: bool = True,
) -> tuple[
    dict[tuple[int, int], Decimal],
    dict[tuple[int, int], Decimal],
    dict[int, Decimal],
    dict[tuple[int, int], Decimal],
    dict[int, Decimal],
]:
    dialect_name = db.get_bind().dialect.name
    payment_date = normalized_date_sql("payment_date", dialect_name)
    return_date = normalized_date_sql("return_date", dialect_name)
    params = {"cid": company_id, "as_of": as_of.isoformat()}
    linked_payments: Any = []
    unlinked_payments: Any = []
    if include_payments:
        linked_payments = db.execute(
            text(
                """SELECT reference_id movement_key,entity_id,
                COALESCE(SUM(CASE WHEN """
                + payment_date
                + """<=:as_of THEN amount ELSE 0 END),0) as_of_total,
                COALESCE(SUM(amount),0) all_time_total
                FROM payments
                WHERE company_id=:cid AND entity_type='customer'
                  AND reference_type='order' AND reference_id IS NOT NULL
                GROUP BY reference_id,entity_id"""
            ),
            params,
        ).mappings().all()
        unlinked_payments = db.execute(
            text(
                """SELECT entity_id,COALESCE(SUM(amount),0) total
                FROM payments
                WHERE company_id=:cid AND entity_type='customer'
                  AND (reference_type IS NULL OR reference_type<>'order'
                       OR reference_id IS NULL)
                  AND """
                + payment_date
                + """<=:as_of
                GROUP BY entity_id"""
            ),
            params,
        ).mappings()
    linked_returns = db.execute(
        text(
            """SELECT source_id movement_key,entity_id,COALESCE(SUM(total),0) total
            FROM returns
            WHERE company_id=:cid AND return_type='sale_return'
              AND source_type='order' AND source_id IS NOT NULL
              AND COALESCE(status,'completed') NOT IN ('draft','cancelled')
              AND """
            + return_date
            + """<=:as_of
            GROUP BY source_id,entity_id"""
        ),
        params,
    ).mappings()
    unlinked_returns = db.execute(
        text(
            """SELECT entity_id,COALESCE(SUM(total),0) total
            FROM returns
            WHERE company_id=:cid AND return_type='sale_return'
              AND (source_type IS NULL OR source_type<>'order'
                   OR source_id IS NULL)
              AND COALESCE(status,'completed') NOT IN ('draft','cancelled')
              AND """
            + return_date
            + """<=:as_of
            GROUP BY entity_id"""
        ),
        params,
    ).mappings()
    return (
        _indexed(linked_payments, "as_of_total"),
        _indexed(linked_payments, "all_time_total"),
        _customer_totals(unlinked_payments),
        _indexed(linked_returns),
        _customer_totals(unlinked_returns),
    )


def _ledger_totals(
    db: Session,
    company_id: int,
    as_of: date,
) -> tuple[dict[int, Decimal], dict[int, Decimal]]:
    allocations = {
        int(row["order_id"]): money(row["total"])
        for row in db.execute(
            text(
                """SELECT order_id,COALESCE(SUM(
                    CASE WHEN reversal_of_allocation_id IS NULL
                         THEN amount ELSE -amount END
                ),0) total
                FROM payment_allocations
                WHERE company_id=:cid AND effective_date<=:as_of
                  AND order_id IS NOT NULL
                GROUP BY order_id"""
            ),
            {"cid": company_id, "as_of": as_of},
        ).mappings()
    }
    residuals = {
        int(row["order_id"]): money(row["amount"])
        for row in db.execute(
            text(
                """SELECT order_id,amount
                FROM receivable_legacy_residuals
                WHERE company_id=:cid AND status IN ('confirmed','resolved')"""
            ),
            {"cid": company_id},
        ).mappings()
    }
    return allocations, residuals


def calculate_receivables(
    db: Session,
    company_id: int,
    as_of: date,
    *,
    payment_term: str | None = None,
) -> list[ReceivableDocument]:
    dialect_name = db.get_bind().dialect.name
    order_date = normalized_date_sql("o.order_date", dialect_name)
    term_filter = ""
    params: dict[str, object] = {
        "cid": company_id,
        "as_of": as_of.isoformat(),
        "sales_import_note": SALES_IMPORT_NOTE,
    }
    if payment_term is not None:
        term_filter = " AND COALESCE(o.payment_term,'PESIN')=:payment_term"
        params["payment_term"] = payment_term
    rows = db.execute(
        text(
            f"""SELECT o.id,o.customer_id,c.name customer_name,o.order_date,
            o.document_no,o.due_date,o.final_total,COALESCE(o.paid_amount,0) paid_amount
            FROM orders o
            JOIN customers c ON c.id=o.customer_id AND c.company_id=o.company_id
            WHERE o.company_id=:cid
              AND o.due_date IS NOT NULL AND o.due_date<>''
               AND {accounting_document_status_sql('o.status', 'o.note')}
              AND """
            + order_date
            + """<=:as_of"""
            + term_filter
            + """ ORDER BY c.name,c.id,o.due_date,o.id"""
        ),
        params,
    ).mappings().all()
    ledger_enabled = settings.payment_allocation_engine_enabled
    (
        linked_payments,
        all_time_linked_payments,
        unlinked_payments,
        linked_returns,
        unlinked_returns,
    ) = _movement_totals(
        db,
        company_id,
        as_of,
        include_payments=not ledger_enabled,
    )
    ledger_allocations, ledger_residuals = (
        _ledger_totals(db, company_id, as_of)
        if ledger_enabled
        else ({}, {})
    )

    documents_by_customer: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        try:
            due_date = parse_receivable_date(row["due_date"])
        except ValueError:
            logger.warning(
                "Alacak hesabında geçersiz vade tarihi atlandı",
                extra={
                    "company_id": company_id,
                    "order_id": int(row["id"]),
                    "due_date": str(row["due_date"]),
                },
            )
            continue
        order_id = int(row["id"])
        customer_id = int(row["customer_id"])
        key = (order_id, customer_id)
        if ledger_enabled:
            direct = money(
                ledger_allocations.get(order_id, ZERO_MONEY)
                + linked_returns.get(key, ZERO_MONEY)
            )
            residual = ledger_residuals.get(order_id, ZERO_MONEY)
        else:
            direct = money(
                linked_payments.get(key, ZERO_MONEY)
                + linked_returns.get(key, ZERO_MONEY)
            )
            residual = max(
                ZERO_MONEY,
                money(row["paid_amount"])
                - all_time_linked_payments.get(key, ZERO_MONEY),
            )
        remaining = max(ZERO_MONEY, money(money(row["final_total"]) - direct - residual))
        documents_by_customer.setdefault(customer_id, []).append(
            {"row": row, "due_date": due_date, "remaining": remaining}
        )

    result: list[ReceivableDocument] = []
    for customer_id, documents in documents_by_customer.items():
        fifo_credit = money(
            (
                ZERO_MONEY
                if ledger_enabled
                else unlinked_payments.get(customer_id, ZERO_MONEY)
            )
            + unlinked_returns.get(customer_id, ZERO_MONEY)
        )
        documents.sort(key=lambda item: (item["due_date"], item["row"]["id"]))
        for document in documents:
            applied_fifo = min(document["remaining"], fifo_credit)
            remaining = money(document["remaining"] - applied_fifo)
            fifo_credit = money(fifo_credit - applied_fifo)
            row = document["row"]
            total = money(row["final_total"])
            result.append(
                ReceivableDocument(
                    id=int(row["id"]),
                    customer_id=customer_id,
                    customer_name=str(row["customer_name"]),
                    transaction_date=str(row["order_date"]),
                    document_no=row["document_no"],
                    due_date=document["due_date"],
                    total=total,
                    applied=money(total - remaining),
                    remaining=remaining,
                )
            )
    return result


def _late_fee_receivables(
    db: Session,
    company_id: int,
    as_of: date,
) -> list[ReceivableDocument]:
    rows = db.execute(
        text(
            """SELECT d.id,d.customer_id,c.name customer_name,d.period_end,
            d.due_date_snapshot,d.revision_no,d.gross_amount,d.charge_type,
            d.work_order_id,w.work_order_no
            FROM receivable_charge_documents d
            JOIN customers c
              ON c.id=d.customer_id AND c.company_id=d.company_id
            LEFT JOIN work_orders w
              ON w.id=d.work_order_id AND w.company_id=d.company_id
            WHERE d.company_id=:cid
              AND d.charge_type IN ('late_fee','service_fee')
              AND d.status IN ('posted','reversed')
              AND d.posted_at IS NOT NULL
              AND d.period_end<=:as_of
            ORDER BY c.name,c.id,d.period_end,d.id"""
        ),
        {"cid": company_id, "as_of": as_of},
    ).mappings().all()
    # Derivation is always ledger-aware, independent of the write-path flag:
    # reversals are netted out, and an empty ledger reproduces the pre-V2c
    # numbers bit for bit (applied=0, remaining=gross).
    applied_by_charge = {
        int(row["receivable_charge_id"]): money(row["total"])
        for row in db.execute(
            text(
                """SELECT receivable_charge_id,COALESCE(SUM(
                    CASE WHEN reversal_of_allocation_id IS NULL
                         THEN amount ELSE -amount END
                ),0) total
                FROM payment_allocations
                WHERE company_id=:cid AND receivable_charge_id IS NOT NULL
                  AND effective_date<=:as_of
                GROUP BY receivable_charge_id"""
            ),
            {"cid": company_id, "as_of": as_of},
        ).mappings()
    }
    documents: list[ReceivableDocument] = []
    for row in rows:
        charge_id = int(row["id"])
        total = money(row["gross_amount"])
        applied = applied_by_charge.get(charge_id, ZERO_MONEY)
        charge_type = str(row["charge_type"])
        is_service_fee = charge_type == "service_fee"
        due_date = charge_due_date(row)
        document_no = (
            f"{row['work_order_no'] or 'SE-'+str(row['work_order_id'])}"
            f"-R{int(row['revision_no'])}"
            if is_service_fee
            else f"VF-{charge_id}-R{int(row['revision_no'])}"
        )
        documents.append(
            ReceivableDocument(
                id=charge_id,
                customer_id=int(row["customer_id"]),
                customer_name=str(row["customer_name"]),
                transaction_date=parse_receivable_date(row["period_end"]).isoformat(),
                document_no=document_no,
                due_date=due_date,
                total=total,
                applied=applied,
                remaining=money(total - applied),
                document_type=charge_type,
            )
        )
    return documents


def calculate_net_receivables(
    db: Session,
    company_id: int,
    as_of: date,
) -> list[ReceivableDocument]:
    """Return sale principal and posted late fees from one receivable source."""
    return [
        *calculate_receivables(db, company_id, as_of),
        *_late_fee_receivables(db, company_id, as_of),
    ]


def build_principal_timelines(
    db: Session,
    company_id: int,
    customer_id: int,
    as_of: date,
) -> list[PrincipalTimeline]:
    """Build the customer principal timeline without depending on the cutover flag.

    Payments represented in the allocation ledger are read only from that
    ledger; raw payments without any ledger row retain the legacy linked/FIFO
    behavior. Returns and the one-time confirmed legacy residual are then
    applied exactly once. This partition keeps preview calculations usable
    before and after allocation-engine cutover without counting a payment twice.
    """

    dialect_name = db.get_bind().dialect.name
    order_date_sql = normalized_date_sql("o.order_date", dialect_name)
    payment_date_sql = normalized_date_sql("p.payment_date", dialect_name)
    return_date_sql = normalized_date_sql("r.return_date", dialect_name)
    params = {
        "cid": company_id,
        "customer_id": customer_id,
        "as_of": as_of.isoformat(),
        "sales_import_note": SALES_IMPORT_NOTE,
    }
    order_rows = db.execute(
        text(
            f"""SELECT o.id,o.customer_id,c.name customer_name,o.document_no,
            o.order_date,o.due_date,o.final_total,o.payment_term
            FROM orders o
            JOIN customers c ON c.id=o.customer_id AND c.company_id=o.company_id
            WHERE o.company_id=:cid AND o.customer_id=:customer_id
              AND o.due_date IS NOT NULL AND o.due_date<>''
               AND {accounting_document_status_sql('o.status', 'o.note')}
              AND """
            + order_date_sql
            + """<=:as_of
            ORDER BY o.id"""
        ),
        params,
    ).mappings().all()

    documents: dict[int, dict[str, Any]] = {}
    for row in order_rows:
        try:
            order_date = parse_receivable_date(row["order_date"])
            due_date = parse_receivable_date(row["due_date"])
        except ValueError:
            logger.warning(
                "Anapara zaman çizelgesinde geçersiz belge tarihi atlandı",
                extra={
                    "company_id": company_id,
                    "order_id": int(row["id"]),
                },
            )
            continue
        order_id = int(row["id"])
        documents[order_id] = {
            "row": row,
            "order_date": order_date,
            "due_date": due_date,
            "total": money(row["final_total"]),
            "balance": ZERO_MONEY,
            "changes": [],
        }
    if not documents:
        return []

    events: dict[date, list[tuple[int, int, str, int, Decimal]]] = {}

    def add_event(
        effective_date: date,
        priority: int,
        order_id: int,
        source_type: str,
        source_id: int,
        amount: Decimal,
    ) -> None:
        if effective_date <= as_of:
            events.setdefault(effective_date, []).append(
                (priority, order_id, source_type, source_id, amount)
            )

    for order_id, document in documents.items():
        add_event(
            document["order_date"],
            0,
            order_id,
            "order",
            order_id,
            document["total"],
        )

    allocation_rows = db.execute(
        text(
            """SELECT a.id,a.payment_id,a.order_id,a.amount,a.effective_date,
            a.reversal_of_allocation_id
            FROM payment_allocations a
            JOIN orders o ON o.id=a.order_id AND o.company_id=a.company_id
            JOIN payments p ON p.id=a.payment_id AND p.company_id=a.company_id
              AND p.entity_type='customer' AND p.entity_id=o.customer_id
            WHERE a.company_id=:cid AND o.customer_id=:customer_id
              AND a.effective_date<=:as_of
            ORDER BY a.effective_date,a.id"""
        ),
        {"cid": company_id, "customer_id": customer_id, "as_of": as_of},
    ).mappings()
    for row in allocation_rows:
        order_id = int(row["order_id"])
        if order_id not in documents:
            continue
        signed_amount = money(row["amount"])
        if row["reversal_of_allocation_id"] is None:
            signed_amount = -signed_amount
        add_event(
            parse_receivable_date(row["effective_date"]),
            1,
            order_id,
            "allocation",
            int(row["id"]),
            signed_amount,
        )

    raw_payment_rows = db.execute(
        text(
            """SELECT p.id,p.amount,p.payment_date,p.reference_type,p.reference_id
            FROM payments p
            WHERE p.company_id=:cid AND p.entity_type='customer'
              AND p.entity_id=:customer_id
              AND """
            + payment_date_sql
            + """<=:as_of
              AND NOT EXISTS(
                  SELECT 1 FROM payment_allocations a
                  WHERE a.company_id=p.company_id AND a.payment_id=p.id
              )
            ORDER BY """
            + payment_date_sql
            + """,p.id"""
        ),
        params,
    ).mappings()
    for row in raw_payment_rows:
        effective_date = parse_receivable_date(row["payment_date"])
        reference_id = row["reference_id"]
        if row["reference_type"] == "order" and reference_id is not None:
            order_id = int(reference_id)
            if order_id in documents:
                add_event(
                    effective_date,
                    1,
                    order_id,
                    "payment",
                    int(row["id"]),
                    -money(row["amount"]),
                )
        elif row["reference_type"] is None and reference_id is None:
            add_event(
                effective_date,
                2,
                0,
                "unlinked_payment",
                int(row["id"]),
                money(row["amount"]),
            )

    return_rows = db.execute(
        text(
            """SELECT r.id,r.total,r.return_date,r.source_type,r.source_id
            FROM returns r
            WHERE r.company_id=:cid AND r.return_type='sale_return'
              AND r.entity_id=:customer_id
              AND COALESCE(r.status,'completed') NOT IN ('draft','cancelled')
              AND """
            + return_date_sql
            + """<=:as_of
            ORDER BY """
            + return_date_sql
            + """,r.id"""
        ),
        params,
    ).mappings()
    for row in return_rows:
        effective_date = parse_receivable_date(row["return_date"])
        source_id = row["source_id"]
        if row["source_type"] == "order" and source_id is not None:
            order_id = int(source_id)
            if order_id in documents:
                add_event(
                    effective_date,
                    1,
                    order_id,
                    "return",
                    int(row["id"]),
                    -money(row["total"]),
                )
        elif row["source_type"] is None and source_id is None:
            add_event(
                effective_date,
                2,
                0,
                "unlinked_return",
                int(row["id"]),
                money(row["total"]),
            )

    residual_rows = db.execute(
        text(
            """SELECT id,order_id,amount
            FROM receivable_legacy_residuals
            WHERE company_id=:cid AND status IN ('confirmed','resolved')
            ORDER BY order_id,id"""
        ),
        {"cid": company_id},
    ).mappings()
    for row in residual_rows:
        order_id = int(row["order_id"])
        if order_id in documents:
            add_event(
                documents[order_id]["order_date"],
                1,
                order_id,
                "legacy_residual",
                int(row["id"]),
                -money(row["amount"]),
            )

    fifo_credit = ZERO_MONEY
    created_orders: set[int] = set()
    for event_date in sorted(events):
        day_events = sorted(events[event_date], key=lambda item: (item[0], item[3]))
        for priority, order_id, source_type, source_id, amount in day_events:
            if priority == 0:
                document = documents[order_id]
                document["balance"] = money(document["balance"] + amount)
                created_orders.add(order_id)
                continue
            if priority == 2:
                fifo_credit = money(fifo_credit + amount)
                continue
            if order_id not in created_orders:
                logger.warning(
                    "Belge tarihinden önceki bağlı alacak hareketi atlandı",
                    extra={
                        "company_id": company_id,
                        "order_id": order_id,
                        "source_type": source_type,
                        "source_id": source_id,
                    },
                )
                continue
            document = documents[order_id]
            document["balance"] = money(document["balance"] + amount)
            document["changes"].append(
                PrincipalChange(event_date, amount, source_type, source_id)
            )

        for order_id in sorted(
            created_orders,
            key=lambda item: (documents[item]["due_date"], item),
        ):
            if fifo_credit <= ZERO_MONEY:
                break
            document = documents[order_id]
            available = max(ZERO_MONEY, money(document["balance"]))
            applied = min(available, fifo_credit)
            if applied <= ZERO_MONEY:
                continue
            document["balance"] = money(document["balance"] - applied)
            fifo_credit = money(fifo_credit - applied)
            document["changes"].append(
                PrincipalChange(
                    event_date,
                    -applied,
                    "customer_fifo",
                    0,
                )
            )

    timelines: list[PrincipalTimeline] = []
    for order_id, document in documents.items():
        row = document["row"]
        timelines.append(
            PrincipalTimeline(
                order_id=order_id,
                customer_id=customer_id,
                customer_name=str(row["customer_name"]),
                document_no=row["document_no"],
                order_date=document["order_date"],
                due_date=document["due_date"],
                original_principal=document["total"],
                changes=tuple(
                    sorted(
                        document["changes"],
                        key=lambda item: (
                            item.effective_date,
                            item.source_type,
                            item.source_id,
                        ),
                    )
                ),
            )
        )
    return sorted(timelines, key=lambda item: (item.due_date, item.order_id))
