"""Hayvancılık V1 — FAZ 2 sözleşmesi: CRUD API ve sürü panosu.

Konu: mobil-erp#17.

Testlerin ağırlığı BEŞ iddiada. Beşi de sessizce yanlış olabilecek türden:

1. **Küpe numarası ENGELLEMEZ, UYARIR.** Beklenmeyen biçimde bir küpe KAYIT
   EDİLİR ve yanıtta `warnings` döner. Reddetseydik, 2026'da değişen küpe
   standardı yüzünden GEÇERLİ küpeler girilemezdi.

2. **Ama aynı küpe iki AKTİF hayvana verilemez.** Uyarmak gevşeklik değil:
   kimlik çakışması hâlâ reddediliyor. İkisi ayrı şeyler.

3. **Hareket, hayvanın durumunu DEĞİŞTİRİR.** Satış kaydedilip hayvan aktif
   kalsaydı "kaç hayvanım var" sorusu satılmışları da sayardı — ve pano bunu
   göstermezdi.

4. **Doğum yavruyu OLUŞTURUR, aynı işlemde.** Ölü doğumda yavru kaydı
   AÇILMAZ; açılsaydı sürüde olmayan bir hayvan görünürdü.

5. **Sürüde bireysel kayıt varsa elle baş sayısı girilemez.** İkisi birden
   dolu olsaydı aynı hayvanlar iki kez sayılırdı.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_ear_tag_warning_never_raises() -> None:
    """Küpe uyarısı bir HATA değil, bir METİNDİR.

    Fonksiyon hiçbir girdide istisna fırlatmamalı: fırlatsaydı uç onu 422'ye
    çevirir ve "uyarı" fiilen "ret" olurdu.
    """
    sys.path.insert(0, str(BACKEND))
    from app.herd_schemas import kupe_uyarisi

    assert kupe_uyarisi("TR1234567890") is None
    assert kupe_uyarisi(None) is None
    assert kupe_uyarisi("   ") is None
    for kotu in ("ABC", "tr123", "TR-123", "12345", "TR" + "9" * 40):
        uyari = kupe_uyarisi(kotu)
        assert isinstance(uyari, str) and uyari, kotu
        # Mesaj NE OLDUĞUNU söylemeli ve kaydın yapıldığını belirtmeli.
        assert "Kayıt yapıldı" in uyari, uyari


def test_faz2_has_no_unbacked_idempotency_field() -> None:
    """FAZ 2 şemaları `operation_id` KABUL ETMEZ.

    Defteri (tekrarı yakalayan tablo) olmadan bu alanı kabul etmek, istemciye
    "tekrar gönderim güvenli" demek olurdu; oysa hiçbir şey onu yakalamıyor ve
    mükerrer aşı/doğum kaydı sessizce oluşurdu.
    """
    sys.path.insert(0, str(BACKEND))
    from app import herd_schemas

    for ad in ("AnimalWrite", "VaccinationWrite", "BirthWrite", "WeightWrite",
               "MilkYieldWrite", "MovementWrite", "BreedingWrite"):
        sema = getattr(herd_schemas, ad)
        assert "operation_id" not in sema.model_fields, ad
        assert sema.model_config.get("extra") == "forbid", ad


def run_herd_api_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_herd_api_sqlite(tmp_path: Path) -> None:
    run_herd_api_smoke(f"sqlite:///{(tmp_path / 'herd-api.db').as_posix()}")


_SMOKE = r'''
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'HerdApi!123456'


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


def ayni_saniyede_farkli_zaman(timestamp):
    onceki = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    mikrosaniye = (onceki.microsecond + 1) % 1_000_000
    return onceki.replace(microsecond=mikrosaniye)


with TestClient(app) as client:
    h = admin_headers(client)

    grup = client.post('/api/animal-groups', headers=h,
                       json={'code':'ahir1','name':'Ahır 1','species':'CATTLE'})
    assert grup.status_code == 201, grup.text
    grup = grup.json()
    assert grup['code'] == 'AHIR1', grup

    # --- CAS: AYNI SANİYEDEKİ BAYAT SÜRÜ YAZISI REDDEDİLİR -----------------
    grup_ilk_surum = grup['updated_at']
    grup_yeni_zaman = ayni_saniyede_farkli_zaman(grup_ilk_surum)
    with patch('app.routers.herd._simdi', return_value=grup_yeni_zaman):
        ilk_yazi = client.put(f"/api/animal-groups/{grup['id']}", headers=h, json={
            'code':'AHIR1','name':'Ahır 1 güncel','species':'CATTLE','status':'ACTIVE',
            'expected_updated_at':grup_ilk_surum})
        assert ilk_yazi.status_code == 200, ilk_yazi.text
        bayat_yazi = client.put(f"/api/animal-groups/{grup['id']}", headers=h, json={
            'code':'AHIR1','name':'Bayat yazı','species':'CATTLE','status':'ACTIVE',
            'expected_updated_at':grup_ilk_surum})
        assert bayat_yazi.status_code == 409, bayat_yazi.text
    grup = ilk_yazi.json()
    assert int(datetime.fromisoformat(grup_ilk_surum.replace('Z', '+00:00')).timestamp()) == int(
        datetime.fromisoformat(grup['updated_at'].replace('Z', '+00:00')).timestamp())

    # Elle baş sayılı sürüye hayvan doğrudan OLUŞTURULAMAZ.
    manuel = client.post('/api/animal-groups', headers=h, json={
        'code':'MANUEL','name':'Manuel sayılan sürü','species':'CATTLE','head_count':25})
    assert manuel.status_code == 201, manuel.text
    manuel = manuel.json()
    cifte_olusturma = client.post('/api/animals', headers=h, json={
        'ear_tag':'TR5555555555','species':'CATTLE','sex':'FEMALE',
        'group_id':manuel['id']})
    assert cifte_olusturma.status_code == 422, cifte_olusturma.text
    assert 'iki kez sayılır' in cifte_olusturma.json()['detail'], cifte_olusturma.text

    # --- 1) KÜPE UYARIR, ENGELLEMEZ ---------------------------------------
    tuhaf = client.post('/api/animals', headers=h,
                        json={'ear_tag':'ESKI-KUPE-7','species':'CATTLE','sex':'FEMALE',
                              'birth_date':'2022-03-01','group_id':grup['id']})
    assert tuhaf.status_code == 201, ('küpe biçimi REDDEDİLMEMELİ', tuhaf.text)
    assert tuhaf.json()['warnings'], tuhaf.json()
    assert 'Kayıt yapıldı' in tuhaf.json()['warnings'][0], tuhaf.json()

    duzgun = client.post('/api/animals', headers=h,
                         json={'ear_tag':'TR1234567890','species':'CATTLE','sex':'FEMALE',
                               'birth_date':'2021-05-10','group_id':grup['id']})
    assert duzgun.status_code == 201, duzgun.text
    assert duzgun.json()['warnings'] == [], duzgun.json()
    anne = duzgun.json()

    # Hayvan güncellemesi de saniye yuvarlamaz ve CAS ile bayat yazıyı keser.
    anne_ilk_surum = anne['updated_at']
    with patch('app.routers.herd._simdi',
               return_value=ayni_saniyede_farkli_zaman(anne_ilk_surum)):
        anne_guncel = client.put(f"/api/animals/{anne['id']}", headers=h, json={
            'ear_tag':anne['ear_tag'],'name':'Anaç','species':'CATTLE','sex':'FEMALE',
            'birth_date':'2021-05-10','group_id':grup['id'],'status':'ACTIVE',
            'expected_updated_at':anne_ilk_surum})
        assert anne_guncel.status_code == 200, anne_guncel.text
        anne_bayat = client.put(f"/api/animals/{anne['id']}", headers=h, json={
            'ear_tag':anne['ear_tag'],'name':'Bayat','species':'CATTLE','sex':'FEMALE',
            'birth_date':'2021-05-10','group_id':grup['id'],'status':'ACTIVE',
            'expected_updated_at':anne_ilk_surum})
        assert anne_bayat.status_code == 409, anne_bayat.text
    anne = anne_guncel.json()

    # --- 2) AMA AYNI KÜPE İKİ AKTİF HAYVANDA OLAMAZ ------------------------
    cakisma = client.post('/api/animals', headers=h,
                          json={'ear_tag':'TR1234567890','species':'CATTLE','sex':'MALE'})
    assert cakisma.status_code == 409, cakisma.text
    assert 'aktif bir hayvanda' in cakisma.json()['detail'], cakisma.text

    # --- 3) YAŞ SUNUCUDA TÜRETİLİR ----------------------------------------
    detay = client.get(f"/api/animals/{anne['id']}", headers=h).json()
    assert detay['age_days'] and detay['age_days'] > 1000, detay

    # --- 4) TOHUMLAMA YALNIZ DİŞİYE ---------------------------------------
    boga = client.post('/api/animals', headers=h,
                       json={'ear_tag':'TR9999999999','species':'CATTLE','sex':'MALE',
                              'birth_date':'2020-01-01'}).json()
    # Sonradan atama da elle baş sayılı sürüye bireysel hayvan sokamaz.
    cifte_atama = client.put(f"/api/animals/{boga['id']}", headers=h, json={
        'ear_tag':boga['ear_tag'],'species':'CATTLE','sex':'MALE',
        'birth_date':'2020-01-01','group_id':manuel['id'],'status':'ACTIVE',
        'expected_updated_at':boga['updated_at']})
    assert cifte_atama.status_code == 422, cifte_atama.text
    assert 'iki kez sayılır' in cifte_atama.json()['detail'], cifte_atama.text
    yanlis = client.post('/api/animal-breedings', headers=h,
                         json={'animal_id':boga['id'],'bred_on':'2026-01-10'})
    assert yanlis.status_code == 422, yanlis.text

    toh = client.post('/api/animal-breedings', headers=h,
                      json={'animal_id':anne['id'],'bred_on':'2026-01-10','method':'AI',
                            'sire_code':'BOGA-77'})
    assert toh.status_code == 201, toh.text
    toh = toh.json()
    assert toh['result'] == 'UNKNOWN', toh

    # Tohumlama güncellemesi de aynı tam-hassasiyetli CAS sözleşmesinde.
    toh_ilk_surum = toh['updated_at']
    with patch('app.routers.herd._simdi',
               return_value=ayni_saniyede_farkli_zaman(toh_ilk_surum)):
        toh_guncel = client.put(f"/api/animal-breedings/{toh['id']}", headers=h, json={
            'animal_id':anne['id'],'bred_on':'2026-01-10','method':'AI',
            'sire_code':'BOGA-77','result':'PREGNANT',
            'expected_updated_at':toh_ilk_surum})
        assert toh_guncel.status_code == 200, toh_guncel.text
        toh_bayat = client.put(f"/api/animal-breedings/{toh['id']}", headers=h, json={
            'animal_id':anne['id'],'bred_on':'2026-01-10','method':'AI',
            'sire_code':'BOGA-88','result':'NOT_PREGNANT',
            'expected_updated_at':toh_ilk_surum})
        assert toh_bayat.status_code == 409, toh_bayat.text
    toh = toh_guncel.json()

    # --- 5) DOĞUM YAVRUYU OLUŞTURUR, AYNI İŞLEMDE -------------------------
    once = client.get('/api/animals', headers=h).json()['total']
    dogum = client.post('/api/animal-births', headers=h,
                        json={'mother_id':anne['id'],'breeding_id':toh['id'],
                              'birth_date':'2026-10-20','outcome':'LIVE','difficulty':1,
                              'offspring':[{'ear_tag':'TR1111111111','sex':'FEMALE'}]})
    assert dogum.status_code == 201, dogum.text
    assert len(dogum.json()['offspring_ids']) == 1, dogum.json()
    assert client.get('/api/animals', headers=h).json()['total'] == once + 1
    yavru = client.get(f"/api/animals/{dogum.json()['offspring_ids'][0]}", headers=h).json()
    # Yavru anneye bağlı, aynı türde ve annenin sürüsünde.
    assert yavru['mother_id'] == anne['id'], yavru
    assert yavru['species'] == 'CATTLE' and yavru['group_id'] == grup['id'], yavru

    # Doğum tarihi anneden ÖNCE olamaz.
    erken = client.post('/api/animal-births', headers=h,
                        json={'mother_id':anne['id'],'birth_date':'2019-01-01'})
    assert erken.status_code == 422, erken.text

    # ÖLÜ DOĞUMDA YAVRU KAYDI AÇILMAZ.
    olu = client.post('/api/animal-births', headers=h,
                      json={'mother_id':anne['id'],'birth_date':'2026-11-01',
                            'outcome':'STILLBORN',
                            'offspring':[{'ear_tag':'TR2222222222','sex':'MALE'}]})
    assert olu.status_code == 422, olu.text

    # --- 6) SÜRÜDE BİREYSEL KAYIT VARSA ELLE BAŞ SAYISI GİRİLEMEZ ---------
    g = client.get(f"/api/animal-groups/{grup['id']}", headers=h).json()
    cifte = client.put(f"/api/animal-groups/{grup['id']}", headers=h,
                       json={'code':'AHIR1','name':'Ahır 1','species':'CATTLE',
                             'head_count':50,'status':'ACTIVE',
                             'expected_updated_at':g['updated_at']})
    assert cifte.status_code == 422, cifte.text
    assert 'iki kez sayılır' in cifte.json()['detail'], cifte.text

    # --- 7) SÜT: HAYVAN YA DA GRUP ----------------------------------------
    ikisi = client.post('/api/milk-yields', headers=h,
                        json={'animal_id':anne['id'],'group_id':grup['id'],
                              'milked_on':'2026-08-01','quantity_liters':'20'})
    assert ikisi.status_code == 422, ikisi.text
    hicbiri = client.post('/api/milk-yields', headers=h,
                          json={'milked_on':'2026-08-01','quantity_liters':'20'})
    assert hicbiri.status_code == 422, hicbiri.text
    tamam = client.post('/api/milk-yields', headers=h,
                        json={'animal_id':anne['id'],'milked_on':'2026-08-01',
                              'session':'SABAH','quantity_liters':'22.5'})
    assert tamam.status_code == 201, tamam.text

    # --- 8) PANO ----------------------------------------------------------
    pano = client.get('/api/herd-dashboard', headers=h)
    assert pano.status_code == 200, pano.text
    p = pano.json()
    assert p['summary']['individual_active'] == 4, p   # 2 dişi + boğa + yavru
    assert p['by_species']['CATTLE']['female'] == 3, p
    assert p['by_species']['CATTLE']['male'] == 1, p

    # --- 9) HAREKET HAYVANIN DURUMUNU DEĞİŞTİRİR --------------------------
    satis = client.post('/api/animal-movements', headers=h,
                        json={'animal_id':boga['id'],'kind':'SALE','moved_on':'2026-08-05',
                              'amount':'45000.00','counterparty':'Mehmet Bey'})
    assert satis.status_code == 201, satis.text
    assert satis.json()['animal']['status'] == 'SOLD', satis.json()

    p = client.get('/api/herd-dashboard', headers=h).json()
    assert p['summary']['individual_active'] == 3, ('satılan hayvan sayılmamalı', p)

    # Satılan hayvanın küpesi ARTIK yeniden kullanılabilir.
    yeni = client.post('/api/animals', headers=h,
                       json={'ear_tag':'TR9999999999','species':'CATTLE','sex':'MALE'})
    assert yeni.status_code == 201, ('satılanın küpesi tekrar kullanılabilmeli', yeni.text)

    # --- 10) ÇAPRAZ KİRACI 404 --------------------------------------------
    b = client.post('/api/companies', headers=h, json={'name':'Hayvan B Firması'}).json()
    hb = dict(h, **{'X-Company-ID': str(b['id'])})
    for yol in (f"/api/animals/{anne['id']}", f"/api/animal-groups/{grup['id']}",
                f"/api/animal-breedings/{toh['id']}"):
        assert client.get(yol, headers=hb).status_code == 404, yol
    # B firması A firmasının sürüsüne hayvan bağlayamaz.
    assert client.post('/api/animals', headers=hb,
                       json={'species':'GOAT','sex':'FEMALE',
                             'group_id':grup['id']}).status_code == 404

    print('HAYVANCILIK FAZ-2 SOZLESMESI TAMAM')
'''
