"""PostgreSQL ikizi: CS2 çek/senet ↔ cari — göç 0086'nın GERÇEK kısıtları ve
köprünün PG'deki SQL'i.

SQLite ikizi ``tests/test_cs2_cek_senet_cari.py`` davranışı ölçüyor. Bu dosya
GELİŞTİRME LEHÇESİNDE GÖRÜNMEYEN beş şeyi ölçer:

1. **UP / DOWN / UP GERÇEK PostgreSQL'de**: ``downgrade`` sütunu, FK'yi,
   tekil indeksi ve firma anahtarını kaldırır, CHECK'leri 0041'e döndürür;
   ``bounced_check`` satırı varken REDDEDER.
2. **CHECK'ler GERÇEKTEN reddediyor** ve ``pg_constraint.convalidated``:
   çeksiz ``bounced_check`` red; vade farkı alanı dolu ``bounced_check`` red;
   vade farkı şekli DEĞİŞMEDİ (siparişsiz ``late_fee`` red); kapalı küme dışı
   tür red.
3. **Bileşik FK** başka firmanın çekini reddeder; **tekil aktif belge**
   indeksi aynı çeke ikinci aktif belgeyi reddeder.
4. **Ekstrenin UNION'ı** (``DATE`` ``period_end`` metne indirilir, ``NULL``
   kolonlar), panonun CTE'leri ve ``SELECT ... FOR UPDATE`` gerçek PG'de.
5. Köprü, tahsis motoru hangi ayardaysa o yolda PG'de yazar.

Paylaşılan veritabanı: her koşu kendi önekini (``KOSU``) kullanır ve yazdığını
siler — ``bounced_check`` satırları DAHİL, çünkü onlar kalırsa başka bir
ikizin (CS1) 0084'e inişi 0086 kapısında düşerdi.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
BELGE = "receivable_charge_documents"
ONCE = "20260914_0085"
BAS = "20260914_0086"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("CS2 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _firmalar(c) -> list[int]:
    return [r[0] for r in c.execute(
        text("SELECT id FROM companies WHERE name LIKE :o"), {"o": "CS2-" + KOSU + "%"})]


def _belgeleri_sil(engine) -> None:
    """Bu koşunun borç belgeleri — ÖNCE (0086 inişini ve FK'yi serbest bırakır)."""
    with engine.begin() as c:
        if c.execute(text("SELECT to_regclass('public.cek_senetler')")).scalar() is None:
            return
        for cid in _firmalar(c):
            c.execute(text(f"DELETE FROM {BELGE} WHERE company_id=:c AND charge_type='bounced_check'"),
                      {"c": cid})


def _temizle(engine) -> None:
    _belgeleri_sil(engine)
    with engine.begin() as c:
        firmalar = _firmalar(c)
    for cid in firmalar:
        # `activity_logs` YALNIZ-EKLEMEDİR: API smoke'unun firması silinemez,
        # pasife alınır (CS1 ikizinin gerekçesi).
        try:
            with engine.begin() as c:
                for tablo in ("payment_allocations", "payment_idempotency", "finance_transactions"):
                    c.execute(text(f"DELETE FROM {tablo} WHERE company_id=:c"), {"c": cid})
                c.execute(text("DELETE FROM cek_senetler WHERE company_id=:c"), {"c": cid})
                for tablo in ("payments", "orders", "finance_accounts", "customers", "suppliers"):
                    c.execute(text(f"DELETE FROM {tablo} WHERE company_id=:c"), {"c": cid})
                c.execute(text("DELETE FROM companies WHERE id=:c"), {"c": cid})
        except IntegrityError:
            with engine.begin() as c:
                c.execute(text("UPDATE companies SET is_active=false WHERE id=:c"), {"c": cid})


def _acilisi_kostur() -> None:
    """Açılış verisi firmalar yazılmadan ÖNCE (CS1 ikizinin gerekçesi)."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app):
        pass


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


def _firma(c, ek: str) -> dict:
    simdi = datetime.now(timezone.utc)
    cid = c.execute(text(
        "INSERT INTO companies(name,is_active,created_at) VALUES (:n,true,:t) RETURNING id"),
        {"n": f"CS2-{KOSU}-{ek}", "t": simdi}).scalar_one()
    mus = c.execute(text("INSERT INTO customers(company_id,name,opening_balance) VALUES (:c,'M',0) RETURNING id"),
                    {"c": cid}).scalar_one()
    hes = c.execute(text(
        "INSERT INTO finance_accounts(company_id,name,account_type,currency,opening_balance,is_active,created_at) "
        "VALUES (:c,'B','bank','TRY',0,true,:t) RETURNING id"), {"c": cid, "t": simdi}).scalar_one()
    bugun = date.today()
    c.execute(text(
        "INSERT INTO orders(customer_id,order_date,due_date,final_total,status,paid_amount,payment_method,company_id) "
        "VALUES (:m,:od,:dd,5000,'completed',0,'credit',:c)"),
        {"m": mus, "od": (bugun - timedelta(days=10)).isoformat(),
         "dd": (bugun + timedelta(days=20)).isoformat(), "c": cid})
    cek = c.execute(text(
        "INSERT INTO cek_senetler(company_id,tur,yon,portfoy_durumu,customer_id,tutar,vade,seri_no,created_at) "
        "VALUES (:c,'cek','alinan','karsiliksiz',:m,100,'2026-12-01',:s,now()) RETURNING id"),
        {"c": cid, "m": mus, "s": f"{KOSU}-{ek}"}).scalar_one()
    return {"cid": cid, "mus": mus, "hes": hes, "cek": cek}


@pytest.fixture(scope="module")
def iki_firma(motor):
    with motor.begin() as c:
        return _firma(c, "A"), _firma(c, "B")


def _belge(a: dict, **fazla) -> dict:
    govde = {"company_id": a["cid"], "cek_senet_id": a["cek"], "customer_id": a["mus"],
             "charge_type": "bounced_check", "period_start": "2026-12-02", "period_end": "2026-12-02",
             "due_date_snapshot": "2026-12-01", "calculation_snapshot": "{}", "gross_amount": "100",
             "status": "posted", "calculation_fingerprint": "x", "revision_no": 1,
             "currency": "TRY", "exchange_rate": 1}
    govde.update(fazla)
    return govde


def _yaz(motor, govde: dict) -> None:
    sutunlar = ",".join(govde)
    yer = ",".join(":" + k for k in govde)
    with motor.begin() as c:
        c.execute(text(f"INSERT INTO {BELGE}(" + sutunlar + ") VALUES (" + yer + ")"), govde)


def _reddedilmeli(motor, govde: dict) -> None:
    with pytest.raises(IntegrityError):
        _yaz(motor, govde)


def test_kisitlar_DOGRULANMIS_ve_yerinde(motor) -> None:
    with motor.connect() as c:
        satirlar = c.execute(text(
            "SELECT conname, convalidated, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'receivable_charge_documents'::regclass AND contype IN ('c','f')")).all()
        indeks = c.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname='uq_receivable_bounced_check_active'")).scalar_one()
    tanim = {r[0]: r[2] for r in satirlar}
    assert all(r[1] for r in satirlar), [r for r in satirlar if not r[1]]
    assert "bounced_check" in tanim["ck_receivable_charge_document_type"]
    assert "bounced_check" in tanim["ck_receivable_charge_document_source_shape"]
    assert "cek_senetler" in tanim["fk_receivable_charge_document_cek_senet"]
    assert "UNIQUE" in indeks and "cek_senet_id IS NOT NULL" in indeks and "'posted'" in indeks
    sutun = {c["name"]: c for c in inspect(motor).get_columns("companies")}["ciro_tedarikci_odemesi"]
    assert sutun["nullable"] is False and str(sutun["default"]).lower() == "false"


def test_gecerli_bounced_check_KABUL_ikinci_aktif_RED(motor, iki_firma) -> None:
    a, _ = iki_firma
    try:
        _yaz(motor, _belge(a))
        # Aynı çeke ikinci AKTİF belge -> tekil indeks.
        _reddedilmeli(motor, _belge(a, revision_no=2))
    finally:
        _belgeleri_sil(motor)


@pytest.mark.parametrize("bozuk", [
    {"cek_senet_id": None},                         # bounced_check çeksiz
    {"charge_type": "kara_liste"},                  # kapalı küme dışı
    {"principal_basis": "100"},                     # vade farkı alanı dolu
])
def test_CHECK_GERCEKTEN_reddediyor(motor, iki_firma, bozuk) -> None:
    a, _ = iki_firma
    _reddedilmeli(motor, _belge(a, **bozuk))


def test_VADE_FARKI_sekli_DEGISMEDI(motor, iki_firma) -> None:
    """Siparişsiz/dönemsiz bir ``late_fee`` 0041'de de 0086'da da red; çeke
    bağlı bir ``late_fee`` 0086'da red."""
    a, _ = iki_firma
    _reddedilmeli(motor, _belge(a, charge_type="late_fee", cek_senet_id=None))
    _reddedilmeli(motor, _belge(a, charge_type="late_fee"))


def test_BILESIK_FK_capraz_firmayi_REDDEDIYOR(motor, iki_firma) -> None:
    a, b = iki_firma
    _reddedilmeli(motor, _belge(a, cek_senet_id=b["cek"]))


def test_GOC_asagi_yukari_PG(motor, iki_firma) -> None:
    url = _url()
    a, _ = iki_firma
    cfg = _config(url)
    try:
        # Kendi satırı varken iniş REDDEDİLİR ...
        _yaz(motor, _belge(a))
        motor.dispose()
        with pytest.raises(RuntimeError, match="0086 geri alınamaz"):
            command.downgrade(cfg, ONCE)
    finally:
        _belgeleri_sil(motor)
        motor.dispose()
    # ... satır yokken 0085'e iner.
    command.downgrade(cfg, ONCE)
    ara = create_engine(url)
    try:
        i = inspect(ara)
        assert "cek_senet_id" not in {c["name"] for c in i.get_columns(BELGE)}
        assert "ciro_tedarikci_odemesi" not in {c["name"] for c in i.get_columns("companies")}
        assert "uq_receivable_bounced_check_active" not in {x["name"] for x in i.get_indexes(BELGE)}
        with ara.connect() as c:
            tur = c.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname='ck_receivable_charge_document_type'")).scalar_one()
            assert "bounced_check" not in tur and "service_fee" in tur
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == ONCE
    finally:
        ara.dispose()
    command.upgrade(cfg, "head")
    son = create_engine(url)
    try:
        with son.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == BAS
        assert "cek_senet_id" in {c["name"] for c in inspect(son).get_columns(BELGE)}
    finally:
        son.dispose()


def test_UC_KATMANI_PG_smoke(motor, iki_firma) -> None:
    """Köprü, geçiş yan etkileri (``FOR UPDATE`` dahil), ekstre UNION'ı, pano
    CTE'leri ve yaşlandırma gerçek PG'de."""
    from fastapi.testclient import TestClient

    from app.auth import hash_password
    from app.business_time import business_today
    from app.main import app

    a, _ = iki_firma
    kullanici = "cs2pg-" + KOSU
    parola = "Cs2PgSmoke!2026x"
    with motor.begin() as c:
        uid = c.execute(text(
            "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,role,"
            "is_active,created_at,must_change_password) VALUES (:u,:e,true,'CS2',:h,'admin',true,now(),false) "
            "RETURNING id"), {"u": kullanici, "e": kullanici + "@cari.example", "h": hash_password(parola)}).scalar_one()
        c.execute(text("INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
                       "VALUES (:u,:c,true,now())"), {"u": uid, "c": a["cid"]})
    bugun = business_today()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            giris = client.post("/api/auth/login", json={"username": kullanici, "password": parola})
            assert giris.status_code == 200, giris.text
            client.cookies.clear()
            h = {"Authorization": "Bearer " + giris.json()["access_token"], "X-Company-ID": str(a["cid"])}
            pencere = f"?date_from={(bugun - timedelta(days=30)).isoformat()}&date_to={bugun.isoformat()}"
            once = client.get(f"/api/customers/{a['mus']}/statement{pencere}", headers=h)
            assert once.status_code == 200, once.text
            odeme = client.post("/api/payments", headers={**h, "Idempotency-Key": f"cs2pg-{KOSU}"}, json={
                "entity_type": "customer", "entity_id": a["mus"], "amount": "2000",
                "payment_date": (bugun - timedelta(days=5)).isoformat(), "payment_method": "check",
                "cek_senet": {"vade": (bugun + timedelta(days=30)).isoformat(), "seri_no": KOSU + "-API"}})
            assert odeme.status_code == 201, odeme.text
            evrak = odeme.json()["cek_senet_id"]
            yol = f"/api/cek-senetler/{evrak}/durum-degistir"
            assert client.post(yol, headers=h, json={"hedef": "tahsile_verildi"}).status_code == 200
            krs = client.post(yol, headers=h, json={"hedef": "karsiliksiz"})
            assert krs.status_code == 200 and krs.json()["charge_document_id"] is not None, krs.text
            sonra = client.get(f"/api/customers/{a['mus']}/statement{pencere}", headers=h)
            assert sonra.status_code == 200, sonra.text
            sonra = sonra.json()
            assert sonra["closing_balance"] == once.json()["closing_balance"]
            etiketler = [s["label"] for s in sonra["lines"]]
            assert "Tahsilat (Çek - Karşılıksız)" in etiketler and "Karşılıksız Çek Dekontu" in etiketler
            assert Decimal(sonra["lines"][-1]["balance"]) == Decimal(sonra["closing_balance"])
            pano = client.get("/api/dashboard", headers=h)
            assert pano.status_code == 200 and "portfolio_checks" in pano.json(), pano.text
            rapor = client.get("/api/reports/receivables-aging", headers=h)
            assert rapor.status_code == 200, rapor.text
            satir = next(s for s in rapor.json()["customers"] if s["customer_id"] == a["mus"])
            assert any(d["document_type"] == "bounced_check" for d in satir["documents"])
            # İkinci bir çek tahsilde -> tahsil edildi: finans hareketi PG'de.
            ikinci = client.post("/api/payments", headers={**h, "Idempotency-Key": f"cs2pg-{KOSU}-2"}, json={
                "entity_type": "customer", "entity_id": a["mus"], "amount": "100",
                "payment_date": bugun.isoformat(), "payment_method": "check",
                "cek_senet": {"vade": (bugun + timedelta(days=5)).isoformat(), "seri_no": KOSU + "-API2"}})
            assert ikinci.status_code == 201, ikinci.text
            yol2 = f"/api/cek-senetler/{ikinci.json()['cek_senet_id']}/durum-degistir"
            assert client.post(yol2, headers=h, json={"hedef": "tahsile_verildi"}).status_code == 200
            tahsil = client.post(yol2, headers=h, json={"hedef": "tahsil_edildi", "tahsil_hesap_id": a["hes"],
                                                        "tahsil_tarihi": bugun.isoformat()})
            assert tahsil.status_code == 200 and tahsil.json()["financial_transaction_id"], tahsil.text
    finally:
        _belgeleri_sil(motor)
        with motor.begin() as c:
            c.execute(text("DELETE FROM auth_tokens WHERE user_id=:u"), {"u": uid})
            c.execute(text("DELETE FROM auth_refresh_tokens WHERE user_id=:u"), {"u": uid})
            c.execute(text("DELETE FROM user_company_memberships WHERE user_id=:u"), {"u": uid})
