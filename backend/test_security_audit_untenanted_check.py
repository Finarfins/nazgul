"""Kapı: firmasız denetim satırı YALNIZ gerçek kimlik-öncesi olaya kalmalı.

``security_audit_logs.company_id`` 93 kiracı tablosu içinde tek NULL kabul
edeni. NULL hiçbir firmaya ait değildir ve ``company_id = ?`` süzen her okumanın
dışına düşer. Sütunu çıplak ``NOT NULL`` yapmak YANLIŞ olurdu (ölçüldü: yazıcı
istisnayı yutuyordu, satır sessizce kayboluyordu) — bu yüzden NULL kalır ama
ANLAMI kısıtlanır: kimliği ÇÖZÜLMÜŞ bir isteğin satırı firmasız olamaz.

``20260812_0059`` kısıtı ekler; bu dosya kapının kendisidir. Kapı MUTASYONLA
kanıtlanır: göç geri alındığında iddianın KIRMIZI olması gerekir ve kırmızının
çökmeden değil İDDİADAN geldiği ``AssertionError`` tipiyle doğrulanır.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent
PREVIOUS_REVISION = "20260812_0057"
TABLE = "security_audit_logs"
CONSTRAINT = "ck_security_audit_logs_untenanted_only_preauth"

_INSERT = (
    f"INSERT INTO {TABLE} "  # noqa: S608 - sabit tablo adı
    "(action, path, status_code, created_at, outcome, user_id, username, company_id) "
    "VALUES ('POST', '/api/kapi', 401, '2026-08-12', 'denied', :uid, :uname, :cid)"
)


def _alembic(database_url: str, *args: str) -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    env["PYTHONIOENCODING"] = "utf-8"
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

    İKİ YÖN BİRDEN çivilenir. Yalnız "reddedilmeli" iddiası, tabloyu tamamen
    yazılamaz yapan bir hatadan da geçerdi; yalnız "kabul edilmeli" iddiası
    kısıtın hiç olmamasından geçerdi.
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


def _sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'audit_untenanted.db').as_posix()}"


def test_untenanted_row_is_allowed_only_without_identity(tmp_path: Path) -> None:
    """CONTROL: on the migrated schema the constraint holds in both directions."""
    url = _sqlite_url(tmp_path)
    _alembic(url, "upgrade", "head")
    engine = create_engine(url)
    try:
        _gate(engine, "CONTROL")
        print("GATE_CONTROL sqlite: identity+NULL rejected, pre-auth row accepted")
    finally:
        engine.dispose()


def test_dropping_the_constraint_turns_the_gate_red(tmp_path: Path) -> None:
    """MUTATION: the migration's own downgrade must make the gate red.

    Mutasyon elle yapılmış bir taklit değil göçün KENDİ ``downgrade``'i; böylece
    kanıtlanan şey TAM OLARAK bu göçün deliği kapattığıdır.
    """
    url = _sqlite_url(tmp_path)
    _alembic(url, "upgrade", "head")
    engine = create_engine(url)
    try:
        _gate(engine, "MUTATION baseline")

        _alembic(url, "downgrade", PREVIOUS_REVISION)

        with pytest.raises(AssertionError) as excinfo:
            _gate(engine, "MUTATION")
        message = str(excinfo.value)
        assert "was ACCEPTED — an identified request lost its tenant" in message, message
        print(f"MUTATION_RED sqlite: {message}")

        # Yeniden uygulamak yeşile döndürmeli: kırmızıyı kapatan şeyin ORTAM
        # değil GÖÇ olduğunu ayırt eder.
        _alembic(url, "upgrade", "head")
        _gate(engine, "MUTATION restored")
        print("GATE_CONTROL sqlite after re-upgrade: constrained again")
    finally:
        engine.dispose()


def test_migration_refuses_to_run_over_violating_legacy_rows(tmp_path: Path) -> None:
    """Eski ihlalli satır varsa göç SESSİZCE SİLMEZ, anlaşılır biçimde durur.

    Bir denetim satırını silmek kapatmaya çalıştığımız kusurun daha kötüsü;
    üyelikten firma tamamlamak ise #57'nin kaldırdığı KİRACI UYDURMASI olurdu.
    Üçüncü yol: karar operatörün, göç durur.
    """
    url = _sqlite_url(tmp_path)
    _alembic(url, "upgrade", "head")
    _alembic(url, "downgrade", PREVIOUS_REVISION)

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(_INSERT), {"uid": 9, "uname": "eski", "cid": None})
    finally:
        engine.dispose()

    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=BACKEND, env=env, text=True, capture_output=True, timeout=300,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode != 0, "migration silently accepted a violating legacy row"
    combined = result.stdout + result.stderr
    assert "eski satır var" in combined, combined

    # SATIR HÂLÂ ORADA. Göçün durması, satırı yok etmesinden farklıdır.
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            survivors = connection.execute(
                text(
                    f"SELECT COUNT(*) FROM {TABLE} "  # noqa: S608
                    "WHERE company_id IS NULL AND user_id IS NOT NULL"
                )
            ).scalar_one()
        assert survivors == 1, "the migration destroyed the legacy audit row"
        print(f"GATE_CONTROL sqlite: migration halted, {survivors} legacy row preserved")
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# ŞEMA EŞİTLİĞİ KAPISI
#
# Göç SQLite'ta tabloyu KOMPLE yeniden yaratıyor. Davranış kapısı yalnız CHECK'i
# koruyor; yeniden yaratma sırasında bir İNDEKS ya da SÜTUN düşse bile o kapı
# yeşil kalırdı. Burada öncesi ile sonrası, göçün kendi DDL'inden BAĞIMSIZ bir
# çıpaya karşı karşılaştırılır: çıpa, göç öncesi revizyonun gerçek katalog
# durumudur, SQLite'ın kendi PRAGMA'larıyla okunur.
#
# BİLEREK beklenen TEK fark CHECK kısıtının kendisidir.
# ---------------------------------------------------------------------------


def _schema_snapshot(db_path: Path) -> dict:
    connection = sqlite3.connect(db_path)
    try:
        columns = [
            {"name": r[1], "type": r[2].upper(), "notnull": r[3], "default": r[4], "pk": r[5]}
            for r in connection.execute(f"PRAGMA table_info({TABLE})")
        ]
        indexes = []
        for row in connection.execute(f"PRAGMA index_list({TABLE})"):
            name, unique, origin = row[1], row[2], row[3]
            cols = [r[2] for r in connection.execute(f'PRAGMA index_info("{name}")')]
            indexes.append({"name": name, "unique": unique, "origin": origin, "columns": cols})
        return {
            "columns": columns,
            "indexes": sorted(indexes, key=lambda i: (str(i["columns"]), i["origin"])),
        }
    finally:
        connection.close()


def test_sqlite_rebuild_preserves_everything_except_the_check(tmp_path: Path) -> None:
    database = tmp_path / "audit_untenanted.db"
    url = f"sqlite:///{database.as_posix()}"

    # ÖNCE head, SONRA 0057'ye geri. Doğrudan ``upgrade 0057`` YETMEZ: baseline
    # şemayı modelden kuruyor ve kısıt artık modelin parçası, yani taze bir
    # zincirde tablo 0057'de de kısıtla doğar; göç "zaten var" deyip hiçbir şey
    # yapmaz ve bu kapı BOŞ geçerdi. Geri dönüş kısıtı gerçekten kaldırır, böylece
    # sonraki upgrade GERÇEK yeniden kurulum yolunu yürür — üretimdeki yol da bu.
    _alembic(url, "upgrade", "head")
    _alembic(url, "downgrade", PREVIOUS_REVISION)
    before = _schema_snapshot(database)
    assert CONSTRAINT not in (
        sqlite3.connect(database)
        .execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,))
        .fetchone()[0]
    ), "downgrade did not actually remove the constraint; the gate would be vacuous"

    _alembic(url, "upgrade", "head")
    after = _schema_snapshot(database)

    assert after["columns"] == before["columns"], (
        "the SQLite rebuild changed the column layout:\n"
        f"before={before['columns']}\nafter={after['columns']}"
    )
    assert after["indexes"] == before["indexes"], (
        "the SQLite rebuild lost or altered an index:\n"
        f"before={before['indexes']}\nafter={after['indexes']}"
    )

    # Ve eklenen tek şey GERÇEKTEN orada: katalogdaki tablo DDL'i kısıt adını
    # taşımalı. Yukarıdaki iki iddia "hiçbir şey değişmedi"yi kanıtlıyor; bu
    # üçüncüsü olmasa göç HİÇ ÇALIŞMASA da dosya yeşil kalırdı.
    connection = sqlite3.connect(database)
    try:
        ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone()[0]
    finally:
        connection.close()
    assert CONSTRAINT in ddl, ddl
    print(f"GATE_CONTROL sqlite: rebuild preserved columns+indexes, added {CONSTRAINT}")
