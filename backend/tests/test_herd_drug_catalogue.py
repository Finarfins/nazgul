"""Hayvan ilaç katalogu: joker tür, gerçek MIXED, kiracı izolasyonu.

`species=''` joker; `MIXED` gerçek değer. İkisi karıştırılamaz.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_catalogue_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_catalogue_sqlite(tmp_path: Path) -> None:
    run_catalogue_smoke(f"sqlite:///{(tmp_path / 'herd-catalogue.db').as_posix()}")


_SMOKE = r'''
from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'HerdCatalogue!12345'

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

    inek = client.post('/api/animals', headers=h,
                       json={'ear_tag':'TR1111111111','species':'CATTLE',
                             'sex':'FEMALE','birth_date':'2021-05-10'}).json()

    # Joker (bütün türler) + tür özeli + gerçek MIXED = 3 ayrı satır.
    joker = client.post('/api/animal-drug-catalogue', headers=h,
                        json={'drug_name':'Penisilin','species':'',
                              'milk_withdrawal_days':3,'meat_withdrawal_days':28})
    assert joker.status_code == 201, joker.text

    ozel = client.post('/api/animal-drug-catalogue', headers=h,
                       json={'drug_name':'Penisilin','species':'CATTLE',
                             'milk_withdrawal_days':5,'meat_withdrawal_days':30})
    assert ozel.status_code == 201, ozel.text

    karma = client.post('/api/animal-drug-catalogue', headers=h,
                        json={'drug_name':'Penisilin','species':'MIXED',
                              'milk_withdrawal_days':4,'meat_withdrawal_days':25})
    assert karma.status_code == 201, karma.text

    # Cattle hayvanı tür özeli (5) bulur; joker (3) ve MIXED (4) DEĞİL.
    ilac = client.post('/api/animal-drug-treatments', headers=h,
                       json={'animal_id':inek['id'],'drug_name':'Penisilin',
                             'treated_on':'2026-07-01'}).json()[0]
    assert ilac['milk_withdrawal_days'] == 5, ilac
    assert ilac['milk_withdrawal_source'] == 'CATALOGUE', ilac
    assert ilac['catalogue_milk_days'] == 5, ilac

    # MIXED hayvanı (yok) — ama MIXED katalog satırı joker DEĞİL: CATTLE
    # sorgusu onu bulmaz. CATTLE özeli varsa özeli alır.

    # Koyun hayvanı (yok, ama species GOAT) — joker (3) bulur.
    koyun = client.post('/api/animals', headers=h,
                        json={'ear_tag':'TR2222222222','species':'SHEEP',
                              'sex':'FEMALE'}).json()
    koyun_ilac = client.post('/api/animal-drug-treatments', headers=h,
                             json={'animal_id':koyun['id'],'drug_name':'Penisilin',
                                   'treated_on':'2026-07-01'}).json()[0]
    assert koyun_ilac['milk_withdrawal_days'] == 3, koyun_ilac
    assert koyun_ilac['milk_withdrawal_source'] == 'CATALOGUE', koyun_ilac

    # Çapraz kiracı: B, A'nın katalogunu göremez, kullanamaz.
    b = client.post('/api/companies', headers=h, json={'name':'Katalog B'}).json()
    hb = dict(h, **{'X-Company-ID': str(b['id'])})
    assert client.get('/api/animal-drug-catalogue', headers=hb).json()['total'] == 0
    assert client.get(f"/api/animal-drug-catalogue/{joker.json()['id']}",
                      headers=hb).status_code == 404
    b_hayvan = client.post('/api/animals', headers=hb,
                           json={'ear_tag':'TR3333333333','species':'CATTLE',
                                 'sex':'FEMALE'}).json()
    b_ilac = client.post('/api/animal-drug-treatments', headers=hb,
                         json={'animal_id':b_hayvan['id'],'drug_name':'Penisilin',
                               'treated_on':'2026-07-01'}).json()[0]
    assert b_ilac['milk_withdrawal_days'] is None, b_ilac
    assert b_ilac['milk_withdrawal_source'] is None, b_ilac

    print('HAYVAN İLAÇ KATALOGU TAMAM')
'''
