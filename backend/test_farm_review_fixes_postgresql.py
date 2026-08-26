import importlib.util
import os
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

psycopg = pytest.importorskip("psycopg")


BACKEND = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "farm_review_fixes_contract",
    BACKEND / "tests" / "test_farm_review_fixes.py",
)
assert _spec and _spec.loader
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_review_smoke = _contract.run_review_smoke


def _postgresql_url() -> str:
    raw_url = os.getenv("APP_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("APP_TEST_DATABASE_URL is required for PostgreSQL tests")
    if not raw_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must be a PostgreSQL URL")

    base_url = make_url(raw_url)
    target_database = f"{base_url.database}_farm_review_fixes"
    admin_url = base_url.set(drivername="postgresql", database="postgres")
    with psycopg.connect(
        admin_url.render_as_string(hide_password=False), autocommit=True
    ) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{target_database}" WITH (FORCE)')
        connection.execute(f'CREATE DATABASE "{target_database}"')
    return base_url.set(database=target_database).render_as_string(hide_password=False)


@pytest.mark.postgresql
def test_farm_review_fixes_postgresql() -> None:
    run_review_smoke(_postgresql_url())
