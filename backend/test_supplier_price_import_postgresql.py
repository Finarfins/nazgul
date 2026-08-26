from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent


@pytest.mark.postgresql
def test_supplier_price_import_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("SUPPLIER_PRICE_IMPORT_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("SUPPLIER_PRICE_IMPORT_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required")
    monkeypatch.setenv("DATABASE_URL", database_url)
    # Import only after DATABASE_URL is set; importing the scenario module also
    # imports app.auth/app.db during collection.
    from test_v3_supplier_price_import import SCENARIO

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
    assert "SUPPLIER_PRICE_IMPORT_OK" in completed.stdout, completed.stdout + completed.stderr


@pytest.mark.postgresql
def test_supplier_price_bridge_roundtrip_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("SUPPLIER_PRICE_IMPORT_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("SUPPLIER_PRICE_IMPORT_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required")
    from test_v3_supplier_price_bridge import SCENARIO

    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(BACKEND),
        timeout=120,
    )
    assert "SUPPLIER_PRICE_BRIDGE_OK" in completed.stdout, completed.stdout + completed.stderr


@pytest.mark.postgresql
def test_tampar_slice_tier1_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("SUPPLIER_PRICE_IMPORT_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("SUPPLIER_PRICE_IMPORT_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required")
    from test_v3_supplier_price_tier1 import SCENARIO

    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", SCENARIO],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(BACKEND),
        timeout=120,
    )
    assert "TAMPAR_TIER1_OK" in completed.stdout, completed.stdout + completed.stderr
