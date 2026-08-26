from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.main import app as _app  # noqa: F401 - import runs migrations on the fresh CI schema


def test_lower_email_unique_index_rejects_case_variant() -> None:
    database_url = os.environ.get("APP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("APP_TEST_DATABASE_URL tanımlı değil")
    engine = create_engine(database_url)
    indexes = {item["name"] for item in inspect(engine).get_indexes("app_users")}
    assert "uq_app_users_email_lower" in indexes

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "INSERT INTO app_users "
                    "(username,email,email_verified,display_name,password_hash,"
                    "role,is_active,created_at,must_change_password) VALUES "
                    "('lowercase-user','same@example.com',TRUE,'İlk Kullanıcı',"
                    "'unused','rapor',TRUE,CURRENT_TIMESTAMP,FALSE)"
                )
            )
            with pytest.raises(IntegrityError) as exc:
                connection.execute(
                    text(
                        "INSERT INTO app_users "
                        "(username,email,email_verified,display_name,password_hash,"
                        "role,is_active,created_at,must_change_password) VALUES "
                        "('case-variant-user','SAME@example.com',TRUE,'Test Kullanıcı',"
                        "'unused','rapor',TRUE,CURRENT_TIMESTAMP,FALSE)"
                    )
                )
            print(f"POSTGRES_CASE_VARIANT_REJECTED={exc.value.orig}")
        finally:
            transaction.rollback()
