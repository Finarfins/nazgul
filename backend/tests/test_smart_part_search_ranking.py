from __future__ import annotations

from app.routers.search import _identifier_match


def test_exact_oem_match_has_highest_priority() -> None:
    row = {
        "oem_number": "8451-2763",
        "product_code": "84512763",
        "barcode": "84512763",
        "alternative_oem": "84512763",
    }
    assert _identifier_match(row, ("84512763",)) == (2040, "oem_exact")


def test_ocr_match_is_penalized_by_candidate_rank() -> None:
    row = {
        "oem_number": "845I2763",
        "product_code": None,
        "barcode": None,
        "alternative_oem": None,
    }
    assert _identifier_match(row, ("84512763", "845I2763")) == (1039, "oem_ocr")


def test_barcode_and_alternative_oem_have_stable_priorities() -> None:
    candidates = ("8691234567890",)
    barcode = {
        "oem_number": None,
        "product_code": None,
        "barcode": "8691234567890",
        "alternative_oem": None,
    }
    alternative = {
        "oem_number": None,
        "product_code": None,
        "barcode": None,
        "alternative_oem": "8691234567890",
    }
    assert _identifier_match(barcode, candidates) == (2020, "barcode_exact")
    assert _identifier_match(alternative, candidates) == (2010, "alternative_oem_exact")


def test_exact_match_in_any_field_beats_ocr_match_in_higher_priority_field() -> None:
    candidates = ("ABCI23", "ABC123")
    product_code_exact = {
        "oem_number": None,
        "product_code": "ABCI23",
        "barcode": None,
        "alternative_oem": None,
    }
    barcode_exact = {
        "oem_number": None,
        "product_code": None,
        "barcode": "ABCI23",
        "alternative_oem": None,
    }
    oem_ocr = {
        "oem_number": "ABC123",
        "product_code": None,
        "barcode": None,
        "alternative_oem": None,
    }

    assert _identifier_match(product_code_exact, candidates)[0] > _identifier_match(
        oem_ocr, candidates
    )[0]
    assert _identifier_match(barcode_exact, candidates)[0] > _identifier_match(
        oem_ocr, candidates
    )[0]


def test_no_identifier_match_returns_none() -> None:
    row = {
        "oem_number": "111111",
        "product_code": "222222",
        "barcode": "333333",
        "alternative_oem": "444444",
    }
    assert _identifier_match(row, ("84512763",)) is None
