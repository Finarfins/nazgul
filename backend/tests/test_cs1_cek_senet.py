"""CS1 — çek/senet portföyü: model, göç 0085, CRUD, durum makinesi.

Konu: ``alembic/versions/20260914_0085_cek_senet_portfoyu.py``,
``app/cek_senet_engine.py`` (saf durum makinesi), ``app/cek_senet_schema.py``,
``app/routers/cek_senetler.py``.

--- MUTASYON TABLOSU --------------------------------------------------------

  * ``GECISLER``e bir geçiş eklemek/çıkarmak -> MATRİS testleri KIRMIZI
  * ``auth.py``deki ``/api/cek-senetler`` kuralını silmek
                        -> YETKİ testleri KIRMIZI (GET ``read``e düşer)
  * ``_cek_evrak``tan ``company_id`` yüklemini düşürmek
                        -> KİRACI YALITIMI (404) testleri KIRMIZI
  * bordroyu satır başına commit etmek -> BORDRO ATOMİKLİĞİ KIRMIZI
  * ``MASKELENEN_ALANLAR``dan ``hesap_no``yu silmek -> MASKELEME KIRMIZI
  * göçten ``uq_finance_accounts_company_id``yi kaldırmak
                        -> SQLite FK ikizi KIRMIZI ("foreign key mismatch")
"""
from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260914_0085_cek_senet_portfoyu.py"
PAROLA = "CekSenetPortfoy!2026x"
ANAHTAR = "cs1-cek-senet-portfoyu-anahtar-cs1-cek-senet-portfoyu-anahtar"


def _goc_modulu():
    spec = importlib.util.spec_from_file_location("goc_0085", GOC)
    modul = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modul)
    return modul


def _app_anahtarlari() -> set[str]:
    return {k for k in sys.modules if k == "app" or k.startswith("app.")}


@contextmanager
def _uygulama(tmp_path: Path):
    onceki = {k: sys.modules[k] for k in _app_anahtarlari()}
    mp = pytest.MonkeyPatch()
    for ad in list(_app_anahtarlari()):
        del sys.modules[ad]
    mp.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'cs1.db').as_posix()}")
    mp.setenv("SUNGUR_DATA_DIR", str(tmp_path))
    mp.setenv("SECRET_KEY", ANAHTAR)
    mp.setenv("BOOTSTRAP_ADMIN_PASSWORD", PAROLA)
    mp.setenv("AUTO_MIGRATE", "true")
    mp.setenv("SUNGUR_PLATFORM_OPERATORS", "")
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


def _tohumla(engine) -> dict:
    from sqlalchemy import text

    from app.auth import hash_password

    simdi = datetime.now(timezone.utc)
    ph = hash_password(PAROLA)
    with engine.begin() as c:
        c.execute(text("UPDATE app_users SET must_change_password=0"))
        a = int(c.execute(text("SELECT MIN(id) FROM companies")).scalar_one())
        b = int(c.execute(text(
            "INSERT INTO companies(name,is_active,created_at) VALUES ('Bravo Cek',1,:t) RETURNING id"),
            {"t": simdi}).scalar_one())

        def kullanici(ad: str, rol: str, firma: int) -> int:
            uid = int(c.execute(text(
                "INSERT INTO app_users(username,email,email_verified,display_name,password_hash,role,"
                "is_active,created_at,must_change_password) VALUES (:u,:e,1,:d,:h,:r,1,:t,0) RETURNING id"),
                {"u": ad, "e": f"{ad}@cek.example", "d": ad, "h": ph, "r": rol, "t": simdi}).scalar_one())
            c.execute(text(
                "INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at) "
                "VALUES (:u,:c,1,:t)"), {"u": uid, "c": firma, "t": simdi})
            return uid

        for ad, rol in (("muhasebe1", "muhasebe"), ("satis1", "satis"), ("depo1", "depo"),
                        ("rapor1", "rapor"), ("yonetici1", "yonetici")):
            kullanici(ad, rol, a)
        kullanici("bravoadmin", "admin", b)

        def cari(tablo: str, firma: int, ad: str) -> int:
            return int(c.execute(text(
                f"INSERT INTO {tablo}(company_id,name) VALUES (:c,:n) RETURNING id"),
                {"c": firma, "n": ad}).scalar_one())

        def hesap(firma: int, ad: str, tip: str) -> int:
            return int(c.execute(text(
                "INSERT INTO finance_accounts(company_id,name,account_type,currency,opening_balance,"
                "is_active,created_at) VALUES (:c,:n,:t,'TRY',0,1,:z) RETURNING id"),
                {"c": firma, "n": ad, "t": tip, "z": simdi}).scalar_one())

        return {
            "a": a, "b": b,
            "mus_a": cari("customers", a, "Alfa Musteri"),
            "ted_a": cari("suppliers", a, "Alfa Tedarikci"),
            "ted_a2": cari("suppliers", a, "Alfa Ciro Tedarikci"),
            "mus_b": cari("customers", b, "Bravo Musteri"),
            "ted_b": cari("suppliers", b, "Bravo Tedarikci"),
            "banka_a": hesap(a, "Alfa Banka", "bank"),
            "kasa_a": hesap(a, "Alfa Kasa", "cash"),
            "pos_a": hesap(a, "Alfa POS", "pos"),
            "banka_b": hesap(b, "Bravo Banka", "bank"),
        }


@pytest.fixture(scope="module")
def ortam(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cs1")
    with _uygulama(tmp) as (main, engine, client):
        k = _tohumla(engine)
        yield {
            "main": main, "engine": engine, "client": client, **k,
            "h_admin": _giris(client, "admin", k["a"]),
            "h_muh": _giris(client, "muhasebe1", k["a"]),
            "h_satis": _giris(client, "satis1", k["a"]),
            "h_depo": _giris(client, "depo1", k["a"]),
            "h_rapor": _giris(client, "rapor1", k["a"]),
            "h_b": _giris(client, "bravoadmin", k["b"]),
        }


def _alinan(ortam, **fazla) -> dict:
    govde = {"tur": "cek", "yon": "alinan", "customer_id": ortam["mus_a"],
             "tutar": "1250.50", "vade": "2026-12-01", "seri_no": "A-0001",
             "banka_adi": "Ziraat", "sube_adi": "Merkez", "hesap_no": "TR330006100519786457841326",
             "kesideci": "Alfa Musteri"}
    govde.update(fazla)
    return govde


def _olustur(ortam, h=None, **fazla) -> dict:
    cevap = ortam["client"].post("/api/cek-senetler", headers=h or ortam["h_muh"], json=_alinan(ortam, **fazla))
    assert cevap.status_code == 201, cevap.text
    return cevap.json()


def _gec(ortam, evrak_id: int, hedef: str, h=None, **yuk):
    return ortam["client"].post(
        f"/api/cek-senetler/{evrak_id}/durum-degistir",
        headers=h or ortam["h_muh"], json={"hedef": hedef, **yuk})


def _sql(engine, sorgu: str, **p):
    from sqlalchemy import text

    with engine.connect() as c:
        return c.execute(text(sorgu), p).all()


# ------------------------------------------------------------ statik kapılar ---

def test_DURUM_KUMESI_goc_ile_motor_AYNI() -> None:
    from app import cek_senet_engine as m

    goc = _goc_modulu()
    assert goc.DURUMLAR == m.DURUMLAR
    assert goc.TURLER == m.TURLER and goc.YONLER == m.YONLER
    assert set(m.GECISLER) == set(m.DURUMLAR)


def test_GECIS_MATRISI_36_cift_7_izinli() -> None:
    from app.cek_senet_engine import DURUMLAR, izinli_mi

    izinli = {(k, h) for k in DURUMLAR for h in DURUMLAR if izinli_mi(k, h)}
    assert len(DURUMLAR) ** 2 == 36
    assert izinli == {
        ("portfoyde", "tahsile_verildi"), ("portfoyde", "ciro_edildi"), ("portfoyde", "iade"),
        ("tahsile_verildi", "tahsil_edildi"), ("tahsile_verildi", "karsiliksiz"),
        ("tahsile_verildi", "portfoyde"), ("karsiliksiz", "iade"),
    }


def test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR() -> None:
    """``cek_senetler`` hiçbir ``create_all`` edilen metadata'da bildirilmiyor."""
    from app import auth, core_schema, finance_engine, inventory, tenancy, workflow

    for modul in (auth, core_schema, finance_engine, inventory, tenancy, workflow):
        assert "cek_senetler" not in modul.metadata.tables, modul.__name__
    kaynak = (BACKEND / "app").rglob("*.py")
    for yol in kaynak:
        metin = yol.read_text(encoding="utf-8")
        if "cek_senet_schema" in metin:
            assert "create_all" not in metin or yol.name != "cek_senet_schema.py", yol


def test_SEMA_MODULU_goc_ile_AYNI_SUTUNLAR(ortam) -> None:
    from sqlalchemy import inspect

    from app.cek_senet_schema import cek_senetler

    goc = {c["name"] for c in inspect(ortam["engine"]).get_columns("cek_senetler")}
    assert goc == set(cek_senetler.c.keys())
    assert len(goc) == 25


# ---------------------------------------------------------------- CRUD ---

def test_olustur_listele_suz_getir(ortam) -> None:
    client, h = ortam["client"], ortam["h_muh"]
    bir = _olustur(ortam, seri_no="LST-1", vade="2026-11-15", tutar="100")
    iki = _olustur(ortam, seri_no="LST-2", vade="2026-10-01", tutar="200.10", kesideci="Zeta")
    uc = client.post("/api/cek-senetler", headers=h, json={
        "tur": "senet", "yon": "verilen", "supplier_id": ortam["ted_a"],
        "tutar": "300", "vade": "2027-01-10", "seri_no": "LST-3"})
    assert uc.status_code == 201, uc.text
    uc = uc.json()
    assert bir["portfoy_durumu"] == "portfoyde" and bir["tutar"] == "100.00"
    assert bir["payment_id"] is None and bir["financial_transaction_id"] is None
    assert bir["charge_document_id"] is None and bir["created_by"] is not None
    # Durum ALINMAZ: istemci göndermeye kalksa da `portfoyde` doğar.
    zorla = client.post("/api/cek-senetler", headers=h, json=_alinan(
        ortam, seri_no="LST-4", portfoy_durumu="tahsil_edildi")).json()
    assert zorla["portfoy_durumu"] == "portfoyde"

    liste = client.get("/api/cek-senetler?q=LST-", headers=h).json()
    assert liste["total"] == 4 and set(liste) == {"total", "limit", "offset", "items"}
    vadeler = [s["vade"] for s in liste["items"]]
    assert vadeler == sorted(vadeler), vadeler  # vade ARTAN
    assert liste["items"][0]["id"] == iki["id"]
    assert client.get("/api/cek-senetler?q=lst-&tur=senet", headers=h).json()["total"] == 1
    assert client.get("/api/cek-senetler?q=LST-&yon=verilen", headers=h).json()["items"][0]["id"] == uc["id"]
    assert client.get(f"/api/cek-senetler?q=LST-&supplier_id={ortam['ted_a']}", headers=h).json()["total"] == 1
    assert client.get(f"/api/cek-senetler?q=LST-&customer_id={ortam['mus_a']}", headers=h).json()["total"] == 3
    assert client.get("/api/cek-senetler?q=LST-&vade_from=2026-11-01&vade_to=2026-12-31",
                      headers=h).json()["total"] == 2
    assert client.get("/api/cek-senetler?q=zeta", headers=h).json()["total"] == 1
    assert client.get("/api/cek-senetler?q=ziraat", headers=h).json()["total"] >= 3
    assert client.get("/api/cek-senetler?q=%25", headers=h).json()["total"] == 0  # joker kaçışlı
    assert client.get("/api/cek-senetler?q=LST-&portfoy_durumu=iade", headers=h).json()["total"] == 0
    sayfa = client.get("/api/cek-senetler?q=LST-&limit=2&offset=2", headers=h).json()
    assert len(sayfa["items"]) == 2 and sayfa["total"] == 4
    assert client.get("/api/cek-senetler?limit=201", headers=h).status_code == 422
    assert client.get("/api/cek-senetler?portfoy_durumu=yok", headers=h).status_code == 422
    tek = client.get(f"/api/cek-senetler/{bir['id']}", headers=h).json()
    assert tek["seri_no"] == "LST-1" and tek["vade"] == "2026-11-15"


@pytest.mark.parametrize("govde, beklenen", [
    ({"customer_id": None}, 422),              # alınan: müşteri zorunlu
    ({"supplier_id": 1}, 422),                 # alınan: tedarikçi verilemez
    ({"tutar": "0"}, 422),
    ({"tutar": "-5"}, 422),
    ({"tutar": "1.005"}, 422),                 # kuruştan ince hane
    ({"seri_no": "   "}, 422),
    ({"tur": "bono"}, 422),
    ({"keside_tarihi": "2027-01-01", "vade": "2026-01-01"}, 422),
])
def test_olusturma_dogrulamasi(ortam, govde, beklenen) -> None:
    cevap = ortam["client"].post("/api/cek-senetler", headers=ortam["h_muh"], json=_alinan(ortam, **govde))
    assert cevap.status_code == beklenen, cevap.text


def test_baska_firmanin_carisi_422(ortam) -> None:
    cevap = ortam["client"].post("/api/cek-senetler", headers=ortam["h_muh"],
                                 json=_alinan(ortam, customer_id=ortam["mus_b"]))
    assert cevap.status_code == 422 and "Müşteri" in cevap.text


# ---------------------------------------------------------- durum makinesi ---

def _duruma_getir(ortam, hedef: str, **fazla) -> int:
    """Yeni bir alınan çeki izinli yoldan ``hedef`` durumuna taşır."""
    evrak = _olustur(ortam, **fazla)["id"]
    yol = {
        "portfoyde": [],
        "tahsile_verildi": [("tahsile_verildi", {})],
        "tahsil_edildi": [("tahsile_verildi", {}),
                          ("tahsil_edildi", {"tahsil_hesap_id": ortam["banka_a"], "tahsil_tarihi": "2026-12-02"})],
        "ciro_edildi": [("ciro_edildi", {"endorsed_supplier_id": ortam["ted_a2"]})],
        "karsiliksiz": [("tahsile_verildi", {}), ("karsiliksiz", {})],
        "iade": [("iade", {})],
    }[hedef]
    for adim, yuk in yol:
        cevap = _gec(ortam, evrak, adim, **yuk)
        assert cevap.status_code == 200, (hedef, adim, cevap.text)
    return evrak


@pytest.mark.parametrize("kaynak, hedef, yuk", [
    ("portfoyde", "tahsile_verildi", {}),
    ("portfoyde", "ciro_edildi", {"endorsed_supplier_id": "ted_a2"}),
    ("portfoyde", "iade", {"not_metni": "müşteri geri istedi"}),
    ("tahsile_verildi", "tahsil_edildi", {"tahsil_hesap_id": "kasa_a", "tahsil_tarihi": "2026-12-05"}),
    ("tahsile_verildi", "karsiliksiz", {}),
    ("tahsile_verildi", "portfoyde", {"not_metni": "banka işlem yapmadı"}),
    ("karsiliksiz", "iade", {}),
])
def test_her_izinli_gecis_YESIL(ortam, kaynak, hedef, yuk) -> None:
    evrak = _duruma_getir(ortam, kaynak)
    cozulmus = {k: (ortam[v] if isinstance(v, str) and v in ortam else v) for k, v in yuk.items()}
    cevap = _gec(ortam, evrak, hedef, **cozulmus)
    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    assert govde["portfoy_durumu"] == hedef
    if hedef == "tahsil_edildi":
        assert govde["tahsil_hesap_id"] == ortam["kasa_a"] and govde["tahsil_tarihi"] == "2026-12-05"
    if hedef == "ciro_edildi":
        assert govde["endorsed_supplier_id"] == ortam["ted_a2"]
        assert govde["endorsed_date"] == date.today().isoformat()  # varsayılan bugün
    if "not_metni" in yuk:
        assert yuk["not_metni"] in govde["notlar"]
    # CS1 evrakı ödemesiz doğar (köprü CS2'de ve isteğe bağlı). CS2'den
    # sonra TEK muhasebe yan etkisi `tahsil_edildi`nin finans hareketidir;
    # ödemesiz evrak karşılıksız/iade'de borç belgesi AÇMAZ (cariyi hiç
    # düşürmemişti). Ayrıntı: `tests/test_cs2_cek_senet_cari.py`.
    assert govde["payment_id"] is None and govde["charge_document_id"] is None
    assert (govde["financial_transaction_id"] is not None) == (hedef == "tahsil_edildi")
    kayit = _sql(ortam["engine"], "SELECT action_type, details FROM activity_logs "
                 "WHERE resource_type='cek_senet' AND resource_id=:i ORDER BY id", i=evrak)
    assert kayit[0][0] == "cek_senet.created" and kayit[-1][0] == "cek_senet.durum"
    assert f'"to": "{hedef}"' in kayit[-1][1]


_IZINSIZ = [
    (k, h)
    for k in ("portfoyde", "tahsile_verildi", "tahsil_edildi", "ciro_edildi", "karsiliksiz", "iade")
    for h in ("portfoyde", "tahsile_verildi", "tahsil_edildi", "ciro_edildi", "karsiliksiz", "iade")
    if (k, h) not in {
        ("portfoyde", "tahsile_verildi"), ("portfoyde", "ciro_edildi"), ("portfoyde", "iade"),
        ("tahsile_verildi", "tahsil_edildi"), ("tahsile_verildi", "karsiliksiz"),
        ("tahsile_verildi", "portfoyde"), ("karsiliksiz", "iade"),
    }
]


@pytest.fixture(scope="module")
def durumdaki_evraklar(ortam) -> dict:
    return {k: _duruma_getir(ortam, k, seri_no=f"MTR-{k}") for k in
            ("portfoyde", "tahsile_verildi", "tahsil_edildi", "ciro_edildi", "karsiliksiz", "iade")}


def test_izinsiz_matris_29_cift() -> None:
    assert len(_IZINSIZ) == 29


@pytest.mark.parametrize("kaynak, hedef", _IZINSIZ)
def test_her_izinsiz_gecis_409(ortam, durumdaki_evraklar, kaynak, hedef) -> None:
    evrak = durumdaki_evraklar[kaynak]
    cevap = _gec(ortam, evrak, hedef)
    assert cevap.status_code == 409, cevap.text
    assert cevap.json()["detail"]["code"] == "CEK_GECIS_GECERSIZ"
    # Reddedilen geçiş satırı DEĞİŞTİRMEZ.
    durum = _sql(ortam["engine"], "SELECT portfoy_durumu FROM cek_senetler WHERE id=:i", i=evrak)[0][0]
    assert durum == kaynak


def test_yuk_dogrulamasi(ortam) -> None:
    tahsilde = _duruma_getir(ortam, "tahsile_verildi", seri_no="YUK-1")
    assert _gec(ortam, tahsilde, "tahsil_edildi").status_code == 422
    assert _gec(ortam, tahsilde, "tahsil_edildi", tahsil_tarihi="2026-12-01").status_code == 422
    assert _gec(ortam, tahsilde, "tahsil_edildi", tahsil_hesap_id=ortam["pos_a"],
                tahsil_tarihi="2026-12-01").status_code == 422          # POS kasa/banka değil
    assert _gec(ortam, tahsilde, "tahsil_edildi", tahsil_hesap_id=ortam["banka_b"],
                tahsil_tarihi="2026-12-01").status_code == 422          # başka firmanın hesabı
    assert _gec(ortam, tahsilde, "karsiliksiz", tahsil_tarihi="2026-12-01").status_code == 422  # ilgisiz alan
    assert _gec(ortam, tahsilde, "portfoyde", tahsil_hesap_id=ortam["banka_a"]).status_code == 422
    portfoy = _olustur(ortam, seri_no="YUK-2")["id"]
    assert _gec(ortam, portfoy, "ciro_edildi").status_code == 422
    assert _gec(ortam, portfoy, "ciro_edildi", endorsed_supplier_id=ortam["ted_b"]).status_code == 422
    # Hiçbiri durumu değiştirmedi.
    assert _sql(ortam["engine"], "SELECT portfoy_durumu FROM cek_senetler WHERE id=:i", i=tahsilde)[0][0] == "tahsile_verildi"


def test_karsiliksiz_notu_kabul_edilir(ortam) -> None:
    # Diğer her hedef not alır; karşılıksız da almalı (lens C, bulgu 2).
    tahsilde = _duruma_getir(ortam, "tahsile_verildi", seri_no="KRS-NOT")
    cevap = _gec(ortam, tahsilde, "karsiliksiz", not_metni="Banka iade etti")
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["portfoy_durumu"] == "karsiliksiz"
    notlar = _sql(ortam["engine"], "SELECT notlar FROM cek_senetler WHERE id=:i", i=tahsilde)[0][0]
    assert notlar.endswith("[karsiliksiz] Banka iade etti")


INT4_ASIM = 2147483648


def test_int4_ustu_kimlik_422(ortam) -> None:
    # PG'de ``::INTEGER`` dönüşümü 500 verirdi; sınır uçta 422'dir.
    client, h = ortam["client"], ortam["h_muh"]
    assert client.get(f"/api/cek-senetler/{INT4_ASIM}", headers=h).status_code == 422
    assert client.get(f"/api/cek-senetler/{INT4_ASIM - 1}", headers=h).status_code == 404
    assert _gec(ortam, INT4_ASIM, "tahsile_verildi").status_code == 422
    for alan in ("customer_id", "supplier_id"):
        cevap = client.get(f"/api/cek-senetler?{alan}={INT4_ASIM}", headers=h)
        assert cevap.status_code == 422, (alan, cevap.text)
    cevap = client.post("/api/cek-senetler", headers=h, json=_alinan(ortam, customer_id=INT4_ASIM))
    assert cevap.status_code == 422, cevap.text
    evrak = _olustur(ortam, seri_no="INT4-1")["id"]
    assert _gec(ortam, evrak, "ciro_edildi", endorsed_supplier_id=INT4_ASIM).status_code == 422


def test_liste_offset_tavani_422(ortam) -> None:
    client, h = ortam["client"], ortam["h_muh"]
    assert client.get("/api/cek-senetler?offset=9223372036854775808", headers=h).status_code == 422
    assert client.get(f"/api/cek-senetler?offset={INT4_ASIM}", headers=h).status_code == 422
    cevap = client.get(f"/api/cek-senetler?offset={INT4_ASIM - 1}", headers=h)
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["items"] == []


def test_verilen_evrak_ciro_edilemez_409(ortam) -> None:
    verilen = ortam["client"].post("/api/cek-senetler", headers=ortam["h_muh"], json={
        "tur": "cek", "yon": "verilen", "supplier_id": ortam["ted_a"], "tutar": "50",
        "vade": "2026-12-31", "seri_no": "VRL-1"}).json()["id"]
    cevap = _gec(ortam, verilen, "ciro_edildi", endorsed_supplier_id=ortam["ted_a2"])
    assert cevap.status_code == 409, cevap.text
    assert cevap.json()["detail"]["code"] == "CEK_CIRO_YALNIZ_ALINAN"


# ---------------------------------------------------------------- kiracı ---

def test_baska_firma_404_ve_listede_yok(ortam) -> None:
    client = ortam["client"]
    evrak = _olustur(ortam, seri_no="KRC-1")["id"]
    assert client.get(f"/api/cek-senetler/{evrak}", headers=ortam["h_b"]).status_code == 404
    gecis = _gec(ortam, evrak, "tahsile_verildi", h=ortam["h_b"])
    assert gecis.status_code == 404, gecis.text
    assert client.get("/api/cek-senetler?q=KRC-", headers=ortam["h_b"]).json()["total"] == 0
    assert client.get("/api/cek-senetler/99999999", headers=ortam["h_muh"]).status_code == 404
    assert _sql(ortam["engine"], "SELECT portfoy_durumu FROM cek_senetler WHERE id=:i", i=evrak)[0][0] == "portfoyde"


# ----------------------------------------------------------------- yetki ---

def test_yetki_matrisi(ortam) -> None:
    """``payments`` BÜTÜN metotlarda. Ölçüldü: depo/rapor GET'te de 403."""
    from app.auth import ROLE_PERMISSIONS

    client = ortam["client"]
    tasiyanlar = sorted(r for r, izin in ROLE_PERMISSIONS.items() if "payments" in izin or "*" in izin)
    assert tasiyanlar == ["admin", "muhasebe", "satis", "yonetici"], tasiyanlar
    for anahtar in ("h_depo", "h_rapor"):
        assert client.get("/api/cek-senetler", headers=ortam[anahtar]).status_code == 403
        assert client.post("/api/cek-senetler", headers=ortam[anahtar],
                           json=_alinan(ortam, seri_no="YTK-X")).status_code == 403
    # Karar 4: `satis` bugünkü matrisle YAZAR.
    assert client.post("/api/cek-senetler", headers=ortam["h_satis"],
                       json=_alinan(ortam, seri_no="YTK-S")).status_code == 201
    assert client.get("/api/cek-senetler", headers=ortam["h_satis"]).status_code == 200


# ---------------------------------------------------------------- bordro ---

def test_bordro_toplu_ve_sirali(ortam) -> None:
    satirlar = [_alinan(ortam, seri_no=f"BRD-{i}", tutar=f"{i}0") for i in range(1, 4)]
    cevap = ortam["client"].post("/api/cek-senetler/bordro", headers=ortam["h_muh"], json={"satirlar": satirlar})
    assert cevap.status_code == 201, cevap.text
    ids = cevap.json()["ids"]
    assert len(ids) == 3 and ids == sorted(ids)
    seriler = _sql(ortam["engine"], "SELECT seri_no FROM cek_senetler WHERE id IN (:a,:b,:c) ORDER BY id",
                   a=ids[0], b=ids[1], c=ids[2])
    assert [s[0] for s in seriler] == ["BRD-1", "BRD-2", "BRD-3"]
    assert _sql(ortam["engine"], "SELECT COUNT(*) FROM activity_logs WHERE action_type='cek_senet.created' "
                "AND details LIKE '%\"bordro\": true%'")[0][0] >= 3


@pytest.mark.parametrize("bozuk", [
    {"customer_id": "mus_b"},     # başka firmanın müşterisi -> 422, uç katmanı
    {"tutar": "0"},               # şema -> 422, FastAPI
])
def test_bordro_tek_bozuk_satir_HICBIR_SEY_yazmaz(ortam, bozuk) -> None:
    once = _sql(ortam["engine"], "SELECT COUNT(*) FROM cek_senetler")[0][0]
    once_log = _sql(ortam["engine"], "SELECT COUNT(*) FROM activity_logs")[0][0]
    cozulmus = {k: (ortam[v] if isinstance(v, str) and v in ortam else v) for k, v in bozuk.items()}
    satirlar = [_alinan(ortam, seri_no="ATM-1"), _alinan(ortam, seri_no="ATM-2", **cozulmus),
                _alinan(ortam, seri_no="ATM-3")]
    cevap = ortam["client"].post("/api/cek-senetler/bordro", headers=ortam["h_muh"], json={"satirlar": satirlar})
    assert cevap.status_code == 422, cevap.text
    assert _sql(ortam["engine"], "SELECT COUNT(*) FROM cek_senetler")[0][0] == once
    assert _sql(ortam["engine"], "SELECT COUNT(*) FROM activity_logs")[0][0] == once_log


def test_bordro_tavani_200(ortam) -> None:
    fazla = [_alinan(ortam, seri_no=f"TVN-{i}") for i in range(201)]
    assert ortam["client"].post("/api/cek-senetler/bordro", headers=ortam["h_muh"],
                                json={"satirlar": fazla}).status_code == 422
    assert ortam["client"].post("/api/cek-senetler/bordro", headers=ortam["h_muh"],
                                json={"satirlar": []}).status_code == 422


# ------------------------------------------------------------- maskeleme ---

def test_hesap_no_maskeleme_fonksiyon_duzeyi() -> None:
    from app.alan_maskeleme import maskele_cari

    satir = {"hesap_no": "TR330006100519786457841326", "seri_no": "S-1"}
    assert maskele_cari(satir, "depo")["hesap_no"] == "*" * 22 + "1326"
    assert maskele_cari(satir, "muhasebe")["hesap_no"] == satir["hesap_no"]
    assert maskele_cari(satir, "depo")["seri_no"] == "S-1"


def test_hesap_no_maskeli_role_HTTPde_maskeli(ortam, monkeypatch) -> None:
    """Bugün `payments` taşıyan maskeli rol YOK (ölçüldü); maskenin BAĞLI
    olduğunu kanıtlamak için `depo`ya geçici olarak `payments` verilir."""
    from app.auth import ROLE_PERMISSIONS

    evrak = _olustur(ortam, seri_no="MSK-1")["id"]
    tam = ortam["client"].get(f"/api/cek-senetler/{evrak}", headers=ortam["h_satis"]).json()
    assert tam["hesap_no"] == "TR330006100519786457841326"
    monkeypatch.setitem(ROLE_PERMISSIONS, "depo", set(ROLE_PERMISSIONS["depo"]) | {"payments"})
    tek = ortam["client"].get(f"/api/cek-senetler/{evrak}", headers=ortam["h_depo"])
    assert tek.status_code == 200, tek.text
    assert tek.json()["hesap_no"] == "*" * 22 + "1326"
    liste = ortam["client"].get("/api/cek-senetler?q=MSK-1", headers=ortam["h_depo"]).json()
    assert liste["items"][0]["hesap_no"].endswith("1326") and liste["items"][0]["hesap_no"].startswith("*")


# ---------------------------------------------------------------- 5.1c ---

def test_kiraci_geri_yukleme_iki_satir_yuvarlak_yolculuk(ortam) -> None:
    """A dışa aktarılır, YENİ firma olarak geri yüklenir: iki çek satırı
    yeni firmaya bileşik FK'leri YENİDEN EŞLENEREK gelir."""
    import zipfile

    from app.config import settings

    engine, client = ortam["engine"], ortam["client"]
    # Kaynak: B'de iki evrak (A zaten çok satır taşıyor; B temiz).
    hb = ortam["h_b"]
    bir = client.post("/api/cek-senetler", headers=hb, json={
        "tur": "cek", "yon": "alinan", "customer_id": ortam["mus_b"], "tutar": "777.77",
        "vade": "2026-12-12", "seri_no": "RT-1", "hesap_no": "TR000000000000000000001111"}).json()["id"]
    iki = client.post("/api/cek-senetler", headers=hb, json={
        "tur": "senet", "yon": "verilen", "supplier_id": ortam["ted_b"], "tutar": "88",
        "vade": "2027-02-02", "seri_no": "RT-2"}).json()["id"]
    assert _gec(ortam, bir, "tahsile_verildi", h=hb).status_code == 200
    assert _gec(ortam, bir, "tahsil_edildi", h=hb, tahsil_hesap_id=ortam["banka_b"],
                tahsil_tarihi="2026-12-13").status_code == 200
    disa = client.get("/api/company/export", headers=hb)
    assert disa.status_code == 200, disa.text[:300]
    assert "cek_senetler.ndjson" in " ".join(zipfile.ZipFile(io.BytesIO(disa.content)).namelist())

    b_admin = _sql(engine, "SELECT id FROM app_users WHERE username='bravoadmin'")[0][0]
    onceki = settings.sungur_platform_operators
    settings.sungur_platform_operators = str(b_admin)
    try:
        cevap = client.post("/api/platform/tenant-restore", headers=hb,
                            files={"file": ("b.zip", disa.content, "application/zip")},
                            data={"mode": "yeni"})
    finally:
        settings.sungur_platform_operators = onceki
    assert cevap.status_code == 200, cevap.text[:800]
    rapor = cevap.json()
    yeni = int(rapor["company_id"])
    assert rapor["tables"]["cek_senetler"]["rows"] == 2, rapor["tables"].get("cek_senetler")
    satirlar = _sql(engine, "SELECT seri_no,tutar,portfoy_durumu,customer_id,supplier_id,tahsil_hesap_id,"
                    "hesap_no FROM cek_senetler WHERE company_id=:c ORDER BY seri_no", c=yeni)
    assert [s[0] for s in satirlar] == ["RT-1", "RT-2"]
    assert Decimal(str(satirlar[0][1])) == Decimal("777.77") and satirlar[0][2] == "tahsil_edildi"
    assert satirlar[0][6] == "TR000000000000000000001111"
    # Bileşik FK'ler YENİ firmanın satırlarına eşlendi (eskilere değil).
    mus = _sql(engine, "SELECT company_id,name FROM customers WHERE id=:i", i=satirlar[0][3])[0]
    ted = _sql(engine, "SELECT company_id,name FROM suppliers WHERE id=:i", i=satirlar[1][4])[0]
    hes = _sql(engine, "SELECT company_id,name FROM finance_accounts WHERE id=:i", i=satirlar[0][5])[0]
    assert tuple(mus) == (yeni, "Bravo Musteri")
    assert tuple(ted) == (yeni, "Bravo Tedarikci")
    assert tuple(hes) == (yeni, "Bravo Banka")
    # Kaynak dokunulmadı.
    assert _sql(engine, "SELECT COUNT(*) FROM cek_senetler WHERE company_id=:c", c=ortam["b"])[0][0] == 2
    assert iki


# ---------------------------------------------------------- göç SQLite ---

def test_goc_SQLite_yukari_asagi_yukari_ve_FK_ikizi(tmp_path) -> None:
    """``PRAGMA foreign_keys=ON`` altında bileşik FK çapraz firmayı reddeder;
    ``downgrade`` tabloyu ve eklediği hedef UNIQUE'i kaldırır."""
    import subprocess

    betik = r'''
import os, sys
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from alembic import command
from alembic.config import Config
url = os.environ["DATABASE_URL"]
cfg = Config("alembic.ini"); cfg.set_main_option("sqlalchemy.url", url)
command.upgrade(cfg, "head")
m = sa.create_engine(url)
@sa.event.listens_for(m, "connect")
def _fk(dbapi, _):
    dbapi.execute("PRAGMA foreign_keys=ON")
i = sa.inspect(m)
assert "cek_senetler" in i.get_table_names()
assert "uq_finance_accounts_company_id" in {u["name"] for u in i.get_unique_constraints("finance_accounts")}
with m.begin() as c:
    a = c.execute(sa.text("INSERT INTO companies(name,is_active,created_at) VALUES ('A',1,CURRENT_TIMESTAMP) RETURNING id")).scalar_one()
    b = c.execute(sa.text("INSERT INTO companies(name,is_active,created_at) VALUES ('B',1,CURRENT_TIMESTAMP) RETURNING id")).scalar_one()
    mb = c.execute(sa.text("INSERT INTO customers(company_id,name) VALUES (:b,'MB') RETURNING id"), {"b": b}).scalar_one()
    ma = c.execute(sa.text("INSERT INTO customers(company_id,name) VALUES (:a,'MA') RETURNING id"), {"a": a}).scalar_one()
def dene(sql, p):
    try:
        with m.begin() as c:
            c.execute(sa.text(sql), p)
    except IntegrityError:
        return "RED"
    return "KABUL"
ekle = "INSERT INTO cek_senetler(company_id,tur,yon,portfoy_durumu,customer_id,supplier_id,tutar,vade,seri_no) VALUES (:c,:t,:y,:d,:m,:s,:u,'2026-12-01','X')"
sonuc = {
  "ayni_firma": dene(ekle, {"c": a, "t": "cek", "y": "alinan", "d": "portfoyde", "m": ma, "s": None, "u": 10}),
  "capraz_firma": dene(ekle, {"c": a, "t": "cek", "y": "alinan", "d": "portfoyde", "m": mb, "s": None, "u": 10}),
  "yon_taraf": dene(ekle, {"c": a, "t": "cek", "y": "alinan", "d": "portfoyde", "m": None, "s": None, "u": 10}),
  "tutar_sifir": dene(ekle, {"c": a, "t": "cek", "y": "alinan", "d": "portfoyde", "m": ma, "s": None, "u": 0}),
  "durum": dene(ekle, {"c": a, "t": "cek", "y": "alinan", "d": "kayip", "m": ma, "s": None, "u": 10}),
  "tur": dene(ekle, {"c": a, "t": "bono", "y": "alinan", "d": "portfoyde", "m": ma, "s": None, "u": 10}),
}
print("SONUC", sonuc)
m.dispose()
command.downgrade(cfg, "20260914_0084")
i = sa.inspect(sa.create_engine(url))
assert "cek_senetler" not in i.get_table_names()
assert "uq_finance_accounts_company_id" not in {u["name"] for u in i.get_unique_constraints("finance_accounts")}
command.upgrade(cfg, "head")
assert "cek_senetler" in sa.inspect(sa.create_engine(url)).get_table_names()
print("GOC_TAMAM")
'''
    import os

    ortam_degiskenleri = {**os.environ, "DATABASE_URL": f"sqlite:///{(tmp_path / 'goc.db').as_posix()}",
                          "PYTHONPATH": str(BACKEND), "PYTHONIOENCODING": "utf-8",
                          "SUNGUR_DATA_DIR": str(tmp_path)}
    kosu = subprocess.run([sys.executable, "-c", betik], cwd=BACKEND, env=ortam_degiskenleri,
                          capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert kosu.returncode == 0, kosu.stdout[-2000:] + kosu.stderr[-4000:]
    assert "GOC_TAMAM" in kosu.stdout
    assert ("SONUC {'ayni_firma': 'KABUL', 'capraz_firma': 'RED', 'yon_taraf': 'RED', "
            "'tutar_sifir': 'RED', 'durum': 'RED', 'tur': 'RED'}") in kosu.stdout, kosu.stdout[-1500:]
