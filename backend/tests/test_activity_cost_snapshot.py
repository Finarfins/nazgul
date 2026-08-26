"""Gerçek Maliyet V1 — FAZ 2 sözleşmesi: oran faaliyet satırına KOPYALANIR.

Konu: mobil-erp#24.

--- BU DOSYANIN ASIL İDDİASI ------------------------------------------------

**ORANI DEĞİŞTİRMEK GEÇMİŞ MALİYETİ DEĞİŞTİRMEZ.** (Senaryo 3.)

`cost_rates` geçmiş tutmuyor, yalnız bugünkü varsayılanı tutuyor (FAZ 1,
migration 0052). Okuma yolu maliyeti o tablodan türetseydi, bu yıl oranı
güncellemek GEÇEN SEZONUN kârını da değiştirirdi ve hiçbir rapora
güvenilemezdi. Bu yüzden oran kayıt anında satıra donuyor ve okuma yolu
`cost_rates`e BİR DAHA BAKMIYOR.

Diğer senaryolar bu iddianın vakumda geçmemesi için var:

* Senaryo 2, çözümün GERÇEKTEN çalıştığını gösteriyor. Olmasaydı "oran hiç
  kopyalanmıyor" hâli de senaryo 3'ü geçerdi (boş, hep boş kalır).
* Senaryo 4, dondurmanın YENİ kayıtları dondurmadığını gösteriyor: oran
  değiştikten SONRA açılan kayıt yeni oranı almalı. Olmasaydı "kod tabloyu hiç
  okumuyor" hâli de geçerdi.
* Senaryo 7, oranın FİRMAYA BAĞLI çözüldüğünü ölçüyor. `cost_rates` ile
  `machines` arasında bileşik yabancı anahtar yok (migration 0052), yani
  kiracı sınırını yalnız sorgunun kendi `company_id` yüklemi tutuyor.
* Senaryo 8, paranın telden STRING çıktığını çiviliyor. FAZ 1'de tam bu sapma
  CI'da yaşandı: aynı uç PostgreSQL'de float, SQLite'ta Decimal döndürüyordu.

Dosyadaki üç statik kapı ayrı bir işe bakıyor: şemanın saatsiz oranı reddetmesi,
faaliyetin GÜNCELLEME ucunun olmaması (kopya tek yerde yazılıyor) ve oranı
satıra yazabilen her rolün oran tablosunu okuyabiliyor da olması.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_cost_snapshot_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_activity_cost_snapshot_sqlite(tmp_path: Path) -> None:
    run_cost_snapshot_smoke(f"sqlite:///{(tmp_path / 'cost-snapshot.db').as_posix()}")


def test_activity_write_schema_rejects_a_rate_without_hours() -> None:
    """Saatsiz oran satırda anlamsız kalırdı; şema onu reddetmeli."""
    sys.path.insert(0, str(BACKEND))
    import pytest
    from pydantic import ValidationError

    from app.farm_schemas import ActivityWrite

    taban = {
        "season_id": 1,
        "activity_type": "TILLAGE",
        "performed_at": "2026-05-10T09:00:00+03:00",
    }
    with pytest.raises(ValidationError):
        ActivityWrite(**taban, labor_hourly_rate="100.00")
    with pytest.raises(ValidationError):
        ActivityWrite(**taban, machine_hourly_rate="100.00")
    # Saatle birlikte KABUL edilmeli — kural "oran yasak" değil.
    assert ActivityWrite(
        **taban, labor_hours="2", labor_hourly_rate="100.00"
    ).labor_hourly_rate is not None


def test_explicit_rate_write_requires_the_rate_read_permission() -> None:
    """Oranı SATIRA YAZABİLEN her rol, oran tablosunu OKUYABİLİYOR da olmalı.

    --- BUGÜN BİR AÇIK YOK, YARIN OLABİLİR -------------------------------

    İki izin ayrı kaynaktan geliyor:

    * ``POST /api/field-activities`` → ``farm.manage`` (faaliyet yazma),
    * ``GET /api/cost-rates`` → ``finance`` (FAZ 1'in bilinçli kararı: "oran
      bir para tanımı ve OKUNMASI da firmanın maliyet yapısını açar").

    ``ActivityWrite`` açıkça gönderilen ``labor_hourly_rate`` /
    ``machine_hourly_rate`` alanlarını kabul ediyor ve o durumda
    ``_oran_kopyala`` ``cost_rates``e hiç bakmıyor. Yani ``farm.manage``
    taşıyan biri, oran tablosunu hiç göremeden satıra istediği oranı
    yazabilir.

    Bugün çakışma YOK: ``farm.manage`` yalnız ``yonetici``de ve o rol zaten
    ``finance`` taşıyor (``admin`` ``*`` ile hepsini taşıyor). Ama
    ``farm.view`` / ``farm.manage`` / ``farm.inputs`` ayrımının varlık sebebi
    ileride ``finance`` taşımayan bir saha rolüne ``farm.manage``
    verebilmek. O rol eklendiği gün açık SESSİZCE oluşurdu.

    Bu yüzden kural koda değil KAPIYA yazıldı. Rol→izin haritası gerçek
    kaynaktan (``app.auth.ROLE_PERMISSIONS``) okunuyor ve ``*`` genişletmesi
    dahil karar üretimin kendi yüklemi (``has_permission``) ile veriliyor;
    sabit bir rol listesi kopyalansaydı kapı, listeyi güncellemeyi unutan ilk
    değişiklikte sessizce boşa düşerdi.
    """
    sys.path.insert(0, str(BACKEND))
    from app.auth import ROLE_PERMISSIONS, has_permission, required_permission
    from app.farm_schemas import ActivityWrite

    acik_oran_alanlari = {"labor_hourly_rate", "machine_hourly_rate"} & set(
        ActivityWrite.model_fields
    )
    if not acik_oran_alanlari:
        # Açık oran alanı KALDIRILMIŞSA bağ da gerekmiyor: oran yalnız
        # sunucunun çözdüğü değer olur ve yazan kişi bir sayı seçemez. Kapı
        # sessizce geçmesin diye durum burada açıkça kaydediliyor.
        assert not acik_oran_alanlari
        return

    # İzin adları SABİT YAZILMIYOR, rotanın kendi kararından okunuyor: yarın
    # faaliyet yazma başka bir izne taşınırsa kapı yeni izni takip etsin.
    yazma_izni = required_permission("POST", "/api/field-activities")
    okuma_izni = required_permission("GET", "/api/cost-rates")
    assert yazma_izni and okuma_izni and yazma_izni != okuma_izni, (
        yazma_izni, okuma_izni,
    )

    ihlal = sorted(
        rol
        for rol in ROLE_PERMISSIONS
        if has_permission(rol, yazma_izni) and not has_permission(rol, okuma_izni)
    )
    assert not ihlal, (
        f"{ihlal} rolleri faaliyet satırına oran YAZABİLİYOR ({yazma_izni}) ama "
        f"oran tablosunu OKUYAMIYOR ({okuma_izni}). ActivityWrite açık oran "
        f"alanlarını ({sorted(acik_oran_alanlari)}) kabul ettiği sürece bu bir "
        "yetki açığıdır: oranı göremeyen biri geçmişe donacak oranı belirler. "
        f"Ya role {okuma_izni} verin, ya açık oran alanlarını kaldırın."
    )


def _sahte_istek(rol: str):
    """`request.state.user` taşıyan en küçük nesne — maske yalnız buna bakıyor."""
    from types import SimpleNamespace

    return SimpleNamespace(state=SimpleNamespace(user={"id": 1, "role": rol}))


def test_replay_response_is_masked_for_a_caller_without_the_rate_permission() -> None:
    """Tekrar-gönderim yanıtı da maskeli çıkmalı.

    --- NEDEN BU TEK YOL HTTP İLE ÖLÇÜLMÜYOR: DÜRÜST GEREKÇE -------------

    Tekrar-gönderim yanıtı YALNIZ POST'tan döner (``_tekrar_mi`` ve yarış
    kaybedildiğinde ``_kuyruga_isle``). ``POST /api/field-activities``
    ``farm.manage`` istiyor ve bu dosyadaki yetki kapısı
    (``test_explicit_rate_write_requires_the_rate_read_permission``) zaten
    ``farm.manage`` taşıyan HER rolün ``finance`` de taşımasını zorunlu
    kılıyor. Yani BUGÜN bu yolu maskesiz görebilecek bir rol ÜRETİLEMEZ —
    HTTP ile ölçmek için önce o kapıyı ihlal eden bir rol uydurmak gerekirdi.

    Maske yine de bu yola uygulandı ve burada doğrudan ölçülüyor: kapı
    ileride gevşetilirse (ya da açık oran alanları kaldırılıp kapı
    kendiliğinden düşerse) bu yol maskesiz kalmasın. Derinlemesine savunma;
    bugün erişilebilir bir açık DEĞİL.
    """
    sys.path.insert(0, str(BACKEND))
    from app.routers.farm import _kuyruk_satiri

    satir = {
        "id": 7, "season_id": 1, "labor_hours": "2.0000",
        "labor_hourly_rate": "100.00", "machine_hours": None,
        "machine_hourly_rate": None,
    }
    maskeli = ("labor_hourly_rate", "machine_hourly_rate", "labor_cost", "machine_cost")

    class _SahteDb:
        """`_satir`ın ihtiyaç duyduğu en küçük yüzey: execute→mappings→first."""

        def __init__(self, kayit): self._kayit = kayit
        def execute(self, *_a, **_k): return self
        def mappings(self): return self
        def first(self): return self._kayit

    def _oku(rol):
        return _kuyruk_satiri(_SahteDb(dict(satir)), 1, "activity", 7, _sahte_istek(rol))

    yetkili = _oku("yonetici")
    assert yetkili["labor_hourly_rate"] == "100.00", yetkili
    assert yetkili["labor_cost"] == "200.00", yetkili

    yetkisiz = _oku("depo")
    for alan in maskeli:
        assert alan not in yetkisiz, (alan, yetkisiz)
    # Saat operasyonel veri; kalmalı.
    assert yetkisiz["labor_hours"] == "2.0000", yetkisiz


def test_activity_snapshot_columns_are_written_only_on_create() -> None:
    """Faaliyetin GÜNCELLEME ucu yok; kopya tek bir yerde yazılıyor.

    Bir PUT açılırsa oranın orada yeniden çözülmemesi gerekir (bkz.
    ``work_order_labor_lines.update_labor_line``: "never silently refreshed").
    Bu iddia o günü yakalasın diye statik: yeni bir yazma yolu eklendiğinde
    kırılır ve dondurma kuralının yeniden düşünülmesini zorlar.
    """
    sys.path.insert(0, str(BACKEND))
    from app.main import app

    # Rota envanteri OpenAPI'den okunuyor — deponun kendi rota sözleşmesi kapısı
    # (`test_route_security_contracts`) da aynı kaynağı kullanıyor.
    faaliyet_yollari = {
        (yol, yontem.upper())
        for yol, kalem in app.openapi()["paths"].items()
        for yontem in kalem
        if yol.startswith("/api/field-activities")
    }
    assert faaliyet_yollari == {
        ("/api/field-activities", "GET"),
        ("/api/field-activities", "POST"),
        ("/api/field-activities/{activity_id}", "GET"),
        ("/api/field-activities/{activity_id}/inputs", "POST"),
    }, faaliyet_yollari


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app

ADMIN_PW = 'CostSnapshot!2026'
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
    ben = client.get('/api/auth/me', headers=h).json()['user']

    ciftlik = client.post('/api/farms', headers=h,
                          json={'code':'m1','name':'Maliyet Çiftliği'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'mp','name':'Maliyet Parseli',
                               'area_decare':'40.0000'}).json()
    sezon = client.post('/api/crop-seasons', headers=h,
                        json={'parcel_id':parsel['id'],'season_year':2026,'crop':'Buğday',
                              'started_on':'2026-03-01','planted_area_decare':'40.0000'}).json()

    TABAN = {'season_id':sezon['id'],'activity_type':'TILLAGE','performed_at':ZAMAN}

    # Kendi firmamıza ait bir makine (oranın makineye bağlanabilmesi için).
    from sqlalchemy import text as _sql

    from app.db import SessionLocal

    with SessionLocal() as oturum:
        oturum.execute(
            _sql('INSERT INTO machines(company_id,brand,model,status,is_active,'
                 "created_at,updated_at) VALUES(:cid,'Traktor','T480','active',"
                 ':aktif,:t,:t)'),
            {'cid': firmam, 'aktif': True, 't': '2026-01-01T00:00:00+00:00'},
        )
        traktor = oturum.execute(
            _sql("SELECT id FROM machines WHERE company_id=:c AND brand='Traktor'"),
            {'c': firmam},
        ).scalar_one()
        oturum.commit()

    # --- 1) ORAN TANIMSIZKEN: MALİYET "BİLİNMİYOR", SIFIR DEĞİL -----------
    # Sıfır yazmak "bu iş bedava yapıldı" demek olurdu; doğrusu ölçülmemiş
    # olduğunu söylemek. Sezon maliyeti sessizce eksik çıkmasın diye ayrım şart.
    oransiz = client.post('/api/field-activities', headers=h,
                          json={**TABAN, 'labor_hours':'3'})
    assert oransiz.status_code == 201, oransiz.text
    assert oransiz.json()['labor_hourly_rate'] is None, oransiz.json()
    assert oransiz.json()['labor_cost'] is None, (
        'Tanimsiz oran SIFIR maliyet olarak yazildi: ' + oransiz.text)
    assert oransiz.json()['labor_hours'] == '3.0000', oransiz.json()

    # --- 2) ORAN TANIMLIYKEN: SATIRA KOPYALANIR ---------------------------
    isci_orani = client.post('/api/cost-rates', headers=h, json={
        'kind':'LABOR', 'label':'Genel işçilik', 'hourly_rate':'100.00'})
    assert isci_orani.status_code == 201, isci_orani.text
    makine_orani = client.post('/api/cost-rates', headers=h, json={
        'kind':'MACHINE', 'label':'Traktör', 'hourly_rate':'250.00',
        'machine_id':traktor})
    assert makine_orani.status_code == 201, makine_orani.text

    gecmis = client.post('/api/field-activities', headers=h, json={
        **TABAN, 'machine_id':traktor, 'labor_hours':'2.5', 'machine_hours':'4'})
    assert gecmis.status_code == 201, gecmis.text
    g = gecmis.json()
    assert g['labor_hourly_rate'] == '100.00', g
    assert g['machine_hourly_rate'] == '250.00', g
    # 2,5 × 100 = 250,00 ve 4 × 250 = 1000,00
    assert g['labor_cost'] == '250.00', g
    assert g['machine_cost'] == '1000.00', g
    gecmis_id = g['id']

    # --- 3) ***ASIL İDDİA***: ORANI DEĞİŞTİR, GEÇMİŞ DEĞİŞMESİN -----------
    # Bu senaryo kırmızıya dönerse FAZ 2'nin varlık sebebi ortadan kalkar:
    # geçen sezonun kârı bu yıl başka çıkar ve hiçbir rapora güvenilemez.
    for kayit, yeni_oran in ((isci_orani.json(), '999.00'),
                             (makine_orani.json(), '888.00')):
        govde = {'kind':kayit['kind'], 'label':kayit['label'],
                 'hourly_rate':yeni_oran, 'expected_updated_at':kayit['updated_at']}
        if kayit['machine_id'] is not None:
            govde['machine_id'] = kayit['machine_id']
        zam = client.put('/api/cost-rates/%d' % kayit['id'], headers=h, json=govde)
        assert zam.status_code == 200, zam.text
        assert str(zam.json()['hourly_rate']).startswith(yeni_oran.split('.')[0]), zam.json()

    donmus = client.get('/api/field-activities/%d' % gecmis_id, headers=h).json()
    assert donmus['labor_hourly_rate'] == '100.00', (
        'ORAN DEGISINCE GECMIS KAYDIN ISCILIK ORANI DA DEGISTI: ' + str(donmus))
    assert donmus['machine_hourly_rate'] == '250.00', (
        'ORAN DEGISINCE GECMIS KAYDIN MAKINE ORANI DA DEGISTI: ' + str(donmus))
    assert donmus['labor_cost'] == '250.00', (
        'GECMIS ISCILIK MALIYETI DEGISTI: ' + str(donmus))
    assert donmus['machine_cost'] == '1000.00', (
        'GECMIS MAKINE MALIYETI DEGISTI: ' + str(donmus))

    # Liste ucu da aynı donmuş değeri vermeli: iki okuma yolundan biri tabloya
    # bakarsa aynı kayıt ekrana iki farklı maliyetle düşer.
    listede = [k for k in client.get(
        '/api/field-activities', headers=h,
        params={'season_id':sezon['id'], 'limit':200}).json()['items']
        if k['id'] == gecmis_id]
    assert len(listede) == 1, listede
    assert listede[0]['labor_hourly_rate'] == '100.00', (
        'LISTE UCU CANLI ORANI OKUYOR: ' + str(listede[0]))
    assert listede[0]['machine_cost'] == '1000.00', listede[0]

    # --- 4) YENİ KAYIT YENİ ORANI ALIR ------------------------------------
    # Dondurma "tabloyu hiç okumamak" değil; zam sonrası açılan kayıt güncel
    # oranı almalı, yoksa oran tanımının hiçbir işlevi kalmazdı.
    bugun = client.post('/api/field-activities', headers=h, json={
        **TABAN, 'machine_id':traktor, 'labor_hours':'1', 'machine_hours':'1'})
    assert bugun.status_code == 201, bugun.text
    assert bugun.json()['labor_hourly_rate'] == '999.00', bugun.json()
    assert bugun.json()['machine_hourly_rate'] == '888.00', bugun.json()

    # --- 5) KİŞİYE ÖZEL ORAN FİRMA GENELİNİ EZER --------------------------
    # `cost_rates` üç hedef tanıyor (makine / kişi / firma geneli). Özelden
    # genele sıra bozulursa kişiye özel tanımlanmış oran sessizce yok sayılır.
    kisiye_ozel = client.post('/api/cost-rates', headers=h, json={
        'kind':'LABOR', 'label':'Usta yevmiyesi', 'hourly_rate':'450.00',
        'user_id':ben['id']})
    assert kisiye_ozel.status_code == 201, kisiye_ozel.text
    operatorlu = client.post('/api/field-activities', headers=h, json={
        **TABAN, 'operator_user_id':ben['id'], 'labor_hours':'2'})
    assert operatorlu.status_code == 201, operatorlu.text
    assert operatorlu.json()['labor_hourly_rate'] == '450.00', (
        'KISIYE OZEL ORAN yerine firma geneli kullanildi: ' + operatorlu.text)
    assert operatorlu.json()['labor_cost'] == '900.00', operatorlu.json()

    # Oranı olmayan BAŞKA bir operatör firma geneline düşmeli.
    with SessionLocal() as oturum:
        oturum.execute(
            _sql('INSERT INTO app_users(username,email,display_name,password_hash,'
                 'role,is_active,must_change_password,created_at)'
                 ' VALUES(:k,:e,:d,:p,:r,:a,:m,:t)'),
            {'k': 'operator1', 'e': 'operator1@ornek.test', 'd': 'Operatör 1',
             'p': 'x', 'r': 'depo', 'a': True, 'm': False,
             't': '2026-01-01T00:00:00+00:00'},
        )
        operator_id = oturum.execute(
            _sql("SELECT id FROM app_users WHERE username='operator1'")
        ).scalar_one()
        oturum.execute(
            _sql('INSERT INTO user_company_memberships(user_id,company_id,'
                 'is_default,created_at) VALUES(:u,:c,:d,:t)'),
            {'u': operator_id, 'c': firmam, 'd': True,
             't': '2026-01-01T00:00:00+00:00'},
        )
        oturum.commit()
    genele_dusen = client.post('/api/field-activities', headers=h, json={
        **TABAN, 'operator_user_id':operator_id, 'labor_hours':'2'})
    assert genele_dusen.status_code == 201, genele_dusen.text
    assert genele_dusen.json()['labor_hourly_rate'] == '999.00', (
        'Kendi orani olmayan operator firma genelini almadi: ' + genele_dusen.text)

    # --- 6) AÇIKÇA GÖNDERİLEN ORAN TABLOYU EZER ---------------------------
    # `work_order_labor_lines` ile aynı desen: gönderilmişse o kullanılır.
    elle = client.post('/api/field-activities', headers=h, json={
        **TABAN, 'operator_user_id':ben['id'], 'labor_hours':'2',
        'labor_hourly_rate':'333.33'})
    assert elle.status_code == 201, elle.text
    assert elle.json()['labor_hourly_rate'] == '333.33', elle.json()
    assert elle.json()['labor_cost'] == '666.66', elle.json()

    # --- 7) ÇAPRAZ KİRACI: BAŞKA FİRMANIN ORANI KOPYALANAMAZ --------------
    # `cost_rates` ile `machines` arasinda bilesik FK YOK (migration 0052);
    # kiraci sinirini yalniz sorgunun kendi company_id yuklemi tutuyor. Yabanci
    # firmanin firma geneli LABOR orani, bizim firmamizda orani olmayan bir
    # faaliyete SIZMAMALI.
    with SessionLocal() as oturum:
        oturum.execute(
            _sql('INSERT INTO companies(name,is_active,created_at)'
                 ' VALUES(:ad,:aktif,:t)'),
            {'ad': 'Yabanci Tarim AS', 'aktif': True,
             't': '2026-01-01T00:00:00+00:00'},
        )
        yabanci = oturum.execute(
            _sql("SELECT id FROM companies WHERE name='Yabanci Tarim AS'")
        ).scalar_one()
        assert yabanci != firmam
        # Yabanci firmanin MACHINE geneli 7777; bizde MACHINE geneli YOK.
        oturum.execute(
            _sql('INSERT INTO cost_rates(company_id,kind,label,hourly_rate,status,'
                 "created_at,updated_at) VALUES(:c,'MACHINE','Yabanci makine',"
                 ":r,'ACTIVE',:t,:t)"),
            {'c': yabanci, 'r': '7777.00', 't': '2026-01-01T00:00:00+00:00'},
        )
        oturum.commit()

    # Bizim MACHINE oranimiz yalniz TRAKTORE bagli; baska bir makine yok ve
    # firma geneli MACHINE orani tanimli degil -> sonuc BOS olmali.
    sizinti = client.post('/api/field-activities', headers=h, json={
        **TABAN, 'machine_hours':'5'})
    assert sizinti.status_code == 201, sizinti.text
    assert sizinti.json()['machine_hourly_rate'] is None, (
        'BASKA FIRMANIN ORANI KOPYALANDI - kiraci sizintisi: ' + sizinti.text)
    assert sizinti.json()['machine_cost'] is None, sizinti.text

    # --- 8) PARA TELDEN STRING ÇIKAR (diyalekt sapmasi kapisi) ------------
    # FAZ 1'de ayni sapma CI'da gercekten yasandi: PostgreSQL NUMERIC'i float'a
    # ceviren bir kodlamaya dusuyor, SQLite Decimal veriyordu. Ayni ucun
    # veritabanina gore FARKLI TIPTE para dondurmesi kabul edilemez.
    for kaynak in (donmus, bugun.json(), listede[0]):
        for alan in ('labor_hourly_rate', 'machine_hourly_rate',
                     'labor_cost', 'machine_cost', 'labor_hours', 'machine_hours'):
            deger = kaynak.get(alan)
            assert deger is None or isinstance(deger, str), (alan, kaynak)
            if deger is not None and alan.endswith(('_rate', '_cost')):
                assert deger == str(Decimal(deger).quantize(Decimal('0.01'))), (
                    alan, deger)

    # --- 9) TEKRAR GÖNDERİM DONMUŞ KOPYAYI DÖNER --------------------------
    # Cevrimdisi kuyruk ayni istegi yeniden yollayabilir. Ikinci gonderim orani
    # YENIDEN cozseydi, arada yapilan bir zam kuyrukta bekleyen eski bir ise
    # gecmise donuk olarak yansirdi.
    OP = 'maliyet-tekrar-0001-abcd'
    govde = {**TABAN, 'operation_id':OP, 'labor_hours':'2'}
    ilk = client.post('/api/field-activities', headers=h, json=govde)
    assert ilk.status_code == 201, ilk.text
    assert ilk.json()['labor_hourly_rate'] == '999.00', ilk.json()

    zam = client.get('/api/cost-rates/%d' % isci_orani.json()['id'], headers=h).json()
    tekrar_zam = client.put('/api/cost-rates/%d' % zam['id'], headers=h, json={
        'kind':'LABOR', 'label':zam['label'], 'hourly_rate':'1500.00',
        'expected_updated_at':zam['updated_at']})
    assert tekrar_zam.status_code == 200, tekrar_zam.text

    tekrar = client.post('/api/field-activities', headers=h, json=govde)
    assert tekrar.status_code == 201, tekrar.text
    assert tekrar.json()['id'] == ilk.json()['id'], (ilk.json(), tekrar.json())
    assert tekrar.json()['labor_hourly_rate'] == '999.00', (
        'TEKRAR GONDERIM ORANI YENIDEN COZDU: ' + tekrar.text)
    assert tekrar.json()['labor_cost'] == '1998.00', tekrar.json()

    # --- 11) READ-SIDE MASKE: finance'siz rol ORANI GORMEZ ----------------
    # FAZ 1 `GET /api/cost-rates`i bilincli olarak finance'e bagladi. FAZ 2 ayni
    # sayiyi faaliyet satirina koydu ve o satir farm.view ile okunuyor; maske
    # olmasaydi depo ve satis rolleri orani gorurdu, yani FAZ 1'in karari
    # fiilen geri alinmis olurdu.
    #
    # ONCE yetkili tarafi olculuyor: TestClient tek bir cerez kavanozu tasiyor,
    # kimlik degistirdikten sonra yapilan olcumler oncekiyle karisabilir.
    PAROLA = 'FazIkiMaske!2026'

    def rol_olustur(kullanici, rol):
        yeni = client.post('/api/users', headers=h, json={
            'username':kullanici, 'display_name':kullanici,
            'password':PAROLA, 'role':rol})
        assert yeni.status_code in (200, 201), yeni.text
        oturum = client.post('/api/auth/login',
                             json={'username':kullanici, 'password':PAROLA})
        assert oturum.status_code == 200, oturum.text
        rh = {'Authorization':'Bearer '+oturum.json()['access_token'],
              'X-Company-ID':h['X-Company-ID']}
        cevir = client.post('/api/auth/change-password', headers=rh, json={
            'current_password':PAROLA, 'new_password':PAROLA+'x'})
        assert cevir.status_code == 200, cevir.text
        rh['Authorization'] = 'Bearer '+cevir.json()['access_token']
        return rh

    # 11a) FINANCE'LI ROL (yonetici): dort alan da VAR ve donmus degerler dogru.
    # admin `*` ile gecer; `yonetici` finance'i ACIKCA tasiyor, yani
    # `has_permission`in iki ayri kolu da olculmus oluyor.
    yh = rol_olustur('faz2_yonetici', 'yonetici')
    y_detay = client.get('/api/field-activities/%d' % gecmis_id, headers=yh)
    assert y_detay.status_code == 200, y_detay.text
    for alan, beklenen in (('labor_hourly_rate','100.00'), ('machine_hourly_rate','250.00'),
                           ('labor_cost','250.00'), ('machine_cost','1000.00')):
        assert y_detay.json().get(alan) == beklenen, (alan, y_detay.json())

    # 11b) AYRIM: orani TANIMSIZ kayitta finance'li rol `null` GORUR.
    # `null` = "maliyet BILINMIYOR". Bu, maskenin urettigi "alan yok" halinden
    # BASKA bir sey ve ikisi karismamali.
    y_oransiz = client.get('/api/field-activities/%d' % oransiz.json()['id'], headers=yh)
    assert y_oransiz.status_code == 200, y_oransiz.text
    assert 'labor_cost' in y_oransiz.json(), y_oransiz.json()
    assert y_oransiz.json()['labor_cost'] is None, y_oransiz.json()
    assert y_oransiz.json()['labor_hourly_rate'] is None, y_oransiz.json()

    # 11c/11d) FINANCE'SIZ ROL (depo): dort alan da YANITTAN CIKARILMIS olmali.
    #
    # Bulgular TEK TEK degil TOPLANIP sonda dogrulanıyor. Sebep mutasyon
    # kanitinda: maske merkezi oldugu icin tek bir kaldirma butun yollari ayni
    # anda deler, ama fail-fast bir test yalnizca ILK deligi gosterir ve
    # "liste ucu de sizdiriyor mu" sorusu cevapsiz kalirdi. Toplayarak tek
    # kosuda butun sizan yollar gorunur oluyor.
    dh = rol_olustur('faz2_depo', 'depo')
    MASKELI = ('labor_hourly_rate', 'machine_hourly_rate', 'labor_cost', 'machine_cost')
    sizinti = []

    d_detay = client.get('/api/field-activities/%d' % gecmis_id, headers=dh)
    assert d_detay.status_code == 200, d_detay.text
    for alan in MASKELI:
        if alan in d_detay.json():
            sizinti.append('TEKIL GET %s=%r' % (alan, d_detay.json()[alan]))
    # Saatler OPERASYONEL veri; maskelenmiyor.
    assert d_detay.json()['labor_hours'] == '2.5000', d_detay.json()
    assert d_detay.json()['machine_hours'] == '4.0000', d_detay.json()

    d_liste = client.get('/api/field-activities', headers=dh,
                         params={'season_id':sezon['id'], 'limit':200})
    assert d_liste.status_code == 200, d_liste.text
    assert d_liste.json()['items'], d_liste.json()
    for kalem in d_liste.json()['items']:
        for alan in MASKELI:
            if alan in kalem:
                sizinti.append('LISTE id=%s %s=%r' % (kalem['id'], alan, kalem[alan]))
    assert any(k['labor_hours'] is not None for k in d_liste.json()['items']), (
        'saatler de kaybolmus - maske fazla genis')

    # AYRIM'IN DIGER UCU: ayni "orani tanimsiz" kayitta finance'siz rol alani
    # HIC gormez. `null` gorseydi "oran tanimlanmamis" diye okurdu; oysa dogru
    # okuma "bu bilgi sana kapali". Ikisi ayni yanitta karismamali (11b, ayni
    # kaydi finance'li rolle okuyup `null` gordugumuzu kanitliyor).
    d_oransiz = client.get('/api/field-activities/%d' % oransiz.json()['id'], headers=dh)
    assert d_oransiz.status_code == 200, d_oransiz.text
    for alan in ('labor_cost', 'labor_hourly_rate'):
        if alan in d_oransiz.json():
            sizinti.append(
                'AYRIM BOZULDU: orani tanimsiz kayitta %s alani finance\'siz role'
                ' gorundu (%r). "kapali" ile "bilinmiyor" karisti'
                % (alan, d_oransiz.json()[alan]))
    assert d_oransiz.json()['labor_hours'] == '3.0000', d_oransiz.json()

    assert not sizinti, (
        'READ-SIDE MASKE DELIK - finance izni olmayan rol donmus orani gordu:\n  '
        + '\n  '.join(sizinti))

print('OK')
'''
