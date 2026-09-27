"""H111: İş emri faturalama önizleme ve POST yollarında iskonto türü harf duyarlılığı tutarlılığı.

MEASURED ON DEVELOP:
- GET /api/work-orders/{id}/invoice?global_discount_type=percent -> 422 (Literal["PERCENT", "FIXED"] küçük harfi reddeder)
- POST /api/invoices/generate with global_discount_type="percent" -> 201 (str kabul eder, motor üst harfe çevirir)
- POST /api/invoices/generate with global_discount_type="bogus" -> 500 (ValueError uncaught)
- GET /api/work-orders/{id}/invoice?global_discount_type=bogus -> 422

H111 ile:
- Her iki yol da sınırda (boundary) büyük harfe normalize eder (PERCENT, FIXED).
- Her iki yol da geçersiz türde (bogus) aynı 422 hatasını verir.
"""
from __future__ import annotations

import os
import tempfile
from decimal import Decimal

import pytest

_CALISMA_ALANI = tempfile.mkdtemp(prefix="h111-iskonto-case-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "h111.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI
os.environ["AUTO_MIGRATE"] = "true"

from fastapi.testclient import TestClient
from app.main import app
from tests.f9_5_servis_kdv_senaryo import dunya, oturum, tamamlanmis_is_emri


@pytest.fixture(scope="module")
def istemci():
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def ortam(istemci):
    h = oturum(istemci, "H111GuvenliParola!2026")
    w = dunya(istemci, h, "h111")
    return w


def test_preview_accepts_lowercase_percent_and_matches_uppercase(istemci, ortam):
    wo_id = tamamlanmis_is_emri(istemci, ortam)
    
    r_lower = istemci.get(
        f"/api/work-orders/{wo_id}/invoice",
        headers=ortam["h"],
        params={"global_discount_type": "percent", "global_discount_value": "10"},
    )
    r_upper = istemci.get(
        f"/api/work-orders/{wo_id}/invoice",
        headers=ortam["h"],
        params={"global_discount_type": "PERCENT", "global_discount_value": "10"},
    )
    
    assert r_lower.status_code == 200, f"Preview percent should return 200, got {r_lower.status_code}: {r_lower.text}"
    assert r_upper.status_code == 200, f"Preview PERCENT should return 200, got {r_upper.status_code}: {r_upper.text}"
    assert r_lower.json() == r_upper.json()


def test_post_accepts_lowercase_percent_and_matches_uppercase(istemci, ortam):
    wo_1 = tamamlanmis_is_emri(istemci, ortam)
    wo_2 = tamamlanmis_is_emri(istemci, ortam)
    
    r_lower = istemci.post(
        "/api/invoices/generate",
        headers=ortam["h"],
        json={"work_order_id": wo_1, "global_discount_type": "percent", "global_discount_value": "10"},
    )
    r_upper = istemci.post(
        "/api/invoices/generate",
        headers=ortam["h"],
        json={"work_order_id": wo_2, "global_discount_type": "PERCENT", "global_discount_value": "10"},
    )
    
    assert r_lower.status_code == 201, f"POST percent should return 201, got {r_lower.status_code}: {r_lower.text}"
    assert r_upper.status_code == 201, f"POST PERCENT should return 201, got {r_upper.status_code}: {r_upper.text}"
    
    inv_lower = r_lower.json()
    inv_upper = r_upper.json()
    
    assert inv_lower["totals"] == inv_upper["totals"]
    assert inv_lower["tax"] == inv_upper["tax"]


def test_preview_accepts_lowercase_fixed_and_matches_uppercase(istemci, ortam):
    wo_id = tamamlanmis_is_emri(istemci, ortam)
    
    r_lower = istemci.get(
        f"/api/work-orders/{wo_id}/invoice",
        headers=ortam["h"],
        params={"global_discount_type": "fixed", "global_discount_value": "50"},
    )
    r_upper = istemci.get(
        f"/api/work-orders/{wo_id}/invoice",
        headers=ortam["h"],
        params={"global_discount_type": "FIXED", "global_discount_value": "50"},
    )
    
    assert r_lower.status_code == 200, f"Preview fixed should return 200, got {r_lower.status_code}: {r_lower.text}"
    assert r_upper.status_code == 200, f"Preview FIXED should return 200, got {r_upper.status_code}: {r_upper.text}"
    assert r_lower.json() == r_upper.json()


def test_post_accepts_lowercase_fixed_and_matches_uppercase(istemci, ortam):
    wo_1 = tamamlanmis_is_emri(istemci, ortam)
    wo_2 = tamamlanmis_is_emri(istemci, ortam)
    
    r_lower = istemci.post(
        "/api/invoices/generate",
        headers=ortam["h"],
        json={"work_order_id": wo_1, "global_discount_type": "fixed", "global_discount_value": "50"},
    )
    r_upper = istemci.post(
        "/api/invoices/generate",
        headers=ortam["h"],
        json={"work_order_id": wo_2, "global_discount_type": "FIXED", "global_discount_value": "50"},
    )
    
    assert r_lower.status_code == 201, f"POST fixed should return 201, got {r_lower.status_code}: {r_lower.text}"
    assert r_upper.status_code == 201, f"POST FIXED should return 201, got {r_upper.status_code}: {r_upper.text}"
    
    inv_lower = r_lower.json()
    inv_upper = r_upper.json()
    
    assert inv_lower["totals"] == inv_upper["totals"]
    assert inv_lower["tax"] == inv_upper["tax"]


def test_preview_and_post_with_bogus_return_identical_422_detail(istemci, ortam):
    wo_prev = tamamlanmis_is_emri(istemci, ortam)
    wo_post = tamamlanmis_is_emri(istemci, ortam)
    
    r_prev = istemci.get(
        f"/api/work-orders/{wo_prev}/invoice",
        headers=ortam["h"],
        params={"global_discount_type": "bogus", "global_discount_value": "10"},
    )
    r_post = istemci.post(
        "/api/invoices/generate",
        headers=ortam["h"],
        json={"work_order_id": wo_post, "global_discount_type": "bogus", "global_discount_value": "10"},
    )
    
    assert r_prev.status_code == 422, f"Preview bogus should return 422, got {r_prev.status_code}: {r_prev.text}"
    assert r_post.status_code == 422, f"POST bogus should return 422, got {r_post.status_code}: {r_post.text}"
    
    prev_detail = r_prev.json()["detail"]
    post_detail = r_post.json()["detail"]
    
    assert len(prev_detail) == 1
    assert len(post_detail) == 1
    
    p_err = prev_detail[0]
    b_err = post_detail[0]
    
    assert p_err["type"] == b_err["type"] == "literal_error"
    assert p_err["msg"] == b_err["msg"]
    assert p_err["input"] == b_err["input"] == "BOGUS"
    assert p_err["ctx"] == b_err["ctx"]
    assert p_err["loc"][-1] == b_err["loc"][-1] == "global_discount_type"
    assert p_err["loc"] == ["query", "global_discount_type"]
    assert b_err["loc"] == ["body", "global_discount_type"]
