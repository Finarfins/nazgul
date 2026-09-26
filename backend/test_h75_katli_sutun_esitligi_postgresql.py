"""PostgreSQL ikizi: H75 — kalıcı katlanmış arama sütunları (göç 20260925_0092).

SQLite ikizi ``tests/test_h75_katli_sutun_esitligi.py`` her yazıcıyı koşar;
bu dosya PG'ye ÖZGÜ olanı ölçer:

1. **Geri doldurma PG'de** — göç ``0091``e geri alınır, sütunlar GİDER;
   firmanın satırları ham SQL ile (aksanlı, küçük harfli, NULL'lı) yazılır;
   ``head``e çıkılınca her ``<kolon>_katli`` Python ``arama_katla`` ile
   harfi harfine aynıdır. PG ``translate`` YERLEŞİKTİR; SQLite'taki kullanıcı
   işlevinin anlamı burada gerçek PostgreSQL'e karşı ölçülür.
2. **Yazıcılar PG'de** — cari/tedarikçi/ürün oluştur+güncelle, satış, finans
   hareketi gerçek istekle; ardından firmanın bütün satırları eşit.
3. **Arama PG'de** — ``/api/customers`` ve ``/api/search`` aksanlı her yazımı
   ``_katli`` üzerinden bulur; ad değişince eski ad BULUNMAZ.

TEMİZLİK: KENDİ firmalarını önekle bulur ve yalnız onların satırlarını siler
(H57 ikiziyle aynı kalıp); tablo SÜPÜRMEZ.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

BACKEND = Path(__file__).resolve().parent
pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
ONEK = f"H75-{KOSU}"
PAROLA = "H75Katli!2026xyz"
GOC = "20260925_0092"
ONCEKI = "20260920_0091"

_SILME_SIRASI = (
    "payment_allocations", "finance_transactions", "payments", "finance_accounts",
    "stock_movements", "warehouse_stocks", "order_items", "orders", "products", "warehouses",
    "entity_change_logs", "customers", "suppliers", "user_company_memberships",
)


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("H75 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _acilisi_kostur() -> None:
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
        command.upgrade(_config(url), "head")
        _temizle(engine)
        engine.dispose()


def _bayat(engine, cid: int) -> tuple[list, dict]:
    from app.arama import arama_katla
    from app.arama_katli import KATLI_SUTUNLAR

    bayat, sayilar = [], {}
    with engine.connect() as c:
        for tablo, kolonlar in KATLI_SUTUNLAR.items():
            secim = ",".join(kolonlar) + "," + ",".join(f"{k}_katli" for k in kolonlar)
            satirlar = c.execute(text(f"SELECT id,{secim} FROM {tablo} WHERE company_id=:c"), {"c": cid}).all()
            sayilar[tablo] = len(satirlar)
            for s in satirlar:
                for i, kolon in enumerate(kolonlar):
                    if s[1 + len(kolonlar) + i] != arama_katla(s[1 + i] or ""):
                        bayat.append((tablo, s[0], kolon, s[1 + i], s[1 + len(kolonlar) + i]))
    return bayat, sayilar


AKSANLI = ("Kâzım Hâlâ Işık", "café émile", "İSMAİL ŞÜKRÜ", "straße ltd", "a\\b_%gıda")


def test_geri_doldurma_PGde_arama_katla_ile_AYNI(motor) -> None:
    url = _url()
    command.downgrade(_config(url), ONCEKI)
    try:
        sutunlar = {c["name"] for c in inspect(motor).get_columns("customers")}
        assert "name_katli" not in sutunlar
        an = datetime.now(timezone.utc)
        with motor.begin() as c:
            cid = c.execute(text("INSERT INTO companies(name,is_active,created_at) VALUES(:n,true,:t) RETURNING id"),
                            {"n": f"{ONEK}-GOC", "t": an}).scalar_one()
            for i, ad in enumerate(AKSANLI):
                c.execute(text("INSERT INTO customers(company_id,name,owner_name,email,opening_balance,is_active)"
                               " VALUES(:c,:n,:o,:e,0,true)"),
                          {"c": cid, "n": ad, "o": None if i % 2 else ad.upper(), "e": None if i % 3 else f"{ad}@x"})
                c.execute(text("INSERT INTO suppliers(company_id,name,owner_name,opening_balance,is_active)"
                               " VALUES(:c,:n,:o,0,true)"), {"c": cid, "n": ad, "o": ad.lower()})
                c.execute(text("INSERT INTO products(company_id,name,product_code,barcode,unit,sale_price,active)"
                               " VALUES(:c,:n,:k,:b,'Adet',1,true)"),
                          {"c": cid, "n": ad, "k": None if i % 2 else ad[:5], "b": f"brk-{ad[:3]}"})
    finally:
        command.upgrade(_config(url), "head")
    with motor.connect() as c:
        surum = c.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert surum == GOC
    bayat, sayilar = _bayat(motor, cid)
    assert bayat == []
    assert sayilar["customers"] == sayilar["suppliers"] == sayilar["products"] == len(AKSANLI)


def _kullanici(c, cid: int) -> str:
    from app.auth import hash_password

    an = datetime.now(timezone.utc)
    ad = f"{ONEK.lower()}-admin"
    uid = c.execute(text(
        "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,role,"
        "is_active,must_change_password,created_at) VALUES(:k,:e,true,'H75',:p,'admin',true,false,:t)"
        " RETURNING id"), {"k": ad, "e": f"{ad}@ornek.test", "p": hash_password(PAROLA), "t": an}).scalar_one()
    c.execute(text("INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at)"
                   " VALUES(:u,:c,true,:t)"), {"u": uid, "c": cid, "t": an})
    return ad


@pytest.fixture(scope="module")
def istek(motor):
    from fastapi.testclient import TestClient

    from app.main import app

    an = datetime.now(timezone.utc)
    with motor.begin() as c:
        cid = c.execute(text("INSERT INTO companies(name,is_active,created_at) VALUES(:n,true,:t) RETURNING id"),
                        {"n": f"{ONEK}-API", "t": an}).scalar_one()
        kullanici = _kullanici(c, cid)
        # Ham kurulan firmanın deposu yoktur; ürün oluşturma aktif depo ister.
        c.execute(text("INSERT INTO warehouses(company_id,name,code,is_active,is_default)"
                       " VALUES(:c,'H75 Depo','H75',true,true)"), {"c": cid})
    with TestClient(app, raise_server_exceptions=False) as client:
        giris = client.post("/api/auth/login", json={"username": kullanici, "password": PAROLA})
        assert giris.status_code == 200, giris.text
        client.cookies.clear()
        yield client, {"Authorization": "Bearer " + giris.json()["access_token"], "X-Company-ID": str(cid)}, cid


def _ok(yanit):
    assert yanit.status_code in (200, 201), (yanit.request.url, yanit.text)
    return yanit.json()


def _cari(ad: str, **ek) -> dict:
    return {"name": ad, "opening_balance": "0", "risk_limit": "0", "payment_term_days": 0,
            "is_active": True, **ek}


def test_yazicilar_PGde_esitler_ve_arama_katliyi_okur(motor, istek) -> None:
    client, h, cid = istek
    m = _ok(client.post("/api/customers", headers=h, json=_cari("Kâzım Çiftlik", email="kazim@ÖRNEK.test")))["id"]
    t = _ok(client.post("/api/suppliers", headers=h, json=_cari("Café Gübre", owner_name="émile")))["id"]
    _ok(client.put(f"/api/suppliers/{t}", headers=h, json=_cari("Café Gübre A.Ş.", owner_name="Émile Işık")))
    u = _ok(client.post("/api/products", headers=h, json={
        "name": "Tohum ılık", "product_code": "th-ı", "barcode": "brk-ş1", "purchase_price": "1",
        "sale_price": "2", "vat_rate": 20, "unit": "Adet"}))["id"]
    _ok(client.post("/api/orders", headers=h, json={
        "entity_id": m, "transaction_date": "2026-09-21", "document_no": "satış-ğ1", "status": "draft",
        "items": [{"product_id": u, "quantity": "1", "unit_price": "2", "vat_rate": 20}]}))
    hesap = _ok(client.post("/api/finance/accounts", headers=h, json={"name": "H75 PG Kasa"}))["id"]
    _ok(client.post("/api/finance/transactions", headers=h, json={
        "account_id": hesap, "txn_date": "2026-09-22", "direction": "in", "amount": "1.00",
        "description": "ışıklı açıklama"}))
    bayat, sayilar = _bayat(motor, cid)
    assert bayat == []
    assert all(sayilar[t] > 0 for t in ("customers", "suppliers", "products", "orders", "finance_transactions")), sayilar

    def musteriler(q: str) -> set[str]:
        return {x["name"] for x in _ok(client.get("/api/customers", headers=h, params={"q": q}))}

    for q in ("kâzım", "KAZIM", "Kazım", "kazim", "çiftlik", "CIFTLIK"):
        assert musteriler(q) == {"Kâzım Çiftlik"}, q
    arama = _ok(client.get("/api/search", headers=h, params={"q": "BRK-S1"}))
    assert {x["title"] for x in arama["items"] if x["type"] == "product"} == {"Tohum ılık"}
    arama = _ok(client.get("/api/search", headers=h, params={"q": "satis-g1"}))
    assert [x["type"] for x in arama["items"]] == ["sale"]
    _ok(client.put(f"/api/customers/{m}", headers=h, json=_cari("Yeni Ünvan")))
    assert musteriler("kazim") == set()
    assert musteriler("YENİ ÜNVAN") == {"Yeni Ünvan"}
    assert _bayat(motor, cid)[0] == []


def _katli_anahtarlari(deger, yol: str = "$") -> list[str]:
    bulunan: list[str] = []
    if isinstance(deger, dict):
        for ad, alt in deger.items():
            if ad.endswith("_katli"):
                bulunan.append(f"{yol}.{ad}")
            bulunan += _katli_anahtarlari(alt, f"{yol}.{ad}")
    elif isinstance(deger, list):
        for alt in deger:
            bulunan += _katli_anahtarlari(alt, f"{yol}[]")
    return bulunan


def test_H96_ALTI_TABLONUN_yaniti_PGde_KATLI_TASIMAZ(motor, istek) -> None:
    """SQLite ikizinin (`tests/test_h75_...::test_H96_...`) PG karşılığı.

    Ölçüldü: düzeltmeden önce PG'de de AYNI beş uç sızdırıyordu (kart,
    ürün detayı, finans hareketleri, satış/alış `document`i). Veri bu
    testin KENDİ firmasında kurulur; tek başına koşunca da boş değildir.
    """
    client, h, _cid = istek
    m = _ok(client.post("/api/customers", headers=h, json=_cari("H96 Müşteri", email="h96@örnek.test")))["id"]
    t = _ok(client.post("/api/suppliers", headers=h, json=_cari("H96 Tedarikçi", owner_name="İlkay")))["id"]
    u = _ok(client.post("/api/products", headers=h, json={
        "name": "H96 Ürün", "product_code": "h96-ı", "barcode": "h96-ş", "purchase_price": "1",
        "sale_price": "2", "vat_rate": 20, "unit": "Adet"}))["id"]
    kalem = [{"product_id": u, "quantity": "1", "unit_price": "2", "vat_rate": 20}]
    siparis = _ok(client.post("/api/orders", headers=h, json={
        "entity_id": m, "transaction_date": "2026-09-23", "document_no": "h96-satış",
        "status": "draft", "items": kalem}))["id"]
    alis = _ok(client.post("/api/purchases", headers=h, json={
        "entity_id": t, "transaction_date": "2026-09-23", "document_no": "h96-alış", "items": kalem}))["id"]
    hesap = _ok(client.post("/api/finance/accounts", headers=h, json={"name": "H96 Kasa"}))["id"]
    _ok(client.post("/api/finance/transactions", headers=h, json={
        "account_id": hesap, "txn_date": "2026-09-23", "direction": "in", "amount": "1.00",
        "description": "H96 açıklama"}))

    sizan = {}
    for yol in (
        "/api/customers", f"/api/customers/{m}",
        "/api/suppliers", f"/api/suppliers/{t}",
        "/api/products", f"/api/products/{u}",
        "/api/orders", f"/api/orders/{siparis}",
        "/api/purchases", f"/api/purchases/{alis}",
        "/api/finance/transactions",
    ):
        govde = _ok(client.get(yol, headers=h))
        assert govde, yol
        if yollar := _katli_anahtarlari(govde):
            sizan[yol] = yollar
    assert sizan == {}
