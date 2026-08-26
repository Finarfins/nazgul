"""Gerçek Maliyet V1 — FAZ 1 sözleşmesi: saatlik maliyet oranları.

Konu: mobil-erp#24.

Beş iddia; hepsi sessizce yanlış PARA üretir:

1. **Aynı hedefe iki AKTİF oran olamaz.** İkisi olsaydı hangisinin uygulanacağı
   satır sırasına kalırdı — yani maliyet, kod değişmeden sessizce değişebilirdi.

2. **Oran SIFIR olabilir, negatif olamaz.** Kendi traktörüyle çalışan işletme
   "makine maliyeti 0" diyebilmeli; negatif oran ise toplam maliyeti sessizce
   düşürür.

3. **Bir oran hem makineye hem kişiye bağlanamaz.** Belirsizlik = hangi oranın
   uygulandığını kimsenin bilememesi.

4. **Başka firmanın makinesine oran bağlanamaz.** Veritabanında bileşik FK YOK
   (migration 0052'de gerekçesi yazılı), yani bu kontrol yalnız uygulama
   katmanında var; atlanırsa kiracı sızıntısı olur.

5. **Uç `finance` iznine bağlı.** Oran bir PARA TANIMI; girdi giren depo
   rolünün onu görmesi ya da değiştirmesi gerekmiyor.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_cost_rates_require_finance_permission() -> None:
    sys.path.insert(0, str(BACKEND))
    from app.auth import ROLE_PERMISSIONS, required_permission

    assert required_permission("GET", "/api/cost-rates") == "finance"
    assert required_permission("POST", "/api/cost-rates") == "finance"
    # Tarla/hayvancılık uçları ETKİLENMEMELİ.
    assert required_permission("GET", "/api/farms") == "farm.view"
    assert required_permission("GET", "/api/animals") == "herd.view"
    # Depo rolü oranlara ERİŞEMEZ: girdi girer ama para tanımı yapmaz.
    assert "finance" not in ROLE_PERMISSIONS["depo"]
    assert "finance" not in ROLE_PERMISSIONS["satis"]


def run_cost_rate_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_cost_rates_sqlite(tmp_path: Path) -> None:
    run_cost_rate_smoke(f"sqlite:///{(tmp_path / 'cost-rates.db').as_posix()}")


_SMOKE = r'''
from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'CostRates!2026'


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

    # --- 1) FİRMA GENELİ VARSAYILAN ---------------------------------------
    genel = client.post('/api/cost-rates', headers=h, json={
        'kind':'LABOR', 'label':'Genel işçilik', 'hourly_rate':'150.00'})
    assert genel.status_code == 201, genel.text
    assert genel.json()['machine_id'] is None
    assert genel.json()['user_id'] is None

    # Aynı türde İKİNCİ bir firma geneli REDDEDİLMELİ.
    ikinci = client.post('/api/cost-rates', headers=h, json={
        'kind':'LABOR', 'label':'Başka genel işçilik', 'hourly_rate':'200.00'})
    assert ikinci.status_code == 409, ikinci.text
    assert 'belirsiz' in ikinci.json()['detail'].lower(), ikinci.json()

    # MAKİNE türünde firma geneli AYRI bir hedef: çakışmamalı.
    makine_genel = client.post('/api/cost-rates', headers=h, json={
        'kind':'MACHINE', 'label':'Genel makine', 'hourly_rate':'0'})
    assert makine_genel.status_code == 201, makine_genel.text
    # --- 2) ORAN SIFIR OLABİLİR -------------------------------------------
    assert str(makine_genel.json()['hourly_rate']) in ('0', '0.00', '0E-8'), makine_genel.json()

    # Negatif oran REDDEDİLMELİ.
    negatif = client.post('/api/cost-rates', headers=h, json={
        'kind':'LABOR', 'label':'Eksi', 'hourly_rate':'-1.00'})
    assert negatif.status_code == 422, negatif.text

    # --- 3) HEM MAKİNE HEM KİŞİ OLAMAZ ------------------------------------
    ikili = client.post('/api/cost-rates', headers=h, json={
        'kind':'MACHINE', 'label':'İkisi', 'hourly_rate':'10',
        'machine_id':1, 'user_id':1})
    assert ikili.status_code == 422, ikili.text

    # Tür ile hedef tutarsız olamaz.
    tutarsiz = client.post('/api/cost-rates', headers=h, json={
        'kind':'LABOR', 'label':'Makineli işçilik', 'hourly_rate':'10', 'machine_id':1})
    assert tutarsiz.status_code == 422, tutarsiz.text

    # --- 4) OLMAYAN/BAŞKA FİRMANIN MAKİNESİ -------------------------------
    yok = client.post('/api/cost-rates', headers=h, json={
        'kind':'MACHINE', 'label':'Hayalet traktör', 'hourly_rate':'500',
        'machine_id':999999})
    # 404: kayıt yok. 403 DEĞİL — 403 "var ama sana kapalı" sızdırırdı.
    assert yok.status_code == 404, yok.text

    # Bu firmada olmayan bir KULLANICI da bağlanamaz.
    yok_kullanici = client.post('/api/cost-rates', headers=h, json={
        'kind':'LABOR', 'label':'Hayalet işçi', 'hourly_rate':'100',
        'user_id':999999})
    assert yok_kullanici.status_code == 404, yok_kullanici.text

    # Ama GERÇEK bir kullanıcıya bağlanabilir (admin bu firmanın üyesi).
    # /auth/me kimliği `user` altında döndürüyor (bkz. _session_payload).
    me = client.get('/api/auth/me', headers=h).json()['user']
    kisi = client.post('/api/cost-rates', headers=h, json={
        'kind':'LABOR', 'label':'Admin işçilik', 'hourly_rate':'250.00',
        'user_id':me['id']})
    assert kisi.status_code == 201, kisi.text
    assert kisi.json()['user_id'] == me['id']

    # Aynı kişiye İKİNCİ aktif oran REDDEDİLMELİ.
    kisi2 = client.post('/api/cost-rates', headers=h, json={
        'kind':'LABOR', 'label':'Admin işçilik 2', 'hourly_rate':'300.00',
        'user_id':me['id']})
    assert kisi2.status_code == 409, kisi2.text

    # --- 5) İYİMSER KİLİT --------------------------------------------------
    kayit = genel.json()
    eski = client.put('/api/cost-rates/%d' % kayit['id'], headers=h, json={
        'kind':'LABOR', 'label':'Genel işçilik', 'hourly_rate':'175.00',
        'expected_updated_at':'2020-01-01T00:00:00+00:00'})
    assert eski.status_code == 409, eski.text

    guncel = client.put('/api/cost-rates/%d' % kayit['id'], headers=h, json={
        'kind':'LABOR', 'label':'Genel işçilik', 'hourly_rate':'175.00',
        'expected_updated_at':kayit['updated_at']})
    assert guncel.status_code == 200, guncel.text
    assert str(guncel.json()['hourly_rate']).startswith('175'), guncel.json()

    # --- 6) ARŞİVLENEN ORAN HEDEFİ SERBEST BIRAKIR -------------------------
    # Koşullu tekil indeks yalnız AKTİF satırlara bakıyor: eski oranı arşivleyip
    # yenisini açmak mümkün olmalı, yoksa oran hiç değiştirilemezdi.
    arsiv = client.put('/api/cost-rates/%d' % kisi.json()['id'], headers=h, json={
        'kind':'LABOR', 'label':'Admin işçilik', 'hourly_rate':'250.00',
        'user_id':me['id'], 'status':'ARCHIVED',
        'expected_updated_at':kisi.json()['updated_at']})
    assert arsiv.status_code == 200, arsiv.text
    yeni = client.post('/api/cost-rates', headers=h, json={
        'kind':'LABOR', 'label':'Admin işçilik (yeni)', 'hourly_rate':'320.00',
        'user_id':me['id']})
    assert yeni.status_code == 201, yeni.text

    # --- 7) LİSTE SÜZGECİ --------------------------------------------------
    sadece_makine = client.get('/api/cost-rates', headers=h, params={'kind':'MACHINE'}).json()
    assert sadece_makine['items'], sadece_makine
    assert {i['kind'] for i in sadece_makine['items']} == {'MACHINE'}

    # --- 8) GERÇEK ÇAPRAZ KİRACI: BAŞKA FİRMANIN VAR OLAN MAKİNESİ --------
    # Olmayan bir kimlikle (999999) denemek YETMEZ: kontrol tamamen kaldırılsa
    # bile yabancı anahtar onu reddederdi, yani o test korumayı ölçmüyor.
    # Sızıntının gerçek biçimi "VAR OLAN ama BAŞKA FİRMAYA ait" makinedir;
    # bileşik FK olmadığı için (migration 0052) veritabanı bunu kabul eder ve
    # tek savunma uygulama katmanıdır.
    from sqlalchemy import text as _sql

    from app.db import SessionLocal

    with SessionLocal() as oturum:
        benim_firmam = int(h['X-Company-ID'])
        oturum.execute(
            _sql('INSERT INTO companies(name,is_active,created_at)'
                 ' VALUES(:ad,:aktif,:t)'),
            {'ad': 'Yabanci AS', 'aktif': True, 't': '2026-01-01T00:00:00+00:00'},
        )
        yabanci_firma = oturum.execute(
            _sql("SELECT id FROM companies WHERE name='Yabanci AS'")
        ).scalar_one()
        assert yabanci_firma != benim_firmam
        for cid_, marka in ((benim_firmam, 'Benim'), (yabanci_firma, 'Yabanci')):
            oturum.execute(
                _sql('INSERT INTO machines(company_id,brand,model,status,'
                     'is_active,created_at,updated_at) VALUES(:cid,:marka,'
                     "'Traktor','active',:aktif,:t,:t)"),
                {'cid': cid_, 'marka': marka, 'aktif': True,
                 't': '2026-01-01T00:00:00+00:00'},
            )
        benim_makinem = oturum.execute(
            _sql("SELECT id FROM machines WHERE company_id=:c AND brand='Benim'"),
            {'c': benim_firmam},
        ).scalar_one()
        yabanci_makine = oturum.execute(
            _sql("SELECT id FROM machines WHERE company_id=:c AND brand='Yabanci'"),
            {'c': yabanci_firma},
        ).scalar_one()
        oturum.commit()

    sizinti = client.post('/api/cost-rates', headers=h, json={
        'kind':'MACHINE', 'label':'Baska firmanin traktoru', 'hourly_rate':'900',
        'machine_id':yabanci_makine})
    assert sizinti.status_code == 404, (
        'BASKA FIRMANIN VAR OLAN MAKINESINE oran baglandi - kiraci sizintisi: '
        + sizinti.text)

    # PUT yolu da aynı kontrolden geçmeli: POST kapalı olup PUT açık kalsaydı
    # önce kendi makinene bağlayıp sonra yabancıya çevirmek yeterdi.
    kendi = client.post('/api/cost-rates', headers=h, json={
        'kind':'MACHINE', 'label':'Kendi traktorum', 'hourly_rate':'120',
        'machine_id':benim_makinem})
    assert kendi.status_code == 201, kendi.text
    cevir = client.put('/api/cost-rates/%d' % kendi.json()['id'], headers=h, json={
        'kind':'MACHINE', 'label':'Kendi traktorum', 'hourly_rate':'120',
        'machine_id':yabanci_makine,
        'expected_updated_at':kendi.json()['updated_at']})
    assert cevir.status_code == 404, (
        'PUT ile yabanci makineye cevrildi - kiraci sizintisi: ' + cevir.text)

    # --- 9) İYİMSER KİLİT YARIŞI ------------------------------------------
    # İki istemci AYNI sürümü okur, ikisi de yazar. Sürüm yüklemi UPDATE'in
    # İÇİNDE değilse ikisi de 200 alır ve ilkinin yazdığı sessizce kaybolur.
    # Senaryo 5 bunu yakalayamaz: orada ikinci PUT birincinin ÜRETTİĞİ yeni
    # sürümü kullanıyor, yani yarış hiç kurulmuyor.
    hedef_id = kendi.json()['id']
    ortak = client.get('/api/cost-rates/%d' % hedef_id, headers=h).json()['updated_at']

    ilk = client.put('/api/cost-rates/%d' % hedef_id, headers=h, json={
        'kind':'MACHINE', 'label':'Kendi traktorum', 'hourly_rate':'11.00',
        'machine_id':benim_makinem, 'expected_updated_at':ortak})
    assert ilk.status_code == 200, ilk.text

    ikinci_yazar = client.put('/api/cost-rates/%d' % hedef_id, headers=h, json={
        'kind':'MACHINE', 'label':'Kendi traktorum', 'hourly_rate':'99.00',
        'machine_id':benim_makinem, 'expected_updated_at':ortak})
    assert ikinci_yazar.status_code == 409, (
        'BAYAT surumle yazma kabul edildi - kayip guncelleme: ' + ikinci_yazar.text)

    son = client.get('/api/cost-rates/%d' % hedef_id, headers=h).json()
    assert str(son['hourly_rate']).startswith('11'), (
        'Yarisi kaybeden yazar uzerine yazmis: ' + str(son))

    # --- 10) GERCEK TOCTOU: OKUMA ILE YAZMA ARASINA GIREN COMMIT ----------
    # Senaryo 9 SIRALI oldugu icin Python tarafindaki on kontrol zaten 409
    # veriyor ve UPDATE yuklemine hic sira gelmiyor - yani o senaryo tek
    # basina atomikligi OLCMEZ. Kaybolan-guncelleme hatasi yalniz araya giren
    # yazma, istegin KENDI okuma-yazma araligina dustugunde olusur. Burada tam
    # o ani kuruyoruz: uretimde `_hedef_dogrula`, `_satir()`+surum
    # kontrolunden SONRA ve UPDATE'ten ONCE calisiyor; onu sararak baska bir
    # oturumun tam o araliкta commit etmesini sagliyoruz. Kilit mantigina
    # dokunulmuyor, yalnizca zamanlama sabitleniyor.
    from app.routers import cost_rates as _cr

    _orijinal_hedef = _cr._hedef_dogrula
    _araya = {'sayi': 0}

    def _araya_gir(db, cid, payload):
        _orijinal_hedef(db, cid, payload)
        if _araya['sayi'] == 0:
            _araya['sayi'] = 1
            with SessionLocal() as rakip:
                rakip.execute(
                    _sql('UPDATE cost_rates SET hourly_rate=:r, updated_at=:t'
                         ' WHERE id=:id'),
                    {'r': '777.00', 't': '2030-01-01T00:00:00+00:00',
                     'id': hedef_id},
                )
                rakip.commit()

    _cr._hedef_dogrula = _araya_gir
    try:
        guncel_surum = client.get(
            '/api/cost-rates/%d' % hedef_id, headers=h).json()['updated_at']
        gec_kalan = client.put('/api/cost-rates/%d' % hedef_id, headers=h, json={
            'kind':'MACHINE', 'label':'Kendi traktorum', 'hourly_rate':'55.00',
            'machine_id':benim_makinem, 'expected_updated_at':guncel_surum})
    finally:
        _cr._hedef_dogrula = _orijinal_hedef

    assert _araya['sayi'] == 1, 'araya giren yazma hic calismadi'
    assert gec_kalan.status_code == 409, (
        'OKUMA ILE YAZMA ARASINA giren commit ezildi - KAYIP GUNCELLEME: '
        + gec_kalan.text)
    kalan = client.get('/api/cost-rates/%d' % hedef_id, headers=h).json()
    assert str(kalan['hourly_rate']).startswith('777'), (
        'Araya giren yazarin degeri kayboldu: ' + str(kalan))

    # --- 11) MAKINE KISMI INDEKSI ------------------------------------------
    # Migration 0052 UC kismi tekil indeks kuruyor; firma geneli (senaryo 1) ve
    # kullanici (senaryo 4/6) olculuyordu, MAKINE indeksi hic denenmiyordu.
    # `uq_cost_rates_company_machine` yanlis, kosulsuz ya da etkisiz olsa iki
    # diyalektte de her sey YESIL kalirdi.
    #
    # Senaryonun sonda durmasi bilincli: senaryo 9/10 `hedef_id`yi hala
    # guncelliyor ve burada onu ARSIVLERSEK o senaryolar arsivlenmis bir satir
    # uzerinde calisirdi (PUT status'u ACTIVE'e geri cevirip yeni aktif oranla
    # cakisirdi). Bu blok en sona konarak o etkilesim ortadan kalkiyor.

    # 11a) AYNI MAKINEYE IKINCI AKTIF ORAN OLAMAZ.
    ikinci_makine = client.post('/api/cost-rates', headers=h, json={
        'kind':'MACHINE', 'label':'Ayni traktore ikinci oran',
        'hourly_rate':'60.00', 'machine_id':benim_makinem})
    assert ikinci_makine.status_code == 409, (
        'AYNI MAKINEYE IKINCI AKTIF ORAN KABUL EDILDI - hangi oranin '
        'uygulanacagi satir sirasina kalir, yani maliyet kod hic degismeden '
        'sessizce degisebilir: ' + ikinci_makine.text)

    # 11b) ARSIVLENEN ORAN MAKINEYI SERBEST BIRAKIR.
    # Indeks KISMI (`status='ACTIVE'` kosullu); kosulsuz olsaydi bir makinenin
    # orani BIR KEZ tanimlanip bir daha hic degistirilemezdi.
    mevcut_makine = client.get('/api/cost-rates/%d' % hedef_id, headers=h).json()
    arsivle = client.put('/api/cost-rates/%d' % hedef_id, headers=h, json={
        'kind':'MACHINE', 'label':mevcut_makine['label'],
        'hourly_rate':mevcut_makine['hourly_rate'], 'machine_id':benim_makinem,
        'status':'ARCHIVED',
        'expected_updated_at':mevcut_makine['updated_at']})
    assert arsivle.status_code == 200, arsivle.text
    yeni_makine = client.post('/api/cost-rates', headers=h, json={
        'kind':'MACHINE', 'label':'Traktor yeni oran', 'hourly_rate':'135.00',
        'machine_id':benim_makinem})
    assert yeni_makine.status_code == 201, (
        'ARSIVLENEN ORAN MAKINEYI SERBEST BIRAKMADI - indeks kismi degil, yani '
        'bir makinenin orani bir daha hic degistirilemez: ' + yeni_makine.text)
    assert yeni_makine.json()['machine_id'] == benim_makinem, yeni_makine.json()

print('OK')
'''
