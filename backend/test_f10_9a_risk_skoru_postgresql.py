"""PostgreSQL ikizi: F10-9a — müşteri risk skoru (`GET /api/customers/{id}/risk-score`).

SQLite ikizi ``tests/test_f10_9a_risk_skoru.py`` formül tablosunu, SQL
kimliğini, izinleri ve KVKK negatifini ölçer; bu dosya YALNIZ PG'de
görünenleri ölçer:

1. **Tarih aritmetiği iki lehçede** — `orders.order_date`/`due_date`
   `String(30)`dur; biri `DD.MM.YYYY` biçiminde yazılır ve PG dalı
   `normalized_date_sql` (`TO_DATE … TO_CHAR`) ile süzülür. Ücret belgesi
   pencereleri (`period_end>:since AND period_end<=:as_of`) PG `DATE`
   sütununa `date` parametresiyle bağlanır: 24 aylık pencerenin tam sınırı
   (−730 DIŞARIDA, −729 İÇERİDE) PG'de ölçülür.
2. **Tahsis defteri AÇIK yol** — ödemeler API'den siparişe bağlı yazılır,
   defter tahsisi `effective_date` (PG `DATE`) taşır; S2 kapanışı defterden,
   S1 `_ledger_totals`tan gelir. SQLite ikizi bayrak KAPALI yolu ölçer.
3. **Eşitlik** — skorun vadesi geçmiş/açık toplamı, aynı müşterinin
   `receivables-aging` satırına PG'de de kuruşu kuruşuna eşittir; komşu
   müşterinin açık belgesi süzgeçte kalır.

TEMİZLİK: KENDİ firmasını önekle bulur ve yalnız onun satırlarını siler.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from tests.pg_ikiz_yardimci import pg_secenekleri

pytestmark = pytest.mark.postgresql

KOSU = uuid4().hex[:8]
ONEK = f"F109A-{KOSU}"
PAROLA = "F109a!Pg2026x"
UTC = timezone.utc

_SILME_SIRASI = (
    "payment_allocations", "payment_idempotency", "finance_movements", "payments",
    "receivable_charge_documents", "receivable_charge_periods", "late_fee_policies",
    "cek_senetler", "orders", "customers", "activity_logs", "user_company_memberships",
)


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("F10-9a ikizi APP_TEST_DATABASE_URL ister")
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

    from app.config import settings
    from app.main import app

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "payment_allocation_engine_enabled", True)
    try:
        with TestClient(app) as istemci:
            yield istemci
    finally:
        mp.undo()


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


def _bugun():
    from app.business_time import business_today

    return business_today()


def _gun(fark: int) -> str:
    return (_bugun() + timedelta(days=fark)).isoformat()


def _tr(fark: int) -> str:
    """Eski düzen `DD.MM.YYYY` — `normalized_date_sql`in PG dalını sürer."""
    return (_bugun() + timedelta(days=fark)).strftime("%d.%m.%Y")


@pytest.fixture(scope="module")
def dunya(uygulama, motor):
    from app.auth import hash_password

    an = datetime.now(UTC)
    with motor.begin() as c:
        cid = _ekle(c, "companies", name=f"{ONEK}-A", is_active=True, created_at=an)
        diger = _ekle(c, "companies", name=f"{ONEK}-B", is_active=True, created_at=an)
        kullanici = f"{ONEK.lower()}-admin"
        uid = _ekle(c, "app_users", username=kullanici, email=f"{kullanici}@ornek.test",
                    email_verified=True, display_name="F109a", password_hash=hash_password(PAROLA),
                    role="admin", is_active=True, must_change_password=False, created_at=an)
        _ekle(c, "user_company_memberships", user_id=uid, company_id=cid, is_default=False,
              created_at=an)

        def musteri(firma: int, ad: str, limit: str = "0") -> int:
            return _ekle(c, "customers", company_id=firma, name=ad, opening_balance=0,
                         risk_limit=Decimal(limit), payment_term_days=0, is_active=True)

        def siparis(mid: int, tutar: str, od: str, dd: str, no: str) -> int:
            return _ekle(c, "orders", company_id=cid, customer_id=mid, order_date=od,
                         due_date=dd, final_total=Decimal(tutar), status="completed",
                         paid_amount=0, payment_method="credit", payment_term="HARMAN_VADELI",
                         document_no=no)

        risk = musteri(cid, "PG Risk", "1000")
        yakin = musteri(cid, "PG Yakin")
        komsu = musteri(diger, "PG Komsu")
        # o1: tarihleri ESKİ düzende (`DD.MM.YYYY`); PG dalı TO_DATE ile süzer.
        siparis(risk, "1000.00", _tr(-120), _tr(-100), f"{KOSU}-O1")
        siparis(risk, "200.00", _gun(-20), _gun(10), f"{KOSU}-O2")
        o3 = siparis(risk, "400.00", _gun(-200), _gun(-180), f"{KOSU}-O3")
        o4 = siparis(risk, "300.00", _gun(-160), _gun(-150), f"{KOSU}-O4")
        o5 = siparis(risk, "300.00", _gun(-80), _gun(-60), f"{KOSU}-O5")
        siparis(yakin, "500.00", _gun(-50), _gun(-40), f"{KOSU}-Y6")

        # 24 aylık pencerenin TAM sınırı: −730 DIŞARIDA, −729 İÇERİDE.
        for sira, fark in enumerate((-730, -729), start=1):
            cek = _ekle(c, "cek_senetler", company_id=cid, tur="cek", yon="alinan",
                        portfoy_durumu="karsiliksiz", customer_id=risk, tutar=Decimal("50"),
                        vade=_gun(fark - 10), seri_no=f"{KOSU}-{sira}", created_at=an)
            _ekle(c, "receivable_charge_documents", company_id=cid, cek_senet_id=cek,
                  customer_id=risk, charge_type="bounced_check",
                  period_start=_bugun() + timedelta(days=fark),
                  period_end=_bugun() + timedelta(days=fark),
                  due_date_snapshot=_bugun() + timedelta(days=fark - 10),
                  calculation_snapshot="{}", gross_amount=Decimal("50"), status="posted",
                  calculation_fingerprint="f109a", revision_no=1, currency="TRY",
                  exchange_rate=Decimal("1"), posted_at=an)

    r = uygulama.post("/api/auth/login", json={"username": kullanici, "password": PAROLA})
    assert r.status_code == 200, r.text
    uygulama.cookies.clear()
    h = {"Authorization": "Bearer " + r.json()["access_token"], "X-Company-ID": str(cid)}
    for siparis_id, tutar, fark in ((o3, "400.00", -170), (o4, "300.00", -152),
                                    (o5, "300.00", -60)):
        cevap = uygulama.post(
            "/api/payments",
            headers={**h, "Idempotency-Key": f"f109a-pg-{uuid4().hex}"},
            json={"entity_type": "customer", "entity_id": risk, "amount": tutar,
                  "payment_date": _gun(fark), "payment_method": "cash",
                  "reference_type": "order", "reference_id": siparis_id},
        )
        assert cevap.status_code in (200, 201), cevap.text
    return {"cid": cid, "risk": risk, "yakin": yakin, "komsu": komsu, "h": h}


def test_PG_defter_tahsisi_ACIK_ve_tarihler_DATE(motor, dunya) -> None:
    """Ön koşul: yol gerçekten defter yolu; `effective_date` PG `DATE`."""
    with motor.connect() as c:
        tip = c.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='payment_allocations' AND column_name='effective_date'")).scalar_one()
        adet = c.execute(text(
            "SELECT COUNT(*) FROM payment_allocations WHERE company_id=:c"),
            {"c": dunya["cid"]}).scalar_one()
    assert tip == "date" and adet == 3


def test_PG_skor_uctan_uca(uygulama, dunya) -> None:
    cevap = uygulama.get(f"/api/customers/{dunya['risk']}/risk-score", headers=dunya["h"])
    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    cezalar = {c["kod"]: c for c in govde["cezalar"]}
    # En eski açık belge −730 çekinin borcu (vade −740); o1'in (DD.MM.YYYY)
    # düşmediği eşitlik testinde 1000 TL olarak ölçülür.
    assert cezalar["gecikme"]["puan"] == 35
    assert cezalar["gecikme"]["kanit"].startswith("KC-")
    assert cezalar["gecikme"]["kanit"].endswith(f" · vade {_gun(-740)} · 740 gün")
    # Defterden: 3 kapanan, 1'i (o3, 10 gün) geç.
    assert cezalar["gec_kapanis"]["kanit"] == "1 / 3 belge"
    assert govde["yetersiz_veri"] is False
    # Pencere sınırı PG DATE'te: −730 dışarıda, −729 içeride.
    assert cezalar["karsiliksiz"]["kanit"] == "1 evrak"
    assert cezalar["karsiliksiz"]["puan"] == 15
    # Bakiye 2200 − 1000 = 1200 > 1000 (kapının kendi bakiyesi; çek borcu YOK).
    assert cezalar["limit"]["kanit"] == "bakiye 1200.00 / limit 1000.00"
    assert "vade_farki" not in cezalar


def test_PG_skor_YASLANDIRMA_satiri_ile_ayni(uygulama, dunya) -> None:
    from app.db import SessionLocal
    from app.risk_skoru_okuma import sinyalleri_topla

    rapor = uygulama.get("/api/reports/receivables-aging", headers=dunya["h"])
    assert rapor.status_code == 200, rapor.text
    satir = next(s for s in rapor.json()["customers"] if s["customer_id"] == dunya["risk"])
    gecikmis = sum((Decimal(satir[k]) for k in
                    ("days_1_30", "days_31_60", "days_61_90", "days_90_plus")), Decimal("0"))
    with SessionLocal() as db:
        sinyal = sinyalleri_topla(db, dunya["cid"], dunya["risk"], _bugun())
    assert sinyal is not None
    # 1000 (o1) + 2 × 50 çek borcu; o2 vadesiz; YAKIN'ın 500'ü SIZMAZ.
    assert gecikmis == Decimal("1100.00")
    assert sinyal.vadesi_gecmis_toplam == gecikmis
    assert sinyal.acik_toplam == Decimal(satir["total"]) == Decimal("1300.00")


def test_PG_komsu_firma_404(uygulama, dunya) -> None:
    cevap = uygulama.get(f"/api/customers/{dunya['komsu']}/risk-score", headers=dunya["h"])
    assert cevap.status_code == 404, cevap.text
