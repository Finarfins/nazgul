"""Tarlaya giriş yasağı (REI) katalogdan çözülüyor — göç YOK.

ÖLÇÜLEN KUSUR. `create_activity` PHI'yi (hasat bekleme) katalogdan ÇÖZÜYOR
(`_katalog_phi`/`_phi_coz`, göç 0063) ama giriş yasağını ÇÖZMÜYORDU:
`reentry_interval_days` `payload.model_dump()`tan olduğu gibi geçiyordu, yani
operatör yazmadıysa BOŞ kalıyordu. Oysa 0064 o sayının üstüne GERÇEK bir kilit
kurdu (`_giris_ihlalleri`): boş süre kilidi sessizce devre dışı bırakıyor ve
firma kataloğa süreyi girmiş OLSA BİLE tarlaya erken giriliyordu.

Bu dosya beş iddiayı çiviliyor:

1. **Katalog boş bırakılan süreyi ÇÖZER** — asıl kazanç.
2. **Operatörün değeri KAZANIR** — katalog önerir, saha karar verir.
3. **Birden çok girdide EN UZUN yasak kazanır** — kısasını seçmek, uzun
   olanın süresi dolmadan girişe izin verirdi.
4. **Katalog susuyorsa davranış DEĞİŞMEZ** — bu dilim öncesiyle birebir.
5. **Çapraz kiracı: B firması A'nın kataloğundan süre ÇÖZMEZ** — sızıntı
   burada başka firmanın süresini bu firmanın KAYDINA yazardı.

Ayrıca bitkiden bağımsız satıra (`crop=''`) düşme ve bitkiye özel satırın
öncelenmesi ölçülüyor.

KÖKEN SÜTUNU BU DOSYADA YOK — ÇÜNKÜ ŞEMADA YOK. PHI'nin kökeni
`preharvest_source`/`catalogue_preharvest_days` sütunlarında duruyor; giriş
yasağının karşılıkları (`reentry_source`/`catalogue_reentry_days`) DEPODA
BULUNMUYOR ve onları açmak bir GÖÇTÜR. Bu dilim göçsüz olduğu için köken
kaydı BİLEREK kapsam dışında; aşağıdaki iddiaların hiçbiri köken iddia
etmiyor, hepsi ETKİN SÜRE üzerinden ölçülüyor.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_rei_katalog_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_rei_katalog_sqlite(tmp_path: Path) -> None:
    run_rei_katalog_smoke(f"sqlite:///{(tmp_path / 'rei-katalog.db').as_posix()}")


def test_giris_yasagi_cozumu_en_uzunu_secer_ve_operatoru_yenmez() -> None:
    """`_giris_yasagi_coz`un KARARLARI, veritabanına inmeden çivilenir.

    Bu iddialar smoke ile ÖRTÜŞMÜYOR, onu TAMAMLIYOR: smoke uçtan uca ETKİN
    süreyi okuyor, burada karar fonksiyonunun kendisi sahte bir katalogla
    sınanıyor. `max` `min`e çevrilirse ya da operatör dalı kaldırılırsa
    aşağıdakiler DÜŞER.
    """
    sys.path.insert(0, str(BACKEND))
    from app.routers import farm as farm_modulu

    class _SahteGirdi:
        def __init__(self, product_id: int | None) -> None:
            self.product_id = product_id

    class _SahtePayload:
        def __init__(self, inputs, rei) -> None:
            self.inputs = inputs
            self.reentry_interval_days = rei

    katalog: dict[int, int | None] = {1: 2, 2: 9, 3: None}
    cagrilan: list[int] = []

    def _sahte_katalog(db, cid, product_id, bitki):
        cagrilan.append(product_id)
        return katalog[product_id]

    gercek = farm_modulu._katalog_giris_yasagi
    farm_modulu._katalog_giris_yasagi = _sahte_katalog
    try:
        sezon = {"crop": "Domates"}
        # EN UZUN kazanır: `min` olsaydı 2 dönerdi.
        p = _SahtePayload([_SahteGirdi(1), _SahteGirdi(2)], None)
        assert farm_modulu._giris_yasagi_coz(None, 1, p, sezon) == 9

        # Sıra ÖNEMSİZ: uzun olan önce gelse de kazanır.
        p = _SahtePayload([_SahteGirdi(2), _SahteGirdi(1)], None)
        assert farm_modulu._giris_yasagi_coz(None, 1, p, sezon) == 9

        # Operatör yazdıysa katalog ÜSTÜNE YAZAMAZ — hem büyük hem küçük yönde.
        p = _SahtePayload([_SahteGirdi(2)], 3)
        assert farm_modulu._giris_yasagi_coz(None, 1, p, sezon) == 3
        p = _SahtePayload([_SahteGirdi(1)], 30)
        assert farm_modulu._giris_yasagi_coz(None, 1, p, sezon) == 30
        # Operatörün SIFIRI da bir karardır; `if payload.reentry_interval_days:`
        # yazılsaydı sıfır boş sayılır ve katalog üstüne yazardı.
        p = _SahtePayload([_SahteGirdi(2)], 0)
        assert farm_modulu._giris_yasagi_coz(None, 1, p, sezon) == 0

        # Katalog SUSUYORSA (satır var, süre NULL) süre BOŞ kalır.
        p = _SahtePayload([_SahteGirdi(3)], None)
        assert farm_modulu._giris_yasagi_coz(None, 1, p, sezon) is None
        # Susan satır, konuşan satırı da BASTIRMAZ.
        p = _SahtePayload([_SahteGirdi(3), _SahteGirdi(1)], None)
        assert farm_modulu._giris_yasagi_coz(None, 1, p, sezon) == 2

        # Serbest metin girdi (product_id YOK) kataloğa hiç SORULMAZ.
        cagrilan.clear()
        p = _SahtePayload([_SahteGirdi(None)], None)
        assert farm_modulu._giris_yasagi_coz(None, 1, p, sezon) is None
        assert cagrilan == []

        # Girdisiz faaliyet de çözülmez.
        p = _SahtePayload(None, None)
        assert farm_modulu._giris_yasagi_coz(None, 1, p, sezon) is None
    finally:
        farm_modulu._katalog_giris_yasagi = gercek


_SMOKE = r"""
from fastapi.testclient import TestClient
from app.main import app

ADMIN_PW = 'ReiKatalog!12345'


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


def yeni_urun(client, hdr, ad, kod):
    r = client.post('/api/products', headers=hdr,
                    json={'name':ad,'product_code':kod,'sale_price':'250.00',
                          'purchase_price':'150.00','vat_rate':20,'unit':'LT'})
    assert r.status_code == 201, r.text
    return r.json()['id']


def yeni_sezon(client, h, parsel_id, yil, bitki):
    s = client.post('/api/crop-seasons', headers=h,
                    json={'parcel_id':parsel_id,'season_year':yil,'crop':bitki,
                          'started_on':'%d-03-01' % yil})
    assert s.status_code == 201, s.text
    return s.json()


def girdi(urun_id, ad):
    return {'product_id':urun_id,'input_name':ad,'quantity':'10','unit':'LT',
            'dose':'2','dose_unit':'LT/DA'}


def ilacla(client, h, sezon_id, tarih, girdiler, rei=None):
    govde = {'season_id':sezon_id,'activity_type':'SPRAYING',
             'performed_at':tarih,'applied_area_decare':'30.0000',
             'inputs':girdiler}
    if rei is not None:
        govde['reentry_interval_days'] = rei
    r = client.post('/api/field-activities', headers=h, json=govde)
    assert r.status_code == 201, r.text
    return r.json()


with TestClient(app) as client:
    h = admin_headers(client)

    u1 = yeni_urun(client, h, 'REI ILAC 1', 'REI-1')
    u2 = yeni_urun(client, h, 'REI ILAC 2', 'REI-2')
    u3 = yeni_urun(client, h, 'REI ILAC 3 SUSAN', 'REI-3')

    ciftlik = client.post('/api/farms', headers=h,
                          json={'code':'r1','name':'REI Ciftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'rp','name':'REI Parsel',
                               'area_decare':'30.0000'}).json()

    # --- KATALOG ----------------------------------------------------------
    # u1: bitkiden BAGIMSIZ satir (crop='') -> giris yasagi 3 gun.
    r = client.post('/api/plant-protection-products', headers=h,
                    json={'product_id':u1,'preharvest_interval_days':14,
                          'reentry_interval_days':3})
    assert r.status_code == 201, r.text
    assert r.json()['crop'] == '', r.json()
    assert r.json()['reentry_interval_days'] == 3, r.json()

    # u1: bitkiye OZEL satir (Domates) -> 8 gun. Bitkiye ozel olan YENER.
    r = client.post('/api/plant-protection-products', headers=h,
                    json={'product_id':u1,'crop':'Domates',
                          'preharvest_interval_days':21,'reentry_interval_days':8})
    assert r.status_code == 201, r.text

    # u2: bitkiden bagimsiz, 2 gun. `max` yerine `min` olsaydi bu kazanirdi.
    r = client.post('/api/plant-protection-products', headers=h,
                    json={'product_id':u2,'preharvest_interval_days':5,
                          'reentry_interval_days':2})
    assert r.status_code == 201, r.text

    # u3: katalog satiri VAR ama giris yasagi BOS (PHI zorunlu, REI degil).
    r = client.post('/api/plant-protection-products', headers=h,
                    json={'product_id':u3,'preharvest_interval_days':4})
    assert r.status_code == 201, r.text
    assert r.json()['reentry_interval_days'] is None, r.json()

    # --- 1) KATALOG BOS BIRAKILAN SUREYI COZER ---------------------------
    # Bitkiye OZEL satir (8) bitkiden bagimsiz satiri (3) YENER.
    s1 = yeni_sezon(client, h, parsel['id'], 2026, 'Domates')
    a1 = ilacla(client, h, s1['id'], '2026-06-01T09:00:00+03:00',
                [girdi(u1,'REI ILAC 1')])
    assert a1['reentry_interval_days'] == 8, a1

    # --- BITKIYE OZEL SATIR YOKSA GENELE DUSER ---------------------------
    s2 = yeni_sezon(client, h, parsel['id'], 2025, 'Patlican')
    a2 = ilacla(client, h, s2['id'], '2025-06-01T09:00:00+03:00',
                [girdi(u1,'REI ILAC 1')])
    assert a2['reentry_interval_days'] == 3, a2

    # --- 3) BIRDEN COK GIRDI: EN UZUN KAZANIR ----------------------------
    # u1 (Domates: 8) + u2 (2). `min` olsaydi 2 yazilir ve tarlaya 6 gun
    # ERKEN girilebilirdi.
    s3 = yeni_sezon(client, h, parsel['id'], 2024, 'Domates')
    a3 = ilacla(client, h, s3['id'], '2024-06-01T09:00:00+03:00',
                [girdi(u1,'REI ILAC 1'), girdi(u2,'REI ILAC 2')])
    assert a3['reentry_interval_days'] == 8, a3

    # Ters sirada da ayni: siraya bagli bir secim degil.
    s3b = yeni_sezon(client, h, parsel['id'], 2023, 'Domates')
    a3b = ilacla(client, h, s3b['id'], '2023-06-01T09:00:00+03:00',
                 [girdi(u2,'REI ILAC 2'), girdi(u1,'REI ILAC 1')])
    assert a3b['reentry_interval_days'] == 8, a3b

    # Susan satir konusan satiri BASTIRMAZ: u3 (NULL) + u2 (2) -> 2.
    s3c = yeni_sezon(client, h, parsel['id'], 2018, 'Domates')
    a3c = ilacla(client, h, s3c['id'], '2018-06-01T09:00:00+03:00',
                 [girdi(u3,'REI ILAC 3'), girdi(u2,'REI ILAC 2')])
    assert a3c['reentry_interval_days'] == 2, a3c

    # --- 2) OPERATORUN DEGERI KAZANIR ------------------------------------
    # Katalog 8 diyor, operator 30 yaziyor: 30 kalir.
    s4 = yeni_sezon(client, h, parsel['id'], 2022, 'Domates')
    a4 = ilacla(client, h, s4['id'], '2022-06-01T09:00:00+03:00',
                [girdi(u1,'REI ILAC 1')], rei=30)
    assert a4['reentry_interval_days'] == 30, a4

    # Ve KISALTMA yonunde de: katalog 8, operator 1. Katalog USTUNE YAZAMAZ.
    s5 = yeni_sezon(client, h, parsel['id'], 2021, 'Domates')
    a5 = ilacla(client, h, s5['id'], '2021-06-01T09:00:00+03:00',
                [girdi(u1,'REI ILAC 1')], rei=1)
    assert a5['reentry_interval_days'] == 1, a5

    # --- 4) KATALOG SUSUYORSA DAVRANIS DEGISMEZ --------------------------
    # (a) Katalog satiri var ama REI BOS -> sure BOS kalir.
    s6 = yeni_sezon(client, h, parsel['id'], 2020, 'Domates')
    a6 = ilacla(client, h, s6['id'], '2020-06-01T09:00:00+03:00',
                [girdi(u3,'REI ILAC 3')])
    assert a6['reentry_interval_days'] is None, a6

    # (b) Serbest metin girdi (urun bagi YOK) -> sure BOS kalir.
    s7 = yeni_sezon(client, h, parsel['id'], 2019, 'Domates')
    a7 = ilacla(client, h, s7['id'], '2019-06-01T09:00:00+03:00',
                [{'input_name':'KENDI KOMPOSTUM','quantity':'10','unit':'KG',
                  'dose':'2','dose_unit':'KG/DA'}])
    assert a7['reentry_interval_days'] is None, a7

    # PHI'YE DOKUNULMADI: ayni faaliyetlerde katalogtan cozulen PHI yerinde.
    assert a1['preharvest_interval_days'] == 21, a1
    assert a1['preharvest_source'] == 'CATALOGUE', a1
    assert a6['preharvest_interval_days'] == 4, a6
    assert a7['preharvest_interval_days'] is None, a7

    # --- 5) CAPRAZ KIRACI -------------------------------------------------
    b = client.post('/api/companies', headers=h, json={'name':'REI B'}).json()
    hb = dict(h, **{'X-Company-ID': str(b['id'])})

    # B kendi urununu acar ve HIC katalog satiri yazmaz.
    burun = yeni_urun(client, hb, 'B REI URUN', 'B-REI-1')
    bc = client.post('/api/farms', headers=hb,
                     json={'code':'br','name':'B Ciftlik'}).json()
    bp = client.post('/api/farm-parcels', headers=hb,
                     json={'farm_id':bc['id'],'code':'bp','name':'B Parsel',
                           'area_decare':'10.0000'}).json()
    bs = client.post('/api/crop-seasons', headers=hb,
                     json={'parcel_id':bp['id'],'season_year':2026,'crop':'Domates',
                           'started_on':'2026-03-01'}).json()
    bf = client.post('/api/field-activities', headers=hb,
                     json={'season_id':bs['id'],'activity_type':'SPRAYING',
                           'performed_at':'2026-06-01T09:00:00+03:00',
                           'applied_area_decare':'10.0000',
                           'inputs':[{'product_id':burun,'input_name':'B REI URUN',
                                      'quantity':'1','unit':'LT','dose':'1',
                                      'dose_unit':'LT/DA'}]}).json()
    # A'nin katalogundaki 8 gun B'nin kaydina DUSMEDI.
    assert bf['reentry_interval_days'] is None, bf

    # Cozum `company_id`ye bagli. B'nin kendi urun kimligi A'nin u1'inden
    # farkli oldugu icin yukaridaki iddia tek basina yeterli DEGIL: ayni
    # product_id ile cozucuye DOGRUDAN soruluyor. Kiraci suzgeci
    # kaldirilirsa ikinci satir DUSER.
    from datetime import date
    from app.db import SessionLocal
    from app.routers.farm import _giris_ihlalleri, _katalog_giris_yasagi
    acid = int(h['X-Company-ID'])
    with SessionLocal() as db:
        assert _katalog_giris_yasagi(db, acid, u1, 'Domates') == 8
        assert _katalog_giris_yasagi(db, int(b['id']), u1, 'Domates') is None

        # --- COZULEN SURE GERCEK KILIT URETIR ---------------------------
        # `/field-safety` BUGUNE gore hesapliyor; kilidi gecmis bir tarihte
        # olcmek icin `_giris_ihlalleri` DOGRUDAN cagriliyor. a1: 2026-06-01
        # + katalogtan cozulen 8 gun -> pencere [06-01, 06-09).
        # Operator hicbir sure GIRMEDI; kilit yalniz katalogtan geldi.
        pid = int(parsel['id'])
        assert _giris_ihlalleri(db, acid, date(2026, 6, 5), pid), 'kilit uretilmedi'
        assert _giris_ihlalleri(db, acid, date(2026, 6, 9), pid) == []
        # Katalogun sustugu faaliyet (a7, 2019-06-01) ihlal URETMEZ: bos sure
        # ihlal degildir ve kilit sessizce sikilasmadi.
        assert _giris_ihlalleri(db, acid, date(2019, 6, 2), pid) == []
        # B firmasinin parseli A'nin katalogundan hic kilit ALMADI.
        assert _giris_ihlalleri(db, int(b['id']), date(2026, 6, 5), int(bp['id'])) == []

    print('E1A REI KATALOG TAMAM')
"""
