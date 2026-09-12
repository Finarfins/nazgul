"""PostgreSQL ikizi: CS1 çek/senet portföyü — göç 0085'in GERÇEK kısıtları.

SQLite ikizi ``tests/test_cs1_cek_senet.py`` davranışı (CRUD, durum makinesi,
yetki, bordro, maskeleme, 5.1c) ve SQLite ``PRAGMA foreign_keys=ON`` altında
FK'yi ölçüyor. Bu dosya GELİŞTİRME LEHÇESİNDE GÖRÜNMEYEN dört şeyi ölçer:

1. **UP / DOWN / UP GERÇEK PostgreSQL'de**: ``downgrade`` tabloyu ve
   ``uq_finance_accounts_company_id``yi kaldırır; yeniden ``upgrade`` kurar.
2. **VALIDATE CONSTRAINT SÖZLEŞMESİ**: tablonun HER CHECK ve FK kısıtı
   ``pg_constraint.convalidated = true``dur. Yeni tablo satırsız kurulduğu
   için ``NOT VALID`` ile eklenmesine gerek yoktur; ikiz, hiçbirinin
   doğrulanmamış (yarım) bırakılmadığını katalogdan okur.
3. **``ck_cek_senetler_yon_taraf`` ve ötekiler GERÇEKTEN REDDEDİYOR**: SQLite
   eski sürümlerde CHECK'i yansıtmaz (0072); PG'de her ihlal kendi
   işleminde denenir ve ``IntegrityError`` beklenir.
4. **Bileşik FK çapraz firmayı REDDEDİYOR**: aynı firmanın müşterisi kabul,
   başka firmanınki red — beş FK'nin beşi için.

Paylaşılan veritabanı: CI'da PG ikizleri AYNI veritabanını paylaşır. Her koşu
kendi önekini (``KOSU``) kullanır ve yazdığını siler. Up/down testi şemayı
0084'e indirip GERİ çıkarır; dosyanın sonunda şema baştadır.
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
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
TABLO = "cek_senetler"
ONCE = "20260914_0084"
BAS = "20260914_0086"  # CS2: baş 0086 (bu dosyanın göçü 0085)


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("CS1 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _temizle(engine) -> None:
    with engine.begin() as c:
        if c.execute(text("SELECT to_regclass('public.cek_senetler')")).scalar() is not None:
            c.execute(text("DELETE FROM cek_senetler WHERE seri_no LIKE :o"), {"o": KOSU + "%"})
        firmalar = [r[0] for r in c.execute(
            text("SELECT id FROM companies WHERE name LIKE :o"), {"o": "CS1-" + KOSU + "%"})]
    for cid in firmalar:
        # `activity_logs` YALNIZ-EKLEMEDİR (BEFORE DELETE tetikleyicisi): API
        # smoke'unun denetim satırı taşıyan firması SİLİNEMEZ. O firma pasife
        # alınır — paylaşılan veritabanında etkisiz bir artık kalır, ama
        # başka hiçbir ikizin açılış seçimine girmez (bootstrap aktif firma arar).
        try:
            with engine.begin() as c:
                c.execute(text("DELETE FROM cek_senetler WHERE company_id=:c"), {"c": cid})
                c.execute(text("DELETE FROM finance_accounts WHERE company_id=:c"), {"c": cid})
                c.execute(text("DELETE FROM payments WHERE company_id=:c"), {"c": cid})
                c.execute(text("DELETE FROM customers WHERE company_id=:c"), {"c": cid})
                c.execute(text("DELETE FROM suppliers WHERE company_id=:c"), {"c": cid})
                c.execute(text("DELETE FROM companies WHERE id=:c"), {"c": cid})
        except IntegrityError:
            with engine.begin() as c:
                c.execute(text("UPDATE companies SET is_active=false WHERE id=:c"), {"c": cid})


def _acilisi_kostur() -> None:
    """Uygulama açılışını BİR KEZ, firmalar yazılmadan ÖNCE koştur.

    Ölçüldü: taze şemada açılış verisi (`seed_bootstrap_data`) "aktif bir
    firma" arar; bu ikizin firması o anda tek aktif firmaysa onu BENİMSER
    (şube/depo açar) ve teardown `branches` FK'sine takılır. Önce açılış
    koşarsa "Ana Firma" doğar ve sonraki açılışlar onu kullanır.
    """
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
        {"n": f"CS1-{KOSU}-{ek}", "t": simdi}).scalar_one()
    mus = c.execute(text("INSERT INTO customers(company_id,name) VALUES (:c,'M') RETURNING id"), {"c": cid}).scalar_one()
    ted = c.execute(text("INSERT INTO suppliers(company_id,name) VALUES (:c,'T') RETURNING id"), {"c": cid}).scalar_one()
    hes = c.execute(text(
        "INSERT INTO finance_accounts(company_id,name,account_type,currency,opening_balance,is_active,created_at) "
        "VALUES (:c,'B','bank','TRY',0,true,:t) RETURNING id"), {"c": cid, "t": simdi}).scalar_one()
    ode = c.execute(text(
        "INSERT INTO payments(company_id,entity_type,entity_id,amount,payment_date,payment_method) "
        "VALUES (:c,'customer',:m,10,'2026-09-14','cash') RETURNING id"), {"c": cid, "m": mus}).scalar_one()
    return {"cid": cid, "mus": mus, "ted": ted, "hes": hes, "ode": ode}


@pytest.fixture(scope="module")
def iki_firma(motor):
    with motor.begin() as c:
        return _firma(c, "A"), _firma(c, "B")


def _satir(a: dict, **fazla) -> dict:
    govde = {"company_id": a["cid"], "tur": "cek", "yon": "alinan", "portfoy_durumu": "portfoyde",
             "customer_id": a["mus"], "supplier_id": None, "endorsed_supplier_id": None,
             "tahsil_hesap_id": None, "payment_id": None, "tutar": "125.50",
             "vade": "2026-12-01", "seri_no": KOSU + "-S"}
    govde.update(fazla)
    return govde


def _yaz(motor, govde: dict) -> None:
    sutunlar = ",".join(govde)
    yer = ",".join(":" + k for k in govde)
    with motor.begin() as c:
        c.execute(text("INSERT INTO cek_senetler(" + sutunlar + ") VALUES (" + yer + ")"), govde)


def _reddedilmeli(motor, govde: dict) -> None:
    with pytest.raises(IntegrityError):
        _yaz(motor, govde)


def test_VALIDATE_CONSTRAINT_sozlesmesi_hepsi_dogrulanmis(motor) -> None:
    with motor.connect() as c:
        satirlar = c.execute(text(
            "SELECT conname, contype, convalidated FROM pg_constraint "
            "WHERE conrelid = 'cek_senetler'::regclass AND contype IN ('c','f','u')")).all()
        hedef = c.execute(text(
            "SELECT convalidated FROM pg_constraint WHERE conname='uq_finance_accounts_company_id'")).scalar_one()
    adlar = {r[0] for r in satirlar}
    assert {"ck_cek_senetler_yon_taraf", "ck_cek_senetler_tur", "ck_cek_senetler_yon",
            "ck_cek_senetler_tutar_pozitif", "ck_cek_senetler_portfoy_durumu",
            "fk_cek_senetler_customer", "fk_cek_senetler_supplier",
            "fk_cek_senetler_endorsed_supplier", "fk_cek_senetler_tahsil_hesap",
            "fk_cek_senetler_payment", "uq_cek_senetler_company_id"} <= adlar, adlar
    assert all(r[2] for r in satirlar), [r for r in satirlar if not r[2]]
    assert hedef is True


def test_tutar_NUMERIC_18_2_ve_tarih_DATE(motor) -> None:
    sutunlar = {c["name"]: c for c in inspect(motor).get_columns(TABLO)}
    assert str(sutunlar["tutar"]["type"]) == "NUMERIC(18, 2)"
    assert str(sutunlar["vade"]["type"]) == "DATE"
    assert str(sutunlar["created_at"]["type"]).startswith("TIMESTAMP")


def test_gecerli_satir_KABUL(motor, iki_firma) -> None:
    a, _ = iki_firma
    _yaz(motor, _satir(a, seri_no=KOSU + "-OK1"))
    _yaz(motor, _satir(a, seri_no=KOSU + "-OK2", yon="verilen", customer_id=None, supplier_id=a["ted"],
                       endorsed_supplier_id=None, tahsil_hesap_id=a["hes"], payment_id=a["ode"]))


@pytest.mark.parametrize("bozuk", [
    {"yon": "alinan", "customer_id": None},                   # ck_..._yon_taraf (alınan)
    {"yon": "verilen", "customer_id": None, "supplier_id": None},   # ck_..._yon_taraf (verilen)
    {"tutar": "0"},                                           # ck_..._tutar_pozitif
    {"tur": "bono"},                                          # ck_..._tur
    {"yon": "kayip"},                                         # ck_..._yon
    {"portfoy_durumu": "kayip"},                              # ck_..._portfoy_durumu
])
def test_CHECK_GERCEKTEN_reddediyor(motor, iki_firma, bozuk) -> None:
    a, _ = iki_firma
    _reddedilmeli(motor, _satir(a, seri_no=KOSU + "-CK", **bozuk))


@pytest.mark.parametrize("sutun, anahtar", [
    ("customer_id", "mus"),
    ("supplier_id", "ted"),
    ("endorsed_supplier_id", "ted"),
    ("tahsil_hesap_id", "hes"),
    ("payment_id", "ode"),
])
def test_BILESIK_FK_capraz_firmayi_REDDEDIYOR(motor, iki_firma, sutun, anahtar) -> None:
    a, b = iki_firma
    # Aynı firmanın kaydı KABUL (kısıt var ama doğru satırı engellemiyor) ...
    _yaz(motor, _satir(a, seri_no=KOSU + "-FKA-" + sutun, **{sutun: a[anahtar]}))
    # ... başka firmanınki RED.
    _reddedilmeli(motor, _satir(a, seri_no=KOSU + "-FKB-" + sutun, **{sutun: b[anahtar]}))


def test_GOC_asagi_yukari_PG(motor) -> None:
    # SIRADAN BAĞIMSIZ: firmalar SİLİNMEZ (karışık sırada sonraki testler
    # onları kullanabilir); `downgrade` yalnız tabloyu ve hedef UNIQUE'i düşürür.
    url = _url()
    motor.dispose()
    cfg = _config(url)
    command.downgrade(cfg, ONCE)
    ara = create_engine(url)
    try:
        i = inspect(ara)
        assert TABLO not in i.get_table_names()
        assert "uq_finance_accounts_company_id" not in {u["name"] for u in i.get_unique_constraints("finance_accounts")}
        with ara.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == ONCE
    finally:
        ara.dispose()
    command.upgrade(cfg, "head")
    son = create_engine(url)
    try:
        i = inspect(son)
        assert TABLO in i.get_table_names()
        assert "uq_finance_accounts_company_id" in {u["name"] for u in i.get_unique_constraints("finance_accounts")}
        with son.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == BAS
    finally:
        son.dispose()


def test_UC_KATMANI_PG_smoke(motor, iki_firma) -> None:
    """Uç katmanının SQL'i gerçek PG'de: tipli ``:p IS NULL`` süzgeçleri,
    Core ``INSERT ... RETURNING``, Date/Numeric bağlama, CAS UPDATE, bordro."""
    from fastapi.testclient import TestClient

    from app.auth import hash_password
    from app.main import app

    a, b = iki_firma
    kullanici = "cs1pg-" + KOSU
    parola = "Cs1PgSmoke!2026x"
    with motor.begin() as c:
        uid = c.execute(text(
            "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,role,"
            "is_active,created_at,must_change_password) VALUES (:u,:e,true,'CS1',:h,'muhasebe',true,now(),false) "
            "RETURNING id"), {"u": kullanici, "e": kullanici + "@cek.example", "h": hash_password(parola)}).scalar_one()
        c.execute(text("INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
                       "VALUES (:u,:c,true,now())"), {"u": uid, "c": a["cid"]})
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            giris = client.post("/api/auth/login", json={"username": kullanici, "password": parola})
            assert giris.status_code == 200, giris.text
            client.cookies.clear()
            h = {"Authorization": "Bearer " + giris.json()["access_token"], "X-Company-ID": str(a["cid"])}
            govde = {"tur": "cek", "yon": "alinan", "customer_id": a["mus"], "tutar": "1500.25",
                     "vade": "2026-12-20", "seri_no": KOSU + "-API1", "hesap_no": "TR0001"}
            olus = client.post("/api/cek-senetler", headers=h, json=govde)
            assert olus.status_code == 201, olus.text
            evrak = olus.json()
            assert evrak["tutar"] == "1500.25" and evrak["vade"] == "2026-12-20"
            # Süzgeçlerin HEPSİ boş (NULL parametre) ve HEPSİ dolu — iki uçta da PG tip çıkarımı.
            bos = client.get("/api/cek-senetler?q=" + KOSU + "-API", headers=h)
            assert bos.status_code == 200 and bos.json()["total"] == 1, bos.text
            dolu = client.get(
                f"/api/cek-senetler?tur=cek&yon=alinan&portfoy_durumu=portfoyde&customer_id={a['mus']}"
                f"&vade_from=2026-12-01&vade_to=2026-12-31&q={KOSU}-API", headers=h)
            assert dolu.status_code == 200 and dolu.json()["total"] == 1, dolu.text
            gec = client.post(f"/api/cek-senetler/{evrak['id']}/durum-degistir", headers=h,
                              json={"hedef": "tahsile_verildi"})
            assert gec.status_code == 200, gec.text
            tahsil = client.post(f"/api/cek-senetler/{evrak['id']}/durum-degistir", headers=h,
                                 json={"hedef": "tahsil_edildi", "tahsil_hesap_id": a["hes"],
                                       "tahsil_tarihi": "2026-12-21"})
            assert tahsil.status_code == 200 and tahsil.json()["tahsil_tarihi"] == "2026-12-21", tahsil.text
            red = client.post(f"/api/cek-senetler/{evrak['id']}/durum-degistir", headers=h,
                              json={"hedef": "iade"})
            assert red.status_code == 409 and red.json()["detail"]["code"] == "CEK_GECIS_GECERSIZ"
            bordro = client.post("/api/cek-senetler/bordro", headers=h, json={"satirlar": [
                {**govde, "seri_no": KOSU + "-BRD1"}, {**govde, "seri_no": KOSU + "-BRD2"}]})
            assert bordro.status_code == 201 and len(bordro.json()["ids"]) == 2, bordro.text
            once = client.get("/api/cek-senetler?q=" + KOSU, headers=h).json()["total"]
            atom = client.post("/api/cek-senetler/bordro", headers=h, json={"satirlar": [
                {**govde, "seri_no": KOSU + "-ATM1"}, {**govde, "seri_no": KOSU + "-ATM2", "customer_id": b["mus"]}]})
            assert atom.status_code == 422, atom.text
            assert client.get("/api/cek-senetler?q=" + KOSU, headers=h).json()["total"] == once
            baska = {"Authorization": h["Authorization"], "X-Company-ID": str(b["cid"])}
            assert client.get(f"/api/cek-senetler/{evrak['id']}", headers=baska).status_code == 403
            # int4 üstü kimlik PG'de `::INTEGER` taşmasıyla 500 verirdi; uçta 422.
            assert client.get("/api/cek-senetler/2147483648", headers=h).status_code == 422
            assert client.get("/api/cek-senetler/2147483647", headers=h).status_code == 404
            assert client.get("/api/cek-senetler?customer_id=2147483648", headers=h).status_code == 422
    finally:
        with motor.begin() as c:
            # `activity_logs` silinmez (yalnız-ekleme); satırları firmayla kalır.
            c.execute(text("DELETE FROM cek_senetler WHERE created_by=:u"), {"u": uid})
            c.execute(text("DELETE FROM auth_tokens WHERE user_id=:u"), {"u": uid})
            c.execute(text("DELETE FROM auth_refresh_tokens WHERE user_id=:u"), {"u": uid})
            c.execute(text("DELETE FROM user_company_memberships WHERE user_id=:u"), {"u": uid})
            c.execute(text("DELETE FROM app_users WHERE id=:u"), {"u": uid})
