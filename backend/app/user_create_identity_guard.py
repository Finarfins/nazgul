from __future__ import annotations

import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.dependencies.utils import get_parameterless_sub_dependant
from fastapi.routing import APIRoute


_FORBIDDEN_IDENTITY_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})


def _contains_forbidden_identity_character(value: str) -> bool:
    """Detect control, invisible-format and surrogate characters in identities."""
    return any(
        unicodedata.category(character) in _FORBIDDEN_IDENTITY_CATEGORIES
        for character in value
    )


async def validate_created_user_identity(request: Request) -> None:
    """Reject identities that become invalid or unsafe after normalization."""
    try:
        payload = await request.json()
    except Exception:
        # Preserve FastAPI's normal malformed-body and content-type handling.
        return

    if not isinstance(payload, dict):
        return

    username = payload.get("username")
    display_name = payload.get("display_name")

    if isinstance(username, str):
        normalized_username = username.strip()
        if len(normalized_username) < 3:
            raise HTTPException(
                status_code=422,
                detail="Kullanıcı adı boşluklar temizlendikten sonra en az 3 karakter olmalıdır",
            )
        if len(normalized_username) > 80:
            raise HTTPException(
                status_code=422,
                detail="Kullanıcı adı en fazla 80 karakter olabilir",
            )
        if _contains_forbidden_identity_character(normalized_username):
            raise HTTPException(
                status_code=422,
                detail="Kullanıcı adı kontrol veya görünmez biçim karakteri içeremez",
            )

    if isinstance(display_name, str):
        normalized_display_name = display_name.strip()
        if len(normalized_display_name) < 2:
            raise HTTPException(
                status_code=422,
                detail="Görünen ad boşluklar temizlendikten sonra en az 2 karakter olmalıdır",
            )
        if len(normalized_display_name) > 160:
            raise HTTPException(
                status_code=422,
                detail="Görünen ad en fazla 160 karakter olabilir",
            )
        if _contains_forbidden_identity_character(normalized_display_name):
            raise HTTPException(
                status_code=422,
                detail="Görünen ad kontrol veya görünmez biçim karakteri içeremez",
            )


def install_user_create_identity_guard(router: APIRouter) -> None:
    """Attach normalized identity validation to the single user-create route."""
    matches = [
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == "/users"
        and "POST" in route.methods
    ]
    if len(matches) != 1:
        raise RuntimeError("Kullanıcı oluşturma rotası tekil olarak bulunamadı")

    route = matches[0]
    dependency = Depends(validate_created_user_identity)
    route.dependencies.insert(0, dependency)
    route.dependant.dependencies.insert(
        0,
        get_parameterless_sub_dependant(
            depends=dependency,
            path=route.path_format,
        ),
    )
