from pathlib import Path

from app.auth import ROLE_PERMISSIONS, required_permission
from app.routers.field import FieldCustomer, FieldSnapshot


def test_field_permission_is_separate_and_checked_before_safe_read() -> None:
    assert required_permission("GET", "/api/field/snapshot") == "field_service"
    assert "field_service" in ROLE_PERMISSIONS["satis"]
    assert "field_service" not in ROLE_PERMISSIONS["rapor"]


def test_offline_dtos_are_strict_allow_lists_without_phone_or_finance() -> None:
    customer_fields = set(FieldCustomer.model_fields)
    snapshot_text = repr(FieldSnapshot.model_json_schema()).lower()
    assert customer_fields == {"display_name", "service_address"}
    for forbidden in ("phone", "telefon", "balance", "tax_number", "total_labor_cost"):
        assert forbidden not in snapshot_text


def test_field_query_is_identity_scoped_and_terminal_filtered() -> None:
    source = Path("app/routers/field.py").read_text(encoding="utf-8")
    assert "w.company_id=:cid" in source
    assert "w.technician_id=:user_id" in source
    assert "request.state" in source
    assert "DELIVERED','CANCELLED" in source
    assert "technician_id:" not in source
