from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class HarvestDueResolution:
    due_date: date
    calendar_id: int
    snapshot: str


def _date_value(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise HTTPException(409, "Sezon takviminde geçersiz vade tarihi var") from exc


def _category_key(value: object) -> str | None:
    cleaned = " ".join(str(value or "").split())
    return cleaned.casefold() if cleaned else None


def _rule_scope(rule: dict[str, Any]) -> int:
    if rule["customer_id"] is not None:
        return 50
    if rule["region_id"] is not None and rule["product_category"] is not None:
        return 40
    if rule["product_category"] is not None:
        return 30
    if rule["region_id"] is not None:
        return 20
    return 10


def _product_categories(
    db: Session,
    company_id: int,
    product_ids: list[int],
) -> list[str | None]:
    unique_ids = sorted(set(product_ids))
    if not unique_ids:
        raise HTTPException(422, "Harman vadesi için en az bir ürün gereklidir")
    statement = text(
        "SELECT id,category FROM products "
        "WHERE company_id=:cid AND id IN :product_ids ORDER BY id"
    ).bindparams(bindparam("product_ids", expanding=True))
    rows = db.execute(
        statement, {"cid": company_id, "product_ids": unique_ids}
    ).mappings().all()
    if len(rows) != len(unique_ids):
        raise HTTPException(404, "Sezon çözümlemesindeki ürün bulunamadı")
    return sorted(
        {_category_key(row["category"]) for row in rows},
        key=lambda value: (value is None, value or ""),
    )


def _customer_region(
    db: Session,
    company_id: int,
    customer_id: int,
) -> int | None:
    row = db.execute(
        text(
            "SELECT harvest_region_id FROM customers "
            "WHERE id=:customer_id AND company_id=:cid"
        ),
        {"customer_id": customer_id, "cid": company_id},
    ).first()
    if row is None:
        raise HTTPException(404, "Müşteri bulunamadı")
    return int(row[0]) if row[0] is not None else None


def _calendar_candidates(
    db: Session,
    company_id: int,
    season_code: str,
    transaction_date: date,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            text(
                """SELECT id,season_code,name,season_year,region_id,
                product_category,due_date
                FROM harvest_calendars
                WHERE company_id=:cid
                  AND UPPER(season_code)=UPPER(:season_code)
                  AND COALESCE(active,TRUE)=TRUE
                  AND due_date>=:transaction_date
                ORDER BY due_date,id"""
            ),
            {
                "cid": company_id,
                "season_code": season_code,
                "transaction_date": transaction_date,
            },
        ).mappings()
    ]


def _calendar_for_rule(
    db: Session,
    company_id: int,
    rule: dict[str, Any],
    transaction_date: date,
    region_id: int | None,
    category: str | None,
) -> dict[str, Any]:
    candidates = []
    for calendar in _calendar_candidates(
        db, company_id, str(rule["season_code"]), transaction_date
    ):
        calendar_region = calendar["region_id"]
        calendar_category = _category_key(calendar["product_category"])
        if calendar_region is not None and (
            region_id is None or int(calendar_region) != region_id
        ):
            continue
        if calendar_category is not None and calendar_category != category:
            continue
        calendar["_specificity"] = int(calendar_region is not None) + int(
            calendar_category is not None
        )
        calendar["_due"] = _date_value(calendar["due_date"])
        candidates.append(calendar)
    if not candidates:
        raise HTTPException(
            422,
            f"{rule['season_code']} sezonu için satış tarihinden sonra aktif takvim yok",
        )
    first_due = min(item["_due"] for item in candidates)
    same_date = [item for item in candidates if item["_due"] == first_due]
    best_specificity = max(item["_specificity"] for item in same_date)
    winners = [
        item for item in same_date if item["_specificity"] == best_specificity
    ]
    if len(winners) != 1:
        raise HTTPException(
            409,
            "Aynı sezon ve tarihte birden fazla eşdeğer takvim bulundu",
        )
    return winners[0]


def _matching_rule(
    rules: list[dict[str, Any]],
    customer_id: int,
    region_id: int | None,
    category: str | None,
) -> dict[str, Any]:
    matching: list[dict[str, Any]] = []
    for rule in rules:
        if rule["customer_id"] is not None and int(rule["customer_id"]) != customer_id:
            continue
        if rule["region_id"] is not None and (
            region_id is None or int(rule["region_id"]) != region_id
        ):
            continue
        rule_category = _category_key(rule["product_category"])
        if rule_category is not None and rule_category != category:
            continue
        rule["_scope"] = _rule_scope(rule)
        matching.append(rule)
    if not matching:
        raise HTTPException(
            422,
            "Bu müşteri ve ürünler için uygun harman sezonu tanımlı değil",
        )
    best_scope = max(int(rule["_scope"]) for rule in matching)
    scoped = [rule for rule in matching if int(rule["_scope"]) == best_scope]
    best_priority = max(int(rule["priority"]) for rule in scoped)
    winners = [rule for rule in scoped if int(rule["priority"]) == best_priority]
    if len(winners) != 1:
        raise HTTPException(
            409,
            "Aynı öncelikte birden fazla harman vade kuralı eşleşti",
        )
    return winners[0]


def _explicit_calendar(
    db: Session,
    company_id: int,
    calendar_id: int,
    transaction_date: date,
    region_id: int | None,
    categories: list[str | None],
) -> dict[str, Any]:
    row = db.execute(
        text(
            """SELECT id,season_code,name,season_year,region_id,
            product_category,due_date
            FROM harvest_calendars
            WHERE id=:id AND company_id=:cid AND COALESCE(active,TRUE)=TRUE"""
        ),
        {"id": calendar_id, "cid": company_id},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Sezon takvimi bulunamadı")
    calendar = dict(row)
    due = _date_value(calendar["due_date"])
    if due < transaction_date:
        raise HTTPException(422, "Seçilen sezon vadesi satış tarihinden önce olamaz")
    if calendar["region_id"] is not None and (
        region_id is None or int(calendar["region_id"]) != region_id
    ):
        raise HTTPException(409, "Seçilen sezon müşterinin bölgesiyle uyuşmuyor")
    calendar_category = _category_key(calendar["product_category"])
    if calendar_category is not None and any(
        category != calendar_category for category in categories
    ):
        raise HTTPException(409, "Seçilen sezon satıştaki tüm ürünlerle uyuşmuyor")
    calendar["_due"] = due
    return calendar


def resolve_harvest_due_date(
    db: Session,
    *,
    company_id: int,
    customer_id: int,
    transaction_date: date,
    product_ids: list[int],
    explicit_calendar_id: int | None = None,
) -> HarvestDueResolution:
    region_id = _customer_region(db, company_id, customer_id)
    categories = _product_categories(db, company_id, product_ids)

    if explicit_calendar_id is not None:
        calendar = _explicit_calendar(
            db,
            company_id,
            explicit_calendar_id,
            transaction_date,
            region_id,
            categories,
        )
        snapshot = {
            "version": 1,
            "mode": "explicit_calendar",
            "customer_id": customer_id,
            "customer_region_id": region_id,
            "transaction_date": transaction_date.isoformat(),
            "product_categories": categories,
            "calendar": {
                "id": int(calendar["id"]),
                "season_code": str(calendar["season_code"]),
                "season_year": int(calendar["season_year"]),
                "due_date": calendar["_due"].isoformat(),
            },
            "rule_ids": [],
        }
        return HarvestDueResolution(
            due_date=calendar["_due"],
            calendar_id=int(calendar["id"]),
            snapshot=json.dumps(
                snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        )

    rules = [
        dict(row)
        for row in db.execute(
            text(
                """SELECT id,customer_id,region_id,product_category,
                season_code,priority
                FROM harvest_due_rules
                WHERE company_id=:cid AND COALESCE(active,TRUE)=TRUE
                  AND (effective_from IS NULL OR effective_from<=:transaction_date)
                  AND (effective_to IS NULL OR effective_to>=:transaction_date)
                ORDER BY id"""
            ),
            {"cid": company_id, "transaction_date": transaction_date},
        ).mappings()
    ]
    resolved: list[tuple[str | None, dict[str, Any], dict[str, Any]]] = []
    for category in categories:
        rule = _matching_rule(rules, customer_id, region_id, category)
        calendar = _calendar_for_rule(
            db,
            company_id,
            rule,
            transaction_date,
            region_id,
            category,
        )
        resolved.append((category, rule, calendar))

    calendar_ids = {int(item[2]["id"]) for item in resolved}
    if len(calendar_ids) != 1:
        raise HTTPException(
            409,
            "Satış kalemleri farklı harman sezonlarına düşüyor; tek sezon seçin",
        )
    calendar = resolved[0][2]
    snapshot = {
        "version": 1,
        "mode": "rule",
        "customer_id": customer_id,
        "customer_region_id": region_id,
        "transaction_date": transaction_date.isoformat(),
        "product_categories": categories,
        "calendar": {
            "id": int(calendar["id"]),
            "season_code": str(calendar["season_code"]),
            "season_year": int(calendar["season_year"]),
            "due_date": calendar["_due"].isoformat(),
        },
        "rule_ids": sorted({int(item[1]["id"]) for item in resolved}),
    }
    return HarvestDueResolution(
        due_date=calendar["_due"],
        calendar_id=int(calendar["id"]),
        snapshot=json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
    )
