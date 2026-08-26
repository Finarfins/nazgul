from __future__ import annotations

import hmac
from typing import Mapping

from fastapi import HTTPException, Request

from .config import settings


def platform_operator_entries(raw: str | None = None) -> frozenset[str]:
    return frozenset(
        item.strip().casefold()
        for item in (settings.sungur_platform_operators if raw is None else raw).split(",")
        if item.strip()
    )


def is_platform_operator(user: Mapping[str, object], raw: str | None = None) -> bool:
    if str(user.get("role", "")) != "admin":
        return False
    entries = platform_operator_entries(raw)
    user_id = str(user.get("id", ""))
    return user_id.isdigit() and any(
        entry.isdigit() and hmac.compare_digest(user_id, entry) for entry in entries
    )


def require_platform_operator(request: Request) -> None:
    user = getattr(request.state, "user", {})
    if not is_platform_operator(user):
        raise HTTPException(status_code=403, detail="Platform operatörü yetkisi gerekli")
