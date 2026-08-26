"""§4 şablon yönetimi — zorunlu mesaj sınıfı + içerik kapısı.

İki kural bu modülün varlık sebebidir:

* **Sınıfsız şablon yoktur (§4.0).** ``message_class`` zorunludur ve
  varsayılanı yoktur; veritabanı seviyesinde de ``NOT NULL`` + CHECK'tir.
  "Servis hatırlatması işlemseldir" bir varsayımdır, tasarım kararı değil —
  sınıfı yazan taraf açıkça beyan eder.
* **Gövde değişimi şablonu yeniden onaya düşürür.** Metne promosyon eklenmesi
  sınıfı değiştirir; bu yüzden gövde ya da sınıf değişince ``is_active``
  ``FALSE`` olur ve ``notifications_admin`` yeniden etkinleştirene kadar hiçbir
  gönderim bu şablonu kullanamaz.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import utcnow
from .content_gate import ContentRejected, assert_content_allowed
from .render import (
    TemplateRenderError,
    assert_template_variables_known,
    strip_placeholders,
)
from .schema import MESSAGE_CLASSES


class TemplateError(ValueError):
    """Şablon kaydı reddedildi."""


CHANNELS: frozenset[str] = frozenset({"SMS", "WHATSAPP", "EMAIL"})

_COLUMNS = """id, company_id, code, channel, name, body, message_class,
    is_active, version, created_by, created_at, updated_by, updated_at,
    approved_by, approved_at"""


def _validate(body: str, message_class: str, channel: str) -> None:
    if message_class not in MESSAGE_CLASSES:
        raise TemplateError(
            "Mesaj sınıfı SERVICE_TRANSACTIONAL veya COMMERCIAL olmalıdır"
        )
    if channel not in CHANNELS:
        raise TemplateError("Geçersiz bildirim kanalı")
    if not (body or "").strip():
        raise TemplateError("Şablon gövdesi boş olamaz")
    try:
        assert_template_variables_known(body)
    except TemplateRenderError as exc:
        raise TemplateError(str(exc)) from exc
    try:
        # §5.5 kontrol noktası 1: şablon gövdesi. Nihai içerik kontrolü
        # render sırasında ayrıca yapılır; bu, erken ve ucuz olanıdır.
        #
        # Yer tutucular kapıya girmeden nötr bir sözcüğe indirgenir: süslü
        # parantez izin verilen karakter kümesinde değildir ve olmamalıdır da
        # (§5.5). Yer tutucunun kendisi ayrıca ``assert_template_variables_known``
        # ile kapalı katalog üzerinden doğrulanır, değerleri ise render
        # sırasında tek tek kapıdan geçer — yani hiçbir aşama denetimsiz kalmaz.
        assert_content_allowed(strip_placeholders(body), channel=channel)
    except ContentRejected as exc:
        raise TemplateError(str(exc)) from exc


def list_templates(db: Session, *, company_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            f"""SELECT {_COLUMNS} FROM notification_templates
                 WHERE company_id = :cid
                 ORDER BY code, channel"""
        ),
        {"cid": company_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def get_template(
    db: Session, *, company_id: int, template_id: int
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            f"""SELECT {_COLUMNS} FROM notification_templates
                 WHERE company_id = :cid AND id = :template_id"""
        ),
        {"cid": company_id, "template_id": template_id},
    ).mappings().first()
    return dict(row) if row else None


def get_active_template(
    db: Session, *, company_id: int, code: str, channel: str
) -> dict[str, Any] | None:
    """Yalnız etkin şablon döner: onaysız şablonla gönderim yapılamaz."""
    row = db.execute(
        text(
            f"""SELECT {_COLUMNS} FROM notification_templates
                 WHERE company_id = :cid
                   AND code = :code
                   AND channel = :channel
                   AND is_active = :active"""
        ),
        {"cid": company_id, "code": code, "channel": channel, "active": True},
    ).mappings().first()
    return dict(row) if row else None


def create_template(
    db: Session,
    *,
    company_id: int,
    code: str,
    channel: str,
    name: str,
    body: str,
    message_class: str,
    user_id: int | None,
) -> dict[str, Any]:
    """Yeni şablon — her zaman ``is_active = FALSE`` doğar (§4.0)."""
    normalized_channel = (channel or "").strip().upper()
    normalized_class = (message_class or "").strip().upper()
    _validate(body, normalized_class, normalized_channel)

    now = utcnow()
    template_id = int(
        db.execute(
            text(
                """INSERT INTO notification_templates
                   (company_id, code, channel, name, body, message_class,
                    is_active, version, created_by, created_at,
                    updated_by, updated_at)
                   VALUES (:cid, :code, :channel, :name, :body, :message_class,
                    :is_active, 1, :user_id, :now, :user_id, :now)
                   RETURNING id"""
            ),
            {
                "cid": company_id,
                "code": (code or "").strip(),
                "channel": normalized_channel,
                "name": (name or "").strip(),
                "body": body,
                "message_class": normalized_class,
                "is_active": False,
                "user_id": user_id,
                "now": now,
            },
        ).scalar_one()
    )
    return get_template(db, company_id=company_id, template_id=template_id) or {}


def update_template(
    db: Session,
    *,
    company_id: int,
    template_id: int,
    name: str | None,
    body: str | None,
    message_class: str | None,
    user_id: int | None,
) -> dict[str, Any]:
    """Gövde ya da sınıf değişirse versiyon artar ve şablon onaya döner."""
    current = get_template(db, company_id=company_id, template_id=template_id)
    if current is None:
        raise TemplateError("Şablon bulunamadı")

    new_body = current["body"] if body is None else body
    new_class = (
        current["message_class"]
        if message_class is None
        else (message_class or "").strip().upper()
    )
    _validate(new_body, new_class, str(current["channel"]))

    material_change = (
        new_body != current["body"] or new_class != current["message_class"]
    )
    version = int(current["version"]) + (1 if material_change else 0)
    now = utcnow()
    db.execute(
        text(
            """UPDATE notification_templates
                  SET name = :name,
                      body = :body,
                      message_class = :message_class,
                      version = :version,
                      is_active = CASE WHEN :material THEN :inactive ELSE is_active END,
                      approved_by = CASE WHEN :material THEN NULL ELSE approved_by END,
                      approved_at = CASE WHEN :material THEN NULL ELSE approved_at END,
                      updated_by = :user_id,
                      updated_at = :now
                WHERE company_id = :cid AND id = :template_id"""
        ),
        {
            "name": (name or current["name"]).strip(),
            "body": new_body,
            "message_class": new_class,
            "version": version,
            "material": material_change,
            "inactive": False,
            "user_id": user_id,
            "now": now,
            "cid": company_id,
            "template_id": template_id,
        },
    )
    return get_template(db, company_id=company_id, template_id=template_id) or {}


def approve_template(
    db: Session, *, company_id: int, template_id: int, user_id: int | None
) -> dict[str, Any]:
    """Sınıf/gövde onayı — şablonu etkinleştirir (yalnız ``notifications_admin``)."""
    current = get_template(db, company_id=company_id, template_id=template_id)
    if current is None:
        raise TemplateError("Şablon bulunamadı")
    if bool(current["is_active"]):
        raise TemplateError("Şablon zaten etkin")

    now = utcnow()
    db.execute(
        text(
            """UPDATE notification_templates
                  SET is_active = :active,
                      approved_by = :user_id,
                      approved_at = :now,
                      updated_at = :now
                WHERE company_id = :cid AND id = :template_id"""
        ),
        {
            "active": True,
            "user_id": user_id,
            "now": now,
            "cid": company_id,
            "template_id": template_id,
        },
    )
    return get_template(db, company_id=company_id, template_id=template_id) or {}
