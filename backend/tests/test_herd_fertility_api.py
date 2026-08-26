"""Hayvancılık V1 — FAZ 5 sözleşmesi: /api/herd-fertility.

Konu: mobil-erp#17.

Saf hesap `test_herd_fertility.py`'de çivilendi. Burada UCUN iddiaları:

1. **Hiç doğurmamış dişi ortalamaya GİRMEZ, ayrı sayılır.** Girseydi ortalama
   düzelmesi imkânsız biçimde bozulurdu.
2. **Örnek sayısı ortalamayla birlikte döner.** Tek ölçümden çıkan bir ortalama
   sürü gerçeği gibi sunulamaz.
3. **Bağsız doğum sayısı raporlanır.** Gebelik başına tohumlama yalnız bağlı
   kayıtlardan; kapsamın dışı gizlenirse gösterge tam sanılır.
4. **Uç `herd.view` iznine bağlı** — önek listesine eklenmezse sessizce genel
   `read`e düşer.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_fertility_endpoint_is_herd_scoped() -> None:
    sys.path.insert(0, str(BACKEND))
    from app.auth import required_permission

    assert required_permission("GET", "/api/herd-fertility") == "herd.view"
    assert required_permission("GET", "/api/herd-fertility") != "read"


def run_fertility_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_herd_fertility_sqlite(tmp_path: Path) -> None:
    run_fertility_smoke(f"sqlite:///{(tmp_path / 'herd-fertility.db').as_posix()}")


_SMOKE = r'''
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'HerdFertility!2026'


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


D0 = date(2020, 1, 1)


with TestClient(app) as client:
    h = admin_headers(client)

    def hayvan(tag, sex='FEMALE', dogum=None):
        r = client.post('/api/animals', headers=h, json={
            'ear_tag':tag, 'species':'CATTLE', 'sex':sex,
            'birth_date':dogum.isoformat() if dogum else None})
        assert r.status_code == 201, r.text
        return r.json()

    def dogum(anne_id, gun, breeding_id=None):
        r = client.post('/api/animal-births', headers=h, json={
            'mother_id':anne_id, 'breeding_id':breeding_id,
            'birth_date':gun.isoformat(), 'outcome':'LIVE', 'offspring_count':1})
        assert r.status_code == 201, r.text
        return r.json()

    def tohumlama(animal_id, gun, sonuc=None):
        r = client.post('/api/animal-breedings', headers=h, json={
            'animal_id':animal_id, 'bred_on':gun.isoformat(), 'method':'AI'})
        assert r.status_code == 201, r.text
        kayit = r.json()
        if sonuc:
            g = client.put('/api/animal-breedings/%d' % kayit['id'], headers=h, json={
                'animal_id':animal_id, 'bred_on':gun.isoformat(), 'method':'AI',
                'result':sonuc, 'expected_updated_at':kayit['updated_at']})
            assert g.status_code == 200, g.text
        return kayit

    # --- İNEK A: iki doğum, aralık 340 gün (iyi), tohumlama BAĞLI -----------
    a = hayvan('TR2000000001', dogum=D0)
    d1 = date(2022, 1, 1)
    t1 = tohumlama(a['id'], d1 + timedelta(days=80), sonuc='PREGNANT')
    d2 = d1 + timedelta(days=340)
    dogum(a['id'], d1)
    dogum(a['id'], d2, breeding_id=t1['id'])

    # --- İNEK B: iki doğum, aralık 430 gün (SORUNLU), bağ YOK --------------
    b = hayvan('TR2000000002', dogum=D0)
    e1 = date(2022, 3, 1)
    dogum(b['id'], e1)
    dogum(b['id'], e1 + timedelta(days=430))

    # --- İNEK C: HİÇ doğurmamış ------------------------------------------
    c = hayvan('TR2000000003', dogum=date(2024, 1, 1))

    # --- BOĞA: dişi olmadığı için listede HİÇ olmamalı --------------------
    boga = hayvan('TR2000000004', sex='MALE', dogum=D0)

    r = client.get('/api/herd-fertility', headers=h)
    assert r.status_code == 200, r.text
    t = r.json()
    assert set(t) == {'as_of', 'thresholds', 'summary', 'items'}, t

    kimlikler = {i['animal_id'] for i in t['items']}
    # --- 1) ERKEK VE HİÇ DOĞURMAMIŞ LİSTEDE YOK --------------------------
    assert boga['id'] not in kimlikler, 'erkek döl verimi listesine düşmüş'
    assert c['id'] not in kimlikler, 'hiç doğurmamış hayvan satır olarak çıkmış'
    # AMA sayılıyor: kaybolmuş değil.
    assert t['summary']['never_calved'] == 1, t['summary']

    satir = {i['animal_id']: i for i in t['items']}

    # --- 2) ARALIKLAR DOĞRU VE SORUN İŞARETİ SON ARALIĞA BAKIYOR ---------
    assert satir[a['id']]['last_calving_interval_days'] == 340
    assert satir[a['id']]['interval_is_problem'] is False
    assert satir[b['id']]['last_calving_interval_days'] == 430
    assert satir[b['id']]['interval_is_problem'] is True
    assert t['summary']['problem_interval_count'] == 1, t['summary']

    # --- 3) ÖRNEK SAYISI ORTALAMAYLA BİRLİKTE ----------------------------
    # İki hayvanın da bir aralığı var → örnek 2, ortalama (340+430)/2 = 385.
    assert t['summary']['calving_interval_sample'] == 2, t['summary']
    assert t['summary']['avg_calving_interval_days'] == 385, t['summary']

    # --- 4) SERVİS PERİYODU YALNIZ BAĞLI KAYITTAN ------------------------
    assert satir[a['id']]['service_period_days'] == 80, satir[a['id']]
    # B'nin doğumu tohumlamaya bağlı DEĞİL → servis periyodu hesaplanmıyor.
    assert satir[b['id']]['service_period_days'] is None, satir[b['id']]
    # Ve bu kapsam boşluğu SAYILIYOR: A'nın 1 bağlı doğumu var, kalan 3 bağsız.
    assert t['summary']['births_without_breeding_link'] == 3, t['summary']

    # --- 5) GEBELİK BAŞINA TOHUMLAMA -------------------------------------
    # A: 1 tohumlama, 1'i gebe → sabit ölçekli Decimal-string. B: veri yok → None.
    assert satir[a['id']]['services_per_conception'] == '1.00', satir[a['id']]
    assert isinstance(satir[a['id']]['services_per_conception'], str), satir[a['id']]
    assert satir[b['id']]['services_per_conception'] is None, satir[b['id']]

    # --- 6) EŞİKLER YANITTA ----------------------------------------------
    # Kullanıcı "neye göre sorun" sorusunu ekranda görebilmeli.
    assert t['thresholds']['problem_calving_interval_days'] == 390, t['thresholds']
    assert t['thresholds']['target_calving_interval_days'] == 360, t['thresholds']

    # --- 7) SORUNLU HAYVAN ÜSTTE -----------------------------------------
    assert t['items'][0]['animal_id'] == b['id'], t['items']

    # --- 8) İLKİNE BUZAĞILAMA YAŞI ---------------------------------------
    assert satir[a['id']]['age_at_first_calving_days'] == (d1 - D0).days
    assert t['summary']['age_at_first_calving_sample'] == 2, t['summary']

print('OK')
'''
