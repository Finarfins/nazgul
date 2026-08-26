from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.dependencies.utils import get_parameterless_sub_dependant
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .tenancy import memberships


def prevent_cross_tenant_user_deactivation(
    user_id: int,
    is_active: bool,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    """Prevent a tenant-scoped deactivation from disabling a shared account globally."""
    if is_active:
        return

    company_id = int(request.state.company_id)
    company_ids = {
        int(row[0])
        for row in db.execute(
            select(memberships.c.company_id).where(memberships.c.user_id == user_id)
        ).all()
    }

    # Preserve the endpoint's tenant-scoped 404 behavior. A caller who cannot
    # manage this user in the active company must not learn that the account is
    # attached to another tenant.
    if company_id not in company_ids:
        return

    if any(candidate != company_id for candidate in company_ids):
        raise HTTPException(
            409,
            "Birden fazla firmaya bağlı kullanıcı bu firma üzerinden pasife alınamaz",
        )


def install_user_status_tenant_guard(router: APIRouter) -> None:
    """Attach the shared-account guard to the single user-status mutation route."""
    matches = [
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == "/users/{user_id}/status"
        and "PATCH" in route.methods
    ]
    if len(matches) != 1:
        raise RuntimeError("Kullanıcı durum rotası tekil olarak bulunamadı")

    route = matches[0]
    dependency = Depends(prevent_cross_tenant_user_deactivation)
    route.dependencies.insert(0, dependency)
    route.dependant.dependencies.insert(
        0,
        get_parameterless_sub_dependant(
            depends=dependency,
            path=route.path_format,
        ),
    )
