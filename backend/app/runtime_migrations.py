from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .config import settings
from .db import engine

logger = logging.getLogger("yerel_hesap.migrations")

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
ALEMBIC_DIR = BACKEND_DIR / "alembic"
POSTGRES_MIGRATION_LOCK_KEY = 79500329


def _alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return config


def expected_revision() -> str | None:
    """Return the single Alembic head expected by this application build."""
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    if not heads:
        return None
    if len(heads) != 1:
        raise RuntimeError(f"Birden fazla Alembic head bulundu: {', '.join(heads)}")
    return heads[0]


def current_revision(target_engine: Engine = engine) -> str | None:
    """Read the applied Alembic revision without mutating the database."""
    with target_engine.connect() as connection:
        table_exists = target_engine.dialect.has_table(connection, "alembic_version")
        if not table_exists:
            return None
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()


def migration_status(target_engine: Engine = engine) -> dict[str, str | bool | None]:
    expected = expected_revision()
    current = current_revision(target_engine)
    return {"current": current, "expected": expected, "up_to_date": current == expected}


def _acquire_postgres_lock(connection: Connection, timeout_seconds: int) -> None:
    deadline = time.monotonic() + max(timeout_seconds, 1)
    while True:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": POSTGRES_MIGRATION_LOCK_KEY},
        ).scalar_one()
        if acquired:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "PostgreSQL migration kilidi zaman aşımına uğradı; "
                "başka bir uygulama örneği migration çalıştırıyor olabilir"
            )
        time.sleep(0.5)


def _release_postgres_lock(connection: Connection) -> None:
    try:
        connection.execute(
            text("SELECT pg_advisory_unlock(:lock_key)"),
            {"lock_key": POSTGRES_MIGRATION_LOCK_KEY},
        )
    except Exception:
        # A pooled PostgreSQL connection must never return to the pool while it
        # may still own a session-level advisory lock.
        connection.invalidate()
        logger.exception("PostgreSQL migration kilidi güvenli biçimde bırakılamadı")
        raise


@contextmanager
def database_bootstrap_lock(target_engine: Engine = engine) -> Iterator[None]:
    """Serialize the *entire* PostgreSQL schema/bootstrap phase.

    Alembic used to hold the advisory lock only while applying revisions. The
    legacy SQLAlchemy ``initialize_*`` functions run before Alembic and can also
    execute DDL or seed rows. Two replicas starting together could therefore
    race before either one reached the migration lock. Holding the same
    cluster-wide advisory lock around both initialization and Alembic closes
    that window. SQLite remains a no-op because the local launcher is a
    single-process deployment and SQLite already has file-level locking.
    """

    if target_engine.dialect.name != "postgresql":
        yield
        return

    with target_engine.connect() as connection:
        _acquire_postgres_lock(connection, settings.migration_lock_timeout_seconds)
        try:
            yield
        finally:
            _release_postgres_lock(connection)


def run_database_migrations(
    target_engine: Engine = engine, *, acquire_lock: bool = True
) -> None:
    """Upgrade the database to head with an optional bounded replica lock.

    ``acquire_lock=False`` is used only when the caller already owns
    :func:`database_bootstrap_lock`. Keeping the default preserves safe direct
    use from maintenance scripts and tests.
    """
    config = _alembic_config()

    def _upgrade() -> None:
        with target_engine.connect() as connection:
            config.attributes["connection"] = connection
            try:
                command.upgrade(config, "head")
            finally:
                config.attributes.pop("connection", None)

    if acquire_lock:
        with database_bootstrap_lock(target_engine):
            _upgrade()
    else:
        _upgrade()

    status = migration_status(target_engine)
    if not status["up_to_date"]:
        raise RuntimeError(
            "Veritabanı migration sürümü uygulama sürümüyle eşleşmiyor: "
            f"current={status['current']!r}, expected={status['expected']!r}"
        )
    logger.info("Veritabanı migration seviyesi doğrulandı", extra=status)
