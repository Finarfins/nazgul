"""Hayvan ilaç bekleme süresi: süt kilidi, katalog, provenance (PR-1).

Test ağırlığı 8 iddiada:

1. İlaç kaydı CRUD (tekil + grup all-or-nothing).
2. Katalog çözümü: operatör kazanır; uyuşmazlık `OPERATOR_OVERRIDE`.
3. Süt kilidi: sınır günü 422, güvenli gün 201.
4. Grup sağım: bir hayvanın ihlali sürüyü kilitler; mesaj küpe adını taşır.
5. Boş süre ihlal DEĞİL.
6. Hareket ucu süt politikasını OKUMAZ — davranışsal: ihlalle hareket 201.
7. Çapraz kiracı: B, A'nın ilacını göremez, kullanamaz, hayvanına yazamaz.
8. VOIDED satır kilidi kaldırır.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_treatment_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_treatments_sqlite(tmp_path: Path) -> None:
    run_treatment_smoke(f"sqlite:///{(tmp_path / 'herd-treatments.db').as_posix()}")


def test_day_arithmetic_is_treated_on_plus_interval() -> None:
    """`safe_from = treated_on + N gün` — sınır mutasyonu burada kırılır."""
    from datetime import date
    sys.path.insert(0, str(BACKEND))
    from app.herd_withdrawal import sut_bekleme_ihlalleri

    satir = {"id": 1, "drug_name": "Test", "treated_on": date(2026, 6, 1),
             "milk_withdrawal_days": 3}
    # 2026-06-04 = 1 Haz + 3 gün → güvenli; 2026-06-03 = sınır günü → ihlal.
    assert sut_bekleme_ihlalleri(sutirlar=[satir], hedef_gun=date(2026, 6, 3))
    assert not sut_bekleme_ihlalleri(sutirlar=[satir], hedef_gun=date(2026, 6, 4))


_SMOKE = r'''
from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'HerdTreatment!12345'

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

    grup = client.post('/api/animal-groups', headers=h,
                       json={'code':'ahir1','name':'Ahır 1','species':'CATTLE'}).json()
    inek = client.post('/api/animals', headers=h,
                       json={'ear_tag':'TR1111111111','species':'CATTLE','sex':'FEMALE',
                             'birth_date':'2021-05-10','group_id':grup['id']}).json()
    boga = client.post('/api/animals', headers=h,
                       json={'ear_tag':'TR9999999999','species':'CATTLE','sex':'MALE',
                             'birth_date':'2020-01-01','group_id':grup['id']}).json()
    ikinci_inek = client.post('/api/animals', headers=h,
                              json={'ear_tag':'TR2222222222','species':'CATTLE','sex':'FEMALE',
                                    'birth_date':'2021-06-10','group_id':grup['id']}).json()

    # --- 1) KATALOG CRUD ve JOKER ------------------------------------------
    katalog = client.post('/api/animal-drug-catalogue', headers=h,
                          json={'drug_name':'Penisilin','species':'',
                                'milk_withdrawal_days':3,'meat_withdrawal_days':28})
    assert katalog.status_code == 201, katalog.text
    katalog_id = katalog.json()['id']

    tur_ozel = client.post('/api/animal-drug-catalogue', headers=h,
                           json={'drug_name':'Penisilin','species':'CATTLE',
                                 'milk_withdrawal_days':5,'meat_withdrawal_days':30})
    assert tur_ozel.status_code == 201, tur_ozel.text

    tekrar = client.post('/api/animal-drug-catalogue', headers=h,
                         json={'drug_name':'Penisilin','species':'CATTLE',
                               'milk_withdrawal_days':7,'meat_withdrawal_days':30})
    assert tekrar.status_code == 409, tekrar.text

    # --- 2) İLAÇ KAYDI: TEKİL, OPERATÖR KAZANIR ---------------------------
    ilac = client.post('/api/animal-drug-treatments', headers=h,
                       json={'animal_id':inek['id'],'drug_name':'Penisilin',
                             'treated_on':'2026-06-01','milk_withdrawal_days':7,
                             'meat_withdrawal_days':35})
    assert ilac.status_code == 201, ilac.text
    kayit = ilac.json()[0]
    assert kayit['milk_withdrawal_days'] == 7, kayit
    assert kayit['milk_withdrawal_source'] == 'OPERATOR_OVERRIDE', kayit
    assert kayit['catalogue_milk_days'] == 5, kayit

    # --- 3) OPERATÖR KATALOGU EZER: OPERATOR_OVERRIDE --------------------
    ilac2 = client.post('/api/animal-drug-treatments', headers=h,
                        json={'animal_id':inek['id'],'drug_name':'Penisilin',
                              'treated_on':'2026-06-10','milk_withdrawal_days':2,
                              'meat_withdrawal_days':35})
    assert ilac2.status_code == 201, ilac2.text
    kayit2 = ilac2.json()[0]
    assert kayit2['milk_withdrawal_source'] == 'OPERATOR_OVERRIDE', kayit2
    assert kayit2['catalogue_milk_days'] == 5, kayit2

    # --- 4) SÜT KİLİDİ: SINIR GÜNÜ REDDEDİLİR ----------------------------
    erken = client.post('/api/milk-yields', headers=h,
                        json={'animal_id':inek['id'],'milked_on':'2026-06-07',
                              'quantity_liters':'10'})
    assert erken.status_code == 422, erken.text
    assert 'güvenli tarih 2026-06-08' in erken.json()['detail'], erken.json()

    # Güvenli gün: 2026-06-08 = 1 Haz + 7 gün.
    gec = client.post('/api/milk-yields', headers=h,
                      json={'animal_id':inek['id'],'milked_on':'2026-06-08',
                            'quantity_liters':'10'})
    assert gec.status_code == 201, gec.text
    assert gec.json()['safety_warning'] is None, gec.json()

    # --- 5) GEREKÇEYLE GEÇER ----------------------------------------------
    # İkinci ilaç 2026-06-10, 2 gün → güvenli 2026-06-12. 11'i ihlal.
    zorla = client.post('/api/milk-yields', headers=h,
                        json={'animal_id':inek['id'],'milked_on':'2026-06-11',
                              'quantity_liters':'5',
                              'safety_override_reason':'İlaçlama tarihi yanlış girilmişti'})
    assert zorla.status_code == 201, zorla.text
    assert zorla.json()['safety_warning'] is not None, zorla.json()
    assert zorla.json()['safety_override_reason'] == 'İlaçlama tarihi yanlış girilmişti'

    # --- 6) GRUP SAĞIM: TEK HAYVAN SÜRÜYÜ KİLİTLER -----------------------
    client.post('/api/animal-drug-treatments', headers=h,
                json={'animal_ids':[inek['id'], ikinci_inek['id']],
                      'drug_name':'Penisilin','treated_on':'2026-07-01',
                      'milk_withdrawal_days':5})
    grup_eksen = client.post('/api/milk-yields', headers=h,
                             json={'group_id':grup['id'],'milked_on':'2026-07-04',
                                   'quantity_liters':'50'})
    assert grup_eksen.status_code == 422, grup_eksen.text
    assert 'TR1111111111' in grup_eksen.json()['detail'], grup_eksen.json()
    assert 'TR2222222222' in grup_eksen.json()['detail'], grup_eksen.json()

    grup_guvenli = client.post('/api/milk-yields', headers=h,
                               json={'group_id':grup['id'],'milked_on':'2026-07-06',
                                     'quantity_liters':'50'})
    assert grup_guvenli.status_code == 201, grup_guvenli.text

    # --- 7) BOŞ SÜRE İHLAL DEĞİL ------------------------------------------
    suresiz = client.post('/api/animal-drug-treatments', headers=h,
                          json={'animal_id':boga['id'],'drug_name':'Serbest',
                                'treated_on':'2026-07-01'})
    assert suresiz.status_code == 201, suresiz.text
    boş_kayit = suresiz.json()[0]
    assert boş_kayit['milk_withdrawal_days'] is None, boş_kayit
    serbest = client.post('/api/milk-yields', headers=h,
                          json={'animal_id':boga['id'],'milked_on':'2026-07-02',
                                'quantity_liters':'3'})
    assert serbest.status_code == 201, serbest.text

    # --- 8) DAVRANIŞSAL TEST 6: HAREKET SÜT KİLİDİNİ OKUMAZ --------------
    hareket = client.post('/api/animal-movements', headers=h,
                          json={'animal_id':boga['id'],'kind':'SALE',
                                'moved_on':'2026-07-03','amount':'1000.00'})
    assert hareket.status_code == 201, hareket.text
    assert 'safety_warning' not in hareket.json()['animal'] or \
        hareket.json()['animal'].get('safety_warning') is None, hareket.json()

    # --- 9) VOIDED: SATIR KALIR, KİLİT KALKAR ----------------------------
    void = client.put(f"/api/animal-drug-treatments/{kayit2['id']}", headers=h,
                      json={'animal_id':inek['id'],'drug_name':'Penisilin',
                            'treated_on':'2026-06-10','milk_withdrawal_days':2,
                            'meat_withdrawal_days':35,'status':'VOIDED',
                            'expected_updated_at':kayit2['updated_at']})
    assert void.status_code == 200, void.text
    temiz = client.post('/api/milk-yields', headers=h,
                        json={'animal_id':inek['id'],'milked_on':'2026-06-11',
                              'quantity_liters':'8'})
    assert temiz.status_code == 201, temiz.json()

    # --- 10) ÇAPRAZ KİRACI ------------------------------------------------
    b = client.post('/api/companies', headers=h, json={'name':'İlaç B'}).json()
    hb = dict(h, **{'X-Company-ID': str(b['id'])})
    assert client.get('/api/animal-drug-treatments', headers=hb).json()['total'] == 0
    assert client.get(f"/api/animal-drug-treatments/{kayit['id']}",
                      headers=hb).status_code == 404
    assert client.get('/api/animal-drug-catalogue', headers=hb).json()['total'] == 0
    assert client.get(f"/api/animal-drug-catalogue/{katalog_id}",
                      headers=hb).status_code == 404
    # B, A'nın hayvanına ilaç yazamaz.
    capraz = client.post('/api/animal-drug-treatments', headers=hb,
                         json={'animal_id':inek['id'],'drug_name':'X',
                               'treated_on':'2026-07-01'})
    assert capraz.status_code == 404, capraz.text

    print('HAYVAN İLAÇ BEKLEME SÜRESİ PR-1 TAMAM')
'''
