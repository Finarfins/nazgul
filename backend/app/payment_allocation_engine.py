from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import random
import time
from typing import Callable

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from .business_time import business_today
from .config import settings
from .document_engine import SALES_IMPORT_NOTE, accounting_document_status_sql
from .money import ZERO_MONEY, money
from .receivables_engine import normalized_date_sql, parse_receivable_date
from .finance_engine import remove_payment_finance, sync_payment_finance

MAX_TRANSACTION_ATTEMPTS = 3
RETRYABLE_SQLSTATES = {"40P01", "40001"}

# V2b shipped this exact message as the write-path guard. V2c keeps it verbatim
# for the flag-OFF case so the contract callers already handle does not change.
CHARGE_ALLOCATION_DISABLED_MESSAGE = "vade farkı belgesine tahsis V2c'de"
# The receivable ledger (orders, late-fee charge documents, allocations) is kept
# in the company base currency. A payment settled through a foreign-currency
# account can therefore never be applied to a charge document.
LEDGER_CURRENCY = "TRY"
# Uniform reply for any charge target that is missing, draft, reversed, owned by
# another tenant or by another customer: the caller learns nothing beyond
# "not allocatable".
CHARGE_TARGET_NOT_FOUND = "Tahsis edilebilir tahakkuk belgesi bulunamadı"


def ensure_charge_allocation_enabled() -> None:
    """Guard every write path that would touch a charge-document target."""

    if not settings.receivable_charge_allocation_enabled:
        raise HTTPException(422, CHARGE_ALLOCATION_DISABLED_MESSAGE)


def _resolve_target(
    order_id: int | None,
    receivable_charge_id: int | None,
) -> tuple[int | None, int | None]:
    """Mirror the database XOR check in the application layer."""

    if (order_id is None) == (receivable_charge_id is None):
        raise HTTPException(
            422,
            "Tahsis hedefi olarak ya belge ya da tahakkuk belirtilmelidir",
        )
    if receivable_charge_id is not None:
        ensure_charge_allocation_enabled()
    return order_id, receivable_charge_id


@dataclass(frozen=True)
class AllocationLine:
    order_id: int | None
    amount: Decimal
    allocation_type: str
    receivable_charge_id: int | None = None


@dataclass(frozen=True)
class AllocationResult:
    payment_id: int
    allocated_amount: Decimal
    unallocated_amount: Decimal
    allocations: tuple[AllocationLine, ...]

    def snapshot(self) -> str:
        payload = asdict(self)
        payload["allocated_amount"] = _decimal_text(self.allocated_amount)
        payload["unallocated_amount"] = _decimal_text(self.unallocated_amount)
        payload["allocations"] = [
            {
                "order_id": row.order_id,
                "amount": _decimal_text(row.amount),
                "allocation_type": row.allocation_type,
                "receivable_charge_id": row.receivable_charge_id,
            }
            for row in self.allocations
        ]
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_snapshot(cls, snapshot: str) -> AllocationResult:
        payload = json.loads(snapshot)
        return cls(
            payment_id=int(payload["payment_id"]),
            allocated_amount=money(payload["allocated_amount"]),
            unallocated_amount=money(payload["unallocated_amount"]),
            allocations=tuple(
                AllocationLine(
                    order_id=(
                        int(row["order_id"])
                        if row.get("order_id") is not None
                        else None
                    ),
                    amount=money(row["amount"]),
                    allocation_type=str(row["allocation_type"]),
                    receivable_charge_id=(
                        int(row["receivable_charge_id"])
                        if row.get("receivable_charge_id") is not None
                        else None
                    ),
                )
                for row in payload["allocations"]
            ),
        )


@dataclass(frozen=True)
class AllocationMutationResult:
    operation: str
    payment_id: int
    order_id: int | None
    amount: Decimal
    net_amount: Decimal
    source_allocation_id: int | None = None
    reversal_allocation_id: int | None = None
    allocation_id: int | None = None
    receivable_charge_id: int | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    def snapshot(self) -> str:
        payload = self.as_dict()
        payload["amount"] = _decimal_text(self.amount)
        payload["net_amount"] = _decimal_text(self.net_amount)
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_snapshot(cls, snapshot: str) -> AllocationMutationResult:
        payload = json.loads(snapshot)
        payload["amount"] = money(payload["amount"])
        payload["net_amount"] = money(payload["net_amount"])
        return cls(**payload)


def _decimal_text(value: Decimal) -> str:
    return format(money(value), ".2f")


def _payment_write_fingerprint(values: dict[str, object]) -> str:
    canonical_values = dict(values)
    canonical_values["amount"] = _decimal_text(money(values["amount"]))
    canonical_values["payment_date"] = parse_receivable_date(
        values["payment_date"]
    ).isoformat()
    canonical = json.dumps(
        canonical_values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _lock_suffix(db: Session) -> str:
    return " FOR UPDATE" if db.get_bind().dialect.name == "postgresql" else ""


def _canonical_fingerprint(
    company_id: int,
    payment: dict[str, object],
) -> str:
    payment_date = parse_receivable_date(payment["payment_date"]).isoformat()
    payload = {
        "company_id": company_id,
        "operation_type": "allocate_payment",
        "resource_type": "payment",
        "resource_id": str(payment["id"]),
        "amount": _decimal_text(money(payment["amount"])),
        "payment_date": payment_date,
        "entity_type": str(payment["entity_type"]),
        "entity_id": int(payment["entity_id"]),
        "reference_type": payment["reference_type"],
        "reference_id": payment["reference_id"],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _claim_idempotency(
    db: Session,
    company_id: int,
    payment_id: int,
    idempotency_key: str,
    fingerprint: str,
) -> tuple[int, AllocationResult | None]:
    params = {
        "cid": company_id,
        "operation": "allocate_payment",
        "resource_type": "payment",
        "resource_id": str(payment_id),
        "key": idempotency_key,
        "fingerprint": fingerprint,
    }
    claim_id: int | None = None
    try:
        with db.begin_nested():
            claim_id = int(
                db.execute(
                    text(
                        """INSERT INTO payment_idempotency(
                            company_id,operation_type,resource_type,resource_id,
                            idempotency_key,status,request_fingerprint
                        ) VALUES(
                            :cid,:operation,:resource_type,:resource_id,
                            :key,'processing',:fingerprint
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
            """SELECT id,status,request_fingerprint,result_snapshot
            FROM payment_idempotency
            WHERE company_id=:cid AND operation_type=:operation
              AND resource_type=:resource_type AND resource_id=:resource_id
              AND idempotency_key=:key"""
            + _lock_suffix(db)
        ),
        params,
    ).mappings().first()
    if not row:
        return _claim_idempotency(
            db, company_id, payment_id, idempotency_key, fingerprint
        )
    if row["request_fingerprint"] != fingerprint:
        raise HTTPException(409, "Idempotency anahtarı farklı bir istek için kullanılmış")
    if row["status"] == "completed" and row["result_snapshot"]:
        return int(row["id"]), AllocationResult.from_snapshot(row["result_snapshot"])
    if row["status"] == "failed":
        db.execute(
            text(
                """UPDATE payment_idempotency
                SET status='processing',result_snapshot=NULL,completed_at=NULL
                WHERE id=:id AND company_id=:cid"""
            ),
            {"id": int(row["id"]), "cid": company_id},
        )
        return int(row["id"]), None
    raise HTTPException(409, "Tahsis işlemi halen devam ediyor")


def _claim_payment_create(
    db: Session,
    company_id: int,
    idempotency_key: str,
    fingerprint: str,
) -> tuple[int, dict[str, object] | None]:
    params = {
        "cid": company_id,
        "operation": "create_payment",
        "resource_type": "payment",
        "resource_id": "create",
        "key": idempotency_key,
        "fingerprint": fingerprint,
    }
    claim_id: int | None = None
    try:
        with db.begin_nested():
            claim_id = int(
                db.execute(
                    text(
                        """INSERT INTO payment_idempotency(
                            company_id,operation_type,resource_type,resource_id,
                            idempotency_key,status,request_fingerprint
                        ) VALUES(
                            :cid,:operation,:resource_type,:resource_id,
                            :key,'processing',:fingerprint
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
            """SELECT id,status,request_fingerprint,result_snapshot
            FROM payment_idempotency
            WHERE company_id=:cid AND operation_type=:operation
              AND resource_type=:resource_type AND resource_id=:resource_id
              AND idempotency_key=:key"""
            + _lock_suffix(db)
        ),
        params,
    ).mappings().first()
    if not row:
        return _claim_payment_create(
            db,
            company_id,
            idempotency_key,
            fingerprint,
        )
    if row["request_fingerprint"] != fingerprint:
        raise HTTPException(409, "Idempotency anahtarı farklı bir ödeme için kullanılmış")
    if row["status"] == "completed" and row["result_snapshot"]:
        return int(row["id"]), json.loads(row["result_snapshot"])
    raise HTTPException(409, "Ödeme oluşturma işlemi halen devam ediyor")


def _operation_fingerprint(
    company_id: int,
    operation_type: str,
    resource_type: str,
    resource_id: str,
    values: dict[str, object],
) -> str:
    def canonical(value: object) -> object:
        if isinstance(value, Decimal):
            return _decimal_text(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: canonical(value[key]) for key in sorted(value)}
        if isinstance(value, (list, tuple)):
            return [canonical(item) for item in value]
        return value

    payload = {
        "company_id": company_id,
        "operation_type": operation_type,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "values": canonical(values),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _claim_operation_idempotency(
    db: Session,
    company_id: int,
    operation_type: str,
    resource_type: str,
    resource_id: str,
    idempotency_key: str,
    fingerprint: str,
) -> tuple[int, AllocationMutationResult | None]:
    key = idempotency_key.strip()
    if not key:
        raise HTTPException(400, "Idempotency-Key zorunludur")
    if len(key) > 255:
        raise HTTPException(400, "Idempotency-Key en fazla 255 karakter olabilir")
    params = {
        "cid": company_id,
        "operation": operation_type,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "key": key,
        "fingerprint": fingerprint,
    }
    claim_id: int | None = None
    try:
        claim_id = int(
            db.execute(
                text(
                    """INSERT INTO payment_idempotency(
                        company_id,operation_type,resource_type,resource_id,
                        idempotency_key,status,request_fingerprint
                    ) VALUES(
                        :cid,:operation,:resource_type,:resource_id,
                        :key,'processing',:fingerprint
                    ) RETURNING id"""
                ),
                params,
            ).scalar_one()
        )
    except IntegrityError:
        # The claim is the mutation transaction's first write. A full rollback
        # keeps PostgreSQL usable after UNIQUE failure and prevents SQLite from
        # persisting a top-level SAVEPOINT as a forever-"processing" claim.
        db.rollback()
    if claim_id is not None:
        return claim_id, None

    row = db.execute(
        text(
            """SELECT id,status,request_fingerprint,result_snapshot
            FROM payment_idempotency
            WHERE company_id=:cid AND operation_type=:operation
              AND resource_type=:resource_type AND resource_id=:resource_id
              AND idempotency_key=:key"""
            + _lock_suffix(db)
        ),
        params,
    ).mappings().first()
    if not row:
        return _claim_operation_idempotency(
            db,
            company_id,
            operation_type,
            resource_type,
            resource_id,
            key,
            fingerprint,
        )
    if row["request_fingerprint"] != fingerprint:
        raise HTTPException(409, "Idempotency anahtarı farklı bir istek için kullanılmış")
    if row["status"] == "completed" and row["result_snapshot"]:
        return int(row["id"]), AllocationMutationResult.from_snapshot(
            row["result_snapshot"]
        )
    if row["status"] == "failed":
        db.execute(
            text(
                """UPDATE payment_idempotency
                SET status='processing',result_snapshot=NULL,completed_at=NULL
                WHERE id=:id AND company_id=:cid"""
            ),
            {"id": int(row["id"]), "cid": company_id},
        )
        return int(row["id"]), None
    raise HTTPException(409, "Tahsis işlemi halen devam ediyor")


def _complete_operation_idempotency(
    db: Session,
    company_id: int,
    claim_id: int,
    result: AllocationMutationResult,
) -> None:
    db.execute(
        text(
            """UPDATE payment_idempotency
            SET status='completed',result_snapshot=:snapshot,completed_at=:completed_at
            WHERE id=:id AND company_id=:cid"""
        ),
        {
            "snapshot": result.snapshot(),
            "completed_at": datetime.now(timezone.utc),
            "id": claim_id,
            "cid": company_id,
        },
    )


def _lock_payment(db: Session, company_id: int, payment_id: int) -> dict[str, object]:
    row = db.execute(
        text(
            """SELECT id,company_id,entity_type,entity_id,amount,payment_date,
            reference_type,reference_id,account_id,financial_transaction_id
            FROM payments WHERE id=:id AND company_id=:cid"""
            + _lock_suffix(db)
        ),
        {"id": payment_id, "cid": company_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Ödeme bulunamadı")
    return dict(row)


def _validate_receivable_payment(
    payment: dict[str, object],
    *,
    require_charge_flag: bool = True,
) -> None:
    if payment["entity_type"] != "customer":
        raise HTTPException(422, "Alacak tahsisi yalnız müşteri ödemelerine uygulanır")
    if payment["reference_type"] not in (None, "order", "late_fee"):
        raise HTTPException(422, "Ödeme ile satış belgesi türü uyuşmuyor")
    if payment["reference_type"] == "late_fee" and require_charge_flag:
        # Reversal must stay reachable after the flag is switched back off, so
        # unwinding paths pass require_charge_flag=False.
        ensure_charge_allocation_enabled()
    if payment["reference_type"] is not None and payment["reference_id"] is None:
        raise HTTPException(422, "Belgeye bağlı ödemede belge kimliği zorunludur")


def _payment_currency(
    db: Session,
    company_id: int,
    payment: dict[str, object],
) -> str:
    """Fail-closed currency of a payment.

    A payment without a finance account settles in the company base currency.
    An account whose currency is missing or unreadable is rejected rather than
    assumed to be the base currency.
    """

    account_id = payment.get("account_id")
    if account_id is None:
        return LEDGER_CURRENCY
    row = db.execute(
        text(
            "SELECT currency FROM finance_accounts WHERE id=:id AND company_id=:cid"
        ),
        {"id": int(account_id), "cid": company_id},
    ).first()
    if not row:
        raise HTTPException(409, "Ödeme hesabı tenant içinde bulunamadı")
    currency = str(row[0] or "").strip().upper()
    if not currency:
        raise HTTPException(409, "Ödeme hesabının para birimi tanımsız")
    return currency


def _require_charge_currency_match(
    db: Session,
    company_id: int,
    payment: dict[str, object],
) -> None:
    currency = _payment_currency(db, company_id, payment)
    if currency != LEDGER_CURRENCY:
        raise HTTPException(
            409,
            "Tahakkuk belgesi ile ödeme para birimi uyuşmuyor",
        )


def _lock_customer(db: Session, company_id: int, customer_id: int) -> None:
    row = db.execute(
        text(
            "SELECT id FROM customers WHERE id=:id AND company_id=:cid"
            + _lock_suffix(db)
        ),
        {"id": customer_id, "cid": company_id},
    ).first()
    if not row:
        raise HTTPException(409, "Ödeme carisi tenant içinde bulunamadı")


def _lock_documents(
    db: Session,
    company_id: int,
    customer_id: int,
    reference_id: int | None,
) -> list[dict[str, object]]:
    dialect = db.get_bind().dialect.name
    normalized_due = normalized_date_sql("due_date", dialect)
    params: dict[str, object] = {
        "cid": company_id,
        "customer_id": customer_id,
        "sales_import_note": SALES_IMPORT_NOTE,
    }
    document_scope = " AND due_date IS NOT NULL AND due_date<>''"
    if reference_id is not None:
        document_scope = (
            " AND ((due_date IS NOT NULL AND due_date<>'') OR id=:reference_id)"
        )
        params["reference_id"] = reference_id
    rows = db.execute(
        text(
            f"""SELECT id,customer_id,due_date,final_total,paid_amount,status,note
            FROM orders
            WHERE company_id=:cid AND customer_id=:customer_id
              AND {accounting_document_status_sql('status', 'note')}"""
            + document_scope
            + f" ORDER BY {normalized_due},id"
            + _lock_suffix(db)
        ),
        params,
    ).mappings().all()
    if reference_id is not None and reference_id not in {
        int(row["id"]) for row in rows
    }:
        raise HTTPException(409, "Ödeme belgesi açık ve vadeli bir müşteri belgesi değil")
    return [dict(row) for row in rows]


def _lock_charge_documents(
    db: Session,
    company_id: int,
    customer_id: int,
    charge_id: int | None,
) -> list[dict[str, object]]:
    """Lock the customer's allocatable charge documents.

    Only posted originals participate: drafts are not receivables yet, reversal
    documents carry the negative counter-entry and reversed originals are closed.
    The rows are locked in ``(due_date_snapshot, id)`` order, immediately after
    the order rows, so every writer takes the same lock sequence.
    """

    rows = db.execute(
        text(
            """SELECT id,customer_id,due_date_snapshot,gross_amount,status,
            reversal_of_document_id
            FROM receivable_charge_documents
            WHERE company_id=:cid AND customer_id=:customer_id
              AND charge_type IN ('late_fee','service_fee') AND status='posted'
              AND reversal_of_document_id IS NULL
            ORDER BY due_date_snapshot,id"""
            + _lock_suffix(db)
        ),
        {"cid": company_id, "customer_id": customer_id},
    ).mappings().all()
    documents = [dict(row) for row in rows]
    if charge_id is not None and charge_id not in {
        int(row["id"]) for row in documents
    }:
        raise HTTPException(404, CHARGE_TARGET_NOT_FOUND)
    return documents


def _lock_charge_document_row(
    db: Session,
    company_id: int,
    charge_id: int,
) -> dict[str, object]:
    """Lock a charge document by id, whatever its status."""

    row = db.execute(
        text(
            """SELECT id,customer_id,due_date_snapshot,gross_amount,status,
            reversal_of_document_id
            FROM receivable_charge_documents
            WHERE company_id=:cid AND id=:id"""
            + _lock_suffix(db)
        ),
        {"cid": company_id, "id": charge_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, CHARGE_TARGET_NOT_FOUND)
    return dict(row)


def _charge_remaining(
    db: Session,
    company_id: int,
    charge: dict[str, object],
) -> Decimal:
    """Charge gross minus the NET active allocation total for that charge."""

    applied = money(
        db.execute(
            text(
                """SELECT COALESCE(SUM(
                    CASE WHEN reversal_of_allocation_id IS NULL
                         THEN amount ELSE -amount END
                ),0) FROM payment_allocations
                WHERE company_id=:cid AND receivable_charge_id=:charge_id"""
            ),
            {"cid": company_id, "charge_id": int(charge["id"])},
        ).scalar_one()
    )
    remaining = money(money(charge["gross_amount"]) - applied)
    if remaining < ZERO_MONEY:
        raise HTTPException(409, "Aktif tahsisler tahakkuk kalanını aşıyor")
    return remaining


def assert_charge_document_unallocated(
    db: Session,
    company_id: int,
    charge_id: int,
) -> None:
    """Reject reversing a charge document that still carries live allocations.

    The caller has already locked the charge document row; this locks exactly
    the allocation rows a new allocation would insert into, so the check and the
    competing write can never interleave. No automatic cascade: the operator
    reverses the allocations first.
    """

    rows = db.execute(
        text(
            """SELECT id,amount,reversal_of_allocation_id
            FROM payment_allocations
            WHERE company_id=:cid AND receivable_charge_id=:charge_id
            ORDER BY id"""
            + _lock_suffix(db)
        ),
        {"cid": company_id, "charge_id": charge_id},
    ).mappings().all()
    net = ZERO_MONEY
    for row in rows:
        amount = money(row["amount"])
        net = money(
            net - amount if row["reversal_of_allocation_id"] is not None else net + amount
        )
    if net > ZERO_MONEY:
        raise HTTPException(
            409,
            "Tahsisli tahakkuk belgesi terslenemez; önce tahsisleri geri alın",
        )


def _lock_existing_allocations(
    db: Session,
    company_id: int,
    payment_id: int,
    order_ids: list[int],
    charge_ids: list[int] | None = None,
) -> None:
    charge_ids = charge_ids or []
    clauses = ["payment_id=:payment_id"]
    params: dict[str, object] = {"cid": company_id, "payment_id": payment_id}
    if order_ids:
        placeholders = ",".join(f":order_{index}" for index in range(len(order_ids)))
        clauses.append(f"order_id IN ({placeholders})")
        params.update(
            {f"order_{index}": value for index, value in enumerate(order_ids)}
        )
    if charge_ids:
        placeholders = ",".join(f":charge_{index}" for index in range(len(charge_ids)))
        clauses.append(f"receivable_charge_id IN ({placeholders})")
        params.update(
            {f"charge_{index}": value for index, value in enumerate(charge_ids)}
        )
    db.execute(
        text(
            f"""SELECT id FROM payment_allocations
            WHERE company_id=:cid AND ({' OR '.join(clauses)})
            ORDER BY id"""
            + _lock_suffix(db)
        ),
        params,
    ).all()


def _document_remaining(
    db: Session,
    company_id: int,
    document: dict[str, object],
) -> Decimal:
    applied = money(
        db.execute(
            text(
                """SELECT COALESCE(SUM(
                    CASE WHEN reversal_of_allocation_id IS NULL
                         THEN amount ELSE -amount END
                ),0) FROM payment_allocations
                WHERE company_id=:cid AND order_id=:order_id"""
            ),
            {"cid": company_id, "order_id": int(document["id"])},
        ).scalar_one()
    )
    direct_applied = money(
        db.execute(
            text(
                """SELECT COALESCE(SUM(
                    CASE WHEN a.reversal_of_allocation_id IS NULL
                         THEN a.amount ELSE -a.amount END
                ),0)
                FROM payment_allocations a
                JOIN payments p ON p.id=a.payment_id AND p.company_id=a.company_id
                WHERE a.company_id=:cid AND a.order_id=:order_id
                  AND p.reference_type='order' AND p.reference_id=a.order_id"""
            ),
            {"cid": company_id, "order_id": int(document["id"])},
        ).scalar_one()
    )
    linked_returns = money(
        db.execute(
            text(
                """SELECT COALESCE(SUM(total),0) FROM returns
                WHERE company_id=:cid AND return_type='sale_return'
                  AND source_type='order' AND source_id=:order_id
                  AND COALESCE(status,'completed') NOT IN ('draft','cancelled')"""
            ),
            {"cid": company_id, "order_id": int(document["id"])},
        ).scalar_one()
    )
    residual_row = db.execute(
        text(
            """SELECT amount,status FROM receivable_legacy_residuals
            WHERE company_id=:cid AND order_id=:order_id"""
            + _lock_suffix(db)
        ),
        {"cid": company_id, "order_id": int(document["id"])},
    ).mappings().first()
    if residual_row and residual_row["status"] == "quarantined":
        raise HTTPException(409, "Belgenin legacy ödeme farkı inceleme bekliyor")
    residual = (
        money(residual_row["amount"])
        if residual_row and residual_row["status"] in {"confirmed", "resolved"}
        else ZERO_MONEY
    )
    stored_paid = money(document["paid_amount"])
    if stored_paid != direct_applied:
        raise HTTPException(
            409,
            "Belgenin paid_amount değeri tahsis defteriyle mutabık değil",
        )
    remaining = money(
        money(document["final_total"]) - applied - linked_returns - residual
    )
    if remaining < ZERO_MONEY:
        raise HTTPException(
            409,
            "Aktif tahsisler belge kalanını aşıyor",
        )
    return remaining


def _unlinked_return_credit(
    db: Session,
    company_id: int,
    customer_id: int,
) -> Decimal:
    return money(
        db.execute(
            text(
                """SELECT COALESCE(SUM(total),0) FROM returns
                WHERE company_id=:cid AND entity_id=:customer_id
                  AND return_type='sale_return'
                  AND (source_type IS NULL OR source_type<>'order' OR source_id IS NULL)
                  AND COALESCE(status,'completed') NOT IN ('draft','cancelled')"""
            ),
            {"cid": company_id, "customer_id": customer_id},
        ).scalar_one()
    )


def _lock_finance_rows(db: Session, company_id: int, payment: dict[str, object]) -> None:
    account_ids = sorted(
        {int(payment["account_id"])} if payment.get("account_id") is not None else set()
    )
    transaction_ids = sorted(
        {int(payment["financial_transaction_id"])}
        if payment.get("financial_transaction_id") is not None
        else set()
    )
    for account_id in account_ids:
        db.execute(
            text(
                "SELECT id FROM finance_accounts WHERE company_id=:cid AND id=:id"
                + _lock_suffix(db)
            ),
            {"cid": company_id, "id": account_id},
        ).first()
    for transaction_id in transaction_ids:
        db.execute(
            text(
                "SELECT id FROM finance_transactions WHERE company_id=:cid AND id=:id"
                + _lock_suffix(db)
            ),
            {"cid": company_id, "id": transaction_id},
        ).first()


def _materialize_direct_paid_amount(
    db: Session,
    company_id: int,
    order_ids: list[int],
) -> None:
    for order_id in sorted(order_ids):
        direct_total = money(
            db.execute(
                text(
                    """SELECT COALESCE(SUM(
                        CASE WHEN a.reversal_of_allocation_id IS NULL
                             THEN a.amount ELSE -a.amount END
                    ),0)
                    FROM payment_allocations a
                    JOIN payments p ON p.id=a.payment_id AND p.company_id=a.company_id
                    WHERE a.company_id=:cid AND a.order_id=:order_id
                      AND p.reference_type='order' AND p.reference_id=a.order_id"""
                ),
                {"cid": company_id, "order_id": order_id},
            ).scalar_one()
        )
        db.execute(
            text(
                """UPDATE orders SET paid_amount=:paid
                WHERE id=:order_id AND company_id=:cid"""
            ),
            {"paid": direct_total, "order_id": order_id, "cid": company_id},
        )


def _record_allocation_audit(
    db: Session,
    company_id: int,
    payment_id: int,
    created_by: int | None,
    snapshot: str,
    action: str = "create",
) -> None:
    db.execute(
        text(
            """INSERT INTO entity_change_logs(
                company_id,entity_type,entity_id,action,before_json,after_json,
                changed_fields_json,actor_user_id,created_at
            ) VALUES(
                :cid,'payment_allocation',:payment_id,:action,NULL,:snapshot,
                :changed_fields,:created_by,:created_at
            )"""
        ),
        {
            "cid": company_id,
            "payment_id": payment_id,
            "action": action,
            "snapshot": snapshot,
            "changed_fields": json.dumps(
                {"fields": ["allocations"]},
                ensure_ascii=False,
                sort_keys=True,
            ),
            "created_by": created_by,
            "created_at": (
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            ),
        },
    )


def allocate_payment(
    db: Session,
    company_id: int,
    payment_id: int,
    idempotency_key: str,
    *,
    created_by: int | None = None,
    commit: bool = True,
) -> AllocationResult:
    if not idempotency_key.strip():
        raise HTTPException(422, "Idempotency anahtarı zorunludur")
    payment_snapshot = db.execute(
        text(
            """SELECT id,entity_type,entity_id,amount,payment_date,
            reference_type,reference_id FROM payments
            WHERE id=:id AND company_id=:cid"""
        ),
        {"id": payment_id, "cid": company_id},
    ).mappings().first()
    if not payment_snapshot:
        raise HTTPException(404, "Ödeme bulunamadı")
    fingerprint = _canonical_fingerprint(company_id, dict(payment_snapshot))
    claim_id, replay = _claim_idempotency(
        db, company_id, payment_id, idempotency_key, fingerprint
    )
    if replay is not None:
        if commit:
            db.commit()
        return replay

    payment = _lock_payment(db, company_id, payment_id)
    _validate_receivable_payment(payment)
    if _canonical_fingerprint(company_id, payment) != fingerprint:
        raise HTTPException(409, "Ödeme tahsis sırasında değişti")
    customer_id = int(payment["entity_id"])
    _lock_customer(db, company_id, customer_id)
    reference_id = (
        int(payment["reference_id"]) if payment["reference_id"] is not None else None
    )
    charge_reference_id = (
        reference_id if payment["reference_type"] == "late_fee" else None
    )
    order_reference_id = reference_id if payment["reference_type"] == "order" else None
    if charge_reference_id is not None:
        _require_charge_currency_match(db, company_id, payment)
    documents = _lock_documents(db, company_id, customer_id, order_reference_id)
    order_ids = [int(row["id"]) for row in documents]
    charges = (
        _lock_charge_documents(db, company_id, customer_id, charge_reference_id)
        if settings.receivable_charge_allocation_enabled
        else []
    )
    charge_ids = [int(row["id"]) for row in charges]
    _lock_existing_allocations(db, company_id, payment_id, order_ids, charge_ids)

    already_allocated = money(
        db.execute(
            text(
                """SELECT COALESCE(SUM(
                    CASE WHEN reversal_of_allocation_id IS NULL
                         THEN amount ELSE -amount END
                ),0) FROM payment_allocations
                WHERE company_id=:cid AND payment_id=:payment_id"""
            ),
            {"cid": company_id, "payment_id": payment_id},
        ).scalar_one()
    )
    available = money(money(payment["amount"]) - already_allocated)
    if available < ZERO_MONEY:
        raise HTTPException(409, "Aktif tahsis toplamı ödeme tutarını aşıyor")

    allocation_type = "document_create" if reference_id is not None else "fifo"
    lines: list[AllocationLine] = []
    remaining_payment = available
    unlinked_return_credit = _unlinked_return_credit(
        db,
        company_id,
        customer_id,
    )
    effective_date = parse_receivable_date(payment["payment_date"])

    def insert_line(order_id: int | None, charge_id: int | None, applied: Decimal):
        db.execute(
            text(
                """INSERT INTO payment_allocations(
                    company_id,payment_id,order_id,receivable_charge_id,amount,
                    allocation_type,effective_date,created_by
                ) VALUES(
                    :cid,:payment_id,:order_id,:charge_id,:amount,
                    :allocation_type,:effective_date,:created_by
                )"""
            ),
            {
                "cid": company_id,
                "payment_id": payment_id,
                "order_id": order_id,
                "charge_id": charge_id,
                "amount": applied,
                "allocation_type": allocation_type,
                "effective_date": effective_date,
                "created_by": created_by,
            },
        )
        lines.append(
            AllocationLine(
                order_id=order_id,
                amount=applied,
                allocation_type=allocation_type,
                receivable_charge_id=charge_id,
            )
        )

    open_principal = ZERO_MONEY
    for document in documents:
        document_remaining = _document_remaining(db, company_id, document)
        applied_return = min(document_remaining, unlinked_return_credit)
        document_remaining = money(document_remaining - applied_return)
        unlinked_return_credit = money(unlinked_return_credit - applied_return)
        if reference_id is not None and (
            order_reference_id is None or int(document["id"]) != order_reference_id
        ):
            open_principal = money(open_principal + document_remaining)
            continue
        if remaining_payment <= ZERO_MONEY:
            open_principal = money(open_principal + document_remaining)
            continue
        applied = min(document_remaining, remaining_payment)
        if applied <= ZERO_MONEY:
            open_principal = money(open_principal + document_remaining)
            continue
        insert_line(int(document["id"]), None, applied)
        remaining_payment = money(remaining_payment - applied)
        open_principal = money(open_principal + document_remaining - applied)

    # Late fees are only reachable once every open principal in the pool
    # (company + customer + currency) is closed. A payment pinned to a charge
    # document skips the principal pool entirely by construction.
    if remaining_payment > ZERO_MONEY and charges:
        if charge_reference_id is not None or open_principal == ZERO_MONEY:
            for charge in charges:
                if charge_reference_id is not None and (
                    int(charge["id"]) != charge_reference_id
                ):
                    continue
                if remaining_payment <= ZERO_MONEY:
                    break
                charge_remaining = _charge_remaining(db, company_id, charge)
                applied = min(charge_remaining, remaining_payment)
                if applied <= ZERO_MONEY:
                    continue
                insert_line(None, int(charge["id"]), applied)
                remaining_payment = money(remaining_payment - applied)
    if remaining_payment > ZERO_MONEY:
        raise HTTPException(409, "Ödeme müşterinin gerçek belge kalanını aşıyor")

    _lock_finance_rows(db, company_id, payment)
    if order_reference_id is not None:
        _materialize_direct_paid_amount(db, company_id, [order_reference_id])
    result = AllocationResult(
        payment_id=payment_id,
        allocated_amount=money(available - remaining_payment),
        unallocated_amount=remaining_payment,
        allocations=tuple(lines),
    )
    snapshot = result.snapshot()
    _record_allocation_audit(
        db,
        company_id,
        payment_id,
        created_by,
        snapshot,
    )
    db.execute(
        text(
            """UPDATE payment_idempotency
            SET status='completed',result_snapshot=:snapshot,completed_at=:completed_at
            WHERE id=:id AND company_id=:cid"""
        ),
        {
            "snapshot": snapshot,
            "completed_at": datetime.now(timezone.utc),
            "id": claim_id,
            "cid": company_id,
        },
    )
    if commit:
        db.commit()
    return result


def create_payment_with_allocation(
    db: Session,
    company_id: int,
    values: dict[str, object],
    idempotency_key: str,
    *,
    created_by: int | None = None,
    on_created: Callable[[int, dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """``on_created`` verilirse commit'ten hemen ÖNCE, aynı transaction içinde
    çağrılır (aktivite kaydı). Idempotent tekrar oynatmada çağrılmaz: tekrar
    oynatma yeni bir olay değildir."""
    key = idempotency_key.strip()
    if not key:
        raise HTTPException(400, "Idempotency-Key zorunludur")
    if len(key) > 255:
        raise HTTPException(400, "Idempotency-Key en fazla 255 karakter olabilir")
    fingerprint = _payment_write_fingerprint(values)
    claim_id, replay = _claim_payment_create(
        db,
        company_id,
        key,
        fingerprint,
    )
    if replay is not None:
        db.commit()
        return replay
    params = dict(values)
    params["cid"] = company_id
    payment_id = int(
        db.execute(
            text(
                """INSERT INTO payments(
                    entity_type,entity_id,amount,payment_date,note,company_id,
                    payment_method,account_id,reference_type,reference_id
                ) VALUES(
                    :entity_type,:entity_id,:amount,:payment_date,:note,:cid,
                    :payment_method,:account_id,:reference_type,:reference_id
                ) RETURNING id"""
            ),
            params,
        ).scalar_one()
    )
    if values["entity_type"] == "customer":
        allocate_payment(
            db,
            company_id,
            payment_id,
            f"{key}:allocation",
            created_by=created_by,
            commit=False,
        )
    sync_payment_finance(
        db,
        company_id,
        payment_id,
        str(values["entity_type"]),
        money(values["amount"]),
        str(values["payment_date"]),
        str(values["payment_method"]),
        values.get("note"),
        int(values["account_id"]) if values.get("account_id") is not None else None,
    )
    result = {"id": payment_id, **values}
    snapshot = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        default=lambda value: _decimal_text(value) if isinstance(value, Decimal) else str(value),
    )
    db.execute(
        text(
            """UPDATE payment_idempotency
            SET status='completed',result_snapshot=:snapshot,completed_at=:completed_at
            WHERE id=:id AND company_id=:cid"""
        ),
        {
            "snapshot": snapshot,
            "completed_at": datetime.now(timezone.utc),
            "id": claim_id,
            "cid": company_id,
        },
    )
    if on_created is not None:
        on_created(payment_id, result)
    db.commit()
    return result


def ensure_payment_mutable(
    db: Session,
    company_id: int,
    payment_id: int,
) -> dict[str, object]:
    payment = _lock_payment(db, company_id, payment_id)
    if payment["entity_type"] != "customer":
        return payment
    allocation_rows = db.execute(
        text(
            """SELECT id,amount,reversed_at,reversal_of_allocation_id
            FROM payment_allocations
            WHERE company_id=:cid AND payment_id=:payment_id
            ORDER BY id"""
            + _lock_suffix(db)
        ),
        {"cid": company_id, "payment_id": payment_id},
    ).mappings().all()
    reversal_totals: dict[int, Decimal] = {}
    for row in allocation_rows:
        original_id = row["reversal_of_allocation_id"]
        if original_id is not None and row["reversed_at"] is None:
            reversal_totals[int(original_id)] = money(
                reversal_totals.get(int(original_id), ZERO_MONEY)
                + money(row["amount"])
            )
    net_active = ZERO_MONEY
    for row in allocation_rows:
        if row["reversal_of_allocation_id"] is not None or row["reversed_at"] is not None:
            continue
        remaining = money(
            money(row["amount"]) - reversal_totals.get(int(row["id"]), ZERO_MONEY)
        )
        if remaining > ZERO_MONEY:
            net_active = money(net_active + remaining)
    if net_active > ZERO_MONEY:
        raise HTTPException(
            409,
            "Tahsis edilmiş ödeme reversal tamamlanmadan değiştirilemez",
        )
    return payment


def update_unallocated_payment(
    db: Session,
    company_id: int,
    payment_id: int,
    values: dict[str, object],
) -> dict[str, object]:
    ensure_payment_mutable(db, company_id, payment_id)
    params = dict(values)
    params.update({"id": payment_id, "cid": company_id})
    remove_payment_finance(db, company_id, payment_id)
    db.execute(
        text(
            """UPDATE payments SET
            entity_type=:entity_type,entity_id=:entity_id,amount=:amount,
            payment_date=:payment_date,note=:note,payment_method=:payment_method,
            account_id=:account_id,reference_type=:reference_type,reference_id=:reference_id
            WHERE id=:id AND company_id=:cid"""
        ),
        params,
    )
    if values["entity_type"] == "customer" and values.get("reference_type") in (
        "order",
        "late_fee",
    ):
        # Both document references write their allocation in the same
        # transaction as the payment row: a late_fee reference without a ledger
        # row would leave the charge document reporting applied=0 forever.
        allocate_payment(
            db,
            company_id,
            payment_id,
            f"payment-update:{payment_id}:{values['reference_type']}:"
            f"{values['reference_id']}",
            commit=False,
        )
    sync_payment_finance(
        db,
        company_id,
        payment_id,
        str(values["entity_type"]),
        money(values["amount"]),
        str(values["payment_date"]),
        str(values["payment_method"]),
        values.get("note"),
        int(values["account_id"]) if values.get("account_id") is not None else None,
    )
    return dict(
        db.execute(
            text("SELECT * FROM payments WHERE id=:id AND company_id=:cid"),
            {"id": payment_id, "cid": company_id},
        ).mappings().one()
    )


def delete_unallocated_payment(
    db: Session,
    company_id: int,
    payment_id: int,
) -> dict[str, object]:
    payment = ensure_payment_mutable(db, company_id, payment_id)
    remove_payment_finance(db, company_id, payment_id)
    db.execute(
        text("DELETE FROM payments WHERE id=:id AND company_id=:cid"),
        {"id": payment_id, "cid": company_id},
    )
    return payment


def _payment_net_allocation(
    db: Session,
    company_id: int,
    payment_id: int,
) -> Decimal:
    return money(
        db.execute(
            text(
                """SELECT COALESCE(SUM(
                    CASE WHEN reversal_of_allocation_id IS NULL
                         THEN amount ELSE -amount END
                ),0)
                FROM payment_allocations
                WHERE company_id=:cid AND payment_id=:payment_id"""
            ),
            {"cid": company_id, "payment_id": payment_id},
        ).scalar_one()
    )


def _fifo_adjusted_remaining(
    db: Session,
    company_id: int,
    customer_id: int,
    documents: list[dict[str, object]],
) -> dict[int, Decimal]:
    return_credit = _unlinked_return_credit(db, company_id, customer_id)
    remaining_by_order: dict[int, Decimal] = {}
    for document in documents:
        remaining = _document_remaining(db, company_id, document)
        applied_return = min(remaining, return_credit)
        remaining_by_order[int(document["id"])] = money(remaining - applied_return)
        return_credit = money(return_credit - applied_return)
    return remaining_by_order


def _allocation_snapshot(
    db: Session,
    company_id: int,
    allocation_id: int,
) -> dict[str, object]:
    row = db.execute(
        text(
            """SELECT id,company_id,payment_id,order_id,receivable_charge_id,amount,
            allocation_type,effective_date,recorded_at,created_by,reversed_at,
            reversed_by,reversal_allocation_id,reversal_of_allocation_id,note
            FROM payment_allocations
            WHERE company_id=:cid AND id=:id"""
        ),
        {"cid": company_id, "id": allocation_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Tahsis kaydı bulunamadı")
    return dict(row)


def _locked_allocation_rows(
    db: Session,
    company_id: int,
    payment_id: int,
    order_ids: list[int],
    charge_ids: list[int] | None = None,
) -> list[dict[str, object]]:
    charge_ids = charge_ids or []
    _lock_existing_allocations(db, company_id, payment_id, order_ids, charge_ids)
    clauses = ["payment_id=:payment_id"]
    params: dict[str, object] = {"cid": company_id, "payment_id": payment_id}
    if order_ids:
        clauses.append(
            "order_id IN ("
            + ",".join(f":order_{index}" for index in range(len(order_ids)))
            + ")"
        )
        params.update(
            {f"order_{index}": value for index, value in enumerate(order_ids)}
        )
    if charge_ids:
        clauses.append(
            "receivable_charge_id IN ("
            + ",".join(f":charge_{index}" for index in range(len(charge_ids)))
            + ")"
        )
        params.update(
            {f"charge_{index}": value for index, value in enumerate(charge_ids)}
        )
    return [
        dict(row)
        for row in db.execute(
            text(
                """SELECT id,company_id,payment_id,order_id,receivable_charge_id,
                amount,allocation_type,effective_date,recorded_at,created_by,
                reversed_at,reversed_by,reversal_allocation_id,
                reversal_of_allocation_id,note
                FROM payment_allocations
                WHERE company_id=:cid AND ("""
                + " OR ".join(clauses)
                + ") ORDER BY id"
                + _lock_suffix(db)
            ),
            params,
        ).mappings()
    ]


def _original_net_amount(
    rows: list[dict[str, object]],
    allocation_id: int,
) -> Decimal:
    original = next(
        (
            row
            for row in rows
            if int(row["id"]) == allocation_id
            and row["reversal_of_allocation_id"] is None
        ),
        None,
    )
    if original is None:
        raise HTTPException(422, "Yalnız normal tahsis kaydı geri alınabilir")
    reversed_total = money(
        sum(
            (
                money(row["amount"])
                for row in rows
                if row["reversal_of_allocation_id"] is not None
                and int(row["reversal_of_allocation_id"]) == allocation_id
            ),
            ZERO_MONEY,
        )
    )
    net_amount = money(money(original["amount"]) - reversed_total)
    if net_amount < ZERO_MONEY:
        raise HTTPException(409, "Reversal toplamı tahsis tutarını aşıyor")
    return net_amount


def _insert_reversal(
    db: Session,
    company_id: int,
    original: dict[str, object],
    amount: Decimal,
    effective_date: date,
    created_by: int | None,
    note: str | None,
) -> int:
    reversal_id = int(
        db.execute(
            text(
                """INSERT INTO payment_allocations(
                    company_id,payment_id,order_id,receivable_charge_id,amount,
                    allocation_type,effective_date,created_by,
                    reversal_of_allocation_id,note
                ) VALUES(
                    :cid,:payment_id,:order_id,:charge_id,:amount,
                    :allocation_type,:effective_date,:created_by,:original_id,:note
                ) RETURNING id"""
            ),
            {
                "cid": company_id,
                "payment_id": int(original["payment_id"]),
                "order_id": (
                    int(original["order_id"])
                    if original["order_id"] is not None
                    else None
                ),
                "charge_id": (
                    int(original["receivable_charge_id"])
                    if original.get("receivable_charge_id") is not None
                    else None
                ),
                "amount": amount,
                "allocation_type": str(original["allocation_type"]),
                "effective_date": effective_date,
                "created_by": created_by,
                "original_id": int(original["id"]),
                "note": note,
            },
        ).scalar_one()
    )
    return reversal_id


def _validate_mutation_date(
    effective_date: date,
    original_effective_date: object,
    closed_through: date | None,
) -> None:
    original_date = parse_receivable_date(original_effective_date)
    if effective_date > business_today():
        raise HTTPException(422, "Tahsis işlem tarihi gelecekte olamaz")
    if closed_through is not None and effective_date <= closed_through:
        raise HTTPException(409, "Kapalı muhasebe dönemine tahsis işlemi yapılamaz")
    if effective_date < original_date:
        raise HTTPException(422, "Reversal tarihi tahsis tarihinden önce olamaz")


def _manual_allocate_payment(
    db: Session,
    company_id: int,
    payment_id: int,
    order_id: int | None,
    amount: Decimal,
    effective_date: date,
    idempotency_key: str,
    *,
    created_by: int | None = None,
    note: str | None = None,
    receivable_charge_id: int | None = None,
    activity_hook: Callable[[Session, AllocationMutationResult], None] | None = None,
) -> AllocationMutationResult:
    order_id, receivable_charge_id = _resolve_target(order_id, receivable_charge_id)
    requested = money(amount)
    if requested <= ZERO_MONEY:
        raise HTTPException(422, "Tahsis tutarı sıfırdan büyük olmalıdır")
    if effective_date > business_today():
        raise HTTPException(422, "Tahsis işlem tarihi gelecekte olamaz")
    resource_id = (
        f"{payment_id}:{order_id}"
        if order_id is not None
        else f"{payment_id}:charge:{receivable_charge_id}"
    )
    fingerprint = _operation_fingerprint(
        company_id,
        "manual_allocation",
        "payment_order",
        resource_id,
        # The charge key is only added for charge targets, so fingerprints of
        # order allocations stay bit-identical to the ones stored before V2c.
        {
            "payment_id": payment_id,
            "order_id": order_id,
            "amount": requested,
            "effective_date": effective_date,
            "note": note,
            **(
                {"receivable_charge_id": receivable_charge_id}
                if receivable_charge_id is not None
                else {}
            ),
        },
    )
    claim_id, replay = _claim_operation_idempotency(
        db,
        company_id,
        "manual_allocation",
        "payment_order",
        resource_id,
        idempotency_key,
        fingerprint,
    )
    if replay is not None:
        db.commit()
        return replay

    payment = _lock_payment(db, company_id, payment_id)
    _validate_receivable_payment(payment)
    customer_id = int(payment["entity_id"])
    _lock_customer(db, company_id, customer_id)
    reference_id = payment["reference_id"]
    if reference_id is not None:
        pinned_target = (
            order_id if payment["reference_type"] == "order" else receivable_charge_id
        )
        if pinned_target is None or int(reference_id) != pinned_target:
            raise HTTPException(409, "Belgeye bağlı ödeme başka belgeye tahsis edilemez")
    if receivable_charge_id is not None:
        _require_charge_currency_match(db, company_id, payment)
    documents = _lock_documents(db, company_id, customer_id, order_id)
    order_ids = [int(row["id"]) for row in documents]
    charges = (
        _lock_charge_documents(db, company_id, customer_id, receivable_charge_id)
        if receivable_charge_id is not None
        else []
    )
    charge_ids = [int(row["id"]) for row in charges]
    _locked_allocation_rows(db, company_id, payment_id, order_ids, charge_ids)
    available = money(
        money(payment["amount"])
        - _payment_net_allocation(db, company_id, payment_id)
    )
    if available < requested:
        raise HTTPException(409, "Tahsis tutarı ödeme kalanını aşıyor")
    if order_id is not None:
        remaining = _fifo_adjusted_remaining(
            db, company_id, customer_id, documents
        ).get(order_id, ZERO_MONEY)
        if remaining < requested:
            raise HTTPException(409, "Tahsis tutarı gerçek belge kalanını aşıyor")
    else:
        charge = next(
            row for row in charges if int(row["id"]) == receivable_charge_id
        )
        if _charge_remaining(db, company_id, charge) < requested:
            raise HTTPException(409, "Tahsis tutarı tahakkuk kalanını aşıyor")
    allocation_id = int(
        db.execute(
            text(
                """INSERT INTO payment_allocations(
                    company_id,payment_id,order_id,receivable_charge_id,amount,
                    allocation_type,effective_date,created_by,note
                ) VALUES(
                    :cid,:payment_id,:order_id,:charge_id,:amount,'manual',
                    :effective_date,:created_by,:note
                ) RETURNING id"""
            ),
            {
                "cid": company_id,
                "payment_id": payment_id,
                "order_id": order_id,
                "charge_id": receivable_charge_id,
                "amount": requested,
                "effective_date": effective_date,
                "created_by": created_by,
                "note": note,
            },
        ).scalar_one()
    )
    _lock_finance_rows(db, company_id, payment)
    if payment["reference_type"] == "order" and order_id is not None:
        _materialize_direct_paid_amount(db, company_id, [order_id])
    result = AllocationMutationResult(
        operation="manual_allocation",
        payment_id=payment_id,
        allocation_id=allocation_id,
        order_id=order_id,
        receivable_charge_id=receivable_charge_id,
        amount=requested,
        net_amount=requested,
    )
    _record_allocation_audit(
        db,
        company_id,
        payment_id,
        created_by,
        result.snapshot(),
        action="manual_allocate",
    )
    _complete_operation_idempotency(db, company_id, claim_id, result)
    if activity_hook is not None:
        activity_hook(db, result)
    db.commit()
    return result


def _reverse_allocation(
    db: Session,
    company_id: int,
    allocation_id: int,
    amount: Decimal,
    effective_date: date,
    idempotency_key: str,
    *,
    created_by: int | None = None,
    note: str | None = None,
    closed_through: date | None = None,
    activity_hook: Callable[[Session, AllocationMutationResult], None] | None = None,
) -> AllocationMutationResult:
    requested = money(amount)
    if requested <= ZERO_MONEY:
        raise HTTPException(422, "Reversal tutarı sıfırdan büyük olmalıdır")
    snapshot = _allocation_snapshot(db, company_id, allocation_id)
    if snapshot["reversal_of_allocation_id"] is not None:
        raise HTTPException(422, "Reversal kaydı yeniden geri alınamaz")
    fingerprint = _operation_fingerprint(
        company_id,
        "reverse_allocation",
        "allocation",
        str(allocation_id),
        {
            "allocation_id": allocation_id,
            "amount": requested,
            "effective_date": effective_date,
            "note": note,
        },
    )
    claim_id, replay = _claim_operation_idempotency(
        db,
        company_id,
        "reverse_allocation",
        "allocation",
        str(allocation_id),
        idempotency_key,
        fingerprint,
    )
    if replay is not None:
        db.commit()
        return replay
    payment_id = int(snapshot["payment_id"])
    payment = _lock_payment(db, company_id, payment_id)
    _validate_receivable_payment(payment, require_charge_flag=False)
    customer_id = int(payment["entity_id"])
    _lock_customer(db, company_id, customer_id)
    order_id = (
        int(snapshot["order_id"]) if snapshot["order_id"] is not None else None
    )
    receivable_charge_id = (
        int(snapshot["receivable_charge_id"])
        if snapshot["receivable_charge_id"] is not None
        else None
    )
    documents = _lock_documents(db, company_id, customer_id, order_id)
    if receivable_charge_id is not None:
        # Reversing an allocation stays possible after the charge document has
        # itself been reversed, so the row is locked by id without a status
        # filter — the same row the charge reversal path locks.
        _lock_charge_document_row(db, company_id, receivable_charge_id)
    rows = _locked_allocation_rows(
        db,
        company_id,
        payment_id,
        [int(row["id"]) for row in documents],
        [receivable_charge_id] if receivable_charge_id is not None else [],
    )
    original = next(
        (row for row in rows if int(row["id"]) == allocation_id),
        None,
    )
    if original is None or original["reversal_of_allocation_id"] is not None:
        raise HTTPException(422, "Yalnız normal tahsis kaydı geri alınabilir")
    _validate_mutation_date(
        effective_date, original["effective_date"], closed_through
    )
    current_net = _original_net_amount(rows, allocation_id)
    if requested > current_net:
        raise HTTPException(409, "Reversal toplamı tahsis tutarını aşamaz")
    reversal_id = _insert_reversal(
        db,
        company_id,
        original,
        requested,
        effective_date,
        created_by,
        note,
    )
    net_amount = money(current_net - requested)
    if net_amount == ZERO_MONEY:
        db.execute(
            text(
                """UPDATE payment_allocations
                SET reversed_at=:reversed_at,reversed_by=:reversed_by,
                    reversal_allocation_id=:reversal_id
                WHERE id=:id AND company_id=:cid"""
            ),
            {
                "reversed_at": datetime.now(timezone.utc),
                "reversed_by": created_by,
                "reversal_id": reversal_id,
                "id": allocation_id,
                "cid": company_id,
            },
        )
    _lock_finance_rows(db, company_id, payment)
    if payment["reference_type"] == "order" and order_id is not None:
        _materialize_direct_paid_amount(db, company_id, [order_id])
    result = AllocationMutationResult(
        operation="reversal",
        payment_id=payment_id,
        source_allocation_id=allocation_id,
        reversal_allocation_id=reversal_id,
        order_id=order_id,
        receivable_charge_id=receivable_charge_id,
        amount=requested,
        net_amount=net_amount,
    )
    _record_allocation_audit(
        db,
        company_id,
        payment_id,
        created_by,
        result.snapshot(),
        action="reverse",
    )
    _complete_operation_idempotency(db, company_id, claim_id, result)
    if activity_hook is not None:
        activity_hook(db, result)
    db.commit()
    return result


def _reallocate_allocation(
    db: Session,
    company_id: int,
    allocation_id: int,
    target_order_id: int | None,
    amount: Decimal,
    effective_date: date,
    idempotency_key: str,
    *,
    created_by: int | None = None,
    note: str | None = None,
    closed_through: date | None = None,
    target_receivable_charge_id: int | None = None,
    activity_hook: Callable[[Session, AllocationMutationResult], None] | None = None,
) -> AllocationMutationResult:
    target_order_id, target_receivable_charge_id = _resolve_target(
        target_order_id, target_receivable_charge_id
    )
    requested = money(amount)
    if requested <= ZERO_MONEY:
        raise HTTPException(422, "Yeniden tahsis tutarı sıfırdan büyük olmalıdır")
    snapshot = _allocation_snapshot(db, company_id, allocation_id)
    if snapshot["reversal_of_allocation_id"] is not None:
        raise HTTPException(422, "Reversal kaydı yeniden tahsis edilemez")
    fingerprint = _operation_fingerprint(
        company_id,
        "reallocate_allocation",
        "allocation",
        str(allocation_id),
        {
            "allocation_id": allocation_id,
            "target_order_id": target_order_id,
            "amount": requested,
            "effective_date": effective_date,
            "note": note,
            **(
                {"target_receivable_charge_id": target_receivable_charge_id}
                if target_receivable_charge_id is not None
                else {}
            ),
        },
    )
    claim_id, replay = _claim_operation_idempotency(
        db,
        company_id,
        "reallocate_allocation",
        "allocation",
        str(allocation_id),
        idempotency_key,
        fingerprint,
    )
    if replay is not None:
        db.commit()
        return replay
    payment_id = int(snapshot["payment_id"])
    payment = _lock_payment(db, company_id, payment_id)
    _validate_receivable_payment(payment)
    if payment["reference_id"] is not None:
        pinned_target = (
            target_order_id
            if payment["reference_type"] == "order"
            else target_receivable_charge_id
        )
        if pinned_target is None or int(payment["reference_id"]) != pinned_target:
            raise HTTPException(
                409,
                "Belgeye bağlı ödeme başka belgeye yeniden tahsis edilemez",
            )
    if target_receivable_charge_id is not None:
        _require_charge_currency_match(db, company_id, payment)
    customer_id = int(payment["entity_id"])
    _lock_customer(db, company_id, customer_id)
    source_order_id = (
        int(snapshot["order_id"]) if snapshot["order_id"] is not None else None
    )
    source_charge_id = (
        int(snapshot["receivable_charge_id"])
        if snapshot["receivable_charge_id"] is not None
        else None
    )
    # Deterministic lock order regardless of which side moves: payment, customer,
    # orders by (due_date, id), then charge documents by (due_date, id).
    documents = _lock_documents(db, company_id, customer_id, target_order_id)
    document_ids = [int(row["id"]) for row in documents]
    if source_order_id is not None and source_order_id not in document_ids:
        raise HTTPException(409, "Kaynak tahsis belgesi açık ve vadeli değil")
    charge_ids = sorted(
        {
            charge_id
            for charge_id in (source_charge_id, target_receivable_charge_id)
            if charge_id is not None
        }
    )
    charges: dict[int, dict[str, object]] = {}
    if target_receivable_charge_id is not None:
        for row in _lock_charge_documents(
            db, company_id, customer_id, target_receivable_charge_id
        ):
            charges[int(row["id"])] = row
    if source_charge_id is not None and source_charge_id not in charges:
        charges[source_charge_id] = _lock_charge_document_row(
            db, company_id, source_charge_id
        )
    rows = _locked_allocation_rows(
        db, company_id, payment_id, document_ids, charge_ids
    )
    original = next(
        (row for row in rows if int(row["id"]) == allocation_id),
        None,
    )
    if original is None or original["reversal_of_allocation_id"] is not None:
        raise HTTPException(422, "Yalnız normal tahsis kaydı yeniden tahsis edilebilir")
    _validate_mutation_date(
        effective_date, original["effective_date"], closed_through
    )
    current_net = _original_net_amount(rows, allocation_id)
    if requested > current_net:
        raise HTTPException(409, "Yeniden tahsis tutarı aktif tahsisi aşamaz")

    reversal_id = _insert_reversal(
        db,
        company_id,
        original,
        requested,
        effective_date,
        created_by,
        note,
    )
    source_net = money(current_net - requested)
    if source_net == ZERO_MONEY:
        db.execute(
            text(
                """UPDATE payment_allocations
                SET reversed_at=:reversed_at,reversed_by=:reversed_by,
                    reversal_allocation_id=:reversal_id
                WHERE id=:id AND company_id=:cid"""
            ),
            {
                "reversed_at": datetime.now(timezone.utc),
                "reversed_by": created_by,
                "reversal_id": reversal_id,
                "id": allocation_id,
                "cid": company_id,
            },
        )
    if target_order_id is not None:
        target_remaining = _fifo_adjusted_remaining(
            db, company_id, customer_id, documents
        ).get(target_order_id, ZERO_MONEY)
        if target_remaining < requested:
            raise HTTPException(
                409, "Yeniden tahsis gerçek hedef belge kalanını aşıyor"
            )
    else:
        target_remaining = _charge_remaining(
            db, company_id, charges[target_receivable_charge_id]
        )
        if target_remaining < requested:
            raise HTTPException(409, "Yeniden tahsis hedef tahakkuk kalanını aşıyor")
    new_allocation_id = int(
        db.execute(
            text(
                """INSERT INTO payment_allocations(
                    company_id,payment_id,order_id,receivable_charge_id,amount,
                    allocation_type,effective_date,created_by,note
                ) VALUES(
                    :cid,:payment_id,:order_id,:charge_id,:amount,'manual',
                    :effective_date,:created_by,:note
                ) RETURNING id"""
            ),
            {
                "cid": company_id,
                "payment_id": payment_id,
                "order_id": target_order_id,
                "charge_id": target_receivable_charge_id,
                "amount": requested,
                "effective_date": effective_date,
                "created_by": created_by,
                "note": note,
            },
        ).scalar_one()
    )
    _lock_finance_rows(db, company_id, payment)
    if payment["reference_type"] == "order":
        _materialize_direct_paid_amount(
            db,
            company_id,
            [
                order_id
                for order_id in (source_order_id, target_order_id)
                if order_id is not None
            ],
        )
    result = AllocationMutationResult(
        operation="reallocation",
        payment_id=payment_id,
        source_allocation_id=allocation_id,
        reversal_allocation_id=reversal_id,
        allocation_id=new_allocation_id,
        order_id=target_order_id,
        receivable_charge_id=target_receivable_charge_id,
        amount=requested,
        net_amount=requested,
    )
    _record_allocation_audit(
        db,
        company_id,
        payment_id,
        created_by,
        result.snapshot(),
        action="reallocate",
    )
    _complete_operation_idempotency(db, company_id, claim_id, result)
    if activity_hook is not None:
        activity_hook(db, result)
    db.commit()
    return result


def manual_allocate_payment(
    db: Session,
    company_id: int,
    payment_id: int,
    order_id: int | None,
    amount: Decimal,
    effective_date: date,
    idempotency_key: str,
    *,
    created_by: int | None = None,
    note: str | None = None,
    receivable_charge_id: int | None = None,
    activity_hook: Callable[[Session, AllocationMutationResult], None] | None = None,
) -> AllocationMutationResult:
    try:
        return _manual_allocate_payment(
            db,
            company_id,
            payment_id,
            order_id,
            amount,
            effective_date,
            idempotency_key,
            created_by=created_by,
            note=note,
            receivable_charge_id=receivable_charge_id,
            activity_hook=activity_hook,
        )
    except Exception:
        db.rollback()
        raise


def reverse_allocation(
    db: Session,
    company_id: int,
    allocation_id: int,
    amount: Decimal,
    effective_date: date,
    idempotency_key: str,
    *,
    created_by: int | None = None,
    note: str | None = None,
    closed_through: date | None = None,
    activity_hook: Callable[[Session, AllocationMutationResult], None] | None = None,
) -> AllocationMutationResult:
    try:
        return _reverse_allocation(
            db,
            company_id,
            allocation_id,
            amount,
            effective_date,
            idempotency_key,
            created_by=created_by,
            note=note,
            closed_through=closed_through,
            activity_hook=activity_hook,
        )
    except Exception:
        db.rollback()
        raise


def reallocate_allocation(
    db: Session,
    company_id: int,
    allocation_id: int,
    target_order_id: int | None,
    amount: Decimal,
    effective_date: date,
    idempotency_key: str,
    *,
    created_by: int | None = None,
    note: str | None = None,
    closed_through: date | None = None,
    target_receivable_charge_id: int | None = None,
    activity_hook: Callable[[Session, AllocationMutationResult], None] | None = None,
) -> AllocationMutationResult:
    try:
        return _reallocate_allocation(
            db,
            company_id,
            allocation_id,
            target_order_id,
            amount,
            effective_date,
            idempotency_key,
            created_by=created_by,
            note=note,
            closed_through=closed_through,
            target_receivable_charge_id=target_receivable_charge_id,
            activity_hook=activity_hook,
        )
    except Exception:
        db.rollback()
        raise


def list_payment_allocations(
    db: Session,
    company_id: int,
    *,
    payment_id: int | None = None,
    order_id: int | None = None,
    as_of: date | None = None,
    receivable_charge_id: int | None = None,
) -> list[dict[str, object]]:
    selectors = [payment_id, order_id, receivable_charge_id]
    if sum(1 for value in selectors if value is not None) != 1:
        raise ValueError(
            "payment_id, order_id veya receivable_charge_id alanlarından "
            "tam biri zorunludur"
        )
    if payment_id is not None:
        exists = db.execute(
            text("SELECT 1 FROM payments WHERE id=:id AND company_id=:cid"),
            {"id": payment_id, "cid": company_id},
        ).first()
        column = "payment_id"
        resource_id = payment_id
    elif order_id is not None:
        exists = db.execute(
            text("SELECT 1 FROM orders WHERE id=:id AND company_id=:cid"),
            {"id": order_id, "cid": company_id},
        ).first()
        column = "order_id"
        resource_id = order_id
    else:
        exists = db.execute(
            text(
                "SELECT 1 FROM receivable_charge_documents "
                "WHERE id=:id AND company_id=:cid"
            ),
            {"id": receivable_charge_id, "cid": company_id},
        ).first()
        column = "receivable_charge_id"
        resource_id = receivable_charge_id
    if not exists:
        raise HTTPException(404, "Tahsis kaynağı bulunamadı")
    rows = [
        dict(row)
        for row in db.execute(
            text(
                f"""SELECT id,payment_id,order_id,receivable_charge_id,amount,
                allocation_type,effective_date,recorded_at,created_by,reversed_at,
                reversed_by,reversal_allocation_id,reversal_of_allocation_id,note
                FROM payment_allocations
                WHERE company_id=:cid AND {column}=:resource_id
                ORDER BY effective_date,id"""
            ),
            {"cid": company_id, "resource_id": resource_id},
        ).mappings()
    ]
    cutoff = as_of or business_today()
    reversal_totals: dict[int, Decimal] = {}
    for row in rows:
        original_id = row["reversal_of_allocation_id"]
        if (
            original_id is not None
            and parse_receivable_date(row["effective_date"]) <= cutoff
        ):
            reversal_totals[int(original_id)] = money(
                reversal_totals.get(int(original_id), ZERO_MONEY)
                + money(row["amount"])
            )
    result: list[dict[str, object]] = []
    for row in rows:
        row["amount"] = money(row["amount"])
        original_id = row["reversal_of_allocation_id"]
        is_effective = parse_receivable_date(row["effective_date"]) <= cutoff
        if not is_effective:
            row["net_amount"] = ZERO_MONEY
            row["status"] = "not_effective"
            result.append(row)
            continue
        if original_id is not None:
            row["net_amount"] = money(-money(row["amount"]))
            row["status"] = "reversal"
        else:
            net_amount = money(
                money(row["amount"]) - reversal_totals.get(int(row["id"]), ZERO_MONEY)
            )
            if net_amount < ZERO_MONEY:
                raise HTTPException(409, "Reversal toplamı tahsis tutarını aşıyor")
            row["net_amount"] = net_amount
            row["status"] = (
                "reversed"
                if net_amount == ZERO_MONEY
                else "partial"
                if net_amount < money(row["amount"])
                else "active"
            )
        result.append(row)
    return result


def _run_mutation_with_retry(
    session_factory: Callable[[], Session],
    operation: Callable[[Session], AllocationMutationResult],
) -> AllocationMutationResult:
    for attempt in range(MAX_TRANSACTION_ATTEMPTS):
        with session_factory() as db:
            try:
                return operation(db)
            except DBAPIError as exc:
                db.rollback()
                sqlstate = getattr(exc.orig, "sqlstate", None)
                if (
                    sqlstate not in RETRYABLE_SQLSTATES
                    or attempt + 1 >= MAX_TRANSACTION_ATTEMPTS
                ):
                    raise
        time.sleep(random.uniform(0.01, 0.05) * (attempt + 1))
    raise RuntimeError("Tahsis mutasyonu retry döngüsü sonuç üretmedi")


def manual_allocate_payment_with_retry(
    session_factory: Callable[[], Session],
    company_id: int,
    payment_id: int,
    order_id: int | None,
    amount: Decimal,
    effective_date: date,
    idempotency_key: str,
    *,
    created_by: int | None = None,
    note: str | None = None,
    receivable_charge_id: int | None = None,
    activity_hook: Callable[[Session, AllocationMutationResult], None] | None = None,
) -> AllocationMutationResult:
    return _run_mutation_with_retry(
        session_factory,
        lambda db: manual_allocate_payment(
            db,
            company_id,
            payment_id,
            order_id,
            amount,
            effective_date,
            idempotency_key,
            created_by=created_by,
            note=note,
            receivable_charge_id=receivable_charge_id,
            activity_hook=activity_hook,
        ),
    )


def reverse_allocation_with_retry(
    session_factory: Callable[[], Session],
    company_id: int,
    allocation_id: int,
    amount: Decimal,
    effective_date: date,
    idempotency_key: str,
    *,
    created_by: int | None = None,
    note: str | None = None,
    closed_through: date | None = None,
    activity_hook: Callable[[Session, AllocationMutationResult], None] | None = None,
) -> AllocationMutationResult:
    return _run_mutation_with_retry(
        session_factory,
        lambda db: reverse_allocation(
            db,
            company_id,
            allocation_id,
            amount,
            effective_date,
            idempotency_key,
            created_by=created_by,
            note=note,
            closed_through=closed_through,
            activity_hook=activity_hook,
        ),
    )


def reallocate_allocation_with_retry(
    session_factory: Callable[[], Session],
    company_id: int,
    allocation_id: int,
    target_order_id: int | None,
    amount: Decimal,
    effective_date: date,
    idempotency_key: str,
    *,
    created_by: int | None = None,
    note: str | None = None,
    closed_through: date | None = None,
    target_receivable_charge_id: int | None = None,
    activity_hook: Callable[[Session, AllocationMutationResult], None] | None = None,
) -> AllocationMutationResult:
    return _run_mutation_with_retry(
        session_factory,
        lambda db: reallocate_allocation(
            db,
            company_id,
            allocation_id,
            target_order_id,
            amount,
            effective_date,
            idempotency_key,
            created_by=created_by,
            note=note,
            closed_through=closed_through,
            target_receivable_charge_id=target_receivable_charge_id,
            activity_hook=activity_hook,
        ),
    )


def allocate_payment_with_retry(
    session_factory: Callable[[], Session],
    company_id: int,
    payment_id: int,
    idempotency_key: str,
    *,
    created_by: int | None = None,
) -> AllocationResult:
    for attempt in range(MAX_TRANSACTION_ATTEMPTS):
        with session_factory() as db:
            try:
                return allocate_payment(
                    db,
                    company_id,
                    payment_id,
                    idempotency_key,
                    created_by=created_by,
                )
            except DBAPIError as exc:
                db.rollback()
                sqlstate = getattr(exc.orig, "sqlstate", None)
                if sqlstate not in RETRYABLE_SQLSTATES or attempt + 1 >= MAX_TRANSACTION_ATTEMPTS:
                    raise
        time.sleep(random.uniform(0.01, 0.05) * (attempt + 1))
    raise RuntimeError("Tahsis retry döngüsü sonuç üretmedi")
