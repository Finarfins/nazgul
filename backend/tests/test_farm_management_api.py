"""Tarla Yönetimi V1 — FAZ 2 sözleşmesi: CRUD API ve dashboard.

Konu: mobil-erp#2.

Testlerin ağırlığı ÜÇ iddiada:

1. **Toplam maliyet istemciden gelemez.** Şemada alan yok; gönderilirse istek
   reddedilir ve değer her hâlükârda sunucuda türetilir. Sezon maliyeti ve
   dekar başına maliyet doğrudan buna dayanıyor.
2. **Çapraz kiracı erişim 404.** 403 değil — 403 "var ama sana kapalı"
   bilgisini sızdırır.
3. **İyimser kilit.** İki kişi aynı kaydı düzenlerse ikincisi birincinin
   yazdığını sessizce ezmez.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_farm_endpoints_do_not_fall_into_the_field_service_permission() -> None:
    """`/api/field-activities` da `/api/field` ile BAŞLIYOR.

    Bu testin varlık sebebi somut bir tuzak: mevcut izin çözümleyicisinde
    ``path.startswith("/api/field")`` kuralı vardı. Tarla kuralları ondan SONRA
    yazılsaydı bütün tarla uçları sessizce SAHA SERVİS iznine düşerdi — satış
    ve rapor rolleri tarla verisini göremez, buna karşılık saha teknisyeni
    parsel yazabilirdi. İkisi de yanlış.
    """
    sys.path.insert(0, str(BACKEND))
    from app.auth import required_permission

    assert required_permission("GET", "/api/farms") == "farm.view"
    assert required_permission("POST", "/api/farms") == "farm.manage"
    assert required_permission("GET", "/api/field-activities") == "farm.view"
    assert required_permission("GET", "/api/field-harvest-decision") == "farm.view"
    assert required_permission("POST", "/api/field-activities") == "farm.manage"
    assert required_permission("GET", "/api/field-dashboard") == "farm.view"
    # Girdi bağlama ayrı izin: depo rolü bunu yapabilir ama parsel yazamaz.
    assert required_permission("POST", "/api/field-activities/5/inputs") == "farm.inputs"
    # Saha servis uçları ETKİLENMEMELİ.
    assert required_permission("GET", "/api/field/snapshot") == "field_service"
    assert required_permission("POST", "/api/field/work-orders/1/status") == "field_service"


def test_every_farm_endpoint_is_covered_by_the_farm_permission_prefixes() -> None:
    """`/api/field-...` ile başlayan HER tarla ucu izin listesinde olmalı.

    Bu tuzak İKİ KEZ yaşandı (FAZ 2'de faaliyet/hasat/görev/pano, FAZ 5'te
    güvenlik ucu): yeni bir uç eklenip ``_FARM_PATH_PREFIXES``'e yazılmayınca
    ``path.startswith("/api/field")`` kuralına düşüyor ve SESSİZCE saha servis
    iznine bağlanıyor — satış/rapor rolleri veriyi göremez, saha teknisyeni
    ise yazabilir.

    Tek tek uç saymak yerine ROTA TABLOSUNDAN türetiliyor: bir sonraki uç
    eklendiğinde bu test kendiliğinden kapsar.
    """
    sys.path.insert(0, str(BACKEND))
    from app.auth import required_permission
    from app.main import app

    # Yollar OpenAPI'den, `farm` ETİKETİNE göre toplanıyor. `app.routes`
    # kullanılamaz: API bir alt uygulamaya bağlı ve orada görünmüyor (denendi,
    # boş küme döndü — yani test hiçbir şey doğrulamıyordu).
    tarla_yollari = {
        yol
        for yol, islemler in app.openapi()["paths"].items()
        if any("farm" in (tanim.get("tags") or []) for tanim in islemler.values())
    }
    assert len(tarla_yollari) >= 14, (
        f"tarla rotaları eksik toplandı ({len(tarla_yollari)}) — test kendini "
        "doğruluyor olabilir"
    )

    kacaklar = sorted(
        yol for yol in tarla_yollari
        if not required_permission("GET", yol).startswith("farm.")
    )
    assert kacaklar == [], (
        "bu tarla uçları farm.* iznine bağlı DEĞİL (muhtemelen field_service'e "
        f"düştüler): {kacaklar}"
    )


def test_input_schema_has_no_total_cost_field() -> None:
    """Toplam maliyet İSTEMCİDEN ALINAMAZ — şema seviyesinde.

    Alan burada olsaydı bir istemci hatası (veya kötü niyet) sezon maliyetini
    ve dekar başına maliyeti sessizce kaydırırdı.
    """
    sys.path.insert(0, str(BACKEND))
    from app.farm_schemas import ActivityInputWrite

    assert "total_cost" not in ActivityInputWrite.model_fields
    # extra="forbid": gönderilse bile reddedilsin, sessizce yutulmasın.
    assert ActivityInputWrite.model_config.get("extra") == "forbid"


def run_farm_api_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_farm_api_sqlite(tmp_path: Path) -> None:
    run_farm_api_smoke(f"sqlite:///{(tmp_path / 'farm-api.db').as_posix()}")


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FarmApi!12345'


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
    return h, body


with TestClient(app) as client:
    h, body = admin_headers(client)
    firma_a = h['X-Company-ID']

    # --- kurulum -----------------------------------------------------------
    c = client.post('/api/farms', headers=h, json={'code':'merkez','name':'Merkez Çiftlik'})
    assert c.status_code == 201, c.text
    ciftlik = c.json()
    # Kod BÜYÜK harfe normalize edilmeli.
    assert ciftlik['code'] == 'MERKEZ', ciftlik

    p = client.post('/api/farm-parcels', headers=h,
                    json={'farm_id':ciftlik['id'],'code':'p1','name':'Dere Tarlası',
                          'area_decare':'50.0000','city':'Çorum'})
    assert p.status_code == 201, p.text
    parsel = p.json()

    s = client.post('/api/crop-seasons', headers=h,
                    json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Buğday',
                          'started_on':'2026-03-01','planted_area_decare':'45.0000'})
    assert s.status_code == 201, s.text
    sezon = s.json()
    assert sezon['status'] == 'PLANNED', sezon

    # --- 1) EKİLEN ALAN PARSELİ AŞAMAZ -------------------------------------
    tas = client.post('/api/crop-seasons', headers=h,
                      json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Arpa',
                            'planted_area_decare':'80.0000'})
    assert tas.status_code == 422, tas.text
    assert 'parsel alanını aşamaz' in tas.json()['detail'], tas.text

    # --- 2) FAALİYET ALANI AŞIYORSA GEREKÇE ŞART ---------------------------
    f = client.post('/api/field-activities', headers=h,
                    json={'season_id':sezon['id'],'activity_type':'spraying',
                          'performed_at':'2026-04-10T08:00:00+00:00',
                          'applied_area_decare':'60.0000',
                          'preharvest_interval_days':21})
    assert f.status_code == 422, f.text
    assert 'gerekçe' in f.json()['detail'], f.text

    f = client.post('/api/field-activities', headers=h,
                    json={'season_id':sezon['id'],'activity_type':'spraying',
                          'performed_at':'2026-04-10T08:00:00+00:00',
                          'applied_area_decare':'60.0000',
                          'area_override_reason':'İkinci geçiş yapıldı',
                          'preharvest_interval_days':21})
    assert f.status_code == 201, f.text
    faaliyet = f.json()

    # --- 3) TOPLAM MALİYET SUNUCUDA TÜRETİLİR ------------------------------
    # Istemci gondermeye CALISSA bile reddedilmeli (extra=forbid).
    kotu = client.post(f"/api/field-activities/{faaliyet['id']}/inputs", headers=h,
                       json={'input_name':'Herbisit','quantity':'10','unit':'LT',
                             'unit_cost':'250.00','total_cost':'1.00'})
    assert kotu.status_code == 422, kotu.text

    g = client.post(f"/api/field-activities/{faaliyet['id']}/inputs", headers=h,
                    json={'input_name':'Herbisit','quantity':'10','unit':'LT',
                          'unit_cost':'250.00','dose':'100','dose_unit':'ML/DEKAR'})
    assert g.status_code == 201, g.text
    girdi = g.json()
    # 10 * 250.00 = 2500.00 — sunucunun hesabı.
    assert Decimal(str(girdi['total_cost'])) == Decimal('2500.00'), girdi

    # --- 4) HASAT SEZON BAŞLANGICINDAN ÖNCE OLAMAZ -------------------------
    erken = client.post('/api/field-harvests', headers=h,
                        json={'season_id':sezon['id'],'harvested_on':'2026-01-15',
                              'quantity':'1000','unit':'KG'})
    assert erken.status_code == 422, erken.text
    assert 'sezon başlangıcından' in erken.json()['detail'], erken.text

    hs = client.post('/api/field-harvests', headers=h,
                     json={'season_id':sezon['id'],'harvested_on':'2026-08-15',
                           'quantity':'18000','unit':'KG','moisture_percent':'12.50'})
    assert hs.status_code == 201, hs.text

    # --- 5) İYİMSER KİLİT --------------------------------------------------
    guncel = client.get(f"/api/farms/{ciftlik['id']}", headers=h).json()
    bayat = {'code':'MERKEZ','name':'Merkez Çiftlik 2','status':'ACTIVE',
             'expected_updated_at':'2020-01-01T00:00:00+00:00'}
    r = client.put(f"/api/farms/{ciftlik['id']}", headers=h, json=bayat)
    assert r.status_code == 409, r.text
    assert 'değişti' in r.json()['detail'], r.text

    iyi = dict(bayat, expected_updated_at=guncel['updated_at'])
    r = client.put(f"/api/farms/{ciftlik['id']}", headers=h, json=iyi)
    assert r.status_code == 200, r.text
    assert r.json()['name'] == 'Merkez Çiftlik 2'

    # --- 6) ÇAPRAZ KİRACI 404 ----------------------------------------------
    b = client.post('/api/companies', headers=h, json={'name':'Tarla B Firması'})
    assert b.status_code == 201, b.text
    hb = dict(h, **{'X-Company-ID': str(b.json()['id'])})

    for yol in (f"/api/farms/{ciftlik['id']}", f"/api/farm-parcels/{parsel['id']}",
                f"/api/crop-seasons/{sezon['id']}", f"/api/field-activities/{faaliyet['id']}"):
        r = client.get(yol, headers=hb)
        assert r.status_code == 404, f'{yol} -> {r.status_code} (404 bekleniyordu)'

    # B firmasi A firmasinin ciftligine parsel BAGLAYAMAZ.
    r = client.post('/api/farm-parcels', headers=hb,
                    json={'farm_id':ciftlik['id'],'code':'X','name':'X','area_decare':'1'})
    assert r.status_code == 404, r.text

    # --- 7) DASHBOARD TÜRETMELERİ ------------------------------------------
    # Sezonu ACTIVE yap ki panele girsin.
    sg = client.get(f"/api/crop-seasons/{sezon['id']}", headers=h).json()
    r = client.put(f"/api/crop-seasons/{sezon['id']}", headers=h,
                   json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Buğday',
                         'started_on':'2026-03-01','planted_area_decare':'45.0000',
                         'status':'ACTIVE','expected_updated_at':sg['updated_at']})
    assert r.status_code == 200, r.text

    client.post('/api/field-tasks', headers=h,
                json={'title':'Gübre at','season_id':sezon['id'],'due_date':'2020-01-01'})

    d = client.get('/api/field-dashboard', headers=h)
    assert d.status_code == 200, d.text
    pano = d.json()
    assert pano['summary']['parcel_count'] == 1, pano
    assert Decimal(pano['summary']['total_area_decare']) == Decimal('50'), pano
    assert pano['tasks']['overdue'] == 1, pano

    (sz,) = [x for x in pano['seasons'] if x['season_id'] == sezon['id']]
    assert Decimal(sz['total_cost']) == Decimal('2500.00'), sz
    # 18000 kg / 45 dekar = 400 kg/dekar
    assert Decimal(sz['yield_per_decare']) == Decimal('400'), sz
    # 2500 / 45 = 55.5556 -> 55.56
    assert Decimal(sz['cost_per_decare']) == Decimal('55.56'), sz

    # --- 8) SAYFALAMA ------------------------------------------------------
    l = client.get('/api/farm-parcels?limit=1&offset=0', headers=h).json()
    assert l['limit'] == 1 and l['total'] == 1 and len(l['items']) == 1, l

    print('TARLA FAZ-2 SOZLESMESI TAMAM')
'''
