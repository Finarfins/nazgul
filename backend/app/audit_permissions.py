from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.dependencies.utils import get_parameterless_sub_dependant
from fastapi.routing import APIRoute

from .auth import has_permission


def require_audit_user_management(request: Request) -> None:
    """Restrict security-audit records to roles allowed to manage users."""
    user = getattr(request.state, "user", None)
    role = str(user.get("role") or "") if isinstance(user, dict) else ""
    if not has_permission(role, "users"):
        raise HTTPException(
            403,
            "Güvenlik denetim kayıtlarını görüntülemek için kullanıcı yönetimi yetkiniz yok",
        )


def install_audit_list_guard(router: APIRouter) -> None:
    """Attach the authorization guard to the single audit-list GET route."""
    matches = [
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == "/audit"
        and "GET" in route.methods
    ]
    if len(matches) != 1:
        raise RuntimeError("Güvenlik denetim listesi rotası tekil olarak bulunamadı")

    route = matches[0]
    dependency = Depends(require_audit_user_management)
    route.dependencies.insert(0, dependency)
    route.dependant.dependencies.insert(
        0,
        get_parameterless_sub_dependant(
            depends=dependency,
            path=route.path_format,
        ),
    )
