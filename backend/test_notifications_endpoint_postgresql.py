"""PostgreSQL 16 twin for the ``/api/notifications`` dashboard badge endpoint.

Regression for a SQLite/PostgreSQL parity bug that reached production. The
endpoint compared two ISO-date *string* columns against ``CURRENT_DATE``::

    orders.due_date    <  CURRENT_DATE      -- varchar < date
    payments.payment_date = CURRENT_DATE    -- varchar = date

SQLite has no date type and happily compares those as text, so the whole
SQLite suite (including ``test_tenancy_notifications.py``, which asserts this
endpoint returns 200) stayed green. PostgreSQL has no varchar/date operator and
raises ``UndefinedFunction``, so every call 500'd in production while the
frontend silently swallowed it. Both comparisons must therefore be exercised
against a real PostgreSQL server.
"""
from __future__ import annotations

import os
import re
from datetime import timedelta

import pytest


@pytest.mark.postgresql
def test_notifications_endpoint_date_comparison_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ.get(
        "NOTIFICATIONS_ENDPOINT_TEST_DATABASE_URL"
    ) or os.environ.get("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip(
            "NOTIFICATIONS_ENDPOINT_TEST_DATABASE_URL or APP_TEST_DATABASE_URL "
            "is required"
        )
    assert database_url.startswith("postgresql"), (
        "the PostgreSQL twin must not run on another engine: " + database_url
    )
    monkeypatch.setenv("DATABASE_URL", database_url)

    # Import after DATABASE_URL is set so app.db binds the PG16 engine.
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.business_time import business_today
    from app.db import SessionLocal
    from app.main import app

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        assert login.status_code == 200, login.text
        body = login.json()
        company_id = int(body["companies"][0]["id"])
        headers = {
            "Authorization": "Bearer " + body["access_token"],
            "X-Company-ID": str(company_id),
        }
        # The bootstrap admin is flagged for a forced password refresh, so every
        # other endpoint answers 403 PASSWORD_CHANGE_REQUIRED until it rotates.
        changed = client.post(
            "/api/auth/change-password",
            headers=headers,
            json={
                "current_password": "admin123",
                "new_password": "NotifyParityPg123!",
            },
        )
        assert changed.status_code == 200, changed.text
        headers["Authorization"] = "Bearer " + changed.json()["access_token"]

        def counts() -> dict[str, int]:
            """First integer inside each badge title, keyed by notification type.

            The number is not always leading — the finance badge reads
            "Bugün N tahsilat/ödeme hareketi var".
            """
            response = client.get("/api/notifications", headers=headers)
            assert response.status_code == 200, response.text
            payload = response.json()
            assert "items" in payload, payload
            found: dict[str, int] = {}
            for item in payload["items"]:
                match = re.search(r"\d+", item["title"])
                if match:
                    found[item["type"]] = int(match.group())
            return found

        # The endpoint must not 500 before any seeding: on PostgreSQL the
        # varchar/date comparison raised UndefinedFunction regardless of data.
        # Counts are asserted relative to this baseline so the test tolerates
        # whatever the seed database already contains.
        baseline = counts()

        today = business_today()
        overdue_day = (today - timedelta(days=3)).isoformat()

        with SessionLocal() as db:
            customer_id = int(
                db.execute(
                    text(
                        "INSERT INTO customers (company_id, name) "
                        "VALUES (:cid, :name) RETURNING id"
                    ),
                    {"cid": company_id, "name": "Vade Bildirim Testi"},
                ).scalar_one()
            )
            # PESIN keeps this row clear of the HARMAN_VADELI due-date invariant
            # added by migration 20260726_0026.
            db.execute(
                text(
                    "INSERT INTO orders (company_id, customer_id, order_date, "
                    "due_date, payment_term, final_total) "
                    "VALUES (:cid, :customer, :order_date, :due_date, 'PESIN', 100)"
                ),
                {
                    "cid": company_id,
                    "customer": customer_id,
                    "order_date": overdue_day,
                    "due_date": overdue_day,
                },
            )
            db.execute(
                text(
                    "INSERT INTO payments (company_id, entity_type, entity_id, "
                    "amount, payment_date) "
                    "VALUES (:cid, 'customer', :customer, 50, :today)"
                ),
                {
                    "cid": company_id,
                    "customer": customer_id,
                    "today": today.isoformat(),
                },
            )
            db.commit()

        seeded = counts()

        # `due_date < today` must actually match the backdated row, proving the
        # comparison is chronologically correct and not merely non-erroring.
        assert seeded.get("due", 0) == baseline.get("due", 0) + 1, (baseline, seeded)

        # `payment_date = today` must match the row stamped with today's date.
        assert seeded.get("finance", 0) == baseline.get("finance", 0) + 1, (
            baseline,
            seeded,
        )

        # A future due date must NOT be reported as overdue.
        with SessionLocal() as db:
            db.execute(
                text(
                    "UPDATE orders SET due_date=:future WHERE company_id=:cid "
                    "AND customer_id=:customer"
                ),
                {
                    "future": (today + timedelta(days=30)).isoformat(),
                    "cid": company_id,
                    "customer": customer_id,
                },
            )
            db.commit()

        moved = counts()
        assert moved.get("due", 0) == baseline.get("due", 0), (baseline, moved)
