"""H57 + H58 — serbest metin aramasının Türkçe katlaması ve LIKE kaçışı.

KUSUR (#139 merceği ölçtü, `bf8e73f` üzerinde):

* **H57** — SQLite ``LOWER()`` yalnız ASCII katlar: sahibi "Mehmet Yılmaz"
  olan cari "YILMAZ" ya da "Yilmaz" aranınca 0 satır dönüyordu. PG'de
  ``/api/search`` hiç katlamıyordu (büyük/küçük harfe duyarlı ``LIKE``).
* **H58** — ``customers.py`` ile ``finance.py``nin üç ucu ``f'%{q}%'``
  kuruyordu: kullanıcının yazdığı ``%`` firmanın BÜTÜN satırlarını, ``_`` her
  tek karakteri eşliyordu.

ÇARE tek dikişte (`app/arama.py`): sorgu Python'da ``tr_katla`` ile, kolon
SQL'de ``katli_sql`` ile AYNI eşlemeden geçer; kalıp ``arama_deseni`` ile
kaçırılır ve her ``LIKE`` ``ESCAPE '\\'`` taşır.

Beş uç, aynı tohum, aynı iddialar. B firmasının carisi aynı sahibi taşır ki
kiracı sızıntısı görünür olsun. PG ikizi: ``test_h57_h58_arama_postgresql.py``.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

import pytest

_CALISMA_ALANI = tempfile.mkdtemp(prefix="h57-h58-arama-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "h57.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI
os.environ["AUTO_MIGRATE"] = "true"

from app.arama import arama_deseni, katli_sql, tr_katla  # noqa: E402

ACILIS_PAROLASI = "admin123"
ADMIN_PAROLASI = "H57Arama!2026xyz"
DEPO_PAROLASI = "H57Depo!2026xyz"

YILMAZ = "Yılmaz Tarım"
#: Tohum adları. Hiçbiri `%` taşımaz: `q=%` ancak joker gibi davranırsa satır döner.
ADLAR = (YILMAZ, "Kaya Ltd", "OEM_123 Parça", "OEMX123 Parça", "A\\B Gıda", "AB Gıda")
B_ADI = "Yılmaz B Firması"

YILMAZ_YAZIMLARI = ("yılmaz", "YILMAZ", "Yilmaz", "yilmaz", "YİLMAZ", "  yılMAZ  ")
UCLAR = ("/api/customers", "/api/suppliers", "/api/payments", "/api/finance/transactions", "/api/search")
CARI_UCLARI = ("/api/customers", "/api/suppliers", "/api/search")


# --------------------------------------------------------------------------
# Saf birim: kalıp ve katlama
# --------------------------------------------------------------------------

def test_arama_deseni_katlar_ve_kacirir() -> None:
    assert arama_deseni("  %_\\  ") == "%\\%\\_\\\\%"
    assert {arama_deseni(y) for y in YILMAZ_YAZIMLARI} == {"%YILMAZ%"}
    assert arama_deseni("") == "%%"


@pytest.mark.parametrize("deger", [
    YILMAZ, "Mehmet Yılmaz", "İSMAİL IŞIK", "şğüöç ŞĞÜÖÇ", "istanbul", "A\\B Gıda",
    "OEM_123", "ali@ornek.test",
])
def test_katli_sql_SQLitete_tr_katla_ile_AYNI(deger: str) -> None:
    """Kolon ifadesi Python katlamasıyla harfi harfine aynı sonucu vermeli; yoksa
    iki taraf farklı biçimlerde buluşur ve eşleşme şansa kalır."""
    baglanti = sqlite3.connect(":memory:")
    try:
        (sonuc,) = baglanti.execute(f"SELECT {katli_sql('?')}", (deger,)).fetchone()
    finally:
        baglanti.close()
    assert sonuc == tr_katla(deger)


# --------------------------------------------------------------------------
# Gerçek istekler
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def istemci():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def admin(istemci):
    giris = istemci.post("/api/auth/login", json={"username": "admin", "password": ACILIS_PAROLASI})
    assert giris.status_code == 200, giris.text
    govde = giris.json()
    basliklar = {
        "Authorization": "Bearer " + govde["access_token"],
        "X-Company-ID": str(govde["companies"][0]["id"]),
    }
    degis = istemci.post(
        "/api/auth/change-password", headers=basliklar,
        json={"current_password": ACILIS_PAROLASI, "new_password": ADMIN_PAROLASI},
    )
    assert degis.status_code == 200, degis.text
    basliklar["Authorization"] = "Bearer " + degis.json()["access_token"]
    return basliklar


@pytest.fixture(scope="module")
def tohum(istemci, admin):
    """A firmasına her uç için aynı adlar; B firmasına aynı sahipli bir cari."""
    from sqlalchemy import text

    from app.db import SessionLocal

    h = admin
    hesap = istemci.post("/api/finance/accounts", headers=h, json={"name": "H57 Kasa"})
    assert hesap.status_code == 201, hesap.text
    hesap_id = hesap.json()["id"]
    for ad in ADLAR:
        sahip = "Mehmet Yılmaz" if ad == YILMAZ else "Ali Kaya"
        m = istemci.post("/api/customers", headers=h, json={"name": ad, "owner_name": sahip})
        assert m.status_code == 201, m.text
        t = istemci.post("/api/suppliers", headers=h, json={"name": ad, "owner_name": sahip})
        assert t.status_code in (200, 201), t.text
        o = istemci.post("/api/payments", headers=h, json={
            "entity_type": "customer", "entity_id": m.json()["id"], "amount": "10.00",
            "payment_date": "2026-09-01", "account_id": hesap_id,
        })
        assert o.status_code == 201, o.text
        f = istemci.post("/api/finance/transactions", headers=h, json={
            "account_id": hesap_id, "txn_date": "2026-09-01", "direction": "in",
            "amount": "5.00", "description": ad,
        })
        assert f.status_code == 201, f.text

    simdi = datetime.now(timezone.utc)
    with SessionLocal() as db:
        b_cid = db.execute(text(
            "INSERT INTO companies(name,is_active,created_at) VALUES('H57-B',true,:t) RETURNING id"),
            {"t": simdi}).scalar_one()
        for tablo in ("customers", "suppliers"):
            db.execute(text(
                f"INSERT INTO {tablo}(company_id,name,owner_name,opening_balance,is_active)"
                " VALUES(:c,:n,'Mehmet Yılmaz',0,true)"), {"c": b_cid, "n": B_ADI})
        db.commit()
    return {"b_cid": b_cid}


@pytest.fixture(scope="module")
def depo(istemci, admin, tohum):
    """Maskeli rol: arama yalnız ad/yetkili adında (SEC-3b) — katlama orada da işlemeli."""
    from sqlalchemy import text

    from app.auth import hash_password
    from app.db import SessionLocal

    cid = int(admin["X-Company-ID"])
    simdi = datetime.now(timezone.utc)
    with SessionLocal() as db:
        uid = db.execute(text(
            "INSERT INTO app_users(username,email,display_name,password_hash,role,is_active,"
            "must_change_password,email_verified,created_at)"
            " VALUES('h57_depo','h57_depo@ornek.test','H57 depo',:p,'depo',true,false,true,:t)"
            " RETURNING id"), {"p": hash_password(DEPO_PAROLASI), "t": simdi}).scalar_one()
        db.execute(text(
            "INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at)"
            " VALUES(:u,:c,true,:t)"), {"u": uid, "c": cid, "t": simdi})
        db.commit()
    giris = istemci.post("/api/auth/login", json={"username": "h57_depo", "password": DEPO_PAROLASI})
    assert giris.status_code == 200, giris.text
    return {"Authorization": "Bearer " + giris.json()["access_token"], "X-Company-ID": str(cid)}


def _adlar(istemci, basliklar, uc: str, q: str) -> set[str]:
    yanit = istemci.get(uc, headers=basliklar, params={"q": q})
    assert yanit.status_code == 200, (uc, q, yanit.text)
    govde = yanit.json()
    if uc == "/api/search":
        return {x["title"] for x in govde["items"] if x["type"] == "customer"}
    alan = {"/api/payments": "entity_name", "/api/finance/transactions": "description"}.get(uc, "name")
    return {x[alan] for x in govde}


@pytest.mark.parametrize("uc", UCLAR)
def test_tohum_her_ucta_gorunur(istemci, admin, tohum, uc) -> None:
    """Boş sonuçlu iddiaların boşa geçmediğinin kanıtı: "Parça"/"Gıda"/"Ltd"/"Tarım"
    ortak bir harf taşımaz ama her biri kendi sorgusuyla gelir."""
    for ad in ADLAR:
        assert ad in _adlar(istemci, admin, uc, ad), (uc, ad)


@pytest.mark.parametrize("uc", UCLAR)
@pytest.mark.parametrize("q", YILMAZ_YAZIMLARI)
def test_H57_turkce_yazimlarin_hepsi_bulur(istemci, admin, tohum, uc, q) -> None:
    """Tabanda SQLite'ta "YILMAZ"/"Yilmaz"/"yilmaz" 0 satırdı."""
    assert _adlar(istemci, admin, uc, q) == {YILMAZ}


@pytest.mark.parametrize("uc", UCLAR)
def test_H58_yuzde_joker_degil(istemci, admin, tohum, uc) -> None:
    """Tabanda `q=%` firmanın BÜTÜN satırlarını döndürüyordu."""
    assert _adlar(istemci, admin, uc, "%") == set()
    assert _adlar(istemci, admin, uc, "%%%") == set()


@pytest.mark.parametrize("uc", UCLAR)
def test_H58_alt_cizgi_harfi_harfine(istemci, admin, tohum, uc) -> None:
    """`_` tek karakter jokeri değil: OEMX123 eşleşmez."""
    assert _adlar(istemci, admin, uc, "OEM_123") == {"OEM_123 Parça"}
    assert _adlar(istemci, admin, uc, "_") == {"OEM_123 Parça"}


@pytest.mark.parametrize("uc", UCLAR)
def test_H58_ters_bolu_harfi_harfine(istemci, admin, tohum, uc) -> None:
    """`\\` kaçış karakteri olarak yutulmaz: "AB Gıda" eşleşmez."""
    assert _adlar(istemci, admin, uc, "A\\B") == {"A\\B Gıda"}


@pytest.mark.parametrize("uc", CARI_UCLARI)
def test_B_firmasinin_ayni_sahipli_carisi_gorunmez(istemci, admin, tohum, uc) -> None:
    for q in ("yilmaz", "Mehmet", "firması", "B Firm"):
        assert B_ADI not in _adlar(istemci, admin, uc, q)


@pytest.mark.parametrize("uc", CARI_UCLARI)
@pytest.mark.parametrize("q", YILMAZ_YAZIMLARI)
def test_maskeli_rol_de_katlanmis_arar(istemci, depo, tohum, uc, q) -> None:
    """Maskeli dal (ad + yetkili adı) aynı katlamayı kullanır."""
    assert _adlar(istemci, depo, uc, q) == {YILMAZ}


@pytest.mark.parametrize("uc", ("/api/customers", "/api/suppliers"))
def test_yetkili_adi_ile_de_katlanmis_bulur(istemci, admin, depo, tohum, uc) -> None:
    for basliklar in (admin, depo):
        assert _adlar(istemci, basliklar, uc, "MEHMET YILMAZ") == {YILMAZ}
