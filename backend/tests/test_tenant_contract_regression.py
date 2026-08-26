from __future__ import annotations

from pathlib import Path

from app.tenant_contracts import (
    assert_repository_contracts,
    validate_function_contract,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_registered_sensitive_routes_remain_tenant_scoped() -> None:
    assert_repository_contracts(BACKEND_ROOT)


def test_contract_rejects_database_access_before_tenant_resolution() -> None:
    source = '''
def unsafe_detail(request, db):
    row = db.execute("SELECT * FROM products").first()
    cid = company_id(request)
    return row
'''

    violations = validate_function_contract(
        source,
        required_fragments=("company_id(request)", "company_id=:cid"),
    )

    assert "missing required tenant fragment: company_id=:cid" in violations
    assert "database access occurs before tenant resolution" in violations


def test_contract_accepts_bound_tenant_filter_before_database_access() -> None:
    source = '''
def safe_detail(request, db):
    cid = company_id(request)
    return db.execute(
        text("SELECT * FROM products WHERE company_id=:cid"),
        {"cid": cid},
    ).first()
'''

    assert validate_function_contract(
        source,
        required_fragments=("company_id(request)", "company_id=:cid"),
    ) == []
