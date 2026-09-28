"""H122: Vade farkı tahakkuk uçları yalnız charge_type='late_fee' belgelerine dokunur.

Hygiene H122:
- POST /api/finance/late-fees/charges/{document_id}/reversal
  bounced_check belgesinde develop'ta 500 dönüyordu (TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType').
  H122 ile 404 "Tahakkuk belgesi bulunamadı" döner.
- GET /charges/{document_id} ve POST /charges/{document_id}/post
  bounced_check ve service_fee belgelerinde 404 döner.
- 404 sonrası aynı Idempotency-Key ile tekrar yapıldığında talep yanmaz (409 dönmez, yine 404).
- Gerçek bir late_fee belgesi 200 ile terslenir, ters kayıt satırı üretilir ve tekrarı (replay) 200 döner.
"""
from __future__ import annotations

import os
import tempfile
from uuid import uuid4
import pytest

_CALISMA_ALANI = tempfile.mkdtemp(prefix="h122-late-fee-type-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "h122.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI
os.environ["AUTO_MIGRATE"] = "true"

from fastapi.testclient import TestClient
from app.db import SessionLocal
from app.main import app
from tests.h122_senaryo import run_h122_scenario


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth_context(client):
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
    cid = login["companies"][0]["id"]
    uid = login["user"]["id"]
    h = {"Authorization": "Bearer " + login["access_token"], "X-Company-ID": str(cid)}
    changed = client.post(
        "/api/auth/change-password",
        headers=h,
        json={"current_password": "admin123", "new_password": "H122TestPass123!"},
    )
    h["Authorization"] = "Bearer " + changed.json()["access_token"]
    return {"c": client, "h": h, "cid": cid, "uid": uid}


def test_h122_late_fee_charge_type_sqlite(auth_context):
    c = auth_context["c"]
    h = auth_context["h"]
    cid = auth_context["cid"]
    uid = auth_context["uid"]
    kosu = uuid4().hex[:8]

    with SessionLocal() as db:
        res = run_h122_scenario(c, h, cid, uid, db, kosu)
        assert res["bounced_doc_id"] > 0
        assert res["service_doc_id"] > 0
        assert res["late_fee_doc_id"] > 0
        assert res["reversal_doc_id"] > 0
