"""Exchange-rate resolution and TCMB daily fetch for the Purchase Comparison
Engine.

All comparisons normalise foreign-currency supplier prices to TRY. The rate a
company uses for a currency is resolved in this order:

    1. company_exchange_rate_overrides(company_id, currency)   -- tenant override
    2. latest exchange_rates(currency) by rate_date            -- global TCMB
    3. TRY -> TRY is always exactly 1

The TCMB fetch is a plain service function (called by a scheduler and by
POST /api/exchange-rates/refresh). It uses only the standard library so an
on-premise / LAN deployment has no extra dependency; when the TCMB host is
unreachable (offline install) callers fall back to the manual override table.
Rates are objective public reference data, so exchange_rates is intentionally
global (not company-scoped); only the override is tenant data.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.request import urlopen
from xml.etree import ElementTree

from sqlalchemy import text
from sqlalchemy.orm import Session

from .money import decimal_value


TCMB_TODAY_URL = "https://www.tcmb.gov.tr/kurlar/today.xml"
SUPPORTED_CURRENCIES = ("TRY", "EUR", "USD")
ONE = Decimal("1")

# Canonical FX precision contract: a manual price is 4dp and an FX rate is 6dp
# (their storage columns are NUMERIC(18,4) and NUMERIC(18,6)); their product, the
# normalised TRY value, is carried at 4dp; the accounting ledger is 2dp. Every
# consumer of a normalised price (the API response, the price-history snapshot,
# and the best-offer ranking) goes through ``to_try`` and therefore sees the one
# identical rounded value — ranking never disagrees with what is displayed.
NORMALIZED_TRY_QUANTUM = Decimal("0.0001")


def resolve_rate_to_try(db: Session, cid: int, currency: str) -> Decimal | None:
    """1 unit of ``currency`` in TRY for this company, or None if unknown.

    None means no override and no stored TCMB rate exist yet — the caller
    decides how to present an un-normalisable price (we sort it last).
    """
    code = (currency or "").strip().upper()
    if code == "TRY":
        return ONE
    override = db.execute(
        text(
            "SELECT rate_to_try FROM company_exchange_rate_overrides "
            "WHERE company_id=:cid AND currency=:c"
        ),
        {"cid": cid, "c": code},
    ).first()
    if override is not None:
        return decimal_value(override[0], field_name="Kur")
    latest = db.execute(
        text(
            "SELECT rate_to_try FROM exchange_rates WHERE currency=:c "
            "ORDER BY rate_date DESC, id DESC LIMIT 1"
        ),
        {"c": code},
    ).first()
    if latest is not None:
        return decimal_value(latest[0], field_name="Kur")
    return None


def resolve_rates(db: Session, cid: int) -> dict[str, Decimal | None]:
    """Effective rate for every supported currency for this company."""
    return {code: resolve_rate_to_try(db, cid, code) for code in SUPPORTED_CURRENCIES}


def to_try(price: Decimal, rate: Decimal | None) -> Decimal | None:
    """Normalise a unit price to TRY given a resolved rate (None -> None).

    The result is quantized to 4dp (ROUND_HALF_UP) per the canonical FX contract
    (see :data:`NORMALIZED_TRY_QUANTUM`). This is the single rounding point, so
    the API response, the history snapshot, and the best-offer ranking always use
    the identical value — a tie stays a tie and ranking never disagrees with the
    displayed price.
    """
    if rate is None:
        return None
    normalized = decimal_value(price, field_name="Fiyat") * rate
    return normalized.quantize(NORMALIZED_TRY_QUANTUM, rounding=ROUND_HALF_UP)


def _parse_tcmb(xml_bytes: bytes) -> dict[str, Decimal]:
    """Extract ForexSelling (fallback ForexBuying) TRY rates from TCMB XML.

    ForexSelling is the cost to acquire the currency, which matches paying a
    foreign supplier, so it is the right normalisation basis for buying.
    """
    root = ElementTree.fromstring(xml_bytes)
    rates: dict[str, Decimal] = {}
    for node in root.findall("Currency"):
        code = (node.get("CurrencyCode") or "").strip().upper()
        if code not in SUPPORTED_CURRENCIES or code == "TRY":
            continue
        raw = (node.findtext("ForexSelling") or node.findtext("ForexBuying") or "").strip()
        if not raw:
            continue
        try:
            value = decimal_value(raw.replace(",", "."), field_name="TCMB kuru")
        except ValueError:
            continue
        if value > 0:
            rates[code] = value
    return rates


def fetch_tcmb_rates(timeout: int = 10) -> dict[str, Decimal]:
    """Fetch today's TCMB rates. Raises on network/parse failure so the caller
    (refresh endpoint / scheduler) can report it; offline installs simply skip
    this and rely on manual overrides."""
    with urlopen(TCMB_TODAY_URL, timeout=timeout) as response:  # noqa: S310 (fixed trusted host)
        payload = response.read()
    return _parse_tcmb(payload)


def store_rates(
    db: Session, rates: dict[str, Decimal], rate_date: date, source: str = "TCMB"
) -> int:
    """Upsert one row per currency for ``rate_date``. Returns rows written.

    Idempotent on (currency, rate_date, source): re-running the same day updates
    the stored value instead of duplicating it. Does not commit — the caller owns
    the transaction.
    """
    written = 0
    for code, value in rates.items():
        existing = db.execute(
            text(
                "SELECT id FROM exchange_rates "
                "WHERE currency=:c AND rate_date=:d AND source=:s"
            ),
            {"c": code, "d": rate_date, "s": source},
        ).first()
        if existing is not None:
            db.execute(
                text("UPDATE exchange_rates SET rate_to_try=:r WHERE id=:id"),
                {"r": value, "id": existing[0]},
            )
        else:
            db.execute(
                text(
                    "INSERT INTO exchange_rates(currency,rate_date,rate_to_try,source) "
                    "VALUES(:c,:d,:r,:s)"
                ),
                {"c": code, "d": rate_date, "r": value, "s": source},
            )
        written += 1
    return written


def serialize_rate(value: Decimal | None) -> Any:
    return None if value is None else str(value)
