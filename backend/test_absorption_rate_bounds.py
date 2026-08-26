"""Focused safety contract for the interactive absorption-rate endpoint."""

from __future__ import annotations

from fastapi import HTTPException
import pytest

from app.routers.absorption import MAX_PERIOD_DAYS, _parse_period


def test_absorption_period_accepts_exact_maximum() -> None:
    start, end = _parse_period("2024-01-01", "2024-12-31")

    assert (end - start).days + 1 == MAX_PERIOD_DAYS


def test_absorption_period_rejects_more_than_maximum() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _parse_period("2023-12-31", "2024-12-31")

    assert exc_info.value.status_code == 400
    assert str(MAX_PERIOD_DAYS) in str(exc_info.value.detail)
