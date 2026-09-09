"""SEC-10: Small hardening bundle tests.

1. SecretStr settings wrap audit (bootstrap_admin_password, turnstile_secret_key;
   exempting database_url and turnstile_site_key by name).
2. verify-email split into non-mutating GET and mutating POST.
3. quoteattr escaping in UBL-TR XML for embedded XSLT filename attribute.
4. restore_deleted column intersection with live table columns via inspect.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import defusedxml.ElementTree as DefusedET
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.change_history import entity_change_logs, restore_deleted
from app.config import Settings
from app.einvoice.ubl_xml import build_invoice_xml
from app.email_verification import token_digest
from app.routers import auth as auth_router

utcnow = lambda: datetime.now(timezone.utc)


# ===========================================================================
# 1. SecretStr Tests
# ===========================================================================

def test_no_secret_settings_field_is_bare_str() -> None:
    """Asserts no Settings field whose name matches secret-like tokens is bare str.

    database_url and turnstile_site_key are exempted BY NAME.
    """
    EXEMPTED_BY_NAME = {"database_url", "turnstile_site_key"}
    secret_pattern = re.compile(r"(password|secret|token|api_key|sirri)", re.IGNORECASE)

    for field_name, field_info in Settings.model_fields.items():
        if field_name in EXEMPTED_BY_NAME:
            continue
        if secret_pattern.search(field_name):
            annotation = field_info.annotation
            # Must not be a bare str or Optional[str]
            assert annotation != str and annotation != (str | None), (
                f"Settings field '{field_name}' annotation ({annotation}) is bare str; "
                f"must be wrapped in SecretStr."
            )


def test_settings_secretstr_wrapping_and_effective_password() -> None:
    """Verify bootstrap_admin_password and turnstile_secret_key wrap as SecretStr."""
    s = Settings(
        bootstrap_admin_password="MySecretPassword!2026",
        turnstile_secret_key="0x4AAAAAAABkMYinukE8nzY_test",
    )
    assert isinstance(s.bootstrap_admin_password, SecretStr)
    assert s.bootstrap_admin_password.get_secret_value() == "MySecretPassword!2026"
    assert s.effective_bootstrap_admin_password == "MySecretPassword!2026"

    assert isinstance(s.turnstile_secret_key, SecretStr)
    assert s.turnstile_secret_key.get_secret_value() == "0x4AAAAAAABkMYinukE8nzY_test"

    # Default admin password fallback remains str for local compatibility
    s_default = Settings(bootstrap_admin_password=None)
    assert s_default.bootstrap_admin_password is None
    assert s_default.effective_bootstrap_admin_password == "admin123"


# ===========================================================================
# 2. verify-email Split Tests
# ===========================================================================

@pytest.fixture
def memory_db(tmp_path: Path) -> Session:
    import sqlite3
    from contextlib import closing

    db_file = tmp_path / "sec10_fixture.db"
    schema_sql = (
        "CREATE TABLE app_users ("
        "id INTEGER PRIMARY KEY, "
        "username TEXT, "
        "email TEXT, "
        "email_verified BOOLEAN DEFAULT 0"
        ");\n"
        "CREATE TABLE email_verification_tokens ("
        "id INTEGER PRIMARY KEY, "
        "user_id INTEGER, "
        "token_hash TEXT, "
        "created_at DATETIME, "
        "expires_at DATETIME, "
        "used_at DATETIME"
        ");\n"
        "CREATE TABLE companies ("
        "id INTEGER PRIMARY KEY, "
        "name TEXT"
        ");\n"
        "CREATE TABLE customers ("
        "id INTEGER PRIMARY KEY, "
        "company_id INTEGER, "
        "name TEXT, "
        "phone TEXT, "
        "email TEXT, "
        "tax_number TEXT, "
        "opening_balance NUMERIC, "
        "risk_limit NUMERIC, "
        "payment_term_days INTEGER, "
        "is_active BOOLEAN"
        ");\n"
        "CREATE TABLE entity_change_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "company_id INTEGER, "
        "entity_type TEXT, "
        "entity_id INTEGER, "
        "action TEXT, "
        "before_json TEXT, "
        "after_json TEXT, "
        "changed_fields_json TEXT, "
        "request_id TEXT, "
        "actor_user_id INTEGER, "
        "actor_username TEXT, "
        "created_at TEXT, "
        "restored_from_log_id INTEGER"
        ");"
    )
    with closing(sqlite3.connect(db_file)) as s_conn:
        s_conn.executescript(schema_sql)

    engine = create_engine(f"sqlite:///{db_file.as_posix()}")
    session_cls = sessionmaker(bind=engine)
    session = session_cls()
    yield session
    session.close()
    engine.dispose()


def test_verify_email_get_is_non_mutating_and_idempotent(memory_db: Session) -> None:
    """GET /api/auth/verify-email is non-mutating and idempotent.

    Calling GET multiple times (e.g. by mail crawlers or link prefetchers)
    validates the token without consuming it (returns 200 and leaves used_at None).

    MUTATION DYING TEST: If GET is mutated to consume the token (as it used to),
    the second GET call will fail with 400 instead of returning 200.
    """
    raw_token = "valid-test-token-48-chars-long-abcdefghijklmnopqrstuvwxyz"
    digest = token_digest(raw_token)
    now = utcnow()

    # Create user
    memory_db.execute(
        text("INSERT INTO app_users (id, username, email, email_verified) VALUES (1, 'u1', 'u1@test.com', 0)")
    )
    # Create verification token
    memory_db.execute(
        text(
            "INSERT INTO email_verification_tokens (id, user_id, token_hash, created_at, expires_at, used_at) "
            "VALUES (1, 1, :hash, :created, :expires, NULL)"
        ),
        {"hash": digest, "created": now, "expires": now + timedelta(hours=24)},
    )
    memory_db.commit()

    # 1) First GET call: returns 200
    res1 = auth_router.verify_email_landing(token=raw_token, db=memory_db)
    assert res1["valid"] is True

    # Check DB state: token is NOT consumed, user is NOT verified
    row1 = memory_db.execute(
        text("SELECT used_at FROM email_verification_tokens WHERE id = 1")
    ).scalar_one()
    assert row1 is None

    verified1 = memory_db.execute(
        text("SELECT email_verified FROM app_users WHERE id = 1")
    ).scalar_one()
    assert bool(verified1) is False

    # 2) Second GET call: still returns 200 (idempotent, unburned)
    res2 = auth_router.verify_email_landing(token=raw_token, db=memory_db)
    assert res2["valid"] is True


def test_verify_email_post_consumes_token_and_second_post_fails(memory_db: Session) -> None:
    """POST /api/auth/verify-email consumes the token and verifies email."""
    raw_token = "consume-test-token-48-chars-long-abcdefghijklmnopqrst"
    digest = token_digest(raw_token)
    now = utcnow()

    memory_db.execute(
        text("INSERT INTO app_users (id, username, email, email_verified) VALUES (2, 'u2', 'u2@test.com', 0)")
    )
    memory_db.execute(
        text(
            "INSERT INTO email_verification_tokens (id, user_id, token_hash, created_at, expires_at, used_at) "
            "VALUES (2, 2, :hash, :created, :expires, NULL)"
        ),
        {"hash": digest, "created": now, "expires": now + timedelta(hours=24)},
    )
    memory_db.commit()

    # First POST: consumes token
    payload = auth_router.VerifyEmailPayload(token=raw_token)
    res = auth_router.verify_email(payload=payload, db=memory_db)
    assert "doğrulandı" in res["message"]

    # Verify DB state
    row = memory_db.execute(
        text("SELECT used_at FROM email_verification_tokens WHERE id = 2")
    ).scalar_one()
    assert row is not None

    verified = memory_db.execute(
        text("SELECT email_verified FROM app_users WHERE id = 2")
    ).scalar_one()
    assert bool(verified) is True

    # Second POST: must fail with 400 (already used)
    with pytest.raises(HTTPException) as exc_info:
        auth_router.verify_email(payload=payload, db=memory_db)
    assert exc_info.value.status_code == 400

    # Subsequent GET: must also fail with 400
    with pytest.raises(HTTPException) as exc_get:
        auth_router.verify_email_landing(token=raw_token, db=memory_db)
    assert exc_get.value.status_code == 400


# ===========================================================================
# 3. quoteattr XML Escaping Tests
# ===========================================================================

def test_ubl_xml_quoteattr_hostile_invoice_id() -> None:
    """Ensure hostile characters in invoice_id are properly escaped by quoteattr.

    Without quoteattr, characters like '"' and '<' break the filename attribute
    and cause an XML parsing error.
    """
    hostile_id = 'GIB2026"evil<tag>&attr'
    payload = {
        "invoice_number": hostile_id,
        "uuid": "12345678-1234-1234-1234-123456789012",
        "profile_id": "TICARIFATURA",
        "issue_date": "2026-09-09",
        "supplier": {"vkn": "1234567890", "name": "Satıcı A.Ş.", "address": "Adres"},
        "customer": {"vkn_tckn": "9876543210", "name": "Alıcı Ltd.", "address": "Adres"},
        "lines": [
            {
                "id": "1",
                "name": "Ürün 1",
                "quantity": "10",
                "unit_code": "C62",
                "unit_price": "100.00",
                "line_extension_amount": "1000.00",
                "tax_amount": "200.00",
                "tax_rate": "20",
            }
        ],
        "monetary_total": {
            "line_extension_amount": "1000.00",
            "tax_exclusive_amount": "1000.00",
            "tax_inclusive_amount": "1200.00",
            "tax_total": "200.00",
            "payable_amount": "1200.00",
        },
        "tax_subtotals": [
            {
                "taxable_amount": "1000.00",
                "tax_amount": "200.00",
                "tax_rate": "20",
            }
        ],
    }

    raw_xml = build_invoice_xml(payload)

    # Must be valid XML parsable by DefusedET without ParseError
    root = DefusedET.fromstring(raw_xml)
    assert root is not None

    # Check that the filename attribute in EmbeddedDocumentBinaryObject was escaped
    ns = {
        "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
        "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    }
    attachment_nodes = root.findall(".//cac:Attachment/cbc:EmbeddedDocumentBinaryObject", ns)
    assert len(attachment_nodes) == 1
    filename = attachment_nodes[0].attrib.get("filename")
    assert filename == f"{hostile_id}.xslt"


# ===========================================================================
# 4. restore_deleted Column Intersection Tests
# ===========================================================================

def test_restore_deleted_drops_extraneous_columns(memory_db: Session, caplog: pytest.LogCaptureFixture) -> None:
    """Extra legacy column in before_json is dropped and logged with warning.

    MUTATION DYING TEST: If column intersection is removed (using raw columns = list(payload)),
    SQLite will fail on INSERT with 'table customers has no column named ghost_legacy_col'
    and this test will die.
    """
    company_id = 1
    memory_db.execute(
        text("INSERT INTO companies (id, name) VALUES (1, 'Firma 1')")
    )
    memory_db.commit()

    # Dump payload has a legacy column not in the live 'customers' table
    dump_payload = {
        "id": 101,
        "company_id": company_id,
        "name": "Eski Müşteri",
        "phone": "5551234567",
        "email": "eski@example.com",
        "tax_number": "1234567890",
        "opening_balance": "0.00",
        "risk_limit": "0.00",
        "payment_term_days": 0,
        "is_active": 1,
        "ghost_legacy_col": "should_be_dropped",
        "another_dropped_col": "extra_data",
    }

    memory_db.execute(
        insert(entity_change_logs).values(
            company_id=company_id,
            entity_type="customer",
            entity_id=101,
            action="delete",
            before_json=json.dumps(dump_payload),
            created_at=utcnow().isoformat(),
        )
    )
    memory_db.commit()
    log_id = memory_db.execute(select(entity_change_logs.c.id)).scalar_one()

    request = SimpleNamespace(
        state=SimpleNamespace(user={"id": 1, "username": "admin"}, request_id="req-123"),
        headers={},
    )

    with caplog.at_level(logging.WARNING, logger="yerel_hesap.change_history"):
        restored = restore_deleted(
            memory_db,
            request,
            company_id=company_id,
            log_id=log_id,
        )

    assert restored["id"] == 101
    assert restored["name"] == "Eski Müşteri"
    assert "ghost_legacy_col" not in restored

    # Check that warning was logged with column names, NOT values
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ghost_legacy_col" in r.message for r in warning_records)
    assert not any("should_be_dropped" in r.message for r in warning_records)


def test_restore_deleted_control_identical_payload(memory_db: Session) -> None:
    """Control test: a dump payload with matching columns restores byte-identical."""
    company_id = 1
    memory_db.execute(
        text("INSERT INTO companies (id, name) VALUES (1, 'Firma 1')")
    )
    memory_db.commit()

    clean_payload = {
        "id": 102,
        "company_id": company_id,
        "name": "Temiz Müşteri",
        "phone": "5559876543",
        "email": "temiz@example.com",
        "tax_number": "9876543210",
        "opening_balance": "150.00",
        "risk_limit": "5000.00",
        "payment_term_days": 30,
        "is_active": 1,
    }

    memory_db.execute(
        insert(entity_change_logs).values(
            company_id=company_id,
            entity_type="customer",
            entity_id=102,
            action="delete",
            before_json=json.dumps(clean_payload),
            created_at=utcnow().isoformat(),
        )
    )
    memory_db.commit()
    log_id = memory_db.execute(
        select(entity_change_logs.c.id).where(entity_change_logs.c.entity_id == 102)
    ).scalar_one()

    request = SimpleNamespace(
        state=SimpleNamespace(user={"id": 1, "username": "admin"}, request_id="req-123"),
        headers={},
    )

    restored = restore_deleted(
        memory_db,
        request,
        company_id=company_id,
        log_id=log_id,
    )

    assert restored["id"] == 102
    assert restored["name"] == "Temiz Müşteri"
    assert float(restored["opening_balance"]) == 150.00
    assert restored["email"] == "temiz@example.com"
    assert restored["is_active"] == 1
