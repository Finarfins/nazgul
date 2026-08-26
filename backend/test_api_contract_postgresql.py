"""API kontrat kapısının PostgreSQL 16 ikizi.

Prod POS çökmesi tam olarak SQLite/PostgreSQL ayrışmasından sızdı: SQLite'ta
TEXT kolonlar string döndürürken PostgreSQL NUMERIC kolonları Decimal → JSON
sayısı üretiyordu. Kontrat kontrollerinin aynısı burada gerçek PostgreSQL
üzerinde koşar; iki motor aynı kanonik JSON şeklini üretmek zorundadır.
"""
from __future__ import annotations

import os

import pytest


@pytest.mark.postgresql
def test_api_contract_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("CONTRACT_TEST_DATABASE_URL") or os.environ.get(
        "APP_TEST_DATABASE_URL"
    )
    if not database_url:
        pytest.skip("CONTRACT_TEST_DATABASE_URL or APP_TEST_DATABASE_URL is required")
    monkeypatch.setenv("DATABASE_URL", database_url)

    from fastapi.testclient import TestClient
    from app.main import app
    from test_api_contract import run_contract_checks

    with TestClient(app) as client:
        run_contract_checks(client)
