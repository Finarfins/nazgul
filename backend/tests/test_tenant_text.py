from __future__ import annotations

import pytest

from app.tenancy import tenant_text


def test_tenant_text_keeps_statement_and_bind_together() -> None:
    statement, params = tenant_text(
        "SELECT id FROM activity_logs WHERE company_id = :cid",
        42,
    )

    assert str(statement) == "SELECT id FROM activity_logs WHERE company_id = :cid"
    assert params == {"cid": 42}

    nested_or, nested_params = tenant_text(
        "SELECT id FROM activity_logs WHERE company_id = :cid "
        "AND (event_type = :event OR created_at IS NULL)",
        42,
    )
    assert "AND (event_type = :event OR created_at IS NULL)" in str(nested_or)
    assert nested_params == {"cid": 42}


def test_tenant_text_rejects_missing_or_projected_company_id() -> None:
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text("SELECT company_id FROM activity_logs", 42)
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text("SELECT id FROM activity_logs -- company_id = :cid", 42)
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text("SELECT 'company_id = :cid' FROM activity_logs", 42)
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text("SELECT $$ company_id = :cid $$ FROM activity_logs", 42)
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text("SELECT $tag$ company_id = :cid $tag$ FROM activity_logs", 42)
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text('SELECT 1 AS "company_id = :cid" FROM activity_logs', 42)
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text("SELECT 1 AS `company_id = :cid` FROM activity_logs", 42)
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text("SELECT 1 AS [company_id = :cid] FROM activity_logs", 42)
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text(
            "SELECT id, company_id = :cid AS same_company FROM activity_logs",
            42,
        )
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text("SELECT id FROM activity_logs ORDER BY company_id = :cid", 42)
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text(
            "SELECT id FROM activity_logs WHERE company_id = :cid OR 1=1",
            42,
        )
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text(
            "SELECT id FROM activity_logs "
            "WHERE company_id = :cid AND event_type = :event OR 1=1",
            42,
        )
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text(
            "SELECT id FROM activity_logs "
            "WHERE (company_id = :cid AND event_type = :event) "
            "OR created_at IS NULL",
            42,
        )
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text(
            "SELECT id FROM activity_logs WHERE (company_id = :cid) IS FALSE",
            42,
        )
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text(
            "SELECT id FROM activity_logs "
            "WHERE (company_id = :cid AND event_type = :event) IS FALSE",
            42,
        )
    with pytest.raises(ValueError, match="company_id = :cid"):
        tenant_text(
            "SELECT * FROM activity_logs WHERE EXISTS ("
            "SELECT 1 FROM branches WHERE company_id = :cid)",
            42,
        )
