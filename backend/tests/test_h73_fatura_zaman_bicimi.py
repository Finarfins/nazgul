"""H73 — fatura uçlarının `*_at` alanları TEK biçimde: UTC ISO-8601.

--- ÖLÇÜLEN SORUN (8a6e3e1, aynı fatura, aynı an) ---------------------------

  SQLite  `created_at`            '2026-09-24 21:22:20.054785+00:00'  (boşluk)
  PG      `created_at`            '2026-09-25T00:22:25.411476+03:00'  (oturum dilimi)
  PG      `einvoice_updated_at`   '2026-09-25 00:22:25.411476+03:00'  (`str()`, boşluk)

Üçü de aynı anı gösteriyordu ama istemci için üç ayrı sözleşmeydi. Artık
hepsi `2026-09-24T21:22:20.054785+00:00`; yardımcı `app/zaman.py`de ve #153'ün
irsaliye görünümüyle (H66) PAYLAŞILIYOR.

--- MUTASYON TABLOSU --------------------------------------------------------

  * `_detail`teki `zamanlari_iso(invoice)`u `dict(invoice)`a geri çevirmek
        -> `test_generate_detail_cancel_UTC_ISO` KIRMIZI (SQLite boşluklu)
  * `list_invoices`teki `zamanlari_iso(dict(row))`u `dict(row)`a çevirmek
        -> `test_liste_UTC_ISO` KIRMIZI
  * `history`deki `zamanlari_iso` sarmalını kaldırmak
        -> `test_gecmis_UTC_ISO` KIRMIZI
  * `_einvoice_view`de `utc_iso(value)`yu `str(value)`ya geri çevirmek
        -> `test_einvoice_status_UTC_ISO` KIRMIZI
  * `zaman.utc`deki `astimezone(timezone.utc)`yu düşürmek
        -> `test_utc_iso_oturum_dilimini_UTCye_ceker` KIRMIZI (+03:00 kalır)

İç içe anlık görüntüler (`work_order.completed_at` vb.) KAPSAM DIŞI: belge
kesildiği anda donmuş JSON'dur, sütun değil; `test_anlik_goruntuye_dokunulmaz`
bunu sabitler.
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

_CALISMA_ALANI = tempfile.mkdtemp(prefix="h73-fatura-zaman-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "h73.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI
os.environ["AUTO_MIGRATE"] = "true"

ACILIS_PAROLASI = "admin123"
ADMIN_PAROLASI = "H73Zaman!2026x"

#: Tek kabul edilen tel biçimi: `T` ayırıcı, isteğe bağlı mikro saniye,
#: offset TAM OLARAK `+00:00`.
UTC_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?\+00:00$")


def _zamanlar(govde: dict) -> dict:
    return {k: v for k, v in govde.items() if k.endswith("_at")}


def _hepsi_utc_iso(govde: dict, *, en_az: int = 1) -> None:
    dolu = {k: v for k, v in _zamanlar(govde).items() if v is not None}
    assert len(dolu) >= en_az, govde
    for ad, deger in dolu.items():
        assert isinstance(deger, str) and UTC_ISO.match(deger), (ad, deger)


@pytest.fixture(scope="module")
def istemci():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def basliklar(istemci):
    giris = istemci.post(
        "/api/auth/login", json={"username": "admin", "password": ACILIS_PAROLASI}
    )
    assert giris.status_code == 200, giris.text
    govde = giris.json()
    b = {
        "Authorization": "Bearer " + govde["access_token"],
        "X-Company-ID": str(govde["companies"][0]["id"]),
    }
    degis = istemci.post(
        "/api/auth/change-password",
        headers=b,
        json={"current_password": ACILIS_PAROLASI, "new_password": ADMIN_PAROLASI},
    )
    assert degis.status_code == 200, degis.text
    b["Authorization"] = "Bearer " + degis.json()["access_token"]
    return b


def _fatura(istemci, b, ad: str) -> dict:
    musteri = istemci.post("/api/customers", headers=b, json={"name": ad}).json()
    makine = istemci.post(
        "/api/machines",
        headers=b,
        json={"brand": "H73", "model": ad, "customer_id": musteri["id"]},
    ).json()
    is_emri = istemci.post(
        "/api/work-orders",
        headers=b,
        json={
            "customer_id": musteri["id"],
            "machine_id": makine["id"],
            "technician_id": 1,
            "title": ad,
            "description": ad,
            "priority": "NORMAL",
        },
    ).json()
    for durum in ("IN_PROGRESS", "COMPLETED"):
        r = istemci.patch(
            f"/api/work-orders/{is_emri['id']}/status", headers=b, json={"status": durum}
        )
        assert r.status_code == 200, r.text
    r = istemci.post(
        "/api/invoices/generate", headers=b, json={"work_order_id": is_emri["id"]}
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def fatura(istemci, basliklar):
    return _fatura(istemci, basliklar, "H73-A")


def test_generate_detail_cancel_UTC_ISO(istemci, basliklar):
    uretilen = _fatura(istemci, basliklar, "H73-IPTAL")
    # created_at, updated_at, einvoice_updated_at dolu.
    _hepsi_utc_iso(uretilen, en_az=3)
    detay = istemci.get(f"/api/invoices/{uretilen['id']}", headers=basliklar)
    assert detay.status_code == 200, detay.text
    _hepsi_utc_iso(detay.json(), en_az=3)
    assert _zamanlar(detay.json()) == _zamanlar(uretilen)
    iptal = istemci.post(
        f"/api/invoices/{uretilen['id']}/cancel",
        headers=basliklar,
        json={"reason": "H73 olcum"},
    )
    assert iptal.status_code == 200, iptal.text
    # cancelled_at da artık dolu: dört alan.
    _hepsi_utc_iso(iptal.json(), en_az=4)
    assert iptal.json()["cancelled_at"] == iptal.json()["updated_at"]


def test_liste_UTC_ISO(istemci, basliklar, fatura):
    r = istemci.get("/api/invoices", headers=basliklar)
    assert r.status_code == 200, r.text
    kalemler = r.json()["items"]
    assert kalemler
    for kalem in kalemler:
        _hepsi_utc_iso(kalem)
    bizim = next(k for k in kalemler if k["id"] == fatura["id"])
    assert bizim["created_at"] == fatura["created_at"]


def test_gecmis_UTC_ISO(istemci, basliklar, fatura):
    r = istemci.get(f"/api/invoices/{fatura['id']}/history", headers=basliklar)
    assert r.status_code == 200, r.text
    assert r.json()
    for satir in r.json():
        _hepsi_utc_iso(satir)


def test_einvoice_status_UTC_ISO(istemci, basliklar, fatura):
    r = istemci.get(f"/api/invoices/{fatura['id']}/einvoice/status", headers=basliklar)
    assert r.status_code == 200, r.text
    govde = r.json()
    assert set(_zamanlar(govde)) == {"einvoice_submitted_at", "einvoice_updated_at"}
    _hepsi_utc_iso(govde)
    assert govde["einvoice_updated_at"] == fatura["einvoice_updated_at"]


def test_anlik_goruntuye_dokunulmaz(fatura):
    """`work_order` anlık görüntüsü donmuş JSON: biçimi DEĞİŞMEMELİ."""
    goruntu = fatura["work_order"]
    assert goruntu.get("completed_at")
    # Görüntü kesildiği andaki `str()` biçimiyle kalır (boşluklu).
    assert "T" not in goruntu["completed_at"]


# --- yardımcının kendisi ---------------------------------------------------


def test_utc_iso_oturum_dilimini_UTCye_ceker():
    from app.zaman import utc_iso

    an = datetime(2026, 9, 24, 21, 22, 20, 54785, tzinfo=timezone.utc)
    beklenen = "2026-09-24T21:22:20.054785+00:00"
    for girdi in (
        an,                                                   # PG, UTC oturum
        an.astimezone(timezone(timedelta(hours=3))),          # PG, +03:00 oturum
        an.replace(tzinfo=None),                              # naive (SQLite)
        "2026-09-24 21:22:20.054785+00:00",                   # SQLite METİN
        "2026-09-24 21:22:20.054785",                         # offset'siz METİN
        "2026-09-25T00:22:20.054785+03:00",                   # ISO, +03:00
        "2026-09-24T21:22:20.054785Z",                        # Z soneki
    ):
        assert utc_iso(girdi) == beklenen, girdi
    assert utc_iso(None) is None


def test_zamanlari_iso_yalniz_ust_duzey_at_alanlari():
    from app.zaman import zamanlari_iso

    satir = {
        "id": 1,
        "created_at": "2026-09-24 21:22:20+00:00",
        "cancelled_at": None,
        "issue_date": "2026-09-24",
        "status_at_text": "dokunma",  # `_at` ile BİTMİYOR
        "work_order": {"completed_at": "2026-09-24 21:00:00+00:00"},
    }
    cikti = zamanlari_iso(satir)
    assert cikti["created_at"] == "2026-09-24T21:22:20+00:00"
    assert cikti["cancelled_at"] is None
    assert cikti["issue_date"] == "2026-09-24"
    assert cikti["status_at_text"] == "dokunma"
    assert cikti["work_order"] == {"completed_at": "2026-09-24 21:00:00+00:00"}
    assert satir["created_at"] == "2026-09-24 21:22:20+00:00"  # girdi değişmedi
