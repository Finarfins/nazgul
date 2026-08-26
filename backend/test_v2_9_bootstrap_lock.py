from __future__ import annotations

from pathlib import Path

from app import runtime_migrations


class _Dialect:
    def __init__(self, name: str) -> None:
        self.name = name


class _Connection:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self):
        self.events.append("connect")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.events.append("disconnect")
        return False


class _Engine:
    def __init__(self, dialect_name: str, events: list[str]) -> None:
        self.dialect = _Dialect(dialect_name)
        self.events = events

    def connect(self):
        return _Connection(self.events)


def test_postgresql_bootstrap_lock_encloses_full_initialization(monkeypatch) -> None:
    events: list[str] = []
    engine = _Engine("postgresql", events)

    monkeypatch.setattr(
        runtime_migrations,
        "_acquire_postgres_lock",
        lambda connection, timeout: events.append("acquire"),
    )
    monkeypatch.setattr(
        runtime_migrations,
        "_release_postgres_lock",
        lambda connection: events.append("release"),
    )

    with runtime_migrations.database_bootstrap_lock(engine):
        events.append("initialize")

    assert events == ["connect", "acquire", "initialize", "release", "disconnect"]


def test_sqlite_bootstrap_lock_does_not_open_extra_connection() -> None:
    events: list[str] = []
    engine = _Engine("sqlite", events)
    with runtime_migrations.database_bootstrap_lock(engine):
        events.append("initialize")
    assert events == ["initialize"]


def test_main_uses_single_outer_bootstrap_lock() -> None:
    source = (Path(__file__).parent / "app" / "main.py").read_text(encoding="utf-8")
    assert "with database_bootstrap_lock(engine):" in source
    assert "run_database_migrations(engine, acquire_lock=False)" in source



def test_compose_requires_external_secrets_and_hardens_app_container() -> None:
    root = Path(__file__).parent.parent
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")

    assert "${POSTGRES_PASSWORD:?" in compose
    assert "${DATABASE_URL:?" in compose
    assert "POSTGRES_PASSWORD: yerelhesap" not in compose
    assert "read_only: true" in compose
    assert "cap_drop:" in compose and "- ALL" in compose
    assert "no-new-privileges:true" in compose
    assert ".env.docker" in gitignore
    assert "**/.env" in dockerignore
    assert "**/.env.*" in dockerignore
