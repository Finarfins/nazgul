"""Tarla Yönetimi V1 — FAZ 5: ilaç güvenlik aralıkları.

Konu: mobil-erp#2.

`preharvest_interval_days` ve `reentry_interval_days` V1'de TOPLANIYOR ama
hiçbir yerde KULLANILMIYORDU. Toplanıp kullanılmayan bir güvenlik verisi, hiç
toplanmamasından kötüdür: kullanıcı sistemin kontrol ettiğini sanır.

Testlerin ağırlığı dört iddiada:

1. **Süresi dolmadan hasat gerekçesiz GEÇMEZ** — 422, ve mesaj güvenli tarihi
   söyler (kullanıcı ne zaman hasat edebileceğini bilmeli).
2. **Gerekçeyle geçer ve gerekçe KAYDA yazılır** — denetimde "bu ürün neden
   erken hasat edildi" sorusunun cevabı satırla birlikte gelsin.
3. **Süre dolduktan sonra engel kalkar** — kontrol kalıcı bir duvar değil.
4. **Süresi GİRİLMEMİŞ faaliyet ihlal sayılmaz** — bilinmeyeni ihlal saymak
   kullanıcıyı gerekçe yazmaya alıştırır ve gerçek uyarıyı değersizleştirir.

Ayrıca saat dilimi: `performed_at` UTC saklanıyor ama bekleme günü İSTANBUL
gününe göre sayılıyor. Akşam geç saatte (yerel) yapılan bir ilaçlama UTC'de
ertesi güne düşer ve bekleme BİR GÜN ERKEN dolmuş görünürdü.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_safety_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_pesticide_safety_sqlite(tmp_path: Path) -> None:
    run_safety_smoke(f"sqlite:///{(tmp_path / 'farm-safety.db').as_posix()}")


def test_local_day_boundary_is_not_utc() -> None:
    """Saat dilimi kayması: 23:30 (İstanbul) UTC'de bir ÖNCEKİ gündür.

    Bekleme süresi UTC gününden sayılsaydı hasat bir gün erken serbest
    kalırdı. Kalıntı süresinde bir gün gerçek bir farktır.
    """
    from datetime import datetime, timezone

    sys.path.insert(0, str(BACKEND))
    from app.routers.farm import _yerel_gun

    # 2026-06-10 23:30 İstanbul = 2026-06-10 20:30 UTC → yerel gün 10 Haziran.
    assert _yerel_gun(datetime(2026, 6, 10, 20, 30, tzinfo=timezone.utc)).isoformat() == "2026-06-10"
    # 2026-06-10 22:30 UTC = 2026-06-11 01:30 İstanbul → yerel gün 11 Haziran.
    assert _yerel_gun(datetime(2026, 6, 10, 22, 30, tzinfo=timezone.utc)).isoformat() == "2026-06-11"


_SMOKE = r'''
from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FarmSafety!12345'


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

    ciftlik = client.post('/api/farms', headers=h, json={'code':'g1','name':'Güvenlik Çiftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'gp','name':'Güvenlik Parsel',
                               'area_decare':'30.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Domates',
                              'started_on':'2026-03-01','planted_area_decare':'30.0000'}).json()

    # 1 Haziran'da ilaçlama, 21 gün hasat bekleme → güvenli tarih 22 Haziran.
    ilac = client.post('/api/field-activities', headers=h,
                       json={'season_id':sezon['id'],'activity_type':'SPRAYING',
                             'performed_at':'2026-06-01T09:00:00+03:00',
                             'applied_area_decare':'30.0000',
                             'preharvest_interval_days':21,
                             'reentry_interval_days':3})
    assert ilac.status_code == 201, ilac.text

    # --- 1) SÜRESİ DOLMADAN HASAT GEREKÇESİZ GEÇMEZ ------------------------
    erken = client.post('/api/field-harvests', headers=h,
                        json={'season_id':sezon['id'],'harvested_on':'2026-06-15',
                              'quantity':'5000','unit':'KG'})
    assert erken.status_code == 422, erken.text
    detay = erken.json()['detail']
    # Kullanıcı NE ZAMAN hasat edebileceğini öğrenmeli.
    assert '2026-06-22' in detay, detay
    assert '21' in detay, detay

    # Sınır günü de engelli olmalı: 21 gün BEKLENECEK, 21. gün henüz güvenli değil.
    sinir = client.post('/api/field-harvests', headers=h,
                        json={'season_id':sezon['id'],'harvested_on':'2026-06-21',
                              'quantity':'1','unit':'KG'})
    assert sinir.status_code == 422, sinir.text

    # --- 2) GEREKÇEYLE GEÇER VE GEREKÇE KAYDA YAZILIR ----------------------
    gerekcesiz_sayim = client.get('/api/field-harvests', headers=h).json()['total']
    assert gerekcesiz_sayim == 0, 'engellenen hasat kaydedilmiş olmamalı'

    zorla = client.post('/api/field-harvests', headers=h,
                        json={'season_id':sezon['id'],'harvested_on':'2026-06-15',
                              'quantity':'5000','unit':'KG',
                              'safety_override_reason':'İlaçlama tarihi yanlış girilmişti'})
    assert zorla.status_code == 201, zorla.text
    assert zorla.json()['safety_override_reason'] == 'İlaçlama tarihi yanlış girilmişti', zorla.json()

    # --- 3) SÜRE DOLDUKTAN SONRA ENGEL KALKAR ------------------------------
    gec = client.post('/api/field-harvests', headers=h,
                      json={'season_id':sezon['id'],'harvested_on':'2026-06-22',
                            'quantity':'4000','unit':'KG'})
    assert gec.status_code == 201, gec.text
    assert gec.json()['safety_override_reason'] is None, gec.json()

    # --- 4) SÜRESİ GİRİLMEMİŞ FAALİYET İHLAL SAYILMAZ ----------------------
    s2 = client.post('/api/crop-seasons', headers=h,
                     json={'parcel_id':parsel['id'],'season_year':2025,'crop':'Biber',
                           'started_on':'2025-03-01'}).json()
    client.post('/api/field-activities', headers=h,
                json={'season_id':s2['id'],'activity_type':'SPRAYING',
                      'performed_at':'2025-06-01T09:00:00+03:00',
                      'applied_area_decare':'30.0000'})
    serbest = client.post('/api/field-harvests', headers=h,
                          json={'season_id':s2['id'],'harvested_on':'2025-06-02',
                                'quantity':'100','unit':'KG'})
    assert serbest.status_code == 201, serbest.text

    # --- 5) UYARI UCU İKİ KISITI AYRI VERİYOR ------------------------------
    guvenlik = client.get('/api/field-safety', headers=h)
    assert guvenlik.status_code == 200, guvenlik.text
    g = guvenlik.json()
    # ÜÇÜNCÜ LİSTE: `plantback_blocks` (ekim-arası bekleme, göç
    # 20260907_0072). Bu kapı ucun ANAHTAR KÜMESİNİ donduruyor, yani
    # yeni liste buraya BİLİNÇLİ olarak yazıldı — sessizce eklenemezdi.
    assert set(g) == {'as_of', 'harvest_blocks', 'reentry_blocks',
                      'plantback_blocks'}, g
    # Geçmiş tarihli ilaçlama olduğu için bugün itibarıyla kısıt kalmamalı.
    assert g['harvest_blocks'] == [], g
    assert g['reentry_blocks'] == [], g

    # Bugün ilaçlama yap: HER İKİ kısıt da görünmeli.
    from datetime import datetime, timezone
    simdi = datetime.now(timezone.utc).isoformat()
    client.post('/api/field-activities', headers=h,
                json={'season_id':sezon['id'],'activity_type':'SPRAYING',
                      'performed_at':simdi,'applied_area_decare':'30.0000',
                      'preharvest_interval_days':14,'reentry_interval_days':2})
    g = client.get('/api/field-safety', headers=h).json()
    assert len(g['harvest_blocks']) == 1, g['harvest_blocks']
    assert g['harvest_blocks'][0]['season_id'] == sezon['id'], g
    assert len(g['reentry_blocks']) == 1, g['reentry_blocks']
    assert g['reentry_blocks'][0]['parcel_name'] == 'Güvenlik Parsel', g

    # --- 6) ÇAPRAZ KİRACI: B firması A'nın kısıtını GÖRMEZ -----------------
    b = client.post('/api/companies', headers=h, json={'name':'Güvenlik B'}).json()
    hb = dict(h, **{'X-Company-ID': str(b['id'])})
    gb = client.get('/api/field-safety', headers=hb).json()
    assert gb['harvest_blocks'] == [] and gb['reentry_blocks'] == [], gb

    print('TARLA FAZ-5 GUVENLIK ARALIKLARI TAMAM')
'''
