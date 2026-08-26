"""Work order attachments: photos and customer signatures (Servis v2 FAZ-1).

Files are stored OUTSIDE the release directory, under
``{SUNGUR_DATA_DIR}/attachments/{company_id}/{work_order_id}/``, because the
production deploy swaps the release directory wholesale — anything written into
the application tree is lost on the next deploy. The database keeps metadata
only. Path handling fails closed: the stored file name is a fresh UUID (client
names are display metadata only) and every resolved path is re-verified to stay
inside the data root before it is written to or served.

Mutation rules follow the work-order freeze exactly: terminal work orders
(DELIVERED/CANCELLED) and invoiced work orders reject uploads and deletions
with 409. Deletion is a soft delete — metadata is flagged, the file stays on
disk (FAZ-1 decision).
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import utcnow
from ..change_history import record_change
from ..config import settings
from ..db import get_db
from ..tenancy import company_id
from .work_orders import ensure_work_order_mutable, ensure_work_order_unbilled

# Deliberately NOT nested under /work-orders/{id}: the body-limit middleware
# matches by path prefix, so a nested upload route would have to raise the cap
# for every /api/work-orders/* endpoint. Its own prefix keeps the higher upload
# limit surgical and leaves the work-order JSON routes on the strict global cap.
router = APIRouter(
    prefix="/work-order-attachments/{work_order_id}", tags=["work-order-attachments"]
)

ATTACHMENT_KINDS = {"photo", "signature", "other"}
_CHUNK_BYTES = 64 * 1024
# Display-name sanitization: strip any path components, then keep a conservative
# character set. The stored on-disk name never derives from client input.
_UNSAFE_CHARS = re.compile(r"[^\w.\- ]", flags=re.UNICODE)
_EXTENSION_RE = re.compile(r"^[A-Za-z0-9]{1,10}$")

ATTACHMENT_COLUMNS = """a.id,a.company_id,a.work_order_id,a.file_name,a.content_type,
    a.file_size,a.kind,a.uploaded_by,a.created_at"""


def _storage_root() -> Path:
    return Path(settings.sungur_data_dir).resolve()


def _sanitized_display_name(raw_name: str | None) -> str:
    # Take the basename across both separator conventions, then whitelist.
    candidate = (raw_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    candidate = _UNSAFE_CHARS.sub("_", candidate).strip(" .")
    if not candidate:
        raise HTTPException(422, "Geçersiz dosya adı")
    return candidate[:255]


def _stored_file_name(display_name: str) -> str:
    extension = ""
    if "." in display_name:
        tail = display_name.rsplit(".", 1)[-1]
        if _EXTENSION_RE.fullmatch(tail):
            extension = f".{tail.lower()}"
    return f"{uuid.uuid4().hex}{extension}"


def _resolved_under_root(relative_path: str) -> Path:
    """Resolve a stored relative path and fail closed if it escapes the root."""
    root = _storage_root()
    target = (root / relative_path).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(422, "Dosya yolu veri dizininin dışına çıkamaz")
    return target


def _require_mutable_work_order(db: Session, cid: int, work_order_id: int) -> None:
    lock_clause = " FOR UPDATE" if db.get_bind().dialect.name == "postgresql" else ""
    row = db.execute(
        text(
            f"SELECT status FROM work_orders WHERE id=:id AND company_id=:cid{lock_clause}"
        ),
        {"id": work_order_id, "cid": cid},
    ).first()
    if not row:
        raise HTTPException(404, "İş emri bulunamadı")
    ensure_work_order_mutable(str(row[0]))
    # Full freeze after invoicing (FAZ-1 decision): no attachment mutations on a
    # billed work order, no exceptions.
    ensure_work_order_unbilled(db, cid, work_order_id)


def _require_work_order(db: Session, cid: int, work_order_id: int) -> None:
    row = db.execute(
        text("SELECT 1 FROM work_orders WHERE id=:id AND company_id=:cid"),
        {"id": work_order_id, "cid": cid},
    ).first()
    if not row:
        raise HTTPException(404, "İş emri bulunamadı")


def _attachment_row(
    db: Session, cid: int, work_order_id: int, attachment_id: int, *, with_path: bool = False
) -> dict[str, Any]:
    path_column = ",a.storage_path" if with_path else ""
    row = db.execute(
        text(
            f"""SELECT {ATTACHMENT_COLUMNS}{path_column},
            u.username uploaded_by_username,u.display_name uploaded_by_name
            FROM work_order_attachments a
            JOIN app_users u ON u.id=a.uploaded_by
            WHERE a.id=:id AND a.work_order_id=:work_order_id AND a.company_id=:cid
              AND a.deleted_at IS NULL"""
        ),
        {"id": attachment_id, "work_order_id": work_order_id, "cid": cid},
    ).mappings().first()
    if not row:
        raise HTTPException(404, "İş emri eki bulunamadı")
    return dict(row)


@router.get("")
def list_attachments(work_order_id: int, request: Request, db: Session = Depends(get_db)):
    cid = company_id(request)
    _require_work_order(db, cid, work_order_id)
    rows = db.execute(
        text(
            f"""SELECT {ATTACHMENT_COLUMNS},
            u.username uploaded_by_username,u.display_name uploaded_by_name
            FROM work_order_attachments a
            JOIN app_users u ON u.id=a.uploaded_by
            WHERE a.work_order_id=:work_order_id AND a.company_id=:cid
              AND a.deleted_at IS NULL
            ORDER BY a.id"""
        ),
        {"work_order_id": work_order_id, "cid": cid},
    ).mappings().all()
    return [dict(row) for row in rows]


@router.post("", status_code=201)
def upload_attachment(
    work_order_id: int,
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form("photo"),
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    normalized_kind = kind.strip().lower()
    if normalized_kind not in ATTACHMENT_KINDS:
        raise HTTPException(422, "Geçersiz ek türü; photo, signature veya other olmalıdır")
    _require_mutable_work_order(db, cid, work_order_id)
    display_name = _sanitized_display_name(file.filename)

    relative_path = "/".join(
        ("attachments", str(cid), str(work_order_id), _stored_file_name(display_name))
    )
    target = _resolved_under_root(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    max_bytes = settings.max_attachment_upload_bytes
    received = 0
    try:
        with target.open("wb") as handle:
            while True:
                chunk = file.file.read(_CHUNK_BYTES)
                if not chunk:
                    break
                received += len(chunk)
                if received > max_bytes:
                    raise HTTPException(
                        413,
                        f"Dosya boyutu izin verilen sınırı aşıyor ({max_bytes} bayt)",
                    )
                handle.write(chunk)
        if received == 0:
            raise HTTPException(422, "Boş dosya yüklenemez")
        content_type = (file.content_type or "application/octet-stream")[:120]
        attachment_id = db.execute(
            text(
                """INSERT INTO work_order_attachments(
                company_id,work_order_id,file_name,content_type,file_size,
                storage_path,kind,uploaded_by,created_at
                ) VALUES(
                :cid,:work_order_id,:file_name,:content_type,:file_size,
                :storage_path,:kind,:uploaded_by,:now
                ) RETURNING id"""
            ),
            {
                "cid": cid,
                "work_order_id": work_order_id,
                "file_name": display_name,
                "content_type": content_type,
                "file_size": received,
                "storage_path": relative_path,
                "kind": normalized_kind,
                "uploaded_by": int((getattr(request.state, "user", {}) or {})["id"]),
                "now": utcnow(),
            },
        ).scalar_one()
        after = _attachment_row(db, cid, work_order_id, int(attachment_id))
        record_change(
            db,
            request,
            company_id=cid,
            entity_type="work_order_attachment",
            entity_id=int(attachment_id),
            action="create",
            before=None,
            after=after,
        )
        db.commit()
        return after
    except Exception:
        # Metadata write failed or the upload was rejected: never leave an
        # orphan file behind, and never keep a file without its metadata row.
        db.rollback()
        target.unlink(missing_ok=True)
        raise


@router.get("/{attachment_id}/download")
def download_attachment(
    work_order_id: int,
    attachment_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    row = _attachment_row(db, cid, work_order_id, attachment_id, with_path=True)
    target = _resolved_under_root(str(row["storage_path"]))
    if not target.is_file():
        raise HTTPException(404, "Dosya bulunamadı")
    return FileResponse(
        target, media_type=str(row["content_type"]), filename=str(row["file_name"])
    )


@router.delete("/{attachment_id}", status_code=204)
def delete_attachment(
    work_order_id: int,
    attachment_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    cid = company_id(request)
    _require_mutable_work_order(db, cid, work_order_id)
    before = _attachment_row(db, cid, work_order_id, attachment_id)
    # Soft delete only: the row is flagged and the file remains on disk (FAZ-1).
    result = db.execute(
        text(
            """UPDATE work_order_attachments SET deleted_at=:now
            WHERE id=:id AND work_order_id=:work_order_id AND company_id=:cid
              AND deleted_at IS NULL"""
        ),
        {
            "now": utcnow(),
            "id": attachment_id,
            "work_order_id": work_order_id,
            "cid": cid,
        },
    )
    if not result.rowcount:
        db.rollback()
        raise HTTPException(404, "İş emri eki bulunamadı")
    record_change(
        db,
        request,
        company_id=cid,
        entity_type="work_order_attachment",
        entity_id=attachment_id,
        action="delete",
        before=before,
        after=None,
    )
    db.commit()
