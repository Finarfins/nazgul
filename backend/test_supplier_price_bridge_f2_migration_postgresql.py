from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

BACKEND = Path(__file__).resolve().parent


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("PostgreSQL migration replay requires APP_TEST_DATABASE_URL")
    return url


@pytest.mark.postgresql
def test_supplier_price_bridge_f2_replays_and_repairs_partial_columns() -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    try:
        command.upgrade(config, "head")
        def replay_after_dropping(*columns: str) -> set[str]:
            with engine.begin() as connection:
                for column in columns:
                    connection.execute(
                        text(
                            "ALTER TABLE supplier_import_profiles "
                            f"DROP COLUMN {column}"
                        )
                    )
                connection.execute(
                    text(
                        "UPDATE alembic_version "
                        "SET version_num='20260728_0037'"
                    )
                )
            command.upgrade(config, "head")
            return {
                column["name"]
                for column in inspect(engine).get_columns(
                    "supplier_import_profiles"
                )
            }

        assert "source_type" in replay_after_dropping("source_type")
        assert "page_sections" in replay_after_dropping("page_sections")
        assert {"source_type", "page_sections"}.issubset(
            replay_after_dropping("source_type", "page_sections")
        )
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE supplier_import_profiles CASCADE"))
            connection.execute(
                text("UPDATE alembic_version SET version_num='20260728_0037'")
            )
        with pytest.raises(RuntimeError, match="20260728_0037 zinciri bozuk"):
            command.upgrade(config, "head")
    finally:
        engine.dispose()
