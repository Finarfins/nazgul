"""Tarla Yönetimi V1 — FAZ 4: çevrimdışı kuyruğun tekrar koruması.

Konu: mobil-erp#2.

Tek bir soru sınanıyor ve cevabı kritik: **aynı işlem ikinci kez gelirse
ikinci bir kayıt oluşur mu?**

Oluşsaydı sonuç sessiz ve pahalı olurdu: mükerrer faaliyet, mükerrer girdi
maliyeti, mükerrer hasat. Hiçbiri hata vermez, yalnızca sezon maliyeti ve
dekar başına verim yanlış çıkar — ve bunu fark etmenin yolu yoktur.

Ayrıca üç şey daha çiviliyor:
* Tekrar gönderim AYNI satırı döndürmeli (istemci hangi kaydı oluşturduğunu
  bilmeli), yeni bir 201 değil.
* Kimlik FİRMA İÇİNDE benzersiz: iki firma aynı kimliği üretirse ikisi de
  kendi kaydını oluşturabilmeli.
* Kimlik GÖNDERİLMEZSE koruma yok — panelden yazan kullanıcı için doğru olan
  bu, ama davranışın bilinçli olduğunu yazıyoruz.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_offline_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_farm_offline_idempotency_sqlite(tmp_path: Path) -> None:
    run_offline_smoke(f"sqlite:///{(tmp_path / 'farm-offline.db').as_posix()}")


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FarmOffline!12345'
OP = 'kuyruk-islem-0001-abcdefgh'


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


def sayim(client, h, yol, **params):
    r = client.get(yol, headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()['total']


with TestClient(app) as client:
    h = admin_headers(client)

    ciftlik = client.post('/api/farms', headers=h, json={'code':'k1','name':'Kuyruk Çiftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'kp','name':'Kuyruk Parsel',
                               'area_decare':'40.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Arpa',
                              'started_on':'2026-03-01','planted_area_decare':'40.0000'}).json()

    # --- 1) FAALİYET: aynı kimlik iki kez -----------------------------------
    govde = {'season_id':sezon['id'],'activity_type':'FERTILIZING',
             'performed_at':'2026-04-01T09:00:00+00:00','applied_area_decare':'40.0000',
             'operation_id':OP}
    ilk = client.post('/api/field-activities', headers=h, json=govde)
    assert ilk.status_code == 201, ilk.text
    ikinci = client.post('/api/field-activities', headers=h, json=govde)
    assert ikinci.status_code == 201, ikinci.text
    # AYNI satır dönmeli — istemci hangi kaydı oluşturduğunu bilmeli.
    assert ikinci.json()['id'] == ilk.json()['id'], (ilk.json(), ikinci.json())
    assert sayim(client, h, '/api/field-activities') == 1, 'MÜKERRER FAALİYET OLUŞTU'
    faaliyet_id = ilk.json()['id']

    # --- 2) GİRDİ: mükerrer maliyet en pahalı hata --------------------------
    g = {'input_name':'Üre','quantity':'100','unit':'KG','unit_cost':'10.00',
         'operation_id':'kuyruk-girdi-0002-abcdefgh'}
    g1 = client.post(f'/api/field-activities/{faaliyet_id}/inputs', headers=h, json=g)
    assert g1.status_code == 201, g1.text
    g2 = client.post(f'/api/field-activities/{faaliyet_id}/inputs', headers=h, json=g)
    assert g2.status_code == 201, g2.text
    assert g2.json()['id'] == g1.json()['id']
    detay = client.get(f'/api/field-activities/{faaliyet_id}', headers=h).json()
    assert len(detay['inputs']) == 1, ('MÜKERRER GİRDİ', detay['inputs'])
    # Toplam maliyet 1000,00 kalmalı — 2000,00 olsaydı sezon maliyeti ikiye katlanırdı.
    assert Decimal(str(detay['inputs'][0]['total_cost'])) == Decimal('1000.00'), detay

    # --- 3) HASAT -----------------------------------------------------------
    hv = {'season_id':sezon['id'],'harvested_on':'2026-07-20','quantity':'12000','unit':'KG',
          'operation_id':'kuyruk-hasat-0003-abcdefgh'}
    h1 = client.post('/api/field-harvests', headers=h, json=hv)
    assert h1.status_code == 201, h1.text
    h2 = client.post('/api/field-harvests', headers=h, json=hv)
    assert h2.json()['id'] == h1.json()['id']
    assert sayim(client, h, '/api/field-harvests') == 1, 'MÜKERRER HASAT OLUŞTU'

    # --- 4) TEKRAR GÖNDERİM DOĞRULAMALARA GİRMEZ ----------------------------
    #
    # Bu testin İLK hâli sezonu CLOSED yapıyordu ve HİÇBİR ŞEY KANITLAMIYORDU:
    # faaliyet oluşturma sezon durumuna bakmıyor, yani taze bir istek de kabul
    # edilirdi. Ölçüldü ve düzeltildi.
    #
    # Şimdi gerçekten reddedilecek bir durum kuruluyor: PARSEL KÜÇÜLTÜLÜYOR.
    # Faaliyet 40 dekara uygulanmıştı; parsel 10 dekara inince aynı gövde artık
    # "alan aşımı, gerekçe girin" ile 422 alır. ZATEN UYGULANMIŞ bir işlemin
    # tekrarı yine de geçmeli — geçmezse istemcinin kuyruğunda tamamlanmış bir
    # iş sonsuza kadar hata olarak takılı kalır ve teknisyen onu elle silmek
    # zorunda bırakılır.
    pg = client.get(f"/api/farm-parcels/{parsel['id']}", headers=h).json()
    kucult = client.put(f"/api/farm-parcels/{parsel['id']}", headers=h,
                        json={'farm_id':ciftlik['id'],'code':'kp','name':'Kuyruk Parsel',
                              'area_decare':'10.0000','status':'ACTIVE',
                              'expected_updated_at':pg['updated_at']})
    assert kucult.status_code == 200, kucult.text

    # Önce KANITLA: aynı gövde YENİ bir kimlikle artık reddediliyor.
    red = client.post('/api/field-activities', headers=h,
                      json={**govde, 'operation_id':'yeni-kimlik-0009-abcdefgh'})
    assert red.status_code == 422, ('doğrulama artık reddetmeliydi', red.text)

    # Ama tekrar gönderim geçmeli ve AYNI kaydı döndürmeli.
    tekrar = client.post('/api/field-activities', headers=h, json=govde)
    assert tekrar.status_code == 201, tekrar.text
    assert tekrar.json()['id'] == faaliyet_id
    assert sayim(client, h, '/api/field-activities') == 1

    # --- 5) KİMLİK FİRMA İÇİNDE BENZERSİZ -----------------------------------
    # İki firma aynı kimliği üretebilir (kimlik istemcide üretiliyor); B
    # firmasının kaydı A'nınki yüzünden engellenmemeli.
    b = client.post('/api/companies', headers=h, json={'name':'Kuyruk B Firması'}).json()
    hb = dict(h, **{'X-Company-ID': str(b['id'])})
    bc = client.post('/api/farms', headers=hb, json={'code':'b1','name':'B Çiftlik'}).json()
    bp = client.post('/api/farm-parcels', headers=hb,
                     json={'farm_id':bc['id'],'code':'bp','name':'B Parsel','area_decare':'10'}).json()
    bs = client.post('/api/crop-seasons', headers=hb,
                     json={'parcel_id':bp['id'],'season_year':2026,'crop':'Mısır'}).json()
    bf = client.post('/api/field-activities', headers=hb,
                     json={'season_id':bs['id'],'activity_type':'IRRIGATION',
                           'performed_at':'2026-05-01T09:00:00+00:00','operation_id':OP})
    assert bf.status_code == 201, bf.text
    assert bf.json()['id'] != faaliyet_id, 'B firması A firmasının kaydını aldı!'
    assert sayim(client, hb, '/api/field-activities') == 1

    # --- 6) KİMLİKSİZ İSTEK KORUNMAZ (bilinçli) -----------------------------
    # Panelden yazan kullanıcının kuyruğu yok; iki kez "kaydet" demek iki kayıt
    # demektir ve bu doğrudur. Davranışın kazara olmadığını çiviliyoruz.
    kimliksiz = {'season_id':bs['id'],'activity_type':'TILLAGE',
                 'performed_at':'2026-05-02T09:00:00+00:00'}
    a1 = client.post('/api/field-activities', headers=hb, json=kimliksiz)
    a2 = client.post('/api/field-activities', headers=hb, json=kimliksiz)
    assert a1.json()['id'] != a2.json()['id']
    assert sayim(client, hb, '/api/field-activities') == 3

    # --- 7) BOZUK KİMLİK REDDEDİLİR ----------------------------------------
    kotu = client.post('/api/field-activities', headers=hb,
                       json={**kimliksiz, 'operation_id':'kısa'})
    assert kotu.status_code == 422, kotu.text
    kotu = client.post('/api/field-activities', headers=hb,
                       json={**kimliksiz, 'operation_id':'bosluk iceren kimlik 12345'})
    assert kotu.status_code == 422, kotu.text

    print('TARLA FAZ-4 TEKRAR KORUMASI TAMAM')
'''
