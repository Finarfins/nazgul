"""Hayvancılık V1 — FAZ 4 sözleşmesi: /api/vaccination-calendar.

Konu: mobil-erp#17.

Saf hesap `test_herd_vaccine_schedule.py`'de çivilendi. Burada UCUN iddiaları
sınanıyor ve dördü de sessizce yanlış olabilecek türden:

1. **Takvim, elle girilen `next_due_on` ile AYNI ŞEY DEĞİL.** Pano sayısı
   "planladım, yapmadım" der; takvim "mevzuat gereği yapılmalıydı" der. İkisini
   tek sayıya indirmek, hiç plan girmemiş bir işletmeye "aşınız tamam" demek
   olurdu.

2. **KODSUZ aşı kaydı hesaba girmez VE bu gizlenmez.** Serbest metinden tahmin
   ('FMD' vs 'Şap') sahte gecikme üretirdi; ama kodsuz kayıt varken "eksiği
   yok" cevabı da eksiktir. Sayı yanıtta AYRICA duruyor.

3. **Kodlu kayıt takvimi GERÇEKTEN ilerletir.** Aşı girilince satır gecikmeden
   çıkıp bir sonraki pencereye geçmeli — yoksa kullanıcı aşıyı yaptığı hâlde
   listede kalır ve listeye güvenmeyi bırakır.

4. **Uç `herd.view` iznine bağlı.** Eklenmemiş bir önek sessizce genel `read`
   iznine düşer; tarla modülünde bu tuzağa iki kez düşüldü.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_calendar_endpoint_is_herd_scoped() -> None:
    """`/api/vaccination-calendar` DOĞRU izne bağlı."""
    sys.path.insert(0, str(BACKEND))
    from app.auth import required_permission

    assert required_permission("GET", "/api/vaccination-calendar") == "herd.view"
    # Genel `read`'e DÜŞMEMİŞ olması iddianın kendisi.
    assert required_permission("GET", "/api/vaccination-calendar") != "read"


def run_calendar_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_vaccination_calendar_sqlite(tmp_path: Path) -> None:
    run_calendar_smoke(f"sqlite:///{(tmp_path / 'vacc-calendar.db').as_posix()}")


_SMOKE = r'''
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.business_time import business_today
from app.main import app

ADMIN_PW = 'VaccCalendar!2026'


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


BUGUN = business_today()
AY = 30


def gun(n):
    """BUGÜN'den n gün önce."""
    return (BUGUN - timedelta(days=n)).isoformat()


with TestClient(app) as client:
    h = admin_headers(client)

    def hayvan(**kw):
        r = client.post('/api/animals', headers=h, json=kw)
        assert r.status_code == 201, r.text
        return r.json()

    # 1) 3 yaşında dişi inek, HİÇ aşı yok → şap ve brusella GECİKMİŞ.
    inek = hayvan(ear_tag='TR1000000001', species='CATTLE', sex='FEMALE',
                  birth_date=gun(3*365))
    # 2) 3 yaşında ERKEK boğa → şap gecikmiş, brusella HİÇ ÇIKMAMALI.
    boga = hayvan(ear_tag='TR1000000002', species='CATTLE', sex='MALE',
                  birth_date=gun(3*365))
    # 3) Doğum tarihi GİRİLMEMİŞ hayvan → UNKNOWN, "tamam" değil.
    bilinmez = hayvan(ear_tag='TR1000000003', species='GOAT', sex='FEMALE')
    # 4) 1 aylık buzağı → şap penceresi henüz AÇILMAMIŞ (UPCOMING).
    buzagi = hayvan(ear_tag='TR1000000004', species='CATTLE', sex='FEMALE',
                    birth_date=gun(AY))

    takvim = client.get('/api/vaccination-calendar', headers=h)
    assert takvim.status_code == 200, takvim.text
    t = takvim.json()
    assert set(t) == {'as_of', 'summary', 'items'}, t

    def satir(animal_id, code):
        bulunan = [i for i in t['items'] if i['animal_id'] == animal_id and i['vaccine_code'] == code]
        return bulunan[0] if bulunan else None

    # --- 1) ERKEKTE BRUSELLA YOK -------------------------------------------
    assert satir(boga['id'], 'BRUCELLA') is None, 'erkek brusella listesine düşmüş'
    assert satir(boga['id'], 'FMD')['state'] == 'OVERDUE'
    # Dişide VAR — karşılaştırma testin dişlerini gösteriyor.
    assert satir(inek['id'], 'BRUCELLA')['state'] == 'OVERDUE'

    # --- 2) DOĞUM TARİHİ YOKSA "TAMAM" DEĞİL -------------------------------
    bilinmez_sap = satir(bilinmez['id'], 'FMD')
    assert bilinmez_sap['state'] == 'UNKNOWN', bilinmez_sap
    assert bilinmez_sap['due_from'] is None
    assert t['summary']['unknown_birth_date'] == 1, t['summary']
    # Sayı 'overdue'ya da 'upcoming'e KARIŞMAMIŞ olmalı.
    assert t['summary']['unknown'] >= 1

    # --- 3) PENCERESİ AÇILMAMIŞ HAYVAN GECİKMİŞ SAYILMAZ -------------------
    buzagi_sap = satir(buzagi['id'], 'FMD')
    assert buzagi_sap['state'] == 'UPCOMING', buzagi_sap
    assert buzagi_sap['overdue_days'] is None
    # Pencere ARALIK olarak geliyor, tek tarih değil.
    assert buzagi_sap['due_from'] != buzagi_sap['due_to'], buzagi_sap
    # Gerekçe kullanıcıya gidiyor.
    assert buzagi_sap['basis'], buzagi_sap

    # --- 4) KODSUZ KAYIT HESABA GİRMEZ AMA GİZLENMEZ -----------------------
    kodsuz = client.post('/api/animal-vaccinations', headers=h, json={
        'animal_id':inek['id'], 'vaccine':'FMD (kod girilmedi)', 'applied_on':gun(10)})
    assert kodsuz.status_code == 201, kodsuz.text
    assert kodsuz.json()['vaccine_code'] is None, kodsuz.json()

    t2 = client.get('/api/vaccination-calendar', headers=h).json()
    # Kodsuz kayıt takvimi İLERLETMEDİ: hâlâ gecikmiş.
    inek_sap = [i for i in t2['items']
                if i['animal_id'] == inek['id'] and i['vaccine_code'] == 'FMD'][0]
    assert inek_sap['state'] == 'OVERDUE', inek_sap
    assert inek_sap['last_applied_on'] is None, 'kodsuz kayıt hesaba girmiş'
    # AMA görünmez de değil.
    assert t2['summary']['uncoded_vaccinations'] == 1, t2['summary']

    # --- 5) KODLU KAYIT TAKVİMİ GERÇEKTEN İLERLETİR ------------------------
    kodlu = client.post('/api/animal-vaccinations', headers=h, json={
        'animal_id':inek['id'], 'vaccine':'Şap', 'vaccine_code':'fmd',
        'applied_on':gun(5)})
    assert kodlu.status_code == 201, kodlu.text
    # Kod BÜYÜK HARFE çevrilmiş olmalı, yoksa 'fmd' hesaba girmez.
    assert kodlu.json()['vaccine_code'] == 'FMD', kodlu.json()

    t3 = client.get('/api/vaccination-calendar', headers=h).json()
    inek_sap3 = [i for i in t3['items']
                 if i['animal_id'] == inek['id'] and i['vaccine_code'] == 'FMD'][0]
    # Artık gecikmiş DEĞİL: ikinci doz bir ay sonra bekleniyor.
    assert inek_sap3['state'] == 'UPCOMING', inek_sap3
    assert inek_sap3['dose_no'] == 2, inek_sap3
    assert inek_sap3['last_applied_on'] == gun(5), inek_sap3

    # --- 6) DURUM SÜZGECİ ---------------------------------------------------
    sadece_gecikmis = client.get('/api/vaccination-calendar', headers=h,
                                 params={'state':'overdue'}).json()
    assert sadece_gecikmis['items'], sadece_gecikmis
    assert {i['state'] for i in sadece_gecikmis['items']} == {'OVERDUE'}
    # Süzgeç ÖZETİ daraltmamalı: kullanıcı süzerken toplamı kaybetmemeli.
    assert sadece_gecikmis['summary'] == t3['summary'], 'süzgeç özeti bozmuş'

    # --- 7) GECİKMİŞLER ÜSTTE ----------------------------------------------
    durumlar = [i['state'] for i in t3['items']]
    sira = {'OVERDUE':0, 'DUE':1, 'UPCOMING':2, 'UNKNOWN':3}
    assert durumlar == sorted(durumlar, key=lambda s: sira[s]), durumlar

    # --- 8) PANO SAYISI İLE TAKVİM AYRI ŞEYLER -----------------------------
    pano = client.get('/api/herd-dashboard', headers=h).json()
    # Hiç `next_due_on` girilmedi → pano "planlanmış ama yapılmamış" 0 diyor...
    assert pano['summary']['vaccination_overdue'] == 0, pano['summary']
    # ...ama takvim GERÇEK eksik olduğunu söylüyor. İkisi karışmamalı.
    assert t3['summary']['overdue'] > 0, t3['summary']

print('OK')
'''
