"""Kullanıcı Aktivite Paneli uçları.

Yüzey kasıtlı olarak dardır:

* ``GET  /api/activity-logs``                 — yalnız admin + yönetici
* ``POST /api/activity-logs/{id}/archive``    — yalnız admin, gerekçe zorunlu
* ``POST /api/activity-logs/{id}/unarchive``  — yalnız admin

Kaydın çekirdek alanlarını (özet, detay, kullanıcı, zaman, kaynak) değiştiren
**hiçbir uç yoktur** ve gerçek silme ucu da yoktur; PUT/PATCH/DELETE çağrıları
405/404 ile karşılanır. Arşivleme/geri alma işleminin kendisi de ayrı bir
aktivite satırı üretir, böylece "denetim kaydını kim gizledi" sorusu yine
denetim kaydından yanıtlanır.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..activity_log import (
    ACTION_TYPES,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MIN_ARCHIVE_REASON_LENGTH,
    RESOURCE_TYPES,
    archive_log,
    get_activity_log,
    list_activity_logs,
    log_request_activity,
    unarchive_log,
)
from ..db import get_db
from ..tenancy import company_id

router = APIRouter(prefix="/activity-logs", tags=["activity-logs"])
logger = logging.getLogger(__name__)

# Paneli görebilen roller. ``users`` yetkisiyle birebir örtüşür ama burada
# açıkça yazılır: ileride ``users`` yetkisi başka bir role verilirse denetim
# paneli sessizce onunla birlikte açılmasın.
PANEL_ROLES = frozenset({"admin", "yonetici"})
ARCHIVE_ROLES = frozenset({"admin"})


class ArchivePayload(BaseModel):
    reason: str = Field(min_length=MIN_ARCHIVE_REASON_LENGTH, max_length=500)


def _role(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return str(user.get("role") or "") if isinstance(user, dict) else ""


def require_panel_role(request: Request) -> None:
    if _role(request) not in PANEL_ROLES:
        raise HTTPException(403, "Aktivite kayıtlarını görüntüleme yetkiniz yok")


def require_archive_role(request: Request) -> None:
    if _role(request) not in ARCHIVE_ROLES:
        raise HTTPException(403, "Aktivite kaydı arşivleme yetkisi yalnız admin'dedir")


@router.get("", dependencies=[Depends(require_panel_role)])
def list_logs(
    request: Request,
    user_id: int | None = None,
    action_type: str | None = None,
    resource_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_archived: bool = False,
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    if action_type and action_type not in ACTION_TYPES:
        raise HTTPException(400, "Bilinmeyen aktivite tipi")
    if resource_type and resource_type not in RESOURCE_TYPES:
        raise HTTPException(400, "Bilinmeyen kaynak tipi")
    # Arşivlenmiş kayıtlar yalnız admin için açılabilir: yönetici, arşivlenmiş
    # bir satırı hiçbir filtre kombinasyonuyla göremez.
    if include_archived and _role(request) not in ARCHIVE_ROLES:
        raise HTTPException(403, "Arşivlenmiş kayıtları yalnız admin görebilir")
    try:
        return list_activity_logs(
            db,
            company_id(request),
            user_id=user_id,
            action_type=action_type,
            resource_type=resource_type,
            date_from=date_from,
            date_to=date_to,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/catalog", dependencies=[Depends(require_panel_role)])
def catalog():
    """Filtre çubuğunun beslendiği v1 olay kataloğu."""
    return {
        "action_types": [
            {"value": value, "label": label} for value, label in ACTION_TYPES.items()
        ],
        "resource_types": sorted(RESOURCE_TYPES),
        "max_limit": MAX_PAGE_SIZE,
    }


@router.post("/{log_id}/archive", dependencies=[Depends(require_archive_role)])
def archive(
    log_id: int,
    payload: ArchivePayload,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    try:
        record = archive_log(
            db,
            cid,
            log_id,
            archived_by=int(request.state.user["id"]),
            reason=payload.reason,
        )
        # Arşivleme işleminin kendisi de denetim kaydıdır — aynı transaction.
        log_request_activity(
            db,
            request,
            cid,
            "activity_log.archive",
            "activity_log",
            log_id,
            f"#{log_id} numaralı aktivite kaydını arşivledi — gerekçe: "
            f"{record['archive_reason']}",
            {
                "archived_log": {"id": log_id, "action_type": record["action_type"]},
                "reason": record["archive_reason"],
            },
        )
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, "Arşivlenecek aktivite kaydı bulunamadı") from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Unexpected activity-log archive failure")
        raise HTTPException(500, "Aktivite kaydı arşivlenemedi.") from exc
    return record


@router.post("/{log_id}/unarchive", dependencies=[Depends(require_archive_role)])
def unarchive(log_id: int, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    try:
        record = unarchive_log(db, cid, log_id)
        log_request_activity(
            db,
            request,
            cid,
            "activity_log.unarchive",
            "activity_log",
            log_id,
            f"#{log_id} numaralı aktivite kaydını arşivden çıkardı",
            {"unarchived_log": {"id": log_id, "action_type": record["action_type"]}},
        )
        db.commit()
    except LookupError as exc:
        db.rollback()
        raise HTTPException(
            404, "Arşivden çıkarılacak aktivite kaydı bulunamadı"
        ) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Unexpected activity-log unarchive failure")
        raise HTTPException(500, "Aktivite kaydı arşivden çıkarılamadı.") from exc
    return record


@router.get("/{log_id}", dependencies=[Depends(require_panel_role)])
def detail(log_id: int, request: Request, db: Session = Depends(get_db)):
    record = get_activity_log(db, company_id(request), log_id)
    # Tenant dışı kayıt ve arşivlenmiş kaydın yönetici tarafından okunması aynı
    # 404 ile karşılanır: varlık bilgisi sızmaz.
    if not record or (record["is_archived"] and _role(request) not in ARCHIVE_ROLES):
        raise HTTPException(404, "Aktivite kaydı bulunamadı")
    return record
