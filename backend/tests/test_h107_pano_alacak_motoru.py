"""H107 — panonun gecikmiş alacakları ALACAK MOTORUNDAN gelir.

Konu: ``app/routers/dashboard.py`` (``overdue_count`` / ``overdue_total`` /
``overdue_receivables``).

Develop'ta pano ``final_total - paid_amount`` okuyordu. ``paid_amount`` yalnız
siparişe DOĞRUDAN bağlı tahsisleri taşır
(``payment_allocation_engine._materialize_direct_paid_amount``); bağsız
tahsilatın defterde FIFO ile kapattığı kısım, iade ve deftere işlenmiş vade
farkı ona hiç girmez.

Seçim: ``calculate_net_receivables`` (satış anaparası + ``late_fee`` /
``service_fee`` / ``bounced_check`` borç belgeleri). Yaşlandırma raporu aynı
fonksiyonu kullanır; F10-9 keşfi §1.6 "vadesi geçmiş" rakamının yaşlandırma
motorununki olmasını önerir.

--- MUTASYON TABLOSU --------------------------------------------------------

  * pano formülünü ``final_total-paid_amount``a döndürmek -> TOPLAM KIRMIZI
  * ``calculate_net_receivables`` yerine ``calculate_receivables`` -> VADE FARKI KIRMIZI
  * ``due_date < today`` yerine ``<=`` -> BUGÜN VADELİ SATIŞ ADEDİ KIRMIZI
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PAROLA = "PanoAlacakMotoru!2026x"
ANAHTAR = "h107-pano-alacak-motoru-anahtar-h107-pano-alacak-motoru-anahtar"
KURUS = Decimal("0.01")


def _app_anahtarlari() -> set[str]:
    return {k for k in sys.modules if k == "app" or k.startswith("app.")}


@contextmanager
def _uygulama(tmp_path: Path):
    onceki = {k: sys.modules[k] for k in _app_anahtarlari()}
    mp = pytest.MonkeyPatch()
    for ad in list(_app_anahtarlari()):
        del sys.modules[ad]
    mp.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'h107.db').as_posix()}")
    mp.setenv("SUNGUR_DATA_DIR", str(tmp_path))
    mp.setenv("SECRET_KEY", ANAHTAR)
    mp.setenv("BOOTSTRAP_ADMIN_PASSWORD", PAROLA)
    mp.setenv("AUTO_MIGRATE", "true")
    mp.setenv("SUNGUR_PLATFORM_OPERATORS", "")
    mp.setenv("PAYMENT_ALLOCATION_ENGINE_ENABLED", "true")
    mp.syspath_prepend(str(BACKEND))
    try:
        from fastapi.testclient import TestClient

        import app.main as main
        from app.db import engine

        with TestClient(main.app, raise_server_exceptions=False) as client:
            yield engine, client
    finally:
        mp.undo()
        for ad in list(_app_anahtarlari()):
            del sys.modules[ad]
        sys.modules.update(onceki)


def _gun(fark: int) -> str:
    from app.business_time import business_today

    return (business_today() + timedelta(days=fark)).isoformat()


def _tohumla(engine) -> dict:
    """Taze firma: yalnız bu testin belgeleri, toplamlar mutlak karşılaştırılır."""
    from sqlalchemy import text

    from app.auth import hash_password

    simdi = datetime.now(timezone.utc)
    with engine.begin() as c:
        c.execute(text("UPDATE app_users SET must_change_password=0"))
        firma = int(c.execute(text(
            "INSERT INTO companies(name,is_active,created_at) VALUES ('H107 Pano',1,:t) RETURNING id"),
            {"t": simdi}).scalar_one())
        uid = int(c.execute(text(
            "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,role,"
            "is_active,created_at,must_change_password) VALUES "
            "('h107admin','h107admin@pano.example',1,'h107admin',:h,'admin',1,:t,0) RETURNING id"),
            {"h": hash_password(PAROLA), "t": simdi}).scalar_one())
        c.execute(text(
            "INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
            "VALUES (:u,:c,1,:t)"), {"u": uid, "c": firma, "t": simdi})

        def musteri(ad: str) -> int:
            return int(c.execute(text(
                "INSERT INTO customers(company_id,name,opening_balance) VALUES (:c,:n,0) RETURNING id"),
                {"c": firma, "n": ad}).scalar_one())

        def siparis(mid: int, *, tutar: str, od: int, dd: int, no: str | None = None) -> int:
            return int(c.execute(text(
                "INSERT INTO orders(customer_id,order_date,due_date,final_total,status,paid_amount,"
                "payment_method,payment_term,document_no,company_id) VALUES "
                "(:m,:od,:dd,:t,'completed',0,'credit','HARMAN_VADELI',:no,:c) RETURNING id"),
                {"m": mid, "od": _gun(od), "dd": _gun(dd), "t": tutar, "no": no,
                 "c": firma}).scalar_one())

        ali = musteri("H107 Ali")
        veli = musteri("H107 Veli")
        # Ali: 1000 TL, vadesi 30 gün önce geçti; bağsız 400 TL tahsilat defterde
        # FIFO ile BUNU kapatır ama `paid_amount` 0 kalır.
        ali_siparis = siparis(ali, tutar="1000.00", od=-60, dd=-30, no="H107-ALI")
        # Veli: vadesi BUGÜN — gecikmiş DEĞİL.
        siparis(veli, tutar="250.00", od=-5, dd=0)
        # Veli: belge numarasız, vadesi geçmiş — `S-{id}` geri dönüşü.
        veli_eski = siparis(veli, tutar="80.00", od=-40, dd=-12)
        c.execute(text(
            "INSERT INTO late_fee_policies(company_id,customer_id,annual_rate,day_count_basis,"
            "grace_days,tax_mode,vat_rate,effective_from,active) VALUES "
            "(:c,NULL,36.5,365,0,'NO_VAT',0,:ef,:a)"),
            {"c": firma, "ef": _gun(-400), "a": True})
    return {"firma": firma, "ali": ali, "veli": veli, "ali_siparis": ali_siparis,
            "veli_eski": veli_eski}


def _giris(client, firma: int) -> dict:
    cevap = client.post("/api/auth/login", json={"username": "h107admin", "password": PAROLA})
    assert cevap.status_code == 200, cevap.text
    client.cookies.clear()
    return {"Authorization": "Bearer " + cevap.json()["access_token"], "X-Company-ID": str(firma)}


@pytest.fixture(scope="module")
def ortam(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("h107")
    with _uygulama(tmp) as (engine, client):
        k = _tohumla(engine)
        h = _giris(client, k["firma"])

        tahsilat = client.post(
            "/api/payments",
            headers={**h, "Idempotency-Key": f"h107-{uuid4().hex}"},
            json={"entity_type": "customer", "entity_id": k["ali"], "amount": "400.00",
                  "payment_date": _gun(-3), "payment_method": "cash"},
        )
        assert tahsilat.status_code in (200, 201), tahsilat.text

        taslak = client.post(
            "/api/finance/late-fees/charges",
            headers={**h, "Idempotency-Key": f"h107-vf-{uuid4().hex}"},
            json={"order_id": k["ali_siparis"], "period_start": _gun(-29),
                  "period_end": _gun(-20)},
        )
        assert taslak.status_code == 200, taslak.text
        onay = client.post(
            f"/api/finance/late-fees/charges/{taslak.json()['id']}/post",
            headers={**h, "Idempotency-Key": f"h107-vfo-{uuid4().hex}"},
        )
        assert onay.status_code == 200, onay.text
        yield {"engine": engine, "client": client, "h": h, **k,
               "vade_farki": onay.json()}


def _pano(ortam) -> dict:
    cevap = ortam["client"].get("/api/dashboard", headers=ortam["h"])
    assert cevap.status_code == 200, cevap.text
    return cevap.json()


def test_on_kosul_paid_amount_BAGSIZ_tahsilati_TASIMAZ(ortam) -> None:
    """Senaryonun anlamı: eski formül 1000 görür, defter 600 der."""
    from sqlalchemy import text

    with ortam["engine"].connect() as c:
        paid, tahsis = c.execute(text(
            "SELECT o.paid_amount,(SELECT COALESCE(SUM(amount),0) FROM payment_allocations a "
            "WHERE a.order_id=o.id AND a.company_id=o.company_id) FROM orders o WHERE o.id=:o"),
            {"o": ortam["ali_siparis"]}).one()
    assert Decimal(str(paid)) == 0
    assert Decimal(str(tahsis)).quantize(KURUS) == Decimal("400.00")


def test_pano_gecikmis_TOPLAMI_yaslandirma_raporu_ile_AYNI(ortam) -> None:
    """Kâhin: `/api/reports/receivables-aging` — aynı motor, vadesi geçmiş kovalar.

    `/api/finance/receivables` kâhin DEĞİL: yalnız harman vadeli satışları
    kapsar, vade farkını hiç görmez.
    """
    client, h = ortam["client"], ortam["h"]
    pano = _pano(ortam)

    rapor = client.get("/api/reports/receivables-aging", headers=h)
    assert rapor.status_code == 200, rapor.text
    toplamlar = rapor.json()["totals"]
    rapor_gecikmis = sum(
        (Decimal(toplamlar[k]) for k in ("days_1_30", "days_31_60", "days_61_90", "days_90_plus")),
        Decimal("0"),
    )
    vade_farki = Decimal(str(ortam["vade_farki"]["gross_amount"]))
    assert vade_farki > 0
    # 1000-400 (defterde FIFO) + 80 (numarasız) + vade farkı; bugün vadeli 250 YOK.
    assert rapor_gecikmis == (Decimal("680.00") + vade_farki).quantize(KURUS)
    assert Decimal(str(pano["overdue_total"])).quantize(KURUS) == rapor_gecikmis


def test_pano_gecikmis_ADET_ve_LISTE_motor_belgeleri(ortam) -> None:
    pano = _pano(ortam)
    liste = pano["overdue_receivables"]
    # Ali satışı + Ali vade farkı + Veli eski satış. Bugün vadeli satış YOK.
    assert pano["overdue_count"] == 3 == len(liste)
    assert [s["due_date"] for s in liste] == sorted(s["due_date"] for s in liste)
    assert all(set(s) == {"id", "customer_id", "customer_name", "due_date", "document_no",
                          "remaining", "days_overdue"} for s in liste), liste

    satirlar = {s["document_no"]: s for s in liste}
    ali = satirlar["H107-ALI"]
    assert ali["id"] == ortam["ali_siparis"]
    assert ali["customer_name"] == "H107 Ali"
    assert ali["remaining"] == 600.0
    assert ali["days_overdue"] == 30

    veli = satirlar[f"S-{ortam['veli_eski']}"]
    assert veli["remaining"] == 80.0 and veli["days_overdue"] == 12

    vf = [s for s in liste if s["document_no"].startswith("VF-")]
    assert len(vf) == 1 and vf[0]["customer_id"] == ortam["ali"]
    assert vf[0]["remaining"] == float(ortam["vade_farki"]["gross_amount"])
