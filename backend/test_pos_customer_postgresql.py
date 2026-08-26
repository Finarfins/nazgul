"""POS Increment 1 (cari + fiyat + iskonto) — PostgreSQL 16 parity twin.

Runs the exact SQLite scenario (``_CUSTOMER_SMOKE`` from test_v3_pos) against
real PostgreSQL, so the Decimal/NUMERIC rounding behind the discount chain
(``compute_line`` per line, then ``apply_global_discount`` over the sum) is
proven on both engines rather than assumed, along with the customer resolution
queries and the credit-without-customer refusal.

Why its own file: the CI PostgreSQL lane drops and recreates the schema once per
FILE, and the bootstrap admin password can be rotated only once per database.
Two scenarios that both log in as admin cannot share a file — whichever runs
second is handed a 401 from the password the first one rotated. Auto-discovered
by the CI ``test_*postgresql*.py`` glob, so no workflow change is needed.
"""

from __future__ import annotations

import os

import pytest


@pytest.mark.postgresql
def test_pos_customer_price_and_discounts_postgresql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ.get("POS_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("POS_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required")
    # Guard the point of the twin: a misconfigured URL must fail loudly instead
    # of silently re-running the scenario on SQLite.
    assert database_url.startswith("postgresql"), (
        "the PostgreSQL twin must not run on another engine: " + database_url
    )
    monkeypatch.setenv("DATABASE_URL", database_url)
    # ``_CUSTOMER_SMOKE`` now exercises the flag-on idempotent replay, where the
    # order's ``paid_amount`` is zeroed and the settled amount is re-derived from
    # ``SUM(payments.amount)``. The SQLite scenario runs it with the allocation
    # engine enabled; keep this twin in lockstep so the replay SUM is proven on
    # real PG 16, not just on SQLite.
    #
    # An env var is too late here: the autouse ``_require_postgresql_engine`` gate
    # (REQUIRE_PG=1 in the backend-postgresql job) already imported the app and
    # froze the settings singleton with the flag off before this body runs. Force
    # the value on the singleton itself — ``pos.py`` reads it per request from the
    # same object — and let monkeypatch restore it at teardown.
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "payment_allocation_engine_enabled", True)

    # Import AFTER DATABASE_URL is set: pulling the scenario at module level
    # would import the app package during collection and freeze the engine on
    # the default SQLite URL.
    from test_v3_pos import _CUSTOMER_SMOKE

    # The scenario is self-contained (creates its own company, product and
    # customer) and asserts inline; a clean exec reproduces the SQLite run.
    exec(compile(_CUSTOMER_SMOKE, "<pos_customer_smoke>", "exec"), {})
