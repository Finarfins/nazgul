"""Tarla Yönetimi V1 — FAZ 10: faaliyet + girdiler TEK istekte, TEK işlemde.

Konu: mobil-erp#2, "Ekranlar" — *yeni faaliyet hızlı girişi* ve
*ilaçlama/gübreleme özel formu*.

Bunun bir arayüz kolaylığından fazlası olduğu ölçüldü: ilaçlama girmek İKİ
AYRI istek gerektiriyordu (önce faaliyet, sonra girdi) ve ikisi AYRI İŞLEMDİ.
Arada kesilme olursa GİRDİSİZ BİR FAALİYET kalıyor, sezon maliyeti sessizce
eksik çıkıyordu. Sahada şebeke kesintisi istisna değil, kural.

Testlerin ağırlığı dört iddiada:

1. **Ya hepsi ya hiçbiri.** Girdilerden biri geçersizse FAALİYET DE
   oluşmamalı. Yarım kayıt, hiç kayıt olmamasından kötüdür: kullanıcı
   girdiğini sanır.
2. **Toplam maliyet yine sunucuda.** İç içe girdide de `total_cost` alanı yok.
3. **Tekrar koruması bozulmadı.** İç içe girdinin AYRI bir işlem kimliği YOK;
   tekrar gönderim ikinci bir faaliyet DE ikinci bir girdi DE oluşturmamalı.
4. **Firma kuralı iç içe girdide de geçerli.** Doz zorunluluğu ayrı uçta
   uygulanıp burada atlanırsa, kural fiilen kapanmış olurdu.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_nested_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_activity_with_inputs_sqlite(tmp_path: Path) -> None:
    run_nested_smoke(f"sqlite:///{(tmp_path / 'farm-nested.db').as_posix()}")


def test_nested_input_schema_has_no_operation_id_or_total_cost() -> None:
    """İç içe girdi kendi işlem kimliğini TAŞIMAZ ve toplamı GÖNDEREMEZ.

    Ayrı kimlik verseydik aynı işlem iki farklı kimlikle deftere yazılırdı ve
    tekrar koruması yarım çalışırdı.
    """
    sys.path.insert(0, str(BACKEND))
    from app.farm_schemas import ActivityInputNested

    alanlar = set(ActivityInputNested.model_fields)
    assert "operation_id" not in alanlar, alanlar
    assert "total_cost" not in alanlar, alanlar
    assert ActivityInputNested.model_config.get("extra") == "forbid"


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FarmNested!123'
ZAMAN = '2026-05-10T09:00:00+03:00'


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


def sayim(client, h, yol):
    return client.get(yol, headers=h).json()['total']


with TestClient(app) as client:
    h = admin_headers(client)
    ciftlik = client.post('/api/farms', headers=h, json={'code':'h1','name':'Hızlı Çiftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'hp','name':'Hızlı Parsel',
                               'area_decare':'40.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Pamuk',
                              'started_on':'2026-03-01','planted_area_decare':'40.0000'}).json()

    TABAN = {'season_id':sezon['id'],'activity_type':'SPRAYING','performed_at':ZAMAN,
             'applied_area_decare':'40.0000','preharvest_interval_days':21}

    # --- 1) TEK İSTEK: faaliyet + İKİ girdi --------------------------------
    r = client.post('/api/field-activities', headers=h, json={**TABAN, 'inputs':[
        {'input_name':'Herbisit','quantity':'10','unit':'LT','unit_cost':'250.00',
         'dose':'250','dose_unit':'ML/DEKAR'},
        {'input_name':'Yayıcı','quantity':'2','unit':'LT','unit_cost':'80.00',
         'dose':'50','dose_unit':'ML/DEKAR'},
    ]})
    assert r.status_code == 201, r.text
    faaliyet = r.json()
    assert len(faaliyet['inputs']) == 2, faaliyet
    # Toplamlar SUNUCUDA: 10*250 = 2500,00 ve 2*80 = 160,00
    toplamlar = sorted(Decimal(str(g['total_cost'])) for g in faaliyet['inputs'])
    assert toplamlar == [Decimal('160.00'), Decimal('2500.00')], faaliyet['inputs']

    # --- 2) YA HEPSİ YA HİÇBİRİ --------------------------------------------
    onceki_faaliyet = sayim(client, h, '/api/field-activities')
    # İkinci girdide doz YOK; ilaçlamada zorunlu → istek tümüyle reddedilmeli.
    kotu = client.post('/api/field-activities', headers=h, json={**TABAN, 'inputs':[
        {'input_name':'İyi girdi','quantity':'1','unit':'LT','dose':'10','dose_unit':'ML/DEKAR'},
        {'input_name':'Dozsuz girdi','quantity':'1','unit':'LT'},
    ]})
    assert kotu.status_code == 422, kotu.text
    # ASIL İDDİA: faaliyet DE oluşmamış olmalı.
    assert sayim(client, h, '/api/field-activities') == onceki_faaliyet, 'YARIM KAYIT KALDI'

    # --- 3) TOPLAM MALİYET GÖNDERİLEMEZ ------------------------------------
    zorla = client.post('/api/field-activities', headers=h, json={**TABAN, 'inputs':[
        {'input_name':'X','quantity':'1','unit':'LT','unit_cost':'100.00',
         'dose':'10','dose_unit':'ML/DEKAR','total_cost':'1.00'},
    ]})
    assert zorla.status_code == 422, zorla.text

    # --- 4) TEKRAR KORUMASI: iç içe girdiler de ikilenmemeli ---------------
    OP = 'ic-ice-islem-0001-abcd'
    govde = {**TABAN, 'operation_id':OP, 'inputs':[
        {'input_name':'Tek girdi','quantity':'5','unit':'LT','unit_cost':'40.00',
         'dose':'100','dose_unit':'ML/DEKAR'},
    ]}
    ilk = client.post('/api/field-activities', headers=h, json=govde)
    assert ilk.status_code == 201, ilk.text
    fid = ilk.json()['id']
    ikinci = client.post('/api/field-activities', headers=h, json=govde)
    assert ikinci.status_code == 201 and ikinci.json()['id'] == fid, (ilk.json(), ikinci.json())
    detay = client.get(f'/api/field-activities/{fid}', headers=h).json()
    assert len(detay['inputs']) == 1, ('MÜKERRER GİRDİ', detay['inputs'])

    # --- 5) GÜBRELEMEDE DOZ ZORUNLU DEĞİL ----------------------------------
    r = client.post('/api/field-activities', headers=h, json={
        'season_id':sezon['id'],'activity_type':'FERTILIZING','performed_at':ZAMAN,
        'inputs':[{'input_name':'Üre','quantity':'400','unit':'KG','unit_cost':'12.50'}]})
    assert r.status_code == 201, r.text
    assert Decimal(str(r.json()['inputs'][0]['total_cost'])) == Decimal('5000.00'), r.json()

    # --- 6) GİRDİSİZ FAALİYET HÂLÂ GEÇERLİ ---------------------------------
    # Sulama gibi girdisi olmayan işler var; `inputs` opsiyonel kalmalı.
    r = client.post('/api/field-activities', headers=h, json={
        'season_id':sezon['id'],'activity_type':'IRRIGATION','performed_at':ZAMAN})
    assert r.status_code == 201, r.text
    assert r.json()['inputs'] == [], r.json()

    print('TARLA FAZ-10 TEK ISTEK TAMAM')
'''
