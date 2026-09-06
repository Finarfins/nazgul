"""KİRACI DIŞA AKTARIMI sözleşmesi — ``GET /api/company/export``.

Bu kapının koruduğu iddialar, hepsi SESSİZCE yanlış veri teslim eder:

1. **Üye olmayan hiçbir şey alamaz.** Aksi hâlde başka bir kiracının TÜM
   verisi tek istekle dışarı çıkardı.
2. **Rol EN YÜKSEK olmalı.** ``rapor`` rolü raporu görebilir; firmanın tüm
   defterini indirmek AYNI şey değildir.
3. **Sıra TÜRETİLİR.** Elle yazılmış bir tablo listesi yeni göçlerde sessizce
   eksik kalır ve o tablonun satırları dosyaya HİÇ girmez.
4. **Kiracı sınırı her tabloda.** Tek bir tabloda ``WHERE`` düşerse dosya
   başka firmanın satırlarını taşır — ve bunu kimse fark etmez.
5. **Decimal METİN kalır.** float'a düşen kuruş geri gelmez.
6. **Diskteki eksik ek dışa aktarımı DÜŞÜRMEZ**, ama manifestte GÖRÜNÜR.
7. **Boş firma boş dosya verir**, hata değil.

ÖLÇÜM YÖNTEMİ
-------------
Ağır hazırlık (göçler + iki firmalık tohum + dışa aktarım) TEK kere, modül
kapsamlı bir fixture'da alt süreçte koşar; ``app.config.Settings`` modül
düzeyinde tek kopyadır ve ancak ayrı bir süreçte başka bir ``DATABASE_URL``
görebilir. Üretilen zip ve ölçüm çıktısı diske yazılır, testler onları okur.
Bu sayede on iddia on ayrı testtir ama göçler bir kez koşar.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

#: Bootstrap parolası KODA GÖMÜLMEZ. Alt süreç onu ``settings`` üzerinden
#: okur; sabit ``admin123`` yazan bir giriş, parolayı döndüren başka bir
#: smoke'tan sonra koşduğunda düşerdi.
_ORTAK = r'''
import base64, json, os
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from app.main import app
from app.config import settings
from app.db import engine

YENI_PAROLA = "DisaAktarim!2026"


def admin_headers(client):
    adaylar = (settings.effective_bootstrap_admin_password, YENI_PAROLA, "admin123")
    for aday in adaylar:
        giris = client.post("/api/auth/login",
                            json={"username": "admin", "password": aday})
        if giris.status_code == 200:
            break
    assert giris.status_code == 200, giris.text
    govde = giris.json()
    h = {"Authorization": "Bearer " + govde["access_token"],
         "X-Company-ID": str(govde["companies"][0]["id"])}
    if aday != YENI_PAROLA:
        ch = client.post("/api/auth/change-password", headers=h,
                         json={"current_password": aday, "new_password": YENI_PAROLA})
        assert ch.status_code == 200, ch.text
        h["Authorization"] = "Bearer " + ch.json()["access_token"]
    return h, govde
'''

_HAZIRLIK = _ORTAK + r'''
CIKTI = Path(os.environ["CIKTI_DIZINI"])

with TestClient(app) as client:
    h, govde = admin_headers(client)
    a_id = int(govde["companies"][0]["id"])
    admin_id = int(govde["user"]["id"])

    from sqlalchemy import MetaData
    md = MetaData(); md.reflect(bind=engine)

    # --- İKİNCİ KİRACI ------------------------------------------------
    # Sınır testinin ANLAMLI olması için B firmasının da verisi OLMALI:
    # boş bir B, "A'da B satırı yok" iddiasını bedavaya doğrulardı.
    with engine.begin() as conn:
        b_id = conn.execute(
            insert(md.tables["companies"]).values(
                name="B Kiracısı", is_active=True,
                created_at=datetime.now(timezone.utc))
        ).inserted_primary_key[0]
        conn.execute(insert(md.tables["user_company_memberships"]).values(
            user_id=admin_id, company_id=b_id, is_default=False,
            created_at=datetime.now(timezone.utc)))

    urunler = md.tables["products"]
    hareketler = md.tables["stock_movements"]

    def urun_ekle(conn, cid, kod):
        return conn.execute(insert(urunler).values(
            company_id=cid, product_code=kod, name="Ürün " + kod,
            unit="ADET")).inserted_primary_key[0]

    with engine.begin() as conn:
        a_urun = urun_ekle(conn, a_id, "A-1")
        b_urun = urun_ekle(conn, b_id, "B-1")
        # Decimal ve datetime yuvarlak-yolculuk kanıtı: kuruşu olan bir miktar.
        conn.execute(insert(hareketler).values(
            company_id=a_id, product_id=a_urun, movement_type="IN",
            quantity=Decimal("12.3456"), movement_date="2026-09-05",
            note="A hareketi"))
        conn.execute(insert(hareketler).values(
            company_id=b_id, product_id=b_urun, movement_type="IN",
            quantity=Decimal("99.9999"), movement_date="2026-09-05",
            note="B hareketi"))

    # --- İŞ EMRİ + EKLER ----------------------------------------------
    musteri = client.post("/api/customers", headers=h, json={"name": "Ek Müşterisi"})
    assert musteri.status_code in (200, 201), musteri.text
    makine = client.post("/api/machines", headers=h, json={
        "customer_id": musteri.json()["id"], "brand": "CASE", "model": "CX 8"})
    assert makine.status_code in (200, 201), makine.text
    emir = client.post("/api/work-orders", headers=h, json={
        "machine_id": makine.json()["id"], "customer_id": musteri.json()["id"],
        "technician_id": admin_id})
    assert emir.status_code == 201, emir.text
    emir_id = emir.json()["id"]

    kok = Path(settings.sungur_data_dir).resolve()
    goreli = f"attachments/{a_id}/{emir_id}"
    (kok / goreli).mkdir(parents=True, exist_ok=True)
    (kok / goreli / "var.bin").write_bytes(b"BU DOSYA DISKTE VAR")
    (kok / goreli / "silinmis.bin").write_bytes(b"SILINMIS AMA DISKTE")

    ekler = md.tables["work_order_attachments"]
    simdi = datetime.now(timezone.utc)
    with engine.begin() as conn:
        # (a) diskte VAR olan ek
        conn.execute(insert(ekler).values(
            company_id=a_id, work_order_id=emir_id, file_name="var.bin",
            content_type="application/octet-stream", file_size=19,
            storage_path=goreli + "/var.bin", kind="other",
            uploaded_by=admin_id, created_at=simdi))
        # (b) satırı olan ama diskte OLMAYAN ek
        conn.execute(insert(ekler).values(
            company_id=a_id, work_order_id=emir_id, file_name="yok.bin",
            content_type="application/octet-stream", file_size=7,
            storage_path=goreli + "/yok.bin", kind="other",
            uploaded_by=admin_id, created_at=simdi))
        # (c) SİLİNMİŞ (deleted_at dolu) ama diskte var olan ek
        conn.execute(insert(ekler).values(
            company_id=a_id, work_order_id=emir_id, file_name="silinmis.bin",
            content_type="application/octet-stream", file_size=19,
            storage_path=goreli + "/silinmis.bin", kind="other",
            uploaded_by=admin_id, created_at=simdi, deleted_at=simdi))

    # --- DIŞA AKTARIM --------------------------------------------------
    r = client.get("/api/company/export", headers=h)
    assert r.status_code == 200, r.text[:800]
    (CIKTI / "a.zip").write_bytes(r.content)

    # --- BAĞIMSIZ KAHN SIRASI (testin kendi tanığı) --------------------
    kiraci = {ad for ad, t in md.tables.items() if "company_id" in t.c}
    bagimlilik = {ad: {fk.column.table.name for fk in md.tables[ad].foreign_keys
                       if fk.column.table.name in kiraci
                       and fk.column.table.name != ad}
                  for ad in kiraci}
    katmanlar, bitti, kalan = [], set(), dict(bagimlilik)
    while kalan:
        katman = sorted(a for a, d in kalan.items() if d <= bitti)
        assert katman, "FK grafiğinde DÖNGÜ var: " + repr(sorted(kalan))
        katmanlar.append(katman); bitti |= set(katman)
        kalan = {a: d for a, d in kalan.items() if a not in bitti}

    # --- AKTİVİTE KAYDI -------------------------------------------------
    kayitlar = md.tables["activity_logs"]
    with engine.connect() as conn:
        disa_kayit = conn.execute(
            select(kayitlar).where(kayitlar.c.company_id == a_id,
                                   kayitlar.c.action_type == "company.exported")
        ).mappings().all()

    (CIKTI / "sonuc.json").write_text(json.dumps({
        "a_id": a_id, "b_id": b_id, "emir_id": emir_id,
        "kiraci_tablolar": sorted(kiraci),
        "bagimlilik": {k: sorted(v) for k, v in bagimlilik.items()},
        "katman_sayisi": len(katmanlar),
        "disa_kayit_sayisi": len(disa_kayit),
        "disa_kayit_ornek": [
            {"action_type": x["action_type"], "resource_type": x["resource_type"],
             "user_id": x["user_id"], "summary": x["summary"]}
            for x in disa_kayit],
        "a_hareket_miktar": "12.3456", "b_hareket_miktar": "99.9999",
    }, ensure_ascii=False), encoding="utf-8")
    print("HAZIRLIK TAMAM")
'''


def _kos(betik: str, veritabani: Path, ciktilar: Path, ek_ortam: dict | None = None):
    ortam = os.environ.copy()
    ortam["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["CIKTI_DIZINI"] = str(ciktilar)
    ortam["SUNGUR_DATA_DIR"] = str(ciktilar / "veri")
    (ciktilar / "veri").mkdir(parents=True, exist_ok=True)
    if ek_ortam:
        ortam.update(ek_ortam)
    return subprocess.run(
        [sys.executable, "-c", betik], cwd=BACKEND, env=ortam,
        capture_output=True, text=True, timeout=900,
    )


@pytest.fixture(scope="module")
def hazir(tmp_path_factory) -> dict:
    """Göçleri, tohumu ve dışa aktarımı BİR KEZ koşar."""
    dizin = tmp_path_factory.mktemp("disa-aktarim")
    tamam = _kos(_HAZIRLIK, dizin / "a.db", dizin)
    assert tamam.returncode == 0, tamam.stdout[-4000:] + "\n" + tamam.stderr[-4000:]
    zf = zipfile.ZipFile(io.BytesIO((dizin / "a.zip").read_bytes()))
    return {
        "zip": zf,
        "adlar": zf.namelist(),
        "manifest": json.loads(zf.read("manifest.json")),
        "sonuc": json.loads((dizin / "sonuc.json").read_text(encoding="utf-8")),
    }


def _ndjson(zf: zipfile.ZipFile, tablo: str) -> list[dict]:
    ham = zf.read(f"tables/{tablo}.ndjson").decode("utf-8")
    return [json.loads(s) for s in ham.splitlines() if s.strip()]


# --------------------------------------------------------------------------
# 1) YETKİ — İZİN BAĞLANTISI
# --------------------------------------------------------------------------
def test_izin_adi_yalniz_en_yuksek_rolde() -> None:
    """MUTASYON: ``required_permission``taki ``/api/company/export`` satırını
    silmek ya da dönüşü ``"read"`` yapmak bu testi KIRMIZI yapar."""
    sys.path.insert(0, str(BACKEND))
    from app.auth import ROLE_PERMISSIONS, ROLE_RANK, has_permission, required_permission

    assert required_permission("GET", "/api/company/export") == "__admin_only__"
    # EN YÜKSEK rol: ROLE_RANK'ta admin tek başına en tepede.
    assert max(ROLE_RANK, key=ROLE_RANK.get) == "admin"
    assert has_permission("admin", "__admin_only__")
    for rol in ROLE_PERMISSIONS:
        if rol != "admin":
            assert not has_permission(rol, "__admin_only__"), rol
    # İzin adı hiçbir rol tablosuna YAZILMAMIŞ olmalı; yazılırsa o rol de
    # firmanın tüm defterini indirebilir hâle gelirdi.
    for rol, izinler in ROLE_PERMISSIONS.items():
        if rol != "admin":
            assert "__admin_only__" not in izinler, rol


_UYELIK_YOK = _ORTAK + r'''
with TestClient(app) as client:
    h, govde = admin_headers(client)
    # Üye OLMAYAN bir firma kimliği istenirse üyelik kapısı düşmeli.
    h_yabanci = dict(h); h_yabanci["X-Company-ID"] = "999999"
    r = client.get("/api/company/export", headers=h_yabanci)
    assert r.status_code == 403, r.status_code
    assert r.json().get("code") == "COMPANY_ACCESS_DENIED", r.json()
    print("UYELIK 403 TAMAM")
'''


def test_uye_olmayan_403_alir(tmp_path: Path) -> None:
    """MUTASYON: ``resolve_company``daki ``memberships`` yüklemini kaldırmak
    (ya da uca ``X-Company-ID``yi doğrulamadan güvenmek) bunu KIRMIZI yapar."""
    tamam = _kos(_UYELIK_YOK, tmp_path / "u.db", tmp_path)
    assert tamam.returncode == 0, tamam.stdout[-3000:] + tamam.stderr[-3000:]


_ROL_DUSUK = _ORTAK + r'''
with TestClient(app) as client:
    h, govde = admin_headers(client)
    cid = int(govde["companies"][0]["id"])
    # AYNI firmanın ÜYESİ olan ama rolü daha düşük bir kullanıcı.
    yeni = client.post("/api/users", headers=h, json={
        "username": "raporcu", "password": "Raporcu!2026",
        "display_name": "Raporcu", "role": "rapor"})
    assert yeni.status_code in (200, 201), yeni.text
    giris = client.post("/api/auth/login",
                        json={"username": "raporcu", "password": "Raporcu!2026"})
    assert giris.status_code == 200, giris.text
    g = giris.json()
    hr = {"Authorization": "Bearer " + g["access_token"],
          "X-Company-ID": str(cid)}
    if g.get("user", {}).get("must_change_password"):
        ch = client.post("/api/auth/change-password", headers=hr, json={
            "current_password": "Raporcu!2026", "new_password": "Raporcu!2027"})
        assert ch.status_code == 200, ch.text
        hr["Authorization"] = "Bearer " + ch.json()["access_token"]
    # ÜYE ama rolü yetmiyor: üyelik reddi DEĞİL, yetki reddi almalı.
    r = client.get("/api/company/export", headers=hr)
    assert r.status_code == 403, r.status_code
    assert r.json().get("code") == "PERMISSION_DENIED", r.json()
    print("ROL 403 TAMAM")
'''


def test_dusuk_rollu_uye_403_alir(tmp_path: Path) -> None:
    """MUTASYON: ``__admin_only__`` iznini ``ROLE_PERMISSIONS["rapor"]``a
    eklemek — ya da uç izninin ``"read"``e düşmesi — bunu KIRMIZI yapar."""
    tamam = _kos(_ROL_DUSUK, tmp_path / "r.db", tmp_path)
    assert tamam.returncode == 0, tamam.stdout[-3000:] + tamam.stderr[-3000:]


# --------------------------------------------------------------------------
# 2) MANİFEST VE DOSYA KÜMESİ
# --------------------------------------------------------------------------
def test_manifest_alanlari_ve_sema_seviyesi(hazir) -> None:
    """MUTASYON: ``schema_revision``ı sabit bir dizgeye gömmek ya da
    ``_sema_seviyesi``i ``None`` döndürmek bunu KIRMIZI yapar."""
    m = hazir["manifest"]
    for alan in ("schema_revision", "exported_at", "company_id", "table_order",
                 "row_counts", "attachment_count", "attachments_include_deleted",
                 "missing_attachments", "app_version"):
        assert alan in m, alan
    assert m["company_id"] == hazir["sonuc"]["a_id"]
    # Şema seviyesi VERİTABANINDAN gelir: alembic revizyon biçiminde olmalı.
    assert isinstance(m["schema_revision"], str) and m["schema_revision"]
    assert m["schema_revision"][:4].isdigit(), m["schema_revision"]
    # UTC ve ISO-8601.
    assert m["exported_at"].endswith("+00:00"), m["exported_at"]


def test_yuz_iki_tablo_dosyasi_tam(hazir) -> None:
    """MUTASYON: ``_kiraci_tablolari``yı elle yazılmış kısa bir listeye
    çevirmek (ya da bir tabloyu atlamak) bunu KIRMIZI yapar."""
    from tests.test_tenant_scoping_guard import TENANT_TABLES

    beklenen = {f"tables/{ad}.ndjson" for ad in TENANT_TABLES}
    gorulen = {a for a in hazir["adlar"] if a.startswith("tables/")}
    assert gorulen == beklenen, {
        "eksik": sorted(beklenen - gorulen), "fazla": sorted(gorulen - beklenen)}
    # 109 -> 110: E1b (göç 0072) BİR kiracı tablosu ekledi
    # (`plant_protection_plantbacks`). Bir öncesi
    # 106 -> 109: D2 (göç 0071) ÜÇ kiracı tablosu daha ekledi
    # (`supplier_advances`, `tax_liabilities`,
    # `producer_receipt_exchange_registrations`). Bir öncesi
    # 104 -> 106: müstahsil makbuzu (göç 0070) İKİ tablo ekledi. Bir öncesi
    # 102 -> 104: kantar fişi v2 (PR #45) İKİ kiracı tablosu ekledi. Sayı
    # burada ELLE güncellendi ama uygulama tarafı DOKUNULMADAN doğru
    # çalıştı: `_kiraci_tablolari` kümeyi ŞEMADAN türetiyor, yani yeni
    # tablolar dosyaya kendiliğinden girdi. Elle yazılmış bir liste
    # olsaydı bu iki tablo SESSİZCE dışarıda kalırdı.
    assert len(gorulen) == 110
    # Uygulamanın ŞEMADAN türettiği küme ile kapının listesi AYNI olmalı.
    assert set(hazir["sonuc"]["kiraci_tablolar"]) == set(TENANT_TABLES)
    assert f"companies/{hazir['sonuc']['a_id']}.json" in hazir["adlar"]
    assert "manifest.json" in hazir["adlar"]


# --------------------------------------------------------------------------
# 3) SIRA TÜRETİLİR
# --------------------------------------------------------------------------
def test_tablo_sirasi_topolojik_ve_tam(hazir) -> None:
    """MUTASYON: ``_tablo_sirasi``daki ``sort_tables``ı ``sorted()`` ile
    değiştirmek (alfabetik sıra) bunu KIRMIZI yapar."""
    sonuc = hazir["sonuc"]
    sira = hazir["manifest"]["table_order"]
    assert len(sira) == 110 and len(set(sira)) == 110
    assert set(sira) == set(sonuc["kiraci_tablolar"])

    # Testin KENDİ bağımsız Kahn tanığı: her bağımlılık, bağımlıdan ÖNCE.
    yer = {ad: i for i, ad in enumerate(sira)}
    ihlal = [(ad, bagli) for ad, bagliar in sonuc["bagimlilik"].items()
             for bagli in bagliar if yer[bagli] > yer[ad]]
    assert not ihlal, ihlal
    # 8 -> 9: müstahsil makbuzu (göç 0070) FK grafiğine bir katman daha
    # ekledi. Zinciri UZATAN şey `producer_receipt_items`tır:
    # `producer_receipts`e bağlı, o da `field_harvest_tickets`e (0069'un
    # tablosu, zaten en derin katmandaydı) bağlı — yani makbuz kalemleri
    # fişin BİR ALTINA düşüyor.
    # Bir öncesi 7 -> 8: kantar fişi v2 (PR #45). Sayı ÖLÇÜLDÜ,
    # devralınmadı; testin kendi bağımsız Kahn tanığından geliyor.
    assert sonuc["katman_sayisi"] == 9, sonuc["katman_sayisi"]


# --------------------------------------------------------------------------
# 4) KİRACI SINIRI
# --------------------------------------------------------------------------
def test_hicbir_dosyada_baska_firmanin_satiri_yok(hazir) -> None:
    """MUTASYON: HERHANGİ bir tabloda ``WHERE company_id = :cid`` düşerse
    (``_satirlar``daki ``.where(...)``i kaldırın) bu test o tablonun ADINI
    söyleyerek KIRMIZI olur."""
    a_id = hazir["sonuc"]["a_id"]
    b_id = hazir["sonuc"]["b_id"]
    assert a_id != b_id
    kirli: dict[str, list] = {}
    toplam = 0
    for ad in hazir["sonuc"]["kiraci_tablolar"]:
        for satir in _ndjson(hazir["zip"], ad):
            toplam += 1
            assert "company_id" in satir, ad
            if satir["company_id"] != a_id:
                kirli.setdefault(ad, []).append(satir["company_id"])
    assert not kirli, f"başka firmanın satırlarını taşıyan tablolar: {kirli}"
    # Tarama VAKUM DEĞİL: gerçekten satır görmüş olmalı.
    assert toplam > 0
    # B firmasının hareketi A'nın dosyasında OLMAMALI.
    miktarlar = [s["quantity"] for s in _ndjson(hazir["zip"], "stock_movements")]
    assert hazir["sonuc"]["a_hareket_miktar"] in miktarlar
    assert hazir["sonuc"]["b_hareket_miktar"] not in miktarlar


# --------------------------------------------------------------------------
# 5) SERİLEŞTİRME
# --------------------------------------------------------------------------
def test_decimal_metin_datetime_utc_kalir(hazir) -> None:
    """MUTASYON: ``_seri``deki ``Decimal`` dalını ``float(deger)`` yapmak
    bunu KIRMIZI yapar (metin yerine sayı düşer)."""
    hareketler = _ndjson(hazir["zip"], "stock_movements")
    assert hareketler, "hareket satırı yok — test vakumlaşır"
    hedef = [s for s in hareketler
             if s["quantity"] == hazir["sonuc"]["a_hareket_miktar"]]
    assert hedef, [s["quantity"] for s in hareketler]
    # Decimal METİN olarak durur; float'a düşmüş olsaydı tip int/float olurdu.
    assert isinstance(hedef[0]["quantity"], str)
    # ve bilimsel gösterime kaçmamış olmalı.
    assert "E" not in hedef[0]["quantity"].upper()

    ekler = _ndjson(hazir["zip"], "work_order_attachments")
    assert ekler
    for satir in ekler:
        assert isinstance(satir["created_at"], str)
        assert satir["created_at"].endswith("+00:00"), satir["created_at"]


# --------------------------------------------------------------------------
# 6) EKLER
# --------------------------------------------------------------------------
def test_ekler_kopyalanir_eksik_dosya_dusurmez(hazir) -> None:
    """MUTASYON: ``_uret``teki eksik-dosya dalını ``raise`` yapmak (dışa
    aktarımı düşürmek) ya da eksik eki manifeste yazmamak bunu KIRMIZI yapar."""
    m = hazir["manifest"]
    emir_id = hazir["sonuc"]["emir_id"]
    # Diskte VAR olan ek — silinmiş satırınki DAHİL — zip'e girmiş olmalı.
    assert f"attachments/{emir_id}/var.bin" in hazir["adlar"]
    assert hazir["zip"].read(f"attachments/{emir_id}/var.bin") == b"BU DOSYA DISKTE VAR"
    assert m["attachments_include_deleted"] is True
    assert f"attachments/{emir_id}/silinmis.bin" in hazir["adlar"]
    assert m["attachment_count"] == 2, m["attachment_count"]
    # Diskte OLMAYAN ek: dosya YOK ama manifest onu AÇIKÇA sayıyor.
    assert f"attachments/{emir_id}/yok.bin" not in hazir["adlar"]
    eksik = m["missing_attachments"]
    assert len(eksik) == 1, eksik
    assert eksik[0]["storage_path"].endswith("yok.bin"), eksik
    assert eksik[0]["work_order_id"] == emir_id
    # Satırın KENDİSİ yine de ndjson'da: veritabanı gerçeği kaybolmadı.
    assert len(_ndjson(hazir["zip"], "work_order_attachments")) == 3


# --------------------------------------------------------------------------
# 7) AKTİVİTE KAYDI
# --------------------------------------------------------------------------
def test_disa_aktarim_basina_tek_aktivite_satiri(hazir) -> None:
    """MUTASYON: ``_uret``in sonundaki ``_gunlukle`` çağrısını silmek bunu
    KIRMIZI yapar; çağrıyı kesitten ÖNCE'ye almak ise
    ``test_bos_firma_bos_dosyalar_verir``i kırar."""
    sonuc = hazir["sonuc"]
    assert sonuc["disa_kayit_sayisi"] == 1, sonuc["disa_kayit_sayisi"]
    kayit = sonuc["disa_kayit_ornek"][0]
    assert kayit["action_type"] == "company.exported"
    assert kayit["resource_type"] == "backup"
    assert kayit["user_id"] is not None
    assert kayit["summary"].strip()


# --------------------------------------------------------------------------
# 8) BOŞ FİRMA
# --------------------------------------------------------------------------
_BOS = _ORTAK + r'''
import zipfile, io
CIKTI = Path(os.environ["CIKTI_DIZINI"])
with TestClient(app) as client:
    h, govde = admin_headers(client)
    admin_id = int(govde["user"]["id"])
    from sqlalchemy import MetaData
    md = MetaData(); md.reflect(bind=engine)
    with engine.begin() as conn:
        bos_id = conn.execute(insert(md.tables["companies"]).values(
            name="Bos Kiraci", is_active=True,
            created_at=datetime.now(timezone.utc))).inserted_primary_key[0]
        conn.execute(insert(md.tables["user_company_memberships"]).values(
            user_id=admin_id, company_id=bos_id, is_default=False,
            created_at=datetime.now(timezone.utc)))
    hb = dict(h); hb["X-Company-ID"] = str(bos_id)
    r = client.get("/api/company/export", headers=hb)
    assert r.status_code == 200, r.text[:500]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    man = json.loads(zf.read("manifest.json"))
    ndjson = [a for a in zf.namelist() if a.startswith("tables/")]
    assert len(ndjson) == 110, len(ndjson)
    # ÖLÇÜLDÜ: "tamamen boş" bir firma dışa AKTARILAMAZ. Dışa aktarımın
    # kendisi ÜYELİK ister ve üyelik satırı `user_company_memberships`
    # tablosundadır — yani erişilebilir HER firmada en az bir satır vardır.
    # Bu tabloyu muaf tutmak testi zayıflatmıyor: sayısı TAM 1 olarak
    # doğrulanıyor ve diğer 101 tablonun boş olduğu aynen aranıyor.
    KACINILMAZ = {"user_company_memberships": 1}
    bos_olmayan = {a: zf.read(a).decode("utf-8").count("\n")
                   for a in ndjson if zf.read(a).strip()}
    assert bos_olmayan == {"tables/user_company_memberships.ndjson": 1}, bos_olmayan
    dolu = {k: v for k, v in man["row_counts"].items() if v}
    assert dolu == KACINILMAZ, dolu
    assert man["attachment_count"] == 0
    assert man["company_id"] == bos_id
    print("BOS FIRMA TAMAM")
'''


def test_bos_firma_bos_dosyalar_verir(tmp_path: Path) -> None:
    """MUTASYON: aktivite kaydını kesitten ÖNCE yazmak, ``activity_logs``a
    bir satır düşürür ve ``row_counts`` sıfır olmaktan çıkar → KIRMIZI.
    Boş firmada hata döndürmek de KIRMIZI yapar."""
    tamam = _kos(_BOS, tmp_path / "b.db", tmp_path)
    assert tamam.returncode == 0, tamam.stdout[-3000:] + tamam.stderr[-3000:]


# --------------------------------------------------------------------------
# 9) BELLEK — AKIŞ GERÇEKTEN AKIYOR MU
# --------------------------------------------------------------------------
_BELLEK = _ORTAK + r'''
import tracemalloc, base64
CIKTI = Path(os.environ["CIKTI_DIZINI"])
SATIR = 5000


def olc(client, h):
    """Bir dışa aktarımın gövde boyutunu ve TEPE bellek artışını ölçer."""
    tracemalloc.start()
    tracemalloc.reset_peak()
    taban = tracemalloc.get_traced_memory()[0]
    toplam = 0
    with client.stream("GET", "/api/company/export", headers=h) as r:
        assert r.status_code == 200, r.status_code
        for parca in r.iter_bytes():
            toplam += len(parca)
    tepe = tracemalloc.get_traced_memory()[1] - taban
    tracemalloc.stop()
    return toplam, tepe


with TestClient(app) as client:
    h, govde = admin_headers(client)
    cid = int(govde["companies"][0]["id"])
    from sqlalchemy import MetaData
    md = MetaData(); md.reflect(bind=engine)

    # TABAN: yük eklenmeden önceki dışa aktarım.
    govde1, tepe1 = olc(client, h)

    with engine.begin() as conn:
        urun = conn.execute(insert(md.tables["products"]).values(
            company_id=cid, product_code="BELLEK-1", name="Bellek Ürünü",
            unit="ADET")).inserted_primary_key[0]
        # Notlar SIKIŞTIRILAMAZ olmalı. Tekrarlı bir dolgu ("D"*200) zip
        # içinde eriyip gider ve gövde büyümediği için ölçüm ANLAMSIZLAŞIR:
        # ölçüldü, 5000 satır tekrarlı dolguyla gövde yalnız 43 KB kaldı.
        conn.execute(insert(md.tables["stock_movements"]), [
            {"company_id": cid, "product_id": urun, "movement_type": "IN",
             "quantity": Decimal("1.2345"), "movement_date": "2026-09-05",
             "note": base64.b64encode(os.urandom(150)).decode("ascii")}
            for _ in range(SATIR)])

    # YÜKLÜ: aynı uç, ~1 MB'lık sıkıştırılamaz ek gövde ile.
    govde2, tepe2 = olc(client, h)
    print("GOVDE1", govde1, "TEPE1", tepe1)
    print("GOVDE2", govde2, "TEPE2", tepe2)

    govde_artis = govde2 - govde1
    tepe_artis = tepe2 - tepe1
    print("GOVDE_ARTIS", govde_artis, "TEPE_ARTIS", tepe_artis)

    # NEDEN FARKSAL ÖLÇÜM — SABİT EŞİK KULLANILAMAZ (ölçüldü):
    # tepe belleğin büyük kısmı YÜKTEN BAĞIMSIZ sabit bir maliyettir; 115
    # tablonun `MetaData.reflect`i tek başına ~14 MB ayırıyor. Sabit bir eşik
    # ya bu gürültüye takılıp yanlış kırmızı verir ya da onu örtmek için o
    # kadar gevşer ki asıl aradığımız tamponlamayı kaçırır. Fark, sabit
    # maliyeti İKİ ölçümden de düşer; geriye tek soru kalır: yük büyüyünce
    # bellek de büyüdü mü?
    # ÖLÇÜLDÜ: 5000 satırın sıkıştırılamaz gövdesi ~791 KB büyüttü. Eşik
    # 700 KB — ölçülenin biraz altında, ama "yük gerçekten büyüdü" demeye
    # yetecek kadar yüksek; tohum küçülürse test vakumlaşmadan kırılır.
    assert govde_artis > 700_000, ("gövde beklendiği kadar büyümedi",
                                   govde_artis)
    # AKAN bir uçta tepe, gövdeyle ORANTILI büyümez: tampon eşiği 256 KB ve
    # satırlar 500'lük parçalar hâlinde çekiliyor. Zip'in tamamı bellekte
    # kurulsaydı tepe artışı gövde artışını (>800 KB) izlerdi.
    assert tepe_artis < govde_artis / 2, (
        "tepe bellek gövdeyle birlikte büyüdü — akış tamponlanıyor",
        tepe_artis, govde_artis)
    print("BELLEK TAMAM")
'''


def test_akis_tum_govdeyi_bellekte_tutmaz(tmp_path: Path) -> None:
    """MUTASYON: ``_uret``i ``StreamingResponse`` yerine tüm zip'i bir
    ``BytesIO``da kurup döndürecek şekilde değiştirmek (ya da ``teslim``
    eşiğini sonsuz yapmak) tepe belleği eşiğin üstüne çıkarır → KIRMIZI."""
    tamam = _kos(_BELLEK, tmp_path / "m.db", tmp_path)
    assert tamam.returncode == 0, tamam.stdout[-3000:] + tamam.stderr[-3000:]


# --------------------------------------------------------------------------
# 10) TEK BAĞLANTI / TEK İŞLEM
# --------------------------------------------------------------------------
_ISLEM = _ORTAK + r'''
from sqlalchemy import event
CIKTI = Path(os.environ["CIKTI_DIZINI"])
with TestClient(app) as client:
    h, govde = admin_headers(client)
    olaylar = []
    event.listen(engine, "begin", lambda conn: olaylar.append("begin"))
    event.listen(engine, "commit", lambda conn: olaylar.append("commit"))
    r = client.get("/api/company/export", headers=h)
    assert r.status_code == 200, r.status_code
    print("OLAYLAR", olaylar)
    # ÖLÇÜLDÜ: bir istekte DÖRT işlem açılıyor ve dördü de gerekli —
    # (1) oturum belirtecinin okunması, (2) `resolve_company` üyelik
    # denetimi, (3) dışa aktarımın TEK okuma işlemi, (4) aktivite kaydının
    # kendi yazma işlemi. Tek COMMIT budur; okuma tarafı hiçbir şey yazmaz.
    # Kapının anlamı sayının küçüklüğü DEĞİL, 102 OLMAMASIDIR: tablo başına
    # bağlantı açan bir gerileme burayı 100'ün üstüne çıkarır.
    assert olaylar.count("begin") <= 8, olaylar
    assert olaylar.count("commit") == 1, olaylar
    print("ISLEM TAMAM")
'''


def test_okuma_tek_islemde_yapilir(tmp_path: Path) -> None:
    """MUTASYON: her tablo için ayrı ``engine.connect()``/``begin()`` açmak
    ``begin`` sayısını 100'ün üstüne çıkarır → KIRMIZI."""
    tamam = _kos(_ISLEM, tmp_path / "i.db", tmp_path)
    assert tamam.returncode == 0, tamam.stdout[-3000:] + tamam.stderr[-3000:]


# --------------------------------------------------------------------------
# 11) SONLU OLMAYAN SAYI — ADI KONMUŞ DURUŞ
# --------------------------------------------------------------------------
def test_sonlu_olmayan_sayi_adi_konmus_hata_firlatir() -> None:
    """MUTASYON: ``_seri``deki ``if not deger.is_finite()`` dalını silmek bunu
    KIRMIZI yapar (NaN sessizce ``json.dumps``a gider ve standart DIŞI ``NaN``
    sözcüğü yazılır)."""
    sys.path.insert(0, str(BACKEND))
    from decimal import Decimal

    from app.disa_aktarim_errors import DisaAktarimError, SonluOlmayanSayiError
    from app.routers.kiraci_disa_aktarim import _seri

    for bozuk in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
        with pytest.raises(SonluOlmayanSayiError) as yakalanan:
            _seri(bozuk, "stock_movements", "quantity")
        hata = yakalanan.value
        # KOD SÖZLEŞMEDİR: istemci METNİ ayrıştırmak zorunda değil.
        assert hata.kod == "EXPORT_NON_FINITE_NUMBER"
        # Aile: çıplak bir RuntimeError değil, dışa aktarım ailesinden.
        assert isinstance(hata, DisaAktarimError)
        # Hangi sütun olduğu mesajda ADIYLA duruyor.
        assert "stock_movements.quantity" in str(hata)

    # SONLU değerler bu daldan ETKİLENMEZ.
    assert _seri(Decimal("12.3456"), "stock_movements", "quantity") == "12.3456"


def test_sonlu_olmayan_sayi_500_ve_kararli_kod_dondurur() -> None:
    """MUTASYON: ``main.py``deki ``@app.exception_handler(DisaAktarimError)``
    kaydını silmek bunu KIRMIZI yapar (hata çıplak biçimde yukarı sızar,
    gövdede kod kalmaz)."""
    import asyncio
    from decimal import Decimal

    sys.path.insert(0, str(BACKEND))
    from app.disa_aktarim_errors import DisaAktarimError, SonluOlmayanSayiError
    from app.main import app

    isleyici = app.exception_handlers.get(DisaAktarimError)
    assert isleyici is not None, "adı konmuş hata için işleyici KAYITLI DEĞİL"

    hata = SonluOlmayanSayiError("stock_movements", "quantity", Decimal("NaN"))
    yanit = asyncio.run(isleyici(None, hata))
    assert yanit.status_code == 500
    govde = json.loads(bytes(yanit.body).decode("utf-8"))
    assert govde["code"] == "EXPORT_NON_FINITE_NUMBER", govde
    assert govde["detail"].strip(), govde


_SQLITE_NAN = _ORTAK + r'''
from sqlalchemy.exc import IntegrityError
from sqlalchemy import MetaData
CIKTI = Path(os.environ["CIKTI_DIZINI"])
with TestClient(app) as client:
    h, govde = admin_headers(client)
    cid = int(govde["companies"][0]["id"])
    md = MetaData(); md.reflect(bind=engine)
    with engine.begin() as conn:
        urun = conn.execute(insert(md.tables["products"]).values(
            company_id=cid, product_code="NAN-1", name="NaN Urunu",
            unit="ADET")).inserted_primary_key[0]
    # SQLite NaN'i SAKLAYAMAZ: surucu Decimal("NaN")i `nan`a cevirir, SQLite
    # onu NULL yapar ve NOT NULL kisiti duser. Uctan uca NaN senaryosu bu
    # yuzden YALNIZ PostgreSQL'de kurulabilir (`numeric` 'NaN' kabul eder).
    hata = None
    try:
        with engine.begin() as conn:
            conn.execute(insert(md.tables["stock_movements"]).values(
                company_id=cid, product_id=urun, movement_type="IN",
                quantity=Decimal("NaN"), movement_date="2026-09-05",
                note="nan"))
    except IntegrityError as exc:
        hata = str(exc)
    assert hata is not None, "SQLite NaN'i SAKLADI — olcum gecersiz"
    assert "NOT NULL" in hata and "quantity" in hata, hata
    print("SQLITE NAN TAMAM")
'''


def test_sqlite_nan_saklayamaz_bu_yuzden_uctan_uca_pg_isi(tmp_path: Path) -> None:
    """SQLite'ta NaN yolunun neden ÖLÇÜLEMEDİĞİNİ kayda geçirir.

    Bu bir mazeret değil, ÖLÇÜM: sürücü ``Decimal("NaN")``ı ``nan``a çevirir,
    SQLite onu ``NULL`` yapar ve ``NOT NULL`` kısıtı düşer. Uçtan uca senaryo
    ``numeric`` türü ``'NaN'`` kabul eden PostgreSQL'de kurulabilir ve o tur
    ERTELENDİ (bkz. PR gövdesindeki ÖLÇÜLMEDİ listesi).

    MUTASYON: SQLite bir gün NaN saklamaya başlarsa ``assert hata is not None``
    KIRMIZI olur ve bu notun geçersizleştiği anda haber alırız."""
    tamam = _kos(_SQLITE_NAN, tmp_path / "n.db", tmp_path)
    assert tamam.returncode == 0, tamam.stdout[-3000:] + tamam.stderr[-3000:]
