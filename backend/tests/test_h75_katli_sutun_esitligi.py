"""H75 — KALICI katlanmış arama sütunları: her yazıcı eşitler, hiçbir yüzey sızdırmaz.

Göç ``20260925_0092`` altı tabloya ``<kolon>_katli`` ekler; arama uçları
artık ``translate`` yerine bu sütunlarda ``LIKE`` yapar. Kural TEK:
``<kolon>_katli == arama_katla(COALESCE(<kolon>, ''))``. Değeri uygulama
tutar (``app/arama_katli.katli_esitle``); tetikleyici yoktur. Bu yüzden
KAÇAN BİR YAZICI sessiz bir arama kaybıdır: kayıt vardır, listede görünür,
ama aranınca BULUNMAZ.

Kapılar:

1. **Eşitlik kapısı** — kaynak kolonu yazan HER üretim yolu gerçek istekle
   koşulur (oluştur + güncelle + içe aktar + POS + virman + çek tahsili +
   ödeme hesabı + satış/alış + silinen kaydı geri yükle) ve sonra altı
   tablonun TAMAMI Python ``arama_katla`` ile karşılaştırılır. MUTASYON: bu
   yollardaki herhangi bir ``katli_esitle`` çağrısını silmek KIRMIZI.
2. **Bayat değer** — ad değiştirilince ESKİ ad artık bulunmaz, YENİ ad bulunur.
3. **Görünmezlik** — maskeli rol ``email_katli``yi ham görmez (SEC-3b);
   değişiklik geçmişi ``_katli`` taşımaz; dışa aktarım/geri yükleme
   ``tests/test_kiraci_geri_yukleme.py::test_H75_*``de.
4. **Göç = kayıt** — göçün kapalı listesi ``KATLI_SUTUNLAR`` ile aynı ve
   katlama tablosu donduruldu (değişirse yeniden doldurma göçü gerekir).

PG ikizi: ``test_h75_katli_sutun_esitligi_postgresql.py``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

_CALISMA_ALANI = tempfile.mkdtemp(prefix="h75-katli-")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    os.path.join(_CALISMA_ALANI, "h75.db").replace(os.sep, "/")
)
os.environ["SUNGUR_DATA_DIR"] = _CALISMA_ALANI
os.environ["AUTO_MIGRATE"] = "true"

from app.arama import HEDEF_HARFLER, KAYNAK_HARFLER, arama_katla  # noqa: E402
from app.arama_katli import KATLI_SUTUNLAR  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260925_0092_arama_katli_sutunlar.py"

ACILIS_PAROLASI = "admin123"
ADMIN_PAROLASI = "H75Katli!2026xyz"
DEPO_PAROLASI = "H75Depo!2026xyz"

#: Katlama tablosunun göç anındaki özeti. Tablo değişirse mevcut satırların
#: `_katli` değerleri ESKİ tabloyla katlanmış kalır: yeni bir göç bütün
#: satırları yeniden doldurmalı, sonra bu özet güncellenmeli.
KATLAMA_OZETI = "fcdd2471b410023c"


def _ozet() -> str:
    return hashlib.sha256((KAYNAK_HARFLER + "|" + HEDEF_HARFLER).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# 4) Göç = kayıt
# --------------------------------------------------------------------------

def _goc_modulu():
    spec = importlib.util.spec_from_file_location("_h75_goc", GOC)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_goc_listesi_KATLI_SUTUNLAR_ile_AYNI() -> None:
    goc = _goc_modulu()
    assert dict(goc._SUTUNLAR) == KATLI_SUTUNLAR
    assert goc.revision == "20260925_0092" and goc.down_revision == "20260920_0091"


def test_katlama_tablosu_donduruldu() -> None:
    assert _ozet() == KATLAMA_OZETI, (
        "ARAMA_ESLESME degisti: mevcut `_katli` degerleri ESKI tabloyla katli. "
        "Butun satirlari yeniden dolduran bir goc yazin, sonra KATLAMA_OZETI'ni guncelleyin."
    )


def test_istek_SQLinde_translate_YOK() -> None:
    for dosya in ("customers.py", "finance.py", "search.py"):
        kaynak = (BACKEND / "app" / "routers" / dosya).read_text(encoding="utf-8")
        assert "translate(" not in kaynak and "katli_sql" not in kaynak, dosya


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


def _ok(yanit, *kodlar):
    assert yanit.status_code in (kodlar or (200, 201)), (yanit.request.url, yanit.text)
    return yanit.json() if yanit.content else None


def _xlsx(basliklar: list[str], satirlar: list[list]) -> bytes:
    from openpyxl import Workbook

    kitap = Workbook()
    sayfa = kitap.active
    sayfa.append(basliklar)
    for satir in satirlar:
        sayfa.append(satir)
    tampon = io.BytesIO()
    kitap.save(tampon)
    return tampon.getvalue()


def _cari(ad: str, **ek) -> dict:
    return {"name": ad, "opening_balance": "0", "risk_limit": "0", "payment_term_days": 0,
            "is_active": True, **ek}


def _urun(ad: str, kod: str, barkod: str) -> dict:
    return {"name": ad, "product_code": kod, "barcode": barkod, "purchase_price": "10",
            "sale_price": "20", "vat_rate": 20, "unit": "Adet"}


_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="module")
def yazilmis(istemci, admin):
    """Kaynak kolonu yazan HER üretim yolu bir kez koşulur."""
    from app.routers.imports import CUSTOMER_HEADERS, PRODUCT_HEADERS

    h = admin
    k = {}
    # Cari / tedarikçi: oluştur + güncelle (aksanlı, küçük harfli, e-postalı).
    k["musteri"] = _ok(istemci.post("/api/customers", headers=h, json=_cari(
        "Kâzım Işık Ltd", owner_name="şükrü öz", email="kazim@örnek.test")))["id"]
    _ok(istemci.put(f"/api/customers/{k['musteri']}", headers=h, json=_cari(
        "Kâzım Işık Ltd. Şti.", owner_name="Şükrü Öztürk", email="Kazim@Örnek.test")))
    k["silinecek"] = _ok(istemci.post("/api/customers", headers=h, json=_cari("Silinip Dönen Çiftçi")))["id"]
    k["tedarikci"] = _ok(istemci.post("/api/suppliers", headers=h, json=_cari(
        "Café Gübre", owner_name="émile", email="cafe@gubre.test")))["id"]
    _ok(istemci.put(f"/api/suppliers/{k['tedarikci']}", headers=h, json=_cari(
        "Café Gübre A.Ş.", owner_name="Émile Çınar", email="Cafe@Gubre.test")))
    # Ürün: oluştur + güncelle.
    k["urun"] = _ok(istemci.post("/api/products", headers=h, json=_urun("Gübre çuvalı", "gb-01", "abc123")))["id"]
    _ok(istemci.put(f"/api/products/{k['urun']}", headers=h, json=_urun("Gübre Çuvalı İthal", "gb-01ı", "abç123")))
    # İçe aktarım kiracı çapında ONARIR; tek kayıt yazıcılarının kaçağını
    # örtmesin diye eşitlik ÖNCE burada ölçülür (mutasyon bataryası ölçtü:
    # bu ara ölçüm yokken dört yazıcının silinmesi YEŞİL kalıyordu).
    # Oluşturma yolu, aynı satırın güncellenmesiyle ÖRTÜLMESİN: hiç
    # güncellenmeyen birer satır daha.
    _ok(istemci.post("/api/customers", headers=h, json=_cari("Dokunulmayan Müşteri", email="d@ö.test")))
    _ok(istemci.post("/api/suppliers", headers=h, json=_cari("Dokunulmayan Tedarikçi", owner_name="öğe")))
    _ok(istemci.post("/api/products", headers=h, json=_urun("Dokunulmayan Ürün", "dk-ı", "dk-ş")))
    k["importtan_once"] = _bayat_satirlar()[0]
    # İçe aktarım: üç uç (yeni satır + var olanın güncellenmesi).
    for yol, basliklar, satirlar in (
        ("/api/imports/customers/excel", CUSTOMER_HEADERS, [["İçe Aktarılan Müşteri", "", "ice@aktar.test", "", "", 0],
                                                      ["Kâzım Işık Ltd. Şti.", "", "yeni@örnek.test", "", "", 0]]),
        ("/api/imports/suppliers/excel", CUSTOMER_HEADERS, [["İçe Aktarılan Tedarikçi", "", "t@aktar.test", "", "", 0]]),
        ("/api/imports/products/excel", PRODUCT_HEADERS, [["İçe Aktarılan Ürün", "ia-1", "ıa-bar", "", 1, 2, 0, "Adet", 20],
                                                    ["Gübre Çuvalı İthal", "gb-01ı", "yeni-barkod-ş", "", 1, 2, 0, "Adet", 20]]),
    ):
        _ok(istemci.post(yol, headers=h, files={"file": ("x.xlsx", _xlsx(basliklar, satirlar), _XLSX)}))
    # Alış (stok) + satış: oluştur + güncelle (belge no yazar).
    kalem = [{"product_id": k["urun"], "quantity": "5", "unit_price": "10", "vat_rate": 20}]
    alis = _ok(istemci.post("/api/purchases", headers=h, json={
        "entity_id": k["tedarikci"], "transaction_date": "2026-09-20", "document_no": "alış-ğ1", "items": kalem}))
    _ok(istemci.put(f"/api/purchases/{alis['id']}", headers=h, json={
        "entity_id": k["tedarikci"], "transaction_date": "2026-09-20", "document_no": "alış-ğ2", "items": kalem}))
    kalem1 = [{"product_id": k["urun"], "quantity": "1", "unit_price": "20", "vat_rate": 20}]
    satis = _ok(istemci.post("/api/orders", headers=h, json={
        "entity_id": k["musteri"], "transaction_date": "2026-09-21", "document_no": "satış-ç1", "items": kalem1}))
    _ok(istemci.put(f"/api/orders/{satis['id']}", headers=h, json={
        "entity_id": k["musteri"], "transaction_date": "2026-09-21", "document_no": "satış-ç2", "items": kalem1,
        "due_date_override_reason": "H75 belge numarası düzeltmesi"}))
    # POS: perakende cari satırı ilk satışta doğar.
    _ok(istemci.post("/api/pos/sale", headers={**h, "Idempotency-Key": "h75-pos-1"}, json={
        "items": [{"product_id": k["urun"], "quantity": "1", "unit_price": "20"}], "payment_type": "cash"}))
    # Finans: hesap, elle hareket, virman, ödeme (hesaplı), çek tahsili.
    kasa = _ok(istemci.post("/api/finance/accounts", headers=h, json={"name": "H75 Kasa"}))["id"]
    banka = _ok(istemci.post("/api/finance/accounts", headers=h, json={"name": "H75 Banka", "account_type": "bank"}))["id"]
    _ok(istemci.post("/api/finance/transactions", headers=h, json={
        "account_id": kasa, "txn_date": "2026-09-22", "direction": "in", "amount": "5.00",
        "description": "Işıl'dan nakit — küçük harf"}))
    _ok(istemci.post("/api/finance/transfers", headers=h, json={
        "source_account_id": kasa, "target_account_id": banka, "amount": "1.00",
        "txn_date": "2026-09-22", "description": "Virman şubeye"}))
    _ok(istemci.post("/api/payments", headers=h, json={
        "entity_type": "customer", "entity_id": k["musteri"], "amount": "3.00",
        "payment_date": "2026-09-22", "account_id": kasa, "note": "tahsilât ödemesi"}))
    cek = _ok(istemci.post("/api/finance/instruments", headers=h, json={
        "instrument_type": "check", "direction": "received", "entity_type": "customer",
        "entity_id": k["musteri"], "amount": 7, "issue_date": "2026-09-01",
        "due_date": "2026-09-23", "serial_no": "H75-ç1"}))["id"]
    _ok(istemci.put(f"/api/finance/instruments/{cek}/status", headers=h, json={
        "status": "collected", "account_id": banka, "transaction_date": "2026-09-23"}))
    # CS1/CS2 çek portföyü: tahsil edilen evrak bankaya finans hareketi yazar
    # (`cek_senet_cari.tahsil_finans_hareketi`).
    from app.business_time import business_today

    bugun = business_today()
    evrak = _ok(istemci.post("/api/cek-senetler", headers=h, json={
        "tur": "cek", "yon": "alinan", "customer_id": k["musteri"], "tutar": "4",
        "vade": bugun.isoformat(), "seri_no": "H75-Ş2"}))["id"]
    _ok(istemci.post(f"/api/cek-senetler/{evrak}/durum-degistir", headers=h, json={"hedef": "tahsile_verildi"}))
    _ok(istemci.post(f"/api/cek-senetler/{evrak}/durum-degistir", headers=h, json={
        "hedef": "tahsil_edildi", "tahsil_hesap_id": banka, "tahsil_tarihi": bugun.isoformat()}))
    # Silinen cariyi değişiklik geçmişinden geri yükle.
    _ok(istemci.delete(f"/api/customers/{k['silinecek']}", headers=h), 204)
    gecmis = _ok(istemci.get(f"/api/history/customer/{k['silinecek']}", headers=h))
    silme = next(x for x in gecmis if x["action"] == "delete")
    k["geri_yuklenen"] = _ok(istemci.post(f"/api/history/restore/{silme['id']}", headers=h))
    return k


def _bayat_satirlar() -> tuple[list, dict]:
    from sqlalchemy import text

    from app.db import SessionLocal

    bayat, sayilar = [], {}
    with SessionLocal() as db:
        for tablo, kolonlar in KATLI_SUTUNLAR.items():
            secim = ",".join(kolonlar) + "," + ",".join(f"{k}_katli" for k in kolonlar)
            satirlar = db.execute(text(f"SELECT id,{secim} FROM {tablo}")).all()
            sayilar[tablo] = len(satirlar)
            for satir in satirlar:
                for i, kolon in enumerate(kolonlar):
                    kaynak, katli = satir[1 + i], satir[1 + len(kolonlar) + i]
                    if katli != arama_katla(kaynak or ""):
                        bayat.append((tablo, satir[0], kolon, kaynak, katli))
    return bayat, sayilar


def test_her_yazicidan_sonra_TUM_tablolar_esit(yazilmis) -> None:
    assert yazilmis["importtan_once"] == []
    bayat, sayilar = _bayat_satirlar()
    assert bayat == []
    # Boş yere yeşil olmasın: her tablo en az bir satır taşımalı.
    assert all(n > 0 for n in sayilar.values()), sayilar


def test_kiraci_esitle_bayat_satiri_onarir(yazilmis, admin) -> None:
    """Kiracı çapındaki yol (içe aktarım/geri yükleme) YALNIZ bayatları yazar
    ama HEPSİNİ onarır: elle bozulan satır eşitlenir."""
    from sqlalchemy import text

    from app.arama_katli import kiraci_katli_esitle
    from app.db import SessionLocal

    cid = int(admin["X-Company-ID"])
    with SessionLocal() as db:
        db.execute(text("UPDATE customers SET name_katli=NULL, email_katli='BOZUK' WHERE company_id=:c"), {"c": cid})
        db.execute(text("UPDATE finance_transactions SET description_katli=NULL WHERE company_id=:c"), {"c": cid})
        db.commit()
    bozuk = {(t, k) for t, _, k, _, _ in _bayat_satirlar()[0]}
    assert {("customers", "name"), ("customers", "email"), ("finance_transactions", "description")} <= bozuk
    with SessionLocal() as db:
        kiraci_katli_esitle(db, cid)
        db.commit()
    assert _bayat_satirlar()[0] == []


def _musteri_adlari(istemci, h, q: str) -> set[str]:
    return {x["name"] for x in _ok(istemci.get("/api/customers", headers=h, params={"q": q}))}


def test_ad_degisince_eski_ad_bulunmaz_yeni_ad_bulunur(istemci, admin, yazilmis) -> None:
    h = admin
    kimlik = _ok(istemci.post("/api/customers", headers=h, json=_cari("Eskiadlı Tarım")))["id"]
    assert "Eskiadlı Tarım" in _musteri_adlari(istemci, h, "ESKIADLI")
    _ok(istemci.put(f"/api/customers/{kimlik}", headers=h, json=_cari("Yeniadlı Tarım")))
    assert _musteri_adlari(istemci, h, "eskiadlı") == set()
    assert _musteri_adlari(istemci, h, "YENİADLI") == {"Yeniadlı Tarım"}
    arama = _ok(istemci.get("/api/search", headers=h, params={"q": "yeniadli"}))
    assert {x["title"] for x in arama["items"] if x["type"] == "customer"} == {"Yeniadlı Tarım"}


def test_geri_yuklenen_kayit_aranir_ve_yanitta_katli_yok(istemci, admin, yazilmis) -> None:
    assert "Silinip Dönen Çiftçi" in _musteri_adlari(istemci, admin, "donen ciftci")
    assert not [a for a in yazilmis["geri_yuklenen"] if a.endswith("_katli")]


def test_degisiklik_gecmisi_katli_TASIMAZ(istemci, admin, yazilmis) -> None:
    gecmis = _ok(istemci.get(f"/api/history/customer/{yazilmis['musteri']}", headers=admin))
    guncelleme = next(x for x in gecmis if x["action"] == "update")
    assert "name" in guncelleme["changed_fields"]["fields"]
    for alan in ("before", "after"):
        assert not [a for a in guncelleme[alan] if a.endswith("_katli")], guncelleme[alan]
    assert not [a for a in guncelleme["changed_fields"]["fields"] if a.endswith("_katli")]


@pytest.fixture(scope="module")
def depo(istemci, admin):
    from sqlalchemy import text

    from app.auth import hash_password
    from app.db import SessionLocal

    cid = int(admin["X-Company-ID"])
    simdi = datetime.now(timezone.utc)
    with SessionLocal() as db:
        uid = db.execute(text(
            "INSERT INTO app_users(username,email,display_name,password_hash,role,is_active,"
            "must_change_password,email_verified,created_at)"
            " VALUES('h75_depo','h75_depo@ornek.test','H75 depo',:p,'depo',true,false,true,:t)"
            " RETURNING id"), {"p": hash_password(DEPO_PAROLASI), "t": simdi}).scalar_one()
        db.execute(text(
            "INSERT INTO user_company_memberships(user_id,company_id,is_default,created_at)"
            " VALUES(:u,:c,true,:t)"), {"u": uid, "c": cid, "t": simdi})
        db.commit()
    giris = istemci.post("/api/auth/login", json={"username": "h75_depo", "password": DEPO_PAROLASI})
    assert giris.status_code == 200, giris.text
    return {"Authorization": "Bearer " + giris.json()["access_token"], "X-Company-ID": str(cid)}


@pytest.mark.parametrize("tablo,yol", [("customers", "/api/customers/{}"), ("suppliers", "/api/suppliers/{}")])
def test_maskeli_rol_email_katliyi_HAM_gormez(istemci, depo, yazilmis, tablo, yol) -> None:
    """SEC-3b: `SELECT *` okuyan cari ucu `email_katli`yi de taşır; maskeli rol
    onu `email` ile AYNI maskeyle görmeli. MUTASYON: `MASKELENEN_ALANLAR`dan
    `email_katli`yi silmek KIRMIZI."""
    from sqlalchemy import text

    from app.alan_maskeleme import maskele_eposta
    from app.db import SessionLocal

    kimlik = yazilmis["musteri" if tablo == "customers" else "tedarikci"]
    with SessionLocal() as db:
        ham = db.execute(text(f"SELECT email_katli FROM {tablo} WHERE id=:i"), {"i": kimlik}).scalar_one()
    assert ham and "@" in ham
    govde = _ok(istemci.get(yol.format(kimlik), headers=depo))
    assert ham not in repr(govde)
    # Kart `SELECT *` satırını `entity` altında döndürür (entity_detail).
    assert govde["entity"]["email_katli"] == maskele_eposta(ham)
