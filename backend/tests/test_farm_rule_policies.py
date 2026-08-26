"""Tarla Yönetimi V1 — FAZ 9: kuralların firma bazlı ayarlanması.

Konu: mobil-erp#2 · owner kararı: alan aşımı, erken hasat ve doz zorunluluğu
her firmanın kendi kararı olsun.

**EN ÖNEMLİ İDDİA — ERKEN HASATTA "KAPAT" SEVİYESİ YOK.** En gevşek seviye
`warn`. Kontrolü tamamen kapatabilen bir ayar, sessiz bir güvenlik kapatma
düğmesi olurdu ve o düğmeye bir kez basılıp unutulur. `warn` modunda kayıt
OLUŞUYOR ama sistemin bulduğu ihlal `field_harvests.safety_warning` sütununa
YAZILIYOR: gevşetme kaydı yok etmiyor, yalnız engellemiyor.

Bu test şunları çiviliyor:

1. **Varsayılanlar mevcut davranışı korur** — hiçbir firmanın davranışı bu
   migration'la değişmiyor.
2. **Her seviye GERÇEKTEN farklı davranıyor.** Bir ayarın "var ama etkisiz"
   olması, hiç olmamasından kötüdür: kullanıcı koruma sanır.
3. **`warn` modunda uyarı kayda yazılıyor** ve kullanıcının gerekçesinden
   AYRI sütunda duruyor — denetimde kimin ne dediği ayırt edilebilmeli.
4. **API şeması `allow` kabul etmiyor** (erken hasat için); yani gevşetme
   sınırı şema seviyesinde, unutulabilecek bir `if` değil.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_early_harvest_schema_has_no_allow_level() -> None:
    """Şema seviyesinde kanıt: erken hasat kuralı KAPATILAMAZ.

    Bunu bir uç testine bırakmıyoruz; şemadaki değer kümesi doğrudan
    okunuyor ki biri "allow" eklemek istediğinde bu test düşsün.
    """
    sys.path.insert(0, str(BACKEND))
    from app.routers.companies import CompanyPolicyUpdate

    def seviyeler(alan: str) -> set[str]:
        annotation = CompanyPolicyUpdate.model_fields[alan].annotation
        # Literal[...] | None → Literal argümanlarını topla
        bulunan: set[str] = set()
        yigin = [annotation]
        while yigin:
            item = yigin.pop()
            args = getattr(item, "__args__", ())
            for arg in args:
                if isinstance(arg, str):
                    bulunan.add(arg)
                else:
                    yigin.append(arg)
        return bulunan

    hasat = seviyeler("farm_early_harvest_policy")
    assert hasat == {"warn", "require_reason", "block"}, hasat
    assert "allow" not in hasat, "erken hasat kuralı KAPATILABİLİR hâle gelmiş!"

    # Alan aşımı bir ölçüm tutarsızlığı, güvenlik değil: orada `allow` var.
    assert seviyeler("farm_area_override_policy") == {"allow", "require_reason", "block"}


def run_policy_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_rule_policies_sqlite(tmp_path: Path) -> None:
    run_policy_smoke(f"sqlite:///{(tmp_path / 'farm-policies.db').as_posix()}")


_SMOKE = r'''
from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FarmPolicy!123'
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


def kural_yaz(client, h, **kurallar):
    r = client.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'allow', 'credit_limit_policy':'allow', **kurallar})
    assert r.status_code == 200, r.text
    return r.json()


with TestClient(app) as client:
    h = admin_headers(client)

    # --- 1) VARSAYILANLAR MEVCUT DAVRANIŞI KORUR ---------------------------
    ayar = client.get('/api/company-settings', headers=h)
    assert ayar.status_code == 200, ayar.text
    a = ayar.json()
    assert a['farm_area_override_policy'] == 'require_reason', a
    assert a['farm_early_harvest_policy'] == 'require_reason', a
    assert a['farm_spraying_dose_required'] is True, a

    ciftlik = client.post('/api/farms', headers=h, json={'code':'k1','name':'Kural Çiftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'kp','name':'Kural Parsel',
                               'area_decare':'20.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Domates',
                              'started_on':'2026-03-01','planted_area_decare':'20.0000'}).json()

    ASAN = {'season_id':sezon['id'],'activity_type':'FERTILIZING',
            'performed_at':ZAMAN,'applied_area_decare':'50.0000'}

    # --- 2) ALAN AŞIMI: ÜÇ SEVİYE ÜÇ FARKLI DAVRANIŞ -----------------------
    # require_reason (varsayılan): gerekçesiz 422
    assert client.post('/api/field-activities', headers=h, json=ASAN).status_code == 422

    kural_yaz(client, h, farm_area_override_policy='block')
    r = client.post('/api/field-activities', headers=h,
                    json={**ASAN, 'area_override_reason':'Gerekçem var'})
    # block: GEREKÇE OLSA BİLE geçmemeli — yoksa seviye "require_reason"la aynı olurdu.
    assert r.status_code == 422, r.text
    assert 'izin verilmiyor' in r.json()['detail'], r.text

    kural_yaz(client, h, farm_area_override_policy='allow')
    r = client.post('/api/field-activities', headers=h, json=ASAN)
    assert r.status_code == 201, r.text  # gerekçesiz geçiyor

    kural_yaz(client, h, farm_area_override_policy='require_reason')

    # --- 3) DOZ ZORUNLULUĞU AÇ/KAPA ----------------------------------------
    ilac = client.post('/api/field-activities', headers=h,
                       json={'season_id':sezon['id'],'activity_type':'SPRAYING',
                             'performed_at':ZAMAN,'applied_area_decare':'20.0000',
                             'preharvest_interval_days':30}).json()
    DOZSUZ = {'input_name':'İlaç','quantity':'2','unit':'LT'}
    assert client.post(f"/api/field-activities/{ilac['id']}/inputs",
                       headers=h, json=DOZSUZ).status_code == 422

    kural_yaz(client, h, farm_spraying_dose_required=False)
    assert client.post(f"/api/field-activities/{ilac['id']}/inputs",
                       headers=h, json=DOZSUZ).status_code == 201
    kural_yaz(client, h, farm_spraying_dose_required=True)

    # --- 4) ERKEN HASAT: block / require_reason / warn ---------------------
    ERKEN = {'season_id':sezon['id'],'harvested_on':'2026-05-15',
             'quantity':'100','unit':'KG'}

    # require_reason (varsayılan): gerekçesiz 422
    assert client.post('/api/field-harvests', headers=h, json=ERKEN).status_code == 422

    kural_yaz(client, h, farm_early_harvest_policy='block')
    r = client.post('/api/field-harvests', headers=h,
                    json={**ERKEN, 'safety_override_reason':'Gerekçem var'})
    # block: gerekçe OLSA BİLE geçmemeli.
    assert r.status_code == 422, r.text
    assert 'izin vermiyor' in r.json()['detail'], r.text

    # warn: GEÇİYOR ama sistem ne bulduğunu YAZIYOR
    kural_yaz(client, h, farm_early_harvest_policy='warn')
    r = client.post('/api/field-harvests', headers=h, json=ERKEN)
    assert r.status_code == 201, r.text
    kayit = r.json()
    assert kayit['safety_warning'], ('warn modunda uyarı KAYDA YAZILMALI', kayit)
    assert 'gün bekleme' in kayit['safety_warning'], kayit
    # Kullanıcının gerekçesi AYRI sütunda ve BOŞ — sistem onun adına konuşmuyor.
    assert kayit['safety_override_reason'] is None, kayit

    # warn modunda kullanıcı yine de gerekçe yazarsa İKİSİ DE saklanmalı.
    r = client.post('/api/field-harvests', headers=h,
                    json={**ERKEN, 'safety_override_reason':'Alıcı bekliyordu'})
    assert r.status_code == 201, r.text
    assert r.json()['safety_warning'], r.json()
    assert r.json()['safety_override_reason'] == 'Alıcı bekliyordu', r.json()

    # Süresi DOLMUŞ hasatta uyarı OLMAMALI — warn modu her kayda damga vurmaz.
    kural_yaz(client, h, farm_early_harvest_policy='warn')
    r = client.post('/api/field-harvests', headers=h,
                    json={'season_id':sezon['id'],'harvested_on':'2026-07-01',
                          'quantity':'50','unit':'KG'})
    assert r.status_code == 201, r.text
    assert r.json()['safety_warning'] is None, r.json()

    # --- 5) GEÇERSİZ SEVİYE REDDEDİLİR -------------------------------------
    kotu = client.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'allow','credit_limit_policy':'allow',
        'farm_early_harvest_policy':'allow'})
    assert kotu.status_code == 422, ('erken hasat KAPATILAMAMALI', kotu.text)

    print('TARLA FAZ-9 KURAL AYARLARI TAMAM')
'''
