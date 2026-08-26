import pytest
from pydantic import ValidationError

from app.routers.auth import (
    ChangePasswordPayload,
    PASSWORD_MIN_LENGTH,
    RegisterPayload,
    UserPayload,
)


def _register_payload(password: str) -> RegisterPayload:
    return RegisterPayload(
        company_name="Test Çiftliği",
        email="kayit@example.com",
        password=password,
        password_confirmation=password,
        display_name="Test Kullanıcı",
        phone="5361234567",
        terms_accepted=True,
    )


def _build_all(password: str) -> None:
    """Construct every payload that carries the shared password policy."""
    ChangePasswordPayload(current_password="eski-parola", new_password=password)
    UserPayload(
        username="kullanici",
        display_name="Test Kullanıcı",
        password=password,
        role="rapor",
    )
    _register_payload(password)


def test_password_minimum_is_six_characters():
    assert PASSWORD_MIN_LENGTH == 6


def test_password_at_the_minimum_length_is_accepted():
    exactly_minimum = "abcdef"
    assert len(exactly_minimum) == PASSWORD_MIN_LENGTH
    _build_all(exactly_minimum)


def test_password_one_below_the_minimum_is_rejected():
    too_short = "abcde"
    assert len(too_short) == PASSWORD_MIN_LENGTH - 1
    with pytest.raises(ValidationError):
        ChangePasswordPayload(current_password="eski-parola", new_password=too_short)
    with pytest.raises(ValidationError):
        UserPayload(
            username="kullanici",
            display_name="Test Kullanıcı",
            password=too_short,
            role="rapor",
        )
    with pytest.raises(ValidationError):
        _register_payload(too_short)


@pytest.mark.parametrize(
    "password",
    [
        "abcdefgh",  # yalnız küçük harf
        "ABCDEFGH",  # yalnız büyük harf
        "12345678",  # yalnız rakam
        "........",  # yalnız özel karakter
        "cilek receli",  # boşluklu, rakamsız
    ],
)
def test_complexity_requirements_are_no_longer_enforced(password):
    """Büyük/küçük harf, rakam ve özel karakter şartları kaldırıldı."""
    _build_all(password)


def test_common_passwords_are_still_rejected():
    # Uzunluk şartı gevşedi ama denylist duruyor: kurulum parolası "admin123"
    # artık uzunluk şartını geçtiği için yalnız bu liste sayesinde reddediliyor.
    for weak in ("admin123", "password", "PaRoLa123456"):
        with pytest.raises(ValidationError):
            ChangePasswordPayload(current_password="eski-parola", new_password=weak)
        with pytest.raises(ValidationError):
            UserPayload(
                username="kullanici",
                display_name="Test Kullanıcı",
                password=weak,
                role="rapor",
            )


def test_password_equal_to_username_is_rejected():
    with pytest.raises(ValidationError):
        UserPayload(
            username="muhasebeci",
            display_name="Muhasebe Kullanıcı",
            password="muhasebeci",
            role="muhasebe",
        )


def test_password_equal_to_email_is_rejected_on_registration():
    with pytest.raises(ValidationError):
        RegisterPayload(
            company_name="Test Çiftliği",
            email="kayit@example.com",
            password="kayit@example.com",
            password_confirmation="kayit@example.com",
            display_name="Test Kullanıcı",
            phone="5361234567",
            terms_accepted=True,
        )
