"""PostgreSQL ikizi: tarla sütun tipleri.

SQLite'ta `PRAGMA table_info` BEYAN EDİLEN tipi döndürür — yani migration ne
yazdıysa onu. SQLite'ın kendisi o tipi zorlamaz (tip yakınlığı/affinity), bu
yüzden SQLite koşusu yalnız "migration doğru yazılmış mı" sorusunu cevaplar.

PostgreSQL'de sütunun tipi GERÇEKTEN uygulanır ve `inspect` gerçek tipi verir.
Para/miktar sütununun NUMERIC olduğunu üretim diyalektinde doğrulamak, kuruş
kaybının imkânsız olduğunu gösteren tek kanıt.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "farm_invariant_guards", BACKEND / "tests" / "test_farm_invariant_guards.py"
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
run_column_type_smoke = _contract.run_column_type_smoke


def _pg_url() -> str:
    url = os.getenv("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("The farm column-type test database URL must point to PostgreSQL")
    return url


@pytest.mark.postgresql
def test_money_and_quantity_columns_are_numeric_postgresql() -> None:
    run_column_type_smoke(_pg_url())
