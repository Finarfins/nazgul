from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from typing import Callable

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .late_fee_engine import (
    SUPPORTED_LATE_FEE_TAX_MODE,
    calculate_late_fee,
    resolve_late_fee_policy,
)
from .money import ZERO_MONEY, decimal_value, money
from .payment_allocation_engine import assert_charge_document_unallocated
from .receivables_engine import (
    PrincipalTimeline,
    build_principal_timelines,
    parse_receivable_date,
)


def _lock_suffix(db: Session) -> str:
    return " FOR UPDATE" if db.get_bind().dialect.name == "postgresql" else ""


def _decimal_text(value: object, places: int = 2) -> str:
    return format(decimal_value(value), f".{places}f")


def _request_fingerprint(values: dict[str, object]) -> str:
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _claim_request(
    db: Session,
    company_id: int,
    operation: str,
    resource_id: str,
    idempotency_key: str,
    fingerprint: str,
) -> tuple[int, int | None]:
    key = idempotency_key.strip()
    if not key:
        raise HTTPException(400, "Idempotency-Key zorunludur")
    if len(key) > 255:
        raise HTTPException(400, "Idempotency-Key en fazla 255 karakter olabilir")
    params = {
        "cid": company_id,
        "operation": operation,
        "resource_id": resource_id,
        "key": key,
        "fingerprint": fingerprint,
    }
    claim_id: int | None = None
    try:
        with db.begin_nested():
            claim_id = int(
                db.execute(
                    text(
                        """INSERT INTO receivable_charge_idempotency(
                            company_id,operation_type,resource_id,idempotency_key,
                            request_fingerprint
                        ) VALUES(
                            :cid,:operation,:resource_id,:key,:fingerprint
                        ) RETURNING id"""
                    ),
                    params,
                ).scalar_one()
            )
    except IntegrityError:
        pass
    if claim_id is not None:
        return claim_id, None
    row = db.execute(
        text(
            """SELECT id,request_fingerprint,result_document_id
            FROM receivable_charge_idempotency
            WHERE company_id=:cid AND operation_type=:operation
              AND resource_id=:resource_id AND idempotency_key=:key"""
            + _lock_suffix(db)
        ),
        params,
    ).mappings().first()
    if not row:
        raise HTTPException(409, "İstek idempotency kaydı eşzamanlı olarak değişti")
    if row["request_fingerprint"] != fingerprint:
        raise HTTPException(409, "Idempotency-Key farklı bir istek için kullanılmış")
    if row["result_document_id"] is None:
        raise HTTPException(409, "Tahakkuk işlemi halen devam ediyor")
    return int(row["id"]), int(row["result_document_id"])


def _request_replay(
    db: Session,
    company_id: int,
    operation: str,
    resource_id: str,
    idempotency_key: str,
    fingerprint: str,
) -> int | None:
    key = idempotency_key.strip()
    if not key:
        raise HTTPException(400, "Idempotency-Key zorunludur")
    if len(key) > 255:
        raise HTTPException(400, "Idempotency-Key en fazla 255 karakter olabilir")
    row = db.execute(
        text(
            """SELECT request_fingerprint,result_document_id
            FROM receivable_charge_idempotency
            WHERE company_id=:cid AND operation_type=:operation
              AND resource_id=:resource_id AND idempotency_key=:key"""
        ),
        {
            "cid": company_id,
            "operation": operation,
            "resource_id": resource_id,
            "key": key,
        },
    ).mappings().first()
    if row is None:
        return None
    if row["request_fingerprint"] != fingerprint:
        raise HTTPException(409, "Idempotency-Key farklı bir istek için kullanılmış")
    if row["result_document_id"] is None:
        raise HTTPException(409, "Tahakkuk işlemi halen devam ediyor")
    return int(row["result_document_id"])


def _complete_request(
    db: Session,
    company_id: int,
    claim_id: int,
    document_id: int,
) -> None:
    db.execute(
        text(
            """UPDATE receivable_charge_idempotency
            SET result_document_id=:document_id
            WHERE id=:id AND company_id=:cid"""
        ),
        {"document_id": document_id, "id": claim_id, "cid": company_id},
    )


def _timeline_for_order(
    db: Session,
    company_id: int,
    order_id: int,
    as_of: date,
) -> PrincipalTimeline:
    target = db.execute(
        text(
            """SELECT customer_id FROM orders
            WHERE id=:order_id AND company_id=:cid
              AND COALESCE(payment_term,'PESIN')='HARMAN_VADELI'
              AND COALESCE(status,'completed') NOT IN ('draft','cancelled')"""
            + _lock_suffix(db)
        ),
        {"order_id": order_id, "cid": company_id},
    ).mappings().first()
    if not target:
        raise HTTPException(404, "Harman vadeli satış bulunamadı")
    timelines = build_principal_timelines(
        db,
        company_id,
        int(target["customer_id"]),
        as_of,
    )
    timeline = next((row for row in timelines if row.order_id == order_id), None)
    if not timeline:
        raise HTTPException(409, "Satışın anapara zaman çizelgesi üretilemedi")
    return timeline


def _calculation_snapshot(
    timeline: PrincipalTimeline,
    period_start: date,
    period_end: date,
    *,
    annual_rate: object,
    day_count_basis: int,
    grace_days: int,
    tax_mode: str,
    vat_rate: object,
) -> tuple[dict[str, object], str]:
    if tax_mode != SUPPORTED_LATE_FEE_TAX_MODE:
        raise HTTPException(
            422,
            f"Desteklenmeyen vade farkı vergi modu: {tax_mode}",
        )
    if period_end < period_start:
        raise HTTPException(422, "Dönem bitişi başlangıçtan önce olamaz")
    if period_start <= timeline.due_date:
        raise HTTPException(422, "Tahakkuk dönemi vade tarihinden sonra başlamalıdır")
    calculation = calculate_late_fee(
        original_principal=timeline.original_principal,
        due_date=period_start - timedelta(days=1),
        as_of=period_end,
        annual_rate=annual_rate,
        day_count_basis=day_count_basis,
        grace_days=0,
        changes=timeline.changes,
    )
    snapshot: dict[str, object] = {
        "order_id": timeline.order_id,
        "customer_id": timeline.customer_id,
        "due_date": timeline.due_date.isoformat(),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "principal_basis": _decimal_text(calculation.principal_at_interest_start),
        "principal_as_of": _decimal_text(calculation.principal_as_of),
        "annual_rate": _decimal_text(annual_rate, 4),
        "day_count_basis": day_count_basis,
        "grace_days": grace_days,
        "tax_mode": tax_mode,
        "vat_rate": _decimal_text(vat_rate, 4),
        "net_amount": _decimal_text(calculation.amount),
        "vat_amount": _decimal_text(ZERO_MONEY),
        "gross_amount": _decimal_text(calculation.amount),
        "segments": [
            {
                "start_date": row.start_date.isoformat(),
                "end_date": row.end_date.isoformat(),
                "days": row.days,
                "principal": _decimal_text(row.principal),
                "unrounded_interest": format(row.unrounded_interest, "f"),
            }
            for row in calculation.segments
        ],
    }
    serialized = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return snapshot, hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _document(db: Session, company_id: int, document_id: int) -> dict[str, object]:
    row = db.execute(
        text(
            """SELECT id,company_id,charge_period_id,order_id,customer_id,
            charge_type,period_start,period_end,due_date_snapshot,principal_basis,
            annual_rate_snapshot,day_count_basis_snapshot,grace_days_snapshot,
            tax_mode_snapshot,vat_rate_snapshot,net_amount,vat_amount,gross_amount,
            status,calculation_fingerprint,revision_no,reversal_of_document_id,
            created_at,posted_at,reversed_at
            FROM receivable_charge_documents
            WHERE id=:id AND company_id=:cid"""
        ),
        {"id": document_id, "cid": company_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Tahakkuk belgesi bulunamadı")
    result = dict(row)
    for field in ("principal_basis", "net_amount", "vat_amount", "gross_amount"):
        result[field] = money(result[field])
    result["annual_rate_snapshot"] = decimal_value(result["annual_rate_snapshot"])
    result["vat_rate_snapshot"] = decimal_value(result["vat_rate_snapshot"])
    return result


def get_late_fee_document(
    db: Session, company_id: int, document_id: int
) -> dict[str, object]:
    return _document(db, company_id, document_id)


def create_late_fee_draft(
    db: Session,
    company_id: int,
    order_id: int,
    period_start: date,
    period_end: date,
    idempotency_key: str,
    *,
    created_by: int,
    installment_id: int | None = None,
    on_created: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """``on_created`` verilirse commit'ten hemen ÖNCE, aynı transaction içinde
    çağrılır (aktivite kaydı). Idempotent tekrar oynatmada çağrılmaz."""
    request_values = {
        "order_id": order_id,
        "installment_id": installment_id,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
    }
    request_fingerprint = _request_fingerprint(request_values)
    existing_document_id = _request_replay(
        db,
        company_id,
        "create_late_fee_draft",
        str(order_id),
        idempotency_key,
        request_fingerprint,
    )
    if existing_document_id is not None:
        db.commit()
        return _document(db, company_id, existing_document_id)

    timeline = _timeline_for_order(db, company_id, order_id, period_end)
    policy = resolve_late_fee_policy(
        db, company_id, timeline.customer_id, period_end
    )
    claim_id, replay_id = _claim_request(
        db,
        company_id,
        "create_late_fee_draft",
        str(order_id),
        idempotency_key,
        request_fingerprint,
    )
    if replay_id is not None:
        db.commit()
        return _document(db, company_id, replay_id)

    installment_predicate = (
        "installment_id IS NULL"
        if installment_id is None
        else "installment_id=:installment_id"
    )
    period_params = {
        "cid": company_id,
        "order_id": order_id,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
    }
    if installment_id is not None:
        period_params["installment_id"] = installment_id
    period = db.execute(
        text(
            f"""SELECT id FROM receivable_charge_periods
            WHERE company_id=:cid AND order_id=:order_id
              AND {installment_predicate}
              AND period_start=:period_start AND period_end=:period_end
              AND charge_kind='late_fee'"""
            + _lock_suffix(db)
        ),
        period_params,
    ).mappings().first()
    if period:
        period_id = int(period["id"])
        latest = db.execute(
            text(
                """SELECT id,status,reversal_of_document_id
                FROM receivable_charge_documents
                WHERE company_id=:cid AND charge_period_id=:period_id
                ORDER BY revision_no DESC LIMIT 1"""
                + _lock_suffix(db)
            ),
            {"cid": company_id, "period_id": period_id},
        ).mappings().first()
        if (
            latest
            and latest["reversal_of_document_id"] is None
            and latest["status"] != "reversed"
        ):
            document_id = int(latest["id"])
            _complete_request(db, company_id, claim_id, document_id)
            db.commit()
            return _document(db, company_id, document_id)
        revision_no = int(
            db.execute(
                text(
                    """SELECT COALESCE(MAX(revision_no),0)+1
                    FROM receivable_charge_documents
                    WHERE company_id=:cid AND charge_period_id=:period_id"""
                ),
                {"cid": company_id, "period_id": period_id},
            ).scalar_one()
        )
    else:
        period_id = int(
            db.execute(
                text(
                    """INSERT INTO receivable_charge_periods(
                        company_id,order_id,installment_id,period_start,period_end,
                        charge_kind
                    ) VALUES(
                        :cid,:order_id,:installment_id,:period_start,:period_end,
                        'late_fee'
                    ) RETURNING id"""
                ),
                {"cid": company_id, **request_values},
            ).scalar_one()
        )
        revision_no = 1
    snapshot, fingerprint = _calculation_snapshot(
        timeline,
        period_start,
        period_end,
        annual_rate=policy.annual_rate,
        day_count_basis=policy.day_count_basis,
        grace_days=policy.grace_days,
        tax_mode=policy.tax_mode,
        vat_rate=policy.vat_rate,
    )
    document_id = int(
        db.execute(
            text(
                """INSERT INTO receivable_charge_documents(
                    company_id,charge_period_id,order_id,customer_id,charge_type,
                    period_start,period_end,due_date_snapshot,principal_basis,
                    annual_rate_snapshot,day_count_basis_snapshot,
                    grace_days_snapshot,tax_mode_snapshot,vat_rate_snapshot,
                    calculation_snapshot,net_amount,vat_amount,gross_amount,status,
                    calculation_fingerprint,revision_no,created_by
                ) VALUES(
                    :cid,:period_id,:order_id,:customer_id,'late_fee',
                    :period_start,:period_end,:due_date,:principal_basis,
                    :annual_rate,:day_count_basis,:grace_days,:tax_mode,:vat_rate,
                    :snapshot,:net_amount,:vat_amount,:gross_amount,'draft',
                    :fingerprint,:revision_no,:created_by
                ) RETURNING id"""
            ),
            {
                "cid": company_id,
                "period_id": period_id,
                "order_id": order_id,
                "customer_id": timeline.customer_id,
                "period_start": period_start,
                "period_end": period_end,
                "due_date": timeline.due_date,
                "principal_basis": snapshot["principal_basis"],
                "annual_rate": snapshot["annual_rate"],
                "day_count_basis": policy.day_count_basis,
                "grace_days": policy.grace_days,
                "tax_mode": policy.tax_mode,
                "vat_rate": snapshot["vat_rate"],
                "snapshot": json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
                "net_amount": snapshot["net_amount"],
                "vat_amount": snapshot["vat_amount"],
                "gross_amount": snapshot["gross_amount"],
                "fingerprint": fingerprint,
                "revision_no": revision_no,
                "created_by": created_by,
            },
        ).scalar_one()
    )
    _complete_request(db, company_id, claim_id, document_id)
    created = _document(db, company_id, document_id)
    if on_created is not None:
        on_created(created)
    db.commit()
    return created


def post_late_fee_document(
    db: Session,
    company_id: int,
    document_id: int,
    idempotency_key: str,
    *,
    actor_id: int,
    on_posted: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """``on_posted`` commit'ten hemen ÖNCE, aynı transaction içinde çağrılır."""
    request_fingerprint = _request_fingerprint({"document_id": document_id})
    replay_id = _request_replay(
        db,
        company_id,
        "post_late_fee_document",
        str(document_id),
        idempotency_key,
        request_fingerprint,
    )
    if replay_id is not None:
        db.commit()
        return _document(db, company_id, replay_id)
    row = db.execute(
        text(
            """SELECT * FROM receivable_charge_documents
            WHERE id=:id AND company_id=:cid"""
            + _lock_suffix(db)
        ),
        {"id": document_id, "cid": company_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Tahakkuk belgesi bulunamadı")
    if row["status"] == "posted":
        raise HTTPException(
            409,
            "Belge zaten onaylı; replay değilse yeni taslak oluşturun",
        )
    if row["status"] != "draft":
        raise HTTPException(409, "Yalnız taslak tahakkuk onaylanabilir")
    claim_id, replay_id = _claim_request(
        db,
        company_id,
        "post_late_fee_document",
        str(document_id),
        idempotency_key,
        request_fingerprint,
    )
    if replay_id is not None:
        db.commit()
        return _document(db, company_id, replay_id)
    timeline = _timeline_for_order(
        db,
        company_id,
        int(row["order_id"]),
        parse_receivable_date(row["period_end"]),
    )
    current_policy = resolve_late_fee_policy(
        db,
        company_id,
        timeline.customer_id,
        parse_receivable_date(row["period_end"]),
    )
    _, fingerprint = _calculation_snapshot(
        timeline,
        parse_receivable_date(row["period_start"]),
        parse_receivable_date(row["period_end"]),
        annual_rate=current_policy.annual_rate,
        day_count_basis=current_policy.day_count_basis,
        grace_days=current_policy.grace_days,
        tax_mode=current_policy.tax_mode,
        vat_rate=current_policy.vat_rate,
    )
    if fingerprint != row["calculation_fingerprint"]:
        raise HTTPException(
            409,
            "Anapara veya vade farkı politikası değişti; taslak yeniden üretilmeli",
        )
    db.execute(
        text(
            """UPDATE receivable_charge_documents
            SET status='posted',posted_by=:actor_id,posted_at=:posted_at
            WHERE id=:id AND company_id=:cid AND status='draft'"""
        ),
        {
            "actor_id": actor_id,
            "posted_at": datetime.now(timezone.utc),
            "id": document_id,
            "cid": company_id,
        },
    )
    _complete_request(db, company_id, claim_id, document_id)
    posted = _document(db, company_id, document_id)
    if on_posted is not None:
        on_posted(posted)
    db.commit()
    return posted


def reverse_late_fee_document(
    db: Session,
    company_id: int,
    document_id: int,
    idempotency_key: str,
    *,
    actor_id: int,
    on_reversed: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """``on_reversed`` commit'ten hemen ÖNCE, aynı transaction içinde çağrılır."""
    claim_id, replay_id = _claim_request(
        db,
        company_id,
        "reverse_late_fee_document",
        str(document_id),
        idempotency_key,
        _request_fingerprint({"document_id": document_id}),
    )
    if replay_id is not None:
        db.commit()
        return _document(db, company_id, replay_id)
    # Deterministic lock order shared with the allocation engine: customer row
    # BEFORE the charge-document row. The allocation path locks customer →
    # orders → charge docs; taking the document first here inverts that order
    # and deadlocks against a concurrent allocation on PostgreSQL. The
    # customer_id is read without a lock first, purely to know which customer
    # row to lock.
    customer_row = db.execute(
        text(
            """SELECT customer_id FROM receivable_charge_documents
            WHERE id=:id AND company_id=:cid"""
        ),
        {"id": document_id, "cid": company_id},
    ).first()
    if not customer_row:
        raise HTTPException(404, "Tahakkuk belgesi bulunamadı")
    db.execute(
        text(
            "SELECT id FROM customers WHERE id=:id AND company_id=:cid"
            + _lock_suffix(db)
        ),
        {"id": int(customer_row[0]), "cid": company_id},
    ).first()
    original = db.execute(
        text(
            """SELECT * FROM receivable_charge_documents
            WHERE id=:id AND company_id=:cid"""
            + _lock_suffix(db)
        ),
        {"id": document_id, "cid": company_id},
    ).mappings().first()
    if not original:
        raise HTTPException(404, "Tahakkuk belgesi bulunamadı")
    if original["status"] != "posted":
        raise HTTPException(409, "Yalnız onaylı tahakkuk terslenebilir")
    # Same row locks as the allocation path: the document row above, then its
    # allocation rows. Reversing a charge with live allocations is refused, and
    # the refusal cannot race an allocation that commits right after the check.
    assert_charge_document_unallocated(db, company_id, document_id)
    revision_no = int(
        db.execute(
            text(
                """SELECT COALESCE(MAX(revision_no),0)+1
                FROM receivable_charge_documents
                WHERE company_id=:cid AND charge_period_id=:period_id"""
            ),
            {"cid": company_id, "period_id": int(original["charge_period_id"])},
        ).scalar_one()
    )
    now = datetime.now(timezone.utc)
    reversal_id = int(
        db.execute(
            text(
                """INSERT INTO receivable_charge_documents(
                    company_id,charge_period_id,order_id,customer_id,charge_type,
                    period_start,period_end,due_date_snapshot,principal_basis,
                    annual_rate_snapshot,day_count_basis_snapshot,
                    grace_days_snapshot,tax_mode_snapshot,vat_rate_snapshot,
                    calculation_snapshot,net_amount,vat_amount,gross_amount,status,
                    calculation_fingerprint,revision_no,reversal_of_document_id,
                    created_by,posted_by,posted_at
                ) SELECT
                    company_id,charge_period_id,order_id,customer_id,charge_type,
                    period_start,period_end,due_date_snapshot,principal_basis,
                    annual_rate_snapshot,day_count_basis_snapshot,
                    grace_days_snapshot,tax_mode_snapshot,vat_rate_snapshot,
                    calculation_snapshot,-net_amount,-vat_amount,-gross_amount,'posted',
                    calculation_fingerprint,:revision_no,id,:actor_id,:actor_id,:now
                FROM receivable_charge_documents
                WHERE id=:id AND company_id=:cid
                RETURNING id"""
            ),
            {
                "revision_no": revision_no,
                "actor_id": actor_id,
                "now": now,
                "id": document_id,
                "cid": company_id,
            },
        ).scalar_one()
    )
    db.execute(
        text(
            """UPDATE receivable_charge_documents
            SET status='reversed',reversed_by=:actor_id,reversed_at=:now
            WHERE id=:id AND company_id=:cid AND status='posted'"""
        ),
        {"actor_id": actor_id, "now": now, "id": document_id, "cid": company_id},
    )
    _complete_request(db, company_id, claim_id, reversal_id)
    reversal = _document(db, company_id, reversal_id)
    if on_reversed is not None:
        on_reversed(reversal)
    db.commit()
    return reversal
