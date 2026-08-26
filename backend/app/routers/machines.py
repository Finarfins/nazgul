"""V3.0 Machine Cards API.

Every statement is scoped by the request's active ``company_id`` so a machine or
customer from another tenant is never visible or assignable. Chassis/serial
uniqueness is enforced both by an application pre-check (clear 409) and by the
partial-unique indexes in :mod:`app.machines` (race-safe backstop). There is no
DELETE endpoint; machines are deactivated instead.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..change_history import record_change
from ..db import get_db
from ..schemas import MachineCreate, MachineStatusUpdate, MachineUpdate
from ..tenancy import company_id

router = APIRouter(prefix="/machines", tags=["machines"])

# ---------------------------------------------------------------------------
# LOCK ORDER: ``machines`` first, then ``work_orders`` — without exception.
#
# A machine's owner (``machines.customer_id``) is read-for-write by the
# work-order paths (a work order's customer must equal its machine's owner) and
# rewritten by the ownership paths (transfer endpoint, owner-changing card PUT).
# Both families touch the same two tables, so they must agree on one order or
# they deadlock.
#
# Every path that reads the owner in order to write, or changes the owner,
# therefore takes ``SELECT ... FOR UPDATE`` on the machines row FIRST and only
# then locks or reads ``work_orders``. No path may lock a work order before its
# machine. Paths that only touch work orders (status, parts, attachments,
# invoicing) take no machine lock at all and so cannot invert the order.
#
# Without the machine lock on the work-order side the two families interleave:
# a transfer passes its "no open work order" check, a concurrent create reads
# the *old* owner unlocked and inserts an open work order for it, and the
# transfer then commits — leaving the machine owned by B with an open work
# order billed to A.
#
# On SQLite the clause is omitted (single-writer database), matching the
# dialect gate used by the finance and payment-allocation engines.
# ---------------------------------------------------------------------------


def lock_machine_row(db: Session, cid: int, machine_id: int) -> dict:
    """Take the machine row lock that opens the protocol above.

    Returns the locked row; its ``customer_id`` is the only authority callers
    may use for the previous owner, because an unlocked re-read could observe a
    concurrent transfer's value.
    """
    lock_clause = " FOR UPDATE" if db.get_bind().dialect.name == "postgresql" else ""
    row = db.execute(
        text(
            "SELECT id, customer_id FROM machines "
            f"WHERE id=:id AND company_id=:cid{lock_clause}"
        ),
        {"id": machine_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Makine bulunamadı")
    return dict(row)

# Column order shared by INSERT/UPDATE (id/created_at are managed here, not by
# the caller). All names are constants, never user input.
_WRITE_COLUMNS = [
    "company_id",
    "customer_id",
    "brand",
    "manufacturer",
    "model",
    "variant",
    "serial_number",
    "chassis_number",
    "registration_number",
    "engine_number",
    "model_year",
    "working_hours",
    "status",
    "notes",
    "is_active",
    "created_at",
    "updated_at",
]
_INSERT_PLACEHOLDERS = ", ".join(f":{column}" for column in _WRITE_COLUMNS)
_UPDATE_ASSIGNMENTS = ", ".join(
    f"{column}=:{column}"
    for column in _WRITE_COLUMNS
    if column not in ("company_id", "created_at")
)
_SELECT_COLUMNS = (
    "m.id, m.company_id, m.customer_id, c.name AS customer_name, m.brand, "
    "m.manufacturer, m.model, m.variant, m.serial_number, m.chassis_number, "
    "m.registration_number, m.engine_number, m.model_year, m.working_hours, "
    "m.status, m.notes, m.is_active, m.created_at, m.updated_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _serialize(row) -> dict:
    # Raw text() results expose the boolean as 0/1 on SQLite and as a real bool on
    # PostgreSQL; normalise so the API always returns a JSON boolean.
    data = dict(row)
    data["is_active"] = bool(data["is_active"])
    return data


def _idempotency_key(request: Request) -> str | None:
    """Return a validated ``Idempotency-Key`` header, or None when absent."""
    key = (request.headers.get("idempotency-key") or "").strip()
    if not key:
        return None
    if len(key) > 255:
        raise HTTPException(400, "Idempotency-Key en fazla 255 karakter olabilir")
    return key


def _request_fingerprint(payload: MachineCreate) -> str:
    """A stable sha256 of the validated request body.

    Uses the normalized model (JSON mode) with sorted keys so semantically
    identical retries fingerprint identically, while a different body differs.
    """
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay_or_conflict(db: Session, cid: int, key: str, fingerprint: str):
    """Resolve a prior use of ``key`` for this company.

    Returns the previously-created machine to replay, or ``None`` when the key
    is unused. Raises 409 when the key was used with a *different* request body
    (fingerprint mismatch); a NULL stored fingerprint predates this feature and
    is treated as a match for backward compatibility.
    """
    row = db.execute(
        text(
            "SELECT machine_id, request_fingerprint FROM machine_idempotency "
            "WHERE company_id=:cid AND idempotency_key=:k"
        ),
        {"cid": cid, "k": key},
    ).first()
    if row is None:
        return None
    prior_fingerprint = row[1]
    if prior_fingerprint is not None and prior_fingerprint != fingerprint:
        raise HTTPException(
            409, "Idempotency-Key farklı bir istek gövdesiyle yeniden kullanılamaz"
        )
    return _fetch(db, cid, int(row[0]))


def _fetch(db: Session, cid: int, machine_id: int):
    return db.execute(
        text(
            f"SELECT {_SELECT_COLUMNS} FROM machines m "
            "LEFT JOIN customers c ON c.id=m.customer_id AND c.company_id=m.company_id "
            "WHERE m.id=:id AND m.company_id=:cid"
        ),
        {"id": machine_id, "cid": cid},
    ).mappings().first()


def _require_same_company_customer(db: Session, cid: int, customer_id: int | None) -> None:
    if customer_id is None:
        return
    exists = db.execute(
        text("SELECT id FROM customers WHERE id=:id AND company_id=:cid"),
        {"id": customer_id, "cid": cid},
    ).first()
    if not exists:
        # A customer from another tenant (or none) is not assignable.
        raise HTTPException(400, "Müşteri bulunamadı")


def record_ownership_change(
    db: Session,
    cid: int,
    machine_id: int,
    *,
    from_customer_id: int | None,
    to_customer_id: int,
    transfer_date,
    reason: str | None,
    created_by: int,
) -> None:
    """Append one ``machine_ownership_history`` row.

    Every path that assigns ``machines.customer_id`` to a customer (create,
    card update, transfer endpoint) goes through here so the ownership
    chronology has no gaps. Owner *removal* (customer_id -> NULL) is not
    representable (``to_customer_id`` is NOT NULL) and keeps the historical
    v1 behavior without a history row.
    """
    db.execute(
        text(
            """INSERT INTO machine_ownership_history(
            company_id,machine_id,from_customer_id,to_customer_id,transfer_date,
            reason,created_by,created_at
            ) VALUES(
            :cid,:machine_id,:from_customer_id,:to_customer_id,:transfer_date,
            :reason,:created_by,:now)"""
        ),
        {
            "cid": cid,
            "machine_id": machine_id,
            "from_customer_id": from_customer_id,
            "to_customer_id": to_customer_id,
            "transfer_date": transfer_date,
            "reason": reason,
            "created_by": created_by,
            "now": utcnow(),
        },
    )


def ensure_no_open_work_orders(db: Session, cid: int, machine_id: int) -> None:
    """Ownership must not change under a live work order: the work-order
    customer validation binds open orders to the current owner, so a transfer
    mid-service would make the order's customer inconsistent."""
    open_order = db.execute(
        text(
            "SELECT 1 FROM work_orders WHERE company_id=:cid AND machine_id=:id "
            "AND status NOT IN ('DELIVERED','CANCELLED') LIMIT 1"
        ),
        {"cid": cid, "id": machine_id},
    ).first()
    if open_order:
        raise HTTPException(409, "Açık iş emri varken makine sahipliği devredilemez")


def _assert_identifiers_unique(
    db: Session,
    cid: int,
    *,
    chassis_number: str | None,
    serial_number: str | None,
    exclude_id: int | None = None,
) -> None:
    checks = (
        ("chassis_number", chassis_number, "Bu şasi numarası bu firmada zaten kayıtlı"),
        ("serial_number", serial_number, "Bu seri numarası bu firmada zaten kayıtlı"),
    )
    for column, value, message in checks:
        if not value:
            continue
        sql = f"SELECT id FROM machines WHERE company_id=:cid AND {column}=:value"
        params: dict[str, object] = {"cid": cid, "value": value}
        if exclude_id is not None:
            sql += " AND id<>:exclude_id"
            params["exclude_id"] = exclude_id
        if db.execute(text(sql), params).first():
            raise HTTPException(409, message)


@router.get("")
def list_machines(
    request: Request,
    q: str = "",
    customer_id: int | None = Query(default=None, ge=1),
    active: str = "active",
    limit: int = Query(default=100, ge=1, le=500),
    # Bounded to keep deep-pagination scans cheap. Callers needing to page past
    # this should switch to keyset pagination (filter by last-seen id) — the
    # ORDER BY ... , m.id tail already gives a stable key to build on.
    offset: int = Query(default=0, ge=0, le=100_000),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    # is_active is NOT NULL, so bare TRUE/FALSE literals (valid on SQLite and
    # PostgreSQL) are enough — no boolean/integer COALESCE mismatch.
    active_sql = (
        ""
        if active == "all"
        else (" AND m.is_active = FALSE" if active == "inactive" else " AND m.is_active = TRUE")
    )
    params: dict[str, object] = {
        "cid": cid,
        "q": f"%{q.strip()}%",
        "limit": limit,
        "offset": offset,
    }
    customer_sql = ""
    if customer_id is not None:
        customer_sql = " AND m.customer_id = :customer_id"
        params["customer_id"] = customer_id
    rows = db.execute(
        text(
            f"""SELECT {_SELECT_COLUMNS}
            FROM machines m
            LEFT JOIN customers c ON c.id=m.customer_id AND c.company_id=m.company_id
            WHERE m.company_id=:cid{active_sql}{customer_sql}
              AND (LOWER(m.brand) LIKE LOWER(:q)
                   OR LOWER(m.model) LIKE LOWER(:q)
                   OR LOWER(COALESCE(m.serial_number,'')) LIKE LOWER(:q)
                   OR LOWER(COALESCE(m.chassis_number,'')) LIKE LOWER(:q)
                   OR LOWER(COALESCE(m.registration_number,'')) LIKE LOWER(:q))
            ORDER BY LOWER(m.brand), LOWER(m.model), m.id
            LIMIT :limit OFFSET :offset"""
        ),
        params,
    ).mappings().all()
    return [_serialize(row) for row in rows]


@router.get("/{machine_id}")
def machine_detail(machine_id: int, request: Request, db: Session = Depends(get_db)):
    row = _fetch(db, company_id(request), machine_id)
    if not row:
        raise HTTPException(404, "Makine bulunamadı")
    return _serialize(row)


@router.post("", status_code=201)
def create_machine(payload: MachineCreate, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    idem = _idempotency_key(request)
    fingerprint = _request_fingerprint(payload) if idem is not None else None
    if idem is not None:
        # A prior POST with this key already created the machine — replay it
        # instead of creating a duplicate, unless the body differs (409).
        replay = _replay_or_conflict(db, cid, idem, fingerprint)
        if replay is not None:
            return _serialize(replay)
    _require_same_company_customer(db, cid, payload.customer_id)
    _assert_identifiers_unique(
        db, cid, chassis_number=payload.chassis_number, serial_number=payload.serial_number
    )
    now = _now()
    values = payload.model_dump()
    values["is_active"] = bool(values["is_active"])
    values.update({"company_id": cid, "created_at": now, "updated_at": now})
    try:
        machine_id = int(
            db.execute(
                text(
                    f"INSERT INTO machines ({', '.join(_WRITE_COLUMNS)}) "
                    f"VALUES ({_INSERT_PLACEHOLDERS}) RETURNING id"
                ),
                values,
            ).scalar_one()
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Bu şasi veya seri numarası bu firmada zaten kayıtlı")
    if payload.customer_id is not None:
        # Opening ownership row so the machine's chronology starts complete
        # (the FAZ-1 migration backfilled the same row for pre-existing machines).
        record_ownership_change(
            db,
            cid,
            machine_id,
            from_customer_id=None,
            to_customer_id=payload.customer_id,
            transfer_date=utcnow(),
            reason="İlk kayıt",
            created_by=int((getattr(request.state, "user", {}) or {})["id"]),
        )
    if idem is not None:
        try:
            db.execute(
                text(
                    "INSERT INTO machine_idempotency"
                    "(company_id,idempotency_key,machine_id,created_at,request_fingerprint) "
                    "VALUES(:cid,:k,:mid,:now,:fp)"
                ),
                {"cid": cid, "k": idem, "mid": machine_id, "now": now, "fp": fingerprint},
            )
        except IntegrityError:
            # A concurrent request with the same key won the race and committed
            # its machine first; discard ours and return the established winner
            # (or 409 if that winner used a different body).
            db.rollback()
            replay = _replay_or_conflict(db, cid, idem, fingerprint)
            if replay is not None:
                return _serialize(replay)
            raise HTTPException(409, "Eşzamanlı istek çakışması; lütfen tekrar deneyin")
    after = _fetch(db, cid, machine_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="machine",
        entity_id=machine_id,
        action="create",
        before=None,
        after=after,
    )
    db.commit()
    return _serialize(after)


@router.put("/{machine_id}")
def update_machine(
    machine_id: int, payload: MachineUpdate, request: Request, db: Session = Depends(get_db)
):
    cid = company_id(request)
    # LOCK ORDER (see the module note): the machine row is locked before
    # anything reads its owner or touches work_orders, so a concurrent transfer
    # cannot make both writers observe the same previous owner and append
    # diverging history rows.
    locked = lock_machine_row(db, cid, machine_id)
    before = _fetch(db, cid, machine_id)
    if not before:
        raise HTTPException(404, "Makine bulunamadı")
    _require_same_company_customer(db, cid, payload.customer_id)
    _assert_identifiers_unique(
        db,
        cid,
        chassis_number=payload.chassis_number,
        serial_number=payload.serial_number,
        exclude_id=machine_id,
    )
    previous_owner = locked["customer_id"]
    owner_changes = (
        payload.customer_id is not None and payload.customer_id != previous_owner
    )
    if owner_changes:
        # A card edit that reassigns the owner is an ownership transfer: it gets
        # the same open-work-order guard and history row as the transfer
        # endpoint, otherwise the endpoint's guarantees could be bypassed here.
        ensure_no_open_work_orders(db, cid, machine_id)
        record_ownership_change(
            db,
            cid,
            machine_id,
            from_customer_id=int(previous_owner) if previous_owner is not None else None,
            to_customer_id=payload.customer_id,
            transfer_date=utcnow(),
            reason="Makine kartı güncellemesiyle sahiplik değişimi",
            created_by=int((getattr(request.state, "user", {}) or {})["id"]),
        )
    values = payload.model_dump()
    values["is_active"] = bool(values["is_active"])
    values.update({"id": machine_id, "cid": cid, "updated_at": _now()})
    try:
        result = db.execute(
            text(
                f"UPDATE machines SET {_UPDATE_ASSIGNMENTS} WHERE id=:id AND company_id=:cid"
            ),
            values,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Bu şasi veya seri numarası bu firmada zaten kayıtlı")
    if not result.rowcount:
        db.rollback()
        raise HTTPException(404, "Makine bulunamadı")
    after = _fetch(db, cid, machine_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="machine",
        entity_id=machine_id,
        action="update",
        before=before,
        after=after,
    )
    db.commit()
    return _serialize(after)


@router.patch("/{machine_id}/active")
def set_machine_active(
    machine_id: int,
    payload: MachineStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    before = _fetch(db, cid, machine_id)
    if not before:
        raise HTTPException(404, "Makine bulunamadı")
    db.execute(
        text(
            "UPDATE machines SET is_active=:active, updated_at=:updated_at "
            "WHERE id=:id AND company_id=:cid"
        ),
        {"active": bool(payload.is_active), "updated_at": _now(), "id": machine_id, "cid": cid},
    )
    after = _fetch(db, cid, machine_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="machine",
        entity_id=machine_id,
        action="update",
        before=before,
        after=after,
    )
    db.commit()
    return _serialize(after)
