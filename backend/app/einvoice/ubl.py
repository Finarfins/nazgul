"""Build a TR e-Fatura UBL/JSON payload from an invoice snapshot.

Monetary contract: every amount goes through :mod:`app.money` (Decimal, never
float), and the payload's ``tax_total`` and ``payable_amount`` equal the
invoice ``totals_snapshot`` to the kuruş. The result is fully JSON-serialisable
(amounts are Decimal-formatted strings), so it can be persisted as a TEXT JSON
string and, later, handed straight to a real provider adapter.

Increment 1 additionally fills the UBL-TR 1.2 header/party/total fields listed in
``docs/efatura-adapter-spec.md`` §4 and stamps the **client ETTN** — a stable
UUIDv5 derived from company id + invoice id. That is the idempotency key of
spec §7: resending the same invoice always carries the same ETTN, so the
provider can reject the second envelope as a duplicate instead of issuing a
second legal document.

What this module deliberately does **not** do is guess the buyer's e-Fatura
registration. ``profile_id``/``channel`` are only filled when the caller passes
``is_efatura_user`` (the answer of a real ``check_taxpayer`` call). Unknown stays
``None`` and the adapter refuses to submit — it never defaults to EFATURA.
"""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..money import HUNDRED, ZERO, money, percentage, quantity


#: Namespace for the client ETTN. Fixed forever: changing it would re-issue new
#: ETTNs for existing invoices and break duplicate detection at the provider.
ETTN_NAMESPACE = uuid.UUID("6f1e5f52-2b2f-5f7c-9a4e-1d9d2f2f0a11")

#: UBL-TR profiles. Which one applies is decided by the buyer's registration,
#: never by a default.
PROFILE_EFATURA = "TICARIFATURA"
PROFILE_EARSIV = "EARSIVFATURA"

#: GİB code lists (not provider wire details — these are published UBL-TR values).
INVOICE_TYPE_CODE = "SATIS"
DEFAULT_UNIT_CODE = "C62"  # UN/ECE Rec.20: "one / adet"

CHANNEL_EFATURA = "EFATURA"
CHANNEL_EARSIV = "EARSIV"

#: Fields a provider must have before an envelope may leave the building.
REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("uuid", "ETTN"),
    ("invoice_number", "fatura numarası"),
    ("issue_date", "düzenlenme tarihi"),
    ("profile_id", "e-Fatura profili (alıcı mükellefiyeti belirlenmedi)"),
    ("supplier.vkn", "gönderici VKN"),
    ("supplier.name", "gönderici unvanı"),
    ("customer.vkn_tckn", "alıcı VKN/TCKN"),
    ("customer.name", "alıcı unvanı"),
    ("lines", "fatura satırı"),
)


#: ``PriceAmount`` birim fiyattır ve matrahın miktara bölümüdür; 2 hane yetmez.
#: 100.00 / 3 = 33.33 olsaydı 33.33 × 3 = 99.99 ≠ 100.00 çıkardı. 6 haneyle
#: ``round(PriceAmount × Quantity, 2) == LineExtensionAmount`` her satırda tutar.
PRICE_QUANTUM = Decimal("0.000001")


def _amount(value: Any) -> str:
    """Kuruş-accurate money as a canonical string (2dp, ROUND_HALF_UP)."""
    return str(money(value))


def _taxable_base(line_total: Decimal, tax_rate: Decimal) -> Decimal:
    """KDV **dahil** satır toplamından matrahı çıkar: ``total / (1 + oran/100)``.

    Anlık görüntüdeki ``tax_amount`` kasıtlı olarak kullanılmaz: o değer başka
    bir yuvarlama zincirinden gelmiş olabilir ve UBL satırını kendi içinde
    tutarsız bırakır. Belgede tek otorite bu formüldür.
    """
    if tax_rate == ZERO:
        return money(line_total)
    return money(line_total / (1 + tax_rate / HUNDRED))


def _unit_price_excl_vat(taxable: Decimal, line_quantity: Decimal) -> str:
    """KDV hariç birim fiyat — UBL-TR ``Price/PriceAmount`` bunu ister.

    Miktar sıfırsa bölme yapılmaz; matrah olduğu gibi yazılır (sıfır miktarlı
    satır zaten geçerli bir fatura satırı değildir, ama burada patlamak yerine
    veriyi olduğu gibi taşımak doğrusu — doğrulama başka katmanın işi).
    """
    if line_quantity == ZERO:
        return str(money(taxable))
    return str((taxable / line_quantity).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP))


def build_client_ettn(company_id: Any, invoice_id: Any) -> str | None:
    """Stable ETTN for one invoice — the idempotency key of spec §7.

    Deterministic: the same (company, invoice) pair always yields the same UUID,
    so a retried submission resolves to the same document at the provider.
    Returns ``None`` when either id is missing rather than inventing one.
    """
    if company_id in (None, "") or invoice_id in (None, ""):
        return None
    return str(uuid.uuid5(ETTN_NAMESPACE, f"sungur-tarim-erp:{company_id}:{invoice_id}"))


def resolve_channel(is_efatura_user: Any) -> tuple[str | None, str | None]:
    """(channel, profile_id) from a **known** taxpayer answer; ``(None, None)`` if unknown.

    Mirrors spec §3: there is no optimistic default. Until ``check_taxpayer``
    actually answers, the invoice has no channel and cannot be submitted.
    """
    if is_efatura_user is True:
        return CHANNEL_EFATURA, PROFILE_EFATURA
    if is_efatura_user is False:
        return CHANNEL_EARSIV, PROFILE_EARSIV
    return None, None


def missing_required_fields(payload: dict[str, Any]) -> list[str]:
    """Human-readable names of the UBL fields the payload still lacks."""
    missing: list[str] = []
    for path, label in REQUIRED_FIELDS:
        node: Any = payload
        for key in path.split("."):
            node = node.get(key) if isinstance(node, dict) else None
        if node in (None, "", [], {}):
            missing.append(label)
    return missing


def _issue_parts(issued_at: Any) -> tuple[str | None, str | None]:
    """Split the invoice timestamp into UBL ``IssueDate`` / ``IssueTime``."""
    if issued_at in (None, ""):
        return None, None
    text = str(issued_at)
    date_part = text[:10]
    time_part = text[11:19] if len(text) >= 19 else None
    return (date_part or None), (time_part or None)


def build_einvoice_payload(invoice_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Map the existing invoice snapshots into TR e-Fatura fields.

    ``invoice_snapshot`` carries ``company`` (seller), ``customer`` (buyer),
    ``totals`` (the totals_snapshot), ``items`` (resolved lines) and ``currency``.
    Optional: ``company_id``/``invoice_id`` (ETTN), ``issued_at`` (IssueDate/Time)
    and ``is_efatura_user`` (channel/profile).
    """
    company = invoice_snapshot.get("company") or {}
    customer = invoice_snapshot.get("customer") or {}
    totals = invoice_snapshot.get("totals") or {}
    items = invoice_snapshot.get("items") or []
    currency = str(invoice_snapshot.get("currency") or "TRY").upper()

    lines: list[dict[str, Any]] = []
    taxable_sum = ZERO
    tax_subtotals: dict[str, list[Decimal]] = {}
    for index, item in enumerate(items, start=1):
        line_total = money(item.get("total", 0))
        rate_value = percentage(item.get("tax_rate", 0))
        rate = str(rate_value)
        # Matrah, anlık görüntüdeki ``tax_amount``'tan DEĞİL, sözleşme
        # formülünden gelir: ``line_total / (1 + oran/100)``. Kaydedilmiş vergi
        # tutarına güvenmek, satırın kendi içinde tutarsız kalmasına yol açıyordu
        # (miktar × birim fiyat = brüt, ama LineExtensionAmount = matrah).
        taxable = _taxable_base(line_total, rate_value)
        tax_amount = money(line_total - taxable)
        taxable_sum += taxable
        bucket = tax_subtotals.setdefault(rate, [ZERO, ZERO])
        bucket[0] += taxable
        bucket[1] += tax_amount
        line_quantity = quantity(item.get("qty", 0))
        lines.append(
            {
                "id": index,
                "name": item.get("description"),
                "quantity": str(line_quantity),
                "unit_code": str(item.get("unit_code") or DEFAULT_UNIT_CODE),
                # Bizim alanımız KDV DAHİL birim fiyat; UBL-TR ``PriceAmount``
                # alanında HARİÇ ister. İkisi de taşınır, karıştırılmasın diye
                # ayrı adlarla.
                "unit_price": _amount(item.get("unit_price", 0)),
                "unit_price_excl_vat": _unit_price_excl_vat(taxable, line_quantity),
                "tax_rate": rate,
                "tax_amount": _amount(tax_amount),
                # UBL LineExtensionAmount is the taxable base, not the gross line.
                "line_extension_amount": _amount(taxable),
                "line_total": _amount(line_total),
            }
        )

    channel, profile_id = resolve_channel(invoice_snapshot.get("is_efatura_user"))
    issue_date, issue_time = _issue_parts(invoice_snapshot.get("issued_at"))
    tax_total = money(totals.get("tax", 0))
    payable = money(totals.get("grand_total", 0))

    return {
        "ubl_version": "UBL-TR 1.2",
        "profile_id": profile_id,
        "channel": channel,
        "invoice_type_code": INVOICE_TYPE_CODE,
        "invoice_number": invoice_snapshot.get("invoice_number"),
        # Client ETTN (spec §7 idempotency key). Empty when the ids are unknown —
        # the adapter then refuses to submit instead of minting a random one.
        "uuid": build_client_ettn(
            invoice_snapshot.get("company_id") or company.get("id"),
            invoice_snapshot.get("invoice_id"),
        ),
        "issue_date": issue_date,
        "issue_time": issue_time,
        "currency": currency,
        "document_currency_code": currency,
        "supplier": {
            "vkn": company.get("tax_number"),
            "name": company.get("name"),
            "tax_office": company.get("tax_office"),
            "address": company.get("address"),
        },
        "customer": {
            "vkn_tckn": customer.get("tax_number"),
            "name": customer.get("name"),
            "address": customer.get("address"),
        },
        "lines": lines,
        "tax_subtotals": [
            {
                "tax_rate": rate,
                "taxable_amount": _amount(values[0]),
                "tax_amount": _amount(values[1]),
            }
            for rate, values in sorted(tax_subtotals.items())
        ],
        # Sözleşme: ``tax_total`` ve ``payable_amount`` anlık görüntüdeki
        # ``tax`` / ``grand_total`` ile kuruşuna kadar aynı kalır — belgenin
        # dışa dönük tutarı faturanın kendisidir.
        #
        # ``tax_exclusive_amount`` ise satırlardan türetilir, ``payable - tax``
        # farkından değil. Sebep: UBL içinde ``Σ LineExtensionAmount ==
        # TaxExclusiveAmount`` tutmak zorunda; iki ayrı yuvarlama zincirinden
        # gelen iki değeri eşit varsaymak kuruş farkı doğuruyordu.
        "monetary_total": {
            "tax_total": _amount(tax_total),
            "payable_amount": _amount(payable),
            "line_extension_amount": _amount(taxable_sum),
            "tax_exclusive_amount": _amount(taxable_sum),
            "tax_inclusive_amount": _amount(payable),
        },
    }
