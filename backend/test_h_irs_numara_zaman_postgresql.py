"""PostgreSQL ikizi: H52 / H53 / H66 / H68 — irsaliye numarası, zaman biçimi, satır sayısı.

SQLite tarafı (`tests/test_e4b1_kismi_sevk.py` bölüm 3b) kuralları ölçer. Bu
dosya aynı kuralları GERÇEK PG 16'da, GERÇEK HTTP ile ölçer, çünkü dördü de
lehçeye bağlı bir yüzeyden geçiyor:

1. **H52 — numara tükenmesi 409.** Sayaç `document_sequences` üzerinde
   `UPDATE ... RETURNING`; istek düştüğünde ilerlemesi PG işlemiyle geri
   alınmalı (sayaç satırı KALMAZ).
2. **H53 — elle numara.** Tekrar ön okuması `UPPER(despatch_number)` ile
   karşılaştırır; şemada `(company_id, despatch_number)` UNIQUE'i YOK, hakem
   bu okumadır. Yıl uyuşmazlığı 422.
3. **H66 — ISO-8601.** PG `TIMESTAMPTZ`i `datetime` olarak döndürür; eski
   `str()` boşluklu bir dize basardı (`"2026-09-16 10:00:00+00:00"`).
   Yazım İstanbul oturum diliminde yapılır; `edespatch/status`, detay ve
   liste `/response` ile BİREBİR aynı UTC ISO dizesini vermeli.
4. **H68 — `lines_count`.** İlişkili alt sorgu PG'de doğru sayar ve kiracı
   kapsamlıdır (B firmasının satırları A'nın listesine girmez).

TEMİZLİK: KENDİ satırlarını önekle siler, tablo SÜPÜRMEZ. `activity_logs`
yalnız-eklemedir; girişin bıraktığı iz yüzünden kullanıcı/firma silinemezse
pasife alınır (H51 ikizinin deseni).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
ONEK = f"HIRS-{KOSU}"
PAROLA = "HIrsNumara!2026xyz"
SOFOR_TCKN = "11111111110"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("H-IRS ikizi APP_TEST_DATABASE_URL ister")
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
    """SIRA: satır -> irsaliye -> denetim -> kalem -> fatura -> sayaç -> üyelik ->
    kullanıcı -> firma."""
    with engine.begin() as c:
        firmalar = [r[0] for r in c.execute(
            text("SELECT id FROM companies WHERE name LIKE :o"), {"o": ONEK + "%"})]
        kullanicilar = [r[0] for r in c.execute(
            text("SELECT id FROM app_users WHERE username LIKE :o"), {"o": ONEK.lower() + "%"})]
    for cid in firmalar:
        with engine.begin() as c:
            for tablo in (
                "despatch_lines", "despatch_notes", "invoice_audit", "invoice_history",
                "invoice_items", "invoices", "document_sequences", "user_company_memberships",
            ):
                c.execute(text(f"DELETE FROM {tablo} WHERE company_id=:c"), {"c": cid})
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


_OZET: dict[str, str] = {}
_SAYAC = {"n": 0}


def _parola_ozeti() -> str:
    if "p" not in _OZET:
        from app.auth import hash_password

        _OZET["p"] = hash_password(PAROLA)
    return _OZET["p"]


def _firma(c, ek: str) -> dict:
    """Bir firma + üye bir admin kullanıcı."""
    an = datetime.now(timezone.utc)
    kullanici = f"{ONEK.lower()}-{ek.lower()}"
    cid = c.execute(text(
        "INSERT INTO companies(name,is_active,created_at) VALUES(:n,true,:t) RETURNING id"),
        {"n": f"{ONEK}-{ek}", "t": an}).scalar_one()
    uid = c.execute(text(
        "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,role,"
        "is_active,must_change_password,created_at) VALUES(:k,:e,true,'HIRS',:p,'admin',true,false,:t)"
        " RETURNING id"),
        {"k": kullanici, "e": f"{kullanici}@ornek.test", "p": _parola_ozeti(), "t": an}).scalar_one()
    c.execute(text("INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
                   "VALUES(:u,:c,true,:t)"), {"u": uid, "c": cid, "t": an})
    return {"cid": int(cid), "uid": int(uid), "kullanici": kullanici}


def _fatura(motor, firma: dict, adet: int) -> dict:
    """``adet`` PART kalemli bir fatura (her kalem 5 birim)."""
    _SAYAC["n"] += 1
    an = datetime.now(timezone.utc)
    with motor.begin() as c:
        fatura = c.execute(text(
            "INSERT INTO invoices(company_id,invoice_number,invoice_type,status,currency,exchange_rate,"
            "customer_snapshot,machine_snapshot,work_order_snapshot,company_snapshot,technician_snapshot,"
            "warranty_snapshot,totals_snapshot,tax_snapshot,created_by,created_at,updated_at)"
            " VALUES(:c,:n,'INVOICE','ISSUED','TRY',1,'{}','{}','{}','{}','{}','{}','{}','{}',:u,:t,:t)"
            " RETURNING id"),
            {"c": firma["cid"], "n": f"{ONEK}-{_SAYAC['n']}", "u": firma["uid"], "t": an}).scalar_one()
        kalemler = [
            int(c.execute(text(
                "INSERT INTO invoice_items(company_id,invoice_id,item_type,description,"
                "quantity,unit_price,original_price,discount_amount,tax_rate,tax_amount,"
                "total,warranty_percent,customer_payable,company_payable,source_snapshot) "
                "VALUES(:c,:f,'PART',:ad,5,1,1,0,0,0,1,0,1,0,:k) RETURNING id"),
                {"c": firma["cid"], "f": fatura, "ad": f"Mal {i}",
                 "k": json.dumps({"product_id": 70 + i})}).scalar_one())
            for i in range(adet)
        ]
    return {"id": int(fatura), "kalemler": kalemler}


@pytest.fixture(scope="module")
def iki_firma(motor):
    with motor.begin() as c:
        return _firma(c, "A"), _firma(c, "B")


@pytest.fixture(scope="module")
def istemciler(iki_firma):
    """İki firmanın kullanıcılarıyla GERÇEK giriş. 500'ler istisna değil yanıt
    olarak görünür — iddia durum koduna bakar."""
    from fastapi.testclient import TestClient

    from app.main import app

    istemciler = []
    with TestClient(app, raise_server_exceptions=False) as ia, \
            TestClient(app, raise_server_exceptions=False) as ib:
        for client, firma in zip((ia, ib), iki_firma):
            giris = client.post(
                "/api/auth/login", json={"username": firma["kullanici"], "password": PAROLA})
            assert giris.status_code == 200, giris.text
            client.cookies.clear()
            client.headers.update({
                "Authorization": "Bearer " + giris.json()["access_token"],
                "X-Company-ID": str(firma["cid"]),
            })
            istemciler.append(client)
        yield tuple(istemciler)


def _govde(fatura: dict, kalemler: list[int] | None = None, **ek) -> dict:
    govde = {
        "invoice_id": fatura["id"],
        "actual_shipment_at": "2026-09-15T08:30:00+00:00",
        "driver_name": "Ahmet Yilmaz",
        "driver_national_id": SOFOR_TCKN,
        "vehicle_plate": "34ABC123",
        "delivery_address": "Depo Yolu 7",
        "delivery_postal_code": "34710",
    }
    if kalemler is not None:
        govde["lines"] = [{"invoice_item_id": k, "quantity": "1"} for k in kalemler]
    govde.update(ek)
    return govde


def _kod(yanit) -> str | None:
    detay = yanit.json().get("detail")
    return detay.get("code") if isinstance(detay, dict) else None


def test_H52_NUMARA_TUKENDI_PGde_409_ve_sayac_geri_alinir(motor, iki_firma, istemciler) -> None:
    """Tabanda 500 (yakalanmayan `UblBuildError`). MUTASYON: `except
    UblBuildError` dalını kaldırmak bunu KIRMIZI yapar."""
    a, _ = iki_firma
    ia, _ = istemciler
    f = _fatura(motor, a, adet=1)
    k = f["kalemler"]
    son = ia.post("/api/despatch-notes",
                  json=_govde(f, k, issue_date="2041-01-02", despatch_number="IRS2041999999999"))
    assert son.status_code == 201, son.text
    tukendi = ia.post("/api/despatch-notes", json=_govde(f, k, issue_date="2041-01-03"))
    assert tukendi.status_code == 409, tukendi.text
    assert _kod(tukendi) == "IRSALIYE_NUMARA_TUKENDI"
    with motor.connect() as c:
        assert c.execute(text(
            "SELECT COUNT(*) FROM document_sequences WHERE company_id=:c "
            "AND sequence_key='despatch_notes:IRS2041'"), {"c": a["cid"]}).scalar_one() == 0
        assert c.execute(text(
            "SELECT COUNT(*) FROM despatch_notes WHERE company_id=:c AND invoice_id=:f"),
            {"c": a["cid"], "f": f["id"]}).scalar_one() == 1


def test_H53_ELLE_NUMARA_PGde_tekrar_409_yil_422(motor, iki_firma, istemciler) -> None:
    """Küçük harfli kopya ve başka faturadaki aynı numara 409; başka FİRMA
    serbest; yıl uyuşmazlığı 422. MUTASYON: tekrar ön okumasını silmek
    ikinci isteği 201 yapar."""
    a, b = iki_firma
    ia, ib = istemciler
    f, g = _fatura(motor, a, adet=1), _fatura(motor, a, adet=1)
    ilk = ia.post("/api/despatch-notes", json=_govde(
        f, f["kalemler"], issue_date="2042-02-01", despatch_number="IRS2042000000042"))
    assert ilk.status_code == 201, ilk.text
    for fatura, numara in ((f, "irs2042000000042"), (g, "IRS2042000000042")):
        tekrar = ia.post("/api/despatch-notes", json=_govde(
            fatura, fatura["kalemler"], issue_date="2042-02-02", despatch_number=numara))
        assert tekrar.status_code == 409, tekrar.text
        assert _kod(tekrar) == "IRSALIYE_NO_TEKRAR"
    yil = ia.post("/api/despatch-notes", json=_govde(
        g, g["kalemler"], issue_date="2043-01-01", despatch_number="IRS2042000000043"))
    assert yil.status_code == 422, yil.text
    assert _kod(yil) == "IRSALIYE_NO_YIL_UYUMSUZ"
    bf = _fatura(motor, b, adet=1)
    baska_firma = ib.post("/api/despatch-notes", json=_govde(
        bf, bf["kalemler"], issue_date="2042-03-01", despatch_number="IRS2042000000042"))
    assert baska_firma.status_code == 201, baska_firma.text


def test_H66_zaman_alanlari_PGde_ISO_ve_response_ile_AYNI(motor, iki_firma, istemciler) -> None:
    """Üç görünüm de `/response` ile AYNI UTC ISO dizesini vermeli.
    MUTASYON: `_gorunum`u `str(deger)`e geri çevirmek bunu KIRMIZI yapar."""
    a, _ = iki_firma
    ia, _ = istemciler
    f = _fatura(motor, a, adet=1)
    yeni = ia.post("/api/despatch-notes", json=_govde(f, f["kalemler"]))
    assert yeni.status_code == 201, yeni.text
    kimlik = yeni.json()["id"]
    with motor.begin() as c:
        c.execute(text("SET LOCAL TIME ZONE 'Europe/Istanbul'"))
        c.execute(text(
            "UPDATE despatch_notes SET edespatch_status='ACCEPTED',response_status='KABUL',"
            "response_received_at=:ra,edespatch_synced_at=:ra WHERE id=:id AND company_id=:c"),
            {"ra": datetime(2026, 9, 16, 10, 0, 0, 123456, tzinfo=timezone.utc),
             "id": kimlik, "c": a["cid"]})
    beklenen = "2026-09-16T10:00:00.123456+00:00"
    yanit = ia.get(f"/api/despatch-notes/{kimlik}/response")
    assert yanit.status_code == 200, yanit.text
    assert yanit.json()["response_received_at"] == beklenen
    durum = ia.get(f"/api/despatch-notes/{kimlik}/edespatch/status").json()
    detay = ia.get(f"/api/despatch-notes/{kimlik}").json()
    liste = ia.get(f"/api/despatch-notes?invoice_id={f['id']}").json()["items"][0]
    for govde in (durum, detay, liste):
        assert govde["response_received_at"] == beklenen, govde
        assert govde["edespatch_synced_at"] == beklenen, govde
        assert govde["actual_shipment_at"] == "2026-09-15T08:30:00+00:00", govde
        for alan in ("created_at", "updated_at"):
            an = datetime.fromisoformat(govde[alan])
            assert "T" in govde[alan] and an.utcoffset().total_seconds() == 0, (alan, govde[alan])


def test_H68_lines_count_PGde_dogru_ve_kiraci_kapsamli(motor, iki_firma, istemciler) -> None:
    """A'nın iki irsaliyesi (1 ve 2 satır); B'nin üç satırlı irsaliyesi A'nın
    listesine girmez. MUTASYON: `lines_count` alanını düşürmek KeyError."""
    a, b = iki_firma
    ia, ib = istemciler
    f = _fatura(motor, a, adet=3)
    bir = ia.post("/api/despatch-notes",
                  json=_govde(f, f["kalemler"][:1], issue_date="2026-09-01"))
    iki = ia.post("/api/despatch-notes",
                  json=_govde(f, f["kalemler"][1:], issue_date="2026-09-02"))
    assert bir.status_code == 201 and iki.status_code == 201, (bir.text, iki.text)
    bf = _fatura(motor, b, adet=3)
    assert ib.post("/api/despatch-notes", json=_govde(bf)).status_code == 201
    liste = ia.get(f"/api/despatch-notes?invoice_id={f['id']}")
    assert liste.status_code == 200, liste.text
    assert [(o["id"], o["lines_count"]) for o in liste.json()["items"]] == [
        (iki.json()["id"], 2), (bir.json()["id"], 1),
    ]
    suzgecsiz = ia.get("/api/despatch-notes?limit=200").json()["items"]
    assert all(o["invoice_id"] != bf["id"] for o in suzgecsiz)
    assert sum(o["lines_count"] for o in suzgecsiz if o["invoice_id"] == f["id"]) == 3
