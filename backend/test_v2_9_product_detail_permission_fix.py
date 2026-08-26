from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.auth import ROLE_PERMISSIONS, has_permission
from app.product_detail_permissions import require_product_detail_stock
from app.routers import products


def _request_for(role: str):
    return SimpleNamespace(state=SimpleNamespace(user={"role": role}))


def test_stock_roles_can_read_product_detail() -> None:
    for role in ("admin", "yonetici", "depo"):
        assert has_permission(role, "stock")
        require_product_detail_stock(_request_for(role))


def test_limited_roles_cannot_read_product_detail() -> None:
    for role in ("muhasebe", "satis", "rapor"):
        assert "read" in ROLE_PERMISSIONS[role]
        assert not has_permission(role, "stock")
        with pytest.raises(HTTPException) as exc_info:
            require_product_detail_stock(_request_for(role))
        assert exc_info.value.status_code == 403


def test_anonymous_product_detail_is_denied() -> None:
    request = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(HTTPException) as exc_info:
        require_product_detail_stock(request)
    assert exc_info.value.status_code == 403


def test_guard_is_attached_only_to_product_detail_route() -> None:
    detail_routes = [
        route
        for route in products.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/products/{product_id}"
        and "GET" in route.methods
    ]
    assert len(detail_routes) == 1
    detail = detail_routes[0]
    assert any(
        dependency.call is require_product_detail_stock
        for dependency in detail.dependant.dependencies
    )

    list_routes = [
        route
        for route in products.router.routes
        if isinstance(route, APIRoute) and route.path == "/products"
    ]
    assert list_routes
    assert all(
        dependency.call is not require_product_detail_stock
        for route in list_routes
        for dependency in route.dependant.dependencies
    )
