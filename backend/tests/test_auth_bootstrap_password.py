from types import SimpleNamespace

from sqlalchemy import create_engine, insert, select

from app import auth


def test_empty_auth_table_uses_effective_bootstrap_password(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    configured_password = "ConfiguredBootstrapSecret-2026!"
    monkeypatch.setattr(
        auth,
        "settings",
        SimpleNamespace(effective_bootstrap_admin_password=configured_password),
    )

    auth.initialize_auth(engine)

    with engine.connect() as connection:
        admin = connection.execute(
            select(auth.users).where(auth.users.c.username == "admin")
        ).mappings().one()

    assert auth.verify_password(configured_password, admin["password_hash"])
    assert not auth.verify_password("admin123", admin["password_hash"])
    assert admin["must_change_password"] is True


def test_existing_well_known_password_is_still_marked_for_change(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    auth.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(auth.users).values(
                username="admin",
                display_name="Sistem Yöneticisi",
                password_hash=auth.hash_password("admin123"),
                role="admin",
                is_active=True,
                must_change_password=False,
                created_at=auth.utcnow(),
            )
        )
    monkeypatch.setattr(
        auth,
        "settings",
        SimpleNamespace(
            effective_bootstrap_admin_password="ConfiguredBootstrapSecret-2026!"
        ),
    )

    auth.initialize_auth(engine)

    with engine.connect() as connection:
        admin = connection.execute(
            select(auth.users).where(auth.users.c.username == "admin")
        ).mappings().one()

    assert auth.verify_password("admin123", admin["password_hash"])
    assert admin["must_change_password"] is True
