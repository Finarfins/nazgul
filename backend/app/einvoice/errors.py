"""Provider error classification and the fixed Turkish message table (spec §6).

Two hard rules live here:

* A raw provider body, URL or status code never reaches the user-facing text.
  Callers put the raw body in ``EInvoiceResult.raw`` (audit) and the *mapped*
  Turkish sentence in ``error``.
* Credentials never reach ``error``, ``raw`` or the log. :func:`scrub` is the
  belt-and-braces pass that redacts them even if a provider echoes them back.
  It redacts on two levels: the exact secret values we hold (login credentials
  *and* the live session/bearer token), plus anything token-*shaped*
  (:func:`mask_tokens`) so a token the provider mints inside a response — one we
  never stored and therefore could not match by value — still cannot land in the
  audit trail.
"""

from __future__ import annotations

import re
from typing import Any

from .endpoints import PROVIDER_ERROR_KEYWORDS


AUTH = "AUTH"
VALIDATION = "VALIDATION"
TAXPAYER = "TAXPAYER"
DUPLICATE = "DUPLICATE"
QUOTA = "QUOTA"
NETWORK = "NETWORK"
UNKNOWN = "UNKNOWN"

REDACTED = "***"

#: spec §6 — sabit son kullanıcı mesajları. Ham sağlayıcı metni buraya girmez.
ERROR_MESSAGES_TR: dict[str, str] = {
    AUTH: "e-Fatura sağlayıcı girişi başarısız — kimlik bilgilerini kontrol edin.",
    VALIDATION: "Fatura e-Fatura biçimine uymuyor: {field}.",
    TAXPAYER: "Bu alıcı e-Fatura mükellefi değil; e-Arşiv olarak kesilecek.",
    DUPLICATE: "Bu fatura zaten gönderilmiş.",
    QUOTA: "e-Fatura kontörü yetersiz.",
    NETWORK: "e-Fatura servisine ulaşılamadı, tekrar denenecek.",
    UNKNOWN: "e-Fatura işlemi tamamlanamadı (kod: {code}).",
}

#: Only these tokens may appear in the ``{code}`` slot of the UNKNOWN message —
#: an arbitrary provider string must not be able to smuggle a URL or a body
#: fragment into the user-facing sentence.
_CODE_ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-.")


def _safe_code(value: Any) -> str:
    """A short, alphanumeric-only echo of the provider code (never a body)."""
    text = "".join(ch for ch in str(value or "") if ch in _CODE_ALLOWED)
    return text[:32] or "BILINMIYOR"


def _safe_field(value: Any) -> str:
    text = str(value or "").strip()
    return text[:80] or "zorunlu alan"


def message_for(code: str, *, field: Any = None, provider_code: Any = None) -> str:
    """Map an error class to its fixed Turkish sentence."""
    template = ERROR_MESSAGES_TR.get(code, ERROR_MESSAGES_TR[UNKNOWN])
    return template.format(field=_safe_field(field), code=_safe_code(provider_code))


def classify_http(status_code: int, body: str = "") -> str:
    """Classify a provider HTTP answer into one of the spec §6 classes.

    HTTP status is authoritative where it is unambiguous; otherwise the body is
    scanned for the ‹doğrulanacak› keyword hints. Anything unrecognised stays
    ``UNKNOWN`` — it is never optimistically treated as success.
    """
    if status_code in (401, 403):
        return AUTH
    if status_code == 409:
        return DUPLICATE
    if status_code in (402, 429):
        return QUOTA
    if status_code in (408, 502, 503, 504) or status_code >= 500:
        return NETWORK
    hint = classify_body(body)
    if hint is not None:
        return hint
    if status_code in (400, 422):
        return VALIDATION
    return UNKNOWN


def classify_body(body: str) -> str | None:
    """Keyword hint from the provider body, or ``None`` when nothing matches."""
    haystack = (body or "").upper()
    if not haystack:
        return None
    for keyword, code in PROVIDER_ERROR_KEYWORDS:
        if keyword in haystack:
            return code
    return None


#: Session / bearer token carriers, masked by *shape* rather than by value.
#: Value-based redaction (:func:`scrub`) only covers tokens we already hold; a
#: provider that mints a NEW token inside an operation response would otherwise
#: land in the audit ``raw`` unmasked. These patterns close that class for both
#: JSON (``"sessionId": "…"``) and SOAP (``<SESSION_ID>…</SESSION_ID>``) bodies.
_TOKEN_KEY = r"(?:session[_-]?id|sessionid|access[_-]?token|refresh[_-]?token|token|bearer|jwt|api[_-]?key)"
_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    # JSON: "sessionId": "value"
    re.compile(rf'("{_TOKEN_KEY}"\s*:\s*")([^"]+)(")', re.IGNORECASE),
    # XML/SOAP: <SESSION_ID>value</SESSION_ID> (namespace prefix tolerated)
    re.compile(rf"(<(?:\w+:)?{_TOKEN_KEY}\s*>)([^<]+)(</)", re.IGNORECASE),
    # Authorization header style: Bearer value
    re.compile(r"(\bBearer\s+)([A-Za-z0-9._\-]{6,})", re.IGNORECASE),
)


def mask_tokens(text: str) -> str:
    """Mask session/bearer token values by shape, whatever their content."""
    for pattern in _TOKEN_PATTERNS:
        if pattern.groups == 3:
            text = pattern.sub(rf"\1{REDACTED}\3", text)
        else:
            text = pattern.sub(rf"\1{REDACTED}", text)
    return text


def scrub(value: Any, secrets: tuple[str, ...]) -> Any:
    """Redact configured credentials anywhere they appear, recursively.

    Applied to every ``error`` string and every ``raw`` payload before it leaves
    the adapter, so a provider that echoes the login body back cannot leak the
    password into the audit trail.
    """
    live = tuple(s for s in secrets if s and len(str(s)) >= 3)
    if isinstance(value, str):
        for secret in live:
            value = value.replace(str(secret), REDACTED)
        # Second layer: mask token-shaped values we may never have held.
        return mask_tokens(value)
    if isinstance(value, dict):
        return {key: scrub(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, secrets) for item in value]
    return value


class EInvoiceError(RuntimeError):
    """Loud failure for the methods that cannot express failure in their return type.

    ``fetch_pdf`` returns ``bytes`` and ``check_taxpayer`` returns a ``dict`` of
    booleans; neither can carry a FAILED result, so they raise instead of
    returning a plausible-looking default (spec §1: no silent fake success).
    """

    def __init__(self, code: str, message: str, *, raw: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.raw: dict[str, Any] = raw or {}
