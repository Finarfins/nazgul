"""BKÜ kataloğu: PHI gün sayısının etiketten gelmesi (göç 20260901_0063).

PHI kilidi 0046/0048'den beri ÇALIŞIYOR. Bu dosya kilidi değil, kilidi
BESLEYEN SAYIYI sınıyor: o sayı 0044'ten beri elle giriliyordu ve operatör
yazmayı unuttuğunda kilit sessizce hiçbir şey yapmıyordu.

Testlerin ağırlığı yedi iddiada:

1. **Katalog boş bırakılan süreyi ÇÖZER** — asıl kazanç bu. Operatör süreyi
   yazmasa da kilit devreye girer.
2. **Operatörün değeri KAZANIR** — katalog önerir, saha karar verir. Etiketi
   elinde tutan kişi sistemi düzeltebilmeli.
3. **Üstüne yazma KAYDA GEÇER** — sessiz bir üstüne yazma, denetimde
   görünmeyen bir karardır. Katalogun dediği ayrı sütunda durur.
4. **Uyuşma üstüne yazma SAYILMAZ** — her eşitliği üstüne yazma saymak, gerçek
   üstüne yazmaları gürültüde kaybederdi.
5. **Katalogdan çözülen süre GERÇEK kilit üretir** — çözülen değer yalnız
   kayıtta durmuyor, erken hasadı fiilen engelliyor.
6. **İSTANBUL günü korunuyor** — UTC'ye kayarsa hasat BİR GÜN ERKEN serbest
   kalır. Bu, teste bilerek UTC'de bir önceki güne düşen bir saat konarak
   ölçülüyor.
7. **Çapraz kiracı: B firması A'nın kataloğunu ne GÖRÜR ne KULLANIR** — ikincisi
   birincisinden önemli: katalog sessizce çözülen bir değer üretiyor, sızıntı
   burada veri sızdırmakla kalmaz, başka firmanın süresini bu firmanın kaydına
   YAZARDI.

Ayrıca geriye dönük davranış: kataloğa BAĞLI OLMAYAN faaliyet, bu göçten
öncekiyle birebir aynı davranır (boş süre ihlal değildir).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_katalog_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_bku_katalogu_sqlite(tmp_path: Path) -> None:
    run_katalog_smoke(f"sqlite:///{(tmp_path / 'farm-katalog.db').as_posix()}")


def test_katalog_bitki_eslesmesi_dialektten_bagimsiz() -> None:
    """Bitki karşılaştırması PYTHON'da yapılıyor, SQL ``LOWER()``ında değil.

    Türkçe'de İ/ı eşlemesi diyalekte bağlıdır: SQLite'ın ``LOWER``ı ASCII
    dışına dokunmaz, PostgreSQL'inki yerel ayara göre davranır. Karşılaştırma
    SQL'de yapılsaydı aynı katalog iki diyalektte FARKLI çözülür ve ikizi
    koşulan bir test bunu yakalayamazdı — çünkü her iki koşu da kendi
    diyalektinde tutarlı olurdu.

    KURAL (2026-09-01'de ÖLÇÜLDÜ ve DEĞİŞTİ): Türkçe katlama, ``lower()`` ile.
    Bu test ilk yazıldığında yalnız ASCII iddia ediyordu ve kuralı ÇİVİLEMİYORDU;
    o boşlukta sunucu ``casefold()``, ön yüz ``toLocaleLowerCase('tr')``
    kullanıyordu ve ikisi tam İ karakterinde AYRILIYORDU. Aşağıdaki iddialar
    kuralı üç yönden çiviliyor: ``casefold()``a, ``lower()``a ya da SQL
    ``LOWER()``a dönülürse en az biri DÜŞER.
    """
    sys.path.insert(0, str(BACKEND))
    from app.routers.farm import _bitki_esit

    assert _bitki_esit("Domates", "domates")
    assert _bitki_esit("  DOMATES  ".strip(), "Domates")
    assert not _bitki_esit("Domates", "Biber")

    # --- KURAL TÜRKÇE KATLAMA: aşağıdakiler `casefold()` ile DÜŞER ----------
    # `"İNCİR".casefold()` = 'i̇ncir' (i + U+0307), yani 'incir'e eşleşmez.
    # Türkçe klavyede BÜYÜK yazmak İ üretir; bu satırlar o girdinin
    # katalogdaki küçük harfli satırı BULDUĞUNU ölçüyor.
    assert _bitki_esit("\u0130NC\u0130R", "incir")          # İNCİR / incir
    assert _bitki_esit("\u0130ncir", "incir")
    assert _bitki_esit("B\u0130BER", "biber")               # BİBER / biber
    assert _bitki_esit("ZEYT\u0130N", "zeytin")
    # Ve ters yön: Türkçe'de `I`nın küçüğü `ı`dır.
    assert _bitki_esit("MISIR", "m\u0131s\u0131r")          # MISIR / mısır
    assert _bitki_esit("PATLICAN", "patl\u0131can")
    assert _bitki_esit("ISPANAK", "\u0131spanak")
    assert _bitki_esit("FINDIK", "f\u0131nd\u0131k")

    # --- KURAL `lower()`, `casefold()` DEĞİL -------------------------------
    # `casefold()` ön yüzün `toLocaleLowerCase('tr')`ından FAZLA iş yapar: ß'yi
    # 'ss'e AÇAR, JavaScript AÇMAZ. Bu çift bilerek BÜYÜK HARF İÇERMİYOR —
    # `WEISSKOHL` yazılsaydı içindeki `I` de Türkçe katlanır ve iddia kuralı
    # ayırt etmezdi (ölçüldü). Katlamanın sonu `casefold()`a çevrilirse bu
    # satır DÜŞER; ikizlik tam burada kopardı.
    assert not _bitki_esit("Wei\u00dfkohl", "weisskohl")

    # --- BİLEREK KAYBEDİLEN: LATİN yazımlı `I` -----------------------------
    # Kural bunu kaybediyor ve kaybı KAYIT ALTINDA. `casefold()`/`lower()` ile
    # eşleşirdi; Türkçe katlamada `Iceberg` → `ıceberg`.
    assert not _bitki_esit("Iceberg", "iceberg")

    # --- SIRA: `I` + U+0307 ------------------------------------------------
    # Unicode'un Türkçe özel kuralı bu diziyi `i`ye indirir. Katlama çıplak
    # `I`yı ÖNCE `ı` yapsaydı burada hiçbir yerde bulunmayan `ı`+U+0307
    # kalırdı; bu satır sıranın doğru olduğunu ölçüyor.
    assert _bitki_esit("I\u0307ncir", "incir")

    # --- SQL `LOWER()` BU İDDİALARI TAŞIYAMAZ ------------------------------
    # SQLite'ın `LOWER`ı ASCII dışına DOKUNMAZ: yukarıdaki hiçbir Türkçe
    # satırı geçmezdi. Karşılaştırmanın Python'da olması bu yüzden.
    assert "\u0130NC\u0130R".lower() != "incir"
    assert "MISIR".lower() != "m\u0131s\u0131r"


_SMOKE = r'''
from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FarmKatalog!12345'


def admin_headers(client):
    login = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    body = login.json()
    h = {'Authorization':'Bearer '+body['access_token'],
         'X-Company-ID':str(body['companies'][0]['id'])}
    ch = client.post('/api/auth/change-password', headers=h,
                     json={'current_password':'admin123','new_password':ADMIN_PW})
    assert ch.status_code == 200, ch.text
    h['Authorization'] = 'Bearer '+ch.json()['access_token']
    return h


def yeni_sezon(client, h, parsel_id, yil, bitki):
    s = client.post('/api/crop-seasons', headers=h,
                    json={'parcel_id':parsel_id,'season_year':yil,'crop':bitki,
                          'started_on':f'{yil}-03-01'})
    assert s.status_code == 201, s.text
    return s.json()


with TestClient(app) as client:
    h = admin_headers(client)

    urun = client.post('/api/products', headers=h,
                       json={'name':'ORNEK BKU','product_code':'BKU-1',
                             'sale_price':'250.00','purchase_price':'150.00',
                             'vat_rate':20,'unit':'LT'})
    assert urun.status_code == 201, urun.text
    urun_id = urun.json()['id']

    ciftlik = client.post('/api/farms', headers=h,
                          json={'code':'k1','name':'Katalog Ciftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'kp','name':'Katalog Parsel',
                               'area_decare':'30.0000'}).json()

    # --- 0) KATALOG CRUD --------------------------------------------------
    # Bitkiden BAGIMSIZ satir (crop bos = butun bitkiler).
    genel = client.post('/api/plant-protection-products', headers=h,
                        json={'product_id':urun_id,'preharvest_interval_days':14,
                              'registration_no':'RUHSAT-1'})
    assert genel.status_code == 201, genel.text
    assert genel.json()['crop'] == '', genel.json()
    assert genel.json()['preharvest_interval_days'] == 14, genel.json()

    # Bitkiye OZEL satir ayni urun icin EKLENEBILIR.
    ozel = client.post('/api/plant-protection-products', headers=h,
                       json={'product_id':urun_id,'crop':'Domates',
                             'preharvest_interval_days':21})
    assert ozel.status_code == 201, ozel.text

    # Ayni urun+bitki IKINCI KEZ eklenemez: cozum belirsiz kalmasin.
    tekrar = client.post('/api/plant-protection-products', headers=h,
                         json={'product_id':urun_id,'crop':'Domates',
                               'preharvest_interval_days':30})
    assert tekrar.status_code == 409, tekrar.text

    liste = client.get('/api/plant-protection-products', headers=h).json()
    assert liste['total'] == 2, liste

    # Guncelleme iyimser kilitli.
    guncel = client.put('/api/plant-protection-products/%d' % ozel.json()['id'], headers=h,
                        json={'product_id':urun_id,'crop':'Domates',
                              'preharvest_interval_days':21,'status':'ACTIVE',
                              'expected_updated_at':ozel.json()['updated_at']})
    assert guncel.status_code == 200, guncel.text
    bayat = client.put('/api/plant-protection-products/%d' % ozel.json()['id'], headers=h,
                       json={'product_id':urun_id,'crop':'Domates',
                             'preharvest_interval_days':21,'status':'ACTIVE',
                             'expected_updated_at':ozel.json()['updated_at']})
    assert bayat.status_code == 409, bayat.text

    # --- 1) KATALOG BOS BIRAKILAN SUREYI COZER ---------------------------
    # Bitkiye OZEL satir (21) bitkiden bagimsiz satiri (14) YENER.
    sezon = yeni_sezon(client, h, parsel['id'], 2026, 'Domates')
    ilac = client.post('/api/field-activities', headers=h,
                       json={'season_id':sezon['id'],'activity_type':'SPRAYING',
                             'performed_at':'2026-06-01T09:00:00+03:00',
                             'applied_area_decare':'30.0000',
                             'inputs':[{'product_id':urun_id,'input_name':'ORNEK BKU',
                                        'quantity':'10','unit':'LT','dose':'2',
                                        'dose_unit':'LT/DA'}]})
    assert ilac.status_code == 201, ilac.text
    g = ilac.json()
    assert g['preharvest_interval_days'] == 21, g
    assert g['preharvest_source'] == 'CATALOGUE', g
    assert g['catalogue_preharvest_days'] == 21, g

    # --- 5) COZULEN SURE GERCEK KILIT URETIR ------------------------------
    # 1 Haziran + 21 gun -> guvenli tarih 22 Haziran. Operator hicbir sure
    # GIRMEDI; kilit yalnizca katalogdan geldi.
    erken = client.post('/api/field-harvests', headers=h,
                        json={'season_id':sezon['id'],'harvested_on':'2026-06-15',
                              'quantity':'5000','unit':'KG'})
    assert erken.status_code == 422, erken.text
    assert '2026-06-22' in erken.json()['detail'], erken.json()

    # Sure dolduktan sonra mesru hasat TEMIZ kaydediliyor.
    temiz = client.post('/api/field-harvests', headers=h,
                        json={'season_id':sezon['id'],'harvested_on':'2026-06-22',
                              'quantity':'4000','unit':'KG'})
    assert temiz.status_code == 201, temiz.text
    assert temiz.json()['safety_warning'] is None, temiz.json()

    # --- 2+3) OPERATOR KAZANIR VE USTUNE YAZMA KAYDA GECER ---------------
    s2 = yeni_sezon(client, h, parsel['id'], 2025, 'Domates')
    yazan = client.post('/api/field-activities', headers=h,
                        json={'season_id':s2['id'],'activity_type':'SPRAYING',
                              'performed_at':'2025-06-01T09:00:00+03:00',
                              'applied_area_decare':'30.0000',
                              'preharvest_interval_days':7,
                              'inputs':[{'product_id':urun_id,'input_name':'ORNEK BKU',
                                         'quantity':'10','unit':'LT','dose':'2',
                                         'dose_unit':'LT/DA'}]})
    assert yazan.status_code == 201, yazan.text
    y = yazan.json()
    # Operatorun degeri ETKIN.
    assert y['preharvest_interval_days'] == 7, y
    # Katalogun dedigi KAYBOLMADI - denetim ikisini de gorebilmeli.
    assert y['catalogue_preharvest_days'] == 21, y
    assert y['preharvest_source'] == 'OPERATOR_OVERRIDE', y

    # Etkin sure 7 gun: 8 Haziran serbest, 7 Haziran degil.
    assert client.post('/api/field-harvests', headers=h,
                       json={'season_id':s2['id'],'harvested_on':'2025-06-07',
                             'quantity':'1','unit':'KG'}).status_code == 422
    assert client.post('/api/field-harvests', headers=h,
                       json={'season_id':s2['id'],'harvested_on':'2025-06-08',
                             'quantity':'1','unit':'KG'}).status_code == 201

    # --- 4) UYUSMA USTUNE YAZMA SAYILMAZ ---------------------------------
    s3 = yeni_sezon(client, h, parsel['id'], 2024, 'Domates')
    uyusan = client.post('/api/field-activities', headers=h,
                         json={'season_id':s3['id'],'activity_type':'SPRAYING',
                               'performed_at':'2024-06-01T09:00:00+03:00',
                               'applied_area_decare':'30.0000',
                               'preharvest_interval_days':21,
                               'inputs':[{'product_id':urun_id,'input_name':'ORNEK BKU',
                                          'quantity':'10','unit':'LT','dose':'2',
                                          'dose_unit':'LT/DA'}]}).json()
    assert uyusan['preharvest_source'] == 'OPERATOR', uyusan
    assert uyusan['catalogue_preharvest_days'] == 21, uyusan

    # --- KATALOGA BAGLI OLMAYAN FAALIYET: ESKISI GIBI --------------------
    # Serbest metin girdi (urun bagi YOK) cozulmez; sure BOS kalir ve bos
    # ihlal DEGILDIR. Bu gocten onceki davranisin birebir aynisi.
    s4 = yeni_sezon(client, h, parsel['id'], 2023, 'Biber')
    serbest = client.post('/api/field-activities', headers=h,
                          json={'season_id':s4['id'],'activity_type':'SPRAYING',
                                'performed_at':'2023-06-01T09:00:00+03:00',
                                'applied_area_decare':'30.0000',
                                'inputs':[{'input_name':'KENDI KOMPOSTUM',
                                           'quantity':'10','unit':'KG','dose':'2',
                                           'dose_unit':'KG/DA'}]}).json()
    assert serbest['preharvest_interval_days'] is None, serbest
    assert serbest['preharvest_source'] is None, serbest
    assert serbest['catalogue_preharvest_days'] is None, serbest
    assert client.post('/api/field-harvests', headers=h,
                       json={'season_id':s4['id'],'harvested_on':'2023-06-02',
                             'quantity':'100','unit':'KG'}).status_code == 201

    # Bitkisi katalogda OLMAYAN sezon: bitkiden bagimsiz satira (14) duser.
    s5 = yeni_sezon(client, h, parsel['id'], 2022, 'Patlican')
    yedek = client.post('/api/field-activities', headers=h,
                        json={'season_id':s5['id'],'activity_type':'SPRAYING',
                              'performed_at':'2022-06-01T09:00:00+03:00',
                              'applied_area_decare':'30.0000',
                              'inputs':[{'product_id':urun_id,'input_name':'ORNEK BKU',
                                         'quantity':'10','unit':'LT','dose':'2',
                                         'dose_unit':'LT/DA'}]}).json()
    assert yedek['preharvest_interval_days'] == 14, yedek
    assert yedek['preharvest_source'] == 'CATALOGUE', yedek

    # --- 6) ISTANBUL GUNU: UTC'YE KAYARSA BU IDDIA DUSER -----------------
    # 2021-06-10T01:30+03:00 = 2021-06-09T22:30Z. Istanbul gunu 10 Haziran,
    # UTC gunu 9 Haziran. Katalogdan 14 gun cozuluyor:
    #     Istanbul'a gore guvenli tarih 24 Haziran
    #     UTC'ye gore olsaydi          23 Haziran
    # 23 Haziran'in REDDEDILMESI, hesabin Istanbul gununde yapildigini
    # kanitliyor. Biri `_yerel_gun`u UTC'ye cevirirse bu satir duser.
    s6 = yeni_sezon(client, h, parsel['id'], 2021, 'Patlican')
    client.post('/api/field-activities', headers=h,
                json={'season_id':s6['id'],'activity_type':'SPRAYING',
                      'performed_at':'2021-06-10T01:30:00+03:00',
                      'applied_area_decare':'30.0000',
                      'inputs':[{'product_id':urun_id,'input_name':'ORNEK BKU',
                                 'quantity':'10','unit':'LT','dose':'2',
                                 'dose_unit':'LT/DA'}]})
    utc_olsaydi = client.post('/api/field-harvests', headers=h,
                              json={'season_id':s6['id'],'harvested_on':'2021-06-23',
                                    'quantity':'1','unit':'KG'})
    assert utc_olsaydi.status_code == 422, (
        'UTC gunune kayma: hasat BIR GUN ERKEN serbest kaldi')
    assert '2021-06-24' in utc_olsaydi.json()['detail'], utc_olsaydi.json()
    assert client.post('/api/field-harvests', headers=h,
                       json={'season_id':s6['id'],'harvested_on':'2021-06-24',
                             'quantity':'1','unit':'KG'}).status_code == 201

    # --- 7) CAPRAZ KIRACI ------------------------------------------------
    b = client.post('/api/companies', headers=h, json={'name':'Katalog B'}).json()
    hb = dict(h, **{'X-Company-ID': str(b['id'])})

    # GORMEZ.
    assert client.get('/api/plant-protection-products', headers=hb).json()['total'] == 0
    # Tekil okuma da 404 - 403 "var ama sana kapali" bilgisini sizdirirdi.
    assert client.get('/api/plant-protection-products/%d' % ozel.json()['id'],
                      headers=hb).status_code == 404
    # YAZAMAZ: A'nin urunune B'nin katalogunda satir acilamaz.
    assert client.post('/api/plant-protection-products', headers=hb,
                       json={'product_id':urun_id,
                             'preharvest_interval_days':99}).status_code == 404
    # GUNCELLEYEMEZ.
    assert client.put('/api/plant-protection-products/%d' % ozel.json()['id'], headers=hb,
                      json={'product_id':urun_id,'crop':'Domates',
                            'preharvest_interval_days':1,'status':'ACTIVE',
                            'expected_updated_at':ozel.json()['updated_at']}
                      ).status_code == 404

    # VE KULLANAMAZ: B'nin faaliyeti A'nin katalog satirindan sure COZMEZ.
    # Bu, listeleme sizintisindan daha agir olurdu - baska firmanin suresi bu
    # firmanin kaydina YAZILMIS olurdu.
    burun = client.post('/api/products', headers=hb,
                        json={'name':'B URUN','product_code':'B-1',
                              'sale_price':'10.00','purchase_price':'5.00',
                              'vat_rate':20,'unit':'LT'}).json()
    bc = client.post('/api/farms', headers=hb, json={'code':'b1','name':'B Ciftlik'}).json()
    bp = client.post('/api/farm-parcels', headers=hb,
                     json={'farm_id':bc['id'],'code':'bp','name':'B Parsel',
                           'area_decare':'10.0000'}).json()
    bs = client.post('/api/crop-seasons', headers=hb,
                     json={'parcel_id':bp['id'],'season_year':2026,'crop':'Domates',
                           'started_on':'2026-03-01'}).json()
    bf = client.post('/api/field-activities', headers=hb,
                     json={'season_id':bs['id'],'activity_type':'SPRAYING',
                           'performed_at':'2026-06-01T09:00:00+03:00',
                           'applied_area_decare':'10.0000',
                           'inputs':[{'product_id':burun['id'],'input_name':'B URUN',
                                      'quantity':'1','unit':'LT','dose':'1',
                                      'dose_unit':'LT/DA'}]}).json()
    assert bf['preharvest_interval_days'] is None, bf
    assert bf['preharvest_source'] is None, bf
    assert bf['catalogue_preharvest_days'] is None, bf

    print('TARLA BKU KATALOGU TAMAM')
'''
