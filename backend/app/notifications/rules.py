"""F2 reminder rules and the cron-invoked rule engine.

Rule evaluation only creates ``AWAITING_APPROVAL`` outbox rows. It never claims
rows and never calls a provider.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..activity_log import log_activity
from ..auth import utcnow
from .consents import evaluate_consent
from .content_gate import ContentRejected
from .render import TemplateRenderError, build_content
from .service import enqueue_notification
from .templates import get_active_template

TRIGGERS = frozenset({"SERVICE_DUE", "HARVEST_DUE_SOON", "HARVEST_OVERDUE"})
CHANNELS = frozenset({"SMS", "WHATSAPP", "EMAIL"})
MAX_PER_RUN = 50


class RuleError(ValueError):
    pass


def _scope(value: str | dict[str, Any]) -> dict[str, list[int]]:
    try:
        raw = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuleError("Kural kapsamı geçerli JSON olmalıdır") from exc
    if not isinstance(raw, dict) or not raw:
        raise RuleError("Etkin kural sınırlı bir kapsam içermelidir")
    unknown = set(raw) - {"customer_ids", "branch_ids"}
    if unknown:
        raise RuleError("Bilinmeyen kapsam alanı: " + ", ".join(sorted(unknown)))
    normalized: dict[str, list[int]] = {}
    for key, values in raw.items():
        if not isinstance(values, list) or not values:
            raise RuleError(f"{key} boş olmayan bir liste olmalıdır")
        try:
            ids = sorted({int(item) for item in values if int(item) > 0})
        except (TypeError, ValueError) as exc:
            raise RuleError(f"{key} yalnız pozitif kimlikler içermelidir") from exc
        if not ids:
            raise RuleError(f"{key} boş olmayan bir liste olmalıdır")
        normalized[key] = ids
    return normalized


def validate_rule(data: dict[str, Any], *, enabling: bool = False) -> dict[str, Any]:
    trigger = str(data.get("trigger_type") or "").upper()
    channel = str(data.get("channel") or "").upper()
    if trigger not in TRIGGERS:
        raise RuleError("Geçersiz tetikleyici")
    if channel not in CHANNELS:
        raise RuleError("Geçersiz kanal")
    limit = int(data.get("max_per_run") or 0)
    if not 1 <= limit <= MAX_PER_RUN:
        raise RuleError("Tek çalıştırma limiti 1 ile 50 arasında olmalıdır")
    scope = _scope(data.get("scope_filter") or {})
    start, end = data.get("send_window_start"), data.get("send_window_end")
    if enabling and (start is None or end is None):
        raise RuleError("Etkin kuralın gönderim penceresi zorunludur")
    return {**data, "trigger_type": trigger, "channel": channel, "max_per_run": limit, "scope_filter": json.dumps(scope, sort_keys=True)}


def list_rules(db: Session, company_id: int) -> list[dict[str, Any]]:
    rows = db.execute(
        text("""SELECT r.*,t.code template_code,t.name template_name
                  FROM notification_rules r
                  JOIN notification_templates t
                    ON t.id=r.template_id AND t.company_id=r.company_id
                 WHERE r.company_id=:cid ORDER BY r.code"""),
        {"cid": company_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def create_rule(db: Session, company_id: int, user_id: int | None, data: dict[str, Any]) -> dict[str, Any]:
    values = validate_rule({**data, "is_enabled": False})
    template = get_active_template(db, company_id=company_id, code=str(data["template_code"]), channel=values["channel"])
    if template is None:
        raise RuleError("Etkin ve onaylı şablon bulunamadı")
    now = utcnow()
    rule_id = db.execute(
        text("""INSERT INTO notification_rules
              (company_id,code,trigger_type,channel,template_id,offset_days,
               is_enabled,send_window_start,send_window_end,max_per_run,
               scope_filter,created_by,created_at,updated_at)
              VALUES (:cid,:code,:trigger_type,:channel,:template_id,:offset_days,
               :is_enabled,:send_window_start,:send_window_end,:max_per_run,
               :scope_filter,:user_id,:now,:now) RETURNING id"""),
        {**values, "cid": company_id, "template_id": int(template["id"]), "user_id": user_id, "now": now, "is_enabled": False},
    ).scalar_one()
    return next(row for row in list_rules(db, company_id) if int(row["id"]) == int(rule_id))


def set_rule_enabled(db: Session, company_id: int, rule_id: int, enabled: bool, user_id: int | None) -> tuple[dict[str, Any], int]:
    row = db.execute(text("SELECT * FROM notification_rules WHERE company_id=:cid AND id=:id"), {"cid": company_id, "id": rule_id}).mappings().first()
    if row is None:
        raise LookupError("Kural bulunamadı")
    validate_rule(dict(row), enabling=enabled)
    now = utcnow()
    updated = db.execute(
        text("""UPDATE notification_rules
                  SET is_enabled=:enabled,approved_by=:approved_by,updated_at=:now
                WHERE company_id=:cid AND id=:id"""),
        {"enabled": enabled, "approved_by": user_id if enabled else None, "now": now, "cid": company_id, "id": rule_id},
    )
    if updated.rowcount != 1:
        raise LookupError("Kural bulunamadı")
    disarmed = 0
    if not enabled:
        result = db.execute(
            text("""UPDATE notifications
                      SET dispatch_armed=:disarmed,disarmed_at=:now,updated_at=:now
                    WHERE company_id=:cid AND rule_id=:rid
                      AND status IN ('PENDING','RETRY_SCHEDULED')"""),
            {"disarmed": False, "now": now, "cid": company_id, "rid": rule_id},
        )
        disarmed = int(result.rowcount or 0)
    action = "notification.rule_enabled" if enabled else "notification.rule_disabled"
    log_activity(db, company_id, user_id, action, "notification_rule", rule_id, "Bildirim kuralı etkinleştirildi" if enabled else "Bildirim kuralı kapatıldı", {"disarmed_count": disarmed})
    if disarmed:
        log_activity(db, company_id, user_id, "notification.rows_disarmed", "notification_rule", rule_id, f"Kuyruktaki {disarmed} bildirim durduruldu", {"disarmed_count": disarmed})
    current = next(item for item in list_rules(db, company_id) if int(item["id"]) == rule_id)
    return current, disarmed


def _scheduled_for(rule: dict[str, Any], now: datetime) -> datetime:
    start = rule.get("send_window_start")
    end = rule.get("send_window_end")
    local = now.astimezone(timezone(timedelta(hours=3)))
    if isinstance(start, str):
        start = time.fromisoformat(start)
    if isinstance(end, str):
        end = time.fromisoformat(end)
    if local.weekday() < 5 and start and end and start <= local.time().replace(tzinfo=None) <= end:
        return now
    candidate = local.date()
    if local.weekday() >= 5 or (start and local.time().replace(tzinfo=None) > start):
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return datetime.combine(candidate, start or time(9), tzinfo=local.tzinfo).astimezone(timezone.utc)


def _candidates(db: Session, rule: dict[str, Any], today: date) -> list[dict[str, Any]]:
    scope = _scope(rule["scope_filter"])
    params: dict[str, Any] = {"cid": int(rule["company_id"]), "target": today - timedelta(days=int(rule["offset_days"])), "limit": int(rule["max_per_run"]) + 1}
    filters = []
    for key, column in (("customer_ids", "c.id"), ("branch_ids", "w.branch_id" if rule["trigger_type"] == "SERVICE_DUE" else "o.branch_id")):
        if key in scope:
            names = []
            for index, value in enumerate(scope[key]):
                name = f"{key}_{index}"
                params[name] = value
                names.append(f":{name}")
            filters.append(f"{column} IN ({','.join(names)})")
    scope_sql = " AND " + " AND ".join(filters) if filters else ""
    if rule["trigger_type"] == "SERVICE_DUE":
        scheduled_date_expr = (
            "date(w.scheduled_date)"
            if db.get_bind().dialect.name == "sqlite"
            else "CAST(w.scheduled_date AS DATE)"
        )
        sql = f"""SELECT w.id source_id,w.work_order_no,c.id customer_id,c.name customer_name,c.phone,
                         w.scheduled_date,m.brand,m.model,NULL amount,NULL document_no
                    FROM work_orders w
                    JOIN customers c ON c.id=w.customer_id AND c.company_id=w.company_id
                    LEFT JOIN machines m ON m.id=w.machine_id AND m.company_id=w.company_id
                   WHERE w.company_id=:cid AND w.status='SCHEDULED'
                     AND {scheduled_date_expr}=:target {scope_sql}
                   ORDER BY w.id LIMIT :limit"""
    else:
        comparison = "=" if rule["trigger_type"] == "HARVEST_DUE_SOON" else "="
        sql = f"""SELECT o.id source_id,o.document_no,c.id customer_id,c.name customer_name,c.phone,
                         NULL scheduled_date,NULL brand,NULL model,
                         (o.final_total-COALESCE(o.paid_amount,0)) amount,
                         o.due_date_normalized
                    FROM orders o
                    JOIN customers c ON c.id=o.customer_id AND c.company_id=o.company_id
                   WHERE o.company_id=:cid AND o.payment_term='HARMAN_VADELI'
                     AND o.due_date_normalized {comparison} :target
                     AND (o.final_total-COALESCE(o.paid_amount,0))>0 {scope_sql}
                   ORDER BY o.due_date_normalized,o.id LIMIT :limit"""
    return [dict(row) for row in db.execute(text(sql), params).mappings().all()]


def evaluate_rule(db: Session, rule: dict[str, Any], *, now: datetime | None = None) -> int:
    moment = now or utcnow()
    candidates = _candidates(db, rule, moment.date())
    if len(candidates) > int(rule["max_per_run"]):
        raise RuleError(f"{rule['code']} kuralı max_per_run sınırını aşıyor; hiçbir satır üretilmedi")
    template = get_active_template(db, company_id=int(rule["company_id"]), code=str(rule["template_code"]), channel=str(rule["channel"]))
    if template is None:
        raise RuleError("Kural şablonu etkin değil")
    company_name = db.execute(text("SELECT name FROM companies WHERE id=:cid"), {"cid": rule["company_id"]}).scalar_one()
    created = 0
    for item in candidates:
        decision = evaluate_consent(db, company_id=int(rule["company_id"]), party_type="CUSTOMER", party_id=int(item["customer_id"]), channel=str(rule["channel"]), recipient=str(item["phone"] or ""))
        if not decision["allowed"]:
            continue
        trigger = str(rule["trigger_type"])
        type_code = {"SERVICE_DUE": "service.reminder", "HARVEST_DUE_SOON": "harvest.due_soon", "HARVEST_OVERDUE": "harvest.overdue"}[trigger]
        variables = {"musteri_adi": item["customer_name"], "firma_adi": company_name}
        metadata = {"source_id": int(item["source_id"]), "rule_id": int(rule["id"])}
        if trigger == "SERVICE_DUE":
            variables.update({"makine_adi": " ".join(filter(None, (item["brand"], item["model"]))) or "Makineniz", "randevu_tarihi": item["scheduled_date"], "is_emri_no": item["work_order_no"]})
        else:
            variables.update({"vade_tarihi": item["due_date_normalized"], "tutar": item["amount"], "belge_no": item["document_no"] or f"#{item['source_id']}"})
        try:
            content = build_content(message_class=str(template["message_class"]), channel=str(rule["channel"]), recipient=str(item["phone"] or ""), template_id=int(template["id"]), template_version=int(template["version"]), body_template=str(template["body"]), variables=variables, metadata=metadata)
        except (TemplateRenderError, ContentRejected):
            continue
        notification_id, was_created = enqueue_notification(
            db, company_id=int(rule["company_id"]), type_=type_code, channel=str(rule["channel"]), recipient=str(item["phone"] or ""), template=str(template["code"]),
            payload_dict={"body": content["body"], "subject": None, "metadata": metadata, "party_type": "CUSTOMER", "party_id": int(item["customer_id"])},
            dedupe_key=f"{type_code}:{rule['id']}:{item['source_id']}:{params_date(item)}", created_by=None,
            message_class=str(template["message_class"]), template_id=int(template["id"]), template_version=int(template["version"]), content_hash=content["content_hash"], consent=decision, rule_id=int(rule["id"]),
            return_created=True,
        )
        db.execute(text("UPDATE notifications SET scheduled_for=:scheduled WHERE company_id=:cid AND id=:id"), {"scheduled": _scheduled_for(rule, moment), "cid": rule["company_id"], "id": notification_id})
        if was_created:
            created += 1
    return created


def params_date(item: dict[str, Any]) -> str:
    value = item.get("scheduled_date") or item.get("due_date_normalized")
    return str(value)[:10]


def run_enabled_rules(db: Session, *, now: datetime | None = None, company_id: int | None = None) -> dict[int, int]:
    where = "WHERE r.is_enabled=:enabled"
    params: dict[str, Any] = {"enabled": True}
    if company_id is not None:
        where += " AND r.company_id=:cid"
        params["cid"] = company_id
    rows = db.execute(text(f"""SELECT r.*,t.code template_code FROM notification_rules r
                               JOIN notification_templates t ON t.id=r.template_id AND t.company_id=r.company_id
                               {where} ORDER BY r.company_id,r.id"""), params).mappings().all()
    results: dict[int, int] = {}
    for row in rows:
        results[int(row["id"])] = evaluate_rule(db, dict(row), now=now)
    return results
