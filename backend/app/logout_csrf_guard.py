from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.dependencies.utils import get_parameterless_sub_dependant
from fastapi.routing import APIRoute

from .auth import csrf_is_valid, extract_bearer
from .config import settings


def _has_session_cookie(request: Request) -> bool:
    """Return whether the request carries an ERP authentication cookie."""
    return any(
        cookie_name in request.cookies
        for cookie_name in (
            settings.access_cookie_name,
            settings.refresh_cookie_name,
        )
    )


def _reject_ambiguous_authorization(request: Request) -> None:
    """Reject duplicate Authorization fields before authentication-source selection."""
    authorization_values = request.headers.getlist("authorization")
    if len(authorization_values) > 1:
        raise HTTPException(status_code=400, detail="Birden fazla Authorization başlığı kabul edilmez")


async def require_logout_csrf(request: Request) -> None:
    """Require CSRF proof only when logout authentication can come from cookies."""
    _reject_ambiguous_authorization(request)

    # ``extract_access_token`` gives an explicit bearer token precedence over the
    # access cookie. Keep the CSRF boundary aligned with that authentication
    # decision: a browser or API wrapper may retain stale ERP cookies while the
    # caller deliberately authenticates this logout with an Authorization header.
    # Cross-site requests cannot attach that header without CORS approval, so
    # requiring double-submit proof in this branch adds no CSRF protection and
    # would break otherwise valid bearer clients.
    if extract_bearer(request):
        return
    if _has_session_cookie(request) and not csrf_is_valid(request):
        raise HTTPException(status_code=403, detail="CSRF doğrulaması başarısız")


def install_logout_csrf_guard(router: APIRouter) -> None:
    """Attach the guard to the single logout route and fail closed on route drift."""
    matches = [
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == "/auth/logout"
        and "POST" in route.methods
    ]
    if len(matches) != 1:
        raise RuntimeError("Oturum kapatma rotası tekil olarak bulunamadı")

    route = matches[0]
    dependency = Depends(require_logout_csrf)
    route.dependencies.insert(0, dependency)
    route.dependant.dependencies.insert(
        0,
        get_parameterless_sub_dependant(
            depends=dependency,
            path=route.path_format,
        ),
    )
