from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..change_history import record_change
from ..db import get_db

# ``_rule_scope`` is the resolver's own specificity ranking. It is imported
# rather than re-implemented so the management screen can never drift from the
# precedence the engine actually applies. The resolver itself is not modified.
from ..harvest_scheduling import (
    _category_key,
    _matching_rule,
    _rule_scope,
    resolve_harvest_due_date,
)
from ..harvest_scheduling_schemas import (
    CustomerHarvestRegionWrite,
    HarvestCalendarView,
    HarvestCalendarWrite,
    HarvestDuePreviewView,
    HarvestDueRuleView,
    HarvestDueRuleWrite,
    HarvestRegionView,
    HarvestRegionWrite,
    HarvestRuleConflictView,
)
from ..tenancy import company_id

router = APIRouter(prefix="/harvest-scheduling", tags=["harvest-scheduling"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _region_row(db: Session, cid: int, region_id: int) -> dict:
    row = db.execute(
        text(
            "SELECT id,company_id,code,name,active FROM harvest_regions "
            "WHERE id=:id AND company_id=:cid"
        ),
        {"id": region_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Hasat bölgesi bulunamadı")
    return dict(row)


def _calendar_row(db: Session, cid: int, calendar_id: int) -> dict:
    row = db.execute(
        text(
            """SELECT id,company_id,season_code,name,season_year,region_id,
            product_category,due_date,active,created_at,updated_at
            FROM harvest_calendars WHERE id=:id AND company_id=:cid"""
        ),
        {"id": calendar_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Sezon takvimi bulunamadı")
    return dict(row)


def _rule_row(db: Session, cid: int, rule_id: int) -> dict:
    row = db.execute(
        text(
            """SELECT id,company_id,customer_id,region_id,product_category,
            season_code,priority,effective_from,effective_to,active
            FROM harvest_due_rules WHERE id=:id AND company_id=:cid"""
        ),
        {"id": rule_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Harman vade kuralı bulunamadı")
    return _with_scope(dict(row))


def _with_scope(rule: dict) -> dict:
    """Attach the resolver's specificity tier to a rule row (read-only)."""
    return {**rule, "scope": _rule_scope(rule)}


# ---------------------------------------------------------------------------
# Rule conflict analysis.
#
# A pair of rules "conflicts" when a real HARMAN_VADELI sale exists that the
# engine refuses to rank (409). Deciding that correctly needs the engine's own
# semantics -- a NULL filter matches EVERY value, so a category-less rule and a
# ``Tohum`` rule at the same scope really do collide in a Tohum sale -- so the
# ranking is delegated to the resolver's ``_matching_rule`` instead of being
# re-derived here. Only the validity-window filter, which lives in the
# resolver's SQL rather than in a reusable function, is applied locally.
#
# Whether a tie is masked depends on WHEN the sale happens: a temporary
# higher-priority rule can rank the pair today and expire next month. So the
# pair's common period is sliced at every point the live rule set can change
# and each slice is probed independently.
# ---------------------------------------------------------------------------

# Probe date for rules whose validity window is open on the left. Any date not
# after every ``effective_to`` works; the engine treats NULL bounds as open.
_OPEN_WINDOW_PROBE = date(1900, 1, 1)
# Stands in for "a customer no customer-specific rule names", so generic rules
# are compared in the context where they actually decide the outcome.
_ANY_CUSTOMER = 0


def _as_date(value: object) -> date | None:
    """Normalize a validity bound (SQLite yields str, PostgreSQL yields date)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _is_active(rule: dict) -> bool:
    # Mirrors the resolver's ``COALESCE(active,TRUE)=TRUE``.
    return rule["active"] is None or bool(rule["active"])


def _live_on(rule: dict, moment: date) -> bool:
    """Same window test as the resolver's rule query."""
    start = _as_date(rule["effective_from"])
    end = _as_date(rule["effective_to"])
    if start is not None and start > moment:
        return False
    if end is not None and end < moment:
        return False
    return True


def _next_day(value: date | None) -> date | None:
    """Day after a validity bound, or ``None`` when there is no such day.

    ``effective_to = 9999-12-31`` is an accepted bound and means the rule never
    stops applying, exactly like a NULL bound. There is no day after it -- and
    ``date.max + 1`` raises ``OverflowError`` -- so it yields no boundary and
    the caller treats the rule as open-ended.
    """
    if value is None or value >= date.max:
        return None
    return value + timedelta(days=1)


def _pair_filters(first: dict, second: dict) -> dict | None:
    """Customer/region/category context in which both rules can match.

    Returns ``None`` when no sale can satisfy both filter sets -- different
    customers, regions or categories. Those pairs are never seen together by
    the engine, so they are not conflicts. A NULL filter matches everything,
    which is why the intersection is taken rather than an equality test.
    """
    for column in ("customer_id", "region_id"):
        left, right = first[column], second[column]
        if left is not None and right is not None and int(left) != int(right):
            return None
    left_category = _category_key(first["product_category"])
    right_category = _category_key(second["product_category"])
    if (
        left_category is not None
        and right_category is not None
        and left_category != right_category
    ):
        return None

    customer_id = (
        first["customer_id"] if first["customer_id"] is not None else second["customer_id"]
    )
    region_id = (
        first["region_id"] if first["region_id"] is not None else second["region_id"]
    )
    return {
        "customer_id": int(customer_id) if customer_id is not None else _ANY_CUSTOMER,
        "region_id": int(region_id) if region_id is not None else None,
        "category": left_category if left_category is not None else right_category,
        "named_customer": customer_id,
    }


def _common_window(first: dict, second: dict) -> tuple[date, date | None] | None:
    """Period during which both rules are simultaneously live, or ``None``."""
    starts = [
        value
        for value in (
            _as_date(first["effective_from"]),
            _as_date(second["effective_from"]),
        )
        if value is not None
    ]
    ends = [
        value
        for value in (
            _as_date(first["effective_to"]),
            _as_date(second["effective_to"]),
        )
        if value is not None
    ]
    start = max(starts) if starts else _OPEN_WINDOW_PROBE
    end = min(ends) if ends else None
    if end is not None and start > end:
        return None
    return start, end


def _probe_dates(
    window: tuple[date, date | None],
    candidates: list[dict],
) -> list[date]:
    """One sample date per time slice of the pair's common period.

    The live rule set only changes when some rule starts or stops applying, so
    slicing at every ``effective_from`` and every ``effective_to + 1 day`` that
    falls inside the window yields one representative date per distinct set. A
    temporary high-priority rule that masks a tie until it expires is caught by
    the slice that begins the day after it ends.

    Boundaries are collected from every candidate rather than only the ones
    whose filters match the context: a superset costs a few extra probes but
    avoids re-deriving the resolver's matching predicate here.
    """
    start, end = window
    probes = {start}
    for rule in candidates:
        for boundary in (
            _as_date(rule["effective_from"]),
            _next_day(_as_date(rule["effective_to"])),
        ):
            if boundary is None or boundary <= start:
                continue
            if end is not None and boundary > end:
                continue
            probes.add(boundary)
    return sorted(probes)


def _engine_refuses(candidates: list[dict], context: dict) -> bool:
    """True when the resolver cannot rank these candidates in this context."""
    try:
        # ``_matching_rule`` writes a ``_scope`` key, so hand it copies.
        _matching_rule(
            [dict(rule) for rule in candidates],
            context["customer_id"],
            context["region_id"],
            context["category"],
        )
    except HTTPException as exc:
        return exc.status_code == 409
    return False


def _rule_conflicts(rules: list[dict]) -> list[dict]:
    """Every rule pair a real sale would hit a 409 on."""
    active = [rule for rule in rules if _is_active(rule)]
    conflicts: list[dict] = []
    for index, first in enumerate(active):
        for second in active[index + 1 :]:
            context = _pair_filters(first, second)
            if context is None:
                continue
            window = _common_window(first, second)
            if window is None:
                continue
            # The pair must tie with each other. Scope and priority do not vary
            # with the sale date, so this is checked once for the whole window.
            if not _engine_refuses([first, second], context):
                continue
            # ...and the full live rule set must still be unrankable in at
            # least one slice. A more specific or higher-priority rule can mask
            # the tie for part of the window and then expire, so every slice is
            # probed rather than just the window start.
            hit = next(
                (
                    probe
                    for probe in _probe_dates(window, active)
                    if _engine_refuses(
                        [rule for rule in active if _live_on(rule, probe)], context
                    )
                ),
                None,
            )
            if hit is None:
                continue
            conflicts.append(
                {
                    "rule_ids": sorted((int(first["id"]), int(second["id"]))),
                    "scope": _rule_scope(first),
                    "priority": int(first["priority"]),
                    "customer_id": context["named_customer"],
                    "region_id": context["region_id"],
                    "product_category": context["category"],
                    "sample_date": hit,
                    "detail": (
                        "Aynı öncelikte birden fazla harman vade kuralı eşleşti"
                    ),
                }
            )
    return conflicts


def _validate_region(db: Session, cid: int, region_id: int | None) -> None:
    if region_id is not None:
        _region_row(db, cid, region_id)


def _validate_customer(db: Session, cid: int, customer_id: int | None) -> None:
    if customer_id is None:
        return
    exists = db.execute(
        text(
            "SELECT 1 FROM customers WHERE id=:id AND company_id=:cid"
        ),
        {"id": customer_id, "cid": cid},
    ).first()
    if not exists:
        raise HTTPException(404, "Müşteri bulunamadı")


@router.put("/customers/{customer_id}/region")
def set_customer_region(
    customer_id: int,
    payload: CustomerHarvestRegionWrite,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    _validate_customer(db, cid, customer_id)
    _validate_region(db, cid, payload.region_id)
    before = db.execute(
        text(
            "SELECT id,company_id,harvest_region_id FROM customers "
            "WHERE id=:id AND company_id=:cid"
        ),
        {"id": customer_id, "cid": cid},
    ).mappings().one()
    db.execute(
        text(
            "UPDATE customers SET harvest_region_id=:region_id "
            "WHERE id=:id AND company_id=:cid"
        ),
        {"region_id": payload.region_id, "id": customer_id, "cid": cid},
    )
    after = db.execute(
        text(
            "SELECT id,company_id,harvest_region_id FROM customers "
            "WHERE id=:id AND company_id=:cid"
        ),
        {"id": customer_id, "cid": cid},
    ).mappings().one()
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="customer",
        entity_id=customer_id,
        action="update",
        before=before,
        after=after,
    )
    db.commit()
    return {
        "customer_id": customer_id,
        "harvest_region_id": payload.region_id,
    }


@router.get("/regions", response_model=list[HarvestRegionView])
def list_regions(request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    return [
        dict(row)
        for row in db.execute(
            text(
                """SELECT id,company_id,code,name,active
                FROM harvest_regions WHERE company_id=:cid
                ORDER BY active DESC,name,id"""
            ),
            {"cid": cid},
        ).mappings()
    ]


@router.post("/regions", status_code=201, response_model=HarvestRegionView)
def create_region(
    payload: HarvestRegionWrite,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    try:
        region_id = int(
            db.execute(
                text(
                    """INSERT INTO harvest_regions(company_id,code,name,active)
                    VALUES(:cid,:code,:name,:active) RETURNING id"""
                ),
                {"cid": cid, **payload.model_dump()},
            ).scalar_one()
        )
        after = _region_row(db, cid, region_id)
        record_change(
            db,
            request,
            company_id=cid,
            entity_type="harvest_region",
            entity_id=region_id,
            action="create",
            before=None,
            after=after,
        )
        db.commit()
        return after
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Bu bölge kodu zaten kullanılıyor") from exc


@router.put("/regions/{region_id}", response_model=HarvestRegionView)
def update_region(
    region_id: int,
    payload: HarvestRegionWrite,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    before = _region_row(db, cid, region_id)
    try:
        db.execute(
            text(
                """UPDATE harvest_regions SET code=:code,name=:name,active=:active
                WHERE id=:id AND company_id=:cid"""
            ),
            {"id": region_id, "cid": cid, **payload.model_dump()},
        )
        after = _region_row(db, cid, region_id)
        record_change(
            db,
            request,
            company_id=cid,
            entity_type="harvest_region",
            entity_id=region_id,
            action="update",
            before=before,
            after=after,
        )
        db.commit()
        return after
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Bu bölge kodu zaten kullanılıyor") from exc


@router.delete("/regions/{region_id}", status_code=204)
def delete_region(
    region_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    before = _region_row(db, cid, region_id)
    try:
        db.execute(
            text(
                "DELETE FROM harvest_regions WHERE id=:id AND company_id=:cid"
            ),
            {"id": region_id, "cid": cid},
        )
        record_change(
            db,
            request,
            company_id=cid,
            entity_type="harvest_region",
            entity_id=region_id,
            action="delete",
            before=before,
            after=None,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Kullanılan hasat bölgesi silinemez") from exc


@router.get("/calendars", response_model=list[HarvestCalendarView])
def list_calendars(request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    return [
        dict(row)
        for row in db.execute(
            text(
                """SELECT id,company_id,season_code,name,season_year,region_id,
                product_category,due_date,active,created_at,updated_at
                FROM harvest_calendars WHERE company_id=:cid
                ORDER BY due_date,season_code,id"""
            ),
            {"cid": cid},
        ).mappings()
    ]


@router.post("/calendars", status_code=201, response_model=HarvestCalendarView)
def create_calendar(
    payload: HarvestCalendarWrite,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    _validate_region(db, cid, payload.region_id)
    now = _now()
    calendar_id = int(
        db.execute(
            text(
                """INSERT INTO harvest_calendars(
                company_id,season_code,name,season_year,region_id,
                product_category,due_date,active,created_at,updated_at)
                VALUES(:cid,:season_code,:name,:season_year,:region_id,
                :product_category,:due_date,:active,:now,:now) RETURNING id"""
            ),
            {"cid": cid, **payload.model_dump(), "now": now},
        ).scalar_one()
    )
    after = _calendar_row(db, cid, calendar_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="harvest_calendar",
        entity_id=calendar_id,
        action="create",
        before=None,
        after=after,
    )
    db.commit()
    return after


@router.put("/calendars/{calendar_id}", response_model=HarvestCalendarView)
def update_calendar(
    calendar_id: int,
    payload: HarvestCalendarWrite,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    before = _calendar_row(db, cid, calendar_id)
    _validate_region(db, cid, payload.region_id)
    db.execute(
        text(
            """UPDATE harvest_calendars SET season_code=:season_code,name=:name,
            season_year=:season_year,region_id=:region_id,
            product_category=:product_category,due_date=:due_date,
            active=:active,updated_at=:now
            WHERE id=:id AND company_id=:cid"""
        ),
        {
            "id": calendar_id,
            "cid": cid,
            **payload.model_dump(),
            "now": _now(),
        },
    )
    after = _calendar_row(db, cid, calendar_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="harvest_calendar",
        entity_id=calendar_id,
        action="update",
        before=before,
        after=after,
    )
    db.commit()
    return after


@router.delete("/calendars/{calendar_id}", status_code=204)
def delete_calendar(
    calendar_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    before = _calendar_row(db, cid, calendar_id)
    try:
        db.execute(
            text(
                "DELETE FROM harvest_calendars WHERE id=:id AND company_id=:cid"
            ),
            {"id": calendar_id, "cid": cid},
        )
        record_change(
            db,
            request,
            company_id=cid,
            entity_type="harvest_calendar",
            entity_id=calendar_id,
            action="delete",
            before=before,
            after=None,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Satışta kullanılan sezon takvimi silinemez") from exc


@router.get("/preview", response_model=HarvestDuePreviewView)
def preview_due_date(
    request: Request,
    customer_id: int = Query(gt=0),
    transaction_date: date = Query(),
    product_ids: list[int] = Query(min_length=1),
    calendar_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
):
    """Dry-run the harvest due-date resolution for a customer/product/date.

    Read-only: the engine only issues SELECTs, and nothing is committed. The
    engine's own fail-closed errors (404/409/422) are propagated unchanged so
    the management screen shows exactly what a real order would hit.
    """
    cid = company_id(request)
    _validate_customer(db, cid, customer_id)
    if any(product_id <= 0 for product_id in product_ids):
        raise HTTPException(422, "Geçersiz ürün seçimi")
    resolution = resolve_harvest_due_date(
        db,
        company_id=cid,
        customer_id=customer_id,
        transaction_date=transaction_date,
        product_ids=product_ids,
        explicit_calendar_id=calendar_id,
    )
    snapshot = json.loads(resolution.snapshot)
    calendar = _calendar_row(db, cid, resolution.calendar_id)
    return {
        "due_date": resolution.due_date,
        "calendar_id": resolution.calendar_id,
        "season_code": calendar["season_code"],
        "season_name": calendar["name"],
        "season_year": calendar["season_year"],
        "mode": snapshot["mode"],
        "customer_id": customer_id,
        "customer_region_id": snapshot["customer_region_id"],
        "transaction_date": transaction_date,
        "product_categories": snapshot["product_categories"],
        "rule_ids": snapshot["rule_ids"],
    }


def _all_rules(db: Session, cid: int) -> list[dict]:
    return [
        dict(row)
        for row in db.execute(
            text(
                """SELECT id,company_id,customer_id,region_id,product_category,
                season_code,priority,effective_from,effective_to,active
                FROM harvest_due_rules WHERE company_id=:cid
                ORDER BY priority DESC,id"""
            ),
            {"cid": cid},
        ).mappings()
    ]


@router.get("/rules/conflicts", response_model=list[HarvestRuleConflictView])
def rule_conflicts(request: Request, db: Session = Depends(get_db)):
    """Rule pairs the engine cannot rank, computed with the engine's own logic.

    Read-only. The management screen renders this verbatim instead of guessing
    at the precedence rules client-side.
    """
    return _rule_conflicts(_all_rules(db, company_id(request)))


@router.get("/rules", response_model=list[HarvestDueRuleView])
def list_rules(request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    return [
        _with_scope(dict(row))
        for row in db.execute(
            text(
                """SELECT id,company_id,customer_id,region_id,product_category,
                season_code,priority,effective_from,effective_to,active
                FROM harvest_due_rules WHERE company_id=:cid
                ORDER BY priority DESC,id"""
            ),
            {"cid": cid},
        ).mappings()
    ]


@router.post("/rules", status_code=201, response_model=HarvestDueRuleView)
def create_rule(
    payload: HarvestDueRuleWrite,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    _validate_customer(db, cid, payload.customer_id)
    _validate_region(db, cid, payload.region_id)
    rule_id = int(
        db.execute(
            text(
                """INSERT INTO harvest_due_rules(
                company_id,customer_id,region_id,product_category,season_code,
                priority,effective_from,effective_to,active)
                VALUES(:cid,:customer_id,:region_id,:product_category,:season_code,
                :priority,:effective_from,:effective_to,:active) RETURNING id"""
            ),
            {"cid": cid, **payload.model_dump()},
        ).scalar_one()
    )
    after = _rule_row(db, cid, rule_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="harvest_due_rule",
        entity_id=rule_id,
        action="create",
        before=None,
        after=after,
    )
    db.commit()
    return after


@router.put("/rules/{rule_id}", response_model=HarvestDueRuleView)
def update_rule(
    rule_id: int,
    payload: HarvestDueRuleWrite,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    before = _rule_row(db, cid, rule_id)
    _validate_customer(db, cid, payload.customer_id)
    _validate_region(db, cid, payload.region_id)
    db.execute(
        text(
            """UPDATE harvest_due_rules SET customer_id=:customer_id,
            region_id=:region_id,product_category=:product_category,
            season_code=:season_code,priority=:priority,
            effective_from=:effective_from,effective_to=:effective_to,active=:active
            WHERE id=:id AND company_id=:cid"""
        ),
        {"id": rule_id, "cid": cid, **payload.model_dump()},
    )
    after = _rule_row(db, cid, rule_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="harvest_due_rule",
        entity_id=rule_id,
        action="update",
        before=before,
        after=after,
    )
    db.commit()
    return after


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    before = _rule_row(db, cid, rule_id)
    db.execute(
        text(
            "DELETE FROM harvest_due_rules WHERE id=:id AND company_id=:cid"
        ),
        {"id": rule_id, "cid": cid},
    )
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="harvest_due_rule",
        entity_id=rule_id,
        action="delete",
        before=before,
        after=None,
    )
    db.commit()
