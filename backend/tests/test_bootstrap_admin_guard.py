from __future__ import annotations

import pytest
from sqlalchemy import create_engine, insert, select

from app.auth import hash_password, metadata, users, utcnow, verify_password
from app.bootstrap_data import _ensure_bootstrap_admin, _ensure_bootstrap_company
from app.tenancy import companies


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    companies.metadata.create_all(engine)
    return engine


def test_creates_bootstrap_admin_only_when_user_table_is_empty():
    engine = _engine()

    with engine.begin() as conn:
        user_id = _ensure_bootstrap_admin(
            conn,
            username="kurucu",
            display_name="Kurucu Yönetici",
            password="guvenli-parola-123",
        )
        row = conn.execute(select(users).where(users.c.id == user_id)).mappings().one()

    assert row["username"] == "kurucu"
    assert row["display_name"] == "Kurucu Yönetici"
    assert row["role"] == "admin"
    assert row["is_active"] is True
    assert row["must_change_password"] is True
    assert verify_password("guvenli-parola-123", row["password_hash"])


def test_existing_installation_rejects_changed_bootstrap_username():
    engine = _engine()

    with engine.begin() as conn:
        conn.execute(
            insert(users).values(
                username="mevcut-admin",
                display_name="Mevcut Admin",
                password_hash=hash_password("guvenli-parola-123"),
                role="admin",
                is_active=True,
                must_change_password=False,
                created_at=utcnow(),
            )
        )
        with pytest.raises(RuntimeError, match="yeni ayrıcalıklı hesap oluşturulmadı"):
            _ensure_bootstrap_admin(
                conn,
                username="yeni-admin",
                display_name="Yeni Admin",
                password="baska-guvenli-parola",
            )

        usernames = conn.execute(select(users.c.username)).scalars().all()

    assert usernames == ["mevcut-admin"]


def test_existing_non_admin_cannot_be_adopted_as_bootstrap_admin():
    engine = _engine()

    with engine.begin() as conn:
        conn.execute(
            insert(users).values(
                username="depo-kullanicisi",
                display_name="Depo Kullanıcısı",
                password_hash=hash_password("guvenli-parola-123"),
                role="depo",
                is_active=True,
                must_change_password=False,
                created_at=utcnow(),
            )
        )
        with pytest.raises(RuntimeError, match="admin rolüne sahip olmayan"):
            _ensure_bootstrap_admin(
                conn,
                username="depo-kullanicisi",
                display_name="Depo Kullanıcısı",
                password="guvenli-parola-123",
            )


def test_existing_inactive_admin_cannot_be_reused_for_bootstrap():
    engine = _engine()

    with engine.begin() as conn:
        conn.execute(
            insert(users).values(
                username="pasif-admin",
                display_name="Pasif Yönetici",
                password_hash=hash_password("guvenli-parola-123"),
                role="admin",
                is_active=False,
                must_change_password=False,
                created_at=utcnow(),
            )
        )
        with pytest.raises(RuntimeError, match="pasif bir yönetici"):
            _ensure_bootstrap_admin(
                conn,
                username="pasif-admin",
                display_name="Pasif Yönetici",
                password="guvenli-parola-123",
            )

        row = conn.execute(
            select(users).where(users.c.username == "pasif-admin")
        ).mappings().one()

    assert row["is_active"] is False
    assert row["must_change_password"] is False


def test_existing_admin_is_reused_without_password_reset():
    engine = _engine()
    password_hash = hash_password("mevcut-guvenli-parola")

    with engine.begin() as conn:
        inserted_id = conn.execute(
            insert(users)
            .values(
                username="admin",
                display_name="Yönetici",
                password_hash=password_hash,
                role="admin",
                is_active=True,
                must_change_password=False,
                created_at=utcnow(),
            )
            .returning(users.c.id)
        ).scalar_one()
        resolved_id = _ensure_bootstrap_admin(
            conn,
            username="admin",
            display_name="Başka İsim",
            password="başka-parola-123",
        )
        row = conn.execute(select(users).where(users.c.id == inserted_id)).mappings().one()

    assert resolved_id == inserted_id
    assert row["password_hash"] == password_hash
    assert row["display_name"] == "Yönetici"


def test_bootstrap_company_is_created_only_when_company_table_is_empty():
    engine = _engine()

    with engine.begin() as conn:
        company_id = _ensure_bootstrap_company(conn)
        row = conn.execute(
            select(companies).where(companies.c.id == company_id)
        ).mappings().one()

    assert row["name"] == "Ana Firma"
    assert row["is_active"] is True


def test_bootstrap_company_uses_active_company_instead_of_earlier_inactive_one():
    engine = _engine()

    with engine.begin() as conn:
        inactive_id = conn.execute(
            insert(companies)
            .values(name="Kapalı Firma", is_active=False, created_at=utcnow())
            .returning(companies.c.id)
        ).scalar_one()
        active_id = conn.execute(
            insert(companies)
            .values(name="Aktif Firma", is_active=True, created_at=utcnow())
            .returning(companies.c.id)
        ).scalar_one()

        resolved_id = _ensure_bootstrap_company(conn)
        rows = conn.execute(select(companies).order_by(companies.c.id)).mappings().all()

    assert resolved_id == active_id
    assert resolved_id != inactive_id
    assert len(rows) == 2
    assert rows[0]["is_active"] is False
    assert rows[1]["is_active"] is True


def test_bootstrap_company_rejects_installation_with_only_inactive_companies():
    engine = _engine()

    with engine.begin() as conn:
        company_id = conn.execute(
            insert(companies)
            .values(name="Devre Dışı Firma", is_active=False, created_at=utcnow())
            .returning(companies.c.id)
        ).scalar_one()

        with pytest.raises(RuntimeError, match="aktif firma bulunamadı"):
            _ensure_bootstrap_company(conn)

        row = conn.execute(
            select(companies).where(companies.c.id == company_id)
        ).mappings().one()
        company_count = len(conn.execute(select(companies.c.id)).all())

    assert row["is_active"] is False
    assert company_count == 1
