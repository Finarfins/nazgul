from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

_IDENTIFIER_SEPARATORS = re.compile(r"[\s\-_.:/\\]+")
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_WHITESPACE = re.compile(r"\s+")

# Common OCR substitutions seen in photographed or scanned part labels.
# They are intentionally applied only when producing candidate variants;
# the user's original normalized value remains the first candidate.
_OCR_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "0": ("O", "Q"),
    "1": ("I", "L"),
    "2": ("Z",),
    "5": ("S",),
    "6": ("G",),
    "8": ("B",),
    "B": ("8",),
    "G": ("6",),
    "I": ("1", "L"),
    "L": ("1", "I"),
    "O": ("0", "Q"),
    "Q": ("0", "O"),
    "S": ("5",),
    "Z": ("2",),
}


class PartSearchKind(StrEnum):
    EMPTY = "empty"
    IDENTIFIER = "identifier"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class PartSearchQuery:
    raw: str
    kind: PartSearchKind
    normalized_text: str
    normalized_identifier: str | None
    identifier_candidates: tuple[str, ...]
    candidates_truncated: bool


def normalize_search_text(value: str | None) -> str:
    """Normalize human-readable search text without changing its meaning."""

    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).strip()
    return _WHITESPACE.sub(" ", normalized)


def normalize_part_identifier(value: str | None) -> str:
    """Return a stable, separator-free identifier suitable for comparison.

    The function deliberately keeps letters and digits only. It does not apply
    OCR substitutions because doing so would silently alter a valid OEM code.
    OCR alternatives are generated separately by ``identifier_candidates``.
    """

    text = normalize_search_text(value).upper()
    if not text:
        return ""
    text = _IDENTIFIER_SEPARATORS.sub("", text)
    return _NON_ALNUM.sub("", text)


def looks_like_part_identifier(value: str | None) -> bool:
    """Heuristically distinguish an OEM/barcode query from free text."""

    text = normalize_search_text(value)
    compact = normalize_part_identifier(text)
    if len(compact) < 3:
        return False

    # Codes normally contain a digit, or are compact uppercase tokens without
    # spaces. Free-text names such as "mazot filtresi" must stay text searches.
    contains_digit = any(char.isdigit() for char in compact)
    has_separator = " " not in text and bool(_IDENTIFIER_SEPARATORS.search(text))
    compact_token = " " not in text and len(compact) >= 5
    return contains_digit or has_separator or compact_token


def identifier_candidates(
    value: str | None,
    *,
    max_ocr_substitutions: int = 1,
    max_candidates: int = 24,
) -> tuple[str, ...]:
    """Build deterministic identifier alternatives for tolerant lookup.

    Candidate order is significant: the exact normalized value is always
    first, followed by conservative OCR alternatives. Combinatorial expansion
    is bounded to protect request latency and database query size.
    """

    normalized = normalize_part_identifier(value)
    if not normalized:
        return ()
    if max_ocr_substitutions < 0:
        raise ValueError("max_ocr_substitutions negatif olamaz")
    if max_candidates < 1:
        raise ValueError("max_candidates en az 1 olmalıdır")

    candidates: list[str] = [normalized]
    seen = {normalized}
    frontier: list[tuple[str, int]] = [(normalized, 0)]

    while frontier and len(candidates) < max_candidates:
        current, substitutions = frontier.pop(0)
        if substitutions >= max_ocr_substitutions:
            continue
        for index, char in enumerate(current):
            for replacement in _OCR_EQUIVALENTS.get(char, ()):
                candidate = f"{current[:index]}{replacement}{current[index + 1:]}"
                if candidate in seen:
                    continue
                seen.add(candidate)
                candidates.append(candidate)
                frontier.append((candidate, substitutions + 1))
                if len(candidates) >= max_candidates:
                    break
            if len(candidates) >= max_candidates:
                break

    return tuple(candidates)


def parse_part_search_query(value: str | None) -> PartSearchQuery:
    raw = value or ""
    normalized_text = normalize_search_text(raw)
    if not normalized_text:
        return PartSearchQuery(
            raw=raw,
            kind=PartSearchKind.EMPTY,
            normalized_text="",
            normalized_identifier=None,
            identifier_candidates=(),
            candidates_truncated=False,
        )

    if looks_like_part_identifier(normalized_text):
        normalized_identifier = normalize_part_identifier(normalized_text)
        candidates = identifier_candidates(normalized_identifier, max_candidates=25)
        return PartSearchQuery(
            raw=raw,
            kind=PartSearchKind.IDENTIFIER,
            normalized_text=normalized_text,
            normalized_identifier=normalized_identifier,
            identifier_candidates=candidates[:24],
            candidates_truncated=len(candidates) > 24,
        )

    return PartSearchQuery(
        raw=raw,
        kind=PartSearchKind.TEXT,
        normalized_text=normalized_text,
        normalized_identifier=None,
        identifier_candidates=(),
        candidates_truncated=False,
    )
