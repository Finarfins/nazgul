"""PostgreSQL ikizi: H57 + H58 — arama Türkçe katlaması ve LIKE kaçışı.

KUSUR (#139 merceği ölçtü, `bf8e73f` üzerinde):

* **H57** — PG'de ``/api/search`` ``LIKE``ı katlamasız kullanıyordu (büyük/
  küçük harfe duyarlı): "yılmaz" "Yılmaz Tarım"ı bulmuyordu. Diğer dört uç
  ``LOWER()`` kullanıyordu; PG ``LOWER('I')`` ``'i'`` verir, yani "YILMAZ"
  -> "yilmaz" "yılmaz"la eşleşmiyordu.
* **H58** — ``f'%{q}%'``: ``%`` firmanın BÜTÜN satırlarını döndürüyordu.
  PG'de ``LIKE``ın VARSAYILAN kaçış karakteri ``\\``dir, yani ters bölü
  SQLite'tan FARKLI davranır — ``A\\B`` kalıbı ``AB``yi de eşliyordu. Bu
  yüzden ters bölü iddiasının ASIL kanıtı bu dosyadır.

Ölçülen:
1. ``katli_sql`` PG'de ``tr_katla`` ile harfi harfine aynı sonucu verir.
2. Beş uçta dört "Yılmaz" yazımı da bulur; ``%`` 0 satır; ``_`` ve ``\\``
   harfi harfine; B firmasının aynı sahipli carisi görünmez.
3. Maskeli rol (``depo``) aynı katlamayla arar.

TEMİZLİK: KENDİ firmalarını önekle bulur ve yalnız onların satırlarını siler;
tablo SÜPÜRMEZ. API girişi ``activity_logs``a iz bırakır ve o tablo yalnız-
eklemedir: firma ya da kullanıcı silinemezse pasife alınır (H51 ikizinin
gerekçesi).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
ONEK = f"H57-{KOSU}"
PAROLA = "H57Arama!2026xyz"

YILMAZ = "Yılmaz Tarım"
ADLAR = (YILMAZ, "Kaya Ltd", "OEM_123 Parça", "OEMX123 Parça", "A\\B Gıda", "AB Gıda")
B_ADI = "Yılmaz B Firması"
YILMAZ_YAZIMLARI = ("yılmaz", "YILMAZ", "Yilmaz", "yilmaz", "YİLMAZ")
UCLAR = ("/api/customers", "/api/suppliers", "/api/payments", "/api/finance/transactions", "/api/search")
CARI_UCLARI = ("/api/customers", "/api/suppliers", "/api/search")

#: Firma satırlarını silme SIRASI (çocuk -> ebeveyn). Bir tablo yoksa atlanır.
_SILME_SIRASI = (
    "payment_allocations", "finance_transactions", "payments", "finance_accounts",
    "entity_change_logs", "customers", "suppliers", "user_company_memberships",
)


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("H57/H58 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _acilisi_kostur() -> None:
    """Açılış verisi firmalar yazılmadan ÖNCE (CS1 ikizinin gerekçesi)."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app):
        pass


def _temizle(engine) -> None:
    with engine.begin() as c:
        firmalar = [r[0] for r in c.execute(
            text("SELECT id FROM companies WHERE name LIKE :o"), {"o": ONEK + "%"})]
        kullanicilar = [r[0] for r in c.execute(
            text("SELECT id FROM app_users WHERE username LIKE :o"), {"o": ONEK.lower() + "%"})]
    for cid in firmalar:
        for tablo in _SILME_SIRASI:
            try:
                with engine.begin() as c:
                    c.execute(text(f"DELETE FROM {tablo} WHERE company_id=:c"), {"c": cid})
            except (IntegrityError, ProgrammingError):
                pass
    for uid in kullanicilar:
        try:
            with engine.begin() as c:
                c.execute(text("DELETE FROM user_company_memberships WHERE user_id=:u"), {"u": uid})
                c.execute(text("DELETE FROM app_users WHERE id=:u"), {"u": uid})
        except IntegrityError:
            with engine.begin() as c:
                c.execute(text("UPDATE app_users SET is_active=false WHERE id=:u"), {"u": uid})
    for cid in firmalar:
        try:
            with engine.begin() as c:
                c.execute(text("DELETE FROM companies WHERE id=:c"), {"c": cid})
        except IntegrityError:
            with engine.begin() as c:
                c.execute(text("UPDATE companies SET is_active=false WHERE id=:c"), {"c": cid})


@pytest.fixture(scope="module")
def motor():
    url = _url()
    command.upgrade(_config(url), "head")
    _acilisi_kostur()
    engine = create_engine(url)
    _temizle(engine)
    try:
        yield engine
    finally:
        _temizle(engine)
        engine.dispose()


def _kullanici(c, cid: int, ek: str, rol: str) -> str:
    from app.auth import hash_password

    an = datetime.now(timezone.utc)
    ad = f"{ONEK.lower()}-{ek}"
    uid = c.execute(text(
        "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,role,"
        "is_active,must_change_password,created_at) VALUES(:k,:e,true,'H57',:p,:r,true,false,:t)"
        " RETURNING id"),
        {"k": ad, "e": f"{ad}@ornek.test", "p": hash_password(PAROLA), "r": rol, "t": an}).scalar_one()
    c.execute(text("INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
                   "VALUES(:u,:c,true,:t)"), {"u": uid, "c": cid, "t": an})
    return ad


@pytest.fixture(scope="module")
def firmalar(motor):
    an = datetime.now(timezone.utc)
    with motor.begin() as c:
        a = c.execute(text("INSERT INTO companies(name,is_active,created_at) VALUES(:n,true,:t) RETURNING id"),
                      {"n": f"{ONEK}-A", "t": an}).scalar_one()
        b = c.execute(text("INSERT INTO companies(name,is_active,created_at) VALUES(:n,true,:t) RETURNING id"),
                      {"n": f"{ONEK}-B", "t": an}).scalar_one()
        yonetici = _kullanici(c, a, "admin", "admin")
        depo = _kullanici(c, a, "depo", "depo")
        for tablo in ("customers", "suppliers"):
            c.execute(text(
                f"INSERT INTO {tablo}(company_id,name,owner_name,opening_balance,is_active)"
                " VALUES(:c,:n,'Mehmet Yılmaz',0,true)"), {"c": b, "n": B_ADI})
    return {"a": a, "b": b, "admin": yonetici, "depo": depo}


@pytest.fixture(scope="module")
def istemci(firmalar):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _basliklar(istemci, kullanici: str, cid: int) -> dict:
    giris = istemci.post("/api/auth/login", json={"username": kullanici, "password": PAROLA})
    assert giris.status_code == 200, giris.text
    istemci.cookies.clear()
    return {"Authorization": "Bearer " + giris.json()["access_token"], "X-Company-ID": str(cid)}


@pytest.fixture(scope="module")
def admin(istemci, firmalar):
    h = _basliklar(istemci, firmalar["admin"], firmalar["a"])
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
    return h


@pytest.fixture(scope="module")
def depo(istemci, firmalar, admin):
    return _basliklar(istemci, firmalar["depo"], firmalar["a"])


def _adlar(istemci, basliklar, uc: str, q: str) -> set[str]:
    yanit = istemci.get(uc, headers=basliklar, params={"q": q})
    assert yanit.status_code == 200, (uc, q, yanit.text)
    govde = yanit.json()
    if uc == "/api/search":
        return {x["title"] for x in govde["items"] if x["type"] == "customer"}
    alan = {"/api/payments": "entity_name", "/api/finance/transactions": "description"}.get(uc, "name")
    return {x[alan] for x in govde}


@pytest.mark.parametrize("deger", [
    YILMAZ, "Mehmet Yılmaz", "İSMAİL IŞIK", "şğüöç ŞĞÜÖÇ", "istanbul", "A\\B Gıda", "OEM_123",
])
def test_katli_sql_PGde_tr_katla_ile_AYNI(motor, deger) -> None:
    from app.arama import katli_sql, tr_katla

    with motor.connect() as c:
        sonuc = c.execute(text(f"SELECT {katli_sql('CAST(:v AS TEXT)')}"), {"v": deger}).scalar_one()
    assert sonuc == tr_katla(deger)


@pytest.mark.parametrize("uc", UCLAR)
def test_tohum_her_ucta_gorunur(istemci, admin, uc) -> None:
    for ad in ADLAR:
        assert ad in _adlar(istemci, admin, uc, ad), (uc, ad)


@pytest.mark.parametrize("uc", UCLAR)
@pytest.mark.parametrize("q", YILMAZ_YAZIMLARI)
def test_H57_turkce_yazimlarin_hepsi_bulur(istemci, admin, uc, q) -> None:
    assert _adlar(istemci, admin, uc, q) == {YILMAZ}


@pytest.mark.parametrize("uc", UCLAR)
def test_H58_yuzde_joker_degil(istemci, admin, uc) -> None:
    assert _adlar(istemci, admin, uc, "%") == set()


@pytest.mark.parametrize("uc", UCLAR)
def test_H58_alt_cizgi_harfi_harfine(istemci, admin, uc) -> None:
    assert _adlar(istemci, admin, uc, "OEM_123") == {"OEM_123 Parça"}


@pytest.mark.parametrize("uc", UCLAR)
def test_H58_ters_bolu_harfi_harfine(istemci, admin, uc) -> None:
    """PG'de `\\` VARSAYILAN kaçış karakteridir: kaçırılmazsa `A\\B` `AB`yi eşler."""
    assert _adlar(istemci, admin, uc, "A\\B") == {"A\\B Gıda"}


@pytest.mark.parametrize("uc", CARI_UCLARI)
def test_B_firmasinin_ayni_sahipli_carisi_gorunmez(istemci, admin, uc) -> None:
    for q in ("yilmaz", "Mehmet", "firması"):
        assert B_ADI not in _adlar(istemci, admin, uc, q)


@pytest.mark.parametrize("uc", CARI_UCLARI)
@pytest.mark.parametrize("q", YILMAZ_YAZIMLARI)
def test_maskeli_rol_de_katlanmis_arar(istemci, depo, uc, q) -> None:
    assert _adlar(istemci, depo, uc, q) == {YILMAZ}
