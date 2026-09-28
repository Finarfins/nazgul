"""F10-9a — müşteri risk skoru: saf modül + `GET /api/customers/{id}/risk-score`.

Üç katman ölçülür:

1. **Tablo testi (saf, DB YOK)** — `app/risk_skoru.py`: her ceza satırı,
   yaşlandırma kovası sınırları 30/31, 60/61, 90/91, tavanlar, `max(0, …)`,
   harf eşikleri, `yetersiz_veri`, `SABITLER` sözlüğünün birebir çivisi.
2. **SQL kimliği (DB YOK)** — `customer_id=None` iken alacak motorunun
   ürettiği SQL, F10-9a ÖNCESİ tabanda (develop `9a6d7e3`) kaydedilen özetle
   bayt bayt aynıdır; iki lehçe × iki bayrak durumu (K5=a).
3. **Uç (SQLite, taze firma)** — `satis`/`admin` 200, `depo`/`rapor` 403,
   komşu firma 404; skorun "vadesi geçmiş"/"açık" toplamları aynı müşterinin
   `receivables-aging` satırına kuruşu kuruşuna eşit; KVKK negatifi (skor
   ekstreye, `yaslandirma_verisi`ne ve WhatsApp'a SIZMAZ).

--- MUTASYON TABLOSU (ölçüldü, her biri adıyla KIRMIZI) ---------------------

  * `calculate_net_receivables`e `customer_id` geçirilmez (süzgeç düşer)
      -> test_skor_YASLANDIRMA_satiri_ile_KURUSU_KURUSUNA_ayni
  * `auth.py`deki `/risk-score` sonek kuralı silinir
      -> test_uc_izinleri (depo 200 alır)
  * `SABITLER["harf_esikleri"]` A eşiği 85 -> 84
      -> test_SABITLER_tablosu_CIVILI
  * `yaslandirma_verisi` müşteri satırına `puan` eklenir
      -> test_KVKK_yaslandirma_satiri_SKOR_TASIMAZ
"""
from __future__ import annotations

import hashlib
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import uuid4

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PAROLA = "RiskSkoru!F109a2026x"
ANAHTAR = "f10-9a-risk-skoru-anahtar-f10-9a-risk-skoru-anahtar-f10-9a"
SKOR_ALANLARI = {"puan", "harf", "yetersiz_veri", "cezalar", "hesaplandi"}


# ============================================================ 1. SAF TABLO ===


def _sinyal(**degisen):
    """Nötr sinyal: hiçbir ceza üretmez, `yetersiz_veri` False."""
    from app.risk_skoru import RiskSinyalleri

    taban = RiskSinyalleri(
        as_of=date(2026, 9, 28),
        acik_toplam=Decimal("0"),
        vadesi_gecmis_toplam=Decimal("0"),
        en_eski_gecikme_gun=0,
        en_eski_belge_no=None,
        en_eski_vade=None,
        kapanan_belge_12ay=3,
        gec_kapanan_belge_12ay=0,
        karsiliksiz_24ay=0,
        vade_farki_12ay=0,
        risk_limit=Decimal("0"),
        bakiye=Decimal("0"),
    )
    return replace(taban, **degisen)


def _cezalar(sinyal) -> dict[str, int]:
    from app.risk_skoru import skor_hesapla

    return {c.kod: c.puan for c in skor_hesapla(sinyal).cezalar}


def test_SABITLER_tablosu_CIVILI() -> None:
    """K1: eşik ve ağırlıklar TEK sözlükte; bir değeri oynatmak burayı kırar."""
    from app.risk_skoru import SABITLER

    assert SABITLER == {
        "taban_puan": 100,
        "gecikme_kovalari": ((30, 5), (60, 15), (90, 25), (None, 35)),
        "vadesi_oran_carpan": 20,
        "vadesi_oran_tavan": 20,
        "gec_kapanis_esik_gun": 7,
        "gec_kapanis_carpan": 15,
        "gec_kapanis_tavan": 15,
        "karsiliksiz_birim": 15,
        "karsiliksiz_tavan": 30,
        "vade_farki_birim": 3,
        "vade_farki_tavan": 10,
        "limit_yaklasma_oran": Decimal("0.80"),
        "limit_yaklasma_ceza": 5,
        "limit_asim_ceza": 15,
        "harf_esikleri": ((85, "A"), (70, "B"), (50, "C"), (30, "D"), (0, "E")),
        "yetersiz_veri_min_kapanan": 3,
        "pencere_12_ay_gun": 365,
        "pencere_24_ay_gun": 730,
    }


@pytest.mark.parametrize(
    "gun, ceza",
    [(-5, 0), (0, 0), (1, 5), (30, 5), (31, 15), (60, 15), (61, 25), (90, 25),
     (91, 35), (4000, 35)],
)
def test_gecikme_basamaklari_YASLANDIRMA_kovalariyla_ayni(gun: int, ceza: int) -> None:
    from app.risk_skoru import gecikme_cezasi
    from app.routers.reports import _aging_bucket

    assert gecikme_cezasi(gun) == ceza
    assert _cezalar(_sinyal(en_eski_gecikme_gun=gun)).get("gecikme", 0) == ceza
    # Sınır kovası raporla aynı: aynı gün, aynı kova, aynı basamak.
    kova = _aging_bucket(date(2026, 1, 1), date(2026, 1, 1) + timedelta(days=gun))
    assert {"not_due": 0, "days_1_30": 5, "days_31_60": 15, "days_61_90": 25,
            "days_90_plus": 35}[kova] == ceza


@pytest.mark.parametrize(
    "vadesi, acik, ceza",
    [("0", "0", 0), ("50", "0", 0), ("0", "100", 0), ("50", "100", 10),
     # 0.025 × 20 = 0.5 -> ROUND_HALF_UP 1 (bankacı yuvarlaması 0 derdi)
     ("2.5", "100", 1), ("2.4", "100", 0), ("100", "100", 20),
     # oran [0,1]e kırpılır: eksi kalanlı belge toplamı aşsa da tavan 20
     ("120", "100", 20), ("-10", "100", 0)],
)
def test_vadesi_oran(vadesi: str, acik: str, ceza: int) -> None:
    sinyal = _sinyal(vadesi_gecmis_toplam=Decimal(vadesi), acik_toplam=Decimal(acik))
    assert _cezalar(sinyal).get("vadesi_oran", 0) == ceza


@pytest.mark.parametrize(
    "gec, kapanan, ceza",
    [(0, 0, 0), (0, 5, 0), (1, 2, 8), (1, 3, 5), (1, 30, 1), (1, 31, 0), (3, 3, 15)],
)
def test_gec_kapanis(gec: int, kapanan: int, ceza: int) -> None:
    sinyal = _sinyal(gec_kapanan_belge_12ay=gec, kapanan_belge_12ay=kapanan)
    assert _cezalar(sinyal).get("gec_kapanis", 0) == ceza


@pytest.mark.parametrize("adet, ceza", [(0, 0), (1, 15), (2, 30), (3, 30), (9, 30)])
def test_karsiliksiz_TAVAN_30(adet: int, ceza: int) -> None:
    assert _cezalar(_sinyal(karsiliksiz_24ay=adet)).get("karsiliksiz", 0) == ceza


@pytest.mark.parametrize("adet, ceza", [(0, 0), (1, 3), (3, 9), (4, 10), (20, 10)])
def test_vade_farki_TAVAN_10(adet: int, ceza: int) -> None:
    assert _cezalar(_sinyal(vade_farki_12ay=adet)).get("vade_farki", 0) == ceza


@pytest.mark.parametrize(
    "bakiye, limit, ceza",
    [("5000", "0", 0),          # limitsiz müşteri: kapı çalışmaz, ceza yok
     ("799.99", "1000", 0), ("800", "1000", 5), ("1000", "1000", 5),
     ("1000.01", "1000", 15), ("-50", "1000", 0)],
)
def test_limit(bakiye: str, limit: str, ceza: int) -> None:
    sinyal = _sinyal(bakiye=Decimal(bakiye), risk_limit=Decimal(limit))
    assert _cezalar(sinyal).get("limit", 0) == ceza


@pytest.mark.parametrize(
    "puan, harf",
    [(100, "A"), (85, "A"), (84, "B"), (70, "B"), (69, "C"), (50, "C"), (49, "D"),
     (30, "D"), (29, "E"), (0, "E")],
)
def test_harf_esikleri(puan: int, harf: str) -> None:
    from app.risk_skoru import harf_bul

    assert harf_bul(puan) == harf


def test_puan_SIFIRIN_altina_inmez_ve_cezalar_yalniz_SIFIRDAN_buyuk() -> None:
    from app.risk_skoru import skor_hesapla

    hepsi = _sinyal(
        en_eski_gecikme_gun=120, en_eski_belge_no="S-9", en_eski_vade=date(2026, 5, 1),
        vadesi_gecmis_toplam=Decimal("100"), acik_toplam=Decimal("100"),
        gec_kapanan_belge_12ay=3, kapanan_belge_12ay=3, karsiliksiz_24ay=5,
        vade_farki_12ay=9, bakiye=Decimal("2000"), risk_limit=Decimal("1000"),
    )
    skor = skor_hesapla(hepsi)
    assert {c.kod: c.puan for c in skor.cezalar} == {
        "gecikme": 35, "vadesi_oran": 20, "gec_kapanis": 15, "karsiliksiz": 30,
        "vade_farki": 10, "limit": 15}                 # Σ 125
    assert (skor.puan, skor.harf) == (0, "E")
    assert skor.cezalar[0].kanit == "S-9 · vade 2026-05-01 · 120 gün"

    temiz = skor_hesapla(_sinyal())
    assert (temiz.puan, temiz.harf, temiz.cezalar) == (100, "A", ())


@pytest.mark.parametrize("kapanan, yetersiz", [(0, True), (2, True), (3, False), (10, False)])
def test_yetersiz_veri_ve_harf_YINE_hesaplanir(kapanan: int, yetersiz: bool) -> None:
    from app.risk_skoru import skor_hesapla

    skor = skor_hesapla(_sinyal(kapanan_belge_12ay=kapanan, en_eski_gecikme_gun=45))
    assert skor.yetersiz_veri is yetersiz
    assert (skor.puan, skor.harf) == (85, "A")


def test_saf_modul_SQL_ve_Session_GORMEZ() -> None:
    import ast

    agac = ast.parse((BACKEND / "app" / "risk_skoru.py").read_text(encoding="utf-8"))
    ithaller: set[str] = set()
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.ImportFrom):
            ithaller.add("." * dugum.level + (dugum.module or ""))
        elif isinstance(dugum, ast.Import):
            ithaller.update(a.name for a in dugum.names)
    assert not {m for m in ithaller if "sqlalchemy" in m or m.startswith(".")}, ithaller


# ======================================================== 2. SQL KİMLİĞİ ===

# develop `9a6d7e3` (F10-9a ÖNCESİ) üzerinde kaydedildi: aynı kaydedici,
# `calculate_net_receivables(db, 1, date(2026, 9, 28))`, 7 ifade.
TABAN_SQL_OZETLERI = {
    ("sqlite", False): "a5f68f136c80a7281218e974a420650821d922d0662cf3fee4a64517424c2305",
    ("sqlite", True): "13de2abb8c73796d2eb5d2ec1b97981c6085b5512f97db9a792bf3f431b15871",
    ("postgresql", False): "d6b08afd5775bc65fac81d4fc088516d402e2a7f20d8e37787eb400add992314",
    ("postgresql", True): "c56e767e827cb7b61d77faa50de1f012efa253651ad16eb9527fcc6b61392995",
}


class _BosSonuc:
    def mappings(self):
        return self

    def all(self):
        return []

    def __iter__(self):
        return iter(())


class _Kaydedici:
    """`Session` yerine geçer: SQL metnini kaydeder, boş sonuç döner."""

    def __init__(self, lehce: str) -> None:
        self.sqller: list[str] = []
        self.parametreler: list[dict] = []
        self._bag = type("B", (), {"dialect": type("D", (), {"name": lehce})})

    def get_bind(self):
        return self._bag

    def execute(self, ifade, parametre=None):
        self.sqller.append(str(ifade))
        self.parametreler.append(dict(parametre or {}))
        return _BosSonuc()


@pytest.mark.parametrize("lehce", ["sqlite", "postgresql"])
@pytest.mark.parametrize("bayrak", [False, True])
def test_customer_id_None_iken_SQL_TABANLA_bayt_bayt_ayni(monkeypatch, lehce, bayrak) -> None:
    from app import receivables_engine
    from app.config import settings

    monkeypatch.setattr(settings, "payment_allocation_engine_enabled", bayrak)
    kayit = _Kaydedici(lehce)
    receivables_engine.calculate_net_receivables(kayit, 1, date(2026, 9, 28))
    ozet = hashlib.sha256("\n--\n".join(kayit.sqller).encode()).hexdigest()
    assert (len(kayit.sqller), ozet) == (7, TABAN_SQL_OZETLERI[(lehce, bayrak)])
    assert not any("customer_id" in p for p in kayit.parametreler)


@pytest.mark.parametrize("bayrak", [False, True])
def test_customer_id_verilince_HER_musteri_anahtarli_sorgu_suzulur(monkeypatch, bayrak) -> None:
    """Satış, ödeme, iade ve ücret belgesi sorguları müşteriye iner; yalnız
    sipariş/ücret id'siyle anahtarlanan defter sorguları firma genelinde kalır
    (sonuç sözlüğünde yalnız kendi belgeleri aranır)."""
    from app import receivables_engine
    from app.config import settings

    monkeypatch.setattr(settings, "payment_allocation_engine_enabled", bayrak)
    kayit = _Kaydedici("sqlite")
    receivables_engine.calculate_net_receivables(kayit, 1, date(2026, 9, 28), customer_id=42)
    suzulen = [s for s, p in zip(kayit.sqller, kayit.parametreler) if p.get("customer_id") == 42]
    for sql in suzulen:
        assert "customer_id=:customer_id" in sql or "entity_id=:customer_id" in sql, sql
    suzulmeyen = [s for s, p in zip(kayit.sqller, kayit.parametreler) if "customer_id" not in p]
    assert all(
        "payment_allocations" in s or "receivable_legacy_residuals" in s for s in suzulmeyen
    ), suzulmeyen
    # bayrak kapalı: satış + 4 hareket + ücret; açık: satış + 2 iade + ücret
    assert len(suzulen) == (4 if bayrak else 6)


# ======================================================== 3. UÇ (SQLite) ===


def _app_anahtarlari() -> set[str]:
    return {k for k in sys.modules if k == "app" or k.startswith("app.")}


@contextmanager
def _uygulama(tmp_path: Path):
    onceki = {k: sys.modules[k] for k in _app_anahtarlari()}
    mp = pytest.MonkeyPatch()
    for ad in list(_app_anahtarlari()):
        del sys.modules[ad]
    mp.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'f109a.db').as_posix()}")
    mp.setenv("SUNGUR_DATA_DIR", str(tmp_path))
    mp.setenv("SECRET_KEY", ANAHTAR)
    mp.setenv("BOOTSTRAP_ADMIN_PASSWORD", PAROLA)
    mp.setenv("AUTO_MIGRATE", "true")
    mp.setenv("SUNGUR_PLATFORM_OPERATORS", "")
    mp.setenv("PAYMENT_ALLOCATION_ENGINE_ENABLED", "false")
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
    """Taze iki firma. A'da üç müşteri, B'de bir komşu müşteri.

    RİSK (limit 1000): o1 1000 (vade −100, açık) · o2 200 (vade +10, açık)
    · o3 400 (vade −180, −170'te ödendi: 10 gün GEÇ) · o4 300 (vade −150,
    −152'de ödendi) · o5 300 (vade −60, −60'ta ödendi) · karşılıksız çek
    belgesi 150 (vade −40) · o1 üzerine vade farkı (dönem −99..−90).
    YAKIN (aynı firma): o6 500 (vade −40, açık) — süzgeç düşerse RİSK'e sızar.
    TEMİZ: belgesiz, limitsiz.
    """
    from sqlalchemy import text

    from app.auth import hash_password

    simdi = datetime.now(timezone.utc)
    with engine.begin() as c:
        c.execute(text("UPDATE app_users SET must_change_password=0"))

        def firma(ad: str) -> int:
            return int(c.execute(text(
                "INSERT INTO companies(name,is_active,created_at) VALUES (:n,1,:t) RETURNING id"),
                {"n": ad, "t": simdi}).scalar_one())

        a, b = firma("F109a Alfa"), firma("F109a Beta")
        for rol in ("admin", "satis", "depo", "rapor"):
            uid = int(c.execute(text(
                "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,"
                "role,is_active,created_at,must_change_password) VALUES "
                "(:u,:e,1,:u,:h,:r,1,:t,0) RETURNING id"),
                {"u": f"f109a{rol}", "e": f"f109a{rol}@risk.example",
                 "h": hash_password(PAROLA), "r": rol, "t": simdi}).scalar_one())
            c.execute(text(
                "INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
                "VALUES (:u,:c,1,:t)"), {"u": uid, "c": a, "t": simdi})

        def musteri(cid: int, ad: str, limit: str = "0") -> int:
            return int(c.execute(text(
                "INSERT INTO customers(company_id,name,opening_balance,risk_limit,phone,tax_number)"
                " VALUES (:c,:n,0,:l,'05550000000','1111111111') RETURNING id"),
                {"c": cid, "n": ad, "l": limit}).scalar_one())

        def siparis(mid: int, tutar: str, od: int, dd: int, no: str) -> int:
            return int(c.execute(text(
                "INSERT INTO orders(customer_id,order_date,due_date,final_total,status,paid_amount,"
                "payment_method,payment_term,document_no,company_id) VALUES "
                "(:m,:od,:dd,:t,'completed',0,'credit','HARMAN_VADELI',:no,:c) RETURNING id"),
                {"m": mid, "od": _gun(od), "dd": _gun(dd), "t": tutar, "no": no,
                 "c": a}).scalar_one())

        risk = musteri(a, "F109a Risk Gizli Ad", "1000")
        yakin = musteri(a, "F109a Yakin")
        temiz = musteri(a, "F109a Temiz")
        komsu = musteri(b, "F109a Komsu Firma")
        o1 = siparis(risk, "1000.00", -120, -100, "R-O1")
        siparis(risk, "200.00", -20, 10, "R-O2")
        o3 = siparis(risk, "400.00", -200, -180, "R-O3")
        o4 = siparis(risk, "300.00", -160, -150, "R-O4")
        o5 = siparis(risk, "300.00", -80, -60, "R-O5")
        siparis(yakin, "500.00", -50, -40, "Y-O6")

        cek = int(c.execute(text(
            "INSERT INTO cek_senetler(company_id,tur,yon,portfoy_durumu,customer_id,tutar,vade,"
            "seri_no,created_at) VALUES (:c,'cek','alinan','karsiliksiz',:m,150,:v,'F109A-1',:t)"
            " RETURNING id"), {"c": a, "m": risk, "v": _gun(-40), "t": simdi}).scalar_one())
        c.execute(text(
            "INSERT INTO receivable_charge_documents(company_id,cek_senet_id,customer_id,"
            "charge_type,period_start,period_end,due_date_snapshot,calculation_snapshot,"
            "gross_amount,status,calculation_fingerprint,revision_no,currency,exchange_rate,"
            "posted_at) VALUES (:c,:k,:m,'bounced_check',:pe,:pe,:dd,'{}',150,'posted','f109a',"
            "1,'TRY',1,:t)"),
            {"c": a, "k": cek, "m": risk, "pe": _gun(-30), "dd": _gun(-40), "t": simdi})
        c.execute(text(
            "INSERT INTO late_fee_policies(company_id,customer_id,annual_rate,day_count_basis,"
            "grace_days,tax_mode,vat_rate,effective_from,active) VALUES "
            "(:c,NULL,36.5,365,0,'NO_VAT',0,:ef,:a)"),
            {"c": a, "ef": _gun(-400), "a": True})
    return {"a": a, "b": b, "risk": risk, "yakin": yakin, "temiz": temiz, "komsu": komsu,
            "o1": o1, "o3": o3, "o4": o4, "o5": o5}


def _giris(client, kullanici: str, firma: int) -> dict:
    cevap = client.post("/api/auth/login", json={"username": kullanici, "password": PAROLA})
    assert cevap.status_code == 200, cevap.text
    client.cookies.clear()
    return {"Authorization": "Bearer " + cevap.json()["access_token"], "X-Company-ID": str(firma)}


@pytest.fixture(scope="module")
def ortam(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("f109a")
    with _uygulama(tmp) as (engine, client):
        k = _tohumla(engine)
        h = {rol: _giris(client, f"f109a{rol}", k["a"])
             for rol in ("admin", "satis", "depo", "rapor")}
        # Bayrak KAPALI (eski düzen): siparişe bağlı ham ödeme. API bu şekli
        # yalnız tahsis motoru açıkken yazar; motor-açık yol PG ikizinde.
        from sqlalchemy import text

        with engine.begin() as c:
            for siparis_id, tutar, gun in ((k["o3"], "400.00", -170),
                                           (k["o4"], "300.00", -152),
                                           (k["o5"], "300.00", -60)):
                c.execute(text(
                    "INSERT INTO payments(entity_type,entity_id,amount,payment_date,company_id,"
                    "payment_method,reference_type,reference_id) VALUES "
                    "('customer',:m,:t,:d,:c,'cash','order',:o)"),
                    {"m": k["risk"], "t": tutar, "d": _gun(gun), "c": k["a"], "o": siparis_id})
        taslak = client.post(
            "/api/finance/late-fees/charges",
            headers={**h["admin"], "Idempotency-Key": f"f109a-vf-{uuid4().hex}"},
            json={"order_id": k["o1"], "period_start": _gun(-99), "period_end": _gun(-90)},
        )
        assert taslak.status_code == 200, taslak.text
        onay = client.post(
            f"/api/finance/late-fees/charges/{taslak.json()['id']}/post",
            headers={**h["admin"], "Idempotency-Key": f"f109a-vfo-{uuid4().hex}"},
        )
        assert onay.status_code == 200, onay.text
        yield {"engine": engine, "client": client, "h": h, **k,
               "vade_farki": Decimal(str(onay.json()["gross_amount"]))}


def _skor(ortam, customer_id: int, rol: str = "satis", **sorgu):
    return ortam["client"].get(
        f"/api/customers/{customer_id}/risk-score", headers=ortam["h"][rol], params=sorgu)


def test_uc_izinleri(ortam) -> None:
    """K7: `sales` — admin/satis 200; depo/rapor 403 (genel `read` düşüşü DEĞİL)."""
    durum = {rol: _skor(ortam, ortam["risk"], rol).status_code
             for rol in ("admin", "satis", "depo", "rapor")}
    assert durum == {"admin": 200, "satis": 200, "depo": 403, "rapor": 403}


def test_komsu_firma_ve_olmayan_musteri_404(ortam) -> None:
    assert _skor(ortam, ortam["komsu"]).status_code == 404
    assert _skor(ortam, 987654).status_code == 404
    # 403 404'ten ÖNCE gelir: depo varlığı bile öğrenemez.
    assert _skor(ortam, ortam["komsu"], "depo").status_code == 403


def test_skor_BUTUN_cezalar_uctan_uca(ortam) -> None:
    cevap = _skor(ortam, ortam["risk"])
    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    assert set(govde) == SKOR_ALANLARI
    vf = ortam["vade_farki"]
    assert vf > 0
    acik = Decimal("1000") + Decimal("200") + Decimal("150") + vf
    vadesi = Decimal("1000") + Decimal("150") + vf
    vadesi_oran = int((vadesi / acik * 20).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    beklenen = {
        "gecikme": 35,          # o1 vadesi 100 gün geçti -> 90+
        "vadesi_oran": vadesi_oran,
        "gec_kapanis": 5,       # 3 kapanan (o3/o4/o5), 1'i >7 gün geç: round(15/3)
        "karsiliksiz": 15,
        "vade_farki": 3,
        # bakiye = siparis 2200-1000 odeme + H104 (#196) sonrasi kapinin saydigi
        # borc belgeleri (karsiliksiz 150 + vade farki vf) > limit 1000
        "limit": 15,
    }
    assert {c["kod"]: c["puan"] for c in govde["cezalar"]} == beklenen
    assert govde["puan"] == max(0, 100 - sum(beklenen.values()))
    assert govde["harf"] == "E"
    assert govde["yetersiz_veri"] is False
    assert govde["hesaplandi"] == _gun(0)
    gecikme = next(c for c in govde["cezalar"] if c["kod"] == "gecikme")
    assert gecikme["kanit"] == f"R-O1 · vade {_gun(-100)} · 100 gün"
    limit = next(c for c in govde["cezalar"] if c["kod"] == "limit")
    # S5 kapinin KENDI bakiyesini okur (`_credit_exposure`); H104 kapiya borc
    # belgelerini soktu, beklenen deger ondan TURETILIR, sabit yazilmaz.
    bakiye = Decimal("1200") + Decimal("150") + vf
    assert limit["kanit"] == f"bakiye {bakiye:.2f} / limit 1000.00"


def test_temiz_musteri_A_ama_YETERSIZ_VERI(ortam) -> None:
    govde = _skor(ortam, ortam["temiz"]).json()
    assert govde == {"puan": 100, "harf": "A", "yetersiz_veri": True, "cezalar": [],
                     "hesaplandi": _gun(0)}


def test_as_of_gecmise_alinabilir(ortam) -> None:
    """`as_of` = −95: vade farkı dönemi (−90) henüz kapanmadı, o1 5 gün gecikmiş."""
    govde = _skor(ortam, ortam["risk"], as_of=_gun(-95)).json()
    cezalar = {c["kod"]: c["puan"] for c in govde["cezalar"]}
    assert govde["hesaplandi"] == _gun(-95)
    assert cezalar["gecikme"] == 5 and "vade_farki" not in cezalar
    assert "karsiliksiz" not in cezalar


def _yaslandirma_satiri(ortam, customer_id: int, as_of: str | None = None) -> dict:
    params = {"as_of": as_of} if as_of else {}
    rapor = ortam["client"].get("/api/reports/receivables-aging",
                                headers=ortam["h"]["admin"], params=params)
    assert rapor.status_code == 200, rapor.text
    return next(s for s in rapor.json()["customers"] if s["customer_id"] == customer_id)


@pytest.mark.parametrize("fark", [0, -45])
def test_skor_YASLANDIRMA_satiri_ile_KURUSU_KURUSUNA_ayni(ortam, fark: int) -> None:
    """K5=a: süzgeçli motor = raporun o müşteri satırı. YAKIN müşterinin açık
    500'ü süzgeç düşerse RİSK'e sızar ve eşitlik KIRMIZI yanar."""
    from app.business_time import business_today
    from app.db import SessionLocal
    from app.risk_skoru_okuma import sinyalleri_topla

    as_of = business_today() + timedelta(days=fark)
    satir = _yaslandirma_satiri(ortam, ortam["risk"], as_of.isoformat())
    gecikmis = sum((Decimal(satir[k]) for k in
                    ("days_1_30", "days_31_60", "days_61_90", "days_90_plus")), Decimal("0"))
    with SessionLocal() as db:
        sinyal = sinyalleri_topla(db, ortam["a"], ortam["risk"], as_of)
    assert sinyal is not None
    assert sinyal.vadesi_gecmis_toplam == gecikmis
    assert sinyal.acik_toplam == Decimal(satir["total"])
    kanit = next(c["kanit"] for c in _skor(ortam, ortam["risk"], as_of=as_of.isoformat())
                 .json()["cezalar"] if c["kod"] == "vadesi_oran")
    assert kanit == f"vadesi geçmiş {gecikmis:.2f} / açık {Decimal(satir['total']):.2f}"


def test_KVKK_cevap_cari_kisisel_alani_TASIMAZ(ortam) -> None:
    from app.routers.risk_skoru import RiskCezasiCevabi, RiskSkoruCevabi

    assert set(RiskSkoruCevabi.model_fields) == SKOR_ALANLARI
    assert set(RiskCezasiCevabi.model_fields) == {"kod", "puan", "aciklama", "kanit"}
    assert RiskSkoruCevabi.model_config.get("extra") == "forbid"
    govde = _skor(ortam, ortam["risk"]).text
    for pii in ("F109a Risk Gizli Ad", "05550000000", "1111111111"):
        assert pii not in govde


def test_KVKK_yaslandirma_satiri_SKOR_TASIMAZ(ortam) -> None:
    """K8-2: WhatsApp `alacak_yaslandirma` aracı `yaslandirma_verisi`ni DOĞRUDAN
    çağırır. Fonksiyon ölçülür, HTTP ucu DEĞİL: ucun `response_model`i fazla
    anahtarı süzer ve sızıntıyı gizlerdi (mutasyonla ölçüldü)."""
    from app.db import SessionLocal
    from app.routers.reports import yaslandirma_verisi

    with SessionLocal() as db:
        veri = yaslandirma_verisi(db, ortam["a"])
    satir = next(s for s in veri["customers"] if s["customer_id"] == ortam["risk"])
    assert set(satir) == {
        "customer_id", "customer_name", "not_due", "days_1_30", "days_31_60",
        "days_61_90", "days_90_plus", "total", "portfolio_checks", "net_risk", "documents"}
    assert not SKOR_ALANLARI & _anahtarlar(veri)


def _anahtarlar(nesne) -> set[str]:
    if isinstance(nesne, dict):
        return set(nesne) | {a for v in nesne.values() for a in _anahtarlar(v)}
    if isinstance(nesne, list):
        return {a for v in nesne for a in _anahtarlar(v)}
    return set()


def test_KVKK_ekstre_SKOR_TASIMAZ(ortam) -> None:
    cevap = ortam["client"].get(f"/api/customers/{ortam['risk']}/statement",
                                headers=ortam["h"]["satis"])
    assert cevap.status_code == 200, cevap.text
    assert not SKOR_ALANLARI & _anahtarlar(cevap.json())


def test_KVKK_musteri_yuzlu_moduller_skoru_ITHAL_ETMEZ() -> None:
    """Ekstre, ekstre PDF'i, yaşlandırma ve WhatsApp skoru içe aktarmaz."""
    app_dir = BACKEND / "app"
    dosyalar = [app_dir / "statement.py", app_dir / "routers" / "outputs.py",
                app_dir / "routers" / "reports.py", app_dir / "routers" / "customers.py",
                app_dir / "entity_detail.py", *sorted((app_dir / "whatsapp").rglob("*.py"))]
    sizanlar = [str(d.relative_to(app_dir)) for d in dosyalar
                if "risk_skoru" in d.read_text(encoding="utf-8")]
    assert sizanlar == []
