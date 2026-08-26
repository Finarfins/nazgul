from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parent


@pytest.mark.postgresql
def test_supplier_price_profiles_postgresql() -> None:
    database_url = os.environ.get("SUPPLIER_PRICE_IMPORT_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("SUPPLIER_PRICE_IMPORT_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required")
    from test_v3_supplier_price_profiles import SCENARIO

    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        env=env,
        capture_output=True,
        text=True,
        cwd=BACKEND,
        timeout=120,
    )
    assert "SUPPLIER_PRICE_PROFILES_OK" in completed.stdout, (
        completed.stdout + completed.stderr
    )
