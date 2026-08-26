from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.audit_permissions import (
    install_audit_list_guard,
    require_audit_user_management,
)
from app.routers import auth


def _request(role: str | None):
    user = {"role": role} if role is not None else None
    return SimpleNamespace(state=SimpleNamespace(user=user))


@pytest.mark.parametrize("role", ["admin", "yonetici"])
def test_audit_list_accepts_user_management_roles(role: str) -> None:
    require_audit_user_management(_request(role))


@pytest.mark.parametrize("role", [None, "", "rapor", "satis", "muhasebe", "depo"])
def test_audit_list_rejects_roles_without_user_management(role: str | None) -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_audit_user_management(_request(role))

    assert exc_info.value.status_code == 403
    assert "kullanıcı yönetimi yetkiniz" in str(exc_info.value.detail)


def test_audit_list_route_has_guard_installed_once() -> None:
    matches = [
        route
        for route in auth.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/audit"
        and "GET" in route.methods
    ]

    assert len(matches) == 1
    calls = [dependency.call for dependency in matches[0].dependant.dependencies]
    assert calls.count(require_audit_user_management) == 1


def test_guard_installer_fails_closed_when_route_is_missing() -> None:
    from fastapi import APIRouter

    with pytest.raises(RuntimeError, match="tekil"):
        install_audit_list_guard(APIRouter())
