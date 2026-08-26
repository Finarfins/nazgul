from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers.transfer_details import _require_stock_permission


def _request(role: str | None):
    user = {"role": role} if role is not None else None
    return SimpleNamespace(state=SimpleNamespace(user=user))


@pytest.mark.parametrize("role", ["admin", "yonetici", "depo"])
def test_transfer_detail_accepts_stock_roles(role: str) -> None:
    _require_stock_permission(_request(role))


@pytest.mark.parametrize("role", [None, "", "rapor", "satis", "muhasebe"])
def test_transfer_detail_rejects_roles_without_stock_permission(role: str | None) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _require_stock_permission(_request(role))

    assert exc_info.value.status_code == 403
    assert "stok yetkiniz" in str(exc_info.value.detail)
