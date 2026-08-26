"""Aynı kapının GERÇEK PostgreSQL karşılığı (bkz. eşi olan SQLite dosyası).

Üretim PostgreSQL'dir ve kısıt burada FARKLI bir biçimde eklenir: SQLite'ta
tablo CHECK ile yeniden kurulur, PostgreSQL'de ``ADD CONSTRAINT ... NOT VALID``
çalıştırılır. İkisi de aynı sonucu vermeli — kimliği çözülmüş bir isteğin satırı
firmasız yazılamamalı — ama biri kapatılıp diğeri açık bırakılabilir. Bu yüzden
kapı iki lehçede de koşar.

``NOT VALID`` ayrıca burada AYRICA ölçülür: kısıt YENİ yazımlarda tam olarak
zorlanmalı, buna karşılık GEÇMİŞ satırlar taranmamalı ve SİLİNMEMELİ. Bu iki
şeyin birlikte doğru olması, göçün üretimde eski denetim satırlarını yok
etmeden uygulanabilmesinin tek sebebi.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent
PREVIOUS_REVISION = "20260812_0057"
TABLE = "security_audit_logs"
CONSTRAINT = "ck_security_audit_logs_untenanted_only_preauth"

_INSERT = (
    f"INSERT INTO {TABLE} "  # noqa: S608 - sabit tablo adı
    "(action, path, status_code, created_at, outcome, user_id, username, company_id) "
    "VALUES ('POST', '/api/kapi', 401, now(), 'denied', :uid, :uname, :cid)"
)


def _alembic(database_url: str, schema: str, *args: str) -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    env["PYTHONIOENCODING"] = "utf-8"
    env["PGOPTIONS"] = f"-csearch_path={schema}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=BACKEND, env=env, text=True, capture_output=True, timeout=300,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def _accepts(engine, *, uid, uname, cid) -> bool:
    """Try one insert. True if the database accepted it. Always rolls back."""
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(text(_INSERT), {"uid": uid, "uname": uname, "cid": cid})
        return True
    except IntegrityError:
        return False
    finally:
        transaction.rollback()
        connection.close()


def _gate(engine, label: str) -> None:
    """The assertion under test. Fires (AssertionError) when the hole is open.

    İKİ YÖN BİRDEN çivilenir; yalnız "reddedilmeli" iddiası tabloyu tamamen
    yazılamaz yapan bir hatadan da geçerdi.
    """
    identified = _accepts(engine, uid=7, uname="ayse", cid=None)
    assert not identified, (
        f"{label}: a row with an identity (user_id=7) and a NULL company_id was "
        "ACCEPTED — an identified request lost its tenant silently"
    )
    named_only = _accepts(engine, uid=None, uname="ayse", cid=None)
    assert not named_only, (
        f"{label}: a row with a username and a NULL company_id was ACCEPTED"
    )
    preauth = _accepts(engine, uid=None, uname=None, cid=None)
    assert preauth, (
        f"{label}: a genuine pre-auth row (no identity, no company) was REJECTED — "
        "login attempts and AUTH_REQUIRED 401s would stop being recorded"
    )
    tenanted = _accepts(engine, uid=7, uname="ayse", cid=3)
    assert tenanted, f"{label}: an ordinary tenanted row was REJECTED"


def _constraint_row(engine, schema: str):
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT c.convalidated FROM pg_constraint c "
                "JOIN pg_namespace n ON n.oid = c.connamespace "
                "WHERE n.nspname = :s AND c.conname = :name AND c.contype = 'c'"
            ),
            {"s": schema, "name": CONSTRAINT},
        ).fetchone()


@pytest.mark.postgresql
def test_untenanted_audit_row_requires_no_identity_on_postgresql() -> None:
    base_url = os.getenv("SECURITY_AUDIT_TEST_DATABASE_URL") or os.getenv(
        "APP_TEST_DATABASE_URL"
    )
    if not base_url:
        pytest.skip(
            "SECURITY_AUDIT_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required"
        )
    if not base_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("this gate must run against PostgreSQL")

    admin_engine = create_engine(base_url, pool_pre_ping=True)
    schema = f"audit_untenanted_{uuid4().hex}"
    quoted = admin_engine.dialect.identifier_preparer.quote(schema)
    with admin_engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {quoted}"))

    test_url = make_url(base_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)
    engine = create_engine(test_url, pool_pre_ping=True)

    try:
        _alembic(base_url, schema, "upgrade", "head")

        # CONTROL — the constraint exists and is enforced at head. On a CLEAN
        # chain it arrives from the model (baseline runs ``create_all`` and the
        # constraint is part of the model), so its validated flag is not asserted
        # here; the NOT VALID contract is measured below on the real upgrade path.
        assert _constraint_row(engine, schema) is not None, (
            f"{CONSTRAINT} is missing from pg_constraint"
        )
        _gate(engine, "CONTROL")
        print(f"GATE_CONTROL postgresql: {CONSTRAINT} present, gate enforced at head")

        # MUTATION — the migration's own downgrade drops the constraint.
        _alembic(base_url, schema, "downgrade", PREVIOUS_REVISION)
        assert _constraint_row(engine, schema) is None, "downgrade left the constraint behind"

        with pytest.raises(AssertionError) as excinfo:
            _gate(engine, "MUTATION")
        message = str(excinfo.value)
        assert "was ACCEPTED — an identified request lost its tenant" in message, message
        print(f"MUTATION_RED postgresql: {message}")

        # NOT VALID SÖZLEŞMESİ — ÜRETİMDEKİ GERÇEK YOL. Kısıt şu an YOK (yukarıda
        # düşürüldü), tıpkı göç uygulanmamış bir üretim veritabanı gibi. Araya
        # ihlal eden ESKİ bir satır konur ve göç uygulanır: satır KORUNMALI, ama
        # YENİ yazım yine de reddedilmeli. İkisi birlikte ölçülmezse "NOT VALID"
        # beyanı boş bir iddia olurdu.
        with engine.begin() as connection:
            connection.execute(text(_INSERT), {"uid": 9, "uname": "eski", "cid": None})
        _alembic(base_url, schema, "upgrade", "head")

        row = _constraint_row(engine, schema)
        assert row is not None, "the migration did not add the constraint"
        assert row[0] is False, (
            "the constraint was added as VALIDATED; it would have scanned and "
            "rejected pre-existing audit rows instead of leaving them in place"
        )
        with engine.connect() as connection:
            survivors = connection.execute(
                text(
                    f"SELECT COUNT(*) FROM {TABLE} "  # noqa: S608
                    "WHERE company_id IS NULL AND user_id IS NOT NULL"
                )
            ).scalar_one()
        assert survivors == 1, (
            "the migration destroyed a pre-existing untenanted audit row; "
            "NOT VALID exists precisely so that cannot happen"
        )
        _gate(engine, "CONTROL after legacy row")
        print(
            f"GATE_CONTROL postgresql: convalidated={row[0]}, {survivors} legacy row "
            "preserved, new writes still constrained"
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted} CASCADE"))
        admin_engine.dispose()
