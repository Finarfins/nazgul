from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_v3_seasonal_plan import SCENARIO

BACKEND = Path(__file__).resolve().parent


@pytest.mark.postgresql
def test_seasonal_plan_postgresql() -> None:
    database_url = os.environ.get("SEASONAL_PLAN_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("SEASONAL_PLAN_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required")
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(BACKEND),
    )
    assert "SEASONAL_PLAN_OK" in completed.stdout, completed.stdout + completed.stderr
