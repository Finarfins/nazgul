"""Kantar fişi — SÖZLEŞME. Türetim, birim çözümü, üç değerli bayraklar, kapılar.

Konu: göç 20260904_0069 (kantar fişi v2). Defter tarafı KARDEŞ dosyada
(`test_kantar_fisi_defter.py`); burada ölçülen şey UÇLARIN sözleşmesi. Dosya
`claude/weighbridge-pr1` dalındaki (87db66a, göç 0064) aynı adlı dosyanın
0069 sözlüğüne taşınmış ve BİRİM ÇÖZÜMÜ ile TABAN BİRİM YAZMA YOLU eklenmiş
hâlidir.

--- SESSİZCE YANLIŞ OLABİLECEK YEDİ ŞEY -------------------------------------

1. **BİLEŞİM.** ``net = brüt − Σ(brüt × oran/100)`` TOPLAMSALDIR, SIRALI
   DEĞİL. %2 + %3 toplamsalda ``0.9500·brüt``, sıralıda ``0.9506·brüt`` verir.
   İkisi de "makul" görünür; test SAYIYI çiviliyor.
2. **`ticket_net_quantity` TÜRETİME GİRMEZ.** Kağıdın neti bir TANIKTIR.
   Türetim ona kayarsa sunucu, istemcinin gönderdiği sayıyı kendi hesabının
   kaynağı yapmış olur — ``ActivityInputWrite.total_cost``un yasakladığı şey.
3. **BAYRAKLAR ÜÇ DEĞERLİ.** ``None`` (ölçülmedi) ile ``False`` (ölçüldü,
   uyuşuyor) aynı şey değil. İkisini birleştirmek, hiç fişi olmayan bir hasadı
   "satış neti aşmıyor" diye okuturdu.
4. **MEVCUT OKUMA UÇLARI DEĞİŞMEDİ.** Fişin varlığı `/api/field-harvests`,
   `/api/field-dashboard` ve parsel zaman çizelgesinin gövdesini
   DEĞİŞTİRMEMELİ. Defter iddiasının okuma tarafındaki eşi.
5. **KAPI SESSİZ KALMASIN.** ``/api/field-harvest-tickets`` hiçbir mevcut
   tarla önekinin altına düşmüyor; listeye yazılmasaydı `/api/field` genel
   kuralına düşüp SESSİZCE `field_service` iznine bağlanırdı.
6. **TABAN BİRİM YOKSA RED, VARSAYIM DEĞİL.** (0069) Ürünün `base_unit`i
   bildirilmemişken fiş yazmak 422 alır ve gövde `sebep` taşır
   (`TABAN_BILDIRILMEMIS`); girilen birimi taban SAYMAK bir olgu uydurmaktır
   (`app/units.py`, sahip kararı 2). İKİ BACAK da ölçülüyor: taban birim
   `PUT /api/products/{id}` ile yazılmadan ÖNCE 4xx, yazıldıktan SONRA 201.
   Alanı GÖNDERMEYEN bir PUT sütuna DOKUNMAZ — eski bir istemci her
   kaydetmede taban birimi silmesin diye. AÇIK `null` GÖNDEREN bir PUT ise
   REDDEDİLİR (422, `code: TABAN_BIRIM_SILINEMEZ`, sütun dokunulmamış):
   #40'taki açık-null kapısıyla aynı şekil, burada ölçülüyor.
7. **TOPLAMLAR TABAN BİRİMDE.** (0069) 2 TON + 500 KG = 2500.0000 KG'dir,
   502 DEĞİL. `gross_entered_quantity` toplamı anlamsız olduğu için özet onu
   HİÇ vermez; toplanabilir tek sütun `base_quantity`dir. Girilen birim
   KANONİK saklanır: "Ton" gönderilir, "TON" okunur — `base_unit` ile aynı
   katlama, ham dizgi hiçbir yerde tutulmaz.

--- STATİK KAPILAR NİYE VAR -------------------------------------------------

Defter testi davranışı ölçüyor; ama "tüketici fişi okumuyor" iddiası, ileride
biri `_hasat_kalemleri`ye bir LEFT JOIN eklediğinde davranış testinden ÖNCE
kırılmalı ve SEBEBİYLE kırılmalı. Üç statik kapı bunu yapıyor: tüketici
kaynağında fiş tablosunun ADI GEÇMEYECEK, ``create_harvest_ticket``
gövdesinde outbox yazıcısı ÇAĞRILMAYACAK, ve yazma şemasında sunucunun
türettiği üç alan (`derived_net_quantity`, `base_quantity`, `entered_factor`)
HİÇ OLMAYACAK.

--- MUTASYONLAR (ÖLÇÜLDÜ, bu dalın birleşmiş ağacında; sonuçlar adıyla) ------

Her mutasyon tek tek uygulandı, adı geçen test KIRMIZI oldu, geri alındı:

* `_turetilmis_net` SIRALI yapılınca (her kesinti kalan miktara)
  -> senaryo 1 (`950.6000 != 950.0000`), `KANTAR FISI SOZLESME` düşer;
  kardeş `test_kantar_fisi_defter_sqlite` de düşer (1.1400 yerine 1.1412).
* `_fis_gorunumu` kağıdın netini türetilen net yerine koyunca
  -> senaryo 3 (`KAGIDIN NETI TURETIME SIZDI`); kardeş defter smoke'u da
  düşer (KD-1 fişinde türetilen 1.1400 yerine kağıdın 1.1000'i görünür).
* `create_harvest_ticket`ta `taban_birim or payload.entered_unit` (taban
  yoksa girileni taban say) -> BACAK 1 (`TABAN YOKKEN FIS YAZILDI`).
* `HarvestTicketWrite.girilen_birim`den `turkce_katla` kaldırılınca
  -> senaryo 5 (`GIRILEN BIRIM KATLANMADI`: "Ton" olduğu gibi geri okunur).
* `update_product`taki açık-null kapısı kaldırılınca
  -> BACAK 1 devamı (`ACIK NULL SESSIZCE YAZILDI`: PUT 200, sütun NULL).
* `auth._FARM_PATH_PREFIXES`ten `/api/field-harvest-tickets` silinince
  -> `test_fis_ucu_farm_iznine_bagli` VE
  `test_farm_management_api.py::test_every_farm_endpoint_is_covered_by_the_farm_permission_prefixes`.
* `units.resolve`daki `is_finite()` kapısı kaldırılınca -> bu dosyada HİÇBİR
  ŞEY düşmez (şema katmanı önde durur); düşen yer
  `test_kantar_fisi_sonluluk.py::test_COZUCU_*`. Buraya YAZILDI ki "sonluluk
  bu dosyada ölçülüyor" sanılmasın.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

FIS_TABLOLARI = ("field_harvest_tickets", "field_harvest_ticket_deductions")


# ---------------------------------------------------------------------------
# STATİK KAPILAR — veritabanı yok.
# ---------------------------------------------------------------------------


def test_tuketici_fisin_adini_bile_gecirmiyor() -> None:
    """`field_stok_tuketici` fiş tablolarına HİÇBİR yerde değinmez.

    Bu kapı, defter testinden ÖNCE ve SEBEBİYLE kırılır: biri
    `_hasat_kalemleri`ye `LEFT JOIN field_harvest_tickets` eklediğinde burası
    kırmızı olur ve hata mesajı ne yapıldığını söyler.

    NE İDDİA ETMEZ: tüketicinin fişi DOLAYLI bir yoldan (başka bir modül
    üzerinden) okumadığını. Ölçtüğü şey, unutmanın/eklemenin pratikte aldığı
    biçim olan doğrudan referanstır.
    """
    kaynak = (BACKEND / "app" / "field_stok_tuketici.py").read_text(encoding="utf-8")
    gecenler = [ad for ad in FIS_TABLOLARI if ad in kaynak]
    assert gecenler == [], (
        "Tüketici kantar fişine değiniyor: "
        f"{gecenler}. Bu dilimin tek iddiası defterin DEĞİŞMEMESİYDİ; fişi "
        "deftere bağlamak AYRI bir iştir ve o iş hasat olayının ÜRETİM ANINI "
        "ya da düzeltici bir ikinci olayı gerektirir (bkz. göç 0069 başlığı)."
    )


def _fonksiyon(kaynak: str, ad: str) -> ast.FunctionDef:
    agac = ast.parse(kaynak)
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.FunctionDef) and dugum.name == ad:
            return dugum
    raise AssertionError(f"{ad} bulunamadı")


def test_fis_yazimi_outbox_olayi_uretmiyor() -> None:
    """`create_harvest_ticket` outbox yazıcısını ÇAĞIRMAZ.

    Çağırsaydı aynı hasat defterde İKİ KEZ üretilirdi: biri hasat yazılırken
    doğan olaydan, biri fişten.
    """
    kaynak = (BACKEND / "app" / "routers" / "farm.py").read_text(encoding="utf-8")
    govde = _fonksiyon(kaynak, "create_harvest_ticket")
    cagrilar = {
        dugum.func.id
        for dugum in ast.walk(govde)
        if isinstance(dugum, ast.Call) and isinstance(dugum.func, ast.Name)
    }
    assert "_entegrasyon_olayi_yaz" not in cagrilar, (
        "Fiş yazımı outbox olayı üretiyor: aynı hasat defterde İKİ KEZ "
        "üretilir (biri hasat olayından, biri fişten)."
    )


def test_yazma_semasinda_sunucunun_turettigi_alanlar_YOK() -> None:
    """`HarvestTicketWrite` üç türevi TAŞIMAZ; `extra=forbid` onları 422 yapar.

    `derived_net_quantity` brütten ve oranlardan, `base_quantity` ile
    `entered_factor` `units.resolve`dan üretilir. Şemada olsalardı istemci
    hesabın KAYNAĞI olurdu; alan adı şemaya girdiği anda bu kapı kırmızı olur,
    davranış testi (senaryo 6) ise 422'nin gerçekten döndüğünü ölçer.
    """
    sys.path.insert(0, str(BACKEND))
    from app.farm_schemas import HarvestTicketWrite

    alanlar = set(HarvestTicketWrite.model_fields)
    for turev in ("derived_net_quantity", "base_quantity", "entered_factor"):
        assert turev not in alanlar, (turev, sorted(alanlar))
    assert HarvestTicketWrite.model_config.get("extra") == "forbid"


def test_fis_ucu_farm_iznine_bagli() -> None:
    """GET -> `farm.view`, POST -> `farm.manage`.

    `/api/field-harvest-tickets`, `/api/field-harvests` ile BAŞLAMIYOR
    (sondaki `s`); `_FARM_PATH_PREFIXES`e yazılmasaydı `/api/field` genel
    kuralına düşer ve sessizce `field_service` iznine bağlanırdı — tarla
    modülünde iki kez yaşanmış tuzak.
    """
    sys.path.insert(0, str(BACKEND))
    from app.auth import required_permission

    assert required_permission("GET", "/api/field-harvest-tickets") == "farm.view"
    assert required_permission("POST", "/api/field-harvest-tickets") == "farm.manage"
    # Kapının GERÇEKTEN bu satır sayesinde kapalı olduğunu göster: önek
    # listesinden çıkarılmış bir yol `field_service`e düşer.
    assert required_permission("GET", "/api/field-baska-bir-sey") == "field_service"


# ---------------------------------------------------------------------------
# DAVRANIŞ — taze veritabanı, gerçek uçlar.
# ---------------------------------------------------------------------------


def run_sozlesme_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "KANTAR FISI SOZLESME TAMAM" in completed.stdout, completed.stdout


def test_kantar_fisi_sozlesme_sqlite(tmp_path: Path) -> None:
    run_sozlesme_smoke(f"sqlite:///{(tmp_path / 'kantar-sozlesme.db').as_posix()}")


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

from app.db import SessionLocal
from app.main import app

# AILENIN ORTAK SIFRESI — bkz. `test_kantar_fisi_defter.py`: uc kantar
# smoke'u PG ikizinde ayni veritabanini paylasir, giris aday dongusuyle.
ADMIN_PW = 'KantarFisi!123'


def admin_headers(client):
    for candidate in ('admin123', ADMIN_PW):
        login = client.post('/api/auth/login',
                            json={'username':'admin','password':candidate})
        if login.status_code == 200:
            break
    assert login.status_code == 200, login.text
    body = login.json()
    h = {'Authorization':'Bearer '+body['access_token'],
         'X-Company-ID':str(body['companies'][0]['id'])}
    if candidate != ADMIN_PW:
        ch = client.post('/api/auth/change-password', headers=h,
                         json={'current_password':candidate,'new_password':ADMIN_PW})
        assert ch.status_code == 200, ch.text
        h['Authorization'] = 'Bearer '+ch.json()['access_token']
    return h, int(body['companies'][0]['id'])


def fis(client, h, **alanlar):
    alanlar.setdefault('entered_unit', 'KG')
    return client.post('/api/field-harvest-tickets', headers=h, json=alanlar)


def fis_satirlari(cid):
    """(fis sayisi, kesinti sayisi) — SIFIR SATIR iddiasinin olcumu."""
    with SessionLocal() as db:
        f = db.execute(_sql(
            "SELECT COUNT(*) FROM field_harvest_tickets WHERE company_id=:c"),
            {'c': cid}).scalar_one()
        k = db.execute(_sql(
            "SELECT COUNT(*) FROM field_harvest_ticket_deductions WHERE company_id=:c"),
            {'c': cid}).scalar_one()
    return int(f), int(k)


def taban_birim_kaydi(cid, urun_id):
    with SessionLocal() as db:
        return db.execute(_sql(
            "SELECT base_unit FROM products WHERE id=:i AND company_id=:c"),
            {'i': urun_id, 'c': cid}).scalar()


def taban_birim_gunlugu(cid, urun_id):
    """`activity_logs`ta taban birim degisikligi: (ozet, details) listesi."""
    with SessionLocal() as db:
        return [(r['summary'], r['details']) for r in db.execute(_sql(
            """SELECT summary, details FROM activity_logs
               WHERE company_id=:c AND resource_type='product' AND resource_id=:i
                 AND summary LIKE '%taban birimi%' ORDER BY id"""),
            {'c': cid, 'i': urun_id}).mappings().all()]


URUN_GOVDE = {'name': 'Bugday', 'unit': 'kg'}

with TestClient(app) as client:
    h, cid = admin_headers(client)

    urun = client.post('/api/products', headers=h, json=URUN_GOVDE)
    assert urun.status_code == 201, urun.text
    urun_id = int(urun.json()['id'])

    ciftlik = client.post('/api/farms', headers=h,
                          json={'code':'ks','name':'Kantar Sozlesme'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'ksp','name':'Parsel',
                               'area_decare':'100.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,
                              'crop':'Bugday','product_id':urun_id,
                              'started_on':'2026-03-01',
                              'planted_area_decare':'100.0000'})
    assert sezon.status_code == 201, sezon.text
    sezon = sezon.json()

    def hasat(**alanlar):
        govde = {'season_id':sezon['id'],'harvested_on':'2026-07-10',
                 'quantity':'1000','unit':'KG'}
        govde.update(alanlar)
        cevap = client.post('/api/field-harvests', headers=h, json=govde)
        assert cevap.status_code == 201, cevap.text
        return cevap.json()

    # --- 0) TABAN BIRIM: IKI BACAK ----------------------------------------
    # BACAK 1 — taban bildirilmemis: fis 422, govde `sebep` tasir, SIFIR satir.
    assert taban_birim_kaydi(cid, urun_id) is None
    h0 = hasat()
    once = fis_satirlari(cid)
    red = fis(client, h, harvest_id=h0['id'], gross_entered_quantity='1000')
    assert red.status_code == 422, ('TABAN YOKKEN FIS YAZILDI', red.status_code, red.text)
    ayrinti = red.json()['detail']
    assert ayrinti['code'] == 'BIRIM_COZULEMEDI', ayrinti
    assert ayrinti['sebep'] == 'TABAN_BILDIRILMEMIS', ayrinti
    assert fis_satirlari(cid) == once, ('RED SATIR BIRAKTI', once, fis_satirlari(cid))

    # Taban birim YAZMA YOLU: `PUT /api/products/{id}`. Kucuk harf giriliyor
    # ki `turkce_katla` kanonik BUYUK bicime cevirsin ("kg" -> "KG"): cozucu
    # kapali kumeyi buyuk harfle arar, katlanmamis bir "kg" BIRIM_TANIMSIZ
    # alirdi.
    yaz = client.put(f'/api/products/{urun_id}', headers=h,
                     json={**URUN_GOVDE, 'base_unit': 'kg'})
    assert yaz.status_code == 200, yaz.text
    assert taban_birim_kaydi(cid, urun_id) == 'KG'
    # KAYIT: onceki ve sonraki deger activity_logs'ta.
    gunluk = taban_birim_gunlugu(cid, urun_id)
    assert len(gunluk) == 1, gunluk
    assert 'KG' in gunluk[0][0] and 'base_unit_after' in (gunluk[0][1] or ''), gunluk

    # BACAK 2 — taban yazildi: AYNI istek 201; katsayi 1, taban miktar = brut.
    kabul = fis(client, h, harvest_id=h0['id'], gross_entered_quantity='1000')
    assert kabul.status_code == 201, kabul.text
    kabul = kabul.json()
    assert Decimal(kabul['entered_factor']) == Decimal('1'), kabul
    assert kabul['base_quantity'] == '1000.0000', kabul
    assert kabul['entered_unit'] == 'KG', kabul

    # Alani GONDERMEYEN bir PUT sutuna DOKUNMAZ: taban birim KG kalir ve fis
    # yazilmaya devam eder. (Alani bilmeyen eski istemci = bu istek.)
    dokunma = client.put(f'/api/products/{urun_id}', headers=h, json=URUN_GOVDE)
    assert dokunma.status_code == 200, dokunma.text
    assert taban_birim_kaydi(cid, urun_id) == 'KG', 'ALANSIZ PUT TABAN BIRIMI SILDI'
    assert len(taban_birim_gunlugu(cid, urun_id)) == 1, 'DEGISMEYEN DEGER LOGLANDI'
    yine = fis(client, h, harvest_id=h0['id'], gross_entered_quantity='10',
               ticket_no='B-2')
    assert yine.status_code == 201, yine.text

    # ACIK NULL REDDEDILIR (sahip karari; #40'taki acik-null kapisiyla ayni
    # sekil): `base_unit: null` GONDERMEK "sil" demektir ve bu yol silmez —
    # AILE ICI 422, `code` govdede, sutun ve stok DOKUNULMAMIS, gunluk
    # BUYUMEMIS, fis yazilmaya devam ediyor. Katlaninca bosa dusen bir dizgi
    # ("  ") de ayni kapiya dusuyor. MUTASYON (olculdu): kapi kaldirilinca
    # burasi kirmizi (PUT 200 doner ve sutun NULL olur).
    for bos in (None, '   '):
        sil = client.put(f'/api/products/{urun_id}', headers=h,
                         json={**URUN_GOVDE, 'base_unit': bos})
        assert sil.status_code == 422, ('ACIK NULL SESSIZCE YAZILDI', bos, sil.status_code, sil.text)
        assert sil.json()['detail']['code'] == 'TABAN_BIRIM_SILINEMEZ', sil.text
        assert taban_birim_kaydi(cid, urun_id) == 'KG', ('ACIK NULL SUTUNU SILDI', bos)
    assert len(taban_birim_gunlugu(cid, urun_id)) == 1, 'RED LOGLANDI'
    hala = fis(client, h, harvest_id=h0['id'], gross_entered_quantity='10',
               ticket_no='B-3')
    assert hala.status_code == 201, hala.text

    # --- 1) BILESIM TOPLAMSAL, SIRALI DEGIL --------------------------------
    h1 = hasat(harvested_on='2026-07-11')
    f1 = fis(client, h, harvest_id=h1['id'], gross_entered_quantity='1000',
             deductions=[{'label':'Rutubet','rate_percent':'2'},
                         {'label':'Yabanci madde','rate_percent':'3'}])
    assert f1.status_code == 201, f1.text
    f1 = f1.json()
    # TOPLAMSAL: 1000 - (20 + 30) = 950.0000
    # SIRALI olsaydi: 1000 * 0.98 * 0.97 = 950.6000
    assert f1['derived_net_quantity'] == '950.0000', f1
    assert f1['deduction_rate_total'] == '5.0000', f1
    # Kagidin neti girilmemis -> ayrisacak bir sey YOK.
    assert f1['net_mismatch'] is None, f1

    # --- 2) KESINTISIZ FIS: net == brut ------------------------------------
    h2 = hasat(harvested_on='2026-07-12')
    f2 = fis(client, h, harvest_id=h2['id'], gross_entered_quantity='1000',
             ticket_net_quantity='1000').json()
    assert f2['derived_net_quantity'] == '1000.0000', f2
    assert f2['deduction_rate_total'] == '0.0000', f2
    assert f2['net_mismatch'] is False, f2

    # --- 3) KAGIDIN NETI TURETIME GIRMEZ -----------------------------------
    # Ayni kesintiler, ama kagit 900 diyor. Sunucu YINE 950 turetiyor ve
    # ayrismayi GOSTERIYOR — kagida uymuyor.
    h3 = hasat(harvested_on='2026-07-13')
    f3 = fis(client, h, harvest_id=h3['id'], gross_entered_quantity='1000',
             ticket_net_quantity='900',
             deductions=[{'label':'Rutubet','rate_percent':'2'},
                         {'label':'Yabanci madde','rate_percent':'3'}]).json()
    assert f3['derived_net_quantity'] == '950.0000', (
        'KAGIDIN NETI TURETIME SIZDI', f3)
    assert f3['ticket_net_quantity'] == '900.0000', f3
    assert f3['net_mismatch'] is True, f3

    # --- 4) `sold_exceeds_net` UC DEGERLI ----------------------------------
    def ozet(hasat_id):
        cevap = client.get('/api/field-harvest-tickets', headers=h,
                           params={'harvest_id':hasat_id})
        assert cevap.status_code == 200, cevap.text
        return cevap.json()['summary']

    # satilan GIRILMEMIS -> None
    assert ozet(h1['id'])['sold_exceeds_net'] is None, ozet(h1['id'])
    # fis YOK -> None (satilan girilmis olsa bile: net OLCULMEMIS)
    h4 = hasat(harvested_on='2026-07-14', sold_quantity='900',
               revenue_amount='45000.00')
    o4 = ozet(h4['id'])
    assert o4['sold_exceeds_net'] is None, o4
    assert o4['ticket_count'] == 0, o4
    # FIS YOKKEN TOPLAMLAR DA None — SIFIR DEGIL.
    assert o4['base_quantity_total'] is None, o4
    assert o4['derived_net_total'] is None, o4
    assert 'gross_quantity_total' not in o4, o4   # 0064 sozlugu GERI GELMEDI

    # asmiyor -> False
    fis(client, h, harvest_id=h4['id'], gross_entered_quantity='1000',
        deductions=[{'label':'Fire','rate_percent':'5'}])
    o4 = ozet(h4['id'])
    assert o4['derived_net_total'] == '950.0000', o4
    assert o4['sold_exceeds_net'] is False, o4

    # asiyor -> True
    h5 = hasat(harvested_on='2026-07-15', sold_quantity='1000',
               revenue_amount='50000.00')
    fis(client, h, harvest_id=h5['id'], gross_entered_quantity='1000',
        deductions=[{'label':'Fire','rate_percent':'10'}])
    o5 = ozet(h5['id'])
    assert o5['derived_net_total'] == '900.0000', o5
    assert o5['sold_exceeds_net'] is True, o5

    # --- 5) BIR HASAT, BIRDEN COK FIS, FARKLI BIRIMLER: TOPLAM TABANDA ------
    # 2 TON + 500 KG = 2500.0000 KG — 502 DEGIL. Katsayi satirda kanit olarak
    # durur ve YUVARLANMAZ.
    h6 = hasat(harvested_on='2026-07-16', quantity='2500')
    c1 = fis(client, h, harvest_id=h6['id'], ticket_no='C-1',
             gross_entered_quantity='2', entered_unit='Ton',
             deductions=[{'label':'Fire','rate_percent':'10'}])
    assert c1.status_code == 201, c1.text
    c1 = c1.json()
    # GIRILEN BIRIM KANONIK SAKLANIR: "Ton" gonderildi, "TON" geri okunur.
    # Sahip karari: ham dizgi HICBIR yerde tutulmaz, `products.base_unit` ile
    # AYNI katlama (`units.turkce_katla`); "Ton"/"ton"/"TON" TEK degerdir.
    # MUTASYON (olculdu): semadaki katlama kaldirilinca burasi kirmizi olur
    # ("Ton" oldugu gibi geri okunur; katsayi yine 1000 gelir cunku cozucu
    # kendi kopyasini katlar — yani bu iddia YALNIZ burada olculuyor).
    assert c1['entered_unit'] == 'TON', ('GIRILEN BIRIM KATLANMADI', c1)
    with SessionLocal() as db:
        saklanan = db.execute(_sql(
            "SELECT entered_unit FROM field_harvest_tickets WHERE id=:i AND company_id=:c"),
            {'i': c1['id'], 'c': cid}).scalar_one()
    assert saklanan == 'TON', ('HAM DIZGI SAKLANDI', saklanan)
    assert Decimal(c1['entered_factor']) == Decimal('1000'), c1
    assert c1['base_quantity'] == '2000.0000', c1
    assert c1['derived_net_quantity'] == '1.8000', c1  # net FISIN biriminde
    c2 = fis(client, h, harvest_id=h6['id'], ticket_no='C-2',
             gross_entered_quantity='500')
    assert c2.status_code == 201, c2.text
    o6 = ozet(h6['id'])
    assert o6['ticket_count'] == 2, o6
    assert o6['base_quantity_total'] == '2500.0000', ('2 TON + 500 KG', o6)

    # --- 6) DOGRULAMA KAPILARI: HEPSI 4xx, HICBIRI SATIR BIRAKMAZ ----------
    once = fis_satirlari(cid)

    asiri = fis(client, h, harvest_id=h1['id'], gross_entered_quantity='100',
                deductions=[{'label':'a','rate_percent':'60'},
                            {'label':'b','rate_percent':'50'}])
    assert asiri.status_code == 422, asiri.text

    tekrar_etiket = fis(client, h, harvest_id=h1['id'], gross_entered_quantity='100',
                        deductions=[{'label':'Rutubet','rate_percent':'1'},
                                    {'label':'rutubet','rate_percent':'2'}])
    assert tekrar_etiket.status_code == 422, tekrar_etiket.text

    sifir = fis(client, h, harvest_id=h1['id'], gross_entered_quantity='0')
    assert sifir.status_code == 422, sifir.text

    negatif_net = fis(client, h, harvest_id=h1['id'], gross_entered_quantity='10',
                      ticket_net_quantity='-1')
    assert negatif_net.status_code == 422, negatif_net.text

    # Sunucunun turettigi UC alan ISTEMCIDEN ALINMAZ: sema `extra=forbid`.
    for turev, deger in (('derived_net_quantity', '9999'),
                         ('base_quantity', '9999'),
                         ('entered_factor', '1')):
        turev_cevap = fis(client, h, harvest_id=h1['id'], gross_entered_quantity='10',
                          **{turev: deger})
        assert turev_cevap.status_code == 422, (turev, turev_cevap.text)

    # BIRIM COZUMU REDLERI AILE ICINDE ve `sebep` govdede: tanimsiz birim ile
    # boyut uyusmazligi (hacim -> kutle, yogunluk evrensel degil).
    tanimsiz = fis(client, h, harvest_id=h1['id'], gross_entered_quantity='10',
                   entered_unit='CUVAL')
    assert tanimsiz.status_code == 422, tanimsiz.text
    assert tanimsiz.json()['detail']['sebep'] == 'BIRIM_TANIMSIZ', tanimsiz.text
    boyut = fis(client, h, harvest_id=h1['id'], gross_entered_quantity='10',
                entered_unit='LT')
    assert boyut.status_code == 422, boyut.text
    assert boyut.json()['detail']['sebep'] == 'BOYUT_UYUSMAZLIGI', boyut.text

    assert fis_satirlari(cid) == once, ('RED SATIR BIRAKTI', once, fis_satirlari(cid))

    # --- 7) KAGIDIN KIMLIGI: AYNI HASADA AYNI NUMARA IKI KEZ GIREMEZ ------
    h7 = hasat(harvested_on='2026-07-17')
    ilk = fis(client, h, harvest_id=h7['id'], ticket_no='D-9', gross_entered_quantity='10')
    assert ilk.status_code == 201, ilk.text
    ikinci = fis(client, h, harvest_id=h7['id'], ticket_no='D-9', gross_entered_quantity='10')
    assert ikinci.status_code == 409, ikinci.text
    # BEDEL ADI KONMUS: numarasiz fis IKI KEZ girilebilir. Bu bir kusur degil,
    # kabul edilmis bir sinir (bkz. goc 0069 basligi) ve BURADA DONDU.
    a = fis(client, h, harvest_id=h7['id'], gross_entered_quantity='10')
    b = fis(client, h, harvest_id=h7['id'], gross_entered_quantity='10')
    assert (a.status_code, b.status_code) == (201, 201), (a.text, b.text)
    assert ozet(h7['id'])['ticket_count'] == 3, ozet(h7['id'])

    # --- 8) KIRACI: OLMAYAN/BASKA FIRMANIN HASADI 404 ---------------------
    yok = fis(client, h, harvest_id=987654, gross_entered_quantity='10')
    assert yok.status_code == 404, yok.text
    liste_yok = client.get('/api/field-harvest-tickets', headers=h,
                           params={'harvest_id':987654})
    assert liste_yok.status_code == 404, liste_yok.text

    # --- 9) MEVCUT OKUMA UCLARI DEGISMEDI ---------------------------------
    # Fisin varligi hasat listesini, panoyu ve zaman cizelgesini
    # DEGISTIRMEMELI. Defter iddiasinin okuma tarafindaki esi.
    h8 = hasat(harvested_on='2026-07-18', sold_quantity='500',
               revenue_amount='25000.00')

    def yuzeyler():
        return (
            client.get('/api/field-harvests', headers=h,
                       params={'season_id':sezon['id']}).json(),
            client.get('/api/field-dashboard', headers=h).json(),
            client.get(f"/api/farm-parcels/{parsel['id']}/timeline", headers=h).json(),
        )

    once_y = yuzeyler()
    fis(client, h, harvest_id=h8['id'], ticket_no='E-1', gross_entered_quantity='1.2',
        entered_unit='TON', ticket_net_quantity='1.1',
        deductions=[{'label':'Rutubet','rate_percent':'4'}])
    sonra_y = yuzeyler()
    for ad, a, b in zip(('field-harvests', 'field-dashboard', 'timeline'),
                        once_y, sonra_y):
        assert a == b, ('FIS MEVCUT OKUMA UCUNU DEGISTIRDI: ' + ad, a, b)

    # Yeni alanlar YALNIZ fis ucunda gorunur.
    (hasat_satiri,) = [x for x in sonra_y[0]['items'] if x['id'] == h8['id']]
    for alan in ('gross_entered_quantity', 'entered_unit', 'entered_factor',
                 'base_quantity', 'derived_net_quantity', 'net_mismatch',
                 'sold_exceeds_net', 'ticket_net_quantity'):
        assert alan not in hasat_satiri, (alan, hasat_satiri)

    print('KANTAR FISI SOZLESME TAMAM')
'''
