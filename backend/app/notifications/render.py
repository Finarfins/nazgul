"""§2.5 kanonik render boru hattı ve ``content_hash``.

Hash **şablon gövdesinden değil, kanonik render edilmiş nihai payload'dan**
üretilir. Şablon metnini hash'lemek yetersizdir: aynı şablon farklı müşteriye,
farklı tutara, farklı vadeye render edilir ve bunların hepsi farklı mesajlardır.
Aynı ``harvest.due_soon`` şablonundan çıkan 1.000,00 TL ile 10.000,00 TL
**farklı** ``content_hash``'lerdir; ilkini onaylamak ikincisini onaylamaz.

Zorunlu sıra (atlanamaz, yeri değiştirilemez):

1. Değişken doğrulama — allow-list üyeliği, tip, uzunluk
2. Render
3. Normalizasyon + URL denetimi (§5.5)
4. Gösterilecek nihai payload'ın kanonikleştirilmesi
5. Hash BU payload'dan
6. Onay CAS'i (``service.approve_notification``)
7. Dispatch öncesi yeniden doğrulama (``service.send_notification``)

6 ve 7 bu modülde değil servis katmanındadır; buradaki 1-5 onların girdisidir.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..activity_log import format_money_tr
from ..money import decimal_value
from .content_gate import ContentRejected, assert_content_allowed, fold_typographic


class TemplateRenderError(ValueError):
    """Şablon değişkeni doğrulamasına ya da render'a takıldı."""


# ---------------------------------------------------------------------------
# §4.2 değişken allow-list'i — tip ve uzunluk sınırlarıyla
# ---------------------------------------------------------------------------
# Sınır aşılırsa render HATA verir; sessizce kırpılmaz. Kırpma, kontrol
# karakteriyle bölünmüş bir şemayı (§5.5) gizleyebilir.
TEXT = "text"
DATE = "date"
MONEY = "money"

VARIABLE_SPECS: dict[str, tuple[str, int]] = {
    "musteri_adi": (TEXT, 80),
    "firma_adi": (TEXT, 80),
    "makine_adi": (TEXT, 60),
    "randevu_tarihi": (DATE, 10),
    "vade_tarihi": (DATE, 10),
    "tutar": (MONEY, 24),
    "is_emri_no": (TEXT, 40),
    "belge_no": (TEXT, 40),
}

_PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def template_variables(body: str) -> list[str]:
    """Gövdedeki değişken adlarını sırayla döndürür (tekrarsız)."""
    seen: list[str] = []
    for name in _PLACEHOLDER_RE.findall(body or ""):
        if name not in seen:
            seen.append(name)
    return seen


def strip_placeholders(body: str, replacement: str = "deger") -> str:
    """Yer tutucuları nötr bir sözcükle değiştirir (şablon gövdesi denetimi)."""
    return _PLACEHOLDER_RE.sub(replacement, body or "")


def assert_template_variables_known(body: str) -> None:
    """Katalog dışı değişken kullanan şablonu reddeder.

    Bilinmeyen değişken sessiz boş string'e dönüşmez — bu, gönderilmiş
    mesajda görünmez bir boşluk bırakır ve fark edilmez.
    """
    unknown = [name for name in template_variables(body) if name not in VARIABLE_SPECS]
    if unknown:
        raise TemplateRenderError(
            "Bilinmeyen şablon değişkeni: " + ", ".join(sorted(unknown))
        )


def _format_date(value: Any, name: str) -> str:
    if isinstance(value, datetime):
        return value.date().strftime("%d.%m.%Y")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10]).strftime("%d.%m.%Y")
        except ValueError as exc:
            raise TemplateRenderError(f"{name} geçerli bir tarih değil") from exc
    raise TemplateRenderError(f"{name} geçerli bir tarih değil")


def _format_money(value: Any, name: str) -> str:
    # Para her zaman Decimal üzerinden; float yoluna hiç girilmez.
    try:
        amount = decimal_value(value, field_name=name)
    except Exception as exc:  # noqa: BLE001 - mesaj kullanıcıya gösterilecek
        raise TemplateRenderError(f"{name} geçerli bir tutar değil") from exc
    return format_money_tr(amount)


def _format_text(value: Any, name: str) -> str:
    if isinstance(value, Decimal):
        raise TemplateRenderError(f"{name} metin olmalı, tutar verildi")
    text_value = fold_typographic(str(value if value is not None else "")).strip()
    if not text_value:
        raise TemplateRenderError(f"{name} boş olamaz")
    return text_value


def _render_value(name: str, value: Any) -> str:
    kind, max_length = VARIABLE_SPECS[name]
    if kind == DATE:
        rendered = _format_date(value, name)
    elif kind == MONEY:
        rendered = _format_money(value, name)
    else:
        rendered = _format_text(value, name)

    if len(rendered) > max_length:
        # Kırpma yok: sınır aşımı hatadır.
        raise TemplateRenderError(
            f"{name} en fazla {max_length} karakter olabilir (şu an {len(rendered)})"
        )
    return rendered


def render_body(body: str, variables: dict[str, Any]) -> str:
    """Adım 1-3: doğrulama → render → içerik kapısı.

    Serbest metin değişkenleri gövdeye yerleştirilmeden **önce** de kapıdan
    geçirilir; böylece değişken üzerinden link enjeksiyonu render aşamasında
    durdurulur (§4.2).
    """
    assert_template_variables_known(body)

    needed = template_variables(body)
    missing = [name for name in needed if name not in variables]
    if missing:
        raise TemplateRenderError(
            "Eksik şablon değişkeni: " + ", ".join(sorted(missing))
        )

    rendered_values: dict[str, str] = {}
    for name in needed:
        rendered = _render_value(name, variables[name])
        if VARIABLE_SPECS[name][0] == TEXT:
            try:
                assert_content_allowed(rendered)
            except ContentRejected as exc:
                raise TemplateRenderError(
                    f"{name} değeri güvenlik denetiminden geçemedi: {exc}"
                ) from exc
        rendered_values[name] = rendered

    result = _PLACEHOLDER_RE.sub(lambda m: rendered_values[m.group(1)], body)
    # Adım 3: nihai metin üzerinde kapı. Belirleyici kontrol budur.
    assert_content_allowed(result)
    return result


# ---------------------------------------------------------------------------
# Adım 4-5: kanonikleştirme + hash
# ---------------------------------------------------------------------------
def canonical_payload(
    *,
    message_class: str,
    channel: str,
    recipient: str,
    template_id: int | None,
    template_version: int | None,
    subject: str | None,
    body: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Alan sırası sabit, boşluğu daraltılmış kanonik JSON.

    ``build_notification_payload`` ile aynı serileştirme sözleşmesini kullanır
    (sort_keys, ensure_ascii=False, sabit ayraç), böylece iki veritabanında ve
    iki Python sürümünde aynı bayt dizisi üretilir.
    """
    document = {
        "message_class": (message_class or "").strip(),
        "channel": (channel or "").strip().upper(),
        "recipient": (recipient or "").strip(),
        "template_id": template_id,
        "template_version": template_version,
        "subject": " ".join((subject or "").split()),
        "body": " ".join((body or "").split()),
        "metadata": metadata or {},
    }
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def content_hash(canonical: str) -> str:
    """Adım 5: kanonik payload'ın SHA-256 özeti."""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_content(
    *,
    message_class: str,
    channel: str,
    recipient: str,
    template_id: int | None,
    template_version: int | None,
    body_template: str,
    variables: dict[str, Any],
    subject: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """1-5 adımlarını sırayla çalıştırır ve gönderilecek içeriği döndürür."""
    body = render_body(body_template, variables)
    canonical = canonical_payload(
        message_class=message_class,
        channel=channel,
        recipient=recipient,
        template_id=template_id,
        template_version=template_version,
        subject=subject,
        body=body,
        metadata=metadata,
    )
    return {
        "body": body,
        "subject": subject,
        "canonical": canonical,
        "content_hash": content_hash(canonical),
    }
