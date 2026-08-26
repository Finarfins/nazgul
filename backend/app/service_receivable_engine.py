from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .billing_service import build_invoice_summary
from .business_time import ISTANBUL
from .late_fee_charge_engine import (
    _claim_request,
    _complete_request,
    _request_fingerprint,
    _request_replay,
)
from .money import ZERO_MONEY, decimal_value, money
from .payment_allocation_engine import assert_charge_document_unallocated


SERVICE_FEE = "service_fee"


def _lock_suffix(db: Session) -> str:
    return " FOR UPDATE" if db.get_bind().dialect.name == "postgresql" else ""


def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _snapshot_fingerprint(snapshot: dict[str, object]) -> tuple[str, str]:
    serialized = _canonical_json(snapshot)
    return serialized, hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _aware_utc(value: object) -> datetime:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise HTTPException(409, "İş emri tamamlanma tarihi geçersiz")
    if parsed.tzinfo is None:
        # SQLite can lose timezone metadata. Stored work-order timestamps are UTC.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _service_document(db: Session, company_id: int, document_id: int) -> dict[str, object]:
    row = db.execute(
        text(
            """SELECT * FROM receivable_charge_documents
            WHERE company_id=:cid AND id=:id AND charge_type='service_fee'"""
        ),
        {"cid": company_id, "id": document_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Servis cari borcu bulunamadı")
    result = dict(row)
    result["gross_amount"] = money(result["gross_amount"])
    result["exchange_rate"] = decimal_value(result["exchange_rate"])
    return result


def _lock_work_order(
    db: Session, company_id: int, work_order_id: int
) -> dict[str, object]:
    row = db.execute(
        text(
            """SELECT id,company_id,customer_id,work_order_no,status,completed_at
            FROM work_orders
            WHERE company_id=:cid AND id=:work_order_id"""
            + _lock_suffix(db)
        ),
        {"cid": company_id, "work_order_id": work_order_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "İş emri bulunamadı")
    return dict(row)


def _source_snapshot(
    db: Session,
    company_id: int,
    work_order: dict[str, object],
) -> tuple[date, date, Decimal, str, Decimal, str, str]:
    completed_at = _aware_utc(work_order["completed_at"])
    document_date = completed_at.astimezone(ISTANBUL).date()
    term_days = int(
        db.execute(
            text(
                """SELECT payment_term_days FROM customers
                WHERE company_id=:cid AND id=:customer_id"""
            ),
            {"cid": company_id, "customer_id": int(work_order["customer_id"])},
        ).scalar_one()
    )
    due_date = document_date + timedelta(days=term_days)
    invoice = db.execute(
        text(
            """SELECT id,currency,exchange_rate
            FROM invoices
            WHERE company_id=:cid AND work_order_id=:work_order_id
              AND status='ISSUED' AND cancelled_at IS NULL
            ORDER BY id DESC LIMIT 1"""
        ),
        {"cid": company_id, "work_order_id": int(work_order["id"])},
    ).mappings().first()
    if invoice:
        invoice_amount = money(
            db.execute(
                text(
                    """SELECT COALESCE(SUM(customer_payable),0)
                    FROM invoice_items
                    WHERE company_id=:cid AND invoice_id=:invoice_id"""
                ),
                {"cid": company_id, "invoice_id": int(invoice["id"])},
            ).scalar_one()
        )
        rate = decimal_value(invoice["exchange_rate"])
        gross_amount = money(invoice_amount * rate)
        currency = str(invoice["currency"]).upper()
        source: dict[str, object] = {
            "source": "invoice",
            "invoice_id": int(invoice["id"]),
            "invoice_customer_amount": format(invoice_amount, "f"),
            "currency": currency,
            "exchange_rate": format(rate, "f"),
        }
    else:
        summary = build_invoice_summary(db, company_id, int(work_order["id"]))
        gross_amount = money(summary["warranty"]["customer_amount"])
        currency = "TRY"
        rate = Decimal("1")
        source = {
            "source": "computed",
            "labor_total": format(money(summary["totals"]["labor"]), "f"),
            "parts_total": format(money(summary["totals"]["parts"]), "f"),
            "grand_total": format(money(summary["totals"]["grand_total"]), "f"),
            "warranty_percent": format(
                decimal_value(summary["warranty"]["coverage_percent"]), "f"
            ),
            "customer_amount": format(gross_amount, "f"),
        }
    snapshot: dict[str, object] = {
        **source,
        "work_order_id": int(work_order["id"]),
        "work_order_no": str(work_order["work_order_no"]),
        "completed_at_utc": completed_at.isoformat(),
        "document_date_europe_istanbul": document_date.isoformat(),
        "payment_term_days": term_days,
        "due_date": due_date.isoformat(),
        "gross_amount_try": format(gross_amount, "f"),
    }
    serialized, fingerprint = _snapshot_fingerprint(snapshot)
    return document_date, due_date, gross_amount, currency, rate, serialized, fingerprint


def _next_revision(db: Session, company_id: int, work_order_id: int) -> int:
    return int(
        db.execute(
            text(
                """SELECT COALESCE(MAX(revision_no),0)+1
                FROM receivable_charge_documents
                WHERE company_id=:cid AND work_order_id=:work_order_id
                  AND charge_type='service_fee'"""
            ),
            {"cid": company_id, "work_order_id": work_order_id},
        ).scalar_one()
    )


def _insert_positive(
    db: Session,
    company_id: int,
    work_order: dict[str, object],
    *,
    actor_id: int,
    revision_no: int,
    source: tuple[date, date, Decimal, str, Decimal, str, str],
    now: datetime,
) -> int:
    document_date, due_date, gross, currency, rate, snapshot, fingerprint = source
    return int(
        db.execute(
            text(
                """INSERT INTO receivable_charge_documents(
                    company_id,work_order_id,customer_id,charge_type,
                    period_start,period_end,due_date_snapshot,
                    calculation_snapshot,gross_amount,status,
                    calculation_fingerprint,revision_no,
                    currency,exchange_rate,created_by,posted_by,created_at,posted_at
                ) VALUES(
                    :cid,:work_order_id,:customer_id,'service_fee',
                    :document_date,:document_date,:due_date,
                    :snapshot,:gross_amount,'posted',
                    :fingerprint,:revision_no,
                    :currency,:exchange_rate,:actor_id,:actor_id,:now,:now
                ) RETURNING id"""
            ),
            {
                "cid": company_id,
                "work_order_id": int(work_order["id"]),
                "customer_id": int(work_order["customer_id"]),
                "document_date": document_date,
                "due_date": due_date,
                "snapshot": snapshot,
                "gross_amount": gross,
                "fingerprint": fingerprint,
                "revision_no": revision_no,
                "currency": currency,
                "exchange_rate": rate,
                "actor_id": actor_id,
                "now": now,
            },
        ).scalar_one()
    )


def _active_document(
    db: Session, company_id: int, work_order_id: int, *, lock: bool
) -> dict[str, object] | None:
    row = db.execute(
        text(
            """SELECT * FROM receivable_charge_documents
            WHERE company_id=:cid AND work_order_id=:work_order_id
              AND charge_type='service_fee' AND status='posted'
              AND reversal_of_document_id IS NULL
            ORDER BY revision_no DESC LIMIT 1"""
            + (_lock_suffix(db) if lock else "")
        ),
        {"cid": company_id, "work_order_id": work_order_id},
    ).mappings().first()
    return dict(row) if row else None


def _has_history(db: Session, company_id: int, work_order_id: int) -> bool:
    return (
        db.execute(
            text(
                """SELECT 1 FROM receivable_charge_documents
                WHERE company_id=:cid AND work_order_id=:work_order_id
                  AND charge_type='service_fee' LIMIT 1"""
            ),
            {"cid": company_id, "work_order_id": work_order_id},
        ).first()
        is not None
    )


def _reverse_active(
    db: Session,
    company_id: int,
    active: dict[str, object],
    *,
    actor_id: int,
    reason: str,
    revision_no: int,
    now: datetime,
) -> int:
    document_id = int(active["id"])
    assert_charge_document_unallocated(db, company_id, document_id)
    reversal_snapshot, reversal_fingerprint = _snapshot_fingerprint(
        {
            "source": "reversal",
            "reversal_of_document_id": document_id,
            "reason": reason,
            "original_calculation_fingerprint": str(active["calculation_fingerprint"]),
        }
    )
    reversal_id = int(
        db.execute(
            text(
                """INSERT INTO receivable_charge_documents(
                    company_id,work_order_id,customer_id,charge_type,
                    period_start,period_end,due_date_snapshot,
                    calculation_snapshot,gross_amount,status,
                    calculation_fingerprint,revision_no,reversal_of_document_id,
                    currency,exchange_rate,created_by,posted_by,created_at,posted_at
                ) VALUES(
                    :cid,:work_order_id,:customer_id,'service_fee',
                    :period_start,:period_end,:due_date,
                    :snapshot,:gross_amount,'posted',
                    :fingerprint,:revision_no,:original_id,
                    :currency,:exchange_rate,:actor_id,:actor_id,:now,:now
                ) RETURNING id"""
            ),
            {
                "cid": company_id,
                "work_order_id": int(active["work_order_id"]),
                "customer_id": int(active["customer_id"]),
                "period_start": active["period_start"],
                "period_end": active["period_end"],
                "due_date": active["due_date_snapshot"],
                "snapshot": reversal_snapshot,
                "gross_amount": money(-money(active["gross_amount"])),
                "fingerprint": reversal_fingerprint,
                "revision_no": revision_no,
                "original_id": document_id,
                "currency": active["currency"],
                "exchange_rate": active["exchange_rate"],
                "actor_id": actor_id,
                "now": now,
            },
        ).scalar_one()
    )
    updated = db.execute(
        text(
            """UPDATE receivable_charge_documents
            SET status='reversed',reversed_by=:actor_id,reversed_at=:now
            WHERE company_id=:cid AND id=:id AND status='posted'"""
        ),
        {"cid": company_id, "id": document_id, "actor_id": actor_id, "now": now},
    )
    if updated.rowcount != 1:
        raise HTTPException(409, "Servis cari borcu eşzamanlı olarak değiştirildi")
    return reversal_id


def reconcile_service_receivable(
    db: Session,
    company_id: int,
    work_order_id: int,
    *,
    actor_id: int,
    allow_initial_create: bool = True,
) -> dict[str, object] | None:
    work_order = _lock_work_order(db, company_id, work_order_id)
    if str(work_order["status"]) not in {"COMPLETED", "DELIVERED"}:
        raise HTTPException(409, "Servis cari borcu için iş emri tamamlanmış olmalıdır")
    source = _source_snapshot(db, company_id, work_order)
    active = _active_document(db, company_id, work_order_id, lock=True)
    now = datetime.now(timezone.utc)
    if active is None:
        if _has_history(db, company_id, work_order_id):
            # Explicit reversal is terminal until a separately approved restore flow exists.
            return None
        if not allow_initial_create:
            return None
        document_id = _insert_positive(
            db,
            company_id,
            work_order,
            actor_id=actor_id,
            revision_no=1,
            source=source,
            now=now,
        )
        return _service_document(db, company_id, document_id)

    if str(active["calculation_fingerprint"]) == source[-1]:
        return _service_document(db, company_id, int(active["id"]))

    next_revision = _next_revision(db, company_id, work_order_id)
    _reverse_active(
        db,
        company_id,
        active,
        actor_id=actor_id,
        reason="invoice_reconciliation",
        revision_no=next_revision,
        now=now,
    )
    document_id = _insert_positive(
        db,
        company_id,
        work_order,
        actor_id=actor_id,
        revision_no=next_revision + 1,
        source=source,
        now=now,
    )
    return _service_document(db, company_id, document_id)


def reverse_service_receivable(
    db: Session,
    company_id: int,
    work_order_id: int,
    idempotency_key: str,
    *,
    actor_id: int,
    reason: str,
) -> tuple[dict[str, object], bool]:
    fingerprint = _request_fingerprint(
        {"work_order_id": work_order_id, "reason": reason}
    )
    replay_id = _request_replay(
        db,
        company_id,
        "reverse_service_fee_document",
        str(work_order_id),
        idempotency_key,
        fingerprint,
    )
    if replay_id is not None:
        return _service_document(db, company_id, replay_id), False

    _lock_work_order(db, company_id, work_order_id)
    active = _active_document(db, company_id, work_order_id, lock=True)
    if active is None:
        raise HTTPException(409, "Aktif servis cari borcu bulunamadı")
    claim_id, replay_id = _claim_request(
        db,
        company_id,
        "reverse_service_fee_document",
        str(work_order_id),
        idempotency_key,
        fingerprint,
    )
    if replay_id is not None:
        return _service_document(db, company_id, replay_id), False
    now = datetime.now(timezone.utc)
    reversal_id = _reverse_active(
        db,
        company_id,
        active,
        actor_id=actor_id,
        reason=reason,
        revision_no=_next_revision(db, company_id, work_order_id),
        now=now,
    )
    _complete_request(db, company_id, claim_id, reversal_id)
    return _service_document(db, company_id, reversal_id), True


def assert_work_order_receivable_inactive(
    db: Session, company_id: int, work_order_id: int
) -> None:
    if _active_document(db, company_id, work_order_id, lock=True) is not None:
        raise HTTPException(
            409,
            "Cari borcu bulunan iş emri yeniden açılamaz; önce borcu tersleyin",
        )


def service_receivable_net_total(
    db: Session, company_id: int, customer_id: int
) -> Decimal:
    return money(
        db.execute(
            text(
                """SELECT COALESCE(SUM(gross_amount),0)
                FROM receivable_charge_documents
                WHERE company_id=:cid AND customer_id=:customer_id
                  AND charge_type='service_fee'
                  AND status IN ('posted','reversed')
                  AND posted_at IS NOT NULL"""
            ),
            {"cid": company_id, "customer_id": customer_id},
        ).scalar_one()
        or ZERO_MONEY
    )
