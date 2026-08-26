"""Tenant-scoped notification outbox API (FAZ-1).

Yetki ayrımı (tasarım §6) uçlara birebir yansır:

* ``notifications``          → kuyruk ve rapor görüntüleme
* ``notifications_approve``  → gönderim onayı (dört-göz kuralına tabi)
* ``notifications_dispatch`` → onaylanmış satırın tetiklenmesi / retry
* ``notifications_admin``    → şablon, sınıf onayı, izin yönetimi

Onaylama ile tetikleme bilinçli olarak ayrıdır; şablonu değiştirebilen kişi
tek başına toplu gönderim yapamaz.
"""

from __future__ import annotations

import json
from datetime import datetime, time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..activity_log import log_request_activity
from ..auth import has_permission
from ..db import get_db
from ..notifications import (
    ContentRejected,
    NotificationApprovalError,
    NotificationBusyError,
    NotificationNotFoundError,
    NotificationNotRetryableError,
    approve_notification,
    build_content,
    cancel_notification,
    enqueue_notification,
    evaluate_consent,
    list_consent_events,
    list_consents,
    outbox_counters,
    schedule_retry,
    send_notification,
    set_consent,
)
from ..notifications.consents import ConsentError
from ..notifications.render import TemplateRenderError
from ..notifications.schema import AWAITING_APPROVAL
from ..notifications.templates import (
    TemplateError,
    approve_template,
    create_template,
    get_active_template,
    list_templates,
    update_template,
)
from ..notifications.rules import (
    RuleError,
    create_rule,
    list_rules,
    set_rule_enabled,
)
from ..tenancy import company_id


router = APIRouter(prefix="/notifications", tags=["notifications"])


# ---------------------------------------------------------------------------
# Yetki kapıları
# ---------------------------------------------------------------------------
def _role(request: Request) -> str:
    user = getattr(request.state, "user", {}) or {}
    return str(user.get("role") or "")


def _user_id(request: Request) -> int | None:
    user = getattr(request.state, "user", {}) or {}
    raw = user.get("id")
    return int(raw) if raw is not None else None


def _require(request: Request, permission: str) -> None:
    if not has_permission(_role(request), permission):
        raise HTTPException(403, "Bu işlem için yetkiniz yok")


# ---------------------------------------------------------------------------
# Şemalar
# ---------------------------------------------------------------------------
class NotificationItem(BaseModel):
    id: int
    company_id: int
    type: str
    channel: str
    recipient: str
    template: str
    status: str
    message_class: str | None = None
    content_hash: str | None = None
    dispatch_armed: bool
    disarmed_at: datetime | None = None
    rule_id: int | None = None
    approved_by: int | None = None
    approved_at: datetime | None = None
    created_by: int | None = None
    attempt_count: int
    next_attempt_at: datetime | None = None
    external_id: str | None = None
    last_error: str | None = None
    consent_decision: str | None = None
    consent_decision_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    # UI'da belirsizlik bırakmayan tek satırlık açıklama (§2.9).
    display_state: str


class NotificationPage(BaseModel):
    items: list[NotificationItem]
    skip: int
    limit: int


class NotificationCounters(BaseModel):
    pending: int
    disarmed: int
    awaiting_approval: int
    # sent = yalnız SENT + DELIVERED. SIMULATED ayrı sayaçtır.
    sent: int
    simulated: int
    failed: int
    stopped: int


class NotificationRuleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    trigger_type: Literal["SERVICE_DUE", "HARVEST_DUE_SOON", "HARVEST_OVERDUE"]
    channel: Literal["SMS", "WHATSAPP", "EMAIL"]
    template_code: str = Field(min_length=1, max_length=60)
    offset_days: int = Field(ge=-365, le=365)
    send_window_start: time
    send_window_end: time
    max_per_run: int = Field(ge=1, le=50)
    scope_filter: dict[str, list[int]]


class RuleEnabledRequest(BaseModel):
    enabled: bool


class ApproveRequest(BaseModel):
    # Kullanıcının GÖRDÜĞÜ özet. CAS koşuluna girer: içerik değiştiyse 409.
    seen_hash: str = Field(min_length=64, max_length=64)


class DispatchResult(BaseModel):
    status: str
    message: str | None = None
    external_id: str | None = None


class TemplateCreate(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    channel: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1)
    # §4.0: zorunlu. Varsayılan yok.
    message_class: Literal["SERVICE_TRANSACTIONAL", "COMMERCIAL"]


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    body: str | None = None
    message_class: Literal["SERVICE_TRANSACTIONAL", "COMMERCIAL"] | None = None


class ConsentUpsert(BaseModel):
    party_type: Literal["CUSTOMER", "SUPPLIER"]
    party_id: int = Field(gt=0)
    channel: Literal["SMS", "WHATSAPP", "EMAIL"]
    granted: bool
    source: Literal["FORM", "PHONE", "CONTRACT", "IMPORT"]
    source_ref: str | None = None
    recipient: str | None = None
    reason: str | None = None


class WorkOrderReminderRequest(BaseModel):
    channel: Literal["SMS", "WHATSAPP"] = "SMS"
    template_code: str = "service.reminder"


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------
def _mask_recipient(value: str) -> str:
    """Return enough context for operations without exposing full PII."""
    normalized = (value or "").strip()
    if "@" in normalized:
        local, domain = normalized.split("@", 1)
        return f"{local[:1] or '*'}***@{domain}"
    if len(normalized) <= 4:
        return "*" * len(normalized)
    return f"{'*' * min(8, len(normalized) - 4)}{normalized[-4:]}"


def _display_state(row: dict[str, Any]) -> str:
    """§2.9: durdurulmuş satır belirsiz bir rozetle gösterilmez.

    "Bekliyor"/"zamanlandı" gibi ifadeler satırın bir gün gidebileceği
    izlenimi verir; disarm edilmiş satır için cevap nettir.
    """
    status = str(row.get("status") or "")
    if status in {"PENDING", "RETRY_SCHEDULED"} and not bool(row.get("dispatch_armed")):
        return "Gönderilmeyecek — kural kapatıldı"
    return {
        "AWAITING_APPROVAL": "Onay bekliyor",
        "PENDING": "Gönderim sırasında",
        "RETRY_SCHEDULED": "Yeniden denenecek",
        "PROCESSING": "Gönderiliyor",
        "SENT": "Gönderildi",
        "DELIVERED": "İletildi",
        "SIMULATED": "Simülasyon (dışarı gönderilmedi)",
        "FAILED": "Gönderilemedi",
        "NONE": "Sağlayıcı yapılandırılmamış",
        "CANCELLED": "İptal edildi",
        "REJECTED": "İzin engeli nedeniyle gönderilmedi",
        "COMPLIANCE_CHECK_UNAVAILABLE": "Uyum doğrulaması yapılamadı",
    }.get(status, status)


def _item(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["recipient"] = _mask_recipient(str(item.get("recipient") or ""))
    item["dispatch_armed"] = bool(item.get("dispatch_armed"))
    item["display_state"] = _display_state(row)
    return item


_LIST_COLUMNS = """id, company_id, type, channel, recipient, template, status,
    message_class, content_hash, dispatch_armed, disarmed_at, rule_id,
    approved_by, approved_at, created_by, attempt_count, next_attempt_at,
    external_id, last_error, consent_decision, consent_decision_reason,
    created_at, updated_at"""

# Sekme → SQL yüklemi. "Bekleyenler" YALNIZ silahlı satırları içerir; durdurulan
# satırlar ayrı sekmededir (§2.9 metrik ayrımı).
_TAB_FILTERS = {
    "pending": "status IN ('PENDING','RETRY_SCHEDULED') AND dispatch_armed = :armed",
    "awaiting": "status = 'AWAITING_APPROVAL'",
    "disarmed": (
        "status IN ('PENDING','RETRY_SCHEDULED') AND dispatch_armed = :disarmed"
    ),
    # "Gönderilenler" SIMULATED içermez: simülasyon dışarı hiçbir şey
    # göndermemiştir ve gerçek gönderimlerle aynı listede gösterilmesi
    # operatöre yanlış rapor olur. Simüle satırların kendi görünümü vardır.
    "sent": "status IN ('SENT','DELIVERED')",
    "simulated": "status = 'SIMULATED'",
    "problem": (
        "status IN ('FAILED','NONE','REJECTED','CANCELLED',"
        "'COMPLIANCE_CHECK_UNAVAILABLE')"
    ),
}


# ---------------------------------------------------------------------------
# Kuyruk
# ---------------------------------------------------------------------------
@router.get("/outbox", response_model=NotificationPage)
def list_notification_outbox(
    request: Request,
    tab: Literal[
        "pending", "awaiting", "disarmed", "sent", "simulated", "problem"
    ] = "pending",
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require(request, "notifications")
    cid = company_id(request)
    rows = db.execute(
        text(
            f"""SELECT {_LIST_COLUMNS} FROM notifications
                 WHERE company_id = :cid AND {_TAB_FILTERS[tab]}
                 ORDER BY id DESC
                 LIMIT :limit OFFSET :skip"""
        ),
        {
            "cid": cid,
            "limit": limit,
            "skip": skip,
            "armed": True,
            "disarmed": False,
        },
    ).mappings().all()
    return {"items": [_item(dict(row)) for row in rows], "skip": skip, "limit": limit}


@router.get("/counters", response_model=NotificationCounters)
def notification_counters(
    request: Request, db: Session = Depends(get_db)
) -> dict[str, int]:
    _require(request, "notifications")
    return outbox_counters(db, company_id=company_id(request))


@router.get("/templates")
def get_templates(
    request: Request, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    _require(request, "notifications")
    return list_templates(db, company_id=company_id(request))


@router.post("/templates", status_code=201)
def post_template(
    body: TemplateCreate, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    _require(request, "notifications_admin")
    cid = company_id(request)
    try:
        template = create_template(
            db,
            company_id=cid,
            code=body.code,
            channel=body.channel,
            name=body.name,
            body=body.body,
            message_class=body.message_class,
            user_id=_user_id(request),
        )
    except TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc

    log_request_activity(
        db,
        request,
        cid,
        "notification.template_created",
        "notification_template",
        int(template["id"]),
        f"Bildirim şablonu oluşturuldu: {body.code}",
        {"message_class": body.message_class, "channel": body.channel},
    )
    db.commit()
    return template


@router.patch("/templates/{template_id}")
def patch_template(
    template_id: int,
    body: TemplateUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require(request, "notifications_admin")
    cid = company_id(request)
    try:
        template = update_template(
            db,
            company_id=cid,
            template_id=template_id,
            name=body.name,
            body=body.body,
            message_class=body.message_class,
            user_id=_user_id(request),
        )
    except TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc

    log_request_activity(
        db,
        request,
        cid,
        "notification.template_updated",
        "notification_template",
        template_id,
        f"Bildirim şablonu güncellendi: {template.get('code')}",
        {"version": template.get("version"), "is_active": template.get("is_active")},
    )
    db.commit()
    return template


@router.post("/templates/{template_id}/approve")
def post_template_approval(
    template_id: int, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Sınıf/gövde onayı — şablonu etkinleştirir."""
    _require(request, "notifications_admin")
    cid = company_id(request)
    try:
        template = approve_template(
            db, company_id=cid, template_id=template_id, user_id=_user_id(request)
        )
    except TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc

    log_request_activity(
        db,
        request,
        cid,
        "notification.template_updated",
        "notification_template",
        template_id,
        f"Bildirim şablonu onaylandı: {template.get('code')}",
        {"message_class": template.get("message_class"), "is_active": True},
    )
    db.commit()
    return template


# ---------------------------------------------------------------------------
# Rıza (opt-in)
# ---------------------------------------------------------------------------
@router.get("/consents")
def get_consents(
    request: Request,
    party_id: int = Query(gt=0),
    party_type: Literal["CUSTOMER", "SUPPLIER"] = "CUSTOMER",
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _require(request, "notifications")
    return list_consents(
        db, company_id=company_id(request), party_type=party_type, party_id=party_id
    )


@router.put("/consents")
def put_consent(
    body: ConsentUpsert, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    _require(request, "notifications_admin")
    cid = company_id(request)
    try:
        consent = set_consent(
            db,
            company_id=cid,
            party_type=body.party_type,
            party_id=body.party_id,
            channel=body.channel,
            granted=body.granted,
            source=body.source,
            source_ref=body.source_ref,
            recipient=body.recipient,
            user_id=_user_id(request),
            reason=body.reason,
        )
    except ConsentError as exc:
        raise HTTPException(400, str(exc)) from exc

    log_request_activity(
        db,
        request,
        cid,
        (
            "notification.consent_granted"
            if body.granted
            else "notification.consent_revoked"
        ),
        "notification_consent",
        int(consent["id"]),
        (
            f"Bildirim izni {'verildi' if body.granted else 'kaldırıldı'}: "
            f"{body.channel}"
        ),
        {"party_type": body.party_type, "party_id": body.party_id},
    )
    db.commit()
    return consent


@router.get("/consents/{consent_id}/events")
def get_consent_events(
    consent_id: int, request: Request, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    _require(request, "notifications")
    return list_consent_events(
        db, company_id=company_id(request), consent_id=consent_id
    )


@router.get("/rules")
def get_notification_rules(
    request: Request, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    _require(request, "notifications_admin")
    return list_rules(db, company_id(request))


@router.post("/rules", status_code=201)
def post_notification_rule(
    body: NotificationRuleCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require(request, "notifications_admin")
    try:
        row = create_rule(
            db,
            company_id(request),
            _user_id(request),
            body.model_dump(),
        )
    except RuleError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return row


@router.put("/rules/{rule_id}/enabled")
def put_notification_rule_enabled(
    rule_id: int,
    body: RuleEnabledRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require(request, "notifications_admin")
    try:
        row, disarmed_count = set_rule_enabled(
            db,
            company_id(request),
            rule_id,
            body.enabled,
            _user_id(request),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuleError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"rule": row, "disarmed_count": disarmed_count}


# ---------------------------------------------------------------------------
# İlk gerçek üretici: iş emri hatırlatması (elle)
# ---------------------------------------------------------------------------
@router.post("/work-orders/{work_order_id}/reminder", status_code=201)
def create_work_order_reminder(
    work_order_id: int,
    body: WorkOrderReminderRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Planlanmış iş emri için hatırlatmayı kuyruğa alır — ONAY BEKLER.

    Bu uç hiçbir koşulda gönderim yapmaz; satır ``AWAITING_APPROVAL`` doğar ve
    ayrı yetkiye sahip başka bir kullanıcının açık onayı olmadan dispatch
    edilemez (§5 sabit güvenlik kuralı + §6 dört-göz).
    """
    _require(request, "notifications_approve")
    cid = company_id(request)

    row = db.execute(
        text(
            """SELECT w.id, w.work_order_no, w.scheduled_date, w.status,
                      w.customer_id, c.name AS customer_name, c.phone AS phone,
                      m.brand AS brand, m.model AS model
                 FROM work_orders w
                 LEFT JOIN customers c
                        ON c.id = w.customer_id AND c.company_id = :cid
                 LEFT JOIN machines m
                        ON m.id = w.machine_id AND m.company_id = :cid
                WHERE w.company_id = :cid AND w.id = :wid"""
        ),
        {"cid": cid, "wid": work_order_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "İş emri bulunamadı")
    if row["scheduled_date"] is None:
        raise HTTPException(400, "İş emrinde planlanmış randevu tarihi yok")
    if row["customer_id"] is None:
        raise HTTPException(400, "İş emrine bağlı müşteri yok")

    template = get_active_template(
        db, company_id=cid, code=body.template_code, channel=body.channel
    )
    if template is None:
        raise HTTPException(
            400, "Bu kanal için onaylanmış ve etkin bir şablon bulunamadı"
        )

    company_name = db.execute(
        text("SELECT name FROM companies WHERE id = :cid"), {"cid": cid}
    ).scalar_one_or_none()

    # §3.3 birinci kapı: rıza yoksa kuyruğa HİÇ girmez.
    decision = evaluate_consent(
        db,
        company_id=cid,
        party_type="CUSTOMER",
        party_id=int(row["customer_id"]),
        channel=body.channel,
        recipient=str(row["phone"] or ""),
    )
    if not decision["allowed"]:
        raise HTTPException(400, decision["message"] or "Bildirim izni yok")

    machine_name = (
        " ".join(part for part in (row["brand"], row["model"]) if part) or "Makineniz"
    )
    try:
        content = build_content(
            message_class=str(template["message_class"]),
            channel=body.channel,
            recipient=str(row["phone"] or ""),
            template_id=int(template["id"]),
            template_version=int(template["version"]),
            body_template=str(template["body"]),
            variables={
                "musteri_adi": row["customer_name"],
                "makine_adi": machine_name,
                "randevu_tarihi": row["scheduled_date"],
                "firma_adi": company_name or "Firmamız",
                "is_emri_no": row["work_order_no"],
            },
            metadata={"work_order_id": work_order_id},
        )
    except (TemplateRenderError, ContentRejected) as exc:
        raise HTTPException(400, str(exc)) from exc

    notification_id = enqueue_notification(
        db,
        company_id=cid,
        type_="service.reminder",
        channel=body.channel,
        recipient=str(row["phone"] or ""),
        template=str(template["code"]),
        payload_dict={
            "body": content["body"],
            "subject": None,
            "metadata": {"work_order_id": work_order_id},
            "party_type": "CUSTOMER",
            "party_id": int(row["customer_id"]),
        },
        dedupe_key=(
            f"service.reminder:{work_order_id}:{str(row['scheduled_date'])[:10]}:0"
        ),
        created_by=_user_id(request),
        message_class=str(template["message_class"]),
        template_id=int(template["id"]),
        template_version=int(template["version"]),
        content_hash=content["content_hash"],
        consent=decision,
    )

    log_request_activity(
        db,
        request,
        cid,
        "notification.queued",
        "notification",
        notification_id,
        f"Servis hatırlatması kuyruğa alındı (iş emri #{work_order_id})",
        {"channel": body.channel, "template": template["code"]},
    )
    db.commit()
    return {
        "id": notification_id,
        "status": AWAITING_APPROVAL,
        "content_hash": content["content_hash"],
        "body": content["body"],
    }


# ---------------------------------------------------------------------------
# Tekil işlemler (yol parametreli uçlar en sona: /templates, /consents gibi
# sabit yolların önüne geçmemeleri için)
# ---------------------------------------------------------------------------
@router.get("/{notification_id}/preview")
def preview_notification(
    notification_id: int, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Onay öncesi önizleme: gövde + kullanıcının onaylayacağı ``content_hash``."""
    _require(request, "notifications_approve")
    cid = company_id(request)
    row = db.execute(
        text(
            """SELECT id, channel, recipient, template, status, message_class,
                      content_hash, payload, created_by
                 FROM notifications
                WHERE company_id = :cid AND id = :nid"""
        ),
        {"cid": cid, "nid": notification_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "Bildirim bulunamadı")

    payload = json.loads(row["payload"])
    return {
        "id": int(row["id"]),
        "channel": row["channel"],
        "recipient": _mask_recipient(str(row["recipient"])),
        "template": row["template"],
        "status": row["status"],
        "message_class": row["message_class"],
        "content_hash": row["content_hash"],
        "body": payload.get("body"),
        "subject": payload.get("subject"),
        # Dört-göz: arayüz onay butonunu buna göre kilitler. Sunucu tarafı
        # kontrolü yine de bağımsızdır (approve_notification).
        "can_approve": row["created_by"] != _user_id(request),
    }


@router.post("/{notification_id}/approve", response_model=NotificationItem)
def approve(
    notification_id: int,
    body: ApproveRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require(request, "notifications_approve")
    cid = company_id(request)
    approver = _user_id(request)
    if approver is None:
        raise HTTPException(403, "Onay için kimlik doğrulaması gerekir")
    try:
        row = approve_notification(
            db,
            company_id=cid,
            notification_id=notification_id,
            approver_id=approver,
            seen_hash=body.seen_hash,
        )
    except NotificationNotFoundError as exc:
        raise HTTPException(404, "Bildirim bulunamadı") from exc
    except NotificationApprovalError as exc:
        raise HTTPException(409, str(exc)) from exc

    log_request_activity(
        db,
        request,
        cid,
        "notification.approved",
        "notification",
        notification_id,
        f"Bildirim gönderimi onaylandı (#{notification_id})",
        {"channel": row.get("channel"), "template": row.get("template")},
    )
    db.commit()
    return _item(row)


@router.post("/{notification_id}/cancel", response_model=NotificationItem)
def cancel(
    notification_id: int, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    _require(request, "notifications_approve")
    cid = company_id(request)
    try:
        row = cancel_notification(db, company_id=cid, notification_id=notification_id)
    except NotificationNotFoundError as exc:
        raise HTTPException(404, "Bildirim bulunamadı") from exc
    except NotificationBusyError as exc:
        raise HTTPException(409, str(exc)) from exc

    log_request_activity(
        db,
        request,
        cid,
        "notification.cancelled",
        "notification",
        notification_id,
        f"Bildirim iptal edildi (#{notification_id})",
    )
    db.commit()
    return _item(row)


@router.post("/{notification_id}/dispatch", response_model=DispatchResult)
def dispatch(
    notification_id: int, request: Request, db: Session = Depends(get_db)
) -> DispatchResult:
    """Onaylanmış satırı tetikler. Onay üretmez — yalnız gönderimi başlatır."""
    _require(request, "notifications_dispatch")
    cid = company_id(request)
    try:
        result = send_notification(db, company_id=cid, notification_id=notification_id)
    except NotificationNotFoundError as exc:
        raise HTTPException(404, "Bildirim bulunamadı") from exc
    except (NotificationBusyError, NotificationNotRetryableError) as exc:
        raise HTTPException(409, str(exc)) from exc

    # send_notification kendi transaction'ını commit eder; denetim kaydı ayrı
    # bir transaction'da yazılır ve gönderim sonucunu değiştirmez.
    log_request_activity(
        db,
        request,
        cid,
        (
            "notification.sent"
            if result.status in {"SENT", "DELIVERED", "SIMULATED"}
            else "notification.failed"
        ),
        "notification",
        notification_id,
        f"Bildirim gönderim sonucu: {result.status} (#{notification_id})",
        {"status": result.status},
    )
    db.commit()
    return DispatchResult(
        status=result.status, message=result.message, external_id=result.external_id
    )


@router.post("/{notification_id}/retry", response_model=NotificationItem)
def retry_notification(
    notification_id: int, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Yeniden deneme planlar. Onay taşımaz, rıza engelini aşamaz (§2.6)."""
    _require(request, "notifications_dispatch")
    cid = company_id(request)
    try:
        row = schedule_retry(db, company_id=cid, notification_id=notification_id)
    except NotificationNotFoundError as exc:
        raise HTTPException(404, "Bildirim bulunamadı") from exc
    except (NotificationBusyError, NotificationNotRetryableError) as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return _item(row)
