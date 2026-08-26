"""V3 Parça Supersession — PostgreSQL 16 parity twin.

Runs the exact SQLite scenario (``_SMOKE`` from test_v3_supersession) against
real PostgreSQL so the dialect-sensitive parts hold on both engines: the
part_supersessions table + FK/UNIQUE/CHECK constraints created by migration
0018, IntegrityError -> 409 mapping on the duplicate supersession, and the
chain resolution queries. Auto-discovered by the CI ``test_*postgresql*.py``
glob.
"""

from __future__ import annotations

import os

import pytest


@pytest.mark.postgresql
def test_supersession_flow_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    # Prefer a dedicated URL, but fall back to the shared APP url the CI parity
    # job already exports so this test runs without touching the workflow.
    database_url = os.environ.get("SUPERSESSION_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("SUPERSESSION_TEST_DATABASE_URL / APP_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    # Import AFTER DATABASE_URL is set: pulling _SMOKE at module level would
    # import the app package (via app.auth) during collection and freeze the
    # engine on the default SQLite URL, so the "PG" run would silently test
    # SQLite instead (and trip over state left by earlier twin files).
    from test_v3_supersession import _SMOKE

    # The scenario is self-contained (creates its own company, products and
    # supersessions) and asserts inline; a clean exec reproduces the SQLite run.
    exec(compile(_SMOKE, "<supersession_smoke>", "exec"), {})
