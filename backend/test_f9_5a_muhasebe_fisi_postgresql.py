"""PostgreSQL ikizi: F9-5a — muhasebe fişi / hesap eşlemesi (göç 20260927_0093).

SQLite ikizi ``tests/test_f9_5a_muhasebe_fisi.py`` fişin aritmetiğini, beş
kolu, izinleri ve maskeyi ölçer; bu dosya YALNIZ PG'de görünenleri ölçer:

1. **G5 ay sınırı `timestamptz` üzerinde** — 31 Temmuz 23:30 İstanbul (20:30
   UTC) kesilen fatura TEMMUZ'dur; 1 Ağustos 00:00 İstanbul (21:00 UTC) tam
   sınırı AĞUSTOS'tur; 1 Temmuz 00:30 İstanbul (30 Haziran 21:30 UTC) TEMMUZ'dur.
   Aynı sınır müstahsil `issued_at`inde. `substr(created_at, …)` PG'de
   çalışmaz — sorgu ona dönerse bu dosya KIRMIZI olur.
2. **NULL'lı tekillik** — iki kısmi indeks `(firma, olay, NULL, NULL)`ı ve
   `(firma, olay, 20, NULL)`ı İKİNCİ kez reddeder; `esleme_yaz`ın tipli
   `:p IS NULL` bağı PG'de `AmbiguousParameter` VERMEZ.
3. **Göç turu** — `0093 -> 0092 -> head` taze şemada: tablo gider, geri gelir,
   iki indeks `WHERE`li ve `COALESCE`li kurulur.

TEMİZLİK: KENDİ firmalarını önekle bulur ve yalnız onların satırlarını siler.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import sessionmaker

BACKEND = Path(__file__).resolve().parent
pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
ONEK = f"F95A-{KOSU}"
GOC = "20260927_0093"
ONCEKI = "20260925_0092"
UTC = timezone.utc

_SILME_SIRASI = (
    "muhasebe_hesap_eslemeleri", "producer_receipt_items", "producer_receipts",
    "invoice_items", "invoices", "customers", "suppliers", "user_company_memberships",
)


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("F9-5a ikizi APP_TEST_DATABASE_URL ister")
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


def _ekle(c, tablo: str, **alan) -> int:
    sutunlar = ",".join(alan)
    yer = ",".join(":" + k for k in alan)
    return int(c.execute(text(f"INSERT INTO {tablo}({sutunlar}) VALUES({yer}) RETURNING id"),
                         alan).scalar_one())


def _firma(c, ek: str) -> int:
    return _ekle(c, "companies", name=f"{ONEK}-{ek}", is_active=True, created_at=datetime.now(UTC))


def _fatura(c, cid, uid, no, an, musteri) -> int:
    bos = "{}"
    fid = _ekle(c, "invoices", company_id=cid, invoice_number=no, status="ISSUED", currency="TRY",
                exchange_rate=D("1"), customer_snapshot=json.dumps(musteri),
                machine_snapshot=bos, work_order_snapshot=bos, company_snapshot=bos,
                technician_snapshot=bos, warranty_snapshot=bos, totals_snapshot=bos,
                tax_snapshot=bos, created_by=uid, created_at=an, updated_at=an)
    _ekle(c, "invoice_items", company_id=cid, invoice_id=fid, item_type="PART", description="Parça",
          quantity=D("1"), unit_price=D("120.00"), original_price=D("120.00"),
          discount_amount=D("0"), tax_rate=D("20"), tax_amount=D("20.00"), total=D("120.00"),
          warranty_percent=D("0"), customer_payable=D("120.00"), company_payable=D("0.00"),
          source_snapshot=bos)
    return fid


def _makbuz(c, cid, tedarikci, no, an) -> int:
    simdi = datetime.now(UTC)
    rid = _ekle(c, "producer_receipts", company_id=cid, supplier_id=tedarikci, receipt_no=no,
                status="issued", issued_at=an, gross_amount=D("100.00"),
                withholding_total=D("2.00"), social_security_total=D("1.00"),
                net_payable=D("97.00"), created_at=simdi, updated_at=simdi)
    _ekle(c, "producer_receipt_items", company_id=cid, receipt_id=rid, entered_quantity=D("100"),
          entered_unit="kg", entered_factor=D("1"), base_quantity=D("100"), unit_price=D("1.00"),
          line_gross=D("100.00"), withholding_rate=D("2"), withholding_amount=D("2.00"),
          social_security_rate=D("1"), social_security_amount=D("1.00"), line_net=D("97.00"),
          created_at=simdi, updated_at=simdi)
    return rid


def test_G5_AY_SINIRI_timestamptz_ISTANBUL_ayina_gore(motor) -> None:
    from app.auth import hash_password
    from app.muhasebe.hesap_plani import HesapPlani
    from app.muhasebe.kaynak import donem_coz, donem_oku

    with motor.begin() as c:
        cid = _firma(c, "SINIR")
        komsu = _firma(c, "KOMSU")
        uid = _ekle(c, "app_users", username=f"{ONEK.lower()}-admin",
                    email=f"{ONEK.lower()}@ornek.test", email_verified=True, display_name="F95a",
                    password_hash=hash_password("F95a!Pg2026x"), role="admin", is_active=True,
                    must_change_password=False, created_at=datetime.now(UTC))
        musteri = _ekle(c, "customers", company_id=cid, name="Sınır Müşteri",
                        tax_number="1111111111", opening_balance=0, risk_limit=0,
                        payment_term_days=0, is_active=True)
        tedarikci = _ekle(c, "suppliers", company_id=cid, name="Sınır Çiftçi",
                          tax_number="2222222222", opening_balance=0, risk_limit=0,
                          payment_term_days=0, is_active=True)
        m = {"id": musteri, "name": "Sınır Müşteri", "tax_number": "1111111111"}
        _fatura(c, cid, uid, f"{KOSU}-SON", datetime(2026, 7, 31, 20, 30, tzinfo=UTC), m)
        _fatura(c, cid, uid, f"{KOSU}-TAM", datetime(2026, 7, 31, 21, 0, tzinfo=UTC), m)
        _fatura(c, cid, uid, f"{KOSU}-ILK", datetime(2026, 6, 30, 21, 30, tzinfo=UTC), m)
        _fatura(c, cid, uid, f"{KOSU}-ONCE", datetime(2026, 6, 30, 20, 59, 59, tzinfo=UTC), m)
        _fatura(c, komsu, uid, f"{KOSU}-KOMSU", datetime(2026, 7, 15, 9, 0, tzinfo=UTC), {})
        _makbuz(c, cid, tedarikci, f"{KOSU}-M1", datetime(2026, 7, 31, 20, 59, 59, tzinfo=UTC))
        _makbuz(c, cid, tedarikci, f"{KOSU}-M2", datetime(2026, 7, 31, 21, 0, tzinfo=UTC))

    Oturum = sessionmaker(bind=motor)
    with Oturum() as db:
        temmuz = donem_oku(db, cid, donem_coz("2026-07"), HesapPlani())
        agustos = donem_oku(db, cid, donem_coz("2026-08"), HesapPlani())
        haziran = donem_oku(db, cid, donem_coz("2026-06"), HesapPlani())
    assert {f.fis_no for f in temmuz.fisler} == {
        f"SVF-{KOSU}-SON", f"SVF-{KOSU}-ILK", f"MM-{KOSU}-M1",
    }
    assert {f.fis_no for f in agustos.fisler} == {f"SVF-{KOSU}-TAM", f"MM-{KOSU}-M2"}
    assert {f.fis_no for f in haziran.fisler} == {f"SVF-{KOSU}-ONCE"}
    son = next(f for f in temmuz.fisler if f.fis_no == f"SVF-{KOSU}-SON")
    assert son.fis_tarihi.isoformat() == "2026-07-31"
    ilk = next(f for f in temmuz.fisler if f.fis_no == f"SVF-{KOSU}-ILK")
    assert ilk.fis_tarihi.isoformat() == "2026-07-01"
    assert any(u.kod == "G5" and u.belge_no == f"{KOSU}-ILK" for u in temmuz.uyarilar)
    assert temmuz.mustahsil.belge_sayisi == 1 and temmuz.mustahsil.stopaj == D("2.00")
    assert all(f.borc_toplami == f.alacak_toplami for f in temmuz.fisler)


def test_TEKIL_NULL_iki_kismi_indeks_ve_tipli_bag(motor) -> None:
    from app.muhasebe.hesap_plani import esleme_yaz

    an = datetime.now(UTC)
    with motor.begin() as c:
        cid = _firma(c, "TEKIL")

    def yaz(olay, oran, taraf, kod):
        with motor.begin() as c:
            c.execute(text(
                "INSERT INTO muhasebe_hesap_eslemeleri(company_id,olay,kdv_orani,taraf_tipi,"
                "hesap_kodu,updated_at) VALUES(:c,:o,:k,:t,:h,:a)"),
                {"c": cid, "o": olay, "k": oran, "t": taraf, "h": kod, "a": an})

    yaz("SATIS_CARI", None, None, "120")
    with pytest.raises(IntegrityError, match="uq_mhe_oransiz"):
        yaz("SATIS_CARI", None, None, "121")
    yaz("SATIS_KDV", D("20"), None, "391.20")
    with pytest.raises(IntegrityError, match="uq_mhe_oranli"):
        yaz("SATIS_KDV", D("20.0000"), None, "391.21")
    yaz("SATIS_KDV", D("20"), "CUSTOMER", "391.22")
    yaz("SATIS_CARI", None, "CUSTOMER", "120.01")
    with pytest.raises(IntegrityError, match="ck_mhe_olay"):
        yaz("YOK", None, None, "100")

    Oturum = sessionmaker(bind=motor)
    with Oturum() as db:
        # Tipli `:p IS NULL` — PG `AmbiguousParameter` vermez; var olan satırı günceller.
        assert esleme_yaz(db, cid, "SATIS_CARI", None, None, "120.05") == ("120", "120.05")
        assert esleme_yaz(db, cid, "SATIS_KDV", D("20"), None, "391.20") == ("391.20", "391.20")
        assert esleme_yaz(db, cid, "ALIS_KDV", D("10"), "SUPPLIER", "191.10") == (None, "191.10")
        db.commit()
    with motor.connect() as c:
        sayi = c.execute(text("SELECT COUNT(*) FROM muhasebe_hesap_eslemeleri WHERE company_id=:c"),
                         {"c": cid}).scalar_one()
    assert sayi == 5


def test_GOC_TURU_up_down_up_taze_semada(motor) -> None:
    url = _url()
    command.downgrade(_config(url), ONCEKI)
    try:
        assert "muhasebe_hesap_eslemeleri" not in inspect(motor).get_table_names()
        with motor.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == ONCEKI
    finally:
        command.upgrade(_config(url), "head")
    with motor.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == GOC
        tanimlar = dict(c.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='muhasebe_hesap_eslemeleri'"
        )).all())
    assert "WHERE (kdv_orani IS NOT NULL)" in tanimlar["uq_mhe_oranli"], tanimlar
    assert "WHERE (kdv_orani IS NULL)" in tanimlar["uq_mhe_oransiz"], tanimlar
    assert "COALESCE(taraf_tipi" in tanimlar["uq_mhe_oranli"]
    assert "COALESCE(taraf_tipi" in tanimlar["uq_mhe_oransiz"]
    kisitlar = {k["name"] for k in inspect(motor).get_check_constraints("muhasebe_hesap_eslemeleri")}
    assert {"ck_mhe_olay", "ck_mhe_kdv_orani", "ck_mhe_taraf_tipi"} <= kisitlar
