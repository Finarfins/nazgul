from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from test_smart_part_search_endpoint import run_endpoint_smoke


def test_smart_part_search_endpoint_on_postgresql() -> None:
    database_url = os.getenv("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL is required")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("APP_TEST_DATABASE_URL must point to PostgreSQL")
    run_endpoint_smoke(database_url)

    engine = create_engine(database_url)
    expected_indexes = {
        "ix_products_company_oem_normalized",
        "ix_products_company_product_code_normalized",
        "ix_products_company_barcode_normalized",
        "ix_products_company_alternative_oem_normalized",
    }
    with engine.connect() as connection:
        indexes = set(
            connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname=current_schema() AND tablename='products'"
                )
            ).scalars()
        )
        assert expected_indexes <= indexes

        company = connection.execute(
            text(
                "SELECT company_id FROM products "
                "WHERE product_code='ABCD' ORDER BY id LIMIT 1"
            )
        ).scalar_one()
        connection.execute(text("SET enable_seqscan=off"))
        plan = "\n".join(
            connection.execute(
                text(
                    """EXPLAIN SELECT id FROM products
                    WHERE company_id=:cid
                    AND regexp_replace(
                        UPPER(COALESCE(product_code,'')),
                        '[^A-Z0-9]+','','g'
                    ) IN ('ABCD')"""
                ),
                {"cid": company},
            ).scalars()
        )
        assert "Index Scan" in plan or "Bitmap Index Scan" in plan
        assert "Seq Scan" not in plan
    engine.dispose()
