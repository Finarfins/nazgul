"""Kapı: ``pos_system_customers.company_id`` KENDİLİĞİNDEN değer üretmemeli.

``company_id`` bu tablonun kiracı anahtarı ve birincil anahtarıdır. Tek sütunlu
tamsayı birincil anahtar her iki lehçede de otomatik artan olur (PostgreSQL'de
``nextval`` varsayılanı, SQLite'ta ``rowid`` takma adı), dolayısıyla değer
verilmeden yapılan bir insert HATA VERMEZ — olmayan bir kiracı UYDURUR.
``20260812_0056`` bunu kapatır; bu dosya kapının kendisidir.

Kapı MUTASYONLA kanıtlanır: göç geri alınıp varsayılan geri getirildiğinde
iddianın KIRMIZI olması gerekir. Kırmızının çökmeden ya da DDL çakışmasından
değil, iddianın kendisinden geldiği ``AssertionError`` tipini şart koşarak
doğrulanır.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent
PREVIOUS_REVISION = "20260812_0056"
PROBE_CUSTOMER_ID = 424242

INSERT_WITHOUT_COMPANY_ID = (
    "INSERT INTO pos_system_customers (customer_id, created_at) VALUES (:customer, :created)"
)


def _alembic(database_url: str, *args: str) -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=BACKEND, env=env, text=True, capture_output=True, timeout=300,
        # Migration messages are Turkish; the Windows console codepage cannot
        # decode them and would raise UnicodeDecodeError while capturing.
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def _fabricated_company_id(engine) -> int | None:
    """Insert WITHOUT company_id. Return the fabricated tenant id, or None if
    the database rejected the write. Always rolls back."""
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
    """The assertion under test. Fires (AssertionError) when the hole is open."""
    fabricated = _fabricated_company_id(engine)
    assert fabricated is None, (
        f"{label}: an insert omitting company_id was ACCEPTED and silently "
        f"fabricated tenant company_id={fabricated}"
    )


def _sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'pos_tenant_default.db').as_posix()}"


def test_insert_without_company_id_is_rejected(tmp_path: Path) -> None:
    """CONTROL: on the migrated schema the write must fail."""
    url = _sqlite_url(tmp_path)
    _alembic(url, "upgrade", "head")
    engine = create_engine(url)
    try:
        _gate(engine, "CONTROL")
        print("GATE_CONTROL sqlite: insert without company_id rejected (no tenant fabricated)")
    finally:
        engine.dispose()


def test_restoring_the_default_turns_the_gate_red(tmp_path: Path) -> None:
    """MUTATION: put the pre-fix schema back and prove the gate goes red.

    The mutation is the migration's own ``downgrade``, not a hand-made
    imitation, so what is proven is that THIS migration closes the hole.
    """
    url = _sqlite_url(tmp_path)
    _alembic(url, "upgrade", "head")
    engine = create_engine(url)
    try:
        _gate(engine, "MUTATION baseline")

        # Restore the pre-fix column. A failure here would be a DDL problem, not
        # the gate firing, and surfaces as a plain assertion inside _alembic.
        _alembic(url, "downgrade", PREVIOUS_REVISION)

        with pytest.raises(AssertionError) as excinfo:
            _gate(engine, "MUTATION")
        message = str(excinfo.value)
        assert "fabricated tenant company_id=" in message, message
        print(f"MUTATION_RED sqlite: {message}")

        # Re-applying the migration makes it green again, which rules out the
        # environment (rather than the migration) being what closed the hole.
        _alembic(url, "upgrade", "head")
        _gate(engine, "MUTATION restored")
        print("GATE_CONTROL sqlite after re-upgrade: rejected again")
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# ŞEMA EŞİTLİĞİ KAPISI
#
# Göç, SQLite'ta tabloyu KOMPLE yeniden yaratıyor. Davranış kapısı (yukarıda)
# yalnız "company_id'siz insert reddedilmeli"yi koruyor; yeniden yaratma
# sırasında UNIQUE ya da bir indeks DÜŞSE bile o kapı yeşil kalır. Burada
# yeniden yaratmanın ÖNCESİ ile SONRASI, göçün kendi DDL'inden BAĞIMSIZ bir
# çıpaya karşı karşılaştırılır: çıpa, göç öncesi revizyonun (20260811_0055)
# gerçek katalog durumudur, SQLite'ın kendi PRAGMA'larıyla okunur.
#
# Yalnız İKİ fark BİLEREK bekleniyor; ikisi de düzeltmenin ta kendisi:
#   1. company_id bildirilen tip  INTEGER -> BIGINT
#   2. BIGINT birincil anahtar artık rowid takma adı olmadığı için SQLite
#      ayrı bir PK autoindex'i yaratır (INTEGER hâlinde yoktu).
# Bunların dışındaki HER fark kapıyı kırmızıya çevirir.
# ---------------------------------------------------------------------------

MIGRATION_PATH = (
    BACKEND / "alembic" / "versions"
    / "20260812_0057_pos_system_customer_tenant_default.py"
)


def _schema_snapshot(db_path: Path) -> dict:
    """Full catalog shape of the table, read straight from SQLite."""
    connection = sqlite3.connect(db_path)
    try:
        columns = [
            {"name": r[1], "type": r[2].upper(), "notnull": r[3], "default": r[4], "pk": r[5]}
            for r in connection.execute("PRAGMA table_info(pos_system_customers)")
        ]
        indexes = []
        for row in connection.execute("PRAGMA index_list(pos_system_customers)"):
            name, unique, origin = row[1], row[2], row[3]
            cols = [r[2] for r in connection.execute(f'PRAGMA index_info("{name}")')]
            indexes.append({"name": name, "unique": unique, "origin": origin, "columns": cols})
        foreign_keys = [
            {"table": r[2], "from": r[3], "to": r[4]}
            for r in connection.execute("PRAGMA foreign_key_list(pos_system_customers)")
        ]
        return {
            "columns": columns,
            # autoindex names are positional in SQLite; sort by column set so a
            # renumbering is not mistaken for a lost constraint.
            "indexes": sorted(indexes, key=lambda i: (str(i["columns"]), i["origin"])),
            "foreign_keys": sorted(foreign_keys, key=lambda f: (f["table"], f["from"])),
        }
    finally:
        connection.close()


def _apply_declared_differences(before: dict) -> dict:
    """The anchor, adjusted by the two differences this migration INTENDS."""
    expected = {
        "columns": [dict(c) for c in before["columns"]],
        "indexes": [dict(i) for i in before["indexes"]],
        "foreign_keys": [dict(f) for f in before["foreign_keys"]],
    }
    for column in expected["columns"]:
        if column["name"] == "company_id":
            column["type"] = "BIGINT"
    expected["indexes"].append(
        {"name": "<pk-autoindex>", "unique": 1, "origin": "pk", "columns": ["company_id"]}
    )
    expected["indexes"].sort(key=lambda i: (str(i["columns"]), i["origin"]))
    return expected


def _comparable(snapshot: dict) -> dict:
    """Drop SQLite's positional autoindex NAMES; compare shape, not numbering."""
    return {
        "columns": snapshot["columns"],
        "indexes": [
            {k: v for k, v in index.items() if k != "name"} for index in snapshot["indexes"]
        ],
        "foreign_keys": snapshot["foreign_keys"],
    }


def _assert_schema_preserved(before: dict, after: dict, label: str) -> None:
    """The assertion under test. Fires when the recreation lost or changed
    anything beyond the two declared differences."""
    expected = _comparable(_apply_declared_differences(before))
    actual = _comparable(after)
    assert actual == expected, (
        f"{label}: the SQLite table recreation did not preserve the schema.\n"
        f"expected (anchor + declared differences): {expected}\n"
        f"actual   (after recreation):              {actual}"
    )


def test_sqlite_recreation_preserves_the_whole_schema(tmp_path: Path) -> None:
    """CONTROL: everything except the two declared differences survives."""
    url = _sqlite_url(tmp_path)
    db_path = tmp_path / "pos_tenant_default.db"

    _alembic(url, "upgrade", PREVIOUS_REVISION)
    before = _schema_snapshot(db_path)
    _alembic(url, "upgrade", "head")
    after = _schema_snapshot(db_path)

    _assert_schema_preserved(before, after, "CONTROL")
    unique_columns = [
        i["columns"] for i in after["indexes"] if i["unique"] and i["origin"] == "u"
    ]
    assert ["customer_id"] in unique_columns, after["indexes"]
    print(
        "GATE_CONTROL sqlite schema-equality: preserved "
        f"(columns={len(after['columns'])} indexes={len(after['indexes'])} "
        f"unique={unique_columns})"
    )


def test_losing_the_unique_in_the_recreation_turns_the_schema_gate_red(
    tmp_path: Path,
) -> None:
    """MUTATION: drop the UNIQUE from the recreation and prove the gate fires.

    The mutated DDL is the migration's OWN ``_SQLITE_RECREATE`` template with
    the UNIQUE line removed, so this exercises the real recreation path rather
    than an imitation of it. Nothing test-only is added to the migration.
    """
    spec = importlib.util.spec_from_file_location("_pos_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    mutated_template = "\n".join(
        line for line in module._SQLITE_RECREATE.splitlines()
        if "uq_pos_system_customers_customer" not in line
    ).replace("PRIMARY KEY (company_id),", "PRIMARY KEY (company_id)")
    assert "uq_pos_system_customers_customer" not in mutated_template
    assert "PRIMARY KEY (company_id)" in mutated_template

    url = _sqlite_url(tmp_path)
    db_path = tmp_path / "pos_tenant_default.db"
    _alembic(url, "upgrade", PREVIOUS_REVISION)
    before = _schema_snapshot(db_path)

    # Perform the recreation exactly as the migration does, minus the UNIQUE.
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            mutated_template.format(new="pos_system_customers_new", company_type="BIGINT")
            + ";\nINSERT INTO pos_system_customers_new (company_id, customer_id, created_at) "
            "SELECT company_id, customer_id, created_at FROM pos_system_customers;\n"
            "DROP TABLE pos_system_customers;\n"
            "ALTER TABLE pos_system_customers_new RENAME TO pos_system_customers;"
        )
        connection.commit()
    finally:
        connection.close()

    after = _schema_snapshot(db_path)
    with pytest.raises(AssertionError) as excinfo:
        _assert_schema_preserved(before, after, "MUTATION")
    message = str(excinfo.value)
    assert "did not preserve the schema" in message, message
    print(f"MUTATION_RED sqlite schema-equality: lost UNIQUE detected :: {message.splitlines()[0]}")

    # CONTROL on UNMUTATED code, same anchor, same assertion: proves the red
    # above came from the missing UNIQUE and not from a gate that is always red.
    control_path = tmp_path / "control.db"
    control_url = f"sqlite:///{control_path.as_posix()}"
    _alembic(control_url, "upgrade", PREVIOUS_REVISION)
    control_before = _schema_snapshot(control_path)
    _alembic(control_url, "upgrade", "head")
    _assert_schema_preserved(control_before, _schema_snapshot(control_path), "CONTROL")
    print("GATE_CONTROL sqlite schema-equality (unmutated, same assertion): green")


# ---------------------------------------------------------------------------
# BEYAN TARAMASI: göç docstring'i BIGINT'in "tamsayı ilgisini (typeof=integer)
# KORUDUĞUNU" beyan ediyor ve yeniden yaratmanın satırları koruduğu iddia
# edildi. İkisi de yalnız ELLE ölçülmüştü — yarın koşacak bir şey bırakmıyordu.
# Beyanlar burada DOĞRUDAN iddiaya bağlanıyor.
# ---------------------------------------------------------------------------

def test_recreation_preserves_rows_and_integer_affinity(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)
    db_path = tmp_path / "pos_tenant_default.db"
    _alembic(url, "upgrade", PREVIOUS_REVISION)

    seeded = [(1, 11, "2026-01-01"), (2, 22, "2026-01-02")]
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO companies (name,is_active,created_at) VALUES ('T1',1,'x')"
        )
        connection.execute(
            "INSERT INTO companies (name,is_active,created_at) VALUES ('T2',1,'x')"
        )
        connection.executemany(
            "INSERT INTO pos_system_customers (company_id,customer_id,created_at) "
            "VALUES (?,?,?)",
            seeded,
        )
        connection.commit()
    finally:
        connection.close()

    _alembic(url, "upgrade", "head")

    connection = sqlite3.connect(db_path)
    try:
        after = connection.execute(
            "SELECT company_id,customer_id,created_at FROM pos_system_customers "
            "ORDER BY company_id"
        ).fetchall()
        affinity = [
            r[0] for r in connection.execute(
                "SELECT DISTINCT typeof(company_id) FROM pos_system_customers"
            )
        ]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()

    assert after == seeded, f"recreation lost or altered rows: {seeded} -> {after}"
    # The docstring claims BIGINT keeps INTEGER affinity. Claim it exactly.
    assert affinity == ["integer"], f"declared typeof=integer, got {affinity}"
    assert integrity == "ok", integrity
    print(
        f"DECLARATION_PIN sqlite: rows={after} typeof={affinity} integrity={integrity}"
    )
