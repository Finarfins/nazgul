from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from app.auth import required_permission


BACKEND = Path(__file__).resolve().parent


def run_harvest_season_scheduling_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_harvest_scheduling_rbac_map() -> None:
    assert (
        required_permission("GET", "/api/harvest-scheduling/calendars")
        == "finance"
    )
    assert (
        required_permission("POST", "/api/harvest-scheduling/rules")
        == "finance"
    )
    assert required_permission("POST", "/api/orders") == "sales"


def test_harvest_season_scheduling_sqlite(tmp_path: Path) -> None:
    run_harvest_season_scheduling_smoke(
        f"sqlite:///{(tmp_path / 'harvest-scheduling.db').as_posix()}"
    )


_SMOKE = r'''
import json

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.main import app


def login(client, username, password, new_password=None):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    headers = {
        "Authorization": "Bearer " + body["access_token"],
        "X-Company-ID": str(body["companies"][0]["id"]),
    }
    if new_password:
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": password, "new_password": new_password},
        )
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]
    return headers


def create_user(client, admin_headers, username, role):
    initial = "HarvestUser123!"
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": username,
            "display_name": username,
            "password": initial,
            "role": role,
        },
    )
    assert created.status_code in (200, 201), created.text
    return login(client, username, initial, "HarvestUser456!")


def create_region(client, headers, code, name):
    response = client.post(
        "/api/harvest-scheduling/regions",
        headers=headers,
        json={"code": code, "name": name, "active": True},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_calendar(
    client,
    headers,
    season_code,
    due_date,
    *,
    region_id=None,
    category=None,
):
    payload = {
        "season_code": season_code,
        "name": season_code,
        "season_year": int(due_date[:4]),
        "due_date": due_date,
        "active": True,
    }
    if region_id is not None:
        payload["region_id"] = region_id
    if category is not None:
        payload["product_category"] = category
    response = client.post(
        "/api/harvest-scheduling/calendars",
        headers=headers,
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_rule(
    client,
    headers,
    season_code,
    *,
    customer_id=None,
    region_id=None,
    category=None,
    priority=0,
):
    payload = {
        "season_code": season_code,
        "priority": priority,
        "active": True,
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
    }
    if customer_id is not None:
        payload["customer_id"] = customer_id
    if region_id is not None:
        payload["region_id"] = region_id
    if category is not None:
        payload["product_category"] = category
    response = client.post(
        "/api/harvest-scheduling/rules",
        headers=headers,
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_customer(client, headers, name, region_id=None):
    response = client.post("/api/customers", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    customer_id = response.json()["id"]
    if region_id is not None:
        assigned = client.put(
            f"/api/harvest-scheduling/customers/{customer_id}/region",
            headers=headers,
            json={"region_id": region_id},
        )
        assert assigned.status_code == 200, assigned.text
    return customer_id


def create_product(client, headers, name, category):
    response = client.post(
        "/api/products",
        headers=headers,
        json={
            "name": name,
            "category": category,
            "sale_price": "10",
            "stock": "200",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def order_payload(customer_id, product_ids, status="completed", **extra):
    payload = {
        "entity_id": customer_id,
        "transaction_date": "2026-01-15",
        "payment_term": "HARMAN_VADELI",
        "payment_method": "credit",
        "status": status,
        "items": [
            {
                "product_id": product_id,
                "quantity": "1",
                "unit_price": "10",
                "vat_rate": 20,
            }
            for product_id in product_ids
        ],
    }
    payload.update(extra)
    return payload


def create_order(client, headers, payload, expected=201):
    response = client.post("/api/orders", headers=headers, json=payload)
    assert response.status_code == expected, response.text
    return response


def document(client, headers, order_id):
    response = client.get(f"/api/orders/{order_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["document"]


with TestClient(app) as client:
    admin = login(client, "admin", "admin123", "HarvestAdmin123!")
    muhasebe = create_user(client, admin, "harvest_muhasebe", "muhasebe")
    satis = create_user(client, admin, "harvest_satis", "satis")
    rapor = create_user(client, admin, "harvest_rapor", "rapor")

    # Calendar/rule management is finance-only; normal sales keep using /orders.
    assert client.get(
        "/api/harvest-scheduling/calendars", headers=muhasebe
    ).status_code == 200
    for denied in (satis, rapor):
        assert client.get(
            "/api/harvest-scheduling/calendars", headers=denied
        ).status_code == 403
        assert client.post(
            "/api/harvest-scheduling/rules",
            headers=denied,
            json={"season_code": "DENIED"},
        ).status_code == 403

    central = create_region(client, muhasebe, "MERKEZ", "Merkez")
    other_region = create_region(client, muhasebe, "OVA", "Ova")
    category_product = create_product(client, admin, "Kategori Ürünü", "Gubre")
    other_product = create_product(client, admin, "Diğer Ürün", "Yedek")

    calendars = {
        "DEFAULT": create_calendar(client, muhasebe, "DEFAULT", "2026-11-30"),
        "REGION": create_calendar(
            client, muhasebe, "REGION", "2026-11-20", region_id=central
        ),
        "PRODUCT": create_calendar(
            client, muhasebe, "PRODUCT", "2026-10-30", category="Gubre"
        ),
        "REGION_PRODUCT": create_calendar(
            client,
            muhasebe,
            "REGION_PRODUCT",
            "2026-10-20",
            region_id=central,
            category="Gubre",
        ),
        "CUSTOMER": create_calendar(
            client, muhasebe, "CUSTOMER", "2026-09-30"
        ),
        "MIXED_OTHER": create_calendar(
            client, muhasebe, "MIXED_OTHER", "2026-08-31", category="Yedek"
        ),
        "EXPLICIT": create_calendar(
            client, muhasebe, "EXPLICIT", "2026-12-15"
        ),
        "CHANGED": create_calendar(
            client, muhasebe, "CHANGED", "2026-09-15"
        ),
        "DRAFT_CHANGED": create_calendar(
            client, muhasebe, "DRAFT_CHANGED", "2026-09-01"
        ),
    }
    create_rule(client, muhasebe, "DEFAULT")
    create_rule(client, muhasebe, "REGION", region_id=central)
    create_rule(client, muhasebe, "PRODUCT", category="Gubre")
    create_rule(
        client,
        muhasebe,
        "REGION_PRODUCT",
        region_id=central,
        category="Gubre",
    )

    # Every deterministic scope level resolves to the expected calendar.
    default_customer = create_customer(client, admin, "Default Çiftçi")
    default_order = create_order(
        client,
        satis,
        order_payload(default_customer, [other_product]),
    ).json()["id"]
    assert document(client, admin, default_order)["due_date"] == "2026-11-30"

    # Simulate a pre-invariant approved HARMAN row with NULL due_date. Editing
    # it must repair the date through the resolver instead of freezing a blank.
    with SessionLocal() as db:
        dialect = db.get_bind().dialect.name
        if dialect == "postgresql":
            db.execute(
                text(
                    "ALTER TABLE orders DROP CONSTRAINT "
                    "ck_orders_harman_due_date_required"
                )
            )
        else:
            db.execute(text("PRAGMA ignore_check_constraints=ON"))
        db.execute(
            text(
                """UPDATE orders SET due_date=NULL,harvest_calendar_id=NULL,
                due_date_source='legacy',harvest_resolution_snapshot=NULL
                WHERE id=:id AND company_id=:cid"""
            ),
            {
                "id": default_order,
                "cid": int(admin["X-Company-ID"]),
            },
        )
        if dialect != "postgresql":
            db.execute(text("PRAGMA ignore_check_constraints=OFF"))
        db.commit()

    repaired = client.put(
        f"/api/orders/{default_order}",
        headers=satis,
        json=order_payload(default_customer, [other_product]),
    )
    assert repaired.status_code == 200, repaired.text
    repaired_doc = document(client, admin, default_order)
    assert repaired_doc["due_date"] == "2026-11-30"
    assert repaired_doc["due_date_source"] == "automatic"
    assert repaired_doc["harvest_calendar_id"] == calendars["DEFAULT"]

    with SessionLocal() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(
                text(
                    """ALTER TABLE orders ADD CONSTRAINT
                    ck_orders_harman_due_date_required CHECK (
                    payment_term != 'HARMAN_VADELI' OR due_date IS NOT NULL)"""
                )
            )
            db.commit()
        try:
            db.execute(
                text(
                    "UPDATE orders SET due_date=NULL "
                    "WHERE id=:id AND company_id=:cid"
                ),
                {
                    "id": default_order,
                    "cid": int(admin["X-Company-ID"]),
                },
            )
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError("HARMAN_VADELI + NULL due_date CHECK reddetmedi")

    # Bakiye birakan PESIN bir satis da ALACAKTIR ve artik vadesi vardir:
    # musterinin anlasilmis vadesinden turetilir (belge tarihi +
    # customers.payment_term_days). Bu satir eskiden due_date'in NULL
    # KALDIGINI sart kosuyordu; o sart, alacagin yaslandirma raporundan ve
    # tahsis defterinden dusmesi demekti -- yani kusuru civiliyordu.
    #
    # ILK HARMAN_VADELI GECISININ DAVRANISI DEGISMEDI ve asil sinanan sey
    # odur (asagidaki dort iddia). Gecis, saklanan degerin NULL olmasina
    # DAYANMIYOR: _due_date_values'taki "eski vadeyi koru" kisa devresi
    # ``old_is_harman`` sartina baglidir, yani ESKI payment_term'e bakar.
    # PESIN -> HARMAN_VADELI ilk geciste old_is_harman False oldugu icin akis
    # her halukarda resolve_harvest_due_date'e duser. Ayrim NULL'dan
    # CIKARILMIYOR; acik bir sutunda (payment_term) duruyor.
    cash_payload = {
        **order_payload(default_customer, [other_product]),
        "payment_term": "PESIN",
    }
    cash_order = create_order(client, satis, cash_payload).json()["id"]
    cash_document = document(client, admin, cash_order)
    assert cash_document["due_date"] == "2026-01-15", cash_document["due_date"]
    assert cash_document["due_date_source"] == "automatic", cash_document
    converted = client.put(
        f"/api/orders/{cash_order}",
        headers=muhasebe,
        json={
            **order_payload(default_customer, [other_product]),
            "due_date_override_reason": "Harman sözleşmesine geçiş",
        },
    )
    assert converted.status_code == 200, converted.text
    converted_doc = document(client, admin, cash_order)
    assert converted_doc["payment_term"] == "HARMAN_VADELI"
    assert converted_doc["due_date"] == "2026-11-30"
    assert converted_doc["due_date_source"] == "automatic"
    assert converted_doc["harvest_calendar_id"] == calendars["DEFAULT"]

    region_customer = create_customer(client, muhasebe, "Bölge Çiftçi", central)
    region_order = create_order(
        client,
        satis,
        order_payload(region_customer, [other_product]),
    ).json()["id"]
    assert document(client, admin, region_order)["due_date"] == "2026-11-20"

    product_customer = create_customer(client, admin, "Ürün Çiftçi")
    product_order = create_order(
        client,
        satis,
        order_payload(product_customer, [category_product]),
    ).json()["id"]
    assert document(client, admin, product_order)["due_date"] == "2026-10-30"

    rp_customer = create_customer(client, muhasebe, "Bölge Ürün Çiftçi", central)
    rp_order = create_order(
        client,
        satis,
        order_payload(rp_customer, [category_product]),
    ).json()["id"]
    assert document(client, admin, rp_order)["due_date"] == "2026-10-20"

    customer = create_customer(client, admin, "Özel Çiftçi", other_region)
    customer_rule = create_rule(
        client, muhasebe, "CUSTOMER", customer_id=customer, priority=10
    )
    customer_order = create_order(
        client,
        satis,
        order_payload(customer, [category_product]),
    ).json()["id"]
    auto_doc = document(client, admin, customer_order)
    assert auto_doc["due_date"] == "2026-09-30"
    assert auto_doc["due_date_source"] == "automatic"
    assert auto_doc["harvest_calendar_id"] == calendars["CUSTOMER"]
    snapshot = json.loads(auto_doc["harvest_resolution_snapshot"])
    assert snapshot["calendar"]["id"] == calendars["CUSTOMER"]
    assert snapshot["rule_ids"] == [customer_rule]

    # Same computed scope + same explicit priority is an error even if dates match.
    conflict_customer = create_customer(client, admin, "Çakışan Çiftçi")
    create_rule(
        client, muhasebe, "CUSTOMER", customer_id=conflict_customer, priority=7
    )
    create_rule(
        client, muhasebe, "CUSTOMER", customer_id=conflict_customer, priority=7
    )
    conflict = create_order(
        client,
        satis,
        order_payload(conflict_customer, [category_product]),
        expected=409,
    )
    assert "aynı öncelikte" in conflict.text.lower()

    # Mixed product seasons require an explicit single calendar.
    mixed_customer = create_customer(client, admin, "Karma Çiftçi")
    create_rule(
        client,
        muhasebe,
        "PRODUCT",
        customer_id=mixed_customer,
        category="Gubre",
        priority=5,
    )
    create_rule(
        client,
        muhasebe,
        "MIXED_OTHER",
        customer_id=mixed_customer,
        category="Yedek",
        priority=5,
    )
    mixed_payload = order_payload(
        mixed_customer, [category_product, other_product]
    )
    mixed = create_order(client, satis, mixed_payload, expected=409)
    assert "farklı harman sezonlarına" in mixed.text.lower()
    explicit = create_order(
        client,
        satis,
        {
            **mixed_payload,
            "harvest_calendar_id": calendars["EXPLICIT"],
        },
    )
    explicit_doc = document(client, admin, explicit.json()["id"])
    assert explicit_doc["due_date"] == "2026-12-15"
    assert (
        json.loads(explicit_doc["harvest_resolution_snapshot"])["mode"]
        == "explicit_calendar"
    )

    # No matching rule/calendar fails closed.
    no_season_customer = create_customer(client, admin, "Sezonsuz Çiftçi")
    no_season_product = create_product(
        client, admin, "Sezonsuz Ürün", "Bilinmeyen"
    )
    # A customer-specific rule points at a season with no calendar, so company
    # default cannot silently hide the broken higher-precedence configuration.
    create_rule(
        client,
        muhasebe,
        "NO_CALENDAR",
        customer_id=no_season_customer,
        priority=99,
    )
    no_season = create_order(
        client,
        satis,
        order_payload(no_season_customer, [no_season_product]),
        expected=422,
    )
    assert "aktif takvim yok" in no_season.text.lower()

    # Manual due date carries actor/reason/time and a change-history audit row.
    manual_customer = create_customer(client, admin, "Manuel Çiftçi")
    manual = create_order(
        client,
        satis,
        order_payload(
            manual_customer,
            [other_product],
            due_date="2026-12-20",
            due_date_override_reason="Sözleşme tarihi",
        ),
    )
    manual_id = manual.json()["id"]
    manual_doc = document(client, admin, manual_id)
    assert manual_doc["due_date_source"] == "manual"
    assert manual_doc["due_date_override_reason"] == "Sözleşme tarihi"
    assert manual_doc["due_date_overridden_by"] is not None
    assert manual_doc["due_date_overridden_at"] is not None
    with SessionLocal() as db:
        audit = db.execute(
            text(
                """SELECT after_json FROM entity_change_logs
                WHERE company_id=:cid AND entity_type='order'
                  AND entity_id=:order_id ORDER BY id DESC LIMIT 1"""
            ),
            {
                "cid": int(admin["X-Company-ID"]),
                "order_id": manual_id,
            },
        ).scalar_one()
        assert json.loads(audit)["due_date_source"] == "manual"

    # A sales-only user cannot change an approved due date. Finance can, with reason.
    blocked_override = client.put(
        f"/api/orders/{manual_id}",
        headers=satis,
        json=order_payload(
            manual_customer,
            [other_product],
            due_date="2026-12-21",
            due_date_override_reason="Yetkisiz değişiklik",
        ),
    )
    assert blocked_override.status_code == 403, blocked_override.text
    allowed_override = client.put(
        f"/api/orders/{manual_id}",
        headers=muhasebe,
        json=order_payload(
            manual_customer,
            [other_product],
            due_date="2026-12-22",
            due_date_override_reason="Finans onayı",
        ),
    )
    assert allowed_override.status_code == 200, allowed_override.text
    assert document(client, admin, manual_id)["due_date"] == "2026-12-22"

    # Approved automatic dates freeze; draft dates are eligible for re-resolution.
    frozen_before = document(client, admin, customer_order)["due_date"]
    changed_rule_payload = {
        "customer_id": customer,
        "season_code": "CHANGED",
        "priority": 10,
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
        "active": True,
    }
    changed_rule = client.put(
        f"/api/harvest-scheduling/rules/{customer_rule}",
        headers=muhasebe,
        json=changed_rule_payload,
    )
    assert changed_rule.status_code == 200, changed_rule.text
    frozen_update = client.put(
        f"/api/orders/{customer_order}",
        headers=satis,
        json=order_payload(customer, [category_product]),
    )
    assert frozen_update.status_code == 200, frozen_update.text
    assert document(client, admin, customer_order)["due_date"] == frozen_before

    draft = create_order(
        client,
        satis,
        order_payload(customer, [category_product], status="draft"),
    )
    draft_id = draft.json()["id"]
    assert document(client, admin, draft_id)["due_date"] == "2026-09-15"
    changed_rule_payload["season_code"] = "DRAFT_CHANGED"
    changed_again = client.put(
        f"/api/harvest-scheduling/rules/{customer_rule}",
        headers=muhasebe,
        json=changed_rule_payload,
    )
    assert changed_again.status_code == 200, changed_again.text
    draft_update = client.put(
        f"/api/orders/{draft_id}",
        headers=satis,
        json=order_payload(customer, [category_product], status="draft"),
    )
    assert draft_update.status_code == 200, draft_update.text
    assert document(client, admin, draft_id)["due_date"] == "2026-09-01"

    # Tenant B cannot see or reference tenant A scheduling resources.
    company_b_response = client.post(
        "/api/companies", headers=admin, json={"name": "Hasat Tenant B"}
    )
    assert company_b_response.status_code == 201, company_b_response.text
    company_b = company_b_response.json()["id"]
    headers_b = {**admin, "X-Company-ID": str(company_b)}
    assert client.get(
        "/api/harvest-scheduling/calendars", headers=headers_b
    ).json() == []
    customer_b = create_customer(client, headers_b, "Tenant B Çiftçi")
    product_b = create_product(client, headers_b, "Tenant B Ürün", "Gubre")
    cross_calendar = create_order(
        client,
        headers_b,
        {
            **order_payload(customer_b, [product_b]),
            "harvest_calendar_id": calendars["DEFAULT"],
        },
        expected=404,
    )
    assert "takvimi bulunamadı" in cross_calendar.text.lower()
    cross_region = client.put(
        f"/api/harvest-scheduling/customers/{customer_b}/region",
        headers=headers_b,
        json={"region_id": central},
    )
    assert cross_region.status_code == 404, cross_region.text

    # The database constraint is tenant-composite too: even a direct write
    # cannot attach tenant A's customer to tenant B's rule.
    with SessionLocal() as db:
        try:
            db.execute(
                text(
                    """INSERT INTO harvest_due_rules(
                    company_id,customer_id,season_code,priority,active)
                    VALUES(:cid,:customer_id,'CROSS_TENANT',0,TRUE)"""
                ),
                {"cid": company_b, "customer_id": default_customer},
            )
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError(
                "Tenant-composite customer FK cross-company rule yazımını reddetmedi"
            )

print("HARVEST_SEASON_SCHEDULING_OK")
'''
