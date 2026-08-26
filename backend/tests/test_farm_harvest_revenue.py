"""Tarla Yönetimi V1 — FAZ 8: hasat geliri ve brüt katkı.

Konu: mobil-erp#2 — hasat için "toplam maliyet ve gelir özeti". Maliyet tarafı
Faz 2'den beri vardı; GELİR TARAFI YOKTU, dolayısıyla "bu tarla para
kazandırdı mı" sorusu hiç cevaplanamıyordu.

Testlerin ağırlığı ÜÇ iddiada — üçü de sessizce yanlış olabilecek türden:

1. **Gelir girilmemişse `null`, SIFIR DEĞİL.** Sıfır göstermek, ürününü henüz
   satmamış bir sezonu "hiç kazanmadı" diye gösterir ve brüt katkıyı zarar
   gibi okutur. Boş "bilinmiyor" demektir.
2. **Satılan miktar hasadı aşamaz.** Hasadın tamamı satılmaz (tohumluk,
   yemlik, fire); aşan bir değer sessiz geçseydi dekar başına geliri şişirirdi.
3. **`gross_margin` KÂR DEĞİL.** Yalnız gelir − GİRDİ maliyeti; işçilik,
   makine ve yakıt bu hesaba girmiyor. Test alan adını ve değerini birlikte
   çiviliyor ki biri "kâr" diye yeniden adlandırmasın.

Ayrıca gelir toplamı AYRI alt sorguyla alınıyor: maliyetle tek sorguda
toplamak kartezyen çarpım üretirdi. Test iki hasat + iki faaliyet kurarak bunu
sınıyor.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_revenue_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_harvest_revenue_sqlite(tmp_path: Path) -> None:
    run_revenue_smoke(f"sqlite:///{(tmp_path / 'farm-revenue.db').as_posix()}")


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FarmRevenue!123'


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


def sezon_ozeti(client, h, sezon_id, parcel_id):
    t = client.get(f'/api/farm-parcels/{parcel_id}/timeline', headers=h).json()
    (sz,) = [x for x in t['seasons'] if x['id'] == sezon_id]
    return sz


with TestClient(app) as client:
    h = admin_headers(client)

    ciftlik = client.post('/api/farms', headers=h, json={'code':'g1','name':'Gelir Çiftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'gp','name':'Gelir Parsel',
                               'area_decare':'100.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Buğday',
                              'started_on':'2026-03-01','planted_area_decare':'100.0000'}).json()
    sg = client.get(f"/api/crop-seasons/{sezon['id']}", headers=h).json()
    client.put(f"/api/crop-seasons/{sezon['id']}", headers=h,
               json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Buğday',
                     'started_on':'2026-03-01','planted_area_decare':'100.0000',
                     'status':'ACTIVE','expected_updated_at':sg['updated_at']})

    # İKİ faaliyet, her biri girdili — kartezyen çarpım tuzağını kuruyor.
    for tarih, tutar in (('2026-04-01T09:00:00+03:00', '6000.00'),
                         ('2026-05-01T09:00:00+03:00', '4000.00')):
        f = client.post('/api/field-activities', headers=h,
                        json={'season_id':sezon['id'],'activity_type':'FERTILIZING',
                              'performed_at':tarih,'applied_area_decare':'100.0000'}).json()
        client.post(f"/api/field-activities/{f['id']}/inputs", headers=h,
                    json={'input_name':'Gübre','quantity':'1','unit':'ADET','unit_cost':tutar})

    # --- 1) GELİRSİZ HASAT: null, SIFIR DEĞİL ------------------------------
    hv1 = client.post('/api/field-harvests', headers=h,
                      json={'season_id':sezon['id'],'harvested_on':'2026-07-10',
                            'quantity':'30000','unit':'KG'})
    assert hv1.status_code == 201, hv1.text
    assert hv1.json()['revenue_amount'] is None, hv1.json()
    assert hv1.json()['sold_quantity'] is None, hv1.json()

    sz = sezon_ozeti(client, h, sezon['id'], parsel['id'])
    assert sz['revenue_amount'] is None, ('gelir SIFIR gösterilmemeli', sz)
    assert sz['gross_margin'] is None, ('brüt katkı ZARAR gibi gösterilmemeli', sz)
    assert Decimal(sz['total_cost']) == Decimal('10000'), sz

    # --- 2) SATILAN MİKTAR HASADI AŞAMAZ -----------------------------------
    asan = client.post('/api/field-harvests', headers=h,
                       json={'season_id':sezon['id'],'harvested_on':'2026-07-20',
                             'quantity':'10000','unit':'KG','sold_quantity':'12000',
                             'revenue_amount':'100000.00'})
    assert asan.status_code == 422, asan.text
    assert 'aşamaz' in asan.json()['detail'], asan.text

    # --- 3) GELİRLİ HASATLAR ------------------------------------------------
    hv2 = client.post('/api/field-harvests', headers=h,
                      json={'season_id':sezon['id'],'harvested_on':'2026-07-20',
                            'quantity':'10000','unit':'KG','sold_quantity':'10000',
                            'revenue_amount':'95000.00'})
    assert hv2.status_code == 201, hv2.text
    hv3 = client.post('/api/field-harvests', headers=h,
                      json={'season_id':sezon['id'],'harvested_on':'2026-08-01',
                            'quantity':'5000','unit':'KG','sold_quantity':'4000',
                            'revenue_amount':'38000.00'})
    assert hv3.status_code == 201, hv3.text

    sz = sezon_ozeti(client, h, sezon['id'], parsel['id'])
    # 95.000 + 38.000 = 133.000 — İKİ faaliyet olmasına rağmen 266.000 DEĞİL.
    assert Decimal(sz['revenue_amount']) == Decimal('133000.00'), sz
    # Hasat: 30.000 + 10.000 + 5.000 = 45.000
    assert Decimal(sz['harvest_quantity']) == Decimal('45000'), sz
    # BRÜT KATKI = 133.000 − 10.000 = 123.000. KÂR DEĞİL: işçilik/makine yok.
    assert Decimal(sz['gross_margin']) == Decimal('123000.00'), sz
    # Alan adı 'profit'/'kar' OLMAMALI — yanlış okunmasın diye çiviliyoruz.
    assert 'profit' not in sz and 'net_margin' not in sz, sz.keys()

    # --- 4) PANO AYNI SONUCU VERMELİ ---------------------------------------
    pano = client.get('/api/field-dashboard', headers=h).json()
    (ps,) = [x for x in pano['seasons'] if x['season_id'] == sezon['id']]
    assert Decimal(ps['revenue_amount']) == Decimal('133000.00'), ps
    assert Decimal(ps['gross_margin']) == Decimal('123000.00'), ps

    # --- 5) SIFIR GELİR ile BİLİNMEYEN GELİR AYRI ---------------------------
    # Açıkça 0 girilen bir hasat "bilinmiyor" değildir.
    s2 = client.post('/api/crop-seasons', headers=h,
                     json={'parcel_id':parsel['id'],'season_year':2025,'crop':'Arpa',
                           'started_on':'2025-03-01','planted_area_decare':'50.0000'}).json()
    client.post('/api/field-harvests', headers=h,
                json={'season_id':s2['id'],'harvested_on':'2025-07-10',
                      'quantity':'1000','unit':'KG','revenue_amount':'0.00'})
    sz2 = sezon_ozeti(client, h, s2['id'], parsel['id'])
    assert sz2['revenue_amount'] is not None, ('açık 0 "bilinmiyor" sayılmamalı', sz2)
    assert Decimal(sz2['revenue_amount']) == Decimal('0'), sz2
    assert Decimal(sz2['gross_margin']) == Decimal('0'), sz2

    print('TARLA FAZ-8 HASAT GELIRI TAMAM')
'''
