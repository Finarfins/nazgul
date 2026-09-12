"""CS2 — çek/senet ↔ cari: köprü, ekstre, tahsis, karşılıksız dekont, ciro anahtarı.

Konu: ``alembic/versions/20260914_0086_cek_senet_cari.py``,
``app/cek_senet_cari.py``, ``routers/finance.py`` (köprü),
``routers/cek_senetler.py`` (ters köprü + geçiş yan etkileri),
``statement.py``, ``routers/dashboard.py``, ``routers/reports.py``.

Karar 1 (Seçenek A): çek ALINDIĞINDA cari düşer; karşılıksız/iade'de tahsis
KORUNUR ve ``bounced_check`` borç belgesi cariyi eski bakiyesine döndürür.

--- MUTASYON TABLOSU --------------------------------------------------------

  * ``_cek_bilgisi``nin 422'sini düşürmek   -> KÖPRÜ DOĞRULAMASI KIRMIZI
  * ``_cek_bagla``yı ``on_created``dan çıkarmak -> KÖPRÜ (motor) KIRMIZI
  * ``borc_belgesi_gerekir_mi``den ``payment_id`` koşulunu düşürmek
                        -> ÖDEMESİZ EVRAK BELGE AÇMAZ KIRMIZI (çift borç)
  * ``charge_document_id`` koşulunu düşürmek -> KARŞILIKSIZ->İADE İKİNCİ
                        BELGE KIRMIZI (ve PG'de tekil indeks)
  * ekstreden ``_cek_dekont_borcu``nu düşürmek -> KAPANIŞ = YÜRÜYEN BAKİYE KIRMIZI
  * ``ciro_anahtari_acik`` kapısını kaldırmak -> ANAHTAR KAPALI KIRMIZI
  * ``charge_due_date_sql``den ``bounced_check``i çıkarmak -> YAŞLANDIRMA VADESİ KIRMIZI
  * ``_cek_evrak(kilit=True)``yi kaldırmak -> (yalnız PG ikizinde ölçülür)
"""
from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260914_0086_cek_senet_cari.py"
PAROLA = "CekSenetCari!2026x"
ANAHTAR = "cs2-cek-senet-cari-anahtar-cs2-cek-senet-cari-anahtar-cs2"
SIPARIS = Decimal("5000.00")
CEK = Decimal("2000.00")


def _goc_modulu():
    spec = importlib.util.spec_from_file_location("goc_0086", GOC)
    modul = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modul)
    return modul


def _app_anahtarlari() -> set[str]:
    return {k for k in sys.modules if k == "app" or k.startswith("app.")}


@contextmanager
def _uygulama(tmp_path: Path, *, motor: bool):
    onceki = {k: sys.modules[k] for k in _app_anahtarlari()}
    mp = pytest.MonkeyPatch()
    for ad in list(_app_anahtarlari()):
        del sys.modules[ad]
    mp.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'cs2.db').as_posix()}")
    mp.setenv("SUNGUR_DATA_DIR", str(tmp_path))
    mp.setenv("SECRET_KEY", ANAHTAR)
    mp.setenv("BOOTSTRAP_ADMIN_PASSWORD", PAROLA)
    mp.setenv("AUTO_MIGRATE", "true")
    mp.setenv("SUNGUR_PLATFORM_OPERATORS", "")
    mp.setenv("PAYMENT_ALLOCATION_ENGINE_ENABLED", "true" if motor else "false")
    mp.syspath_prepend(str(BACKEND))
    try:
        from fastapi.testclient import TestClient

        import app.main as main
        from app.db import engine

        with TestClient(main.app, raise_server_exceptions=False) as client:
            yield main, engine, client
    finally:
        mp.undo()
        for ad in list(_app_anahtarlari()):
            del sys.modules[ad]
        sys.modules.update(onceki)


def _giris(client, kullanici: str, firma: int | None = None) -> dict:
    cevap = client.post("/api/auth/login", json={"username": kullanici, "password": PAROLA})
    assert cevap.status_code == 200, cevap.text
    client.cookies.clear()
    h = {"Authorization": "Bearer " + cevap.json()["access_token"]}
    if firma is not None:
        h["X-Company-ID"] = str(firma)
    return h


def _gun(fark: int) -> str:
    from app.business_time import business_today

    return (business_today() + timedelta(days=fark)).isoformat()


def _tohumla(engine) -> dict:
    from sqlalchemy import text

    from app.auth import hash_password

    simdi = datetime.now(timezone.utc)
    ph = hash_password(PAROLA)
    with engine.begin() as c:
        c.execute(text("UPDATE app_users SET must_change_password=0"))
        a = int(c.execute(text("SELECT MIN(id) FROM companies")).scalar_one())
        b = int(c.execute(text(
            "INSERT INTO companies(name,is_active,created_at) VALUES ('Bravo Cari',1,:t) RETURNING id"),
            {"t": simdi}).scalar_one())

        def kullanici(ad: str, rol: str, firma: int) -> None:
            uid = int(c.execute(text(
                "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,role,"
                "is_active,created_at,must_change_password) VALUES (:u,:e,1,:d,:h,:r,1,:t,0) RETURNING id"),
                {"u": ad, "e": f"{ad}@cari.example", "d": ad, "h": ph, "r": rol, "t": simdi}).scalar_one())
            c.execute(text(
                "INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
                "VALUES (:u,:c,1,:t)"), {"u": uid, "c": firma, "t": simdi})

        for ad, rol in (("muhasebe2", "muhasebe"), ("depo2", "depo"), ("rapor2", "rapor")):
            kullanici(ad, rol, a)
        kullanici("bravoadmin2", "admin", b)

        def musteri(firma: int, ad: str, siparis: Decimal = SIPARIS) -> int:
            mid = int(c.execute(text(
                "INSERT INTO customers(company_id,name,opening_balance) VALUES (:c,:n,0) RETURNING id"),
                {"c": firma, "n": ad}).scalar_one())
            # AÇIK BELGE: tahsis motoru belge kalanını AŞAN tahsilatı 409'la
            # reddeder (WA4 notu); çekle tahsilatın bir borcu kapatması gerek.
            c.execute(text(
                "INSERT INTO orders(customer_id,order_date,due_date,final_total,status,"
                "paid_amount,payment_method,company_id) VALUES "
                "(:m,:od,:dd,:t,'completed',0,'credit',:c)"),
                {"m": mid, "od": _gun(-10), "dd": _gun(20), "t": str(siparis), "c": firma})
            return mid

        def tedarikci(firma: int, ad: str) -> int:
            return int(c.execute(text(
                "INSERT INTO suppliers(company_id,name,opening_balance) VALUES (:c,:n,0) RETURNING id"),
                {"c": firma, "n": ad}).scalar_one())

        def hesap(firma: int, ad: str, tip: str) -> int:
            return int(c.execute(text(
                "INSERT INTO finance_accounts(company_id,name,account_type,currency,opening_balance,"
                "is_active,created_at) VALUES (:c,:n,:t,'TRY',0,1,:z) RETURNING id"),
                {"c": firma, "n": ad, "t": tip, "z": simdi}).scalar_one())

        return {
            "a": a, "b": b,
            "mus_tahsil": musteri(a, "Tahsil Musteri"),
            "mus_krs": musteri(a, "Karsiliksiz Musteri"),
            "mus_iade": musteri(a, "Iade Musteri"),
            "mus_ters": musteri(a, "Ters Kopru Musteri"),
            "mus_diger": musteri(a, "Diger Musteri"),
            "ted_a": tedarikci(a, "Alfa Tedarikci"),
            "ted_ciro": tedarikci(a, "Ciro Tedarikci"),
            "ted_ciro2": tedarikci(a, "Ciro Tedarikci Iki"),
            "banka_a": hesap(a, "Alfa Banka", "bank"),
            "mus_b": musteri(b, "Bravo Musteri"),
            "banka_b": hesap(b, "Bravo Banka", "bank"),
        }


@pytest.fixture(scope="module")
def ortam(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cs2")
    with _uygulama(tmp, motor=True) as (main, engine, client):
        k = _tohumla(engine)
        yield {
            "main": main, "engine": engine, "client": client, **k,
            "h_admin": _giris(client, "admin", k["a"]),
            "h_muh": _giris(client, "muhasebe2", k["a"]),
            "h_depo": _giris(client, "depo2", k["a"]),
            "h_rapor": _giris(client, "rapor2", k["a"]),
            "h_b": _giris(client, "bravoadmin2", k["b"]),
        }


def _sql(engine, sorgu: str, **p):
    from sqlalchemy import text

    with engine.connect() as c:
        return c.execute(text(sorgu), p).all()


def _odeme_govdesi(musteri: int, *, entity_type: str = "customer", tutar=CEK, seri: str,
                   yontem: str = "check", cek: dict | None = None, **fazla) -> dict:
    govde = {"entity_type": entity_type, "entity_id": musteri, "amount": str(tutar),
             "payment_date": _gun(-5), "payment_method": yontem}
    if cek is not False:
        govde["cek_senet"] = {"vade": _gun(30), "seri_no": seri, "banka_adi": "Ziraat",
                              **(cek or {})}
    govde.update(fazla)
    return govde


def _cekle_ode(ortam, musteri: int, seri: str, h=None, anahtar: str | None = None, **fazla):
    hh = dict(h or ortam["h_muh"])
    hh["Idempotency-Key"] = anahtar or f"cs2-{seri}-{uuid4().hex}"
    return ortam["client"].post("/api/payments", headers=hh, json=_odeme_govdesi(musteri, seri=seri, **fazla))


def _gec(ortam, evrak_id: int, hedef: str, h=None, **yuk):
    return ortam["client"].post(
        f"/api/cek-senetler/{evrak_id}/durum-degistir",
        headers=h or ortam["h_muh"], json={"hedef": hedef, **yuk})


def _ekstre(ortam, musteri: int, h=None, tur: str = "customers") -> dict:
    cevap = ortam["client"].get(
        f"/api/{tur}/{musteri}/statement?date_from={_gun(-30)}&date_to={_gun(0)}",
        headers=h or ortam["h_admin"])
    assert cevap.status_code == 200, cevap.text
    return cevap.json()


def _ekstre_tutarli(ekstre: dict) -> None:
    """Yürüyen bakiyenin son satırı kapanışa, kapanış açılış+borç−alacağa eşit."""
    son = Decimal(ekstre["lines"][-1]["balance"]) if ekstre["lines"] else Decimal(ekstre["opening_balance"])
    assert son == Decimal(ekstre["closing_balance"]), ekstre
    assert Decimal(ekstre["closing_balance"]) == (
        Decimal(ekstre["opening_balance"]) + Decimal(ekstre["total_debit"]) - Decimal(ekstre["total_credit"])
    )


def _tahsis_net(engine, payment_id: int) -> Decimal:
    return Decimal(str(_sql(engine, """SELECT COALESCE(SUM(CASE WHEN reversal_of_allocation_id IS NULL
        THEN amount ELSE -amount END),0) FROM payment_allocations WHERE payment_id=:p""", p=payment_id)[0][0]))


def _cari_bakiye(ortam, musteri: int) -> Decimal:
    cevap = ortam["client"].get(f"/api/customers/{musteri}", headers=ortam["h_admin"])
    assert cevap.status_code == 200, cevap.text
    return Decimal(str(cevap.json()["summary"]["current_balance"]))


# ------------------------------------------------------------ statik kapılar ---

def test_GOC_bas_ve_tur_kumesi() -> None:
    from app import cek_senet_cari

    goc = _goc_modulu()
    assert goc.revision == "20260914_0086" and goc.down_revision == "20260914_0085"
    assert goc.TURLER == ("late_fee", "service_fee", "bounced_check")
    assert goc.TURLER[-1] == cek_senet_cari.KARSILIKSIZ_CEK
    # Eski iki dal DEĞİŞMEDİ, yalnız `cek_senet_id IS NULL` eklendi.
    for dal in ("charge_type = 'late_fee'", "charge_type = 'service_fee'"):
        assert dal in goc.SEKIL_0041 and dal in goc.SEKIL_0086
    assert goc.SEKIL_0086.count("cek_senet_id IS NULL") == 2
    assert "cek_senet_id IS NOT NULL" in goc.SEKIL_0086


def test_GOC_SQLite_indeksleri_KORUNUR_ve_geri_alma(tmp_path) -> None:
    """CHECK değişimi SQLite'ta tabloyu yeniden kurar; indeksler (kısmi olanlar
    dahil, WHERE yüklemiyle) KORUNUR. up -> down -> up; ``bounced_check``
    satırı varken geri alma REDDEDİLİR."""
    from alembic import command
    from alembic.config import Config

    with _uygulama(tmp_path, motor=False) as (_main, engine, _client):
        from sqlalchemy import text

        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "alembic"))

        def dokum() -> tuple[dict[str, str], set[str]]:
            with engine.connect() as c:
                satirlar = c.execute(text(
                    "SELECT type,name,sql FROM sqlite_master "
                    "WHERE tbl_name='receivable_charge_documents'")).all()
            indeksler = {r[1]: r[2] for r in satirlar if r[0] == "index" and r[2]}
            tablo = next(r[2] for r in satirlar if r[0] == "table")
            return indeksler, {s.strip().rstrip(",") for s in tablo.splitlines()}

        bas_indeks, bas_tablo = dokum()
        assert "uq_receivable_bounced_check_active" in bas_indeks
        assert "WHERE cek_senet_id IS NOT NULL AND status='posted'" in bas_indeks[
            "uq_receivable_bounced_check_active"]
        command.downgrade(cfg, "20260914_0085")
        alt_indeks, alt_tablo = dokum()
        assert set(bas_indeks) - set(alt_indeks) == {"uq_receivable_bounced_check_active"}
        for ad, ddl in alt_indeks.items():
            assert bas_indeks[ad] == ddl, ad  # WHERE yüklemi dahil BİREBİR
        assert not any("cek_senet" in s for s in alt_tablo)
        with engine.connect() as c:
            assert "ciro_tedarikci_odemesi" not in {
                r[1] for r in c.execute(text("PRAGMA table_info(companies)"))}
        command.upgrade(cfg, "head")
        yeni_indeks, yeni_tablo = dokum()
        assert yeni_indeks == bas_indeks and yeni_tablo == bas_tablo

        # Geri alma kapısı: bir karşılıksız belge varken RuntimeError.
        with engine.begin() as c:
            cid = c.execute(text("SELECT MIN(id) FROM companies")).scalar_one()
            mus = c.execute(text("INSERT INTO customers(company_id,name) VALUES (:c,'G') RETURNING id"),
                            {"c": cid}).scalar_one()
            cek = c.execute(text(
                "INSERT INTO cek_senetler(company_id,tur,yon,portfoy_durumu,customer_id,tutar,vade,seri_no,"
                "created_at) VALUES (:c,'cek','alinan','karsiliksiz',:m,10,'2026-12-01','G-1',:t) RETURNING id"),
                {"c": cid, "m": mus, "t": datetime.now(timezone.utc)}).scalar_one()
            c.execute(text(
                "INSERT INTO receivable_charge_documents(company_id,cek_senet_id,customer_id,charge_type,"
                "period_start,period_end,due_date_snapshot,calculation_snapshot,gross_amount,status,"
                "calculation_fingerprint,revision_no,currency,exchange_rate) VALUES "
                "(:c,:k,:m,'bounced_check','2026-12-02','2026-12-02','2026-12-01','{}',10,'posted','x',1,'TRY',1)"),
                {"c": cid, "k": cek, "m": mus})
        with pytest.raises(RuntimeError, match="0086 geri alınamaz"):
            command.downgrade(cfg, "20260914_0085")


def test_CHECK_sekli_SQLitete_REDDEDER(ortam) -> None:
    """``bounced_check`` çeksiz yazılamaz; kapalı küme dışı tür yazılamaz."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    engine = ortam["engine"]
    for tur in ("bounced_check", "kara_liste"):
        with pytest.raises(IntegrityError):
            with engine.begin() as c:
                c.execute(text(
                    "INSERT INTO receivable_charge_documents(company_id,customer_id,charge_type,"
                    "period_start,period_end,due_date_snapshot,calculation_snapshot,gross_amount,status,"
                    "calculation_fingerprint,revision_no,currency,exchange_rate) VALUES "
                    "(:c,:m,:t,'2026-12-02','2026-12-02','2026-12-01','{}',10,'posted','x',1,'TRY',1)"),
                    {"c": ortam["a"], "m": ortam["mus_diger"], "t": tur})


# ------------------------------------------------------------ köprü: ödeme -> evrak ---

@pytest.mark.parametrize("govde_fazla, beklenen", [
    ({"cek": False}, 422),                                         # çek yöntemi, evraksız
    ({"yontem": "promissory_note", "cek": False}, 422),            # senet yöntemi, evraksız
    ({"yontem": "cash"}, 422),                                     # nakitte evrak YASAK
    ({"cek": {"seri_no": "   "}}, 422),
    ({"cek": {"keside_tarihi": "2099-01-01"}}, 422),               # keşide > vade
])
def test_kopru_dogrulamasi(ortam, govde_fazla, beklenen) -> None:
    once = _sql(ortam["engine"], "SELECT COUNT(*) FROM payments")[0][0]
    cevap = _cekle_ode(ortam, ortam["mus_diger"], f"DOG-{uuid4().hex[:6]}", **govde_fazla)
    assert cevap.status_code == beklenen, cevap.text
    assert _sql(ortam["engine"], "SELECT COUNT(*) FROM payments")[0][0] == once


def test_kopru_cek_ile_tahsilat_evrak_dogurur_ve_TAHSIS_eder(ortam) -> None:
    engine = ortam["engine"]
    once = _cari_bakiye(ortam, ortam["mus_diger"])
    cevap = _cekle_ode(ortam, ortam["mus_diger"], "KOP-1",
                       cek={"sube_adi": "Merkez", "hesap_no": "TR11", "kesideci": "Diger"})
    assert cevap.status_code == 201, cevap.text
    govde = cevap.json()
    evrak = ortam["client"].get(f"/api/cek-senetler/{govde['cek_senet_id']}", headers=ortam["h_muh"]).json()
    assert evrak["payment_id"] == govde["id"] and evrak["portfoy_durumu"] == "portfoyde"
    assert (evrak["tur"], evrak["yon"], evrak["customer_id"]) == ("cek", "alinan", ortam["mus_diger"])
    assert Decimal(evrak["tutar"]) == CEK and evrak["vade"] == _gun(30)
    assert (evrak["banka_adi"], evrak["sube_adi"], evrak["hesap_no"]) == ("Ziraat", "Merkez", "TR11")
    # Seçenek A: cari O AN düştü; tahsis motoru ödemeyi açık siparişe uyguladı.
    assert _cari_bakiye(ortam, ortam["mus_diger"]) == once - CEK
    assert _tahsis_net(engine, govde["id"]) == CEK
    # Ödemenin kendi finans satırı YOK (çek kasaya girmedi).
    assert _sql(engine, "SELECT financial_transaction_id FROM payments WHERE id=:i", i=govde["id"])[0][0] is None
    kayit = _sql(engine, "SELECT action_type FROM activity_logs WHERE resource_type='cek_senet' "
                 "AND resource_id=:i", i=evrak["id"])
    assert [k[0] for k in kayit] == ["cek_senet.created"]


def test_kopru_senet_ve_tedarikci_verilen(ortam) -> None:
    cevap = _cekle_ode(ortam, ortam["ted_a"], "SEN-1", entity_type="supplier", yontem="promissory_note")
    assert cevap.status_code == 201, cevap.text
    evrak = ortam["client"].get(f"/api/cek-senetler/{cevap.json()['cek_senet_id']}",
                                headers=ortam["h_muh"]).json()
    assert (evrak["tur"], evrak["yon"], evrak["supplier_id"], evrak["customer_id"]) == (
        "senet", "verilen", ortam["ted_a"], None)
    ekstre = _ekstre(ortam, ortam["ted_a"], tur="suppliers")
    assert any(s["label"] == "Ödeme (Senet - Portföyde)" for s in ekstre["lines"]), ekstre["lines"]


def test_kopru_IDEMPOTENT_tekrar_ikinci_evrak_YAZMAZ(ortam) -> None:
    engine, anahtar = ortam["engine"], f"cs2-idem-{uuid4().hex}"
    bir = _cekle_ode(ortam, ortam["mus_diger"], "IDM-1", anahtar=anahtar, tutar="10")
    iki = _cekle_ode(ortam, ortam["mus_diger"], "IDM-1", anahtar=anahtar, tutar="10")
    assert bir.status_code == 201 and iki.status_code == 201, (bir.text, iki.text)
    assert bir.json()["id"] == iki.json()["id"] and bir.json()["cek_senet_id"] == iki.json()["cek_senet_id"]
    assert _sql(engine, "SELECT COUNT(*) FROM cek_senetler WHERE seri_no='IDM-1'")[0][0] == 1
    # Aynı anahtar, FARKLI evrak -> 409 (evrak parmak izine girer).
    uc = _cekle_ode(ortam, ortam["mus_diger"], "IDM-2", anahtar=anahtar, tutar="10")
    assert uc.status_code == 409, uc.text


def test_bagli_odeme_duzenlenemez_silinemez_409(ortam) -> None:
    client, h = ortam["client"], dict(ortam["h_muh"])
    govde = _cekle_ode(ortam, ortam["mus_diger"], "KLT-1", tutar="15").json()
    duz = _odeme_govdesi(ortam["mus_diger"], seri="KLT-1", tutar="16", cek=False)
    cevap = client.put(f"/api/payments/{govde['id']}", headers=h, json=duz)
    assert cevap.status_code == 409 and cevap.json()["detail"]["code"] == "CEK_BAGLI_ODEME", cevap.text
    assert client.delete(f"/api/payments/{govde['id']}", headers=h).status_code == 409
    # PUT'ta evrak alanı hiç kabul edilmez.
    duz_cekli = _odeme_govdesi(ortam["mus_diger"], seri="KLT-X", tutar="16")
    assert client.put(f"/api/payments/{govde['id']}", headers=h, json=duz_cekli).status_code == 422


def test_yetki_ve_kiraci(ortam) -> None:
    client = ortam["client"]
    for rol in ("h_depo", "h_rapor"):
        cevap = _cekle_ode(ortam, ortam["mus_diger"], f"YTK-{rol}", h=ortam[rol])
        assert cevap.status_code == 403, (rol, cevap.text)
    # B, A'nın müşterisine çekle tahsilat yazamaz; A'nın evrakını geçiremez.
    assert _cekle_ode(ortam, ortam["mus_diger"], "KRC-1", h=ortam["h_b"]).status_code in (400, 404)
    a_evrak = _cekle_ode(ortam, ortam["mus_diger"], "KRC-2", tutar="11").json()["cek_senet_id"]
    assert _gec(ortam, a_evrak, "tahsile_verildi", h=ortam["h_b"]).status_code == 404
    # B'nin ekstresi ve panosu A'nın evrakını görmez.
    assert all("KRC" not in (s["document_no"] or "") for s in _ekstre(ortam, ortam["mus_b"], h=ortam["h_b"])["lines"])
    pano_b = client.get("/api/dashboard", headers=ortam["h_b"]).json()
    assert pano_b["portfolio_checks"] == {"count": 0, "total": 0}


# ------------------------------------------------------------ ters köprü ---

def test_ters_kopru_payment_olustur(ortam) -> None:
    client, engine = ortam["client"], ortam["engine"]
    h = {**ortam["h_muh"], "Idempotency-Key": f"cs2-ters-{uuid4().hex}"}
    once = _cari_bakiye(ortam, ortam["mus_ters"])
    cevap = client.post("/api/cek-senetler", headers=h, json={
        "tur": "cek", "yon": "alinan", "customer_id": ortam["mus_ters"], "tutar": "1500",
        "vade": _gun(45), "seri_no": "TRS-1", "payment_olustur": True, "odeme_tarihi": _gun(-2)})
    assert cevap.status_code == 201, cevap.text
    evrak = cevap.json()
    assert evrak["payment_id"] is not None and evrak["portfoy_durumu"] == "portfoyde"
    odeme = _sql(engine, "SELECT entity_type,entity_id,amount,payment_method,payment_date FROM payments "
                 "WHERE id=:i", i=evrak["payment_id"])[0]
    assert (odeme[0], odeme[1], Decimal(str(odeme[2])), odeme[3], odeme[4]) == (
        "customer", ortam["mus_ters"], Decimal("1500"), "check", _gun(-2))
    assert _tahsis_net(engine, evrak["payment_id"]) == Decimal("1500")
    assert _cari_bakiye(ortam, ortam["mus_ters"]) == once - Decimal("1500")
    # Varsayılan false: CS1 davranışı, ödeme YOK.
    yok = client.post("/api/cek-senetler", headers=ortam["h_muh"], json={
        "tur": "cek", "yon": "alinan", "customer_id": ortam["mus_ters"], "tutar": "1",
        "vade": _gun(45), "seri_no": "TRS-2"}).json()
    assert yok["payment_id"] is None
    # odeme_tarihi yalnız payment_olustur ile.
    assert client.post("/api/cek-senetler", headers=ortam["h_muh"], json={
        "tur": "cek", "yon": "alinan", "customer_id": ortam["mus_ters"], "tutar": "1",
        "vade": _gun(45), "seri_no": "TRS-3", "odeme_tarihi": _gun(0)}).status_code == 422
    # Verilen evrak -> tedarikçi ödemesi.
    h2 = {**ortam["h_muh"], "Idempotency-Key": f"cs2-ters-{uuid4().hex}"}
    ver = client.post("/api/cek-senetler", headers=h2, json={
        "tur": "senet", "yon": "verilen", "supplier_id": ortam["ted_a"], "tutar": "300",
        "vade": _gun(60), "seri_no": "TRS-4", "payment_olustur": True}).json()
    assert _sql(engine, "SELECT entity_type,entity_id,payment_method FROM payments WHERE id=:i",
                i=ver["payment_id"])[0] == ("supplier", ortam["ted_a"], "promissory_note")


def test_ters_kopru_TEKRAR_ikinci_evrak_ve_odeme_YAZMAZ(ortam) -> None:
    """Bu uçta başlığın sahibi genel ara katmandır; ödeme defterine TÜREV
    anahtar gider (``cek-senet:<anahtar>``). Aynı istek iki kez -> tek evrak,
    tek ödeme; iki defter aynı anahtarı iddia etmez."""
    client, engine = ortam["client"], ortam["engine"]
    anahtar = f"cs2-ters-tekrar-{uuid4().hex}"
    govde = {"tur": "cek", "yon": "alinan", "customer_id": ortam["mus_ters"], "tutar": "5",
             "vade": _gun(45), "seri_no": "TRS-TKR", "payment_olustur": True}
    h = {**ortam["h_muh"], "Idempotency-Key": anahtar}
    bir = client.post("/api/cek-senetler", headers=h, json=govde)
    iki = client.post("/api/cek-senetler", headers=h, json=govde)
    assert bir.status_code == 201 and iki.status_code == 201, (bir.text, iki.text)
    assert bir.json()["id"] == iki.json()["id"]
    assert _sql(engine, "SELECT COUNT(*) FROM cek_senetler WHERE seri_no='TRS-TKR'")[0][0] == 1
    assert _sql(engine, "SELECT COUNT(*) FROM payment_idempotency WHERE idempotency_key=:k",
                k="cek-senet:" + anahtar)[0][0] == 1
    assert _sql(engine, "SELECT COUNT(*) FROM payment_idempotency WHERE idempotency_key=:k",
                k=anahtar)[0][0] == 0


# ------------------------------------------------------------ çek döngüsü ve ekstre ---

def test_dongu_TAHSIL_bakiyeyi_DEGISTIRMEZ_parayi_bankaya_yazar(ortam) -> None:
    engine, mus = ortam["engine"], ortam["mus_tahsil"]
    once = _ekstre(ortam, mus)
    assert Decimal(once["closing_balance"]) == SIPARIS
    odeme = _cekle_ode(ortam, mus, "THS-1").json()
    portfoyde = _ekstre(ortam, mus)
    _ekstre_tutarli(portfoyde)
    assert Decimal(portfoyde["closing_balance"]) == SIPARIS - CEK
    assert [s["label"] for s in portfoyde["lines"]][-1] == "Tahsilat (Çek - Portföyde)"

    evrak = odeme["cek_senet_id"]
    assert _gec(ortam, evrak, "tahsile_verildi").status_code == 200
    assert _ekstre(ortam, mus)["lines"][-1]["label"] == "Tahsilat (Çek - Tahsilde)"
    cevap = _gec(ortam, evrak, "tahsil_edildi", tahsil_hesap_id=ortam["banka_a"], tahsil_tarihi=_gun(0))
    assert cevap.status_code == 200, cevap.text
    sonra = _ekstre(ortam, mus)
    _ekstre_tutarli(sonra)
    # Bakiye DEĞİŞMEDİ (cari alındığında düşmüştü); yalnız ibare değişti.
    assert sonra["closing_balance"] == portfoyde["closing_balance"]
    assert sonra["lines"][-1]["label"] == "Tahsilat (Çek - Tahsil Edildi)"
    tx = cevap.json()["financial_transaction_id"]
    satir = _sql(engine, "SELECT account_id,direction,amount,reference_type,reference_id,txn_date "
                 "FROM finance_transactions WHERE id=:i", i=tx)[0]
    assert (satir[0], satir[1], Decimal(str(satir[2])), satir[3], satir[4], satir[5]) == (
        ortam["banka_a"], "in", CEK, "cek_senet", evrak, _gun(0))
    # Tahsis dokunulmadı; ödemenin finans satırı hâlâ yok (para BİR kez girdi).
    assert _tahsis_net(engine, odeme["id"]) == CEK
    assert _sql(engine, "SELECT financial_transaction_id FROM payments WHERE id=:i", i=odeme["id"])[0][0] is None
    assert _sql(engine, "SELECT COUNT(*) FROM receivable_charge_documents WHERE cek_senet_id=:k", k=evrak)[0][0] == 0


def test_dongu_KARSILIKSIZ_borc_belgesi_bakiyeyi_GERI_getirir(ortam) -> None:
    engine, mus = ortam["engine"], ortam["mus_krs"]
    once_ekstre = _ekstre(ortam, mus)
    once_360 = _cari_bakiye(ortam, mus)
    odeme = _cekle_ode(ortam, mus, "KRS-1").json()
    evrak = odeme["cek_senet_id"]
    assert _gec(ortam, evrak, "tahsile_verildi").status_code == 200
    cevap = _gec(ortam, evrak, "karsiliksiz", not_metni="banka iade")
    assert cevap.status_code == 200, cevap.text
    belge_id = cevap.json()["charge_document_id"]
    belge = _sql(engine, "SELECT charge_type,customer_id,cek_senet_id,gross_amount,due_date_snapshot,"
                 "status,order_id,work_order_id,charge_period_id,calculation_snapshot "
                 "FROM receivable_charge_documents WHERE id=:i", i=belge_id)[0]
    assert (belge[0], belge[1], belge[2], Decimal(str(belge[3])), str(belge[4])[:10], belge[5]) == (
        "bounced_check", mus, evrak, CEK, _gun(30), "posted")
    assert belge[6] is None and belge[7] is None and belge[8] is None
    assert '"sebep":"karsiliksiz"' in belge[9]
    # Tahsis KORUNDU (seçenek b); cari çekten önceki bakiyesine DÖNDÜ.
    assert _tahsis_net(engine, odeme["id"]) == CEK
    sonra = _ekstre(ortam, mus)
    _ekstre_tutarli(sonra)
    assert sonra["closing_balance"] == once_ekstre["closing_balance"]
    assert _cari_bakiye(ortam, mus) == once_360
    etiketler = [s["label"] for s in sonra["lines"]]
    assert "Tahsilat (Çek - Karşılıksız)" in etiketler and "Karşılıksız Çek Dekontu" in etiketler
    dekont = next(s for s in sonra["lines"] if s["label"] == "Karşılıksız Çek Dekontu")
    assert Decimal(dekont["debit"]) == CEK and dekont["document_no"] == "KRS-1"

    # karsiliksiz -> iade: belge ZATEN açık, ikincisi AÇILMAZ.
    iade = _gec(ortam, evrak, "iade")
    assert iade.status_code == 200 and iade.json()["charge_document_id"] == belge_id
    assert _sql(engine, "SELECT COUNT(*) FROM receivable_charge_documents WHERE cek_senet_id=:k", k=evrak)[0][0] == 1
    assert _ekstre(ortam, mus)["closing_balance"] == once_ekstre["closing_balance"]


def test_dongu_PORTFOYDEN_IADE_dekont_acar(ortam) -> None:
    mus = ortam["mus_iade"]
    once = _ekstre(ortam, mus)["closing_balance"]
    evrak = _cekle_ode(ortam, mus, "IAD-1").json()["cek_senet_id"]
    cevap = _gec(ortam, evrak, "iade")
    assert cevap.status_code == 200 and cevap.json()["charge_document_id"] is not None
    sonra = _ekstre(ortam, mus)
    _ekstre_tutarli(sonra)
    assert sonra["closing_balance"] == once
    assert "İade Çek Dekontu" in [s["label"] for s in sonra["lines"]]


def test_ODEMESIZ_evrak_karsiliksizda_belge_ACMAZ(ortam) -> None:
    """CS1 evrakı cariyi hiç düşürmedi; borç yazmak müşteriyi İKİ KEZ borçlandırırdı."""
    client = ortam["client"]
    evrak = client.post("/api/cek-senetler", headers=ortam["h_muh"], json={
        "tur": "cek", "yon": "alinan", "customer_id": ortam["mus_diger"], "tutar": "99",
        "vade": _gun(10), "seri_no": "ODS-1"}).json()["id"]
    once = _cari_bakiye(ortam, ortam["mus_diger"])
    assert _gec(ortam, evrak, "tahsile_verildi").status_code == 200
    cevap = _gec(ortam, evrak, "karsiliksiz")
    assert cevap.status_code == 200 and cevap.json()["charge_document_id"] is None
    assert _cari_bakiye(ortam, ortam["mus_diger"]) == once


# ------------------------------------------------------------ ciro anahtarı ---

def _ayar(ortam, **ek):
    client, h = ortam["client"], ortam["h_admin"]
    mevcut = client.get("/api/company-settings", headers=h).json()
    govde = {"negative_stock_policy": mevcut["negative_stock_policy"],
             "credit_limit_policy": mevcut["credit_limit_policy"], **ek}
    return client.put("/api/company-settings", headers=h, json=govde)


def test_ciro_anahtari_KAPALI_varsayilan_tedarikci_odemesi_YOK(ortam) -> None:
    client, engine = ortam["client"], ortam["engine"]
    assert client.get("/api/company-settings", headers=ortam["h_admin"]).json()["ciro_tedarikci_odemesi"] is False
    evrak = _cekle_ode(ortam, ortam["mus_diger"], "CRO-1", tutar="25").json()["cek_senet_id"]
    cevap = _gec(ortam, evrak, "ciro_edildi", endorsed_supplier_id=ortam["ted_ciro"])
    assert cevap.status_code == 200, cevap.text
    assert _sql(engine, "SELECT COUNT(*) FROM payments WHERE entity_type='supplier' AND entity_id=:t",
                t=ortam["ted_ciro"])[0][0] == 0


def test_ciro_anahtari_ACIK_tedarikci_odemesi_acar(ortam) -> None:
    client, engine = ortam["client"], ortam["engine"]
    # Yalnız admin değiştirir (komşu ayarlarla aynı kapı).
    red = client.put("/api/company-settings", headers=ortam["h_muh"], json={
        "negative_stock_policy": "block", "credit_limit_policy": "block", "ciro_tedarikci_odemesi": True})
    assert red.status_code == 403, red.text
    cevap = _ayar(ortam, ciro_tedarikci_odemesi=True)
    assert cevap.status_code == 200 and cevap.json()["ciro_tedarikci_odemesi"] is True, cevap.text
    try:
        assert client.get("/api/company-settings", headers=ortam["h_admin"]).json()["ciro_tedarikci_odemesi"] is True
        evrak = _cekle_ode(ortam, ortam["mus_diger"], "CRO-2", tutar="35").json()["cek_senet_id"]
        gecis = _gec(ortam, evrak, "ciro_edildi", endorsed_supplier_id=ortam["ted_ciro2"],
                     endorsed_date=_gun(-1))
        assert gecis.status_code == 200, gecis.text
        odemeler = _sql(engine, "SELECT amount,payment_method,payment_date,note,financial_transaction_id "
                        "FROM payments WHERE entity_type='supplier' AND entity_id=:t", t=ortam["ted_ciro2"])
        assert len(odemeler) == 1
        tutar, yontem, tarih, not_metni, fin = odemeler[0]
        assert (Decimal(str(tutar)), yontem, tarih, fin) == (Decimal("35"), "check", _gun(-1), None)
        assert "CRO-2" in not_metni
        ekstre = _ekstre(ortam, ortam["ted_ciro2"], tur="suppliers")
        assert Decimal(ekstre["closing_balance"]) == Decimal("-35")
        # Açık `null` dokunmaz.
        assert _ayar(ortam, ciro_tedarikci_odemesi=None).status_code == 200
        assert client.get("/api/company-settings", headers=ortam["h_admin"]).json()["ciro_tedarikci_odemesi"] is True
    finally:
        assert _ayar(ortam, ciro_tedarikci_odemesi=False).status_code == 200


# ------------------------------------------------------------ pano ve yaşlandırma ---

def test_pano_ve_yaslandirma_SQL_gercegiyle_ayni(ortam) -> None:
    client, engine, a = ortam["client"], ortam["engine"], ortam["a"]
    # Portföyde bekleyen bir evrak garanti olsun; karşılıksız belge garanti olsun.
    # `mus_diger`: yalnız GÖRELİ iddia taşır (mutlak bakiyeli müşteriler sıraya duyarlı).
    _cekle_ode(ortam, ortam["mus_diger"], "PNO-1", tutar="123.45")
    krs = _cekle_ode(ortam, ortam["mus_krs"], "PNO-2", tutar="50").json()["cek_senet_id"]
    assert _gec(ortam, krs, "iade").status_code == 200
    adet, toplam = _sql(engine, """SELECT COUNT(*),COALESCE(SUM(tutar),0) FROM cek_senetler
        WHERE company_id=:c AND yon='alinan' AND portfoy_durumu IN ('portfoyde','tahsile_verildi')""", c=a)[0]
    pano = client.get("/api/dashboard", headers=ortam["h_admin"]).json()
    assert pano["portfolio_checks"]["count"] == adet > 0
    assert Decimal(str(pano["portfolio_checks"]["total"])) == Decimal(str(toplam))
    satis = _sql(engine, "SELECT COALESCE(SUM(final_total),0) FROM orders WHERE company_id=:c "
                 "AND COALESCE(status,'completed') NOT IN ('draft','cancelled')", c=a)[0][0]
    tahsilat = _sql(engine, "SELECT COALESCE(SUM(amount),0) FROM payments WHERE company_id=:c "
                    "AND entity_type='customer'", c=a)[0][0]
    acilis = _sql(engine, "SELECT COALESCE(SUM(opening_balance),0) FROM customers WHERE company_id=:c", c=a)[0][0]
    karsiliksiz = _sql(engine, "SELECT COALESCE(SUM(gross_amount),0) FROM receivable_charge_documents "
                       "WHERE company_id=:c AND charge_type='bounced_check'", c=a)[0][0]
    assert Decimal(str(karsiliksiz)) > 0
    beklenen = (Decimal(str(acilis)) + Decimal(str(satis)) - Decimal(str(tahsilat))
                + Decimal(str(karsiliksiz))).quantize(Decimal("0.01"))
    assert Decimal(str(pano["customer_receivables"])) == beklenen

    rapor = client.get("/api/reports/receivables-aging", headers=ortam["h_admin"])
    assert rapor.status_code == 200, rapor.text
    rapor = rapor.json()
    musteri_portfoy = dict(_sql(engine, """SELECT customer_id,SUM(tutar) FROM cek_senetler
        WHERE company_id=:c AND yon='alinan' AND portfoy_durumu IN ('portfoyde','tahsile_verildi')
        GROUP BY customer_id""", c=a))
    for satir in rapor["customers"]:
        beklenen_p = Decimal(str(musteri_portfoy.get(satir["customer_id"], 0))).quantize(Decimal("0.01"))
        assert Decimal(satir["portfolio_checks"]) == beklenen_p, satir
        assert Decimal(satir["net_risk"]) == Decimal(satir["total"]) - beklenen_p
    assert Decimal(rapor["totals"]["portfolio_checks"]) == Decimal(str(toplam)).quantize(Decimal("0.01"))
    assert Decimal(rapor["totals"]["net_risk"]) == (
        Decimal(rapor["totals"]["total"]) - Decimal(rapor["totals"]["portfolio_checks"]))
    # Karşılıksız çek borcu yaşlandırmada EVRAK VADESİYLE durur (başlangıç: vade).
    krs_satir = next(c for c in rapor["customers"] if c["customer_id"] == ortam["mus_krs"])
    belgeler = [d for d in krs_satir["documents"] if d["document_type"] == "bounced_check"]
    assert belgeler and all(d["due_date"] == _gun(30) and d["document_no"].startswith("KC-")
                            for d in belgeler), belgeler


def test_charge_due_date_bounced_check_VADEDIR() -> None:
    from app.receivables_engine import charge_due_date, charge_due_date_sql

    assert "'bounced_check'" in charge_due_date_sql("d")
    satir = {"charge_type": "bounced_check", "due_date_snapshot": "2026-10-01", "period_end": "2026-12-15"}
    assert charge_due_date(satir).isoformat() == "2026-10-01"
    assert charge_due_date({**satir, "charge_type": "late_fee"}).isoformat() == "2026-12-15"


# ------------------------------------------------------------ 5.1c ---

def test_kiraci_geri_yukleme_cek_ve_karsiliksiz_belge(ortam) -> None:
    """B dışa aktarılır, YENİ firma olarak geri yüklenir: borç belgesi YENİ
    çeke, çek YENİ ödemeye, finans hareketi YENİ çeke bağlanır."""
    import zipfile

    from app.config import settings

    engine, client, hb = ortam["engine"], ortam["client"], ortam["h_b"]
    krs = _cekle_ode(ortam, ortam["mus_b"], "RTB-1", h=hb, tutar="700").json()["cek_senet_id"]
    assert _gec(ortam, krs, "tahsile_verildi", h=hb).status_code == 200
    assert _gec(ortam, krs, "karsiliksiz", h=hb).status_code == 200
    ths = _cekle_ode(ortam, ortam["mus_b"], "RTB-2", h=hb, tutar="800").json()["cek_senet_id"]
    assert _gec(ortam, ths, "tahsile_verildi", h=hb).status_code == 200
    assert _gec(ortam, ths, "tahsil_edildi", h=hb, tahsil_hesap_id=ortam["banka_b"],
                tahsil_tarihi=_gun(0)).status_code == 200
    disa = client.get("/api/company/export", headers=hb)
    assert disa.status_code == 200, disa.text[:300]
    assert "receivable_charge_documents.ndjson" in " ".join(zipfile.ZipFile(io.BytesIO(disa.content)).namelist())

    b_admin = _sql(engine, "SELECT id FROM app_users WHERE username='bravoadmin2'")[0][0]
    onceki = settings.sungur_platform_operators
    settings.sungur_platform_operators = str(b_admin)
    try:
        cevap = client.post("/api/platform/tenant-restore", headers=hb,
                            files={"file": ("b.zip", disa.content, "application/zip")},
                            data={"mode": "yeni"})
    finally:
        settings.sungur_platform_operators = onceki
    assert cevap.status_code == 200, cevap.text[:800]
    yeni = int(cevap.json()["company_id"])
    cekler = {r[0]: r[1:] for r in _sql(engine, "SELECT seri_no,id,payment_id,charge_document_id,"
                                        "financial_transaction_id FROM cek_senetler WHERE company_id=:c", c=yeni)}
    assert set(cekler) == {"RTB-1", "RTB-2"}
    yeni_krs, yeni_odeme, yeni_belge, _ = cekler["RTB-1"]
    belge = _sql(engine, "SELECT company_id,cek_senet_id,customer_id,charge_type FROM receivable_charge_documents "
                 "WHERE id=:i", i=yeni_belge)[0]
    assert belge[0] == yeni and belge[1] == yeni_krs and belge[3] == "bounced_check"
    assert _sql(engine, "SELECT company_id FROM customers WHERE id=:i", i=belge[2])[0][0] == yeni
    assert _sql(engine, "SELECT company_id FROM payments WHERE id=:i", i=yeni_odeme)[0][0] == yeni
    yeni_ths, _, _, yeni_tx = cekler["RTB-2"]
    tx = _sql(engine, "SELECT company_id,reference_type,reference_id FROM finance_transactions WHERE id=:i",
              i=yeni_tx)[0]
    assert tuple(tx) == (yeni, "cek_senet", yeni_ths)
    # Kaynak dokunulmadı.
    assert _sql(engine, "SELECT COUNT(*) FROM receivable_charge_documents WHERE company_id=:c "
                "AND charge_type='bounced_check'", c=ortam["b"])[0][0] == 1


# ------------------------------------------------------------ motor KAPALI ---

def test_kopru_motor_KAPALI_yolunda_da_atomik(tmp_path) -> None:
    """Tahsis motoru kapalıyken ``POST /api/payments`` ayrı bir yazım yoludur
    (``finance.odeme_kaydet`` alt dalı); köprü orada da aynı işlemdedir."""
    with _uygulama(tmp_path, motor=False) as (_main, engine, client):
        k = _tohumla(engine)
        o = {"client": client, "engine": engine, "h_muh": _giris(client, "muhasebe2", k["a"]), **k}
        assert _cekle_ode(o, k["mus_diger"], "OFF-0", cek=False).status_code == 422
        cevap = _cekle_ode(o, k["mus_diger"], "OFF-1", tutar="40")
        assert cevap.status_code == 201, cevap.text
        govde = cevap.json()
        assert govde["cek_senet_id"] is not None and "cek_senet" not in govde
        assert tuple(_sql(engine, "SELECT payment_id,portfoy_durumu FROM cek_senetler WHERE id=:i",
                          i=govde["cek_senet_id"])[0]) == (govde["id"], "portfoyde")
        # İçe aktarım yolu (evrak sütunsuz) CS2 öncesi gibi evraksız yazar.
        from app.routers.finance import _cek_bilgisi
        from app.schemas import PaymentCreate

        odeme = PaymentCreate(entity_type="customer", entity_id=k["mus_diger"], amount="1",
                              payment_date=_gun(0), payment_method="check")
        assert _cek_bilgisi(odeme, cek_zorunlu=False) is None
