from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote

from fastapi import HTTPException, Request
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, insert, select
from sqlalchemy.orm import Session

from .auth import utcnow
from .tenancy import companies

POLICY_BLOCK: Final = "block"
POLICY_MANAGER_OVERRIDE: Final = "manager_override"
POLICY_ALLOW: Final = "allow"
VALID_POLICY_MODES: Final = {POLICY_BLOCK, POLICY_MANAGER_OVERRIDE, POLICY_ALLOW}
MANAGER_OVERRIDE_ROLES: Final = {"admin", "yonetici"}

metadata = MetaData()
policy_override_logs = Table(
    "policy_override_logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("company_id", Integer, nullable=False, index=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("username", String(80), nullable=False),
    Column("policy_name", String(60), nullable=False),
    Column("resource_type", String(80), nullable=False),
    Column("resource_id", Integer),
    Column("reason", Text, nullable=False),
    Column("request_id", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


@dataclass(frozen=True)
class PolicySettings:
    negative_stock_policy: str = POLICY_BLOCK
    credit_limit_policy: str = POLICY_BLOCK


@dataclass(frozen=True)
class PolicyOverrideContext:
    requested: bool
    reason: str | None
    user_id: int
    username: str
    role: str
    request_id: str | None


def override_context(request: Request) -> PolicyOverrideContext:
    raw = request.headers.get("x-policy-override", "").strip().lower()
    requested = raw in {"1", "true", "yes", "on"}
    raw_reason = (request.headers.get("x-policy-override-reason") or "").strip()
    try:
        decoded_reason = unquote(raw_reason)
    except Exception as exc:
        raise HTTPException(400, "Politika istisnası gerekçesi çözülemedi") from exc
    reason = decoded_reason.strip() or None
    if reason and (len(reason) > 500 or any(ord(ch) < 32 for ch in reason)):
        raise HTTPException(400, "Politika istisnası gerekçesi geçersiz")
    user = getattr(request.state, "user", {}) or {}
    return PolicyOverrideContext(
        requested=requested,
        reason=reason,
        user_id=int(user.get("id") or 0),
        username=str(user.get("username") or "unknown"),
        role=str(user.get("role") or ""),
        request_id=getattr(request.state, "request_id", None),
    )


def get_policy_settings(db: Session, company_id: int, *, for_update: bool = False) -> PolicySettings:
    query = select(
        companies.c.negative_stock_policy,
        companies.c.credit_limit_policy,
    ).where(companies.c.id == company_id)
    if for_update and db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update()
    row = db.execute(query).mappings().first()
    if not row:
        raise HTTPException(404, "Firma bulunamadı")
    negative = str(row.get("negative_stock_policy") or POLICY_BLOCK)
    credit = str(row.get("credit_limit_policy") or POLICY_BLOCK)
    return PolicySettings(
        negative_stock_policy=negative if negative in VALID_POLICY_MODES else POLICY_BLOCK,
        credit_limit_policy=credit if credit in VALID_POLICY_MODES else POLICY_BLOCK,
    )


def _validate_manager_override(context: PolicyOverrideContext) -> None:
    if context.role not in MANAGER_OVERRIDE_ROLES:
        raise HTTPException(403, "Bu politika istisnasını yalnızca yönetici onaylayabilir")
    if not context.reason or len(context.reason) < 5:
        raise HTTPException(400, "Politika istisnası için en az 5 karakterlik gerekçe girin")


def negative_stock_allowed(mode: str, context: PolicyOverrideContext) -> bool:
    if mode == POLICY_ALLOW:
        return True
    if mode == POLICY_MANAGER_OVERRIDE and context.requested:
        _validate_manager_override(context)
        return True
    return False


def enforce_known_violation(
    *,
    mode: str,
    context: PolicyOverrideContext,
    blocked_message: str,
    override_message: str,
) -> bool:
    """Raise for a known policy violation or return whether it was overridden."""
    if mode == POLICY_ALLOW:
        return False
    if mode == POLICY_MANAGER_OVERRIDE:
        if not context.requested:
            raise HTTPException(409, override_message)
        _validate_manager_override(context)
        return True
    raise HTTPException(409, blocked_message)


def stock_policy_message(mode: str) -> str:
    if mode == POLICY_MANAGER_OVERRIDE:
        return (
            "Firma negatif stok politikası işlemi engelledi. "
            "Yönetici onayıyla devam etmek için gerekçe girin."
        )
    return "Firma negatif stok politikası işlemi engelledi; depoda yeterli stok yok."


def record_policy_overrides(
    db: Session,
    *,
    company_id: int,
    context: PolicyOverrideContext,
    policy_names: set[str],
    resource_type: str,
    resource_id: int | None,
) -> None:
    if not policy_names:
        return
    # Audit persistence is the final policy boundary. Callers cannot manufacture
    # an override log to bypass the explicit request, manager role, or reason
    # checks performed by the policy layer.
    if not context.requested:
        raise HTTPException(409, "Bu işlem yönetici onayı ve gerekçe gerektirir")
    _validate_manager_override(context)
    for policy_name in sorted(policy_names):
        db.execute(
            insert(policy_override_logs).values(
                company_id=company_id,
                user_id=context.user_id,
                username=context.username,
                policy_name=policy_name,
                resource_type=resource_type,
                resource_id=resource_id,
                reason=context.reason,
                request_id=context.request_id,
                created_at=utcnow(),
            )
        )
