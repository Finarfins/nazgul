from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


BACKEND = Path(__file__).resolve().parent
REVISION = "20260728_0037"
PREDECESSOR = "20260728_0036"
TABLES = {
    "supplier_import_profiles",
    "supplier_price_imports",
    "supplier_price_import_lines",
    "supplier_part_xrefs",
    "supplier_part_prices",
}


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("PostgreSQL migration replay requires APP_TEST_DATABASE_URL")
    return url


def _stamp_predecessor(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num=:revision"),
            {"revision": PREDECESSOR},
        )


@pytest.mark.postgresql
def test_supplier_price_bridge_migration_replays_partial_schema_and_repairs_index() -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    try:
        command.upgrade(config, "head")
        _stamp_predecessor(engine)
        command.upgrade(config, "head")
        print(f"{REVISION}_FULL_SCHEMA_REPLAY_OK")

        with engine.begin() as connection:
            connection.execute(text("DROP TABLE supplier_part_prices"))
            connection.execute(text("DROP TABLE supplier_part_xrefs"))
            connection.execute(text("DROP TABLE supplier_price_import_lines"))
            connection.execute(text("DROP TABLE supplier_price_imports"))
        _stamp_predecessor(engine)
        command.upgrade(config, "head")

        assert TABLES.issubset(set(inspect(engine).get_table_names()))
        print(f"{REVISION}_PARTIAL_SCHEMA_REPAIRED")

        with engine.begin() as connection:
            connection.execute(
                text("DROP INDEX ix_supplier_price_import_line_state")
            )
        _stamp_predecessor(engine)
        command.upgrade(config, "head")

        indexes = {
            index["name"]
            for index in inspect(engine).get_indexes(
                "supplier_price_import_lines"
            )
        }
        assert "ix_supplier_price_import_line_state" in indexes
        print(f"{REVISION}_INDEX_MUTATION_REPAIRED")
    finally:
        engine.dispose()
