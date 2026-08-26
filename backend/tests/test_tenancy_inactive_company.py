from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session

from app.tenancy import companies, memberships, metadata, resolve_company


def _seed_company(db: Session, *, name: str, active: bool) -> int:
    result = db.execute(
        insert(companies).values(
            name=name,
            is_active=active,
            negative_stock_policy="block",
            credit_limit_policy="block",
            created_at=datetime.now(timezone.utc),
        )
    )
    return int(result.inserted_primary_key[0])


def test_explicit_inactive_company_selection_is_rejected() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)

    with Session(engine) as db:
        inactive_company_id = _seed_company(db, name="Kapalı Firma", active=False)
        db.execute(
            insert(memberships).values(
                user_id=7,
                company_id=inactive_company_id,
                is_default=True,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            resolve_company(db, 7, inactive_company_id)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Bu firmaya erişim yetkiniz yok"


def test_default_resolution_skips_inactive_membership() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)

    with Session(engine) as db:
        inactive_company_id = _seed_company(db, name="Eski Firma", active=False)
        active_company_id = _seed_company(db, name="Aktif Firma", active=True)
        created_at = datetime.now(timezone.utc)
        db.execute(
            insert(memberships),
            [
                {
                    "user_id": 11,
                    "company_id": inactive_company_id,
                    "is_default": True,
                    "created_at": created_at,
                },
                {
                    "user_id": 11,
                    "company_id": active_company_id,
                    "is_default": False,
                    "created_at": created_at,
                },
            ],
        )
        db.commit()

        resolved = resolve_company(db, 11, None)

    assert resolved == active_company_id


def test_active_member_company_still_resolves() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)

    with Session(engine) as db:
        company_id = _seed_company(db, name="Sungur Tarım", active=True)
        db.execute(
            insert(memberships).values(
                user_id=13,
                company_id=company_id,
                is_default=True,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        assert resolve_company(db, 13, company_id) == company_id
