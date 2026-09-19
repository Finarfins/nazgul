"""H57 + H58 — serbest metin aramasının Türkçe katlaması ve LIKE kaçışı.

KUSUR (#139 merceği ölçtü, `bf8e73f` üzerinde):

* **H57** — SQLite ``LOWER()`` yalnız ASCII katlar: sahibi "Mehmet Yılmaz"
  olan cari "YILMAZ" ya da "Yilmaz" aranınca 0 satır dönüyordu. PG'de
  ``/api/search`` hiç katlamıyordu (büyük/küçük harfe duyarlı ``LIKE``).
* **H58** — ``customers.py`` ile ``finance.py``nin üç ucu ``f'%{q}%'``
  kuruyordu: kullanıcının yazdığı ``%`` firmanın BÜTÜN satırlarını, ``_`` her
  tek karakteri eşliyordu.

ÇARE tek dikişte (`app/arama.py`): sorgu Python'da ``arama_katla`` ile, kolon
SQL'de ``katli_sql`` ile AYNI tablodan (``ARAMA_ESLESME``) geçer; kalıp
``arama_deseni`` ile kaçırılır ve her ``LIKE`` ``ESCAPE '\\'`` taşır.

DÜZELTME TURU (mercek NO-GO, `0580dcc`): ilk sürüm sorguyu Python
``.upper()`` ile, kolonu SQL ``UPPER()`` ile büyütüyordu; tabloda olmayan
aksanlar iki tarafta farklı katlandı ve "Kâzım", "hâlâ", "Café" tabanda
bulunup HEAD'de BOŞ döndü. Artık tek kural karakter başına tablo eşlemesidir;
``UPPER`` yok. İddialar: aksanlı her yazım bulur, tabanda birebir alt
dizgiyle bulunan her ad hâlâ bulunur, ``katli_sql`` istek başına ÇAĞRILMAZ.

Beş uç, aynı tohum, aynı iddialar. B firmasının carisi aynı sahibi taşır ki
kiracı sızıntısı görünür olsun. PG ikizi: ``test_h57_h58_arama_postgresql.py``.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

import pytest

_CALISMA_ALANI = tempfile.mkdtemp(prefix="h57-h58-arama-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "h57.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI
os.environ["AUTO_MIGRATE"] = "true"

from app.arama import (  # noqa: E402
    ARAMA_ESLESME, arama_deseni, arama_katla, katli_sql, sqlite_katlamayi_kaydet,
)

ACILIS_PAROLASI = "admin123"
ADMIN_PAROLASI = "H57Arama!2026xyz"
DEPO_PAROLASI = "H57Depo!2026xyz"

YILMAZ = "Yılmaz Tarım"
#: Tohum adları. Hiçbiri `%` taşımaz: `q=%` ancak joker gibi davranırsa satır döner.
KAZIM = "Kâzım Hâlâ Tarım"
CAFE = "Café Deniz"
EMILE = "Émile"
ADLAR = (YILMAZ, "Kaya Ltd", "OEM_123 Parça", "OEMX123 Parça", "A\\B Gıda", "AB Gıda",
         KAZIM, CAFE, EMILE)
#: Aksanlı adların her yazımı YALNIZ kendi adını bulmalı (0580dcc'de hepsi BOŞTU).
AKSAN_YAZIMLARI = {
    KAZIM: ("Kâzım", "kâzım", "KÂZIM", "Kazım", "kazim", "KAZIM", "hâlâ", "HÂLÂ", "Hâlâ",
            "hala", "HALA", "kâzım hâlâ"),
    CAFE: ("Café", "café", "CAFÉ", "cafe", "CAFE", "Cafe Deniz", "CAFÉ DENİZ"),
    EMILE: ("Émile", "émile", "ÉMILE", "emile", "EMILE", "Emile"),
}
AKSAN_CIFTLERI = tuple((ad, q) for ad, yazimlar in AKSAN_YAZIMLARI.items() for q in yazimlar)
#: Tabloda OLMAYAN harfler: dokunulmadan kalır, birebir yazılınca bulunur.
TABLO_DISI = ("Straße", "Москва Ltd", "Ωmega", "Æsir", "œuvre")
B_ADI = "Yılmaz B Firması"

YILMAZ_YAZIMLARI = ("yılmaz", "YILMAZ", "Yilmaz", "yilmaz", "YİLMAZ", "  yılMAZ  ")
UCLAR = ("/api/customers", "/api/suppliers", "/api/payments", "/api/finance/transactions", "/api/search")
CARI_UCLARI = ("/api/customers", "/api/suppliers", "/api/search")


# --------------------------------------------------------------------------
# Saf birim: kalıp ve katlama
# --------------------------------------------------------------------------

def test_arama_deseni_katlar_ve_kacirir() -> None:
    assert arama_deseni("  %_\\  ") == "%\\%\\_\\\\%"
    assert {arama_deseni(y) for y in YILMAZ_YAZIMLARI} == {"%YILMAZ%"}
    assert arama_deseni("") == "%%"
    assert arama_deseni("Kâzım") == arama_deseni("KAZIM") == "%KAZIM%"
    assert arama_deseni("café") == arama_deseni("CAFÉ") == "%CAFE%"


def test_tek_kural_UPPER_yok_tablo_BUYUK_ASCII() -> None:
    """Katlama YALNIZ tablodur: SQL'de `UPPER` geçmez, her hedef A-Z."""
    assert "UPPER" not in katli_sql("x") and "REPLACE" not in katli_sql("x")
    # Ayristirici yigini YAPIYA bagli: ifade TEK cagri, ic ice DEGIL (CI kirmizisi).
    assert katli_sql("x").count("(") == 1
    assert all(len(k) == 1 and "A" <= h <= "Z" for k, h in ARAMA_ESLESME)
    for harf in "âÂîÎûÛéÉèêëáàäóöôúüñçğışİ":
        assert harf in dict(ARAMA_ESLESME), harf
    # Tablo dışı harf iki tarafta da DOKUNULMADAN kalır (Python `.upper()` yok).
    assert arama_katla("straße москва") == "STRAßE москва"


@pytest.mark.parametrize("deger", [
    YILMAZ, "Mehmet Yılmaz", "İSMAİL IŞIK", "şğüöç ŞĞÜÖÇ", "istanbul", "A\\B Gıda",
    "OEM_123", "ali@ornek.test", KAZIM, CAFE, EMILE, "HÂLÂ ÿØøÑñ", *TABLO_DISI,
])
def test_katli_sql_SQLitete_arama_katla_ile_AYNI(deger: str) -> None:
    """Kolon ifadesi Python katlamasıyla harfi harfine aynı sonucu vermeli; yoksa
    iki taraf farklı biçimlerde buluşur ve eşleşme şansa kalır."""
    baglanti = sqlite3.connect(":memory:")
    sqlite_katlamayi_kaydet(baglanti)
    try:
        (sonuc,) = baglanti.execute(f"SELECT {katli_sql('?')}", (deger,)).fetchone()
    finally:
        baglanti.close()
    assert sonuc == arama_katla(deger)


def _alt_dizgiler(ad: str) -> set[str]:
    return {ad[i:j] for i in range(len(ad)) for j in range(i + 1, len(ad) + 1) if ad[i:j].strip()}


def test_birebir_alt_dizgi_her_zaman_bulunur_SQLite() -> None:
    """Gerilemezlik (katlama düzeyi): bir adın HER birebir alt dizgisi, katlanmış
    kolonda katlanmış kalıpla eşleşir. Karakter başına eşlemenin özelliği; ilk
    sürümde "â"/"é" içeren her alt dizgi bunu bozuyordu."""
    baglanti = sqlite3.connect(":memory:")
    sqlite_katlamayi_kaydet(baglanti)
    try:
        for ad in (*ADLAR, B_ADI, *TABLO_DISI):
            for alt in _alt_dizgiler(ad):
                (esles,) = baglanti.execute(
                    f"SELECT {katli_sql('?')} LIKE ? ESCAPE '\\'", (ad, arama_deseni(alt))).fetchone()
                assert esles == 1, (ad, alt)
    finally:
        baglanti.close()


def test_katli_sql_istek_basina_CAGRILMAZ() -> None:
    """Şef koşulu: katlama ifadesi modül yüklenirken BİR KEZ kurulur. Router'larda
    `katli_sql(...)` yalnız modül düzeyindeki atamalarda geçebilir (AST ile)."""
    import ast
    from pathlib import Path

    def cagrilar(dugum) -> list[int]:
        return [a.lineno for a in ast.walk(dugum)
                if isinstance(a, ast.Call) and getattr(a.func, "id", None) == "katli_sql"]

    kok = Path(__file__).resolve().parents[1] / "app" / "routers"
    modul_duzeyi, fonksiyon_ici = 0, []
    for dosya in sorted(kok.glob("*.py")):
        agac = ast.parse(dosya.read_text(encoding="utf-8"))
        for dugum in ast.walk(agac):
            if isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                fonksiyon_ici += [f"{dosya.name}:{n}" for n in cagrilar(dugum)]
        for d in agac.body:
            if isinstance(d, ast.Assign):
                modul_duzeyi += len(cagrilar(d))
    assert fonksiyon_ici == []
    # search 10 + finance 5 + customers 3: sayı düşerse bir çağrı modül
    # düzeyinden (atama dışı bir yere) kaçmıştır.
    assert modul_duzeyi == 18


# --------------------------------------------------------------------------
# Gerçek istekler
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def istemci():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def admin(istemci):
    giris = istemci.post("/api/auth/login", json={"username": "admin", "password": ACILIS_PAROLASI})
    assert giris.status_code == 200, giris.text
    govde = giris.json()
    basliklar = {
        "Authorization": "Bearer " + govde["access_token"],
        "X-Company-ID": str(govde["companies"][0]["id"]),
    }
    degis = istemci.post(
        "/api/auth/change-password", headers=basliklar,
        json={"current_password": ACILIS_PAROLASI, "new_password": ADMIN_PAROLASI},
    )
    assert degis.status_code == 200, degis.text
    basliklar["Authorization"] = "Bearer " + degis.json()["access_token"]
    return basliklar


@pytest.fixture(scope="module")
def tohum(istemci, admin):
    """A firmasına her uç için aynı adlar; B firmasına aynı sahipli bir cari."""
    from sqlalchemy import text

    from app.db import SessionLocal

    h = admin
    hesap = istemci.post("/api/finance/accounts", headers=h, json={"name": "H57 Kasa"})
    assert hesap.status_code == 201, hesap.text
    hesap_id = hesap.json()["id"]
    for ad in ADLAR:
        sahip = "Mehmet Yılmaz" if ad == YILMAZ else "Ali Kaya"
        m = istemci.post("/api/customers", headers=h, json={"name": ad, "owner_name": sahip})
        assert m.status_code == 201, m.text
        t = istemci.post("/api/suppliers", headers=h, json={"name": ad, "owner_name": sahip})
        assert t.status_code in (200, 201), t.text
        o = istemci.post("/api/payments", headers=h, json={
            "entity_type": "customer", "entity_id": m.json()["id"], "amount": "10.00",
            "payment_date": "2026-09-01", "account_id": hesap_id,
        })
        assert o.status_code == 201, o.text
        f = istemci.post("/api/finance/transactions", headers=h, json={
            "account_id": hesap_id, "txn_date": "2026-09-01", "direction": "in",
            "amount": "5.00", "description": ad,
        })
        assert f.status_code == 201, f.text

    simdi = datetime.now(timezone.utc)
    with SessionLocal() as db:
        b_cid = db.execute(text(
            "INSERT INTO companies(name,is_active,created_at) VALUES('H57-B',true,:t) RETURNING id"),
            {"t": simdi}).scalar_one()
        for tablo in ("customers", "suppliers"):
            db.execute(text(
                f"INSERT INTO {tablo}(company_id,name,owner_name,opening_balance,is_active)"
                " VALUES(:c,:n,'Mehmet Yılmaz',0,true)"), {"c": b_cid, "n": B_ADI})
        db.commit()
    return {"b_cid": b_cid}


@pytest.fixture(scope="module")
def depo(istemci, admin, tohum):
    """Maskeli rol: arama yalnız ad/yetkili adında (SEC-3b) — katlama orada da işlemeli."""
    from sqlalchemy import text

    from app.auth import hash_password
    from app.db import SessionLocal

    cid = int(admin["X-Company-ID"])
    simdi = datetime.now(timezone.utc)
    with SessionLocal() as db:
        uid = db.execute(text(
            "INSERT INTO app_users(username,email,display_name,password_hash,role,is_active,"
            "must_change_password,email_verified,created_at)"
            " VALUES('h57_depo','h57_depo@ornek.test','H57 depo',:p,'depo',true,false,true,:t)"
            " RETURNING id"), {"p": hash_password(DEPO_PAROLASI), "t": simdi}).scalar_one()
        db.execute(text(
            "INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at)"
            " VALUES(:u,:c,true,:t)"), {"u": uid, "c": cid, "t": simdi})
        db.commit()
    giris = istemci.post("/api/auth/login", json={"username": "h57_depo", "password": DEPO_PAROLASI})
    assert giris.status_code == 200, giris.text
    return {"Authorization": "Bearer " + giris.json()["access_token"], "X-Company-ID": str(cid)}


def _adlar(istemci, basliklar, uc: str, q: str) -> set[str]:
    yanit = istemci.get(uc, headers=basliklar, params={"q": q})
    assert yanit.status_code == 200, (uc, q, yanit.text)
    govde = yanit.json()
    if uc == "/api/search":
        return {x["title"] for x in govde["items"] if x["type"] == "customer"}
    alan = {"/api/payments": "entity_name", "/api/finance/transactions": "description"}.get(uc, "name")
    return {x[alan] for x in govde}


@pytest.mark.parametrize("uc", UCLAR)
def test_tohum_her_ucta_gorunur(istemci, admin, tohum, uc) -> None:
    """Boş sonuçlu iddiaların boşa geçmediğinin kanıtı: "Parça"/"Gıda"/"Ltd"/"Tarım"
    ortak bir harf taşımaz ama her biri kendi sorgusuyla gelir."""
    for ad in ADLAR:
        assert ad in _adlar(istemci, admin, uc, ad), (uc, ad)


@pytest.mark.parametrize("uc", UCLAR)
@pytest.mark.parametrize("q", YILMAZ_YAZIMLARI)
def test_H57_turkce_yazimlarin_hepsi_bulur(istemci, admin, tohum, uc, q) -> None:
    """Tabanda SQLite'ta "YILMAZ"/"Yilmaz"/"yilmaz" 0 satırdı."""
    assert _adlar(istemci, admin, uc, q) == {YILMAZ}


@pytest.mark.parametrize("uc", UCLAR)
def test_H58_yuzde_joker_degil(istemci, admin, tohum, uc) -> None:
    """Tabanda `q=%` firmanın BÜTÜN satırlarını döndürüyordu."""
    assert _adlar(istemci, admin, uc, "%") == set()
    assert _adlar(istemci, admin, uc, "%%%") == set()


@pytest.mark.parametrize("uc", UCLAR)
def test_H58_alt_cizgi_harfi_harfine(istemci, admin, tohum, uc) -> None:
    """`_` tek karakter jokeri değil: OEMX123 eşleşmez."""
    assert _adlar(istemci, admin, uc, "OEM_123") == {"OEM_123 Parça"}
    assert _adlar(istemci, admin, uc, "_") == {"OEM_123 Parça"}


@pytest.mark.parametrize("uc", UCLAR)
def test_H58_ters_bolu_harfi_harfine(istemci, admin, tohum, uc) -> None:
    """`\\` kaçış karakteri olarak yutulmaz: "AB Gıda" eşleşmez."""
    assert _adlar(istemci, admin, uc, "A\\B") == {"A\\B Gıda"}


@pytest.mark.parametrize("uc", UCLAR)
@pytest.mark.parametrize(("ad", "q"), AKSAN_CIFTLERI)
def test_aksanli_yazimlarin_hepsi_bulur(istemci, admin, tohum, uc, ad, q) -> None:
    """0580dcc'de "Kâzım"/"hâlâ"/"Café"/"Émile" BOŞ dönüyordu (tabanda bulunuyordu)."""
    assert _adlar(istemci, admin, uc, q) == {ad}


@pytest.mark.parametrize("uc", UCLAR)
def test_tabanda_bulunan_her_ad_hala_bulunur(istemci, admin, tohum, uc) -> None:
    """Gerilemezlik (istek düzeyi): her adın her 3 harflik penceresi ve her kelimesi
    — tabanda birebir alt dizgiyle bulunan her kalıp — adı hâlâ getirir."""
    for ad in ADLAR:
        kaliplar = {ad[i:i + 3] for i in range(len(ad) - 2)} | set(ad.split())
        for q in sorted(k for k in kaliplar if k.strip()):
            assert ad in _adlar(istemci, admin, uc, q), (uc, ad, q)


@pytest.mark.parametrize("uc", CARI_UCLARI)
def test_B_firmasinin_ayni_sahipli_carisi_gorunmez(istemci, admin, tohum, uc) -> None:
    for q in ("yilmaz", "Mehmet", "firması", "B Firm"):
        assert B_ADI not in _adlar(istemci, admin, uc, q)


@pytest.mark.parametrize("uc", CARI_UCLARI)
@pytest.mark.parametrize("q", YILMAZ_YAZIMLARI)
def test_maskeli_rol_de_katlanmis_arar(istemci, depo, tohum, uc, q) -> None:
    """Maskeli dal (ad + yetkili adı) aynı katlamayı kullanır."""
    assert _adlar(istemci, depo, uc, q) == {YILMAZ}


@pytest.mark.parametrize("uc", ("/api/customers", "/api/suppliers"))
def test_yetkili_adi_ile_de_katlanmis_bulur(istemci, admin, depo, tohum, uc) -> None:
    for basliklar in (admin, depo):
        assert _adlar(istemci, basliklar, uc, "MEHMET YILMAZ") == {YILMAZ}
