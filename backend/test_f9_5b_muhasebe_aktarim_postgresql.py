"""PostgreSQL ikizi: F9-5b — muhasebe dışa aktarımı (`GET /api/accounting/export`).

SQLite ikizi ``tests/test_f9_5b_muhasebe_aktarim.py`` serileştiricileri, zip'i,
409/422'yi, izinleri ve günlüğü ölçer; bu dosya YALNIZ PG'de görünenleri ölçer:

1. **Tutarlı kesit** — dönem `REPEATABLE READ, READ ONLY` işlemde okunur. İlk
   okuma (hesap planı) kesiti sabitledikten SONRA BAŞKA bir bağlantıdan
   eklenip commit edilen onaylı satış aktarıma GİRMEZ; bir sonraki aktarımda
   GİRER. Kesit ifadesi etkisizleştirilince (`READ COMMITTED`) aynı senaryoda
   satış aktarıma SIZAR — adım dişli, boşa geçmiyor.
2. **İşlem gerçekten salt-okunur ve RR** — kesit içinde `SHOW
   transaction_isolation` / `transaction_read_only` ölçülür.
3. **Uçtan uca PG** — 200 zip + manifest özeti, dengesiz dönem 409 ve 409'da
   günlük satırı YOK (psycopg3 `Decimal`/`timestamptz` yolu).

TEMİZLİK: KENDİ firmalarını önekle bulur ve yalnız onların satırlarını siler.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from datetime import datetime, timezone
from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from tests.pg_ikiz_yardimci import pg_secenekleri

pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
ONEK = f"F95B-{KOSU}"
PAROLA = "F95b!Pg2026x"
DONEM = "2026-07"
UTC = timezone.utc

_SILME_SIRASI = (
    "order_items", "orders", "customers", "activity_logs", "user_company_memberships",
)


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("F9-5b ikizi APP_TEST_DATABASE_URL ister")
    return url


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
def uygulama():
    """Açılış göçleri koşturur; uygulamanın motoru `DATABASE_URL`dir (CI: aynı PG)."""
    _url()
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as istemci:
        yield istemci


@pytest.fixture(scope="module")
def motor(uygulama):
    engine = create_engine(_url(), connect_args={"options": pg_secenekleri()})
    _temizle(engine)
    try:
        yield engine
    finally:
        _temizle(engine)
        engine.dispose()


def _ekle(c, tablo: str, **alan) -> int:
    sutunlar = ",".join(alan)
    yer = ",".join(":" + k for k in alan)
    return int(c.execute(text(f"INSERT INTO {tablo}({sutunlar}) VALUES({yer}) RETURNING id"),
                         alan).scalar_one())


def _satis(c, cid, musteri, tarih, no, kalemler=(("20", "100.00", "20.00"),)) -> int:
    kdv = sum((D(k[2]) for k in kalemler), D("0"))
    toplam = sum((D(k[1]) + D(k[2]) for k in kalemler), D("0"))
    oid = _ekle(c, "orders", company_id=cid, customer_id=musteri, order_date=tarih,
                status="approved", document_no=no, payment_method="credit", vat_total=kdv,
                final_total=toplam, subtotal=toplam - kdv, grand_total=toplam)
    for oran, matrah, kv in kalemler:
        tp = D(matrah) + D(kv)
        _ekle(c, "order_items", company_id=cid, order_id=oid, product_name="Ürün",
              quantity=D("1"), unit_price=tp, vat_rate=D(oran), line_subtotal=D(matrah),
              line_vat=D(kv), line_total=tp)
    return oid


@pytest.fixture()
def dunya(motor):
    from app.auth import hash_password

    ek = uuid4().hex[:6]
    an = datetime.now(UTC)
    with motor.begin() as c:
        cid = _ekle(c, "companies", name=f"{ONEK}-{ek}", is_active=True, created_at=an)
        kullanici = f"{ONEK.lower()}-{ek}"
        uid = _ekle(c, "app_users", username=kullanici, email=f"{kullanici}@ornek.test",
                    email_verified=True, display_name="F95b", password_hash=hash_password(PAROLA),
                    role="admin", is_active=True, must_change_password=False, created_at=an)
        _ekle(c, "user_company_memberships", user_id=uid, company_id=cid, is_default=False,
              created_at=an)
        musteri = _ekle(c, "customers", company_id=cid, name="Kesit Müşteri",
                        tax_number="3333333333", opening_balance=0, risk_limit=0,
                        payment_term_days=0, is_active=True)
        ilk = _satis(c, cid, musteri, "2026-07-10", f"{KOSU}-S1")
    return {"cid": cid, "kullanici": kullanici, "musteri": musteri, "ilk": ilk}


def _baslik(istemci, dunya) -> dict:
    r = istemci.post("/api/auth/login", json={"username": dunya["kullanici"], "password": PAROLA})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"], "X-Company-ID": str(dunya["cid"])}


def _aktar(istemci, dunya, target="canonical"):
    return istemci.get(f"/api/accounting/export?period={DONEM}&target={target}",
                       headers=_baslik(istemci, dunya))


def _fis_nolari(cevap) -> list[str]:
    assert cevap.status_code == 200, cevap.text
    zf = zipfile.ZipFile(io.BytesIO(cevap.content))
    return [f["fis_no"] for f in json.loads(zf.read("fisler.json"))]


def _araya_giren_satis(monkeypatch, motor, dunya, olcum: dict) -> None:
    """Router'ın `plan_oku`su: ilk okumayı yapar (kesit SABİTLENİR), sonra BAŞKA
    bir bağlantıdan onaylı satış ekleyip commit eder."""
    from app.routers import accounting

    gercek = accounting.plan_oku

    def araya_gir(oturum, cid):
        plan = gercek(oturum, cid)
        olcum["izolasyon"] = oturum.execute(text("SHOW transaction_isolation")).scalar_one()
        olcum["salt_okunur"] = oturum.execute(text("SHOW transaction_read_only")).scalar_one()
        with motor.begin() as c:
            olcum["yeni"] = _satis(c, dunya["cid"], dunya["musteri"], "2026-07-20",
                                   f"{KOSU}-ARA-{uuid4().hex[:4]}")
        return plan

    monkeypatch.setattr(accounting, "plan_oku", araya_gir)


def test_KESIT_araya_giren_satis_aktarima_GIRMEZ(uygulama, motor, dunya, monkeypatch) -> None:
    olcum: dict = {}
    _araya_giren_satis(monkeypatch, motor, dunya, olcum)
    ilk = _fis_nolari(_aktar(uygulama, dunya))
    assert olcum["izolasyon"] == "repeatable read" and olcum["salt_okunur"] == "on"
    assert ilk == [f"SAT-{dunya['ilk']}"], ilk
    monkeypatch.undo()
    sonraki = _fis_nolari(_aktar(uygulama, dunya))
    assert sonraki == [f"SAT-{dunya['ilk']}", f"SAT-{olcum['yeni']}"]


def test_KESIT_ifadesi_olmadan_satis_SIZAR(uygulama, motor, dunya, monkeypatch) -> None:
    """Dişli: kesit ifadesi düşerse (READ COMMITTED) yukarıdaki adım KIRMIZI olur."""
    from app.routers import accounting

    olcum: dict = {}
    monkeypatch.setattr(accounting, "_kesit_baslat", lambda conn: None)
    _araya_giren_satis(monkeypatch, motor, dunya, olcum)
    nolar = _fis_nolari(_aktar(uygulama, dunya))
    assert olcum["izolasyon"] == "read committed"
    assert f"SAT-{olcum['yeni']}" in nolar


def test_UCTAN_UCA_zip_ozet_ve_409(uygulama, motor, dunya) -> None:
    r = _aktar(uygulama, dunya, target="luca")
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["icerik_sha256"] == hashlib.sha256(zf.read("fisler.json")).hexdigest()
    assert manifest["fis_sayisi"] == 1 and manifest["borc_toplami"] == "120.00"
    assert zf.namelist()[0] == f"luca-{DONEM}-001.xlsx"

    with motor.begin() as c:
        bos = _satis(c, dunya["cid"], dunya["musteri"], "2026-07-21", f"{KOSU}-BOS", kalemler=())
        once = c.execute(text(
            "SELECT COUNT(*) FROM activity_logs WHERE company_id=:c "
            "AND action_type='accounting.exported'"), {"c": dunya["cid"]}).scalar_one()
    r = _aktar(uygulama, dunya)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "DONEM_DENGESIZ"
    assert r.json()["detail"]["ilk_hatalar"][0].startswith(f"SAT-{bos}:")
    with motor.begin() as c:
        sonra = c.execute(text(
            "SELECT COUNT(*) FROM activity_logs WHERE company_id=:c "
            "AND action_type='accounting.exported'"), {"c": dunya["cid"]}).scalar_one()
    assert (once, sonra) == (1, 1)
