from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.dependencies.utils import get_parameterless_sub_dependant
from fastapi.routing import APIRoute

from .auth import has_permission


def require_product_detail_stock(request: Request) -> None:
    """Require stock access before exposing product commercial and inventory detail."""
    user = getattr(request.state, "user", None)
    role = str(user.get("role") or "") if isinstance(user, dict) else ""
    if not has_permission(role, "stock"):
        raise HTTPException(403, "Ürün detayını görüntülemek için stok yetkiniz yok")


def install_product_detail_guard(router: APIRouter) -> None:
    """Attach the guard to the single numeric product-detail GET route."""
    matches = [
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == "/products/{product_id}"
        and "GET" in route.methods
    ]
    if len(matches) != 1:
        raise RuntimeError("Ürün detay rotası tekil olarak bulunamadı")

    route = matches[0]
    dependency = Depends(require_product_detail_stock)
    route.dependencies.insert(0, dependency)
    route.dependant.dependencies.insert(
        0,
        get_parameterless_sub_dependant(
            depends=dependency,
            path=route.path_format,
        ),
    )
