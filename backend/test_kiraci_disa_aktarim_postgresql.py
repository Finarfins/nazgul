"""PostgreSQL ikizi: 5.1a kiracı dışa aktarımının (routers/kiraci_disa_aktarim.py) GERÇEK veritabanında doğrulanması.

Bu test, SQLite ikizinde (tests/test_kiraci_disa_aktarim.py) ölçülen dışa aktarım sözleşmesini
gerçek PostgreSQL 16 üzerinde ve tüm kiracı tabloları (TENANT_TABLES) tohumlanmış bir firma ile doğrular:

1. **Tüm kiracı tabloları ve tam satır sayıları:** Dışa aktarılan manifest'teki ``row_counts``,
   PostgreSQL'deki GERÇEK satır sayılarına BİREBİR eşittir (tüm kiracı tabloları tohumludur).
2. **Kullanıcı e-posta haritası en az ve yalnız üyeler:** ``user_emails`` yalnız firmanın
   aktif üyelerini (admin, aktif_uye) taşır; ayrılmış üye (ayrilmis) ve başka firma üyesi (ekili_b)
   ve hiçbir yerde anılmayan kullanıcı harita dışındadır. Parola özeti zip'e sızmaz.
3. **JSON sütunları gerçek JSON:** ``supplier_import_profiles`` içindeki ``column_map`` (dict) ve
   ``page_sections`` (list) gerçek JSON yapıları olarak serileştirilir; Python repr'i (tek tırnak, True/None) yazılmaz.
4. **Zip dosya adlarında e-posta yok:** Zip içindeki hiçbir dosya veya dizin yolu kullanıcı e-postası içermez.
5. **Kiracı izolasyonu:** Başka firmanın (B) verisi veya kimliği A'nın dışa aktarımına karışmaz.
6. **Decimal metin ve datetime UTC:** Ondalık sayılar kuruş kayıpsız metin kalır, zaman damgaları UTC kalır.
7. **Ekler ve manifest:** Depodaki ekler zip'e yazılır, eksik ekler manifestte kayıt altına alınır.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

if os.environ.get("APP_TEST_DATABASE_URL", "").startswith("postgresql"):
    os.environ["DATABASE_URL"] = os.environ["APP_TEST_DATABASE_URL"]
elif os.environ.get("DATABASE_URL", "").startswith("postgresql"):
    os.environ["APP_TEST_DATABASE_URL"] = os.environ["DATABASE_URL"]


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    if not url.startswith("postgresql"):
        if os.environ.get("REQUIRE_PG") == "1":
            pytest.fail("REQUIRE_PG=1 ancak PostgreSQL URL bulunamadı")
        pytest.skip("PostgreSQL ikizi APP_TEST_DATABASE_URL veya DATABASE_URL ister")
    return url


def _gy_modul():
    spec = importlib.util.spec_from_file_location(
        "_gy_sqlite", BACKEND / "tests" / "test_kiraci_geri_yukleme.py"
    )
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


_HAZIRLIK_GOVDESI = r'''
from app.main import _semayi_hazirla
with engine.begin() as conn:
    if conn.dialect.name == "postgresql":
        conn.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
_semayi_hazirla()

with TestClient(app) as client:
    h, govde = admin_headers(client)
    a_id = int(govde["companies"][0]["id"])
    admin_id = int(govde["user"]["id"])
    md = semayi_yansit()

    # --- 1) İKİNCİ FİRMA (B) ---
    with engine.begin() as conn:
        b_id = conn.execute(insert(md.tables["companies"]).values(
            name="B Kiracisi", is_active=True,
            created_at=datetime.now(timezone.utc))).inserted_primary_key[0]
        conn.execute(insert(md.tables["user_company_memberships"]).values(
            user_id=admin_id, company_id=b_id, is_default=False,
            created_at=datetime.now(timezone.utc)))

    # --- 2) KULLANICILAR VE ÜYELİKLER ---
    kullanicilar = md.tables["app_users"]
    uyelikler = md.tables["user_company_memberships"]
    GIZLI_PAROLA = "H49-GIZLI-PAROLA-OZETI-SIZMAMALI"

    def kullanici_ekle(conn, ad):
        return conn.execute(insert(kullanicilar).values(
            username=ad, email=ad + "@ornek.invalid", email_verified=False,
            display_name=ad, password_hash=GIZLI_PAROLA, role="rapor", is_active=True,
            created_at=datetime.now(timezone.utc), must_change_password=False,
        )).inserted_primary_key[0]

    with engine.begin() as conn:
        aktif_uye = kullanici_ekle(conn, "aktifuye")
        ayrilmis = kullanici_ekle(conn, "ayrilmis")
        ekili_b = kullanici_ekle(conn, "ekilib")
        hic_anilmayan = kullanici_ekle(conn, "hicanilmayan")
        conn.execute(insert(uyelikler).values(
            user_id=aktif_uye, company_id=a_id, is_default=False,
            created_at=datetime.now(timezone.utc)))
        conn.execute(insert(uyelikler).values(
            user_id=ekili_b, company_id=b_id, is_default=False,
            created_at=datetime.now(timezone.utc)))
        admin_eposta = conn.execute(
            select(kullanicilar.c.email).where(kullanicilar.c.id == admin_id)
        ).scalar_one()

    # --- 3) ANLAMSAL TOHUM (Ekler, İş Emirleri) ---
    musteri = client.post("/api/customers", headers=h, json={"name": "Ek Müşterisi"})
    assert musteri.status_code in (200, 201), musteri.text
    musteri_id = musteri.json()["id"]
    makine = client.post("/api/machines", headers=h, json={
        "customer_id": musteri_id, "brand": "CASE", "model": "CX 8"})
    assert makine.status_code in (200, 201), makine.text
    emir = client.post("/api/work-orders", headers=h, json={
        "machine_id": makine.json()["id"], "customer_id": musteri_id,
        "technician_id": admin_id})
    assert emir.status_code == 201, emir.text
    emir_id = emir.json()["id"]

    kok = Path(settings.sungur_data_dir).resolve()
    goreli = f"attachments/{a_id}/{emir_id}"
    (kok / goreli).mkdir(parents=True, exist_ok=True)
    (kok / goreli / "var.bin").write_bytes(b"BU DOSYA DISKTE VAR")

    ekler = md.tables["work_order_attachments"]
    simdi = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(insert(ekler).values(
            company_id=a_id, work_order_id=emir_id, file_name="var.bin",
            content_type="application/octet-stream", file_size=19,
            storage_path=goreli + "/var.bin", kind="other",
            uploaded_by=ekili_b, created_at=simdi))
        conn.execute(insert(ekler).values(
            company_id=a_id, work_order_id=emir_id, file_name="yok.bin",
            content_type="application/octet-stream", file_size=7,
            storage_path=goreli + "/yok.bin", kind="other",
            uploaded_by=ayrilmis, created_at=simdi))
        conn.execute(md.tables["work_orders"].update().where(
            md.tables["work_orders"].c.id == emir_id
        ).values(created_by=ekili_b))

    # --- 4) ÜRÜNLER, STOK, DECIMAL KANITI ---
    urunler = md.tables["products"]
    partiler = md.tables["product_lots"]
    hareketler = md.tables["stock_movements"]
    depolar = md.tables["warehouses"]
    with engine.begin() as conn:
        depo_a = conn.execute(insert(depolar), zorunlu(depolar, {"company_id": a_id, "name": "Ana Depo A"})).inserted_primary_key[0]
        depo_b = conn.execute(insert(depolar), zorunlu(depolar, {"company_id": b_id, "name": "Ana Depo B"})).inserted_primary_key[0]
        u1 = conn.execute(insert(urunler), zorunlu(urunler, {"company_id": a_id, "product_code": "A-1", "name": "Ürün A-1", "unit": "ADET"})).inserted_primary_key[0]
        u2 = conn.execute(insert(urunler), zorunlu(urunler, {"company_id": a_id, "product_code": "A-2", "name": "Ürün A-2", "unit": "ADET"})).inserted_primary_key[0]
        ub = conn.execute(insert(urunler), zorunlu(urunler, {"company_id": b_id, "product_code": "B-1", "name": "Ürün B-1", "unit": "ADET"})).inserted_primary_key[0]

        for kod, urun in (("P-1", u1), ("P-2", u1), ("P-3", u2)):
            conn.execute(insert(partiler), zorunlu(partiler, {
                "company_id": a_id, "product_id": urun, "lot_code": kod, "warehouse_id": depo_a,
                "quantity": Decimal("5"), "created_at": datetime(2026, 9, 1)}))
        conn.execute(insert(hareketler), zorunlu(hareketler, {
            "company_id": a_id, "product_id": u1, "movement_type": "IN",
            "quantity": Decimal("12.3456"), "movement_date": "2026-09-05", "note": "A hareketi"}))
        conn.execute(insert(hareketler), zorunlu(hareketler, {
            "company_id": b_id, "product_id": ub, "movement_type": "IN",
            "quantity": Decimal("99.9999"), "movement_date": "2026-09-05", "note": "B hareketi"}))

    # --- 5) FATURA, İRSALİYE, YANIT, ÖDEME ---
    faturalar = md.tables["invoices"]
    kalemler = md.tables["invoice_items"]
    with engine.begin() as conn:
        fat_id = conn.execute(insert(faturalar), zorunlu(faturalar, {
            "company_id": a_id, "invoice_number": "GY-2026-1", "created_by": admin_id,
            "totals_snapshot": json.dumps({"total": "300.00"}),
            "created_at": datetime(2026, 9, 1), "updated_at": datetime(2026, 9, 1)})).inserted_primary_key[0]
        kalem_idleri = []
        for i, tutar in enumerate((Decimal("100.00"), Decimal("200.00")), 1):
            k_id = conn.execute(insert(kalemler), zorunlu(kalemler, {
                "company_id": a_id, "invoice_id": fat_id, "item_type": "part",
                "quantity": Decimal("1"), "unit_price": tutar, "original_price": tutar,
                "discount_amount": Decimal("0"), "tax_rate": Decimal("0"), "tax_amount": Decimal("0"),
                "total": tutar, "warranty_percent": Decimal("0"), "customer_payable": tutar,
                "company_payable": Decimal("0"), "description": f"Kalem {i}", "source_snapshot": "{}"})).inserted_primary_key[0]
            kalem_idleri.append(k_id)

    irsaliyeler = md.tables["despatch_notes"]
    sevk_satirlari = md.tables["despatch_lines"]
    with engine.begin() as conn:
        irs_id = conn.execute(insert(irsaliyeler), zorunlu(irsaliyeler, {
            "company_id": a_id, "invoice_id": fat_id,
            "despatch_uuid": "00000000-0000-4000-8000-00000000e4b1",
            "despatch_number": "IRS2026000000901", "issue_date": date(2026, 9, 15),
            "actual_shipment_at": datetime(2026, 9, 15, 8, 0), "driver_name": "Sofor",
            "driver_national_id": "11111111110", "vehicle_plate": "34ABC123",
            "delivery_address": "Adres", "delivery_postal_code": "34000",
            "edespatch_status": "NONE", "created_at": datetime(2026, 9, 15),
            "updated_at": datetime(2026, 9, 15)})).inserted_primary_key[0]
        sevk_satir_idleri = []
        for no, (kalem, urun, miktar) in enumerate(
                ((kalem_idleri[0], u1, Decimal("0.4")), (kalem_idleri[1], u2, Decimal("1"))), 1):
            sevk_satir_idleri.append(conn.execute(insert(sevk_satirlari), zorunlu(sevk_satirlari, {
                "company_id": a_id, "despatch_id": irs_id, "invoice_item_id": kalem,
                "line_no": no, "product_id": urun, "item_name": f"Sevk {no}",
                "quantity": miktar, "unit_code": "C62",
                "created_at": datetime(2026, 9, 15), "updated_at": datetime(2026, 9, 15)})).inserted_primary_key[0])

    yanitlar = md.tables["despatch_responses"]
    yanit_satirlari = md.tables["despatch_response_lines"]
    with engine.begin() as conn:
        yanit_id = conn.execute(insert(yanitlar), zorunlu(yanitlar, {
            "company_id": a_id, "despatch_id": irs_id,
            "response_uuid": "00000000-0000-4000-8000-00000000e4b2",
            "response_number": "ALC2026000000901", "response_type": "KISMI_KABUL",
            "issue_date": date(2026, 9, 16), "raw_xml": "<ReceiptAdvice/>",
            "created_at": datetime(2026, 9, 16)})).inserted_primary_key[0]
        for sevk_satiri, alinan, reddedilen in (
                (sevk_satir_idleri[0], Decimal("0.4"), Decimal("0")),
                (sevk_satir_idleri[1], Decimal("0.25"), Decimal("0.75"))):
            conn.execute(insert(yanit_satirlari), zorunlu(yanit_satirlari, {
                "company_id": a_id, "response_id": yanit_id, "despatch_line_id": sevk_satiri,
                "received_quantity": alinan, "rejected_quantity": reddedilen}))

    odemeler = md.tables["payments"]
    tahsisler = md.tables["payment_allocations"]
    siparisler = md.tables["orders"]
    with engine.begin() as conn:
        sip_id = conn.execute(insert(siparisler), zorunlu(siparisler, {
            "company_id": a_id, "customer_id": musteri_id, "warehouse_id": depo_a})).inserted_primary_key[0]
        od_id = conn.execute(insert(odemeler), zorunlu(odemeler, {
            "company_id": a_id, "entity_type": "customer", "entity_id": musteri_id,
            "amount": Decimal("300.00"), "payment_date": "2026-09-05"})).inserted_primary_key[0]
        for tutar in (Decimal("120.00"), Decimal("180.00")):
            conn.execute(insert(tahsisler), zorunlu(tahsisler, {
                "company_id": a_id, "payment_id": od_id, "order_id": sip_id,
                "amount": tutar, "allocation_type": "manual", "effective_date": date(2026, 9, 5),
                "created_by": admin_id}))

    belgeler = md.tables["receivable_charge_documents"]
    try:
        with engine.begin() as conn:
            conn.execute(insert(belgeler), zorunlu(belgeler, {
                "company_id": a_id, "customer_id": musteri_id, "work_order_id": emir_id,
                "charge_type": "service_fee", "status": "draft", "revision_no": 1,
                "gross_amount": Decimal("50"), "net_amount": None, "vat_amount": None,
                "period_start": date(2026, 9, 1), "period_end": date(2026, 9, 30),
                "document_no": "SRV-1", "created_by": admin_id,
                "created_at": datetime(2026, 9, 1), "updated_at": datetime(2026, 9, 1)}))
    except (IntegrityError, StatementError):
        pass

    # --- 6) JSON SÜTUNLARI: supplier_import_profiles ---
    JSON_KOLON = {"sku": "A", "fiyat": 3, "aktif": True, "bos": None,
                  "ic": {"liste": [1, "iki"]}}
    JSON_SAYFA = [{"sayfa": 1, "baslik": "Fiyat Listesi"}]
    with engine.begin() as conn:
        ted = conn.execute(insert(md.tables["suppliers"]).values(
            company_id=a_id, name="JSON Tedarikcisi")).inserted_primary_key[0]
        conn.execute(insert(md.tables["supplier_import_profiles"]).values(
            company_id=a_id, supplier_id=ted, name="H49 Profili",
            sheet_selector="first", header_row_strategy="auto",
            column_map=JSON_KOLON, page_sections=JSON_SAYFA,
            currency_mode="TRY", term_days=0, vat_source="excluded",
            created_at=simdi, updated_at=simdi))

    # --- 7) JENERİK TOHUM: TÜM KİRACI TABLOLARI DOLDURULUR ---
    basarisiz = generik_tohum(md, a_id, admin_id)
    assert not basarisiz, f"Jenerik tohum başarısız tablolar: {basarisiz}"

    # PostgreSQL'deki kesin satır sayımları
    pg_sayimlar = sayimlar(md, a_id)
    b_pg_sayimlar = sayimlar(md, b_id)

    # --- 8) DIŞA AKTARIM ---
    r = client.get("/api/company/export", headers=h)
    assert r.status_code == 200, r.text[:800]
    zip_bytes = r.content
    (CIKTI / "a.zip").write_bytes(zip_bytes)
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    manifest = json.loads(zf.read("manifest.json"))

    yaz("sonuc.json", {
        "a_id": a_id, "b_id": b_id, "admin_id": admin_id, "emir_id": emir_id,
        "aktif_uye": aktif_uye, "ayrilmis": ayrilmis, "ekili_b": ekili_b, "hic_anilmayan": hic_anilmayan,
        "admin_eposta": admin_eposta, "aktif_uye_eposta": "aktifuye@ornek.invalid",
        "ayrilmis_eposta": "ayrilmis@ornek.invalid", "ekili_b_eposta": "ekilib@ornek.invalid",
        "gizli_parola": GIZLI_PAROLA,
        "json_kolon": JSON_KOLON, "json_sayfa": JSON_SAYFA,
        "pg_sayimlar": pg_sayimlar, "b_pg_sayimlar": b_pg_sayimlar,
        "manifest": manifest,
        "zip_namelist": zf.namelist(),
    })
    print("HAZIRLIK TAMAM")
'''


@pytest.fixture(scope="module")
def hazir(tmp_path_factory) -> dict:
    url = _url()
    ikiz = _gy_modul()
    dizin = tmp_path_factory.mktemp("disa-aktarim-pg")
    tamam = ikiz._kos(ikiz._ORTAK + _HAZIRLIK_GOVDESI, dizin / "unused.db", dizin, url=url)
    ikiz._basarili(tamam)
    sonuc = json.loads((dizin / "sonuc.json").read_text(encoding="utf-8"))
    zip_bytes = (dizin / "a.zip").read_bytes()
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    return {
        "sonuc": sonuc,
        "manifest": sonuc["manifest"],
        "zip": zf,
        "zip_bytes": zip_bytes,
        "_url": url,
    }


def test_pg_disa_aktarim_tum_kiraci_tablolari_satir_sayilari_esit(hazir) -> None:
    """Manifest row_counts PostgreSQL'deki GERÇEK satır sayılarına eşittir.

    Tüm kiracı tabloları tohumlanmıştır (sayı > 0) ve tek bir tablo bile eksik kalamaz.
    """
    manifest_counts = hazir["manifest"]["row_counts"]
    pg_sayimlar = hazir["sonuc"]["pg_sayimlar"]

    # __companies__ kiracı tablosu değil küme toplamıdır
    beklenen_tablolar = {t for t in pg_sayimlar.keys() if t != "__companies__"}
    assert set(manifest_counts.keys()) == beklenen_tablolar, (
        f"Eksik: {sorted(beklenen_tablolar - set(manifest_counts.keys()))}, "
        f"Fazla: {sorted(set(manifest_counts.keys()) - beklenen_tablolar)}"
    )

    for tablo in beklenen_tablolar:
        pg_sayi = pg_sayimlar[tablo]
        man_sayi = manifest_counts[tablo]
        assert pg_sayi > 0, f"{tablo} PostgreSQL'de tohumlanmamış (0 satır)"
        assert man_sayi == pg_sayi, (
            f"{tablo} satır sayısı uyuşmuyor: manifest={man_sayi}, pg={pg_sayi}"
        )


def test_pg_disa_aktarim_kullanici_epostalari_yalniz_aktif_uyeler(hazir) -> None:
    """Kullanıcı e-posta haritası YALNIZ firmanın aktif üyelerini içerir.

    Ayrılmış üye (ayrilmis) ve başka firmanın üyesi (ekili_b) A'nın satırlarında
    anılsa bile e-posta haritasına GİREMEZ. Parola özeti hiçbir yere sızmaz.
    """
    s = hazir["sonuc"]
    user_emails = hazir["manifest"]["user_emails"]
    assert isinstance(user_emails, list), user_emails

    for item in user_emails:
        assert set(item.keys()) == {"id", "email"}, item

    ids = [item["id"] for item in user_emails]
    assert ids == sorted(ids), "user_emails id'ye göre sıralı olmalı"
    assert len(ids) == len(set(ids)), "Tekrarlanan id olamaz"

    # Yalnız firmanın üyeleri
    assert set(ids) == {s["admin_id"], s["aktif_uye"]}, ids
    epostalar = {item["id"]: item["email"] for item in user_emails}
    assert epostalar[s["admin_id"]] == s["admin_eposta"]
    assert epostalar[s["aktif_uye"]] == s["aktif_uye_eposta"]

    # Ayrılmış ve başka firma üyeleri HARİÇ TUTULMALI
    assert s["ayrilmis"] not in ids, "Ayrılmış üye e-posta haritasında bulunamaz"
    assert s["ekili_b"] not in ids, "Başka firma üyesi e-posta haritasında bulunamaz"
    assert s["hic_anilmayan"] not in ids, "İlişkisiz kullanıcı e-posta haritasında bulunamaz"

    # Parola özeti zip dosyasının hiçbir baytında geçmemeli
    assert s["gizli_parola"].encode("utf-8") not in hazir["zip_bytes"]


def test_pg_disa_aktarim_json_sutunlari_gercek_json(hazir) -> None:
    """_seri JSON sütunlarını (dict/list) gerçek JSON olarak yazar.

    Python repr'i (tek tırnaklı anahtarlar, True, None) yazılmaz; ndjson satırı
    json.loads ile açıldığında değerler doğrudan dict ve list olarak gelir.
    """
    zf = hazir["zip"]
    ham = zf.read("tables/supplier_import_profiles.ndjson").decode("utf-8")
    satirlar = [s for s in ham.splitlines() if s.strip()]
    assert len(satirlar) >= 1, satirlar

    profil = json.loads(satirlar[0])
    assert isinstance(profil["column_map"], dict), profil["column_map"]
    assert profil["column_map"] == hazir["sonuc"]["json_kolon"]
    assert isinstance(profil["page_sections"], list), profil["page_sections"]
    assert profil["page_sections"] == hazir["sonuc"]["json_sayfa"]

    # Ham satırda Python repr sözcükleri veya tek tırnaklar bulunmamalı
    assert "'" not in satirlar[0], satirlar[0]
    for yasak in ("True", "None", "False"):
        assert yasak not in satirlar[0], (yasak, satirlar[0])


def test_pg_disa_aktarim_zip_dosya_adlarinda_eposta_yok(hazir) -> None:
    """Zip içindeki dosya ve dizin adlarında hiçbir kullanıcının e-postası bulunmaz."""
    s = hazir["sonuc"]
    namelist = hazir["sonuc"]["zip_namelist"]
    epostalar = [
        s["admin_eposta"],
        s["aktif_uye_eposta"],
        s["ayrilmis_eposta"],
        s["ekili_b_eposta"],
    ]

    for dosya_yolu in namelist:
        assert "@" not in dosya_yolu, f"Dosya yolunda '@' karakteri sızdı: {dosya_yolu}"
        for ep in epostalar:
            assert ep not in dosya_yolu, f"Dosya yolunda e-posta bulundu: {dosya_yolu} ({ep})"


def test_pg_disa_aktarim_baska_firmanin_verisi_yok(hazir) -> None:
    """Kiracı izolasyonu: B firmasının hiçbir satırı veya dosyası A'nın zip'ine karışmaz."""
    zf = hazir["zip"]
    a_id = hazir["sonuc"]["a_id"]
    b_id = hazir["sonuc"]["b_id"]

    for dosya_yolu in zf.namelist():
        if dosya_yolu.startswith("tables/") and dosya_yolu.endswith(".ndjson"):
            icerik = zf.read(dosya_yolu).decode("utf-8")
            for satir in icerik.splitlines():
                if not satir.strip():
                    continue
                kayit = json.loads(satir)
                if "company_id" in kayit:
                    assert kayit["company_id"] == a_id, (
                        f"{dosya_yolu} içinde başka firma satırı: {kayit['company_id']} != {a_id}"
                    )

    assert f"companies/{a_id}.json" in zf.namelist()
    assert f"companies/{b_id}.json" not in zf.namelist()


def test_pg_disa_aktarim_decimal_ve_datetime_utc(hazir) -> None:
    """Decimal alanlar kuruş kayıpsız metin kalır, datetime UTC ISO formatındadır."""
    zf = hazir["zip"]
    icerik = zf.read("tables/stock_movements.ndjson").decode("utf-8")
    hareketler = [json.loads(s) for s in icerik.splitlines() if s.strip()]
    assert any(h.get("quantity") == "12.3456" for h in hareketler), (
        f"Kuruşlu miktar 12.3456 metin olarak bulunamadı: {hareketler}"
    )

    for h in hareketler:
        dt = h.get("created_at")
        if dt:
            assert dt.endswith("+00:00") or dt.endswith("Z"), f"UTC olmayan damga: {dt}"


def test_pg_disa_aktarim_ekler_ve_manifest_yapisi(hazir) -> None:
    """Ekler diske uygun kopyalanır, eksik ek manifestte kayıt altına alınır."""
    manifest = hazir["manifest"]
    zf = hazir["zip"]
    emir_id = hazir["sonuc"]["emir_id"]

    assert manifest["attachment_count"] == 1
    assert len(manifest["missing_attachments"]) == 1
    assert manifest["missing_attachments"][0]["storage_path"].endswith("yok.bin")

    assert f"attachments/{emir_id}/var.bin" in zf.namelist()
    assert f"attachments/{emir_id}/yok.bin" not in zf.namelist()
    assert manifest["schema_revision"] is not None
