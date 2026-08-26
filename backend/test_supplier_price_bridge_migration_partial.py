from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_supplier_price_bridge_repairs_partial_schema(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'partial.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        env=env,
        capture_output=True,
        text=True,
        cwd=BACKEND,
        timeout=120,
    )
    assert "SUPPLIER_PRICE_PARTIAL_SCHEMA_OK" in completed.stdout, (
        completed.stdout + completed.stderr
    )


SCENARIO = r'''
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import settings

tables = {
    "supplier_import_profiles",
    "supplier_price_imports",
    "supplier_price_import_lines",
    "supplier_part_xrefs",
    "supplier_part_prices",
}
config = Config("alembic.ini")
command.upgrade(config, "head")
engine = create_engine(settings.database_url)
with engine.begin() as connection:
    connection.execute(text("DROP TABLE supplier_part_prices"))
    connection.execute(text("DROP TABLE supplier_part_xrefs"))
    connection.execute(text("DROP TABLE supplier_price_import_lines"))
    connection.execute(text("DROP TABLE supplier_price_imports"))
    connection.execute(
        text("UPDATE alembic_version SET version_num='20260728_0036'")
    )
command.upgrade(config, "head")
assert tables.issubset(set(inspect(engine).get_table_names()))
print("SUPPLIER_PRICE_PARTIAL_SCHEMA_OK")
'''
