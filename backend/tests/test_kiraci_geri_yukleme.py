"""KİRACI GERİ YÜKLEME sözleşmesi — ``POST /api/platform/tenant-restore`` (5.1c).

Bu kapının koruduğu iddialar, hepsi SESSİZCE yanlış veri üretir:

1. **Yuvarlak yolculuk BİREBİR.** 5.1a zip'inden dönen yeni firmanın HER
   tablosu manifestteki satır sayısına eşittir; tek sapma, firma dışı tekil
   sırlarını taşıyan iki WhatsApp tablosudur ve o sapma raporda SAYILIDIR.
2. **Kimlikler YENİDEN yazılır, kaynak DOKUNULMAZ.** Yeni firmanın her
   yabancı anahtarı yeni firmanın kendi satırına gider; kaynak firmanın satır
   sayıları ve ``is_active=false`` bayrağı restore'dan sonra AYNIDIR.
3. **HER kiracı tablosu yolculuğa girer.** ``TENANT_TABLES`` tek tek gezilir;
   tohumlanamayan tablo ``BOS_KABUL``de gerekçesiyle yazılıdır ve ölü bir
   ``BOS_KABUL`` girdisi de kapıyı kırar.
4. **Yarım firma YOKTUR.** Manifest kurcalaması 4xx ve SIFIR yazma; işlemin
   ortasına enjekte edilen hata işlemin TAMAMINI geri alır.
5. **Kuru koşu YAZMAZ.** Satır sayıları ve ek dizini aynı kalır.
6. **Operatör olmayan giremez**, kiracı seçicisi platform ucunda da sızmaz.
7. **``yerine`` yalnız BOŞ kapalı kimliğe.** Aktif firma 409; artık satır 409.

ÖLÇÜM YÖNTEMİ
-------------
Ağır hazırlık (göçler + zengin tohum + dışa aktarım + imha + kuru koşu +
kurcalama + enjekte hata + geri yükleme + denetimler) TEK kere, modül kapsamlı
bir fixture'da alt süreçte koşar; ``app.config.Settings`` modül düzeyinde tek
kopyadır ve ancak ayrı bir süreçte başka bir ``DATABASE_URL`` görebilir. Sonuç
JSON'a yazılır, testler onu okur. Tohum JENERİKTİR: her kiracı tablosuna
yansımadan türetilen bir satır yazılır (FK'lar var olan ebeveyne, ayırt edici
sütunlar sözlükten), üstüne fatura/parti/tahsis gibi anlamsal denetimlerin
konusu olan satırlar ELLE eklenir.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

#: Jenerik tohumun DOLDURAMADIĞI tablolar — her biri gerekçesiyle. Ölçüldü;
#: bir tablo buradayken satır kazanırsa (ölü girdi) kapı kırılır.
BOS_KABUL: dict[str, str] = {}

_ORTAK = r'''
import json, os, re, io, zipfile, shutil
from decimal import Decimal
from datetime import datetime, timezone, date, time as saat
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import (MetaData, insert, select, update, func, Integer, Numeric,
                        String, Boolean, DateTime, Date, Time, JSON, LargeBinary,
                        CheckConstraint)
from sqlalchemy.schema import sort_tables
from sqlalchemy.exc import IntegrityError, StatementError

from app.main import app
from app.config import settings
from app.db import engine
from app import kiraci_geri_yukleme as gy

YENI_PAROLA = "GeriYukle!2026"
CIKTI = Path(os.environ["CIKTI_DIZINI"])


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


def semayi_yansit():
    md = MetaData(); md.reflect(bind=engine)
    return md


def yaz(ad, veri):
    (CIKTI / ad).write_text(json.dumps(veri, ensure_ascii=False, default=str), encoding="utf-8")


def kiraci_tablolari(md):
    return {n for n, t in md.tables.items() if "company_id" in t.c}


def sayimlar(md, cid):
    """Her kiracı tablosunda cid'e ait satır sayısı."""
    out = {}
    with engine.connect() as conn:
        for ad in sorted(kiraci_tablolari(md)):
            t = md.tables[ad]
            out[ad] = int(conn.execute(select(func.count()).select_from(t)
                                       .where(t.c.company_id == cid)).scalar_one())
        out["__companies__"] = int(conn.execute(
            select(func.count()).select_from(md.tables["companies"])).scalar_one())
    return out


def yukle(client, h, zip_bytes, **form):
    veri = {k: str(v) for k, v in form.items()}
    return client.post("/api/platform/tenant-restore", headers=h,
                       files={"file": ("a.zip", zip_bytes, "application/zip")}, data=veri)


def zorunlu(t, verilen):
    """Verilen sütunlara (yalnız TABLODA OLANLAR), NOT NULL ve varsayılansız
    sütunları tipine göre ekler; CHECK ``IN`` listesi varsa ilk seçeneği alır."""
    satir = {k: v for k, v in verilen.items() if k in t.c}
    secenek = _check_secenekleri(t); secenek.pop("__hepsi__", None)
    for c in t.c:
        if c.name in satir or c.nullable or c.name == "id" or c.default is not None or c.server_default is not None:
            continue
        if c.name in secenek:
            satir[c.name] = secenek[c.name]; continue
        satir[c.name] = ("x" if isinstance(c.type, String) else Decimal("1") if isinstance(c.type, Numeric)
                         else date(2026, 9, 1) if isinstance(c.type, Date) else datetime(2026, 9, 1) if isinstance(c.type, DateTime)
                         else False if isinstance(c.type, Boolean) else 1)
    return satir


# ----------------------------------------------------------------------------
# JENERİK TOHUM — her kiracı tablosuna yansımadan türetilen BİR satır
# ----------------------------------------------------------------------------
_IN_DESENI = re.compile(r"\b([A-Za-z_]+)\s+IN\s*\(([^)]*)\)", re.IGNORECASE)
#: PostgreSQL yansıması ``IN`` listesini ``col::text = ANY (ARRAY['a'::character
#: varying, ...]::text[])`` biçiminde verir (ölçüldü).
_ANY_DESENI = re.compile(r"\b([A-Za-z_]+)(?:::\w+)?\s*=\s*ANY\s*\(\s*ARRAY\[(.*?)\]", re.IGNORECASE | re.DOTALL)
#: PG yansıması eşitliği ``col::text = 'x'::text`` / ``col = 365`` yazar.
_ESIT_DESENI = re.compile(r"\b([A-Za-z_]+)(?:::\w+)?\s*=\s*('(?:[^']|'')*'|\d+(?:\.\d+)?)(?:::[a-z ]+)?")
_BUYUK_ESIT_DESENI = re.compile(r"\b([A-Za-z_]+)(?:::\w+)?\s*>=\s*(\d+(?:\.\d+)?)")


def _check_secenekleri(t):
    """CHECK metinlerinden sütun başına KABUL EDİLEN değer listesi çıkarır.

    ``x IN ('a','b')`` -> ['a','b']; ``x = 'a'`` / ``x = 365`` -> [o değer];
    ``x >= 2000`` -> [2000]. Yalnız BASİT biçimler; kalanı deneme yakalar.
    """
    secenek = {}
    def _ekle(sutun, degerler):
        if sutun in t.c and degerler:
            secenek.setdefault(sutun, degerler)
    for c in t.constraints:
        if not (isinstance(c, CheckConstraint) and c.sqltext is not None):
            continue
        metin = str(c.sqltext)
        for sutun, liste in _IN_DESENI.findall(metin) + _ANY_DESENI.findall(metin):
            metinler = [d.replace("''", "'") for d in re.findall(r"'((?:[^']|'')*)'", liste)]
            sayilar = re.findall(r"(?<![\w'])(\d+(?:\.\d+)?)(?![\w'])", liste)
            _ekle(sutun, metinler or [Decimal(s) if "." in s else int(s) for s in sayilar])
        if "<>" in metin or " OR " in metin.upper():
            continue  # bileşik koşul; eşitlikleri güvenle okuyamayız
        for sutun, deger in _ESIT_DESENI.findall(metin):
            _ekle(sutun, [deger.strip("'").replace("''", "'") if deger.startswith("'") else (Decimal(deger) if "." in deger else int(deger))])
        for sutun, deger in _BUYUK_ESIT_DESENI.findall(metin):
            _ekle(sutun, [Decimal(deger) if "." in deger else int(deger)])
    return {k: (v[0] if not isinstance(v, list) else v[0]) for k, v in secenek.items()} | {"__hepsi__": secenek}


def _deger(t, col, n, secenek):
    tip = col.type
    if col.name in secenek:
        return secenek[col.name]
    if isinstance(tip, Boolean): return False
    if isinstance(tip, Numeric): return Decimal("1.50")
    if isinstance(tip, Integer): return 1
    if isinstance(tip, DateTime): return datetime(2026, 9, 1, 8, 0, 0)
    if isinstance(tip, Date): return date(2026, 9, 1)
    if isinstance(tip, Time): return saat(8, 0)
    if isinstance(tip, JSON): return {"k": "v"}
    if isinstance(tip, LargeBinary): return b"x"
    uzunluk = getattr(tip, "length", None) or 40
    return f"T{n}-{t.name}"[:uzunluk]


def _ebeveyn(conn, md, hedef, cid, admin_id, sira=0):
    """Hedef tablonun cid'e ait ``sira``. satırının kimliği (yoksa ilki)."""
    if hedef == "companies": return cid
    if hedef == "app_users": return admin_id
    t = md.tables[hedef]
    if "id" not in t.c:
        return None
    secim = select(t.c.id).order_by(t.c.id)
    if "company_id" in t.c:
        secim = secim.where(t.c.company_id == cid)
    kimlikler = conn.execute(secim).scalars().all()
    if not kimlikler:
        return None
    return kimlikler[min(sira, len(kimlikler) - 1)]


def generik_tohum(md, cid, admin_id):
    """Her kiracı tablosuna BİR satır; sıra yumuşak referansları da sayar.

    Deneme stratejileri, sırayla: (1) TAM (her sütun dolu), (2) ASGARİ
    (nullable sütunlar NULL — FK'lar dahil), (3) ASGARİ + TEK nullable ``*_id``
    sütunu dolu (XOR/tek-hedef CHECK'leri), (4) 2 ve 3, ayırt edici CHECK
    listesinin ÖTEKİ seçenekleriyle. Hiçbiri geçmezse tablo ve son hata
    ``basarisiz``a yazılır; test onu ``BOS_KABUL``e karşı ölçer.
    """
    kiraci = kiraci_tablolari(md)
    planlar = gy.sutun_plani(md, frozenset(kiraci))
    sirali = gy.geri_yukleme_sirasi(md, frozenset(kiraci), planlar)
    basarisiz = {}
    n = 0
    for t in sirali:
        n += 1
        with engine.connect() as conn:
            if conn.execute(select(func.count()).select_from(t).where(t.c.company_id == cid)).scalar_one():
                continue
        secenek_hepsi = _check_secenekleri(t)
        hepsi = secenek_hepsi.pop("__hepsi__")
        fk_hedef = {}
        for kisit in t.foreign_key_constraints:
            for col in kisit.columns:
                fk_hedef[col.name] = kisit.referred_table.name
        nullable_idler = [c.name for c in t.c if c.name.endswith("_id") and c.nullable and c.name != "company_id"]
        # Ayırt edici seçenek varyantları: ilk seçenek + ötekiler (en çok 3 sütun).
        varyantlar = [dict(secenek_hepsi)]
        for sutun, liste in list(hepsi.items())[:3]:
            for deger in liste[1:3]:
                v = dict(secenek_hepsi); v[sutun] = deger; varyantlar.append(v)
        denemeler = []
        for secenek in varyantlar:
            denemeler.append(("tam", None, secenek))
            denemeler.append(("asgari", None, secenek))
            for tek in nullable_idler:
                denemeler.append(("asgari", tek, secenek))
        hata = None
        for deneme, tek, secenek in denemeler:
            satir = {}
            kullanim = {}
            with engine.connect() as conn:
                for col in t.c:
                    ad = col.name
                    if ad == "id" and col.primary_key and isinstance(col.type, Integer) and len(list(t.primary_key.columns)) == 1:
                        if planlar[t.name].kimlik_uretilir:
                            # PG'de serial olmayan PK (notifications_archive): kimliği biz veririz.
                            satir[ad] = (conn.execute(select(func.max(t.c.id))).scalar() or 0) + 1
                        continue
                    if ad == "company_id":
                        satir[ad] = cid; continue
                    bos_birak = deneme == "asgari" and col.nullable and ad != tek
                    if ad in fk_hedef:
                        hedef = fk_hedef[ad]
                        if hedef == t.name or bos_birak:
                            satir[ad] = None
                        else:
                            k = kullanim.get(hedef, 0); kullanim[hedef] = k + 1
                            satir[ad] = _ebeveyn(conn, md, hedef, cid, admin_id, k)
                        continue
                    anahtar = (t.name, ad)
                    if anahtar in gy.KULLANICI_SUTUNLARI:
                        satir[ad] = None if bos_birak else admin_id; continue
                    if anahtar in gy.DOGRUDAN_HEDEFLER:
                        hedef = gy.DOGRUDAN_HEDEFLER[anahtar]
                        if bos_birak or hedef == t.name:
                            satir[ad] = None
                        else:
                            k = kullanim.get(hedef, 0); kullanim[hedef] = k + 1
                            satir[ad] = _ebeveyn(conn, md, hedef, cid, admin_id, k)
                        continue
                    if anahtar in gy.AYIRT_EDICI_HEDEFLER:
                        ayirt, sozluk = gy.AYIRT_EDICI_HEDEFLER[anahtar]
                        if bos_birak:
                            satir[ad] = None
                            if ayirt in secenek: satir[ayirt] = secenek[ayirt]
                            continue
                        secilen = None
                        adaylar = [(secenek[ayirt], sozluk.get(secenek[ayirt]))] if ayirt in secenek else list(sozluk.items())
                        for tur, hedef in adaylar:
                            if hedef is None or hedef == t.name: continue
                            kimlik = _ebeveyn(conn, md, hedef, cid, admin_id)
                            if kimlik is not None:
                                secilen = (tur, kimlik); break
                        if secilen:
                            satir[ayirt] = secilen[0]; satir[ad] = secilen[1]
                        else:
                            satir[ad] = 1 if not col.nullable else None
                            if ayirt in secenek: satir[ayirt] = secenek[ayirt]
                        continue
                    if bos_birak:
                        satir[ad] = None; continue
                    if ad in satir:
                        continue
                    satir[ad] = _deger(t, col, n, secenek)
            try:
                with engine.begin() as conn:
                    conn.execute(insert(t), satir)
                hata = None
                break
            except (IntegrityError, StatementError, ValueError, TypeError) as exc:
                hata = str(exc).splitlines()[0][:300]
        if hata:
            basarisiz[t.name] = hata
    return basarisiz


def zip_degistir(zip_bytes, fn):
    """Her üyeyi fn(ad, veri) -> (yeni_ad, yeni_veri) | None ile yeniden yazar."""
    kaynak = zipfile.ZipFile(io.BytesIO(zip_bytes))
    cikis = io.BytesIO()
    with zipfile.ZipFile(cikis, "w", zipfile.ZIP_DEFLATED) as yeni:
        for ad in kaynak.namelist():
            sonuc = fn(ad, kaynak.read(ad))
            if sonuc is None: continue
            yeni.writestr(sonuc[0], sonuc[1])
    return cikis.getvalue()


def firma_kimligi_degistir(zip_bytes, manifest, eski, yeni_cid):
    """Manifesti ve firma dosyasını başka bir firma kimliğine çevirir."""
    m = dict(manifest); m["company_id"] = yeni_cid
    firma = json.loads(zipfile.ZipFile(io.BytesIO(zip_bytes)).read(f"companies/{eski}.json")); firma["id"] = yeni_cid
    def fn(ad, v):
        if ad == "manifest.json": return (ad, json.dumps(m))
        if ad == f"companies/{eski}.json": return (f"companies/{yeni_cid}.json", json.dumps(firma))
        return (ad, v)
    return zip_degistir(zip_bytes, fn)
'''

_HAZIRLIK = _ORTAK + r'''
# `raise_server_exceptions=False`: enjekte edilen RuntimeError'ın 500 olarak
# İSTEMCİYE düşmesi ölçülüyor; TestClient'ın varsayılanı onu yeniden fırlatır.
with TestClient(app, raise_server_exceptions=False) as client:
    h, govde = admin_headers(client)
    a_id = int(govde["companies"][0]["id"])
    admin_id = int(govde["user"]["id"])
    assert str(admin_id) in settings.sungur_platform_operators.split(","), settings.sungur_platform_operators
    md = semayi_yansit()

    # --- İKİNCİ FİRMA: imhadan sonra operatörün ayakta kalacağı yer --------
    with engine.begin() as conn:
        b_id = conn.execute(insert(md.tables["companies"]).values(
            name="B Kiracısı", is_active=True,
            created_at=datetime.now(timezone.utc))).inserted_primary_key[0]
        conn.execute(insert(md.tables["user_company_memberships"]).values(
            user_id=admin_id, company_id=b_id, is_default=False,
            created_at=datetime.now(timezone.utc)))
        # İkinci bir kullanıcı: üyeliği zip'te olacak ama SONRA silinecek.
        kullanicilar = md.tables["app_users"]
        silinecek = conn.execute(insert(kullanicilar), zorunlu(kullanicilar, {
            "username": "gidici", "email": "gidici@ornek.invalid", "email_verified": True,
            "display_name": "Gidici", "password_hash": "x", "role": "rapor", "is_active": True,
            "created_at": datetime.now(timezone.utc), "must_change_password": False,
        })).inserted_primary_key[0]
        conn.execute(insert(md.tables["user_company_memberships"]).values(
            user_id=silinecek, company_id=a_id, is_default=True,
            created_at=datetime.now(timezone.utc)))

    # --- ANLAMSAL TOHUM (HTTP + Core) ---------------------------------------
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
        for ad_, boyut in (("var.bin", 19), ("yok.bin", 7)):
            conn.execute(insert(ekler), zorunlu(ekler, {
                "company_id": a_id, "work_order_id": emir_id, "file_name": ad_,
                "content_type": "application/octet-stream", "file_size": boyut,
                "storage_path": goreli + "/" + ad_, "kind": "other",
                "uploaded_by": admin_id, "created_at": simdi}))

    urunler = md.tables["products"]; partiler = md.tables["product_lots"]
    hareketler = md.tables["stock_movements"]; depolar = md.tables["warehouses"]
    with engine.begin() as conn:
        depo = conn.execute(insert(depolar), zorunlu(depolar, {"company_id": a_id, "name": "Ana Depo"})).inserted_primary_key[0]
        u1 = conn.execute(insert(urunler), zorunlu(urunler, {"company_id": a_id, "product_code": "A-1",
                          "name": "Ürün A-1", "unit": "ADET"})).inserted_primary_key[0]
        u2 = conn.execute(insert(urunler), zorunlu(urunler, {"company_id": a_id, "product_code": "A-2",
                          "name": "Ürün A-2", "unit": "ADET"})).inserted_primary_key[0]
        for kod, urun in (("P-1", u1), ("P-2", u1), ("P-3", u2)):
            conn.execute(insert(partiler), zorunlu(partiler, {
                "company_id": a_id, "product_id": urun, "lot_code": kod, "warehouse_id": depo,
                "quantity": Decimal("5"), "created_at": datetime(2026, 9, 1)}))
        conn.execute(insert(hareketler), zorunlu(hareketler, {
            "company_id": a_id, "product_id": u1, "movement_type": "IN",
            "quantity": Decimal("12.3456"), "movement_date": "2026-09-05", "note": "A hareketi"}))

    # Fatura + kalemler: totals_snapshot.total = kalem toplamı (anlamsal denetim).
    faturalar = md.tables["invoices"]; kalemler = md.tables["invoice_items"]
    with engine.begin() as conn:
        fat_id = conn.execute(insert(faturalar), zorunlu(faturalar, {
            "company_id": a_id, "invoice_number": "GY-2026-1", "created_by": admin_id,
            "totals_snapshot": json.dumps({"total": "300.00"}),
            "created_at": datetime(2026, 9, 1), "updated_at": datetime(2026, 9, 1)})).inserted_primary_key[0]
        for i, tutar in enumerate((Decimal("100.00"), Decimal("200.00")), 1):
            conn.execute(insert(kalemler), zorunlu(kalemler, {
                "company_id": a_id, "invoice_id": fat_id, "item_type": "part",
                "quantity": Decimal("1"), "unit_price": tutar, "original_price": tutar,
                "discount_amount": Decimal("0"), "tax_rate": Decimal("0"), "tax_amount": Decimal("0"),
                "total": tutar, "warranty_percent": Decimal("0"), "customer_payable": tutar,
                "company_payable": Decimal("0"), "description": f"Kalem {i}", "source_snapshot": "{}"}))

    # Ödeme + tahsisler (siparişe): tahsis toplamı ödeme tutarına eşit.
    odemeler = md.tables["payments"]; tahsisler = md.tables["payment_allocations"]
    siparisler = md.tables["orders"]
    with engine.begin() as conn:
        sip_id = conn.execute(insert(siparisler), zorunlu(siparisler, {
            "company_id": a_id, "customer_id": musteri_id, "warehouse_id": depo})).inserted_primary_key[0]
        od_id = conn.execute(insert(odemeler), zorunlu(odemeler, {
            "company_id": a_id, "entity_type": "customer", "entity_id": musteri_id,
            "amount": Decimal("300.00"), "payment_date": "2026-09-05"})).inserted_primary_key[0]
        for tutar in (Decimal("120.00"), Decimal("180.00")):
            conn.execute(insert(tahsisler), zorunlu(tahsisler, {
                "company_id": a_id, "payment_id": od_id, "order_id": sip_id,
                "amount": tutar, "allocation_type": "manual", "effective_date": date(2026, 9, 5),
                "created_by": admin_id}))

    # Alacak ücret belgesi: şekil CHECK'i (servis ücreti -> iş emri dolu,
    # dönem/sipariş boş) jenerik tohumun deneme uzayının dışında; elle.
    belgeler = md.tables["receivable_charge_documents"]
    try:
        with engine.begin() as conn:
            conn.execute(insert(belgeler), zorunlu(belgeler, {
                "company_id": a_id, "customer_id": musteri_id, "work_order_id": emir_id,
                "charge_type": "service_fee", "status": "draft", "revision_no": 1,
                # Şekil CHECK'i: servis ücretinde net/KDV alanları NULL kalır.
                "gross_amount": Decimal("50"), "net_amount": None, "vat_amount": None,
                "period_start": date(2026, 9, 1), "period_end": date(2026, 9, 30),
                "document_no": "SRV-1", "created_by": admin_id,
                "created_at": datetime(2026, 9, 1), "updated_at": datetime(2026, 9, 1)}))
    except (IntegrityError, StatementError):
        pass  # jenerik tohum dener; o da geçemezse BOS_KABUL ölçer

    # --- JENERİK TOHUM: geri kalan HER tablo ----------------------------------
    basarisiz = generik_tohum(md, a_id, admin_id)
    onceki_a = sayimlar(md, a_id)
    bos_a = sorted(t for t, n in onceki_a.items() if n == 0 and t != "__companies__")

    # --- DIŞA AKTARIM -------------------------------------------------------
    r = client.get("/api/company/export", headers=h)
    assert r.status_code == 200, r.text[:800]
    zip_bytes = r.content
    (CIKTI / "a.zip").write_bytes(zip_bytes)
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    manifest = json.loads(zf.read("manifest.json"))

    # --- İMHA (5.1b) ---------------------------------------------------------
    imha = client.post("/api/company/erase", headers=h, json={"confirm_name": govde["companies"][0]["name"]})
    assert imha.status_code == 200, imha.text
    # Silinen kullanıcı: üyeliği zip'te var, hesabı artık YOK.
    with engine.begin() as conn:
        conn.execute(md.tables["app_users"].delete().where(md.tables["app_users"].c.id == silinecek))
    h["X-Company-ID"] = str(b_id)
    imha_sonrasi_a = sayimlar(md, a_id)

    # --- KURU KOŞU: hiçbir şey yazmaz ----------------------------------------
    ek_dizini_once = sorted(str(p.relative_to(kok)) for p in kok.rglob("*") if p.is_file())
    kuru = yukle(client, h, zip_bytes, mode="yeni", dry_run="true")
    assert kuru.status_code == 200, kuru.text[:800]
    kuru_rapor = kuru.json()
    kuru_sonrasi = sayimlar(md, a_id)
    ek_dizini_sonra = sorted(str(p.relative_to(kok)) for p in kok.rglob("*") if p.is_file())

    # --- KURCALAMA: dördü de 4xx ve SIFIR yazma ------------------------------
    m2 = dict(manifest); m2["row_counts"] = dict(manifest["row_counts"]); m2["row_counts"]["products"] += 1
    sayi_bozuk = zip_degistir(zip_bytes, lambda ad, v: (ad, json.dumps(m2)) if ad == "manifest.json" else (ad, v))
    m3 = dict(manifest); m3["schema_revision"] = "19990101_0000"
    sema_bozuk = zip_degistir(zip_bytes, lambda ad, v: (ad, json.dumps(m3)) if ad == "manifest.json" else (ad, v))
    manifest_yok = zip_degistir(zip_bytes, lambda ad, v: None if ad == "manifest.json" else (ad, v))
    kurcalama = {}
    for etiket, govde_zip in (("sayi", sayi_bozuk), ("sema", sema_bozuk), ("yok", manifest_yok), ("zip_degil", b"bu bir zip degil")):
        r = yukle(client, h, govde_zip, mode="yeni")
        kurcalama[etiket] = {"status": r.status_code, "code": (r.json() or {}).get("code")}
    kurcalama_sonrasi = sayimlar(md, a_id)

    # --- ENJEKTE HATA: 5. tabloda patlat, hiçbir şey kalmasın ---------------
    asil = gy._tabloyu_yaz
    sayac = {"n": 0}
    def patlayan(*a, **k):
        sayac["n"] += 1
        if sayac["n"] == 5:
            raise RuntimeError("enjekte edilen hata: 5. tablo")
        return asil(*a, **k)
    gy._tabloyu_yaz = patlayan
    try:
        r = yukle(client, h, zip_bytes, mode="yeni")
        enjekte_status = r.status_code
    finally:
        gy._tabloyu_yaz = asil
    enjekte_sonrasi = sayimlar(md, a_id)
    with engine.connect() as conn:
        toplam_enjekte = {ad: int(conn.execute(select(func.count()).select_from(md.tables[ad])).scalar_one())
                          for ad in ("products", "customers", "user_company_memberships")}

    # --- YERİNE: artık satırlı kapalı firma 409, aktif firma 409 ------------
    r = yukle(client, h, zip_bytes, mode="yerine")
    yerine_artik = {"status": r.status_code, "code": (r.json() or {}).get("code")}
    r = yukle(client, h, firma_kimligi_degistir(zip_bytes, manifest, a_id, b_id), mode="yerine")
    yerine_aktif = {"status": r.status_code, "code": (r.json() or {}).get("code")}
    yerine_sonrasi = sayimlar(md, a_id)

    # --- GERÇEK GERİ YÜKLEME ----------------------------------------------------
    r = yukle(client, h, zip_bytes, mode="yeni")
    assert r.status_code == 200, r.text[:1500]
    rapor = r.json()
    c_id = int(rapor["company_id"])
    sonraki_a = sayimlar(md, a_id)
    sonraki_c = sayimlar(md, c_id)

    # Yeni firmanın HER yabancı anahtarı yeni firmanın kendi satırına gider.
    kiraci = kiraci_tablolari(md)
    fk_ihlal = []
    with engine.connect() as conn:
        for ad in sorted(kiraci):
            t = md.tables[ad]
            for kisit in t.foreign_key_constraints:
                hedef = kisit.referred_table
                if hedef.name not in kiraci or hedef.name == ad or "id" not in hedef.c:
                    continue
                for col in kisit.columns:
                    if col.name == "company_id": continue
                    hedef_kimlikler = {int(x) for x in conn.execute(
                        select(hedef.c.id).where(hedef.c.company_id == c_id)).scalars()}
                    degerler = [int(x) for x in conn.execute(
                        select(t.c[col.name]).where(t.c.company_id == c_id)).scalars() if x is not None]
                    kacak = [d for d in degerler if d not in hedef_kimlikler]
                    if kacak:
                        fk_ihlal.append({"table": ad, "column": col.name, "ids": kacak[:5]})
        fat = conn.execute(select(faturalar.c.id, faturalar.c.totals_snapshot, faturalar.c.invoice_number).where(faturalar.c.company_id == c_id)).mappings().all()
        kalem_toplam = {}
        for k in conn.execute(select(kalemler.c.invoice_id, kalemler.c.total).where(kalemler.c.company_id == c_id)).mappings():
            kalem_toplam[int(k["invoice_id"])] = kalem_toplam.get(int(k["invoice_id"]), Decimal(0)) + Decimal(str(k["total"]))
        def _toplam(snap):
            try: return str(json.loads(snap).get("total"))
            except Exception: return None
        anlamsal = {"fatura": [{"id": int(f["id"]), "no": f["invoice_number"], "total": _toplam(f["totals_snapshot"]),
                                "kalem": str(kalem_toplam.get(int(f["id"]), Decimal(0)))} for f in fat]}
        od = conn.execute(select(odemeler.c.id, odemeler.c.amount).where(odemeler.c.company_id == c_id)).mappings().all()
        tah = {}
        for a in conn.execute(select(tahsisler.c.payment_id, tahsisler.c.amount).where(tahsisler.c.company_id == c_id)).mappings():
            tah[int(a["payment_id"])] = tah.get(int(a["payment_id"]), Decimal(0)) + Decimal(str(a["amount"]))
        anlamsal["odeme"] = [{"id": int(o["id"]), "amount": str(o["amount"]), "tahsis": str(tah.get(int(o["id"]), Decimal(0)))} for o in od]
        parti = conn.execute(select(partiler.c.lot_code, partiler.c.product_id).where(partiler.c.company_id == c_id)).mappings().all()
        urun_c = {int(u["id"]): u["product_code"] for u in conn.execute(select(urunler.c.id, urunler.c.product_code).where(urunler.c.company_id == c_id)).mappings()}
        anlamsal["parti"] = sorted((p["lot_code"], urun_c.get(int(p["product_id"]))) for p in parti)
        anlamsal["hareket_miktar"] = [str(x) for x in conn.execute(select(hareketler.c.quantity).where(hareketler.c.company_id == c_id, hareketler.c.note == "A hareketi")).scalars()]
        uyelik = md.tables["user_company_memberships"]
        anlamsal["uyeler"] = sorted(int(u) for u in conn.execute(select(uyelik.c.user_id).where(uyelik.c.company_id == c_id)).scalars())
        firma_c = conn.execute(select(md.tables["companies"]).where(md.tables["companies"].c.id == c_id)).mappings().first()
        anlamsal["firma"] = {"name": firma_c["name"], "is_active": bool(firma_c["is_active"])}
        firma_a = conn.execute(select(md.tables["companies"].c.is_active).where(md.tables["companies"].c.id == a_id)).scalar_one()
        kayit = md.tables["activity_logs"]
        anlamsal["aktivite"] = [dict(x) for x in conn.execute(
            select(kayit.c.company_id, kayit.c.user_id, kayit.c.resource_type, kayit.c.details)
            .where(kayit.c.action_type == "company.restored")).mappings()]
        ek_c = conn.execute(select(ekler.c.file_name, ekler.c.storage_path, ekler.c.work_order_id).where(ekler.c.company_id == c_id)).mappings().all()
        anlamsal["ekler"] = [dict(e) for e in ek_c]
        anlamsal["ek_dosyalari"] = {e["file_name"]: (kok / e["storage_path"]).is_file() for e in ek_c}

    hc = dict(h); hc["X-Company-ID"] = str(c_id)
    me_c = client.get("/api/customers", headers=hc)

    # --- YERİNE, BOŞ KAPALI KİMLİK: kimlikler korunur ---------------------------
    # Kaynak satırları GERÇEKTEN yok olan bir firma gerekir: D açılır, üyelikle
    # dışa aktarılır, sonra üyelik satırı silinip firma kapatılır (sert silme
    # simülasyonu; `activity_logs` D'ye HİÇ yazılmadı çünkü dışa aktarım kaydı
    # kesitten SONRA düşer ve zip'e girmez). Zip'teki tek satır üyeliktir ve
    # kimliği bilinir.
    uyelik = md.tables["user_company_memberships"]
    with engine.begin() as conn:
        y_id = conn.execute(insert(md.tables["companies"]).values(
            name="Yerine Firması", is_active=True,
            created_at=datetime.now(timezone.utc))).inserted_primary_key[0]
        y_uyelik = conn.execute(insert(uyelik).values(
            user_id=admin_id, company_id=y_id, is_default=False,
            created_at=datetime.now(timezone.utc))).inserted_primary_key[0]
    hy = dict(h); hy["X-Company-ID"] = str(y_id)
    r = client.get("/api/company/export", headers=hy)
    assert r.status_code == 200, r.text[:500]
    zip_y = r.content
    manifest_y = json.loads(zipfile.ZipFile(io.BytesIO(zip_y)).read("manifest.json"))
    with engine.begin() as conn:
        # D'nin satırları: dışa aktarım kaydı (activity_logs) + üyelik. Kayıt
        # tetikleyiciyle silinemez; bu yüzden D'nin zip'i ALINDIKTAN SONRA
        # yazılan o satır "artık" sayılır ve önce 409 ÖLÇÜLÜR, sonra kaydın
        # firma bağı kesilerek (company_id -> B) boş kimlik senaryosu kurulur.
        conn.execute(uyelik.delete().where(uyelik.c.company_id == y_id))
        conn.execute(update(md.tables["companies"]).where(md.tables["companies"].c.id == y_id).values(is_active=False))
    r = yukle(client, h, zip_y, mode="yerine")
    yerine_kayit_artik = {"status": r.status_code, "code": (r.json() or {}).get("code"),
                          "details": (r.json() or {}).get("details")}
    kayit = md.tables["activity_logs"]
    with engine.begin() as conn:
        # SERT SİLME SİMÜLASYONU (yalnız bu test veritabanında): ürün bugün
        # aktivite satırını SİLEMEZ (BEFORE DELETE tetikleyicisi) ve bu tam
        # olarak `yerine`nin gerçek akışta erişilemez olmasının sebebidir.
        # Tetikleyici kaldırılıp D'nin tek kaydı silinir ki "boş kimlik"
        # ön koşulu KURULABİLSİN ve kimlik korunumu ÖLÇÜLEBİLSİN.
        if engine.dialect.name == "postgresql":
            conn.exec_driver_sql("DROP TRIGGER IF EXISTS trg_activity_logs_no_delete ON activity_logs")
        else:
            conn.exec_driver_sql("DROP TRIGGER IF EXISTS trg_activity_logs_no_delete")
        conn.execute(kayit.delete().where(kayit.c.company_id == y_id))
    r = yukle(client, h, zip_y, mode="yerine")
    yerine_bos = {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text[:500]}
    with engine.connect() as conn:
        y_uyelik_sonra = sorted(int(x) for x in conn.execute(select(uyelik.c.id).where(uyelik.c.company_id == y_id)).scalars())
        y_aktif = bool(conn.execute(select(md.tables["companies"].c.is_active).where(md.tables["companies"].c.id == y_id)).scalar_one())
    zipteki_urun_kimlikleri = [int(y_uyelik)]
    y_urun = y_uyelik_sonra
    manifest_y_uyelik = manifest_y["row_counts"]["user_company_memberships"]
    # Aktivite kayıtlarını B'ye taşıdık; C'nin "restored" kayıtları hâlâ B'de.
    with engine.connect() as conn:
        anlamsal["aktivite"] = [dict(x) for x in conn.execute(
            select(kayit.c.company_id, kayit.c.user_id, kayit.c.resource_type, kayit.c.details)
            .where(kayit.c.action_type == "company.restored")).mappings()]

    yaz("sonuc.json", {
        "a_id": a_id, "b_id": b_id, "c_id": c_id, "y_id": y_id, "admin_id": admin_id,
        "silinen_kullanici": int(silinecek),
        "tohum_basarisiz": basarisiz, "bos_tablolar": bos_a,
        "manifest_row_counts": manifest["row_counts"], "schema_revision": manifest["schema_revision"],
        "onceki_a": onceki_a, "imha_sonrasi_a": imha_sonrasi_a,
        "kuru_rapor": kuru_rapor, "kuru_sonrasi": kuru_sonrasi, "ek_dizini_ayni": ek_dizini_once == ek_dizini_sonra,
        "kurcalama": kurcalama, "kurcalama_sonrasi": kurcalama_sonrasi,
        "enjekte_status": enjekte_status, "enjekte_sonrasi": enjekte_sonrasi, "toplam_enjekte": toplam_enjekte,
        "yerine_artik": yerine_artik, "yerine_aktif": yerine_aktif, "yerine_sonrasi": yerine_sonrasi,
        "rapor": rapor, "sonraki_a": sonraki_a, "sonraki_c": sonraki_c, "firma_a_aktif": bool(firma_a),
        "fk_ihlal": fk_ihlal, "anlamsal": anlamsal, "me_c": me_c.status_code,
        "yerine_bos": yerine_bos, "zipteki_urun_kimlikleri": zipteki_urun_kimlikleri,
        "y_urun": y_urun, "y_aktif": y_aktif, "yerine_kayit_artik": yerine_kayit_artik,
        "manifest_y_uyelik": manifest_y_uyelik,
    })
    print("HAZIRLIK TAMAM")
'''


def _kos(betik: str, veritabani: Path, ciktilar: Path, ek_ortam: dict | None = None,
         url: str | None = None):
    """Betiği ayrı süreçte koşar. ``url`` verilirse (PG ikizi) o veritabanı."""
    ortam = os.environ.copy()
    ortam["DATABASE_URL"] = url or f"sqlite:///{veritabani.as_posix()}"
    ortam["PYTHONPATH"] = str(BACKEND)
    ortam["CIKTI_DIZINI"] = str(ciktilar)
    ortam["SUNGUR_DATA_DIR"] = str(ciktilar / "veri")
    # Bootstrap admin taze veritabanında ilk kullanıcıdır (id=1); betik bunu
    # `settings` üzerinden DOĞRULAR, varsaymaz.
    ortam["SUNGUR_PLATFORM_OPERATORS"] = "1"
    ortam["PYTHONUTF8"] = "1"
    ortam["PYTHONIOENCODING"] = "utf-8"
    (ciktilar / "veri").mkdir(parents=True, exist_ok=True)
    if ek_ortam:
        ortam.update(ek_ortam)
    # Betik DOSYADAN koşar: `-c` ile Windows komut satırı sınırı (32 KiB)
    # aşılıyordu ve `CreateProcess` FileNotFoundError veriyordu.
    betik_yolu = ciktilar / f"betik_{abs(hash(betik)) % 10**8}.py"
    betik_yolu.write_text(betik, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(betik_yolu)], cwd=BACKEND, env=ortam,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1500,
    )


def _basarili(tamam) -> None:
    assert tamam.returncode == 0, tamam.stdout[-6000:] + "\n" + tamam.stderr[-6000:]


@pytest.fixture(scope="module")
def hazir(tmp_path_factory) -> dict:
    dizin = tmp_path_factory.mktemp("geri-yukleme")
    tamam = _kos(_HAZIRLIK, dizin / "a.db", dizin)
    _basarili(tamam)
    return json.loads((dizin / "sonuc.json").read_text(encoding="utf-8"))


def _tenant_tables() -> frozenset[str]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_kiraci_sql_kapisi", Path(__file__).with_name("test_tenant_scoping_guard.py")
    )
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return frozenset(modul.TENANT_TABLES)


# --------------------------------------------------------------------------
# 1) DURAĞAN KAPILAR
# --------------------------------------------------------------------------
def test_izin_admin_only_ve_katalog_girdisi() -> None:
    """MUTASYON: ``required_permission`` kuralını ``"read"`` yapmak KIRMIZI;
    ``ACTION_TYPES``tan ``company.restored``ı silmek KIRMIZI."""
    sys.path.insert(0, str(BACKEND))
    from app.activity_log import ACTION_TYPES
    from app.auth import ROLE_PERMISSIONS, has_permission, required_permission

    assert required_permission("POST", "/api/platform/tenant-restore") == "__admin_only__"
    for rol in ROLE_PERMISSIONS:
        if rol != "admin":
            assert not has_permission(rol, "__admin_only__"), rol
    assert "company.restored" in ACTION_TYPES and ACTION_TYPES["company.restored"]


_PLAN = r'''
import json
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app): pass
from app.db import engine
from app import kiraci_geri_yukleme as gy
from app.routers.kiraci_disa_aktarim import _yansit, _kiraci_tablolari
with engine.connect() as conn:
    md = _yansit(conn)
    kiraci = _kiraci_tablolari(md)
    print("SINIF", json.dumps(gy.siniflandirilmamis_sutunlar(md, kiraci)))
    planlar = gy.sutun_plani(md, kiraci)
    print("FK", sum(len(p.fk_kiraci) + len(p.fk_kendine) for p in planlar.values()))
    print("YUMUSAK", sum(len(p.yumusak_dogrudan) + len(p.yumusak_ayirt) for p in planlar.values()))
'''


def test_siniflandirilmamis_yumusak_referans_yok(tmp_path: Path) -> None:
    """MUTASYON: ``DOGRUDAN_HEDEFLER``ten bir girdi silmek bunu KIRMIZI yapar.

    ``*_id`` adlı, FK kısıtı olmayan HER sütun ya kullanıcı, ya metin, ya
    bilinen çözümsüz, ya doğrudan hedef, ya ayırt edici sözlüğündedir. Yeni bir
    göç böyle bir sütun eklerse bu test ADIYLA kırılır — sessiz geçiş yok.
    """
    tamam = _kos(_PLAN, tmp_path / "p.db", tmp_path)
    _basarili(tamam)
    satirlar = {s.split(" ", 1)[0]: s.split(" ", 1)[1] for s in tamam.stdout.splitlines() if s[:1].isupper() and " " in s}
    assert json.loads(satirlar["SINIF"]) == [], satirlar["SINIF"]
    # ÖLÇÜLDÜ (0082): 113 AYRI kiracı-içi FK sütunu (yol haritasındaki "111"
    # buna karşılık gelir; 294 kısıt-sütununun 76'sı `companies`e, 30'u
    # `app_users`a gider ve bileşik FK'ların `company_id` yarısı ayrı sütun
    # DEĞİLDİR), 80 yumuşak referans sözlükten. Alt sınır: E4a 121. tabloyu
    # getirdiğinde sayı büyür, küçülmez.
    assert int(satirlar["FK"]) >= 100, satirlar["FK"]
    assert int(satirlar["YUMUSAK"]) >= 70, satirlar["YUMUSAK"]


# --------------------------------------------------------------------------
# 2) YUVARLAK YOLCULUK
# --------------------------------------------------------------------------
def test_her_kiraci_tablosu_yolculukta(hazir) -> None:
    """Tohumlanamayan tablo GEREKÇESİYLE ``BOS_KABUL``de olmak zorunda; ölü
    girdi de kırar. MUTASYON: ``BOS_KABUL``e dolu bir tablo yazmak KIRMIZI."""
    tablolar = _tenant_tables()
    sayilar = hazir["manifest_row_counts"]
    assert set(sayilar) == set(tablolar), (sorted(set(sayilar) ^ set(tablolar)))
    bos = sorted(t for t in tablolar if sayilar[t] == 0)
    assert bos == sorted(BOS_KABUL), {
        "bos_ama_kabulde_yok": sorted(set(bos) - set(BOS_KABUL)),
        "kabulde_ama_dolu": sorted(set(BOS_KABUL) - set(bos)),
        "tohum_hatalari": hazir["tohum_basarisiz"],
    }


def test_yuvarlak_yolculuk_satir_sayilari_manifeste_esit(hazir) -> None:
    """MUTASYON: ``_tabloyu_yaz``da bir tabloyu atlamak ya da ``rows``u yanlış
    saymak KIRMIZI. Tek sapma iki WhatsApp sırrı: kaynak satırlar dururken
    küresel tekil çakışır, satır ATLANIR ve raporda SAYILIR."""
    sys.path.insert(0, str(BACKEND))
    from app.kiraci_geri_yukleme import KURESEL_TEKIL_ATLANIR

    manifest = hazir["manifest_row_counts"]
    c = hazir["sonraki_c"]
    rapor = hazir["rapor"]["tables"]
    sapma = {}
    for tablo, beklenen in manifest.items():
        if tablo in KURESEL_TEKIL_ATLANIR:
            assert rapor[tablo]["skipped"] == beklenen, (tablo, rapor[tablo])
            assert c[tablo] == 0, (tablo, c[tablo])
            continue
        if tablo == "user_company_memberships":
            continue  # ayrı iddia aşağıda
        if c[tablo] != beklenen or rapor[tablo]["rows"] != beklenen:
            sapma[tablo] = {"manifest": beklenen, "db": c[tablo], "rapor": rapor[tablo]}
    assert not sapma, sapma
    assert hazir["rapor"]["row_total"] > 100
    assert hazir["rapor"]["mode"] == "yeni" and hazir["rapor"]["dry_run"] is False


def test_uyelikler_var_olan_kullanicilara_ve_operatore(hazir) -> None:
    """Zip'teki iki üyelik: admin (var) ve silinen kullanıcı (yok). MUTASYON:
    var olmayan kullanıcıya üyelik yazmak SQLite'ta geçer, PG'de de geçer
    (FK yok) — bu yüzden ATLANDIĞI raporda ölçülür."""
    uye = hazir["rapor"]["memberships"]
    assert hazir["anlamsal"]["uyeler"] == [hazir["admin_id"]]
    assert uye["restored"] == 1
    assert uye["skipped_user_ids"] == [hazir["silinen_kullanici"]]
    # Operatör yeni firmaya GERÇEKTEN girebiliyor (üyelik + is_active).
    assert hazir["me_c"] == 200, hazir["me_c"]
    assert hazir["anlamsal"]["firma"]["is_active"] is True


def test_yabanci_anahtarlar_yeni_firmanin_icinde(hazir) -> None:
    """MUTASYON: ``_haritala``yı eski değeri döndürecek biçimde kırmak KIRMIZI:
    yeni firmanın FK'ları kaynak firmanın satırlarına işaret ederdi."""
    assert hazir["fk_ihlal"] == [], hazir["fk_ihlal"][:10]
    rapor = hazir["rapor"]
    # Rapor YAZILAN satırların dokunduğu sütunları sayar (plan 113 sütun
    # bilir; tek satırlık tohumda NULL kalan FK'lar sayılmaz). Ölçüldü: 88.
    assert rapor["fk_columns_remapped"] >= 80, rapor["fk_columns_remapped"]
    assert rapor["soft_columns_remapped"] >= 40, rapor["soft_columns_remapped"]
    assert rapor["id_maps"]["products"] == hazir["manifest_row_counts"]["products"]


def test_anlamsal_denetimler(hazir) -> None:
    """Fatura toplamı = kalem toplamı; tahsis toplamı = ödeme; parti zinciri
    ürünle taşındı; Decimal kuruşu korundu."""
    a = hazir["anlamsal"]
    fatura = [f for f in a["fatura"] if f["no"] == "GY-2026-1"]
    assert fatura and Decimal(fatura[0]["kalem"]) == Decimal("300.00") == Decimal(fatura[0]["total"]), a["fatura"]
    assert any(Decimal(o["tahsis"]) == Decimal("300.00") == Decimal(o["amount"]) for o in a["odeme"]), a["odeme"]
    assert [p for p in a["parti"] if p[0].startswith("P-")] == [["P-1", "A-1"], ["P-2", "A-1"], ["P-3", "A-2"]], a["parti"]
    assert "12.3456" in a["hareket_miktar"], a["hareket_miktar"]


def test_kaynak_firma_dokunulmadi(hazir) -> None:
    """MUTASYON: ``_tabloyu_yaz``da ``company_id``yi eski değerde bırakmak
    kaynak firmanın sayımını büyütür ve bunu KIRMIZI yapar."""
    once = {k: v for k, v in hazir["imha_sonrasi_a"].items() if k != "__companies__"}
    sonra = {k: v for k, v in hazir["sonraki_a"].items() if k != "__companies__"}
    assert sonra == once
    # Yeni firma bir `companies` satırı EKLER (2 -> 3); kaynağa dokunmaz.
    assert hazir["sonraki_a"]["__companies__"] == hazir["imha_sonrasi_a"]["__companies__"] + 1
    assert hazir["firma_a_aktif"] is False
    assert hazir["c_id"] not in (hazir["a_id"], hazir["b_id"])


def test_ekler_kopyalandi_ve_yol_yeniden_yazildi(hazir) -> None:
    """Diskte var olan ek yeni firmanın dizinine kopyalanır; zip'te olmayan
    (5.1a'nın ``missing_attachments``ı) SAYILIR, satırı düşmez."""
    ek = hazir["rapor"]["attachments"]
    assert ek == {"planned": 2, "copied": 1, "missing_in_zip": 1}, ek
    c_id = hazir["c_id"]
    for e in hazir["anlamsal"]["ekler"]:
        assert e["storage_path"].startswith(f"attachments/{c_id}/{e['work_order_id']}/"), e
    assert hazir["anlamsal"]["ek_dosyalari"] == {"var.bin": True, "yok.bin": False}


def test_aktivite_kaydi_operatorun_firmasinda(hazir) -> None:
    """Denetim satırı operatörün kendi firmasına (B) yazılır; yeni firmanın
    tabloları manifeste birebir kalır. Kuru koşu ve kurcalama YAZMAZ."""
    kayitlar = hazir["anlamsal"]["aktivite"]
    # Gerçek geri yükleme + `yerine` boş kimlik = İKİ kayıt, ikisi de B'de.
    assert len(kayitlar) == 2, kayitlar
    for k in kayitlar:
        assert k["company_id"] == hazir["b_id"] and k["user_id"] == hazir["admin_id"]
        assert k["resource_type"] == "backup"
    detay = kayitlar[0]["details"] if isinstance(kayitlar[0]["details"], dict) else json.loads(kayitlar[0]["details"])
    assert detay["company_id"] == hazir["c_id"] and detay["source_company_id"] == hazir["a_id"]
    assert detay["mode"] == "yeni"


# --------------------------------------------------------------------------
# 3) HİÇBİR ŞEY YAZMAYAN YOLLAR
# --------------------------------------------------------------------------
def test_kuru_kosu_yazmaz(hazir) -> None:
    """MUTASYON: ``_KuruKosuBitti``yi yakalayıp commit etmek KIRMIZI."""
    assert hazir["kuru_sonrasi"] == hazir["imha_sonrasi_a"]
    assert hazir["kuru_sonrasi"]["__companies__"] == 2
    assert hazir["ek_dizini_ayni"] is True
    rapor = hazir["kuru_rapor"]
    assert rapor["dry_run"] is True
    assert rapor["row_total"] == hazir["rapor"]["row_total"]
    assert rapor["attachments"] == {"planned": 2, "copied": 0, "missing_in_zip": 0}


def test_manifest_kurcalama_4xx_ve_sifir_yazma(hazir) -> None:
    """MUTASYON: ``manifest_dogrula``dan satır sayısı kapısını silmek
    ``sayi``yı 200 yapar ve bunu KIRMIZI yapar."""
    k = hazir["kurcalama"]
    assert k["sayi"] == {"status": 422, "code": "RESTORE_ROW_COUNT_MISMATCH"}, k
    assert k["sema"] == {"status": 409, "code": "RESTORE_SCHEMA_MISMATCH"}, k
    assert k["yok"] == {"status": 422, "code": "RESTORE_MANIFEST_MISSING"}, k
    assert k["zip_degil"] == {"status": 422, "code": "RESTORE_ZIP_INVALID"}, k
    assert hazir["kurcalama_sonrasi"] == hazir["imha_sonrasi_a"]
    assert hazir["kurcalama_sonrasi"]["__companies__"] == 2


def test_islem_ortasinda_hata_tamamini_geri_alir(hazir) -> None:
    """5. tabloda enjekte edilen hata: firma satırı dahil HİÇBİR ŞEY kalmaz.
    MUTASYON: tablo başına ``engine.begin()`` açmak (işlemi bölmek) ilk dört
    tabloyu kalıcı kılar ve bunu KIRMIZI yapar."""
    assert hazir["enjekte_status"] == 500, hazir["enjekte_status"]
    assert hazir["enjekte_sonrasi"] == hazir["imha_sonrasi_a"]
    assert hazir["enjekte_sonrasi"]["__companies__"] == 2
    onceki = hazir["imha_sonrasi_a"]
    toplam = hazir["toplam_enjekte"]
    # Bütün firmaların toplamı = yalnız A'nın satırları (B boş, C yok).
    assert toplam["products"] == onceki["products"], toplam
    assert toplam["customers"] == onceki["customers"], toplam
    # Üyelikler: yalnız admin'in B üyeliği (A'nınkiler imhada silindi).
    assert toplam["user_company_memberships"] == 1, toplam


# --------------------------------------------------------------------------
# 4) `yerine` KİPİ
# --------------------------------------------------------------------------
def test_yerine_artik_satirli_kapali_firma_409(hazir) -> None:
    """Yumuşak imha veriyi SİLMEZ; kaynak kimlik dolu → 409. Bu, gerçek
    5.1b akışında ``yerine``nin BUGÜN erişilemez olduğunun ölçümüdür."""
    assert hazir["yerine_artik"] == {"status": 409, "code": "RESTORE_SOURCE_ROWS_PRESENT"}
    assert hazir["yerine_sonrasi"] == hazir["imha_sonrasi_a"]


def test_yerine_aktif_firma_409(hazir) -> None:
    assert hazir["yerine_aktif"] == {"status": 409, "code": "RESTORE_SOURCE_ACTIVE"}


def test_yerine_bos_kapali_kimlik_kimlikleri_korur(hazir) -> None:
    """Kapalı ve BOŞ kimliğe yerinde geri yükleme: satır kimliği zip'tekiyle
    AYNI (haritalama yok), firma aktif. ÖNCE ölçülen 409: dışa aktarımın
    KENDİ aktivite kaydı bile "artık satır" sayılır — yani gerçek akışta
    ``yerine`` bugün yalnız satırları başka yolla temizlenmiş bir kimliğe
    uygulanabilir (açık karar, PR gövdesinde)."""
    k = hazir["yerine_kayit_artik"]
    assert k["status"] == 409 and k["code"] == "RESTORE_SOURCE_ROWS_PRESENT", k
    assert k["details"] == {"activity_logs": 1}, k
    y = hazir["yerine_bos"]
    assert y["status"] == 200, y
    assert y["body"]["mode"] == "yerine" and y["body"]["company_id"] == hazir["y_id"]
    assert y["body"]["tables"]["user_company_memberships"]["rows"] == hazir["manifest_y_uyelik"] == 1
    assert hazir["y_urun"] == hazir["zipteki_urun_kimlikleri"], (hazir["y_urun"], hazir["zipteki_urun_kimlikleri"])
    assert hazir["y_aktif"] is True


# --------------------------------------------------------------------------
# 5) YETKİ VE KİRACI SEÇİCİSİ
# --------------------------------------------------------------------------
_YETKI = _ORTAK + r'''
with TestClient(app) as client:
    h, govde = admin_headers(client)
    cid = int(govde["companies"][0]["id"])
    md = semayi_yansit()
    bos_zip = io.BytesIO()
    with zipfile.ZipFile(bos_zip, "w") as z: z.writestr("manifest.json", "{}")
    sonuc = {}
    # (a) admin ama OPERATÖR DEĞİL (liste boş) -> 403, yönlendirici kapısı.
    r = yukle(client, h, bos_zip.getvalue(), mode="yeni")
    sonuc["operator_degil"] = {"status": r.status_code, "detail": r.json().get("detail")}
    # (b) düşük rollü üye -> ara katman 403 PERMISSION_DENIED.
    yeni = client.post("/api/users", headers=h, json={
        "username": "raporcu", "password": "Raporcu!2026", "display_name": "Raporcu", "role": "rapor"})
    assert yeni.status_code in (200, 201), yeni.text
    giris = client.post("/api/auth/login", json={"username": "raporcu", "password": "Raporcu!2026"})
    g = giris.json()
    hr = {"Authorization": "Bearer " + g["access_token"], "X-Company-ID": str(cid)}
    if g.get("user", {}).get("must_change_password"):
        ch = client.post("/api/auth/change-password", headers=hr, json={
            "current_password": "Raporcu!2026", "new_password": "Raporcu!2027"})
        hr["Authorization"] = "Bearer " + ch.json()["access_token"]
    r = yukle(client, hr, bos_zip.getvalue(), mode="yeni")
    sonuc["dusuk_rol"] = {"status": r.status_code, "code": r.json().get("code")}
    # (c) üye olunmayan firma seçicisi -> 403 COMPANY_ACCESS_DENIED (sızma yok).
    h2 = dict(h); h2["X-Company-ID"] = "999999"
    r = yukle(client, h2, bos_zip.getvalue(), mode="yeni")
    sonuc["yabanci_secici"] = {"status": r.status_code, "code": r.json().get("code")}
    with engine.connect() as conn:
        sonuc["firma_sayisi"] = int(conn.execute(select(func.count()).select_from(md.tables["companies"])).scalar_one())
    yaz("yetki.json", sonuc)
    print("YETKI TAMAM")
'''


def test_operator_olmayan_ve_yabanci_secici_403(tmp_path: Path) -> None:
    """MUTASYON: ``require_platform_operator`` çağrısını silmek (a)'yı 4xx
    (manifest) yapar ve KIRMIZI; izni ``read`` yapmak (b)'yi geçirir."""
    tamam = _kos(_YETKI, tmp_path / "y.db", tmp_path, {"SUNGUR_PLATFORM_OPERATORS": ""})
    _basarili(tamam)
    s = json.loads((tmp_path / "yetki.json").read_text(encoding="utf-8"))
    assert s["operator_degil"]["status"] == 403, s
    assert "operatör" in s["operator_degil"]["detail"].lower(), s
    assert s["dusuk_rol"] == {"status": 403, "code": "PERMISSION_DENIED"}, s
    assert s["yabanci_secici"] == {"status": 403, "code": "COMPANY_ACCESS_DENIED"}, s
    assert s["firma_sayisi"] == 1


_BOYUT = _ORTAK + r'''
with TestClient(app) as client:
    h, govde = admin_headers(client)
    buyuk = io.BytesIO()
    with zipfile.ZipFile(buyuk, "w", zipfile.ZIP_STORED) as z:
        z.writestr("dolgu.bin", os.urandom(6000))
    r = yukle(client, h, buyuk.getvalue(), mode="yeni")
    yaz("boyut.json", {"status": r.status_code, "body": r.json()})
    print("BOYUT TAMAM")
'''


def test_boyut_tavani_413(tmp_path: Path) -> None:
    """Uç tavanı KENDİSİ uygular (ara katman +1 MiB tolerans taşır)."""
    tamam = _kos(_BOYUT, tmp_path / "b.db", tmp_path, {"MAX_TENANT_RESTORE_UPLOAD_BYTES": "4096"})
    _basarili(tamam)
    s = json.loads((tmp_path / "boyut.json").read_text(encoding="utf-8"))
    assert s["status"] == 413, s
