"""ÇKS tek ürün kilidi — üçüncü yıl aynı ürün gerekçesiz GEÇMEZ.

Kilit `_hasat_guvenlik_dogrula` ile aynı şekilde: block / warn / require_reason.
`allow` YOK — uyum kontrolünü tamamen kapatmak sessiz bir düğme olurdu.

Yıl aritmetiği bilinçli: Y-1 VE Y-2 ayrı bağlanır. `LIMIT 2` + sırasız okuma
bir yıl boşluğunu üçüncü yıl sanırdı.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_monoculture_schema_has_no_allow_level() -> None:
    sys.path.insert(0, str(BACKEND))
    from app.routers.companies import CompanyPolicyUpdate

    def seviyeler(alan: str) -> set[str]:
        annotation = CompanyPolicyUpdate.model_fields[alan].annotation
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

    assert seviyeler("farm_monoculture_policy") == {"warn", "require_reason", "block"}
    assert "allow" not in seviyeler("farm_monoculture_policy")


def test_monoculture_consecutive_year_arithmetic() -> None:
    """Yıl boşluğu seriyi kırar; LIMIT 2 bunu göremezdi."""
    sys.path.insert(0, str(BACKEND))
    from app.routers.farm import _monokultur_ihlal_mi

    # 2024 + 2025 Domates → 2026 üçüncü yıl.
    assert _monokultur_ihlal_mi(
        {2024: ["Domates"], 2025: ["Domates"]}, 2026, "Domates"
    )
    # 2023 + 2025, 2024 yok → boşluk, 2026 üçüncü yıl DEĞİL.
    assert not _monokultur_ihlal_mi(
        {2023: ["Domates"], 2025: ["Domates"]}, 2026, "Domates"
    )
    # Farklı ürün Y-1'de seriyi sıfırlar.
    assert not _monokultur_ihlal_mi(
        {2024: ["Biber"], 2025: ["Domates"]}, 2026, "Domates"
    )
    # İlk iki yıl: Y-2 yok.
    assert not _monokultur_ihlal_mi({2025: ["Domates"]}, 2026, "Domates")
    # casefold: "domates" == "Domates"
    assert _monokultur_ihlal_mi(
        {2024: ["domates"], 2025: ["DOMATES"]}, 2026, "Domates"
    )
    # Bir yılda karışık ürün o yılın tek-ürün iddiasını kuramaz.
    assert not _monokultur_ihlal_mi(
        {2024: ["Domates", "Biber"], 2025: ["Domates"]}, 2026, "Domates"
    )


def test_monoculture_history_query_binds_two_years() -> None:
    """Geçmiş sorgusu `:y1` ve `:y2` bağlar; LIMIT 2 yok."""
    kaynak = (BACKEND / "app" / "routers" / "farm.py").read_text(encoding="utf-8")
    bas = kaynak.index("def _monokultur_gecmisi")
    son = kaynak.index("def _monokultur_ihlal_mi")
    govde = kaynak[bas:son]
    sql_bas = govde.index('"""SELECT')
    sql_son = govde.index('"""', sql_bas + 3)
    sql = govde[sql_bas:sql_son]
    assert "season_year IN (:y1, :y2)" in sql, sql
    assert "LIMIT 2" not in sql, sql
    assert "company_id=:cid" in sql


def run_monoculture_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_monoculture_sqlite(tmp_path: Path) -> None:
    run_monoculture_smoke(f"sqlite:///{(tmp_path / 'farm-mono.db').as_posix()}")


_SMOKE = r'''
from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'FarmMono!12345'


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

    ayar = client.get('/api/company-settings', headers=h).json()
    assert ayar['farm_monoculture_policy'] == 'require_reason', ayar

    ciftlik = client.post('/api/farms', headers=h, json={'code':'m1','name':'Mono Çiftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'mp','name':'Mono Parsel',
                               'area_decare':'10.0000'}).json()
    pid = parsel['id']

    def sezon(yil, urun, **ek):
        return client.post('/api/crop-seasons', headers=h, json={
            'parcel_id': pid, 'season_year': yil, 'crop': urun, **ek})

    # --- 1) İLK İKİ YIL GEÇER ----------------------------------------------
    r = sezon(2024, 'Domates')
    assert r.status_code == 201, r.text
    r = sezon(2025, 'Domates')
    assert r.status_code == 201, r.text

    # --- 2) ÜÇÜNCÜ YIL GEREKÇESİZ 422 --------------------------------------
    erken = sezon(2026, 'Domates')
    assert erken.status_code == 422, erken.text
    assert '2024' in erken.json()['detail'], erken.text
    assert 'gerekçe' in erken.json()['detail'].lower() or 'gerekce' in erken.json()['detail'].lower(), erken.text

    # --- 3) GEREKÇEYLE GEÇER VE KAYDA YAZILIR ------------------------------
    zorla = sezon(2026, 'Domates', monoculture_override_reason='Müşteri sözleşmesi')
    assert zorla.status_code == 201, zorla.text
    assert zorla.json()['monoculture_override_reason'] == 'Müşteri sözleşmesi', zorla.json()
    assert zorla.json()['monoculture_warning'], zorla.json()

    # Not-only güncelleme denetim sütunlarını silmemeli.
    not_upd = client.put('/api/crop-seasons/' + str(zorla.json()['id']), headers=h, json={
        'parcel_id': pid, 'season_year': 2026, 'crop': 'Domates',
        'status': zorla.json()['status'], 'notes': 'sadece not',
        'expected_updated_at': zorla.json()['updated_at'],
    })
    assert not_upd.status_code == 200, not_upd.text
    assert not_upd.json()['monoculture_override_reason'] == 'Müşteri sözleşmesi', not_upd.json()
    assert not_upd.json()['monoculture_warning'], not_upd.json()
    assert not_upd.json()['notes'] == 'sadece not', not_upd.json()

    # --- 4) FARKLI ÜRÜN SERİYİ SIFIRLAR ------------------------------------
    p2 = client.post('/api/farm-parcels', headers=h,
                     json={'farm_id':ciftlik['id'],'code':'mp2','name':'Rotasyon',
                           'area_decare':'8.0000'}).json()
    for yil, urun in ((2024, 'Biber'), (2025, 'Domates')):
        r = client.post('/api/crop-seasons', headers=h, json={
            'parcel_id': p2['id'], 'season_year': yil, 'crop': urun})
        assert r.status_code == 201, r.text
    r = client.post('/api/crop-seasons', headers=h, json={
        'parcel_id': p2['id'], 'season_year': 2026, 'crop': 'Domates'})
    assert r.status_code == 201, r.text  # Y-2 Biber, seri yok

    # --- 5) YIL BOŞLUĞU SERİYİ KIRAR ---------------------------------------
    p3 = client.post('/api/farm-parcels', headers=h,
                     json={'farm_id':ciftlik['id'],'code':'mp3','name':'Boşluk',
                           'area_decare':'8.0000'}).json()
    for yil in (2023, 2025):
        r = client.post('/api/crop-seasons', headers=h, json={
            'parcel_id': p3['id'], 'season_year': yil, 'crop': 'Domates'})
        assert r.status_code == 201, r.text
    r = client.post('/api/crop-seasons', headers=h, json={
        'parcel_id': p3['id'], 'season_year': 2026, 'crop': 'Domates'})
    assert r.status_code == 201, r.text  # 2024 yok

    # --- 6) POLİTİKA: block gerekçeyi de reddeder --------------------------
    p4 = client.post('/api/farm-parcels', headers=h,
                     json={'farm_id':ciftlik['id'],'code':'mp4','name':'Blok',
                           'area_decare':'8.0000'}).json()
    for yil in (2024, 2025):
        assert client.post('/api/crop-seasons', headers=h, json={
            'parcel_id': p4['id'], 'season_year': yil, 'crop': 'Buğday'}).status_code == 201
    kural_yaz(client, h, farm_monoculture_policy='block')
    r = client.post('/api/crop-seasons', headers=h, json={
        'parcel_id': p4['id'], 'season_year': 2026, 'crop': 'Buğday',
        'monoculture_override_reason': 'Gerekçem var'})
    assert r.status_code == 422, r.text
    assert 'izin vermiyor' in r.json()['detail'], r.text

    # --- 7) warn: geçer, uyarı kayda yazılır --------------------------------
    kural_yaz(client, h, farm_monoculture_policy='warn')
    p5 = client.post('/api/farm-parcels', headers=h,
                     json={'farm_id':ciftlik['id'],'code':'mp5','name':'Uyarı',
                           'area_decare':'8.0000'}).json()
    for yil in (2024, 2025):
        assert client.post('/api/crop-seasons', headers=h, json={
            'parcel_id': p5['id'], 'season_year': yil, 'crop': 'Arpa'}).status_code == 201
    r = client.post('/api/crop-seasons', headers=h, json={
        'parcel_id': p5['id'], 'season_year': 2026, 'crop': 'Arpa'})
    assert r.status_code == 201, r.text
    assert r.json()['monoculture_warning'], r.json()
    assert r.json()['monoculture_override_reason'] is None, r.json()

    kural_yaz(client, h, farm_monoculture_policy='require_reason')

    # --- 7b) CANCELLED seriye girmez; ürün değişince kilit yeniden bakar ----
    p6 = client.post('/api/farm-parcels', headers=h,
                     json={'farm_id':ciftlik['id'],'code':'mp6','name':'İptal',
                           'area_decare':'8.0000'}).json()
    r24 = client.post('/api/crop-seasons', headers=h, json={
        'parcel_id': p6['id'], 'season_year': 2024, 'crop': 'Domates'})
    assert r24.status_code == 201, r24.text
    iptal = client.put('/api/crop-seasons/' + str(r24.json()['id']), headers=h, json={
        'parcel_id': p6['id'], 'season_year': 2024, 'crop': 'Domates',
        'status': 'CANCELLED', 'expected_updated_at': r24.json()['updated_at']})
    assert iptal.status_code == 200, iptal.text
    r25 = client.post('/api/crop-seasons', headers=h, json={
        'parcel_id': p6['id'], 'season_year': 2025, 'crop': 'Domates'})
    assert r25.status_code == 201, r25.text
    r26 = client.post('/api/crop-seasons', headers=h, json={
        'parcel_id': p6['id'], 'season_year': 2026, 'crop': 'Domates'})
    assert r26.status_code == 201, r26.text  # 2024 iptal, seri yok

    p7 = client.post('/api/farm-parcels', headers=h,
                     json={'farm_id':ciftlik['id'],'code':'mp7','name':'Ürün değiş',
                           'area_decare':'8.0000'}).json()
    for yil in (2024, 2025):
        assert client.post('/api/crop-seasons', headers=h, json={
            'parcel_id': p7['id'], 'season_year': yil, 'crop': 'Domates'}).status_code == 201
    biber = client.post('/api/crop-seasons', headers=h, json={
        'parcel_id': p7['id'], 'season_year': 2026, 'crop': 'Biber'})
    assert biber.status_code == 201, biber.text
    kirilim = client.put('/api/crop-seasons/' + str(biber.json()['id']), headers=h, json={
        'parcel_id': p7['id'], 'season_year': 2026, 'crop': 'Domates',
        'status': biber.json()['status'],
        'expected_updated_at': biber.json()['updated_at']})
    assert kirilim.status_code == 422, kirilim.text
    gerekceyle = client.put('/api/crop-seasons/' + str(biber.json()['id']), headers=h, json={
        'parcel_id': p7['id'], 'season_year': 2026, 'crop': 'Domates',
        'status': biber.json()['status'],
        'monoculture_override_reason': 'Rotasyon iptal',
        'expected_updated_at': biber.json()['updated_at']})
    assert gerekceyle.status_code == 200, gerekceyle.text
    assert gerekceyle.json()['monoculture_override_reason'] == 'Rotasyon iptal', gerekceyle.json()

    # --- 8) allow şemada yok -----------------------------------------------
    kotu = client.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'allow','credit_limit_policy':'allow',
        'farm_monoculture_policy':'allow'})
    assert kotu.status_code == 422, kotu.text

    # --- 9) ÇAPRAZ KİRACI --------------------------------------------------
    b = client.post('/api/companies', headers=h, json={'name':'Mono B'}).json()
    hb = dict(h, **{'X-Company-ID': str(b['id'])})
    ciftlik_b = client.post('/api/farms', headers=hb, json={'code':'mb','name':'B Çiftlik'}).json()
    parsel_b = client.post('/api/farm-parcels', headers=hb,
                           json={'farm_id':ciftlik_b['id'],'code':'bp','name':'B Parsel',
                                 'area_decare':'10.0000'}).json()
    # B'nin boş parselinde üçüncü yıl yok; A'nın geçmişi sızmamalı.
    r = client.post('/api/crop-seasons', headers=hb, json={
        'parcel_id': parsel_b['id'], 'season_year': 2026, 'crop': 'Domates'})
    assert r.status_code == 201, r.text

    print('TARLA MONOKULTUR KILIDI TAMAM')
'''
