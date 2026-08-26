from __future__ import annotations

import pytest

from app.part_search import (
    PartSearchKind,
    identifier_candidates,
    looks_like_part_identifier,
    normalize_part_identifier,
    normalize_search_text,
    parse_part_search_query,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  8451-2763  ", "84512763"),
        ("8451 2763", "84512763"),
        ("8451.2763", "84512763"),
        ("nh/8451_2763", "NH84512763"),
        ("ＮＨ－８４５１", "NH8451"),
        (None, ""),
    ],
)
def test_normalize_part_identifier(raw: str | None, expected: str) -> None:
    assert normalize_part_identifier(raw) == expected


def test_normalize_search_text_preserves_words_and_collapses_space() -> None:
    assert normalize_search_text("  Mazot   filtresi  ") == "Mazot filtresi"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("84512763", True),
        ("NH-8451", True),
        ("845I2763", True),
        ("ABCDEF", True),
        ("mazot filtresi", False),
        ("yağ", False),
        ("", False),
    ],
)
def test_identifier_detection(raw: str, expected: bool) -> None:
    assert looks_like_part_identifier(raw) is expected


def test_candidates_keep_exact_value_first_and_add_conservative_ocr_variants() -> None:
    candidates = identifier_candidates("845I2763")

    assert candidates[0] == "845I2763"
    assert "84512763" in candidates
    assert len(candidates) == len(set(candidates))
    assert len(candidates) <= 24


def test_candidate_expansion_is_bounded() -> None:
    candidates = identifier_candidates(
        "OILB50",
        max_ocr_substitutions=2,
        max_candidates=5,
    )

    assert len(candidates) == 5
    assert candidates[0] == "OILB50"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_ocr_substitutions": -1}, "negatif"),
        ({"max_candidates": 0}, "en az 1"),
    ],
)
def test_candidate_limits_are_validated(kwargs: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        identifier_candidates("84512763", **kwargs)


def test_parse_empty_query() -> None:
    query = parse_part_search_query("   ")

    assert query.kind is PartSearchKind.EMPTY
    assert query.normalized_identifier is None
    assert query.identifier_candidates == ()


def test_parse_identifier_query() -> None:
    query = parse_part_search_query(" 8451-2763 ")

    assert query.kind is PartSearchKind.IDENTIFIER
    assert query.normalized_text == "8451-2763"
    assert query.normalized_identifier == "84512763"
    assert query.identifier_candidates[0] == "84512763"
    assert query.candidates_truncated is False


def test_parse_reports_when_candidate_expansion_is_truncated() -> None:
    query = parse_part_search_query("000000000000")

    assert query.kind is PartSearchKind.IDENTIFIER
    assert len(query.identifier_candidates) == 24
    assert query.candidates_truncated is True


def test_parse_text_query() -> None:
    query = parse_part_search_query("  Mazot   filtresi ")

    assert query.kind is PartSearchKind.TEXT
    assert query.normalized_text == "Mazot filtresi"
    assert query.normalized_identifier is None
    assert query.identifier_candidates == ()
