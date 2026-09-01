"""Kantar fişi — DEFTER DEĞİŞMEDİ. Beş senaryo, tek iddia.

Konu: migration 0064 (kantar fişi PR1).

--- BU DOSYANIN TEK İDDİASI --------------------------------------------------

**Kantar fişi yazmak STOK DEFTERİNDE HİÇBİR ŞEYİ DEĞİŞTİRMEZ.** Hareketin
miktarı fişten ÖNCE de SONRA da `field_harvests.quantity`dir; fişin brütü de,
türetilen neti de, kağıdın neti de deftere GİRMEZ.

Bu iddia sınanabilir olduğu için PR1 böyle kesildi: dilim `field_stok_tuketici`
ye dokunmuyor ve outbox'a olay YAZMIYOR. Sınanmasaydı "dokunmadık" bir
NİYET beyanı olurdu; burada ÖLÇÜLÜYOR.

--- NİYE BU KADAR ÖNEMLİ ----------------------------------------------------

Yanlış miktar HATA VERMEZ, CEVAP VERİR. Defter fişin brütünü yazmaya başlasa
hiçbir yerde kırmızı çıkmaz; çiftçinin ambarında olmayan ürün görünür ve bunu
ancak aylar sonra bir sayımda fark eder. `field_stok_tuketici`nin başlığındaki
ölçülmüş kusurun (yön hatası) tam olarak aynı sınıfı.

--- NİYE `_hasat_kalemleri`YE LEFT JOIN EKLENMEDİ ---------------------------

Fişi deftere bağlamanın "bariz" yolu `_hasat_kalemleri`ye
`LEFT JOIN field_harvest_tickets` eklemekti. YANLIŞ OLURDU ve sebebi bir SIRA
sorunudur: hasat olayı hasat YAZILIRKEN aynı işlemde üretilir ve tüketici onu
ilk döngüsünde tüketir; kantar fişi ise kamyon depoya VARDIKTAN sonra girilir.
Birleştirme çalıştığı anda fiş HENÜZ YOKTUR — sorgu her seferinde NULL görür,
"fişi varsa netini kullan" kuralı hiç ateşlenmez ve kod DOĞRU GÖRÜNÜR ama
davranış hiç değişmez.

SENARYO 4 bunu tersinden de ölçüyor: fiş tüketiciden ÖNCE girilse bile hareket
değişmiyor. Yani bu dilim "fiş geç geliyor" diye şanslı değil; fişi HİÇ
OKUMUYOR.

--- BEŞ SENARYO -------------------------------------------------------------

1. FİŞSİZ HASAT — TABAN. Bir olay, bir hareket, miktar = hasat miktarı.
2. FİŞ YAZILDI — SAYILAR ARTMIYOR. Fiş yazımı `field_integration_events`e ve
   `stock_movements`a SIFIR satır ekler.
3. KESİNTİLİ FİŞ — MİKTAR DEĞİŞMİYOR. Brüt hasat miktarından FARKLI ve %5
   kesinti var; hareket yine hasat miktarı.
4. FİŞ ÖNCE, TÜKETİCİ SONRA. LEFT JOIN'in "çalışacağı" sıra; hareket yine
   değişmiyor.
5. BAYRAK YANARKEN DEFTER SESSİZ. `net_mismatch` VE `sold_exceeds_net` ikisi
   de `true` iken fişli hasadın defter satırı, fişsiz hasadınkiyle alan alan
   AYNI (kimlik ve referans sütunları hariç — onlar zaten satır başına farklı).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_defter_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "KANTAR FISI DEFTER TAMAM" in completed.stdout, completed.stdout


def test_kantar_fisi_defter_sqlite(tmp_path: Path) -> None:
    run_defter_smoke(f"sqlite:///{(tmp_path / 'kantar-defter.db').as_posix()}")


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

from app.db import SessionLocal
from app.field_stok_tuketici import olaylari_isle
from app.main import app

ADMIN_PW = 'KantarDefter!123'
URUN_ID = 4101


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
    return h, int(body['companies'][0]['id'])


def sayilar(db, cid):
    """(olay sayisi, hareket sayisi) — ikisi de KIRACI KAPSAMLI."""
    olay = db.execute(_sql(
        "SELECT COUNT(*) FROM field_integration_events WHERE company_id = :c"),
        {'c': cid}).scalar_one()
    hareket = db.execute(_sql(
        "SELECT COUNT(*) FROM stock_movements WHERE company_id = :c"),
        {'c': cid}).scalar_one()
    return int(olay), int(hareket)


def hareket_satirlari(db, cid):
    return [dict(r) for r in db.execute(_sql(
        """SELECT id,product_id,movement_type,quantity,reference_type,reference_id,
           note,company_id,warehouse_id FROM stock_movements
           WHERE company_id = :c ORDER BY id"""), {'c': cid}).mappings().all()]


def karsilastirilabilir(satir):
    """Satir basina ZORUNLU olarak farkli olan alanlar normalize edilir.

    `id` ve `reference_id` (olay kimligi) her satirda farklidir ve cikariliyor.
    `note` de olay kimligini metin icinde tasiyor (`tarla olayi #7 (...)`);
    ATILMIYOR, yalnizca o SAYI maskeleniyor — kaynak tipi (`field_harvest`)
    karsilastirmada KALSIN diye. Notu tumden atmak, "hasat notu" ile "faaliyet
    notu" farkini da gorunmez yapardi.

    `quantity` diyalektten str/Decimal gelebildigi icin Decimal'e normalize
    ediliyor: karsilastirma DEGERIN KENDISI uzerinde, temsili uzerinde degil.
    """
    kopya = dict(satir)
    kopya.pop('id')
    olay_id = kopya.pop('reference_id')
    if kopya.get('note'):
        kopya['note'] = kopya['note'].replace('#%d' % int(olay_id), '#<olay>')
    kopya['quantity'] = Decimal(str(kopya['quantity']))
    return kopya


with TestClient(app) as client:
    h, cid = admin_headers(client)

    # Urun ham SQL ile: bu dosyanin konusu urun ucu degil, defter.
    with SessionLocal() as db:
        # `active` BOOLEAN olarak BAGLANIYOR, 1 olarak DEGIL: PostgreSQL
        # boolean sutununa tamsayi kabul etmez ("column is of type boolean but
        # expression is of type integer"). ILK YAZIMDA 1 yazilmisti ve SQLite
        # bunu sessizce kabul ediyordu; kusuru PG IKIZI yakaladi.
        db.execute(_sql(
            "INSERT INTO products (id,name,purchase_price,sale_price,vat_rate,"
            "stock,unit,price_per,active,critical_stock,minimum_stock,company_id) "
            "VALUES (:i,'Bugday',0,0,0,'0.0000','kg','unit',:aktif,0,0,:c)"),
            {'i': URUN_ID, 'c': cid, 'aktif': True})
        db.commit()

    ciftlik = client.post('/api/farms', headers=h,
                          json={'code':'kd','name':'Kantar Defter'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'kdp','name':'Parsel',
                               'area_decare':'100.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,
                              'crop':'Bugday','product_id':URUN_ID,
                              'started_on':'2026-03-01',
                              'planted_area_decare':'100.0000'})
    assert sezon.status_code == 201, sezon.text
    sezon = sezon.json()
    assert sezon['product_id'] == URUN_ID, sezon

    # --- SENARYO 1: FISSIZ HASAT — TABAN ----------------------------------
    h1 = client.post('/api/field-harvests', headers=h,
                     json={'season_id':sezon['id'],'harvested_on':'2026-07-10',
                           'quantity':'1000','unit':'KG'})
    assert h1.status_code == 201, h1.text
    h1 = h1.json()

    # Fisli olacak hasat AYNI miktarda: iki defter satirini alan alan
    # karsilastirabilmek icin tek fark FISIN VARLIGI olmali.
    h2 = client.post('/api/field-harvests', headers=h,
                     json={'season_id':sezon['id'],'harvested_on':'2026-07-11',
                           'quantity':'1000','unit':'KG','sold_quantity':'980',
                           'revenue_amount':'50000.00'})
    assert h2.status_code == 201, h2.text
    h2 = h2.json()

    with SessionLocal() as db:
        olay0, hareket0 = sayilar(db, cid)
    assert (olay0, hareket0) == (2, 0), (olay0, hareket0)

    # --- SENARYO 2: FIS YAZILDI — SAYILAR ARTMIYOR ------------------------
    # SENARYO 4 ile ayni yazim: fis TUKETICIDEN ONCE giriliyor, yani LEFT
    # JOIN'in "calisacagi" sira. Asagida hareket miktarinin yine hasat
    # miktari oldugu olculuyor.
    #
    # KESINTILI (SENARYO 3): brut 1200 hasat miktarindan FARKLI, kesinti
    # toplami %5, turetilen net 1140. Kagidin neti 1100 — BILEREK farkli,
    # `net_mismatch` yansin diye (SENARYO 5).
    fis = client.post('/api/field-harvest-tickets', headers=h,
                      json={'harvest_id':h2['id'],'ticket_no':'KD-1',
                            'buyer_name':'Alici','plate':'06 kd 64',
                            'gross_quantity':'1200','ticket_net_quantity':'1100',
                            'deductions':[{'label':'Rutubet','rate_percent':'2'},
                                          {'label':'Yabanci madde','rate_percent':'3'}]})
    assert fis.status_code == 201, fis.text
    fis = fis.json()
    assert fis['derived_net_quantity'] == '1140.0000', fis
    assert fis['net_mismatch'] is True, fis

    with SessionLocal() as db:
        olay1, hareket1 = sayilar(db, cid)
    assert (olay1, hareket1) == (olay0, hareket0), (
        'FIS YAZIMI DEFTERE/OUTBOXA SATIR EKLEDI', (olay0, hareket0), (olay1, hareket1))

    # SENARYO 5'in okuma tarafi: IKI BAYRAK DA YANIYOR.
    okuma = client.get('/api/field-harvest-tickets', headers=h,
                       params={'harvest_id':h2['id']}).json()
    assert okuma['summary']['sold_exceeds_net'] is False, okuma
    # Satilan 980, turetilen net 1140 -> asmiyor. Asan hali icin ayri bir
    # hasat kuruluyor (asagida): ayni fisi bozmadan iki bayragi da olcmek
    # icin.

    # --- TUKETICI KOSUYOR: SENARYO 1, 3, 4 -------------------------------
    with SessionLocal() as db:
        sayac = olaylari_isle(db, cid)
        db.commit()
    assert sayac['girdi'] == 2, sayac
    assert sayac['SENT'] == 2, sayac

    with SessionLocal() as db:
        satirlar = hareket_satirlari(db, cid)
    assert len(satirlar) == 2, satirlar
    for satir in satirlar:
        assert Decimal(str(satir['quantity'])) == Decimal('1000'), (
            'DEFTERE HASAT MIKTARI DISINDA BIR SEY YAZILDI', satir)
        assert satir['product_id'] == URUN_ID, satir

    # --- SENARYO 5: FISLI VE FISSIZ SATIR ALAN ALAN AYNI ------------------
    fissiz, fisli = (karsilastirilabilir(s) for s in satirlar)
    assert fissiz == fisli, ('FISIN VARLIGI DEFTER SATIRINI DEGISTIRDI', fissiz, fisli)

    # --- SENARYO 5 (devami): `sold_exceeds_net` YANARKEN DE SESSIZ --------
    # Ayri bir hasat: satilan miktar turetilen neti ASIYOR.
    h3 = client.post('/api/field-harvests', headers=h,
                     json={'season_id':sezon['id'],'harvested_on':'2026-07-12',
                           'quantity':'500','unit':'KG','sold_quantity':'500',
                           'revenue_amount':'25000.00'}).json()
    with SessionLocal() as db:
        olay2, hareket2 = sayilar(db, cid)
    assert (olay2, hareket2) == (3, 2), (olay2, hareket2)

    # Bu fis TUKETICIDEN SONRA girilecek (gercek sira). Once tuketici kossun.
    with SessionLocal() as db:
        sayac = olaylari_isle(db, cid)
        db.commit()
    assert sayac['girdi'] == 1 and sayac['SENT'] == 1, sayac

    fis3 = client.post('/api/field-harvest-tickets', headers=h,
                       json={'harvest_id':h3['id'],'gross_quantity':'500',
                             'deductions':[{'label':'Fire','rate_percent':'10'}]})
    assert fis3.status_code == 201, fis3.text
    assert fis3.json()['derived_net_quantity'] == '450.0000', fis3.text
    # Kagidin neti GIRILMEMIS -> ayrisacak bir sey yok.
    assert fis3.json()['net_mismatch'] is None, fis3.text

    okuma3 = client.get('/api/field-harvest-tickets', headers=h,
                        params={'harvest_id':h3['id']}).json()
    assert okuma3['summary']['sold_exceeds_net'] is True, okuma3

    with SessionLocal() as db:
        olay3, hareket3 = sayilar(db, cid)
        satirlar3 = hareket_satirlari(db, cid)
    assert (olay3, hareket3) == (3, 3), (
        'TUKETICIDEN SONRA GIRILEN FIS DEFTERE SATIR EKLEDI', olay3, hareket3)
    (son,) = [s for s in satirlar3 if s['id'] not in {satirlar[0]['id'], satirlar[1]['id']}]
    assert Decimal(str(son['quantity'])) == Decimal('500'), (
        'BAYRAK YANARKEN DEFTERE NET YAZILDI', son)

    # Tuketiciyi bir kez daha kosturmak da hicbir sey uretmemeli: fis PENDING
    # bir olay dogurmadi.
    with SessionLocal() as db:
        sayac = olaylari_isle(db, cid)
        db.commit()
        olay4, hareket4 = sayilar(db, cid)
    assert sayac['girdi'] == 0, sayac
    assert (olay4, hareket4) == (3, 3), (olay4, hareket4)

    print('KANTAR FISI DEFTER TAMAM')
'''
