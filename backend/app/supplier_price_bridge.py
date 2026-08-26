from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "₺": "TRY"}
CURRENCY_CODES = {"TL": "TRY", "TRY": "TRY", "EUR": "EUR", "USD": "USD"}
NO_PRICE_NOTES = (
    ("STOK MEVCUT-SORUNUZ", "stok mevcut-sorunuz"),
    ("SORUNUZ", "sorunuz"),
    ("KALMADI", "kalmadı"),
    ("STOK AZ", "stok az"),
    ("XXX", "sorunuz"),
)
MULTIPLE_PRICE_PATTERN = re.compile(
    r"(?:€\s*\d[\d\s.,]*|\d[\d\s.,]*\s*EUR)", re.IGNORECASE
)
# Prefer the most actionable diagnosis: competing prices are an explicit
# ambiguity, a stock/status sentinel explains the missing price, and a generic
# parse failure carries the least information.
REASON_PRIORITY = {
    "INVALID_PRICE": 1,
    "NO_PRICE_STATUS": 2,
    "MULTIPLE_PRICES": 3,
}


@dataclass(frozen=True)
class PriceCell:
    amount: Decimal | None
    currency: str | None
    status_note: str | None = None
    reason_code: str | None = None


def collect_reason_codes(
    cash: PriceCell,
    term: PriceCell,
    note: PriceCell | None,
) -> tuple[dict[str, str | None], str | None]:
    codes = {
        "price_cash": cash.reason_code,
        "price_term": term.reason_code,
        "status_note": note.reason_code if note else None,
    }
    review_codes = [cash.reason_code, term.reason_code]
    has_prices = cash.amount is not None and term.amount is not None
    if note and (
        note.reason_code == "MULTIPLE_PRICES"
        or (not has_prices and note.reason_code == "NO_PRICE_STATUS")
    ):
        review_codes.append(note.reason_code)
    eligible = [code for code in review_codes if code]
    primary = (
        max(eligible, key=lambda code: REASON_PRIORITY.get(code, 0))
        if eligible
        else None
    )
    return codes, primary


def normalize_price(value: Any) -> tuple[Decimal, str | None]:
    if value is None:
        raise ValueError("Fiyat boş")
    text = str(value).strip().upper()
    currency = next((code for symbol, code in CURRENCY_SYMBOLS.items() if symbol in text), None)
    for token, code in CURRENCY_CODES.items():
        if re.search(rf"(?<![A-Z]){token}(?![A-Z])", text):
            currency = code
            break
    number = re.sub(r"(EUR|USD|TRY|TL|€|\$|₺)", "", text).strip()
    has_digit_gap = bool(re.search(r"(?<=\d)\s+(?=\d)", number))
    if has_digit_gap and not re.fullmatch(r"[+-]?\d+\s+\d+[,.]\d+", number):
        raise ValueError("Fiyat ayrıştırılamadı")
    number = re.sub(r"(?<=\d)\s+(?=\d)", "", number)
    number = re.sub(r"\s*([,.])\s*", r"\1", number).strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,-", number):
        number = number.replace(".", "")
    number = number.replace(",-", "").replace(".-", "")
    if "," in number and "." in number:
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    elif "," in number:
        number = number.replace(",", ".")
    elif number.count(".") > 1:
        number = number.replace(".", "")
    try:
        parsed = Decimal(number)
    except InvalidOperation as exc:
        raise ValueError("Fiyat ayrıştırılamadı") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("Fiyat geçersiz")
    return parsed.quantize(Decimal("0.0001")), currency


def normalize_price_cell(value: Any) -> PriceCell:
    text = str(value or "").strip()
    normalized = re.sub(r"\s+", " ", text).upper()
    note = next(
        (note for token, note in NO_PRICE_NOTES if token in normalized),
        None,
    )
    if note is not None:
        return PriceCell(None, None, status_note=note, reason_code="NO_PRICE_STATUS")
    if len(MULTIPLE_PRICE_PATTERN.findall(text)) > 1:
        return PriceCell(None, None, status_note=text, reason_code="MULTIPLE_PRICES")
    try:
        amount, currency = normalize_price(value)
    except ValueError:
        return PriceCell(None, None, status_note=text or None, reason_code="INVALID_PRICE")
    return PriceCell(amount, currency)


def vat_details(value: Any) -> tuple[Decimal, bool]:
    text = str(value or "").upper()
    compact = re.sub(r"\s+", "", text)
    included = "KDVDAHİL" in compact or "KDVDAHIL" in compact
    match = re.search(r"(\d[\d\s]*)\s*%", text)
    rate = Decimal(re.sub(r"\s+", "", match.group(1))) if match else Decimal("0")
    return rate.quantize(Decimal("0.01")), included


def split_supplier_code(value: Any, description: Any = None) -> tuple[str, str | None]:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    if not lines:
        return "", str(description).strip() or None if description else None
    if len(lines) > 1 and lines[0].endswith("-"):
        code = lines[0] + re.sub(r"\s+", "", lines[1])
        lines = [code, *lines[2:]]
    extra = "\n".join(lines[1:])
    base = str(description).strip() if description else ""
    merged = "\n".join(item for item in (extra, base) if item)
    return lines[0], merged or None


def deviation_percent(old: Decimal | None, new: Decimal | None) -> Decimal:
    old = Decimal(str(old)) if old is not None else None
    new = Decimal(str(new)) if new is not None else None
    if old is None or old == 0 or new is None:
        return Decimal("0.00") if old == new else Decimal("100.00")
    return ((new - old).copy_abs() / old.copy_abs() * Decimal(100)).quantize(Decimal("0.01"))


def classify_deviation(value: Decimal, warning: Decimal, blocked: Decimal) -> str:
    if value >= blocked:
        return "BLOCKED"
    if value >= warning:
        return "WARNING"
    return "OK"


def canonical_price_digest(rows: Iterable[dict[str, Any]]) -> str:
    def fixed(value: Any, places: int) -> str:
        return "NULL" if value is None else f"{Decimal(str(value)):.{places}f}"

    lines = [
        f"{row['part_id']}|{fixed(row['price_cash'], 4)}|{fixed(row['price_term'], 4)}|"
        f"{row['currency']}|{fixed(row['vat_rate'], 2)}"
        for row in sorted(rows, key=lambda item: int(item["part_id"]))
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
