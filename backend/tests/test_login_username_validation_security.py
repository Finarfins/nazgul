from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.routers.auth import LoginPayload


def test_login_username_rejects_whitespace_only_input() -> None:
    """Whitespace-only identities must fail before lockout/audit database access."""
    with pytest.raises(ValidationError):
        LoginPayload(username="   ", password="not-empty")


def test_login_username_normalizes_surrounding_whitespace() -> None:
    payload = LoginPayload(username="  operator  ", password="not-empty")

    assert payload.username == "operator"


def test_login_username_keeps_valid_identity_compatible() -> None:
    payload = LoginPayload(username="operator", password="not-empty")

    assert payload.username == "operator"
