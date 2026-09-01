"""BKÜ kataloğunun dosyadan doldurulması ve satır KÖKENİ (göç 20260901_0064).

Göç 0063 kataloğu açtı ama doldurma yolu TEK TEK FORM'du. BKÜ listesi
kalabalık olan bir firma için bu gerçek bir veri girişi yüküdür ve katalog boş
kaldığı sürece PHI kilidi 0063 öncesindeki gibi susmaya devam eder.

Bu dosya beş iddiayı sınıyor ve beşi de "olsa iyi olur" değil, YANLIŞ
YAPILDIĞINDA ZARAR VEREN türden:

1. **Bir bozuk satır YALNIZ KENDİSİNİ reddeder.** 200 satırlık listede bir
   yazım hatası olan çiftçi diğer 199'u kaybetmemeli. Buradaki test bunun
   ölçülebilir hâli: iyi satırlar YAZILIR, bozuk olan yazılmaz.

2. **Reddedilen satırlar SAYI DEĞİL LİSTE olarak döner.** "3 satır atlandı"
   kullanıcıya hangisini düzelteceğini SÖYLEMEZ. Yanıt her reddin satır
   numarasını ve gerekçesini taşıyor; test satır numaralarını TEK TEK iddia
   ediyor, uzunluğunu değil — yalnız sayıyı sınayan bir test, satır numarası
   yanlış hesaplansa da yeşil kalırdı.

3. **Mevcut satırla çakışma REDDEDİLİR, GÜNCELLENMEZ.** Bu, deponun diğer
   içe aktarmalarından (`routers/imports.py` müşteri/ürün: eşleşeni günceller)
   BİLEREK ayrılıyor. Test yalnız "reddedildi"yi değil, ESKİ DEĞERİN YERİNDE
   DURDUĞUNU ölçüyor: reddin asıl anlamı budur ve yalnız yanıt gövdesine bakan
   bir test, sessizce üstüne yazan bir sürümü yakalayamazdı.

4. **Köken kayıt altında.** Dosyadan gelen satır `origin='IMPORT'` ve
   `origin_reference='<dosya>:<satır>'`, formdan gelen satır `origin='MANUAL'`.
   "Katalogdaki 21 nereden geldi" sorusunun cevabı bir adım daha geriye,
   firmanın kendi dosyasına kadar gidiyor.

5. **DÜZENLEME KÖKENİ DEĞİŞTİRMEZ.** İçe aktarılan bir satır ekrandan
   düzeltildiğinde `origin` `IMPORT` kalır. Ters tercih denetçiden bilgi
   SAKLARDI: satırın bir listeden geldiği gerçeği ilk düzeltmede silinirdi.

Ayrıca zincirin ucu: içe aktarılan satırdan ÇÖZÜLEN PHI, elle girilenle
birebir aynı kilidi üretiyor. İçe aktarma bir "raporlama" özelliği değil;
yazdığı satır gerçekten hasadı durduruyor.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_ice_aktarma_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_bku_ice_aktarma_sqlite(tmp_path: Path) -> None:
    run_ice_aktarma_smoke(f"sqlite:///{(tmp_path / 'farm-ice-aktarma.db').as_posix()}")


def test_kesirli_gun_sessizce_yuvarlanmaz() -> None:
    """``20,6`` gün REDDEDİLİR; ``21,0`` KABUL edilir.

    ``int(float(...))`` kısa yolu ikisini de sessizce tamsayıya çevirirdi ve
    ``20,6`` -> 20 AŞAĞI yuvarlanmış bir gün demektir: süresi dolmadan hasada
    izin verir. Bu bir biçim ayrıntısı değil, kalıntı riskidir. ``21,0`` ise
    bir yazım değil elektronik tablo BİÇİMİdir ve reddedilseydi kullanıcı
    kendi doğru dosyasının neden geri çevrildiğini anlamazdı.
    """
    sys.path.insert(0, str(BACKEND))
    from app.routers.farm import _ice_aktarma_tamsayi

    assert _ice_aktarma_tamsayi("21") == 21
    assert _ice_aktarma_tamsayi(21) == 21
    assert _ice_aktarma_tamsayi("21,0") == 21
    assert _ice_aktarma_tamsayi("21.0") == 21
    assert _ice_aktarma_tamsayi(21.0) == 21
    # AŞAĞI YUVARLAMA YOK.
    assert _ice_aktarma_tamsayi("20,6") is None
    assert _ice_aktarma_tamsayi(20.6) is None
    assert _ice_aktarma_tamsayi("yirmi bir") is None
    assert _ice_aktarma_tamsayi("") is None
    assert _ice_aktarma_tamsayi(None) is None
    # `bool` Python'da `int`tir; "Evet" yazılmış bir hücre 1 gün OLMAMALI.
    assert _ice_aktarma_tamsayi(True) is None


_SMOKE = r'''
from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FarmIceAktarma!12345'
UC = '/api/plant-protection-products'


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


def urun_ac(client, h, ad, kod):
    r = client.post('/api/products', headers=h,
                    json={'name':ad,'product_code':kod,'sale_price':'250.00',
                          'purchase_price':'150.00','vat_rate':20,'unit':'LT'})
    assert r.status_code == 201, r.text
    return r.json()['id']


def yukle(client, h, govde, ad='bku-listesi.csv'):
    return client.post(UC + '/import', headers=h,
                       files={'file': (ad, govde.encode('utf-8'), 'text/csv')})


def redler(yanit):
    """{satir_no: gerekce} — testler satir numarasini TEK TEK iddia etsin."""
    return {r['row']: r['message'] for r in yanit['rejected']}


def sadelestir(metin):
    return metin.replace('ı','i').replace('İ','I').lower()


with TestClient(app) as client:
    h = admin_headers(client)
    ilac = urun_ac(client, h, 'ORNEK BKU', 'BKU-1')
    ikinci = urun_ac(client, h, 'IKINCI BKU', 'BKU-2')

    ciftlik = client.post('/api/farms', headers=h,
                          json={'code':'i1','name':'Ice Aktarma Ciftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'ip','name':'Ice Parsel',
                               'area_decare':'30.0000'}).json()

    # --- 1) BIR BOZUK SATIR YALNIZ KENDISINI REDDEDER --------------------
    # Alti veri satiri; UCU saglam, UCU bozuk. Saglamlar YAZILMALI.
    dosya = (
        'Urun Kodu,Urun Adi,Bitki,Ruhsat No,Hasat Bekleme (Gun),Giris Yasagi (Gun),Not\n'
        'BKU-1,,Domates,RUHSAT-1,21,2,etiketten\n'          # 2  saglam
        'BKU-1,,,RUHSAT-1,14,,butun bitkiler\n'             # 3  saglam
        'BKU-YOK,,Domates,,10,,\n'                          # 4  RED: urun yok
        ',,Elma,,7,,\n'                                     # 5  RED: urun bos
        'BKU-2,,Elma,,yirmi,,\n'                            # 6  RED: sayi degil
        'BKU-2,,Elma,,7,,\n'                                # 7  saglam
    )
    y = yukle(client, h, dosya)
    assert y.status_code == 200, y.text
    b = y.json()
    assert b['total_rows'] == 6, b
    assert b['imported'] == 3, b
    # SATIR NUMARALARI TEK TEK: yalniz uzunlugu sinayan bir test, satir
    # numarasi yanlis hesaplansa da yesil kalirdi.
    assert set(redler(b)) == {4, 5, 6}, b['rejected']
    assert 'bulunamadi' in sadelestir(redler(b)[4]), b['rejected']
    assert 'tam sayi' in sadelestir(redler(b)[6]), b['rejected']
    # Ve reddin gerekcesi satirin KENDISINI isaret ediyor.
    assert b['rejected'][0]['product'] == 'BKU-YOK', b['rejected']

    liste = client.get(UC, headers=h, params={'limit':200}).json()
    assert liste['total'] == 3, liste

    # --- 4) KOKEN KAYIT ALTINDA ------------------------------------------
    dosyadan = [r for r in liste['items'] if r['origin'] == 'IMPORT']
    assert len(dosyadan) == 3, liste
    isaretler = sorted(r['origin_reference'] for r in dosyadan)
    assert isaretler == ['bku-listesi.csv:2','bku-listesi.csv:3','bku-listesi.csv:7'], isaretler

    # Formdan giren satir MANUAL; ayni tabloda iki koken ayirt edilebiliyor.
    elle = client.post(UC, headers=h,
                       json={'product_id':ikinci,'crop':'Armut',
                             'preharvest_interval_days':30})
    assert elle.status_code == 201, elle.text
    assert elle.json()['origin'] == 'MANUAL', elle.json()
    assert elle.json()['origin_reference'] is None, elle.json()

    # --- 3) CAKISMA REDDEDILIR, GUNCELLENMEZ -----------------------------
    # Ayni urun+bitki icin FARKLI bir gun sayisi tasiyan ikinci dosya.
    # Asil iddia: ESKI DEGER YERINDE DURUYOR. Yalniz yanit govdesine bakan
    # bir test, sessizce ustune yazan bir surumu YAKALAYAMAZDI.
    onceki = [r for r in liste['items'] if r['crop'] == 'Domates'][0]
    assert onceki['preharvest_interval_days'] == 21, onceki
    catisan = (
        'Urun Kodu,Bitki,Hasat Bekleme (Gun)\n'
        'BKU-1,Domates,3\n'      # 2  RED: katalogda zaten var
        'BKU-2,Kiraz,12\n'       # 3  saglam
    )
    y2 = yukle(client, h, catisan, 'ikinci-liste.csv')
    assert y2.status_code == 200, y2.text
    b2 = y2.json()
    assert b2['imported'] == 1, b2
    assert set(redler(b2)) == {2}, b2['rejected']
    assert 'zaten var' in redler(b2)[2], b2['rejected']
    # Reddedilen satirin catistigi kaydin KIMLIGI soyleniyor: kullanici
    # hangisinin dogru oldugunu ekrandan bakip KENDISI karar versin diye.
    assert ('#%d' % onceki['id']) in redler(b2)[2], b2['rejected']
    # VE ESKI DEGER DEGISMEDI.
    hala = client.get(UC + '/%d' % onceki['id'], headers=h).json()
    assert hala['preharvest_interval_days'] == 21, hala
    assert hala['origin'] == 'IMPORT', hala
    assert hala['origin_reference'] == 'bku-listesi.csv:2', hala

    # --- 2b) DOSYA ICI TEKRAR: gerekce "dosyada zaten var" olmali --------
    # Veritabanina bakmak yetmez; ilki yazildiktan SONRA veritabaninda
    # gorunur ve gerekce "katalogda zaten var" olurdu — kullanici satiri
    # KENDISININ iki kez yazdigini anlamazdi.
    tekrar = (
        'Urun Kodu,Bitki,Hasat Bekleme (Gun)\n'
        'BKU-2,Seftali,9\n'      # 2  saglam
        'BKU-2,seftali,11\n'     # 3  RED: ayni satir dosyada 2de de var
    )
    y3 = yukle(client, h, tekrar, 'ucuncu-liste.csv')
    assert y3.status_code == 200, y3.text
    b3 = y3.json()
    assert b3['imported'] == 1, b3
    assert set(redler(b3)) == {3}, b3['rejected']
    assert 'dosyanin 2' in sadelestir(redler(b3)[3]), b3['rejected']

    # --- 5) DUZENLEME KOKENI DEGISTIRMEZ ---------------------------------
    duzelt = client.put(UC + '/%d' % onceki['id'], headers=h,
                        json={'product_id':ilac,'crop':'Domates',
                              'preharvest_interval_days':25,'status':'ACTIVE',
                              'expected_updated_at':hala['updated_at']})
    assert duzelt.status_code == 200, duzelt.text
    assert duzelt.json()['preharvest_interval_days'] == 25, duzelt.json()
    # Koken satirin NEREDEN GELDIGIDIR; bir insanin sonradan degeri
    # duzeltmesi onu "elle girilmis" YAPMAZ.
    assert duzelt.json()['origin'] == 'IMPORT', duzelt.json()
    assert duzelt.json()['origin_reference'] == 'bku-listesi.csv:2', duzelt.json()

    # --- ZINCIRIN UCU: ICE AKTARILAN SATIR GERCEK KILIT URETIYOR ---------
    # Ice aktarma bir "raporlama" ozelligi degil. Katalogda BKU-2/Elma icin
    # dosyadan gelen 7 gun var; operator hicbir sure GIRMIYOR.
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,
                              'crop':'Elma','started_on':'2026-03-01'})
    assert sezon.status_code == 201, sezon.text
    sezon = sezon.json()
    faaliyet = client.post('/api/field-activities', headers=h,
                           json={'season_id':sezon['id'],'activity_type':'SPRAYING',
                                 'performed_at':'2026-06-01T09:00:00+03:00',
                                 'applied_area_decare':'30.0000',
                                 'inputs':[{'product_id':ikinci,'input_name':'IKINCI BKU',
                                            'quantity':'10','unit':'LT','dose':'2',
                                            'dose_unit':'LT/DA'}]})
    assert faaliyet.status_code == 201, faaliyet.text
    g = faaliyet.json()
    assert g['preharvest_interval_days'] == 7, g
    assert g['preharvest_source'] == 'CATALOGUE', g
    # 1 Haziran + 7 gun -> guvenli tarih 8 Haziran. 5 Haziran ERKENDIR.
    erken = client.post('/api/field-harvests', headers=h,
                        json={'season_id':sezon['id'],'harvested_on':'2026-06-05',
                              'quantity':'5000','unit':'KG'})
    assert erken.status_code == 422, erken.text
    gec = client.post('/api/field-harvests', headers=h,
                      json={'season_id':sezon['id'],'harvested_on':'2026-06-09',
                            'quantity':'5000','unit':'KG'})
    assert gec.status_code == 201, gec.text

    # --- BASLIK EKSIKLIGI DOSYAYI REDDEDER -------------------------------
    # Eksik sutun HER satiri ayni sekilde cozumsuz birakir; reddedilen sey
    # gercekten dosyanin kendisidir ve ayni cumleyi 200 kez yazmanin anlami
    # olmazdi.
    eksik = yukle(client, h, 'Urun Kodu,Bitki\nBKU-1,Ayva\n', 'eksik.csv')
    assert eksik.status_code == 400, eksik.text
    assert 'Hasat Bekleme' in eksik.json()['detail'], eksik.text
    urunsuz = yukle(client, h, 'Bitki,Hasat Bekleme (Gun)\nAyva,5\n', 'urunsuz.csv')
    assert urunsuz.status_code == 400, urunsuz.text

    print('BKU ICE AKTARMA OK')
'''
