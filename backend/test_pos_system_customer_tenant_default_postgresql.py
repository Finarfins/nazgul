"""Aynı kapının GERÇEK PostgreSQL karşılığı (bkz. eşi olan SQLite dosyası).

Üretim PostgreSQL'dir ve kusurun burada aldığı biçim farklıdır: SQLite'ta
``rowid`` takma adı, PostgreSQL'de ``nextval(...)`` sunucu varsayılanı. İkisi de
aynı sonucu verir — ``company_id``'siz insert hata vermez, kiracı uydurur — ama
biri kapatılıp diğeri açık bırakılabilir. Bu yüzden kapı iki lehçede de koşar.

Kapı burada ayrıca varsayılanın ve dizinin katalogdan GERÇEKTEN gittiğini
ölçer; ``information_schema``/``pg_class`` bunu SQLite'ta ölçülemeyen bir
kesinlikle söyler.
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
PREVIOUS_REVISION = "20260812_0056"
PROBE_CUSTOMER_ID = 424242

INSERT_WITHOUT_COMPANY_ID = (
    "INSERT INTO pos_system_customers (customer_id, created_at) VALUES (:customer, :created)"
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


def _fabricated_company_id(engine) -> int | None:
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(
            text(INSERT_WITHOUT_COMPANY_ID),
            {"customer": PROBE_CUSTOMER_ID, "created": "2026-08-12"},
        )
        return int(
            connection.execute(
                text("SELECT company_id FROM pos_system_customers WHERE customer_id=:c"),
                {"c": PROBE_CUSTOMER_ID},
            ).scalar_one()
        )
    except IntegrityError:
        return None
    finally:
        transaction.rollback()
        connection.close()


def _gate(engine, label: str) -> None:
    fabricated = _fabricated_company_id(engine)
    assert fabricated is None, (
        f"{label}: an insert omitting company_id was ACCEPTED and silently "
        f"fabricated tenant company_id={fabricated}"
    )


def _column_default(engine, schema: str) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema=:s AND table_name='pos_system_customers' "
                "AND column_name='company_id'"
            ),
            {"s": schema},
        ).scalar()


@pytest.mark.postgresql
def test_company_id_is_not_auto_generated_on_postgresql() -> None:
    base_url = os.getenv("POS_TENANT_DEFAULT_TEST_DATABASE_URL") or os.getenv(
        "APP_TEST_DATABASE_URL"
    )
    if not base_url:
        pytest.skip(
            "POS_TENANT_DEFAULT_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required"
        )
    if not base_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("this gate must run against PostgreSQL")

    admin_engine = create_engine(base_url, pool_pre_ping=True)
    schema = f"pos_tenant_default_{uuid4().hex}"
    quoted = admin_engine.dialect.identifier_preparer.quote(schema)
    with admin_engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {quoted}"))

    test_url = make_url(base_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)
    engine = create_engine(test_url, pool_pre_ping=True)

    try:
        _alembic(base_url, schema, "upgrade", "head")

        # CONTROL — migrated schema rejects the write, and the catalog shows the
        # generator is really gone rather than merely unused.
        default_after = _column_default(engine, schema)
        assert default_after is None, f"company_id still has a server default: {default_after}"
        with engine.connect() as connection:
            sequences = connection.execute(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE c.relkind='S' AND n.nspname=:s "
                    "AND c.relname='pos_system_customers_company_id_seq'"
                ),
                {"s": schema},
            ).scalar_one()
        assert sequences == 0, "the SERIAL sequence survived the migration"
        _gate(engine, "CONTROL")
        print(
            f"GATE_CONTROL postgresql: default={default_after} sequences={sequences} "
            "insert without company_id rejected"
        )

        # MUTATION — the migration's own downgrade restores the default.
        _alembic(base_url, schema, "downgrade", PREVIOUS_REVISION)
        restored = _column_default(engine, schema)
        assert restored is not None and "nextval" in restored, (
            f"the mutation did not restore the generator (default={restored}); "
            "a red from a failed mutation would not be the gate firing"
        )
        with pytest.raises(AssertionError) as excinfo:
            _gate(engine, "MUTATION")
        message = str(excinfo.value)
        assert "fabricated tenant company_id=" in message, message
        print(f"MUTATION_RED postgresql: default={restored} :: {message}")

        _alembic(base_url, schema, "upgrade", "head")
        _gate(engine, "MUTATION restored")
        print("GATE_CONTROL postgresql after re-upgrade: rejected again")
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted} CASCADE"))
        admin_engine.dispose()


# ---------------------------------------------------------------------------
# DİZİ SÖZLEŞMESİ.
#
# Docstring ne BEYAN ediyorsa iddia TAM OLARAK onu talep eder. Önceki hâl
# zayıftı: yalnız "eski değer 500 DEĞİL" ve "sıradaki 1" deniyordu, dolayısıyla
# (1, true) gibi docstring'e AYKIRI bir durum testten geçebiliyordu. Artık
# beyan edilen demet DOĞRUDAN iddia ediliyor.
#
# Yapısal özellikler upgrade/downgrade boyunca KARŞILAŞTIRILIR ve her biri ya
# GERİ GELMİŞ ya da GERİ GELMEDİĞİ AÇIKÇA BEYAN EDİLMİŞ olarak çivilenir. Ne
# geri gelen ne çivilenen bir özellik, bilinen farkın kimse görmeden büyümesi
# demektir.
#
#   sahiplik (OWNED BY)  -> GERİ GELİR, eşitlik iddia edilir
#   increment_by         -> GERİ GELİR, eşitlik iddia edilir
#   min_value            -> GERİ GELİR, eşitlik iddia edilir
#   max_value            -> GERİ GELİR, eşitlik iddia edilir
#   cache_size           -> GERİ GELİR, eşitlik iddia edilir
#   cycle                -> GERİ GELİR, eşitlik iddia edilir
#   data_type            -> GERİ GELİR, eşitlik iddia edilir
#   start_value          -> GERİ GELİR, eşitlik iddia edilir
#   (last_value, is_called) -> GERİ GELMEZ (upgrade yok eder). Beyan edilen
#       SONUÇ durumu doğrudan iddia edilir: boş tabloda tam olarak (1, False),
#       dolu tabloda tam olarak (MAX, True).
#
# ÖLÇÜM NOTU: incelemede örnek verilen (0, true) durumu bu dizide ULAŞILAMAZ;
# PostgreSQL reddediyor ("setval: value 0 is out of bounds ... (1..2147483647)")
# çünkü MINVALUE 1. Beyanı çelen ULAŞILABİLİR durum (1, true)'dur — düzeltme
# öncesi davranışın ta kendisi (sıradaki değer 1 yerine 2) — mutasyon budur.
# ---------------------------------------------------------------------------

SEQUENCE = "pos_system_customers_company_id_seq"
DECLARED_EMPTY_STATE = (1, False)
STRUCTURAL_PROPERTIES = (
    "data_type", "start_value", "min_value", "max_value",
    "increment_by", "cycle", "cache_size",
)


def _sequence_state(engine, schema: str):
    with engine.connect() as connection:
        exists = connection.execute(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relkind='S' AND n.nspname=:s AND c.relname=:q"
            ),
            {"s": schema, "q": SEQUENCE},
        ).scalar_one()
        if not exists:
            return None
        row = connection.execute(
            text(f"SELECT last_value, is_called FROM {SEQUENCE}")
        ).one()
        return (int(row[0]), bool(row[1]))


def _sequence_properties(engine, schema: str) -> dict | None:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT data_type::text, start_value, min_value, max_value, "
                "increment_by, cycle, cache_size FROM pg_sequences "
                "WHERE schemaname=:s AND sequencename=:q"
            ),
            {"s": schema, "q": SEQUENCE},
        ).first()
        if row is None:
            return None
        properties = dict(zip(STRUCTURAL_PROPERTIES, row))
        properties["owned_by"] = connection.execute(
            text(
                "SELECT c.relname || '.' || a.attname FROM pg_depend d "
                "JOIN pg_class s ON s.oid=d.objid "
                "JOIN pg_namespace n ON n.oid=s.relnamespace "
                "JOIN pg_class c ON c.oid=d.refobjid "
                "JOIN pg_attribute a ON a.attrelid=d.refobjid AND a.attnum=d.refobjsubid "
                "WHERE s.relname=:q AND n.nspname=:s AND d.deptype='a'"
            ),
            {"s": schema, "q": SEQUENCE},
        ).scalar()
        return properties


def _assert_sequence_contract(before: dict, after: dict, state, label: str, rows: int = 0) -> None:
    """The assertion under test. Claims EXACTLY what the migration declares."""
    assert after is not None, f"{label}: downgrade must recreate the sequence"
    for name in (*STRUCTURAL_PROPERTIES, "owned_by"):
        assert after[name] == before[name], (
            f"{label}: structural sequence property {name!r} was NOT restored by downgrade: "
            f"before={before[name]!r} after={after[name]!r}"
        )
    expected_state = DECLARED_EMPTY_STATE if rows == 0 else (rows_max(rows), True)
    assert state == expected_state, (
        f"{label}: sequence state is not the declared state: "
        f"expected {expected_state}, got {state}"
    )


def rows_max(value: int) -> int:
    return value


def _prepare(base_url: str):
    admin_engine = create_engine(base_url, pool_pre_ping=True)
    schema = f"pos_seq_{uuid4().hex}"
    quoted = admin_engine.dialect.identifier_preparer.quote(schema)
    with admin_engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {quoted}"))
    test_url = make_url(base_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)
    return admin_engine, schema, quoted, create_engine(test_url, pool_pre_ping=True)


def _require_url():
    base_url = os.getenv("POS_TENANT_DEFAULT_TEST_DATABASE_URL") or os.getenv(
        "APP_TEST_DATABASE_URL"
    )
    if not base_url:
        pytest.skip(
            "POS_TENANT_DEFAULT_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required"
        )
    return base_url


@pytest.mark.postgresql
def test_downgrade_restores_every_structural_property_and_the_declared_state() -> None:
    """CONTROL: structure restored, state EXACTLY the declared tuple."""
    base_url = _require_url()
    admin_engine, schema, quoted, engine = _prepare(base_url)
    try:
        _alembic(base_url, schema, "upgrade", PREVIOUS_REVISION)
        before = _sequence_properties(engine, schema)
        assert before is not None
        with engine.begin() as connection:
            connection.execute(
                text(f"SELECT setval('{SEQUENCE}', :v, true)"), {"v": 500}
            )
        assert _sequence_state(engine, schema) == (500, True)

        _alembic(base_url, schema, "upgrade", "head")
        assert _sequence_state(engine, schema) is None, "upgrade must drop the sequence"

        _alembic(base_url, schema, "downgrade", PREVIOUS_REVISION)
        after = _sequence_properties(engine, schema)
        state = _sequence_state(engine, schema)
        _assert_sequence_contract(before, after, state, "CONTROL")
        print(
            f"SEQUENCE_CONTROL postgresql: state={state} (declared {DECLARED_EMPTY_STATE}) "
            f"structural={{{', '.join(f'{k}={after[k]!r}' for k in (*STRUCTURAL_PROPERTIES, 'owned_by'))}}}"
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted} CASCADE"))
        admin_engine.dispose()


@pytest.mark.postgresql
def test_downgrade_on_a_non_empty_table_positions_the_sequence_past_max() -> None:
    """The docstring also declares the NON-EMPTY case; assert it directly."""
    base_url = _require_url()
    admin_engine, schema, quoted, engine = _prepare(base_url)
    try:
        _alembic(base_url, schema, "upgrade", PREVIOUS_REVISION)
        before = _sequence_properties(engine, schema)
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO companies (name,is_active,created_at) VALUES ('T',true,now())")
            )
            connection.execute(
                text("INSERT INTO customers (name,company_id) VALUES ('c',1)")
            )
            connection.execute(
                text(
                    "INSERT INTO pos_system_customers (company_id,customer_id,created_at) "
                    "VALUES (7,1,'x')"
                )
            )
        _alembic(base_url, schema, "upgrade", "head")
        _alembic(base_url, schema, "downgrade", PREVIOUS_REVISION)

        after = _sequence_properties(engine, schema)
        state = _sequence_state(engine, schema)
        _assert_sequence_contract(before, after, state, "CONTROL non-empty", rows=7)
        with engine.begin() as connection:
            next_value = connection.execute(
                text(f"SELECT nextval('{SEQUENCE}')")
            ).scalar_one()
        assert next_value == 8, f"declared MAX+1, got {next_value}"
        print(
            f"SEQUENCE_CONTROL postgresql non-empty: state={state} next_value={next_value} (declared MAX+1)"
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted} CASCADE"))
        admin_engine.dispose()


# Each mutation rewrites ONE of the migration's own downgrade statements.
# (create_sequence_sql, position_sql, label)
_SEQUENCE_MUTATIONS = [
    (
        None,
        "SELECT setval('{seq}', 1, true)",
        "state-(1,true)-instead-of-(1,false)",
    ),
    ("CREATE SEQUENCE {seq} AS integer INCREMENT BY 2 OWNED BY {table}.company_id", None, "increment_by"),
    ("CREATE SEQUENCE {seq} AS integer CACHE 20 OWNED BY {table}.company_id", None, "cache_size"),
    ("CREATE SEQUENCE {seq} AS integer CYCLE OWNED BY {table}.company_id", None, "cycle"),
    ("CREATE SEQUENCE {seq} AS integer", None, "owned_by-binding-dropped"),
]


@pytest.mark.postgresql
@pytest.mark.parametrize("create_sql,position_sql,label", _SEQUENCE_MUTATIONS)
def test_a_broken_downgrade_turns_the_sequence_contract_red(
    create_sql, position_sql, label
) -> None:
    """MUTATION: break exactly one part of the downgrade; the gate must fire."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_pos_migration_pg",
        BACKEND / "alembic" / "versions"
        / "20260812_0057_pos_system_customer_tenant_default.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    base_url = _require_url()
    admin_engine, schema, quoted, engine = _prepare(base_url)
    try:
        _alembic(base_url, schema, "upgrade", PREVIOUS_REVISION)
        before = _sequence_properties(engine, schema)
        _alembic(base_url, schema, "upgrade", "head")

        table = module.TABLE
        create = (create_sql or module._PG_CREATE_SEQUENCE).format(seq=SEQUENCE, table=table)
        position = (position_sql or module._PG_POSITION_SEQUENCE).format(
            seq=SEQUENCE, table=table
        )
        set_default = module._PG_SET_DEFAULT.format(seq=SEQUENCE, table=table)
        # The mutated DDL must APPLY cleanly; a failure here is a broken
        # mutation, not the gate firing, and would surface as an error.
        with engine.begin() as connection:
            connection.execute(text(create))
            connection.execute(text(set_default))
            connection.execute(text(position))

        after = _sequence_properties(engine, schema)
        state = _sequence_state(engine, schema)
        with pytest.raises(AssertionError) as excinfo:
            _assert_sequence_contract(before, after, state, f"MUTATION[{label}]")
        message = str(excinfo.value)
        assert f"MUTATION[{label}]" in message, message
        print(f"MUTATION_RED postgresql sequence[{label}]: {message.splitlines()[0]}")
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted} CASCADE"))
        admin_engine.dispose()


# ---------------------------------------------------------------------------
# BEYAN TARAMASI: göç docstring'i teşhis sorgusu için ÖLÇÜLMÜŞ bir sonuç
# bildiriyor — uydurulmuş satır GERÇEK bir firmayla çakıştığında eski sinyal 0,
# sahiplik uyuşmazlığı 1 buluyor. Bu bir docstring ölçümüydü; burada iddiaya
# bağlanıyor, böylece sinyalin gücü yarın da doğrulanır.
# ---------------------------------------------------------------------------

@pytest.mark.postgresql
def test_ownership_signal_catches_a_fabricated_row_that_collides_with_a_real_company() -> None:
    base_url = _require_url()
    admin_engine, schema, quoted, engine = _prepare(base_url)
    try:
        # Pre-fix schema: the generator is still in place, so a real fabricated
        # row can be produced rather than simulated.
        _alembic(base_url, schema, "upgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO companies (name,is_active,created_at) "
                "VALUES ('T1',true,now()),('T2',true,now())"
            ))
            connection.execute(text(
                "INSERT INTO customers (name,company_id) VALUES ('c1',1),('c2',1)"
            ))
            # legitimate row: explicit company_id, matching its customer's owner
            connection.execute(text(
                "INSERT INTO pos_system_customers (company_id,customer_id,created_at) "
                "VALUES (1,1,'legit')"
            ))
            # burn sequence value 1 (already taken by the legitimate row), so the
            # fabricated row lands on 2 — a company that EXISTS.
            connection.execute(text(f"SELECT setval('{SEQUENCE}', 1, true)"))
            connection.execute(text(
                "INSERT INTO pos_system_customers (customer_id,created_at) VALUES (2,'fabricated')"
            ))

        with engine.connect() as connection:
            fabricated_company, company_exists, customer_owner = connection.execute(text(
                "SELECT p.company_id, "
                "EXISTS(SELECT 1 FROM companies c WHERE c.id=p.company_id), "
                "(SELECT cu.company_id FROM customers cu WHERE cu.id=p.customer_id) "
                "FROM pos_system_customers p WHERE p.created_at='fabricated'"
            )).one()
            old_signal = connection.execute(text(
                "SELECT count(*) FROM pos_system_customers p "
                "WHERE NOT EXISTS (SELECT 1 FROM companies c WHERE c.id=p.company_id)"
            )).scalar_one()
            owner_mismatch = connection.execute(text(
                "SELECT count(*) FROM pos_system_customers p "
                "JOIN customers cu ON cu.id=p.customer_id "
                "WHERE cu.company_id <> p.company_id"
            )).scalar_one()

        # The premise the diagnostic rests on: this fabricated row DOES collide
        # with a real company, so the old signal genuinely cannot see it.
        assert company_exists is True, "the fabricated id must collide with a real company"
        assert fabricated_company != customer_owner
        assert old_signal == 0, (
            f"declared: the company-existence signal finds 0 here, got {old_signal}"
        )
        assert owner_mismatch == 1, (
            f"declared: the ownership signal finds 1 here, got {owner_mismatch}"
        )
        print(
            f"DECLARATION_PIN postgresql diagnostic: fabricated_company={fabricated_company} "
            f"company_exists={company_exists} customer_owner={customer_owner} "
            f"old_signal={old_signal} owner_mismatch={owner_mismatch}"
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted} CASCADE"))
        admin_engine.dispose()
