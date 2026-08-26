"""Pytest collection policy for the production-readiness suite.

Older releases stored executable smoke scripts under ``test_*.py`` names. Those
files perform work at import time and share a mutable ``veriler.db`` fixture, so
collecting them inside one pytest process produces order-dependent failures and
can accidentally package test data. They remain available for historical
reference while their coverage is migrated to isolated pytest tests.

It also enforces the PostgreSQL-parity invariant: when ``REQUIRE_PG=1`` is set
(the ``backend-postgresql`` CI job, see .github/workflows/ci.yml) every test must
run against a real PostgreSQL engine. See :func:`_require_postgresql_engine`.
"""

import os

import pytest

collect_ignore = [
    "test_detail_workflows.py",
    "test_document_engine.py",
    "test_e2e_browser.py",
    "test_finance_core.py",
    "test_imports.py",
    "test_inventory_reports.py",
    "test_operations.py",
    "test_outputs.py",
    "test_performance_filters.py",
    "test_search_analytics.py",
    "test_stabilization.py",
    "test_stabilization2.py",
    "test_tenancy_notifications.py",
    "test_transaction_integrity.py",
    "test_transaction_warehouse.py",
    "test_v2_2_validations.py",
    "test_v2_3_payment_lists.py",
    "test_v2_4_dashboard.py",
    "test_v2_5_cari_crm.py",
    "test_v2_6_quick_actions.py",
    "test_v2_7_tenant_security.py",
]


@pytest.fixture(autouse=True)
def _require_postgresql_engine() -> None:
    """Fail loudly when a PostgreSQL-job test is really running on SQLite.

    ``app.config.Settings`` is a module-level singleton frozen the first time it
    is imported, so a twin that only monkeypatches ``DATABASE_URL`` inside its
    test body arrives too late: the engine is already bound to the default local
    SQLite ``veriler.db`` and the "PostgreSQL parity" test passes without ever
    touching PostgreSQL. That failure mode is silent — the suite stays green and
    the dialect-specific bugs these twins exist to catch (strict GROUP BY,
    boolean handling, RETURNING, NUMERIC precision) go unverified.

    The ``backend-postgresql`` job therefore sets ``REQUIRE_PG=1`` and this
    fixture turns the invariant into a hard gate: every test in that job must see
    a PostgreSQL engine or CI goes red naming the offending test. Other jobs
    (``backend-quality``) leave the flag unset and keep running on SQLite, so
    this is a no-op for them.

    Importing ``app.db`` here is deliberate and safe: the job exports
    ``DATABASE_URL``, so the settings singleton this triggers already resolves to
    PostgreSQL. If it does not, that is exactly the misconfiguration being caught.
    """
    if os.environ.get("REQUIRE_PG") != "1":
        return

    from app.db import engine

    if engine.dialect.name != "postgresql":
        pytest.fail(
            f"REQUIRE_PG=1 ancak aktif motor '{engine.dialect.name}' — bu test "
            "PostgreSQL yerine SQLite üzerinde koşuyor. DATABASE_URL bu iş için "
            "dışa aktarılmalı ve twin, senaryosunu setenv'den SONRA import "
            "etmelidir.",
            pytrace=False,
        )
