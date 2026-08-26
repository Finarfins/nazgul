"""Technician profiles (Servis v2 FAZ-1).

A profile is optional service metadata for an existing ``app_users`` row —
identity stays in ``app_users`` and the work-order technician validation is
unchanged (a user without a profile can still be assigned). One profile per
user per company, enforced by ``uq_technician_profiles_company_user``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..change_history import record_change
from ..db import get_db
from ..machine_service_schemas import TechnicianProfileCreate, TechnicianProfileUpdate
from ..tenancy import company_id

router = APIRouter(prefix="/technician-profiles", tags=["technician-profiles"])

PROFILE_COLUMNS = """p.id,p.company_id,p.user_id,p.phone,p.specialty,p.active,p.created_at,
    u.username,u.display_name"""


def _serialize(row: Any) -> dict:
    data = dict(row)
    data["active"] = bool(data["active"])
    return data


def _profile_row(db: Session, cid: int, profile_id: int) -> dict[str, Any]:
    row = db.execute(
        text(
            f"""SELECT {PROFILE_COLUMNS} FROM technician_profiles p
            JOIN app_users u ON u.id=p.user_id
            WHERE p.id=:id AND p.company_id=:cid"""
        ),
        {"id": profile_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Teknisyen profili bulunamadı")
    return dict(row)


def _require_company_member(db: Session, cid: int, user_id: int) -> None:
    exists = db.execute(
        text(
            """SELECT 1 FROM app_users u
            JOIN user_company_memberships membership ON membership.user_id=u.id
            WHERE u.id=:user_id AND membership.company_id=:cid
              AND u.is_active=TRUE"""
        ),
        {"user_id": user_id, "cid": cid},
    ).first()
    if not exists:
        raise HTTPException(400, "Teknisyen bu firmada geçerli bir kullanıcı değil")


@router.get("")
def list_profiles(
    request: Request,
    active: str = "active",
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    active_sql = (
        ""
        if active == "all"
        else (" AND p.active = FALSE" if active == "inactive" else " AND p.active = TRUE")
    )
    rows = db.execute(
        text(
            f"""SELECT {PROFILE_COLUMNS} FROM technician_profiles p
            JOIN app_users u ON u.id=p.user_id
            WHERE p.company_id=:cid{active_sql}
            ORDER BY u.display_name,u.username"""
        ),
        {"cid": cid},
    ).mappings().all()
    return [_serialize(row) for row in rows]


@router.post("", status_code=201)
def create_profile(
    payload: TechnicianProfileCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    _require_company_member(db, cid, payload.user_id)
    try:
        profile_id = db.execute(
            text(
                """INSERT INTO technician_profiles(
                company_id,user_id,phone,specialty,active,created_at
                ) VALUES(:cid,:user_id,:phone,:specialty,:active,:now)
                RETURNING id"""
            ),
            {
                "cid": cid,
                "user_id": payload.user_id,
                "phone": payload.phone,
                "specialty": payload.specialty,
                "active": bool(payload.active),
                "now": utcnow(),
            },
        ).scalar_one()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Bu kullanıcı için teknisyen profili zaten var") from exc
    after = _profile_row(db, cid, int(profile_id))
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="technician_profile",
        entity_id=int(profile_id),
        action="create",
        before=None,
        after=after,
    )
    db.commit()
    return _serialize(after)


@router.put("/{profile_id}")
def update_profile(
    profile_id: int,
    payload: TechnicianProfileUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    before = _profile_row(db, cid, profile_id)
    db.execute(
        text(
            """UPDATE technician_profiles SET
            phone=:phone,specialty=:specialty,active=:active
            WHERE id=:id AND company_id=:cid"""
        ),
        {
            "phone": payload.phone,
            "specialty": payload.specialty,
            "active": bool(payload.active),
            "id": profile_id,
            "cid": cid,
        },
    )
    after = _profile_row(db, cid, profile_id)
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="technician_profile",
        entity_id=profile_id,
        action="update",
        before=before,
        after=after,
    )
    db.commit()
    return _serialize(after)
