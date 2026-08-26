from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from .document_engine import SALES_IMPORT_NOTE
from .money import ZERO_MONEY, money
from .receivables_engine import parse_receivable_date


@dataclass(frozen=True)
class ReconciliationRow:
    company_id: int
    customer_id: int
    evidence_source: str
    confidence: str
    status: str
    reason: str
    order_id: int | None = None
    receivable_charge_id: int | None = None
    document_no: str | None = None
    stored_paid_amount: Decimal = ZERO_MONEY
    linked_payment_total: Decimal = ZERO_MONEY
    unlinked_customer_payment_total: Decimal = ZERO_MONEY
    linked_return_total: Decimal = ZERO_MONEY
    unlinked_customer_return_total: Decimal = ZERO_MONEY
    active_allocation_total: Decimal = ZERO_MONEY
    frozen_residual_amount: Decimal = ZERO_MONEY
    document_allocation_excess: Decimal = ZERO_MONEY
    payment_allocation_excess: Decimal = ZERO_MONEY
    charge_allocation_excess: Decimal = ZERO_MONEY
    candidate_residual: Decimal = ZERO_MONEY

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _totals_by_entity(
    db: Session,
    sql: str,
    company_id: int,
) -> dict[int, Decimal]:
    rows = db.execute(text(sql), {"cid": company_id}).mappings()
    return {int(row["entity_id"]): money(row["total"]) for row in rows}


def build_reconciliation_report(
    db: Session,
    company_id: int,
) -> list[ReconciliationRow]:
    orders = db.execute(
        text(
            """SELECT id,customer_id,document_no,note,status,due_date,final_total,
            COALESCE(paid_amount,0) paid_amount
            FROM orders WHERE company_id=:cid"""
        ),
        {"cid": company_id},
    ).mappings().all()

    def order_key(row: dict[str, object]) -> tuple[int, date, int]:
        try:
            due_date = parse_receivable_date(row["due_date"])
        except ValueError:
            due_date = date.max
        return int(row["customer_id"]), due_date, int(row["id"])

    orders = sorted(orders, key=order_key)
    linked_payments = {
        int(row["order_id"]): money(row["total"])
        for row in db.execute(
            text(
                """SELECT reference_id order_id,COALESCE(SUM(amount),0) total
                FROM payments
                WHERE company_id=:cid AND entity_type='customer'
                  AND reference_type='order' AND reference_id IS NOT NULL
                GROUP BY reference_id"""
            ),
            {"cid": company_id},
        ).mappings()
    }
    unlinked_payments = _totals_by_entity(
        db,
        """SELECT entity_id,COALESCE(SUM(amount),0) total FROM payments
        WHERE company_id=:cid AND entity_type='customer'
          AND (reference_type IS NULL OR reference_type<>'order' OR reference_id IS NULL)
        GROUP BY entity_id""",
        company_id,
    )
    linked_returns = {
        int(row["order_id"]): money(row["total"])
        for row in db.execute(
            text(
                """SELECT source_id order_id,COALESCE(SUM(total),0) total FROM returns
                WHERE company_id=:cid AND return_type='sale_return'
                  AND source_type='order' AND source_id IS NOT NULL
                  AND COALESCE(status,'completed') NOT IN ('draft','cancelled')
                GROUP BY source_id"""
            ),
            {"cid": company_id},
        ).mappings()
    }
    unlinked_returns = _totals_by_entity(
        db,
        """SELECT entity_id,COALESCE(SUM(total),0) total FROM returns
        WHERE company_id=:cid AND return_type='sale_return'
          AND (source_type IS NULL OR source_type<>'order' OR source_id IS NULL)
          AND COALESCE(status,'completed') NOT IN ('draft','cancelled')
        GROUP BY entity_id""",
        company_id,
    )
    allocation_rows = db.execute(
        text(
            """SELECT a.order_id,a.payment_id,COALESCE(SUM(
                CASE WHEN a.reversal_of_allocation_id IS NULL
                     THEN a.amount ELSE -a.amount END
            ),0) total
            FROM payment_allocations a
            WHERE a.company_id=:cid AND a.order_id IS NOT NULL
            GROUP BY a.order_id,a.payment_id"""
        ),
        {"cid": company_id},
    ).mappings().all()
    allocations_by_order: dict[int, Decimal] = {}
    orders_by_payment: dict[int, set[int]] = {}
    allocations_by_payment: dict[int, Decimal] = {}
    for allocation in allocation_rows:
        order_id = int(allocation["order_id"])
        payment_id = int(allocation["payment_id"])
        amount = money(allocation["total"])
        allocations_by_order[order_id] = money(
            allocations_by_order.get(order_id, ZERO_MONEY) + amount
        )
        allocations_by_payment[payment_id] = money(
            allocations_by_payment.get(payment_id, ZERO_MONEY) + amount
        )
        orders_by_payment.setdefault(payment_id, set()).add(order_id)
    payment_amounts = {
        int(row["id"]): money(row["amount"])
        for row in db.execute(
            text("SELECT id,amount FROM payments WHERE company_id=:cid"),
            {"cid": company_id},
        ).mappings()
    }
    # Payment excess is measured across every target, so a payment split between
    # an order and a charge document cannot hide behind the per-target totals.
    for row in db.execute(
        text(
            """SELECT payment_id,COALESCE(SUM(
                CASE WHEN reversal_of_allocation_id IS NULL
                     THEN amount ELSE -amount END
            ),0) total
            FROM payment_allocations
            WHERE company_id=:cid
            GROUP BY payment_id"""
        ),
        {"cid": company_id},
    ).mappings():
        allocations_by_payment[int(row["payment_id"])] = money(row["total"])
    payment_excess_by_order: dict[int, Decimal] = {}
    for payment_id, allocated in allocations_by_payment.items():
        excess = money(allocated - payment_amounts.get(payment_id, ZERO_MONEY))
        if excess <= ZERO_MONEY:
            continue
        # A payment allocated only to charge documents has no order row to carry
        # the excess; _charge_allocation_rows reports those separately.
        for order_id in orders_by_payment.get(payment_id, ()):
            payment_excess_by_order[order_id] = money(
                payment_excess_by_order.get(order_id, ZERO_MONEY) + excess
            )
    frozen_residuals = {
        int(row["order_id"]): money(row["amount"])
        for row in db.execute(
            text(
                """SELECT order_id,amount FROM receivable_legacy_residuals
                WHERE company_id=:cid AND status IN ('confirmed','resolved')"""
            ),
            {"cid": company_id},
        ).mappings()
    }

    report: list[ReconciliationRow] = []
    return_credit_by_customer = dict(unlinked_returns)
    for row in orders:
        order_id = int(row["id"])
        customer_id = int(row["customer_id"])
        stored = money(row["paid_amount"])
        linked = linked_payments.get(order_id, ZERO_MONEY)
        candidate = money(stored - linked)
        customer_unlinked = unlinked_payments.get(customer_id, ZERO_MONEY)
        customer_unlinked_returns = unlinked_returns.get(customer_id, ZERO_MONEY)
        active_allocation = allocations_by_order.get(order_id, ZERO_MONEY)
        linked_return = linked_returns.get(order_id, ZERO_MONEY)
        frozen_residual = frozen_residuals.get(order_id, ZERO_MONEY)
        return_credit = return_credit_by_customer.get(customer_id, ZERO_MONEY)
        return_capacity = max(
            ZERO_MONEY,
            money(money(row["final_total"]) - linked_return - frozen_residual),
        )
        applied_unlinked_return = min(return_capacity, return_credit)
        return_credit_by_customer[customer_id] = money(
            return_credit - applied_unlinked_return
        )
        document_excess = money(
            active_allocation
            + linked_return
            + frozen_residual
            + applied_unlinked_return
            - money(row["final_total"])
        )
        payment_excess = payment_excess_by_order.get(order_id, ZERO_MONEY)
        is_bizimhesap_draft = (
            row["note"] == SALES_IMPORT_NOTE
            and row["status"] == "draft"
            and stored == ZERO_MONEY
            and linked == ZERO_MONEY
        )
        if payment_excess > ZERO_MONEY:
            evidence = "payment_allocation_excess"
            confidence = "invalid"
            status = "quarantined"
            reason = "Aktif tahsis toplamı ödeme tutarını aşıyor"
        elif document_excess > ZERO_MONEY:
            evidence = "order_allocation_excess"
            confidence = "invalid"
            status = "quarantined"
            reason = "Aktif tahsis satış belgesinin gerçek kalanını aşıyor"
        elif candidate < ZERO_MONEY:
            evidence = "negative_paid_vs_linked"
            confidence = "invalid"
            status = "quarantined"
            reason = "Bağlı ödeme toplamı stored paid_amount değerini aşıyor"
        elif is_bizimhesap_draft:
            evidence = "bizimhesap_draft_zero_paid"
            confidence = "high"
            status = "confirmed"
            reason = "#148 taslağı paid_amount=0 ve ödeme kaydı olmadan oluşturur"
        elif customer_unlinked > ZERO_MONEY or customer_unlinked_returns > ZERO_MONEY:
            evidence = "legacy_unlinked_overlap_unknown"
            confidence = "unknown"
            status = "quarantined"
            reason = "Bağlantısız hareketin paid_amount geçmişine girip girmediği kanıtlanamıyor"
        elif candidate == ZERO_MONEY:
            evidence = "linked_payment_match"
            confidence = "high"
            status = "confirmed"
            reason = "Stored paid_amount bağlı ödeme toplamıyla eşleşiyor"
        else:
            evidence = "unexplained_paid_amount"
            confidence = "unknown"
            status = "quarantined"
            reason = "Ödeme hareketiyle açıklanamayan paid_amount inceleme gerektiriyor"
        report.append(
            ReconciliationRow(
                company_id=company_id,
                order_id=order_id,
                customer_id=customer_id,
                document_no=row["document_no"],
                stored_paid_amount=stored,
                linked_payment_total=linked,
                unlinked_customer_payment_total=customer_unlinked,
                linked_return_total=linked_returns.get(order_id, ZERO_MONEY),
                unlinked_customer_return_total=customer_unlinked_returns,
                active_allocation_total=active_allocation,
                frozen_residual_amount=frozen_residual,
                document_allocation_excess=document_excess,
                payment_allocation_excess=payment_excess,
                candidate_residual=candidate,
                evidence_source=evidence,
                confidence=confidence,
                status=status,
                reason=reason,
            )
        )
    charge_only_payment_excess = {
        payment_id: money(allocated - payment_amounts.get(payment_id, ZERO_MONEY))
        for payment_id, allocated in allocations_by_payment.items()
        if payment_id not in orders_by_payment
        and money(allocated - payment_amounts.get(payment_id, ZERO_MONEY)) > ZERO_MONEY
    }
    report.extend(
        _charge_allocation_rows(db, company_id, charge_only_payment_excess)
    )
    return report


def _charge_allocation_rows(
    db: Session,
    company_id: int,
    charge_only_payment_excess: dict[int, Decimal],
) -> list[ReconciliationRow]:
    """Allocation-level quarantine rows that no order row can express.

    Three of the five reasons live here: an orphan charge foreign key, a target
    XOR violation, and a charge document whose net active allocation exceeds its
    gross amount.
    """

    rows: list[ReconciliationRow] = []
    for payment_id, excess in sorted(charge_only_payment_excess.items()):
        payment_row = db.execute(
            text(
                "SELECT entity_id FROM payments WHERE id=:id AND company_id=:cid"
            ),
            {"id": payment_id, "cid": company_id},
        ).first()
        rows.append(
            ReconciliationRow(
                company_id=company_id,
                customer_id=int(payment_row[0]) if payment_row else 0,
                payment_allocation_excess=excess,
                evidence_source="payment_allocation_excess",
                confidence="invalid",
                status="quarantined",
                reason=(
                    "Aktif tahsis toplamı ödeme tutarını aşıyor "
                    f"(payment #{payment_id})"
                ),
            )
        )
    orphans = db.execute(
        text(
            """SELECT a.id,a.receivable_charge_id,p.entity_id customer_id
            FROM payment_allocations a
            LEFT JOIN payments p
              ON p.id=a.payment_id AND p.company_id=a.company_id
            WHERE a.company_id=:cid AND a.receivable_charge_id IS NOT NULL
              AND NOT EXISTS(
                  SELECT 1 FROM receivable_charge_documents d
                  WHERE d.company_id=a.company_id AND d.id=a.receivable_charge_id
              )
            ORDER BY a.id"""
        ),
        {"cid": company_id},
    ).mappings()
    for row in orphans:
        rows.append(
            ReconciliationRow(
                company_id=company_id,
                customer_id=int(row["customer_id"] or 0),
                receivable_charge_id=int(row["receivable_charge_id"]),
                evidence_source="charge_allocation_orphan_fk",
                confidence="invalid",
                status="quarantined",
                reason=(
                    "Tahsis kaydı var olmayan tahakkuk belgesine bağlı "
                    f"(allocation #{int(row['id'])})"
                ),
            )
        )
    xor_rows = db.execute(
        text(
            """SELECT a.id,a.order_id,a.receivable_charge_id,p.entity_id customer_id
            FROM payment_allocations a
            LEFT JOIN payments p
              ON p.id=a.payment_id AND p.company_id=a.company_id
            WHERE a.company_id=:cid
              AND ((a.order_id IS NULL AND a.receivable_charge_id IS NULL)
                   OR (a.order_id IS NOT NULL AND a.receivable_charge_id IS NOT NULL))
            ORDER BY a.id"""
        ),
        {"cid": company_id},
    ).mappings()
    for row in xor_rows:
        rows.append(
            ReconciliationRow(
                company_id=company_id,
                customer_id=int(row["customer_id"] or 0),
                order_id=(
                    int(row["order_id"]) if row["order_id"] is not None else None
                ),
                receivable_charge_id=(
                    int(row["receivable_charge_id"])
                    if row["receivable_charge_id"] is not None
                    else None
                ),
                evidence_source="allocation_target_xor_violation",
                confidence="invalid",
                status="quarantined",
                reason=(
                    "Tahsis kaydı tam olarak bir hedefe bağlı değil "
                    f"(allocation #{int(row['id'])})"
                ),
            )
        )
    charge_totals = db.execute(
        text(
            """SELECT d.id,d.customer_id,d.gross_amount,COALESCE(SUM(
                CASE WHEN a.reversal_of_allocation_id IS NULL
                     THEN a.amount ELSE -a.amount END
            ),0) allocated
            FROM receivable_charge_documents d
            JOIN payment_allocations a
              ON a.company_id=d.company_id AND a.receivable_charge_id=d.id
            WHERE d.company_id=:cid
            GROUP BY d.id,d.customer_id,d.gross_amount"""
        ),
        {"cid": company_id},
    ).mappings()
    for row in charge_totals:
        allocated = money(row["allocated"])
        excess = money(allocated - money(row["gross_amount"]))
        if excess <= ZERO_MONEY:
            continue
        rows.append(
            ReconciliationRow(
                company_id=company_id,
                customer_id=int(row["customer_id"]),
                receivable_charge_id=int(row["id"]),
                active_allocation_total=allocated,
                charge_allocation_excess=excess,
                evidence_source="charge_allocation_excess",
                confidence="invalid",
                status="quarantined",
                reason="Aktif tahsis tahakkuk belgesinin gerçek kalanını aşıyor",
            )
        )
    return rows
