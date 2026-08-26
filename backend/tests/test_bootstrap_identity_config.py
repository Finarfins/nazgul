from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_bootstrap_identity_is_trimmed() -> None:
    settings = Settings(
        bootstrap_admin_username="  first-admin  ",
        bootstrap_admin_display_name="  İlk Yönetici  ",
    )

    assert settings.bootstrap_admin_username == "first-admin"
    assert settings.bootstrap_admin_display_name == "İlk Yönetici"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("bootstrap_admin_username", "   ", "BOOTSTRAP_ADMIN_USERNAME boş olamaz"),
        (
            "bootstrap_admin_display_name",
            "\t\n",
            "BOOTSTRAP_ADMIN_DISPLAY_NAME boş olamaz",
        ),
        (
            "bootstrap_admin_username",
            "u" * 81,
            "BOOTSTRAP_ADMIN_USERNAME en fazla 80 karakter olabilir",
        ),
        (
            "bootstrap_admin_display_name",
            "Y" * 161,
            "BOOTSTRAP_ADMIN_DISPLAY_NAME en fazla 160 karakter olabilir",
        ),
    ],
)
def test_invalid_bootstrap_identity_is_rejected(
    field: str, value: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(**{field: value})
