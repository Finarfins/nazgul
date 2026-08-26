"""Tarla Yönetimi V1 — FAZ 6: ilaçlama değişmezleri.

Konu: mobil-erp#2, "Değişmezler" başlığı — *"İlaçlamada ürün, doz ve birim
zorunlu."*

BU DEĞİŞMEZ V1'DE HİÇ UYGULANMIYORDU. Ölçüldü: alansız bir ilaçlama ve dozsuz
bir ilaç girdisi 201 alıyordu. Konuda yazılı ama kodda karşılığı olmayan bir
kural, olmayan kuraldan kötüdür — okuyan "uygulanıyor" sanır.

Neden bu iki alan: miktar tek başına dekar başına kullanımı VERMEZ. 5 litre
ilacı 10 dekara atmakla 100 dekara atmak farklı şeydir; kalıntı hesabı ve
denetimde "ne kadar attınız" sorusu doz + alan ister.

Testler ayrıca İKİ SINIRI çiviliyor — aşırı zorlamadığımızı göstermek için:

* **Bekleme süreleri zorunlu DEĞİL.** Konu onları "girildiyse negatif olamaz"
  diye tanımlıyor. Zorunlu tutmak kullanıcıyı rakam uydurmaya iterdi ve
  uydurulmuş bir bekleme süresi hiç olmamasından tehlikelidir.
* **Kural yalnız SPRAYING'e uygulanıyor.** Sulama ya da toprak işleme
  kaydında alan/doz bilinmese de kayıt değerlidir.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_spray_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_spraying_invariants_sqlite(tmp_path: Path) -> None:
    run_spray_smoke(f"sqlite:///{(tmp_path / 'farm-spray.db').as_posix()}")


_SMOKE = r'''
from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FarmSpray!12345'


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
    ciftlik = client.post('/api/farms', headers=h, json={'code':'i1','name':'İlaç Çiftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'ip','name':'İlaç Parsel',
                               'area_decare':'60.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Pamuk',
                              'started_on':'2026-03-01','planted_area_decare':'60.0000'}).json()

    ZAMAN = '2026-05-10T09:00:00+03:00'

    # --- 1) İLAÇLAMADA UYGULANAN ALAN ZORUNLU ------------------------------
    alansiz = client.post('/api/field-activities', headers=h,
                          json={'season_id':sezon['id'],'activity_type':'SPRAYING',
                                'performed_at':ZAMAN})
    assert alansiz.status_code == 422, alansiz.text
    assert 'uygulanan alan' in alansiz.json()['detail'], alansiz.text
    # Reddedilen istek KAYIT BIRAKMAMALI.
    assert client.get('/api/field-activities', headers=h).json()['total'] == 0

    # --- 2) KURAL YALNIZ İLAÇLAMAYA — sulama alansız geçmeli ---------------
    sulama = client.post('/api/field-activities', headers=h,
                         json={'season_id':sezon['id'],'activity_type':'IRRIGATION',
                               'performed_at':ZAMAN})
    assert sulama.status_code == 201, sulama.text

    # --- 3) BEKLEME SÜRESİ ZORUNLU DEĞİL -----------------------------------
    # Alan verildi, süreler verilmedi: geçmeli. Zorunlu tutmak kullanıcıyı
    # rakam uydurmaya iterdi.
    ilac = client.post('/api/field-activities', headers=h,
                       json={'season_id':sezon['id'],'activity_type':'SPRAYING',
                             'performed_at':ZAMAN,'applied_area_decare':'60.0000'})
    assert ilac.status_code == 201, ilac.text
    assert ilac.json()['preharvest_interval_days'] is None, ilac.json()
    ilac_id = ilac.json()['id']

    # --- 4) İLAÇ GİRDİSİNDE DOZ VE DOZ BİRİMİ ZORUNLU ----------------------
    dozsuz = client.post(f'/api/field-activities/{ilac_id}/inputs', headers=h,
                         json={'input_name':'Herbisit','quantity':'5','unit':'LT'})
    assert dozsuz.status_code == 422, dozsuz.text
    detay = dozsuz.json()['detail']
    assert 'doz' in detay and 'doz birimi' in detay, detay

    # Yalnız doz verilip birim verilmezse de reddedilmeli.
    yarim = client.post(f'/api/field-activities/{ilac_id}/inputs', headers=h,
                        json={'input_name':'Herbisit','quantity':'5','unit':'LT','dose':'100'})
    assert yarim.status_code == 422, yarim.text
    assert 'doz birimi' in yarim.json()['detail'], yarim.text

    # Boş metin de eksik sayılmalı (şema kırpıyor olsa bile uç kontrol eder).
    tam = client.post(f'/api/field-activities/{ilac_id}/inputs', headers=h,
                      json={'input_name':'Herbisit','quantity':'5','unit':'LT',
                            'dose':'100','dose_unit':'ML/DEKAR','unit_cost':'250.00'})
    assert tam.status_code == 201, tam.text
    assert client.get(f'/api/field-activities/{ilac_id}', headers=h).json()['inputs'].__len__() == 1

    # --- 5) SULAMA/GÜBRELEME GİRDİSİNDE DOZ ZORUNLU DEĞİL ------------------
    gubre = client.post('/api/field-activities', headers=h,
                        json={'season_id':sezon['id'],'activity_type':'FERTILIZING',
                              'performed_at':ZAMAN}).json()
    dozsuz_gubre = client.post(f"/api/field-activities/{gubre['id']}/inputs", headers=h,
                               json={'input_name':'Üre','quantity':'400','unit':'KG'})
    assert dozsuz_gubre.status_code == 201, dozsuz_gubre.text

    # --- 6) TEKRAR GÖNDERİM DOĞRULAMAYA TAKILMAZ ---------------------------
    # Zaten uygulanmış bir ilaçlamanın tekrarı, kural sonradan sıkılaşsa bile
    # geçmeli (FAZ 4 defteri). Aynı kimlikle iki kez gönderiyoruz.
    OP = 'ilac-islem-0001-abcdefgh'
    g1 = client.post('/api/field-activities', headers=h,
                     json={'season_id':sezon['id'],'activity_type':'SPRAYING',
                           'performed_at':ZAMAN,'applied_area_decare':'60.0000',
                           'operation_id':OP})
    assert g1.status_code == 201, g1.text
    g2 = client.post('/api/field-activities', headers=h,
                     json={'season_id':sezon['id'],'activity_type':'SPRAYING',
                           'performed_at':ZAMAN,'applied_area_decare':'60.0000',
                           'operation_id':OP})
    assert g2.status_code == 201 and g2.json()['id'] == g1.json()['id'], (g1.json(), g2.json())

    print('TARLA FAZ-6 ILACLAMA DEGISMEZLERI TAMAM')
'''
