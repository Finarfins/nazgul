"""Gerçek Maliyet V1 — FAZ 3 dilim 1: kâra işçilik ve makine giriyor.

Konu: mobil-erp#24.

İşin başladığı şikâyet buydu: *"panoda gösterdiğimiz kâr eksik — tarla brüt
katkısı yalnız girdi maliyetini düşüyor, işçilik ve traktör hesapta yok."*
FAZ 1 oranı kurdu, FAZ 2 satıra dondurdu; bu dilim o iki sütunu kâra bağlıyor.

--- ÇİVİLENEN İKİ KARAR ------------------------------------------------------

**1. Toplam da maskeli (seçenek b).** Saatleri gören biri toplam maliyeti de
görürse bölerek ORANI bulur. `finance` izni olmayan çağıran bu yüzden yalnız
GİRDİ tabanlı toplamı görüyor — yani bugünkü davranışın aynısı — ve
``cost_basis`` hangi tabanın kullanıldığını açıkça söylüyor. Seçenek (a)
(toplamı tümüyle gizlemek) `depo`/`satis`/`rapor` rollerinin BUGÜN gördüğü
veriyi geri alırdı; bu dilimin işi kârı tamamlamak, mevcut ekranı kırmak değil.

Kritik ayrıntı: toplam ayrı bir yetki kontrolüyle değil, **maskeli satırlardan**
hesaplanıyor. `finance` yoksa satırda `labor_cost` zaten yok, dolayısıyla
toplama giremez. Maske ile toplamın tabanı tek kaynaktan gelir; iki karar
ayrışamaz.

**2. Oranı tanımsız faaliyet sessizce atlanmıyor.** Maliyeti BİLİNMEYEN bir
faaliyeti toplamda sıfır saymak, işin başındaki şikâyetin aynısını yeni bir
yerde üretir. Toplam bir TABAN olarak veriliyor ve yanında ``cost_incomplete``
+ ``activities_missing_rate`` taşınıyor.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_season_cost_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_season_cost_with_labour_sqlite(tmp_path: Path) -> None:
    run_season_cost_smoke(f"sqlite:///{(tmp_path / 'season-cost.db').as_posix()}")


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'SeasonCost!2026'
PAROLA = 'FazUcMaliyet!2026'
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


with TestClient(app) as client:
    h = admin_headers(client)
    firmam = int(h['X-Company-ID'])

    from sqlalchemy import text as _sql

    from app.db import SessionLocal

    # İki makine: birinin oranı VAR, digerinin YOK. Oranı olmayan, "maliyeti
    # bilinmiyor" durumunu kurmak icin.
    with SessionLocal() as oturum:
        for marka in ('Oranli', 'Oransiz'):
            oturum.execute(
                _sql('INSERT INTO machines(company_id,brand,model,status,is_active,'
                     "created_at,updated_at) VALUES(:cid,:marka,'T','active',:a,:t,:t)"),
                {'cid': firmam, 'marka': marka, 'a': True,
                 't': '2026-01-01T00:00:00+00:00'},
            )
        oranli = oturum.execute(
            _sql("SELECT id FROM machines WHERE company_id=:c AND brand='Oranli'"),
            {'c': firmam}).scalar_one()
        oransiz = oturum.execute(
            _sql("SELECT id FROM machines WHERE company_id=:c AND brand='Oransiz'"),
            {'c': firmam}).scalar_one()
        oturum.commit()

    ciftlik = client.post('/api/farms', headers=h,
                          json={'code':'f3','name':'FAZ3 Çiftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'f3p','name':'FAZ3 Parsel',
                               'area_decare':'40.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Pamuk',
                              'started_on':'2026-03-01','planted_area_decare':'40.0000'}).json()
    # Sezon PLANNED olarak açiliyor; pano yalniz ACTIVE/HARVESTED sezonlari
    # listeliyor, o yüzden ACTIVE'e alinmali.
    aktif = client.put('/api/crop-seasons/%d' % sezon['id'], headers=h, json={
        'parcel_id':parsel['id'], 'season_year':2026, 'crop':'Pamuk',
        'started_on':'2026-03-01', 'planted_area_decare':'40.0000',
        'status':'ACTIVE', 'expected_updated_at':sezon['updated_at']})
    assert aktif.status_code == 200, aktif.text

    # C: ORANLAR HENUZ TANIMSIZKEN, ayni faaliyette hem isçilik hem makine saati.
    #
    # Oran faaliyete YAZILDIGI ANDA donuyor (FAZ 2), o yuzden "iki bileseni de
    # orani tanimsiz" bir faaliyet ancak oranlar kurulmadan ONCE girilmis olur.
    # Gercek dunyada tam olarak bu oluyor: firma once faaliyet girmeye basliyor,
    # oranlari sonra yapilandiriyor.
    #
    # SAYAC SOZLESMESI: bu TEK faaliyet, iki bileseni de eksik olsa bile
    # `activities_missing_rate`e 1 katkida bulunmali. Sayac eskiden bilesen
    # dongusunun ICINDE artiyordu ve bunu 2 sayiyordu.
    c = client.post('/api/field-activities', headers=h, json={
        'season_id':sezon['id'], 'activity_type':'TILLAGE', 'performed_at':ZAMAN,
        'machine_id':oransiz, 'labor_hours':'3', 'machine_hours':'6'})
    assert c.status_code == 201, c.text
    assert c.json()['labor_cost'] is None, ('C: isçilik oranı tanımsız olmalıydı', c.json())
    assert c.json()['machine_cost'] is None, ('C: makine oranı tanımsız olmalıydı', c.json())

    # Oranlar: isçilik firma geneli 100/saat, YALNIZ `oranli` makineye 250/saat.
    # Firma geneli MACHINE orani BILEREK tanimlanmiyor -> `oransiz` makinenin
    # maliyeti hesaplanamayacak.
    assert client.post('/api/cost-rates', headers=h, json={
        'kind':'LABOR', 'label':'Genel işçilik', 'hourly_rate':'100.00'}).status_code == 201
    assert client.post('/api/cost-rates', headers=h, json={
        'kind':'MACHINE', 'label':'Oranlı traktör', 'hourly_rate':'250.00',
        'machine_id':oranli}).status_code == 201

    # A: girdi 1000 + isçilik 2sa*100=200 + makine 4sa*250=1000
    a = client.post('/api/field-activities', headers=h, json={
        'season_id':sezon['id'], 'activity_type':'TILLAGE', 'performed_at':ZAMAN,
        'machine_id':oranli, 'labor_hours':'2', 'machine_hours':'4',
        'inputs':[{'input_name':'Gübre','quantity':'10','unit':'KG','unit_cost':'100.00'}]})
    assert a.status_code == 201, a.text
    assert a.json()['labor_cost'] == '200.00', a.json()
    assert a.json()['machine_cost'] == '1000.00', a.json()

    # B: orani TANIMSIZ makinede 5 saat -> maliyet BILINMIYOR
    b = client.post('/api/field-activities', headers=h, json={
        'season_id':sezon['id'], 'activity_type':'IRRIGATION', 'performed_at':ZAMAN,
        'machine_id':oransiz, 'machine_hours':'5'})
    assert b.status_code == 201, b.text
    assert b.json()['machine_hourly_rate'] is None, b.json()
    assert b.json()['machine_cost'] is None, b.json()

    # Gelir 5000
    hasat = client.post('/api/field-harvests', headers=h, json={
        'season_id':sezon['id'], 'harvested_on':'2026-09-01', 'quantity':'1000',
        'unit':'KG', 'revenue_amount':'5000.00'})
    assert hasat.status_code == 201, hasat.text

    def sezon_ozeti(headers):
        tl = client.get('/api/farm-parcels/%d/timeline' % parsel['id'], headers=headers)
        assert tl.status_code == 200, tl.text
        s = [x for x in tl.json()['seasons'] if x['id'] == sezon['id']]
        assert len(s) == 1, tl.json()['seasons']
        return s[0]

    def pano_ozeti(headers):
        pano = client.get('/api/field-dashboard', headers=headers)
        assert pano.status_code == 200, pano.text
        s = [x for x in pano.json()['seasons'] if x['season_id'] == sezon['id']]
        assert len(s) == 1, pano.json()['seasons']
        return s[0]

    # --- 1) YETKILI: isçilik + makine KARA giriyor ------------------------
    # girdi 1000 + isçilik 200 + makine 1000 = 2200
    # (B'nin 5 saati ve C'nin 3+6 saati HESAPLANAMADI)
    #
    # PARA ALANLARI BIREBIR METIN OLARAK iddia ediliyor, Decimal() ile DEGIL.
    # Deger karsilastirmasi temsil farkini GORMEZ: ham SUM(NUMERIC) PG'de
    # '5000.00', SQLite'ta '5000' donuyordu ve `Decimal(x) == Decimal(y)` ikisini
    # de esit sayiyordu. Bu kusur tam da bu yuzden PG ikizinin gozunden kacti.
    PARA_ALANLARI = ('total_cost', 'input_cost', 'labor_machine_cost',
                     'revenue_amount', 'gross_margin', 'cost_per_decare')
    BEKLENEN = {'total_cost':'2200.00', 'input_cost':'1000.00',
                'labor_machine_cost':'1200.00', 'revenue_amount':'5000.00',
                'gross_margin':'2800.00', 'cost_per_decare':'55.00'}
    for ad, ozet in (('timeline', sezon_ozeti(h)), ('dashboard', pano_ozeti(h))):
        assert ozet['cost_basis'] == 'inputs+labor+machine', (ad, ozet)
        for alan in PARA_ALANLARI:
            assert ozet[alan] == BEKLENEN[alan], (
                'PARA TEMSILI SAPTI (%s.%s): beklenen %r, gelen %r — diyalektler '
                'arasi bicim farki deger karsilastirmasiyla gorunmez'
                % (ad, alan, BEKLENEN[alan], ozet[alan]))
        # --- 2) ORANI TANIMSIZ FAALIYET SESSIZCE ATLANMIYOR ---------------
        assert ozet['cost_incomplete'] is True, (
            'oranı tanımsız faaliyet var ama toplam TAM gibi sunuluyor: %s %s'
            % (ad, ozet))
        # SAYAC FAALIYET SAYAR, BILESEN DEGIL. Eksik olan iki FAALIYET var:
        # B (yalniz makine saati) ve C (hem isçilik hem makine saati). C tek
        # basina 2 sayilirsa toplam 3 cikar — sayac bilesen dongusunun icinde
        # artiyorsa tam olarak bu olur.
        assert ozet['activities_missing_rate'] == 2, (
            'activities_missing_rate FAALIYET saymali, BILESEN degil. C tek bir '
            'faaliyet ama iki bileseni de eksik; 3 geldiyse sayac bilesen '
            'sayiyor demektir: %s %s' % (ad, ozet))

    # --- 3) FINANCE'SIZ ROL: taban yalniz GIRDI ---------------------------
    yeni = client.post('/api/users', headers=h, json={
        'username':'f3_depo', 'display_name':'FAZ3 Depo', 'password':PAROLA,
        'role':'depo'})
    assert yeni.status_code in (200, 201), yeni.text
    oturum2 = client.post('/api/auth/login',
                          json={'username':'f3_depo','password':PAROLA})
    assert oturum2.status_code == 200, oturum2.text
    dh = {'Authorization':'Bearer '+oturum2.json()['access_token'],
          'X-Company-ID':h['X-Company-ID']}
    cevir = client.post('/api/auth/change-password', headers=dh, json={
        'current_password':PAROLA, 'new_password':PAROLA+'x'})
    assert cevir.status_code == 200, cevir.text
    dh['Authorization'] = 'Bearer '+cevir.json()['access_token']

    FINANSAL = ('input_cost', 'labor_machine_cost', 'cost_incomplete',
                'activities_missing_rate')
    # Maskeli yanitta da para BIREBIR METIN olarak iddia ediliyor.
    MASKELI_BEKLENEN = {'total_cost':'1000.00', 'revenue_amount':'5000.00',
                        'gross_margin':'4000.00', 'cost_per_decare':'25.00'}
    for ad, ozet in (('timeline', sezon_ozeti(dh)), ('dashboard', pano_ozeti(dh))):
        assert ozet['cost_basis'] == 'inputs', (ad, ozet)
        for alan, beklenen in MASKELI_BEKLENEN.items():
            assert ozet[alan] == beklenen, (
                'PARA TEMSILI SAPTI (maskeli %s.%s): beklenen %r, gelen %r'
                % (ad, alan, beklenen, ozet[alan]))
        assert Decimal(ozet['total_cost']) == Decimal('1000.00'), (
            'TOPLAM ORANI SIZDIRDI - isçilik/makine finance izni olmayan role '
            'girdi: %s %s' % (ad, ozet))
        # FINANCE IZNI OLMAYAN ROL `cost_incomplete`/`activities_missing_rate`
        # GORMEZ. Iki sebep: (1) bu alanlar firmanin maliyet YAPILANDIRMASININ
        # durumunu sizdirir; (2) daha kotusu YANLIS BILGI olur — bu rolun
        # toplami zaten girdi-bazli (`cost_basis="inputs"`) ve girdilerin hepsi
        # fiyatli, yani onun gordugu sayi EKSIK DEGIL. Ona `cost_incomplete:
        # True` gostermek yalan olurdu.
        for alan in FINANSAL:
            assert alan not in ozet, (
                'FINANSAL BILESEN SIZDI (%s): %s %s' % (alan, ad, ozet))

    # --- 4) SIZINTI VEKTÖRÜ: saat görünür, oran türetilemez ---------------
    # Saat operasyonel veri, görünür kaliyor. Ama toplam girdi tabanli oldugu
    # icin bölme yoluyla oran BULUNAMAZ.
    tl = client.get('/api/farm-parcels/%d/timeline' % parsel['id'], headers=dh).json()
    faal = [x for x in tl['activities'] if x['id'] == a.json()['id']][0]
    assert faal['labor_hours'] == '2.0000', faal
    assert faal['machine_hours'] == '4.0000', faal
    for alan in ('labor_hourly_rate', 'machine_hourly_rate', 'labor_cost', 'machine_cost'):
        assert alan not in faal, ('FAALIYET SATIRINDA ORAN SIZDI (%s): %s' % (alan, faal))

    # --- 5) İKİ UÇ AYNI SAYIYI VERMELİ ------------------------------------
    # Ayrisalardi kullanici hangi ekrana baktigina göre farkli kâr görürdü.
    #
    # Karsilastirma DEGER üzerinden (Decimal), metin üzerinden degil: iki ucun
    # para BIÇIMLENDIRMESI bu dilimden ÖNCE de ayriydi (timeline '4000.00',
    # pano '4000' — geliri farkli kaynaklardan türetiyorlar). O ayrim bu
    # dilimin isi degil; burada iddia edilen sey ayni SAYIYI vermeleri.
    for headers, etiket in ((h, 'finance'), (dh, 'finance-siz')):
        t, p = sezon_ozeti(headers), pano_ozeti(headers)
        assert Decimal(t['total_cost']) == Decimal(p['total_cost']), (etiket, t, p)
        assert Decimal(t['gross_margin']) == Decimal(p['gross_margin']), (etiket, t, p)
        assert t['cost_basis'] == p['cost_basis'], (etiket, t, p)

    # --- 6) MASKELI YANIT BUGÜNKÜ DAVRANIŞLA BİREBİR AYNI -----------------
    # Seçenek (a) (toplami tümüyle gizlemek) yerine (b) seçildi; o kararin
    # bedeli sifir olmali: finance'siz rolün gördügü toplam, FAZ 3 öncesiyle
    # AYNI deger olmali (girdi tabanli 1000).
    depo_ozet = sezon_ozeti(dh)
    # DEGER degismedi (girdi tabanli 1000); TEMSIL sabit ölçege alindi. Ham
    # SUM(NUMERIC) diyalekte göre '1000' ya da '1000.00' donuyordu — PG ikizi
    # bunu yakaladi ve para sözlesmesi geregi ikisi esitlendi.
    assert depo_ozet['total_cost'] == '1000.00', (
        'maskeli yanitin DEGERI degisti - bu dilim mevcut ekrani kirmamaliydi: %s'
        % depo_ozet)
    assert depo_ozet['cost_per_decare'] == '25.00', depo_ozet

print('OK')
'''
