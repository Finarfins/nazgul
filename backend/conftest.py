"""Pytest collection policy for the production-readiness suite.

Older releases stored executable smoke scripts under ``test_*.py`` names. Those
files perform work at import time and share a mutable ``veriler.db`` fixture, so
collecting them inside one pytest process produces order-dependent failures and
can accidentally package test data. They remain available for historical
reference while their coverage is migrated to isolated pytest tests.

It also enforces the PostgreSQL-parity invariant: when ``REQUIRE_PG=1`` is set
(the ``backend-postgresql`` CI job, see .github/workflows/ci.yml) every test must
run against a real PostgreSQL engine. See :func:`_require_postgresql_engine`.

H94: every PostgreSQL session opened from a test process runs in
``Europe/Istanbul``, not UTC. See :data:`PG_OTURUM_DILIMI`.
"""

import os

import pytest

#: H94 — PG ikizlerinin OTURUM dilimi. CI'ın PG servisi UTC'dir; PG
#: `TIMESTAMPTZ`yi oturum diliminde döndürdüğü için `+03:00` gerilemeleri
#: (H73: `utc_iso`dan geçmeyen bir `*_at`) orada GÖRÜNMEZDİ. `PGOPTIONS`
#: libpq'nun ortam değişkenidir: psycopg ile açılan HER bağlantı (uygulamanın
#: motoru, ikizin kendi `create_engine`i, alt süreçteki alembic) onu okur.
#: Test modülleri içe aktarılmadan, yani ilk bağlantıdan ÖNCE yazılır. Sona
#: eklenir: aynı parametrenin SONRAKİ `-c`si kazanır, önceden verilmiş bir
#: `PGOPTIONS` başka ayarlarıyla korunur.
PG_OTURUM_DILIMI = "Europe/Istanbul"
_PG_DILIM_SECENEGI = f"-c timezone={PG_OTURUM_DILIMI}"
os.environ["PGOPTIONS"] = " ".join(
    p for p in (os.environ.get("PGOPTIONS", "").strip(), _PG_DILIM_SECENEGI) if p
)

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

@pytest.fixture(scope="session", autouse=True)
def _pg_oturum_dilimi_istanbul() -> None:
    """H94 kapısı: `REQUIRE_PG=1` işinde oturum dilimi GERÇEKTEN İstanbul mu?

    `PGOPTIONS` sessizce ezilirse (ör. sürücü ortamı okumazsa) ikizler yine
    UTC'de koşar ve H73 sınıfı gerilemeler yeniden görünmez olurdu. Oturum
    başına BİR bağlantı.

    `app` BİLEREK içe aktarılmaz — ÖLÇÜLDÜ: oturum kapsamlı bir fikstür,
    ikizin modül fikstüründen ÖNCE koşar; `app.db`yi burada içe aktarmak
    `Settings`i ikiz kendi ortamını (ör. `AUTO_MIGRATE=false`) yazmadan
    DONDURDU ve `test_sec3b_cari_maskeleme_postgresql.py` taze şemada
    açılış yöneticisi hatasıyla 7 hata verdi. Çıplak psycopg bağlantısı
    aynı libpq'yu ve aynı `PGOPTIONS`u okur.
    """
    if os.environ.get("REQUIRE_PG") != "1":
        return
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        return  # `_require_postgresql_engine` bunu kendi adıyla kırmızı yakar.

    import psycopg

    with psycopg.connect("postgresql://" + url.split("://", 1)[1]) as baglanti:
        dilim = baglanti.execute("SHOW TimeZone").fetchone()[0]
    if dilim != PG_OTURUM_DILIMI:
        pytest.fail(
            f"H94: PG oturum dilimi '{dilim}', beklenen '{PG_OTURUM_DILIMI}'. "
            f"PGOPTIONS={os.environ.get('PGOPTIONS')!r}",
            pytrace=False,
        )


@pytest.fixture()
def acilis_sifresi():
    """PostgreSQL ikizleri için açılış şifresi fixture'ı (kurulum + teardown)."""
    from tests.pg_ikiz_yardimci import acilisa_cek

    acilisa_cek()
    try:
        yield
    finally:
        acilisa_cek()
