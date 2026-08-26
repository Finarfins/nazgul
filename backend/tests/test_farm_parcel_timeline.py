"""Tarla Yönetimi V1 — FAZ 7: parsel zaman çizelgesi ucu.

Konu: mobil-erp#2, "Ekranlar" başlığı — *parsel detay* ve *sezon zaman
çizelgesi*.

Uç TEK istekte parselin sezonlarını, faaliyetlerini ve hasatlarını veriyor.
Mevcut liste uçlarıyla da yapılabilirdi ama istemcinin her sezon için ayrı
faaliyet ve hasat isteği atması gerekirdi (N+1); sahadaki telefon bağlantısında
bu, ekranın açılışını sezon sayısı kadar yavaşlatır.

Testlerin ağırlığı üç iddiada:

1. **Maliyet ve hasat AYRI toplanıyor.** Tek sorguda JOIN'lemek kartezyen
   çarpım üretir: iki faaliyeti olan bir sezonda her hasat iki kez sayılırdı.
   Test tam da bu şekli kuruyor (2 faaliyet + 2 hasat) ve toplamları çiviliyor.
2. **Ekilen alan yoksa oran `null`.** Parselin alanına DÜŞMÜYORUZ — ölçülmemiş
   bir sezonu ölçülmüş gibi göstermek, dekar başına maliyeti uydurmaktır.
3. **Çapraz kiracı 404** ve başka parselin verisi sızmıyor.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_timeline_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_parcel_timeline_sqlite(tmp_path: Path) -> None:
    run_timeline_smoke(f"sqlite:///{(tmp_path / 'farm-timeline.db').as_posix()}")


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FarmTimeline!123'


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


with TestClient(app) as client:
    h = admin_headers(client)

    ciftlik = client.post('/api/farms', headers=h, json={'code':'z1','name':'Zaman Çiftlik'}).json()
    p1 = client.post('/api/farm-parcels', headers=h,
                     json={'farm_id':ciftlik['id'],'code':'zp1','name':'Birinci Parsel',
                           'area_decare':'100.0000'}).json()
    p2 = client.post('/api/farm-parcels', headers=h,
                     json={'farm_id':ciftlik['id'],'code':'zp2','name':'İkinci Parsel',
                           'area_decare':'50.0000'}).json()

    # p1'de ekilen alanı GİRİLMİŞ sezon.
    s1 = client.post('/api/crop-seasons', headers=h,
                     json={'parcel_id':p1['id'],'season_year':2026,'crop':'Buğday',
                           'started_on':'2026-03-01','planted_area_decare':'80.0000'}).json()
    # p1'de ekilen alanı GİRİLMEMİŞ sezon.
    s2 = client.post('/api/crop-seasons', headers=h,
                     json={'parcel_id':p1['id'],'season_year':2025,'crop':'Arpa',
                           'started_on':'2025-03-01'}).json()
    # p2'de ayrı sezon — sızmamalı.
    s3 = client.post('/api/crop-seasons', headers=h,
                     json={'parcel_id':p2['id'],'season_year':2026,'crop':'Mısır',
                           'started_on':'2026-03-01','planted_area_decare':'50.0000'}).json()

    # --- KARTEZYEN ÇARPIM TUZAĞINI KURAN ŞEKİL -----------------------------
    # s1: İKİ faaliyet (her biri girdili) + İKİ hasat.
    for tarih, tutar in (('2026-04-01T09:00:00+03:00', '1000.00'),
                         ('2026-05-01T09:00:00+03:00', '500.00')):
        f = client.post('/api/field-activities', headers=h,
                        json={'season_id':s1['id'],'activity_type':'FERTILIZING',
                              'performed_at':tarih,'applied_area_decare':'80.0000'})
        assert f.status_code == 201, f.text
        g = client.post(f"/api/field-activities/{f.json()['id']}/inputs", headers=h,
                        json={'input_name':'Gübre','quantity':'1','unit':'ADET','unit_cost':tutar})
        assert g.status_code == 201, g.text

    for tarih, miktar in (('2026-07-10', '12000'), ('2026-07-20', '8000')):
        hv = client.post('/api/field-harvests', headers=h,
                         json={'season_id':s1['id'],'harvested_on':tarih,
                               'quantity':miktar,'unit':'KG'})
        assert hv.status_code == 201, hv.text

    # s3 (başka parsel) da dolduruluyor; p1 çizelgesinde GÖRÜNMEMELİ.
    f3 = client.post('/api/field-activities', headers=h,
                     json={'season_id':s3['id'],'activity_type':'IRRIGATION',
                           'performed_at':'2026-06-01T09:00:00+03:00'}).json()
    client.post('/api/field-harvests', headers=h,
                json={'season_id':s3['id'],'harvested_on':'2026-08-01',
                      'quantity':'5000','unit':'KG'})

    # --- ÇİZELGE -----------------------------------------------------------
    r = client.get(f"/api/farm-parcels/{p1['id']}/timeline", headers=h)
    assert r.status_code == 200, r.text
    t = r.json()
    assert set(t) == {'parcel', 'farm', 'seasons', 'activities', 'harvests'}, t.keys()
    assert t['parcel']['id'] == p1['id']
    assert t['farm']['name'] == 'Zaman Çiftlik'

    # Yalnız bu parselin kayıtları.
    assert {a['season_id'] for a in t['activities']} == {s1['id']}, t['activities']
    assert {x['season_id'] for x in t['harvests']} == {s1['id']}, t['harvests']
    assert f3['id'] not in {a['id'] for a in t['activities']}
    assert len(t['activities']) == 2 and len(t['harvests']) == 2

    (sz1,) = [x for x in t['seasons'] if x['id'] == s1['id']]
    # 1000 + 500 = 1500 — İKİ hasat olmasına rağmen 3000 DEĞİL.
    assert Decimal(sz1['total_cost']) == Decimal('1500'), sz1
    # 12000 + 8000 = 20000 — İKİ faaliyet olmasına rağmen 40000 DEĞİL.
    assert Decimal(sz1['harvest_quantity']) == Decimal('20000'), sz1
    # 1500 / 80 = 18.75 ; 20000 / 80 = 250
    assert Decimal(sz1['cost_per_decare']) == Decimal('18.75'), sz1
    assert Decimal(sz1['yield_per_decare']) == Decimal('250'), sz1

    # --- EKİLEN ALAN YOKSA ORAN NULL ---------------------------------------
    (sz2,) = [x for x in t['seasons'] if x['id'] == s2['id']]
    assert sz2['planted_area_decare'] is None, sz2
    # Parselin 100 dekarına DÜŞMÜYORUZ: ölçülmemiş sezon ölçülmüş gösterilemez.
    assert sz2['cost_per_decare'] is None, sz2
    assert sz2['yield_per_decare'] is None, sz2

    # Sezonlar yeniden eskiye.
    assert [x['season_year'] for x in t['seasons']] == [2026, 2025], t['seasons']

    # --- ÇAPRAZ KİRACI 404 -------------------------------------------------
    b = client.post('/api/companies', headers=h, json={'name':'Zaman B'}).json()
    hb = dict(h, **{'X-Company-ID': str(b['id'])})
    assert client.get(f"/api/farm-parcels/{p1['id']}/timeline", headers=hb).status_code == 404

    # --- SEZONSUZ PARSEL: boş çizelge, hata değil --------------------------
    p3 = client.post('/api/farm-parcels', headers=h,
                     json={'farm_id':ciftlik['id'],'code':'zp3','name':'Boş Parsel',
                           'area_decare':'10'}).json()
    bos = client.get(f"/api/farm-parcels/{p3['id']}/timeline", headers=h)
    assert bos.status_code == 200, bos.text
    assert bos.json()['seasons'] == [] and bos.json()['activities'] == []

    print('TARLA FAZ-7 PARSEL CIZELGESI TAMAM')
'''
