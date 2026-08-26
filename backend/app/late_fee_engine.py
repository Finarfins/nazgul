from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .money import HUNDRED, MONEY_QUANTUM, ZERO, ZERO_MONEY, decimal_value, money
from .receivables_engine import PrincipalChange, PrincipalTimeline, build_principal_timelines

SUPPORTED_LATE_FEE_TAX_MODE = "NO_VAT"


@dataclass(frozen=True)
class LateFeePolicy:
    id: int
    source: str
    annual_rate: Decimal
    day_count_basis: int
    grace_days: int
    tax_mode: str
    vat_rate: Decimal
    effective_from: date
    effective_to: date | None


@dataclass(frozen=True)
class InterestSegment:
    start_date: date
    end_date: date
    days: int
    principal: Decimal
    unrounded_interest: Decimal


@dataclass(frozen=True)
class InterestCalculation:
    interest_start_date: date
    principal_at_interest_start: Decimal
    principal_as_of: Decimal
    amount: Decimal
    segments: tuple[InterestSegment, ...]


def _validated_decimal(value: object, field_name: str) -> Decimal:
    result = decimal_value(value, field_name=field_name)
    if result < ZERO:
        raise ValueError(f"{field_name} negatif olamaz")
    return result


def calculate_late_fee(
    *,
    original_principal: object,
    due_date: date,
    as_of: date,
    annual_rate: object,
    day_count_basis: int = 365,
    grace_days: int = 0,
    changes: tuple[PrincipalChange, ...] = (),
) -> InterestCalculation:
    principal = _validated_decimal(original_principal, "Anapara")
    rate = _validated_decimal(annual_rate, "Yıllık oran")
    if day_count_basis != 365:
        raise ValueError("Gün bazının 365 olması gerekir")
    if grace_days < 0:
        raise ValueError("Ek süre negatif olamaz")

    interest_start = due_date + timedelta(days=grace_days + 1)
    ordered_changes = sorted(
        changes,
        key=lambda item: (item.effective_date, item.source_type, item.source_id),
    )
    balance = principal
    for change in ordered_changes:
        if change.effective_date < interest_start:
            balance += decimal_value(change.amount, field_name="Anapara hareketi")
    opening_balance = max(ZERO, balance)
    if as_of < interest_start:
        for change in ordered_changes:
            if interest_start <= change.effective_date <= as_of:
                balance += decimal_value(change.amount, field_name="Anapara hareketi")
        return InterestCalculation(
            interest_start_date=interest_start,
            principal_at_interest_start=money(opening_balance),
            principal_as_of=money(max(ZERO, balance)),
            amount=ZERO_MONEY,
            segments=(),
        )

    changes_by_date: dict[date, Decimal] = {}
    for change in ordered_changes:
        if interest_start <= change.effective_date <= as_of:
            changes_by_date[change.effective_date] = (
                changes_by_date.get(change.effective_date, ZERO)
                + decimal_value(change.amount, field_name="Anapara hareketi")
            )

    segments: list[InterestSegment] = []
    current = interest_start
    total = ZERO
    with localcontext() as context:
        context.prec = 38
        daily_rate = rate / (HUNDRED * Decimal(day_count_basis))
        for event_date in sorted(changes_by_date):
            if event_date > current:
                days = (event_date - current).days
                effective_principal = max(balance, ZERO)
                contribution = effective_principal * daily_rate * Decimal(days)
                segments.append(
                    InterestSegment(
                        current,
                        event_date - timedelta(days=1),
                        days,
                        effective_principal,
                        contribution,
                    )
                )
                total += contribution
            balance += changes_by_date[event_date]
            current = event_date
        if current <= as_of:
            days = (as_of - current).days + 1
            effective_principal = max(balance, ZERO)
            contribution = effective_principal * daily_rate * Decimal(days)
            segments.append(
                InterestSegment(
                    current,
                    as_of,
                    days,
                    effective_principal,
                    contribution,
                )
            )
            total += contribution

    return InterestCalculation(
        interest_start_date=interest_start,
        principal_at_interest_start=money(opening_balance),
        principal_as_of=money(max(balance, ZERO)),
        amount=total.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP),
        segments=tuple(segments),
    )


def resolve_late_fee_policy(
    db: Session,
    company_id: int,
    customer_id: int,
    as_of: date,
) -> LateFeePolicy:
    rows = db.execute(
        text(
            """SELECT id,customer_id,annual_rate,day_count_basis,grace_days,
            tax_mode,vat_rate,effective_from,effective_to
            FROM late_fee_policies
            WHERE company_id=:cid AND active=:active
              AND (customer_id=:customer_id OR customer_id IS NULL)
              AND effective_from<=:as_of
              AND (effective_to IS NULL OR effective_to>=:as_of)
            ORDER BY CASE WHEN customer_id=:customer_id THEN 0 ELSE 1 END,
                     effective_from DESC,id DESC"""
        ),
        {
            "cid": company_id,
            "customer_id": customer_id,
            "as_of": as_of,
            "active": True,
        },
    ).mappings().all()
    if not rows:
        raise HTTPException(404, "Geçerli vade farkı politikası bulunamadı")
    selected = rows[0]
    customer_specific = selected["customer_id"] is not None
    same_scope = [
        row
        for row in rows
        if (row["customer_id"] is not None) == customer_specific
        and row["effective_from"] == selected["effective_from"]
    ]
    if len(same_scope) > 1:
        raise HTTPException(409, "Aynı öncelikte çakışan vade farkı politikaları var")
    tax_mode = str(selected["tax_mode"])
    if tax_mode != SUPPORTED_LATE_FEE_TAX_MODE:
        raise HTTPException(
            422,
            f"Desteklenmeyen vade farkı vergi modu: {tax_mode}",
        )
    return LateFeePolicy(
        id=int(selected["id"]),
        source="customer" if customer_specific else "company",
        annual_rate=_validated_decimal(selected["annual_rate"], "Yıllık oran"),
        day_count_basis=int(selected["day_count_basis"]),
        grace_days=int(selected["grace_days"]),
        tax_mode=tax_mode,
        vat_rate=_validated_decimal(selected["vat_rate"], "KDV oranı"),
        effective_from=selected["effective_from"],
        effective_to=selected["effective_to"],
    )


def build_late_fee_preview(
    db: Session,
    company_id: int,
    *,
    as_of: date,
    order_id: int | None = None,
    customer_id: int | None = None,
) -> dict[str, object]:
    if (order_id is None) == (customer_id is None):
        raise HTTPException(422, "order_id veya customer_id alanlarından tam biri zorunludur")
    if order_id is not None:
        target = db.execute(
            text(
                """SELECT o.customer_id
                FROM orders o
                WHERE o.id=:order_id AND o.company_id=:cid
                  AND COALESCE(o.payment_term,'PESIN')='HARMAN_VADELI'"""
            ),
            {"order_id": order_id, "cid": company_id},
        ).mappings().first()
        if not target:
            raise HTTPException(404, "Harman vadeli satış bulunamadı")
        customer_id = int(target["customer_id"])
    else:
        customer = db.execute(
            text("SELECT id FROM customers WHERE id=:id AND company_id=:cid"),
            {"id": customer_id, "cid": company_id},
        ).first()
        if not customer:
            raise HTTPException(404, "Müşteri bulunamadı")

    assert customer_id is not None
    timelines = build_principal_timelines(db, company_id, customer_id, as_of)
    selected_timelines = [
        timeline
        for timeline in timelines
        if (order_id is None or timeline.order_id == order_id)
    ]
    harman_order_ids = {
        int(row["id"])
        for row in db.execute(
            text(
                """SELECT id FROM orders
                WHERE company_id=:cid AND customer_id=:customer_id
                  AND COALESCE(payment_term,'PESIN')='HARMAN_VADELI'"""
            ),
            {"cid": company_id, "customer_id": customer_id},
        ).mappings()
    }
    selected_timelines = [
        timeline
        for timeline in selected_timelines
        if timeline.order_id in harman_order_ids
    ]
    if order_id is not None and not selected_timelines:
        raise HTTPException(404, "Harman vadeli satış alacak hesabına uygun değil")

    customer_name = (
        selected_timelines[0].customer_name
        if selected_timelines
        else str(
            db.execute(
                text("SELECT name FROM customers WHERE id=:id AND company_id=:cid"),
                {"id": customer_id, "cid": company_id},
            ).scalar_one()
        )
    )
    policy = resolve_late_fee_policy(db, company_id, customer_id, as_of)
    policy_snapshot = {
        "id": policy.id,
        "source": policy.source,
        "annual_rate": policy.annual_rate,
        "day_count_basis": policy.day_count_basis,
        "grace_days": policy.grace_days,
        "tax_mode": policy.tax_mode,
        "vat_rate": policy.vat_rate,
        "effective_from": policy.effective_from,
        "effective_to": policy.effective_to,
    }
    order_previews: list[dict[str, object]] = []
    total = ZERO_MONEY
    for timeline in selected_timelines:
        calculation = calculate_late_fee(
            original_principal=timeline.original_principal,
            due_date=timeline.due_date,
            as_of=as_of,
            annual_rate=policy.annual_rate,
            day_count_basis=policy.day_count_basis,
            grace_days=policy.grace_days,
            changes=timeline.changes,
        )
        total = money(total + calculation.amount)
        order_previews.append(
            {
                "order_id": timeline.order_id,
                "document_no": timeline.document_no,
                "due_date": timeline.due_date,
                "interest_start_date": calculation.interest_start_date,
                "as_of": as_of,
                "principal_at_interest_start": calculation.principal_at_interest_start,
                "principal_as_of": calculation.principal_as_of,
                "late_fee_net": calculation.amount,
                "late_fee_vat": ZERO_MONEY,
                "late_fee_gross": calculation.amount,
                "policy": policy_snapshot,
                "segments": [
                    {
                        "start_date": segment.start_date,
                        "end_date": segment.end_date,
                        "days": segment.days,
                        "principal": money(segment.principal),
                        "unrounded_interest": segment.unrounded_interest,
                    }
                    for segment in calculation.segments
                ],
            }
        )
    return {
        "company_id": company_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "as_of": as_of,
        "total_late_fee_net": total,
        "total_late_fee_vat": ZERO_MONEY,
        "total_late_fee_gross": total,
        "orders": order_previews,
    }
