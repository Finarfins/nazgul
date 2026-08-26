"""V3 Absorption Rate KPI — PostgreSQL 16 parity twin.

Runs the exact SQLite scenario (``_SMOKE`` from test_v3_absorption_rate) against
real PostgreSQL so the dialect-sensitive parts of the KPI hold on both engines:
the strict GROUP BY in the counter-parts aggregation, NUMERIC SUM/COALESCE
arithmetic, the ``LOWER(COALESCE(txn_type,''))='expense'`` overhead filter over
ISO-text ``txn_date``, and the ``completed_at`` timestamp range bounds on work
orders. Auto-discovered by the CI ``test_*postgresql*.py`` glob.
"""

from __future__ import annotations

import os

import pytest


@pytest.mark.postgresql
def test_absorption_rate_flow_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    # Prefer a dedicated URL, but fall back to the shared APP url the CI parity
    # job already exports so this test runs without touching the workflow.
    database_url = os.environ.get("ABSORPTION_RATE_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("ABSORPTION_RATE_TEST_DATABASE_URL / APP_TEST_DATABASE_URL is not configured")
    monkeypatch.setenv("DATABASE_URL", database_url)

    # ``app.config.Settings`` is a module-level singleton frozen the first time it
    # is imported, and ``test_v3_absorption_rate`` pulls in ``app.auth`` (hence
    # app.config) transitively. Importing it at module scope would therefore bind
    # the engine to the default SQLite file during collection — before the
    # monkeypatch above runs — and this "PostgreSQL" twin would quietly test
    # SQLite. Importing here, after setenv, is what makes the parity real.
    from test_v3_absorption_rate import _SMOKE

    from app.db import engine

    assert engine.dialect.name == "postgresql", (
        f"beklenen PostgreSQL, bulunan {engine.dialect.name}: parity testi SQLite "
        "üzerinde koşuyor, DATABASE_URL bu iş için dışa aktarılmalı"
    )

    # The scenario is self-contained (creates its own company, customer, machine,
    # product, sale and work order) and asserts inline; a clean exec reproduces
    # the SQLite run.
    exec(compile(_SMOKE, "<absorption_rate_smoke>", "exec"), {})
