"""Tarlaya giriş yasağı artık YAZMA yolunda da ısırıyor.

PHI kilidi hasat tarafına indi; `reentry_interval_days` toplanıyordu ama
HİÇ KULLANILMIYORDU (farm.py:1199-1201). Bu test o asimetriyi kapatır.

Şekil `_hasat_guvenlik_dogrula` ile birebir: block / warn / require_reason.
Tarih İSTANBUL gününe göre. Pencere [yapıldığı gün, güvenli).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_reentry_schema_has_no_allow_level() -> None:
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

    assert seviyeler("farm_reentry_policy") == {"warn", "require_reason", "block"}
    assert "allow" not in seviyeler("farm_reentry_policy")


def test_reentry_query_is_parcel_scoped_and_tenant_bound() -> None:
    kaynak = (BACKEND / "app" / "routers" / "farm.py").read_text(encoding="utf-8")
    bas = kaynak.index("_GIRIS_SORGU")
    son = kaynak.index("def _giris_ihlalleri")
    govde = kaynak[bas:son]
    assert "company_id=:cid" in govde
    assert "reentry_interval_days IS NOT NULL" in govde
    assert ":pid" in govde
    # `bindparam("pid", type_=Integer)` BURADA ARANMIYOR: kaynağı grep'lemek,
    # dizgeyi koruyup DAVRANIŞI bozan bir yeniden düzenlemeyi yeşil geçirirdi.
    # Tipin gerçekten iş görüp görmediği `run_bindparam_typing_probe` ile
    # PostgreSQL ikizinde ÖLÇÜLÜYOR.


def run_reentry_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr



def run_bindparam_typing_probe(database_url: str) -> None:
    """`pid` TİPİ olmadan `pid=None` PostgreSQL'de ÇÖZÜLEMEZ.

    Kaynağı grep'lemek `bindparam(...)` dizgesini yerinde bırakıp tipi etkisiz
    kılan bir yeniden düzenlemeyi (örneğin sorgunun `str()`inden yeni bir
    `text()` kurmak) YEŞİL geçirirdi. Bu ölçüm o boşluğu kapatıyor: sorgunun
    METNİ birebir aynı kalıp yalnız tip düşerse PostgreSQL `42P08
    AmbiguousParameter` veriyor, gerçek sabit ise vermiyor. `field-safety`
    raporu sorguyu `pid=None` ile çağırıyor; tip düşerse o uç 500 döner.

    SQLite'ta ölçülemez: tipi belirsiz bir parametre orada hata vermez, yani
    bu ayrım yalnız üretim diyalektinde görünür.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _BINDPARAM_PROBE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


_BINDPARAM_PROBE = r'''
import os

from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

# SEMAYI KURAN SEY ASAGIDAKI ICE AKTARMADIR. `app.main` ice aktarildigi anda,
# modul duzeyinde, alembic gocunu kosturur (`run_database_migrations`; lifespan
# icinde DEGIL). Bu olcum tablolarin VAR OLDUGUNU varsaymamali: yalniz
# `app.routers.farm`i ice aktarip dogrudan `create_engine` ile baglanan ilk
# surum, KARDES kosunun goclerini calistirmis olmasina bel bagliyordu ve TEK
# BASINA `UndefinedTable` ile dusuyordu — yesilligi SIRAYA bagliydi. Olculdu,
# iki yonde: bu ice aktarma OLMADAN bos veritabaninda kirmizi, kardes semayi
# kurduktan sonra yesil; ice aktarma VARKEN bos veritabaninda da yesil. Onceki
# surumdeki `with TestClient(app): pass` satiri semayi KURMUYORDU (o satir
# silinip ice aktarma birakilinca da yesil, olculdu); bu yuzden kaldirildi.
import app.main  # noqa: F401
from app.routers.farm import _GIRIS_SORGU

motor = create_engine(os.environ['DATABASE_URL'])

# Sorgunun METNI birebir ayni; DUSEN tek sey `bindparam` tipi.
tipsiz = text(str(_GIRIS_SORGU))

with motor.connect() as baglanti:
    try:
        baglanti.execute(tipsiz, {'cid': 1, 'pid': None}).all()
    except ProgrammingError as exc:
        kaynak = getattr(exc, 'orig', None)
        assert type(kaynak).__name__ == 'AmbiguousParameter', type(kaynak).__name__
        assert getattr(kaynak, 'sqlstate', None) == '42P08', kaynak
    else:
        raise AssertionError(
            'Tipsiz `pid=None` PostgreSQL tarafindan REDDEDILMEDI; bu olcum '
            'artik bir sey ayirt etmiyor.'
        )
    baglanti.rollback()

    # GERCEK sabit: ayni cagri hata VERMEZ ve `pid` verildiginde de calisir.
    baglanti.execute(_GIRIS_SORGU, {'cid': 1, 'pid': None}).all()
    baglanti.execute(_GIRIS_SORGU, {'cid': 1, 'pid': 7}).all()

print('GIRIS SORGUSU PID TIPI OK')
'''


def test_reentry_enforcement_sqlite(tmp_path: Path) -> None:
    run_reentry_smoke(f"sqlite:///{(tmp_path / 'farm-reentry.db').as_posix()}")


_SMOKE = r'''
from datetime import date, datetime, timezone, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.routers.farm import _yerel_gun, _giris_ihlalleri

ADMIN_PW = 'FarmReentry!123'


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


with TestClient(app) as client:
    h = admin_headers(client)

    ayar = client.get('/api/company-settings', headers=h).json()
    assert ayar['farm_reentry_policy'] == 'require_reason', ayar

    ciftlik = client.post('/api/farms', headers=h, json={'code':'r1','name':'Giriş Çiftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'rp','name':'Giriş Parsel',
                               'area_decare':'20.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Domates',
                              'started_on':'2026-03-01'}).json()

    # 1 Haziran ilaçlama, 3 gün giriş yasağı → güvenli 4 Haziran.
    ilac = client.post('/api/field-activities', headers=h, json={
        'season_id': sezon['id'], 'activity_type': 'SPRAYING',
        'performed_at': '2026-06-01T09:00:00+03:00',
        'applied_area_decare': '20.0000',
        'reentry_interval_days': 3,
    })
    assert ilac.status_code == 201, ilac.text

    # --- 1) YASAK İÇİNDE GEREKÇESİZ 422 ------------------------------------
    iceride = client.post('/api/field-activities', headers=h, json={
        'season_id': sezon['id'], 'activity_type': 'IRRIGATION',
        'performed_at': '2026-06-03T08:00:00+03:00',
        'applied_area_decare': '5.0000',
    })
    assert iceride.status_code == 422, iceride.text
    detay = iceride.json()['detail']
    assert '2026-06-04' in detay, detay
    assert '3' in detay, detay

    # Sınır günü de engelli: 3 gün BEKLENECEK, 3. gün henüz güvenli değil.
    sinir = client.post('/api/field-activities', headers=h, json={
        'season_id': sezon['id'], 'activity_type': 'IRRIGATION',
        'performed_at': '2026-06-03T23:00:00+03:00',
    })
    assert sinir.status_code == 422, sinir.text

    # --- 2) GÜVENLİ TARİHTE GEÇER ------------------------------------------
    serbest = client.post('/api/field-activities', headers=h, json={
        'season_id': sezon['id'], 'activity_type': 'IRRIGATION',
        'performed_at': '2026-06-04T08:00:00+03:00',
    })
    assert serbest.status_code == 201, serbest.text
    assert serbest.json()['reentry_override_reason'] is None, serbest.json()
    assert serbest.json()['reentry_warning'] is None, serbest.json()

    # --- 3) GEREKÇEYLE GEÇER VE KAYDA YAZILIR ------------------------------
    zorla = client.post('/api/field-activities', headers=h, json={
        'season_id': sezon['id'], 'activity_type': 'FERTILIZING',
        'performed_at': '2026-06-02T08:00:00+03:00',
        'reentry_override_reason': 'Kayıt sonradan girildi',
    })
    assert zorla.status_code == 201, zorla.text
    assert zorla.json()['reentry_override_reason'] == 'Kayıt sonradan girildi', zorla.json()
    assert zorla.json()['reentry_warning'], zorla.json()
    assert 'giriş yasağı' in zorla.json()['reentry_warning'], zorla.json()

    # --- 4) BOŞ SÜRE İHLAL DEĞİL -------------------------------------------
    s2 = client.post('/api/crop-seasons', headers=h, json={
        'parcel_id': parsel['id'], 'season_year': 2025, 'crop': 'Biber',
        'started_on': '2025-03-01'}).json()
    bos = client.post('/api/field-activities', headers=h, json={
        'season_id': s2['id'], 'activity_type': 'SPRAYING',
        'performed_at': '2025-06-01T09:00:00+03:00',
        'applied_area_decare': '10.0000',
    })
    assert bos.status_code == 201, bos.text  # reentry yok; 2026 yasağı 2025'i kesmez

    # --- 5) İSTANBUL / UTC SINIRI ------------------------------------------
    # 4 Haziran 01:30+03 = 3 Haziran 22:30 UTC. UTC günü 3 Haziran olsa
    # yasak sürüyor SANILIRDI; yerel gün 4 Haziran, serbest.
    utc_tuzagi = client.post('/api/field-activities', headers=h, json={
        'season_id': sezon['id'], 'activity_type': 'TILLAGE',
        'performed_at': '2026-06-04T01:30:00+03:00',
    })
    assert utc_tuzagi.status_code == 201, utc_tuzagi.text

    # --- 6) field-safety ile AYNI sınır ------------------------------------
    # Taze ilaçlama: bugün. Raporun safe_from'u 422 metnindeki tarihle aynı olmalı.
    simdi = datetime.now(timezone.utc)
    bugun_lokal = _yerel_gun(simdi)
    taze = client.post('/api/field-activities', headers=h, json={
        'season_id': sezon['id'], 'activity_type': 'SPRAYING',
        'performed_at': simdi.isoformat(),
        'applied_area_decare': '20.0000',
        'reentry_interval_days': 2,
    })
    assert taze.status_code == 201, taze.text
    g = client.get('/api/field-safety', headers=h).json()
    bu_parsel = [b for b in g['reentry_blocks'] if b['parcel_id'] == parsel['id']
                 and b['activity_id'] == taze.json()['id']]
    assert len(bu_parsel) == 1, g['reentry_blocks']
    safe = bu_parsel[0]['safe_from']
    yarin = (bugun_lokal + timedelta(days=1)).isoformat()
    reddet = client.post('/api/field-activities', headers=h, json={
        'season_id': sezon['id'], 'activity_type': 'IRRIGATION',
        'performed_at': datetime(
            bugun_lokal.year, bugun_lokal.month, bugun_lokal.day,
            12, 0, tzinfo=timezone(timedelta(hours=3)),
        ).isoformat(),
    })
    assert reddet.status_code == 422, reddet.text
    assert safe in reddet.json()['detail'], (safe, reddet.json()['detail'])

    # --- 7) block: gerekçe yetmez ------------------------------------------
    kural_yaz(client, h, farm_reentry_policy='block')
    r = client.post('/api/field-activities', headers=h, json={
        'season_id': sezon['id'], 'activity_type': 'IRRIGATION',
        'performed_at': datetime(
            bugun_lokal.year, bugun_lokal.month, bugun_lokal.day,
            13, 0, tzinfo=timezone(timedelta(hours=3)),
        ).isoformat(),
        'reentry_override_reason': 'Gerekçem var',
    })
    assert r.status_code == 422, r.text
    assert 'izin vermiyor' in r.json()['detail'], r.text

    # --- 8) warn: geçer, uyarı yazılır -------------------------------------
    kural_yaz(client, h, farm_reentry_policy='warn')
    r = client.post('/api/field-activities', headers=h, json={
        'season_id': sezon['id'], 'activity_type': 'IRRIGATION',
        'performed_at': datetime(
            bugun_lokal.year, bugun_lokal.month, bugun_lokal.day,
            14, 0, tzinfo=timezone(timedelta(hours=3)),
        ).isoformat(),
    })
    assert r.status_code == 201, r.text
    assert r.json()['reentry_warning'], r.json()
    assert r.json()['reentry_override_reason'] is None, r.json()

    kural_yaz(client, h, farm_reentry_policy='require_reason')

    # --- 9) allow yok ------------------------------------------------------
    kotu = client.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'allow','credit_limit_policy':'allow',
        'farm_reentry_policy':'allow'})
    assert kotu.status_code == 422, kotu.text

    # --- 10) ÇAPRAZ KİRACI -------------------------------------------------
    b = client.post('/api/companies', headers=h, json={'name':'Giriş B'}).json()
    hb = dict(h, **{'X-Company-ID': str(b['id'])})
    gb = client.get('/api/field-safety', headers=hb).json()
    assert gb['reentry_blocks'] == [], gb
    ciftlik_b = client.post('/api/farms', headers=hb, json={'code':'rb','name':'B Çiftlik'}).json()
    parsel_b = client.post('/api/farm-parcels', headers=hb,
                           json={'farm_id':ciftlik_b['id'],'code':'rbp','name':'B Parsel',
                                 'area_decare':'10.0000'}).json()
    sezon_b = client.post('/api/crop-seasons', headers=hb, json={
        'parcel_id': parsel_b['id'], 'season_year': 2026, 'crop': 'Domates'}).json()
    r = client.post('/api/field-activities', headers=hb, json={
        'season_id': sezon_b['id'], 'activity_type': 'IRRIGATION',
        'performed_at': '2026-06-02T08:00:00+03:00',
    })
    assert r.status_code == 201, r.text  # A'nın yasağı B'yi kesmez

    print('TARLA GIRIS YASAGI KILIDI TAMAM')
'''
