from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent


def test_supplier_price_bridge_f2_migration_replay_and_partial_repair(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'f2-migration.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "SUPPLIER_PRICE_F2_MIGRATION_OK" in completed.stdout, (
        completed.stdout + completed.stderr
    )


SCENARIO = r'''
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import settings

config = Config("alembic.ini")
command.upgrade(config, "head")
engine = create_engine(settings.database_url)

def replay_after_dropping(*columns):
    with engine.begin() as connection:
        for column in columns:
            connection.execute(text(
                f"ALTER TABLE supplier_import_profiles DROP COLUMN {column}"
            ))
        connection.execute(text(
            "UPDATE alembic_version SET version_num='20260728_0037'"
        ))
    command.upgrade(config, "head")
    return {
        column["name"]
        for column in inspect(engine).get_columns("supplier_import_profiles")
    }

assert "source_type" in replay_after_dropping("source_type")
assert "page_sections" in replay_after_dropping("page_sections")
assert {"source_type", "page_sections"}.issubset(
    replay_after_dropping("source_type", "page_sections")
)

with engine.begin() as connection:
    connection.execute(text("DROP TABLE supplier_import_profiles"))
    connection.execute(text(
        "UPDATE alembic_version SET version_num='20260728_0037'"
    ))
try:
    command.upgrade(config, "head")
except RuntimeError as exc:
    assert "20260728_0037 zinciri bozuk" in str(exc)
else:
    raise AssertionError("Eksik supplier_import_profiles sessiz atlandı")
print("SUPPLIER_PRICE_F2_MIGRATION_OK")
'''
