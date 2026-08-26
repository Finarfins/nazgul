"""İş günü İstanbul sabitlemesinin PostgreSQL ikizi.

Senaryo gövdesi SQLite tarafıyla birebir aynı; buradaki fark motorun gerçekten
PostgreSQL olması. Bu önemli, çünkü değiştirilen yerlerden ikisi doğrudan
dialect'e bağlıydı: ``CURRENT_DATE`` PostgreSQL'de sunucunun (UTC) günüdür ve
``orders.order_date`` metin kolonu üzerindeki karşılaştırmalar iki motorda
farklı davranabilir.
"""
from __future__ import annotations

import os

import pytest

from test_istanbul_business_date import run_business_date_smoke


def _database_url() -> str:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    return database_url


@pytest.mark.postgresql
def test_business_date_endpoints_postgresql() -> None:
    run_business_date_smoke(_database_url())
