from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.routers.warehouse_count_reports import _date, _parsed_quantities, _safe_cell


def test_variance_report_helpers_validate_and_escape() -> None:
    assert _date("20.07.2026", "Tarih") == "2026-07-20"
    assert _date("2026-07-20", "Tarih") == "2026-07-20"
    with pytest.raises(HTTPException):
        _date("20/07/2026", "Tarih")
    system, counted, note = _parsed_quantities(
        "Fiziksel sayım | Sistem: 10 | Sayılan: 7 | Not: Raf A", Decimal("-3")
    )
    assert system == Decimal("10.0000")
    assert counted == Decimal("7.0000")
    assert note == "Raf A"
    assert _safe_cell("=SUM(A1:A2)") == "'=SUM(A1:A2)"
    assert _safe_cell("  =SUM(A1:A2)") == "'  =SUM(A1:A2)"
    assert _safe_cell("\t+TEHLIKE") == "'\t+TEHLIKE"
    assert _safe_cell("Normal metin") == "Normal metin"
    assert _safe_cell(Decimal("-3")) == Decimal("-3")
