from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import APIRouter, HTTPException

from app.user_status_tenant_guard import (
    install_user_status_tenant_guard,
    prevent_cross_tenant_user_deactivation,
)


class _Result:
    def __init__(self, company_ids: list[int]):
        self._rows = [(company_id,) for company_id in company_ids]

    def all(self):
        return self._rows


class _Session:
    def __init__(self, company_ids: list[int]):
        self.company_ids = company_ids
        self.execute_calls = 0

    def execute(self, _statement):
        self.execute_calls += 1
        return _Result(self.company_ids)


def _request(company_id: int):
    return SimpleNamespace(state=SimpleNamespace(company_id=company_id))


def test_single_tenant_user_deactivation_is_allowed():
    db = _Session([7])

    prevent_cross_tenant_user_deactivation(15, False, _request(7), db)

    assert db.execute_calls == 1


def test_shared_user_deactivation_fails_closed():
    db = _Session([7, 9])

    with pytest.raises(HTTPException) as exc_info:
        prevent_cross_tenant_user_deactivation(15, False, _request(7), db)

    assert exc_info.value.status_code == 409
    assert "Birden fazla firmaya bağlı" in str(exc_info.value.detail)


def test_shared_user_activation_remains_allowed_without_cross_tenant_query():
    db = _Session([7, 9])

    prevent_cross_tenant_user_deactivation(15, True, _request(7), db)

    assert db.execute_calls == 0


def test_foreign_only_user_preserves_endpoint_404_without_existence_disclosure():
    db = _Session([9])

    prevent_cross_tenant_user_deactivation(15, False, _request(7), db)

    assert db.execute_calls == 1


def test_guard_is_installed_only_on_user_status_patch_route():
    router = APIRouter()

    @router.patch("/users/{user_id}/status")
    def change_status(user_id: int, is_active: bool):
        return {"id": user_id, "is_active": is_active}

    @router.get("/users")
    def list_users():
        return []

    install_user_status_tenant_guard(router)

    status_route = next(route for route in router.routes if route.path.endswith("/status"))
    list_route = next(route for route in router.routes if route.path == "/users")
    assert len(status_route.dependencies) == 1
    assert status_route.dependencies[0].dependency is prevent_cross_tenant_user_deactivation
    assert list_route.dependencies == []


def test_guard_installer_fails_closed_when_route_is_missing():
    with pytest.raises(RuntimeError, match="Kullanıcı durum rotası"):
        install_user_status_tenant_guard(APIRouter())
