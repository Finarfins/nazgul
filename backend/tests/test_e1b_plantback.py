"""Ekim-arası bekleme (plant-back) kilidi ve giriş yasağının KÖKENİ.

Konu: göç `20260907_0072`, `app/routers/farm.py` (`_plantback_*`,
`_giris_yasagi_coz`), `app/farm_schemas.py`, `app/routers/companies.py`.

İKİ AYRI EKSİK ölçülüyor:

1. PLANT-BACK ŞEMADA YOKTU. Devir notu: "herbisit ekim-arası 10-18 ay". Bu
   ne PHI'dir ne giriş yasağı — ilacın toprakta kalan etkisi SONRAKİ SEZONUN
   ekimini yakar. Kilit `POST/PUT /api/crop-seasons`ta.
2. GİRİŞ YASAĞININ KÖKENİ KAYITTA YOKTU. E1a değeri katalogdan çözüyordu ama
   kimin koyduğunu yazmıyordu; `_giris_yasagi_coz`un kendi başlığı bunu adıyla
   söylüyordu ("sütunu yok ve açmak göç olurdu"). Göç 0072 sütunları açtı.

Şekil, deponun mevcut kalıbı: STATİK KAPILAR + alt süreçte GERÇEK ŞEMALI
davranış smoke'u (`test_farm_reentry_enforcement.py` ile aynı).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
GOC = BACKEND / "alembic" / "versions" / "20260907_0072_plantback_rei_kaynak.py"


# --------------------------------------------------------------- statik ---

def test_goc_ayri_tablo_aciyor_sutun_DEGIL() -> None:
    """Plant-back AYRI TABLODUR ve gerekçesi göçün başlığında ÖLÇÜLMÜŞTÜR.

    `plant_protection_products`ın tekilliği `(company_id, product_id, crop)`
    olduğu için aynı ilaç+bitki için İKİNCİ bir ardıl bitki satırı GİRİLEMEZ;
    sütun tercihi o yüzden reddedildi. Bu kapı, birinin sütunlara geri
    dönmesini sessiz bırakmıyor.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    assert "plant_protection_plantbacks" in kaynak
    assert "uq_ppb_company_product_crop_next" in kaynak
    assert "fk_ppb_product_same_company" in kaynak
    # Reddedilen tasarım: katalog tablosuna sütun.
    assert "plantback_interval_days" not in kaynak.split('"""')[2]
    assert "plantback_crop" not in kaynak.split('"""')[2]


def test_goc_turu_up_down_up_SQLitede_KOSUYOR(tmp_path: Path) -> None:
    """Dört parça da doğuyor, `downgrade` DÖRDÜNÜ DE geri alıyor, tur kapanıyor.

    Kaynağı grep'lemek YETMEZDİ: `downgrade` gövdesi tablo adını SABİTTEN
    (``PLANTBACK``) okuyor ve dizge araması onu göremezdi — daha kötüsü,
    `drop_column` çağrılarının SQLite'ta GERÇEKTEN çalıştığını hiç
    ölçmezdi. 0071'de ölçülen kusur (yansıtılan CHECK düşürülmüş sütunu
    adıyla anıyor) yalnız gerçek bir turda görünür.
    """
    veritabani = tmp_path / "e1b-goc.db"
    betik = _GOC_TURU % {"url": f"sqlite:///{veritabani.as_posix()}"}
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND)
    env["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    completed = subprocess.run(
        [sys.executable, "-c", betik], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


_GOC_TURU = r"""
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

URL = %(url)r
config = Config("alembic.ini")
motor = sa.create_engine(URL)


def durum():
    d = sa.inspect(motor)
    d.info.clear() if hasattr(d, "info") else None
    tablolar = set(d.get_table_names())
    def sutunlar(t):
        return {c["name"] for c in d.get_columns(t)}
    return {
        "tablo": "plant_protection_plantbacks" in tablolar,
        "faaliyet": {"reentry_source", "catalogue_reentry_days"} <= sutunlar("field_activities"),
        "sezon": {"plantback_warning", "plantback_override_reason"} <= sutunlar("crop_seasons"),
        "firma": "farm_plantback_policy" in sutunlar("companies"),
    }


command.upgrade(config, "head")
motor.dispose(); motor = sa.create_engine(URL)
assert all(durum().values()), durum()

# HEDEF AÇIK YAZILDI, "-1" DEĞİL. "-1" bir GÖREL adımdır ve zincirin
# UCUNU indirir; 1B-A (`20260908_0073`) 0072'nin ÜSTÜNE binince "-1" artık
# 0073'ü indiriyordu ve 0072'nin nesneleri AYAKTA kalıyordu — bu kapı o gün
# "geri alma çalışmıyor" diye kırmızı oldu, oysa ölçtüğü şey hiç
# çalıştırılmamıştı. Mutlak hedef, üstüne kaç göç binerse binsin 0072'nin
# KENDİ `downgrade`ini sürer.
command.downgrade(config, "20260906_0071")
motor.dispose(); motor = sa.create_engine(URL)
assert not any(durum().values()), durum()

command.upgrade(config, "head")
motor.dispose(); motor = sa.create_engine(URL)
assert all(durum().values()), durum()

# BAŞ TEK: göç 0072 zincire ikinci bir baş EKLEMEDİ. Baş ARTIK 0073'tür
# (1B-A, PARTİ DEFTERİ DEPOYA BAĞLANIR) ve bu kapının ölçtüğü şey başın
# HANGİ göç olduğu değil, TEK olduğudur — 0072 hâlâ zincirin İÇİNDE ve
# yukarıdaki `downgrade -1` turu onu adıyla sürüyor.
from alembic.script import ScriptDirectory
baslar = ScriptDirectory.from_config(config).get_heads()
assert tuple(baslar) == ("20260908_0073",), baslar
print("GOC TURU TAMAM")
"""


def test_check_ve_sutun_AYNI_batchte_dusuyor() -> None:
    """0071'in dersi: SQLite'ta yansıtılan CHECK, düşürülmüş sütunu adıyla anar.

    `farm_plantback_policy` düşürülürken CHECK'i AYNI batch'te ÖNCE düşmeli;
    ayrı çağrılara bölünürse `downgrade` `OperationalError` verir.
    """
    kaynak = GOC.read_text(encoding="utf-8")
    asagi = kaynak[kaynak.index("def downgrade"):]
    batch = asagi[asagi.index('batch_alter_table(FIRMA)'):]
    kisit = batch.index("drop_constraint")
    sutun = batch.index('drop_column("farm_plantback_policy")')
    assert kisit < sutun, "CHECK sütundan SONRA düşüyor"


def test_ACILIS_DDLi_GOCUN_ONUNE_GECMIYOR() -> None:
    """0072'nin dört nesnesinden HANGİLERİ açılış DDL'inde de bildiriliyor.

    ÖLÇÜLMÜŞ KUSUR (CI'da kırmızı oldu): `app/tenancy.py` `companies`i
    `Table()` olarak bildiriyor ve uygulamanın AÇILIŞI o tabloyu alembic'ten
    ÖNCE kurabiliyor. Sütun bildirime eklendiği için göç 0072 onu VAR bulup
    tek `if` dalını ATLADI ve `ck_companies_farm_plantback_policy` HİÇ
    KURULMADI — göç yeşil bitti, kısıt yoktu.

    Bu kapı o sınıfı ADIYLA çiviliyor:

    * `companies` açılışta bildiriliyor (bu bir OLGU, kusur değil), bu yüzden
      göç sütunu ve CHECK'i AYRI AYRI sormak ZORUNDA — kapı göçün kaynağında
      o ayrımı arıyor.
    * Öteki ÜÇ nesnenin tabloları (`plant_protection_plantbacks`,
      `crop_seasons`, `field_activities`) HİÇBİR açılış bildiriminde YOK,
      yani onların TEK yaratıcısı göçtür ve aynı kusur onlarda ÜRETİLEMEZ.
      Biri bir gün açılış DDL'ine girerse bu kapı kırmızı olur ve o göçün de
      aynı ayrımı yapması gerektiği İNCELEMEYE zorlanır.
    """
    import re

    acilis = ""
    for modul in ("tenancy.py", "core_schema.py", "auth.py", "inventory.py",
                  "finance_engine.py", "workflow.py"):
        acilis += (BACKEND / "app" / modul).read_text(encoding="utf-8")
    bildirilen = set(re.findall(r"""Table\(\s*['"]([a-z_]+)['"]""", acilis))

    assert "companies" in bildirilen, (
        "companies açılışta bildirilmiyor — bu kapının dayandığı olgu değişti"
    )
    for tablo in ("plant_protection_plantbacks", "crop_seasons",
                  "field_activities"):
        assert tablo not in bildirilen, (
            "%s açılış DDL'ine girmiş; göç 0072 onu VAR bulup atlayabilir "
            "(companies'te ölçülen kusurun aynısı)" % tablo
        )

    goc = GOC.read_text(encoding="utf-8")
    # Sütun ve CHECK AYRI AYRI soruluyor mu — companies açılışta bildirildiği
    # için bu bir tercih değil ZORUNLULUK.
    assert "sutun_eksik" in goc and "check_eksik" in goc, (
        "companies dalı sütun ve CHECK'i tek koşulda soruyor; açılış DDL'i "
        "sütunu kurduğunda CHECK SESSİZCE kurulmaz"
    )


def test_plantback_politikasinda_allow_seviyesi_YOK() -> None:
    """0048/0064 ile AYNI sınır: kontrolü tamamen kapatan bir seviye YOK."""
    sys.path.insert(0, str(BACKEND))
    from app.routers.companies import CompanyPolicyUpdate

    def seviyeler(alan: str) -> set[str]:
        annotation = CompanyPolicyUpdate.model_fields[alan].annotation
        bulunan: set[str] = set()
        yigin = [annotation]
        while yigin:
            item = yigin.pop()
            for arg in getattr(item, "__args__", ()):
                if isinstance(arg, str):
                    bulunan.add(arg)
                else:
                    yigin.append(arg)
        return bulunan

    assert seviyeler("farm_plantback_policy") == {"warn", "require_reason", "block"}
    assert "allow" not in seviyeler("farm_plantback_policy")
    # KARDEŞ KAPI: giriş yasağı seviyeleri de KIMILDAMADI.
    assert seviyeler("farm_reentry_policy") == {"warn", "require_reason", "block"}


def test_plantback_sorgusu_parsel_kapsamli_ve_kiraci_bagli() -> None:
    kaynak = (BACKEND / "app" / "routers" / "farm.py").read_text(encoding="utf-8")
    bas = kaynak.index("_PLANTBACK_SORGU = text(")
    son = kaynak.index("_PLANTBACK_OZGULLUK")
    govde = kaynak[bas:son]
    assert "a.company_id=:cid" in govde
    assert "s.parcel_id=:pid" in govde
    # Her birleştirme kiracı içinde: çıplak bir JOIN başka firmanın kuralını
    # bu parsele uygulayabilirdi.
    assert "b.company_id=a.company_id" in govde
    assert "u.company_id=b.company_id" in govde
    assert "s.company_id=a.company_id" in govde
    assert "i.company_id=a.company_id" in govde


def test_kodda_YASAL_PLANTBACK_SABITI_YOK() -> None:
    """Depo hiçbir ekim-arası süre İDDİA ETMEZ (0063'ün duruşu).

    Devir notundaki "10-18 ay" bir ETİKET bilgisidir, kod sabiti değil. Bir
    gün 365 ya da 540 gibi bir varsayılan koda düşerse depo o rakamın SAHİBİ
    olur ve yanlış olduğunda sorumluluğu üstlenir.
    """
    kaynak = (BACKEND / "app" / "routers" / "farm.py").read_text(encoding="utf-8")
    bas = kaynak.index("def _plantback_satirlari")
    son = kaynak.index("def _monokultur_gecmisi") if "def _monokultur_gecmisi" in kaynak[bas:] else len(kaynak)
    govde = kaynak[bas:kaynak.index("_PLANTBACK_SEBEP")]
    for uydurma in ("365", "540", "300", "180"):
        assert uydurma not in govde, "kodda plant-back sabiti: %s" % uydurma


def test_giris_yasagi_kokeni_PHI_ile_AYNI_sozlugu_kullaniyor() -> None:
    """İkinci bir sözlük uydurmak denetçiye aynı olguyu iki dilde okuturdu."""
    kaynak = (BACKEND / "app" / "routers" / "farm.py").read_text(encoding="utf-8")
    govde = kaynak[kaynak.index("def _giris_yasagi_coz"):kaynak.index("@router.get(\"/plant-protection-products\")")]
    assert "_PHI_KOKEN_KATALOG" in govde
    assert "_PHI_KOKEN_OPERATOR" in govde
    assert "_PHI_KOKEN_USTUNE_YAZMA" in govde
    # Giriş yasağına ÖZEL bir sözlük SABİTİ açılmadı.
    assert "_REI_KOKEN" not in kaynak


def test_plantback_ucu_farm_izin_ailesinde() -> None:
    """`/api/plant-protection-plantbacks` genel `read` iznine DÜŞMÜYOR."""
    sys.path.insert(0, str(BACKEND))
    from app.auth import required_permission

    assert required_permission("GET", "/api/plant-protection-plantbacks") == "farm.view"
    assert required_permission("POST", "/api/plant-protection-plantbacks") == "farm.manage"
    assert required_permission("PUT", "/api/plant-protection-plantbacks/1") == "farm.manage"
    # ÖNEK EŞLEŞMESİ: katalog önekinin ALTINA düşmüyor, KENDİ satırından geliyor.
    assert not "/api/plant-protection-plantbacks".startswith(
        "/api/plant-protection-products"
    )


# ------------------------------------------------------------- davranış ---

def run_plantback_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def test_plantback_enforcement_sqlite(tmp_path: Path) -> None:
    run_plantback_smoke(f"sqlite:///{(tmp_path / 'e1b-plantback.db').as_posix()}")


_SMOKE = r'''
import sys
from fastapi.testclient import TestClient
from app.main import app

ADMIN_PW = 'E1bPlant!123'


def admin_headers(client):
    for aday in ('admin123', ADMIN_PW):
        r = client.post('/api/auth/login',
                        json={'username':'admin','password':aday})
        if r.status_code == 200:
            break
    assert r.status_code == 200, r.text
    b = r.json()
    h = {'Authorization':'Bearer '+b['access_token'],
         'X-Company-ID':str(b['companies'][0]['id'])}
    if aday != ADMIN_PW:
        ch = client.post('/api/auth/change-password', headers=h,
                         json={'current_password':aday,'new_password':ADMIN_PW})
        assert ch.status_code == 200, ch.text
        h['Authorization'] = 'Bearer '+ch.json()['access_token']
    return h


def kural_yaz(client, h, **kurallar):
    r = client.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'allow','credit_limit_policy':'allow', **kurallar})
    assert r.status_code == 200, r.text


def sezon_yaz(client, h, parsel, yil, bitki, gun, **fazla):
    return client.post('/api/crop-seasons', headers=h, json={
        'parcel_id':parsel,'season_year':yil,'crop':bitki,'started_on':gun,
        **fazla})


with TestClient(app) as client:
    h = admin_headers(client)

    ayar = client.get('/api/company-settings', headers=h).json()
    assert ayar['farm_plantback_policy'] == 'require_reason', ayar

    urun = client.post('/api/products', headers=h, json={
        'name':'Herbisit E1B','unit':'LT','sale_price':'10.00'}).json()
    pid = urun['id']
    urun2 = client.post('/api/products', headers=h, json={
        'name':'Herbisit E1B Kisa','unit':'LT','sale_price':'10.00'}).json()
    pid2 = urun2['id']

    # KATALOG: PHI + giriş yasağı (E1a kökeni için) ve plant-back satırları.
    r = client.post('/api/plant-protection-products', headers=h, json={
        'product_id':pid,'crop':'','preharvest_interval_days':21,
        'reentry_interval_days':4})
    assert r.status_code == 201, r.text

    # Buğdaya atılınca: ayçiçeği 365, mercimek 120, ardıl belirtilmeyen 60.
    for ardil, gun in (('Ayçiçeği',365), ('Mercimek',120), ('',60)):
        r = client.post('/api/plant-protection-plantbacks', headers=h, json={
            'product_id':pid,'crop':'Buğday','next_crop':ardil,'interval_days':gun})
        assert r.status_code == 201, r.text
    # Bitkiden BAĞIMSIZ kural (crop=''), ardılı da boş: 30 gün.
    r = client.post('/api/plant-protection-plantbacks', headers=h, json={
        'product_id':pid2,'crop':'','next_crop':'','interval_days':30})
    assert r.status_code == 201, r.text

    # AYNI ÜRÜN+BİTKİ İÇİN İKİNCİ ARDIL SATIRI GEÇİYOR — bu göçün TÜM SEBEBİ.
    # (yukarıda üç satır aynı (pid,'Buğday') için yazıldı ve üçü de 201 aldı.)
    liste = client.get('/api/plant-protection-plantbacks', headers=h,
                       params={'product_id':pid}).json()
    assert liste['total'] == 3, liste
    # Aynı üçlünün TEKRARI reddediliyor.
    tekrar = client.post('/api/plant-protection-plantbacks', headers=h, json={
        'product_id':pid,'crop':'Buğday','next_crop':'Ayçiçeği','interval_days':999})
    assert tekrar.status_code == 409, tekrar.text

    ciftlik = client.post('/api/farms', headers=h,
                          json={'code':'e1b','name':'E1B Çiftlik'}).json()
    parsel = client.post('/api/farm-parcels', headers=h, json={
        'farm_id':ciftlik['id'],'code':'e1bp','name':'E1B Parsel',
        'area_decare':'20.0000'}).json()
    parsel2 = client.post('/api/farm-parcels', headers=h, json={
        'farm_id':ciftlik['id'],'code':'e1bp2','name':'E1B Parsel 2',
        'area_decare':'20.0000'}).json()

    onceki = sezon_yaz(client, h, parsel['id'], 2025, 'Buğday', '2025-01-01').json()

    ilac = client.post('/api/field-activities', headers=h, json={
        'season_id':onceki['id'],'activity_type':'SPRAYING',
        'performed_at':'2025-06-01T09:00:00+03:00',
        'applied_area_decare':'20.0000',
        'inputs':[{'product_id':pid,'input_name':'Herbisit E1B','quantity':'5',
                   'unit':'LT','dose':'1','dose_unit':'LT/da'},
                  {'product_id':pid2,'input_name':'Herbisit E1B Kisa',
                   'quantity':'2','unit':'LT','dose':'1','dose_unit':'LT/da'}]})
    assert ilac.status_code == 201, ilac.text
    a = ilac.json()

    # --- 1) E1a KÖKENİ: giriş yasağı PHI ile AYNI çifti yazıyor ------------
    assert a['reentry_interval_days'] == 4, a
    assert a['reentry_source'] == 'CATALOGUE', a
    assert a['catalogue_reentry_days'] == 4, a
    assert a['preharvest_source'] == 'CATALOGUE', a

    # --- 2) EN UZUN KAZANIR: ayçiçeği 365 (mercimek 120 ve genel 60 DEĞİL) -
    ihlal = sezon_yaz(client, h, parsel['id'], 2026, 'Ayçiçeği', '2026-05-01')
    assert ihlal.status_code == 422, ihlal.text
    d = ihlal.json()['detail']
    assert d['sebep'] == 'PLANTBACK_SURESI_DOLMADI', d
    assert d['blocking'][0]['interval_days'] == 365, d
    assert d['blocking'][0]['earliest_allowed'] == '2026-06-01', d
    assert d['blocking'][0]['product_name'] == 'Herbisit E1B', d
    assert d['blocking'][0]['performed_on'] == '2025-06-01', d

    # --- 3) require_reason: GEREKÇEYLE GEÇİYOR, UYARI SATIRA YAZILIYOR -----
    gecti = sezon_yaz(client, h, parsel['id'], 2026, 'Ayçiçeği', '2026-05-01',
                      plantback_override_reason='toprak analizi temiz')
    assert gecti.status_code == 201, gecti.text
    g = gecti.json()
    assert g['plantback_warning'], g
    assert g['plantback_override_reason'] == 'toprak analizi temiz', g
    # 0048 kuralı: sistemin bulduğu ile kullanıcının söylediği AYRI sütunda.
    assert g['plantback_warning'] != g['plantback_override_reason'], g

    # --- 4) ARDIL BİTKİYE ÖZEL EŞLEŞME: mercimek 120 gün -------------------
    m = sezon_yaz(client, h, parsel['id'], 2026, 'Mercimek', '2025-09-01')
    assert m.status_code == 422, m.text
    dm = m.json()['detail']
    assert dm['blocking'][0]['interval_days'] == 120, dm
    assert dm['blocking'][0]['earliest_allowed'] == '2025-09-29', dm

    # --- 5) SINIR GÜNÜ İZİNLİ: ekim == en erken tarih ----------------------
    sinir = sezon_yaz(client, h, parsel['id'], 2026, 'Mercimek', '2025-09-29')
    assert sinir.status_code == 201, sinir.text
    assert sinir.json()['plantback_warning'] is None, sinir.text

    # --- 6) ARDIL '' YEDEĞİ: kuralı olmayan bitki genel satıra düşüyor -----
    # Pancar için ÖZEL kural yok; (Buğday, '') 60 gün geçerli.
    pancar = sezon_yaz(client, h, parsel['id'], 2026, 'Pancar', '2025-07-01')
    assert pancar.status_code == 422, pancar.text
    dp = pancar.json()['detail']
    assert dp['blocking'][0]['interval_days'] == 60, dp
    assert dp['blocking'][0]['next_crop'] == '', dp
    # 60 günün dolduğu gün serbest.
    assert sezon_yaz(client, h, parsel['id'], 2026, 'Pancar',
                     '2025-07-31').status_code == 201

    # --- 7) BİTKİ '' YEDEĞİ: ikinci ürünün kuralı bitki bağımsız -----------
    # Buğdayda atılan pid2'nin kuralı crop='' ile eşleşiyor (30 gün).
    # pid'in kuralları 2025-07-31'de dolduğu için kalan tek kısıt odur.
    erken = sezon_yaz(client, h, parsel['id'], 2026, 'Pancar', '2025-06-15')
    assert erken.status_code == 422, erken.text
    engeller = erken.json()['detail']['blocking']
    kurallar = {x['interval_days'] for x in engeller}
    assert kurallar == {60, 30}, kurallar
    # EN UZUN KAZANIR: iki kural da ısırıyor, kullanıcıya söylenen tarih
    # UZUN olanınki. Kısa olanı seçmek, uzun olanın süresi dolmadan ekime
    # izin verirdi.
    assert engeller[0]['interval_days'] == 60, engeller
    assert engeller[0]['earliest_allowed'] == '2025-07-31', engeller
    assert '60 gün' in erken.json()['detail']['message'], erken.text

    # --- 8) BAŞKA PARSEL ETKİLENMİYOR --------------------------------------
    temiz = sezon_yaz(client, h, parsel2['id'], 2026, 'Ayçiçeği', '2026-05-01')
    assert temiz.status_code == 201, temiz.text
    assert temiz.json()['plantback_warning'] is None, temiz.text

    # --- 9) POLİTİKA: block ------------------------------------------------
    kural_yaz(client, h, farm_plantback_policy='block')
    bloke = sezon_yaz(client, h, parsel['id'], 2026, 'Ayçiçeği', '2026-05-02',
                      plantback_override_reason='gerekçe YAZILDI ama block')
    assert bloke.status_code == 422, bloke.text
    db_ = bloke.json()['detail']
    assert db_['sebep'] == 'PLANTBACK_SURESI_DOLMADI', db_
    assert 'izin vermiyor' in db_['message'], db_

    # --- 10) POLİTİKA: warn -> KABUL ama UYARI YAZILIYOR -------------------
    kural_yaz(client, h, farm_plantback_policy='warn')
    uyarili = sezon_yaz(client, h, parsel['id'], 2026, 'Ayçiçeği', '2026-05-03')
    assert uyarili.status_code == 201, uyarili.text
    assert uyarili.json()['plantback_warning'], uyarili.text
    assert uyarili.json()['plantback_override_reason'] is None, uyarili.text
    kural_yaz(client, h, farm_plantback_policy='require_reason')

    # --- 11) "allow" SEVİYESİ YOK ------------------------------------------
    kotu = client.put('/api/company-settings', headers=h, json={
        'negative_stock_policy':'allow','credit_limit_policy':'allow',
        'farm_plantback_policy':'allow'})
    assert kotu.status_code == 422, kotu.text

    # --- 12) PUT: BİTKİ DEĞİŞİNCE YENİDEN DEĞERLENDİRİLİYOR ----------------
    # Sınır gününde açılmış TEMİZ bir sezon (5. adım) ayçiçeğine çevrilirse
    # 365 günlük kural onu KESER.
    temiz_id = sinir.json()['id']
    surum = sinir.json()['updated_at']
    put = client.put('/api/crop-seasons/%d' % temiz_id, headers=h, json={
        'parcel_id':parsel['id'],'season_year':2026,'crop':'Ayçiçeği',
        'started_on':'2025-09-29','status':'ACTIVE',
        'expected_updated_at':surum})
    assert put.status_code == 422, put.text
    assert put.json()['detail']['sebep'] == 'PLANTBACK_SURESI_DOLMADI', put.text

    # PUT gerekçeyle geçiyor ve sezon KENDİ faaliyetini kendine uygulamıyor.
    put2 = client.put('/api/crop-seasons/%d' % temiz_id, headers=h, json={
        'parcel_id':parsel['id'],'season_year':2026,'crop':'Ayçiçeği',
        'started_on':'2025-09-29','status':'ACTIVE',
        'plantback_override_reason':'sahip kararı',
        'expected_updated_at':surum})
    assert put2.status_code == 200, put2.text
    assert put2.json()['plantback_warning'], put2.text

    # --- 13) GET /field-safety: plantback_blocks -------------------------
    guvenlik = client.get('/api/field-safety', headers=h).json()
    assert 'plantback_blocks' in guvenlik, guvenlik
    assert isinstance(guvenlik['plantback_blocks'], list), guvenlik
    # Mevcut kısıtlar 2026-06-01'de bitiyor; rapor BUGÜNE göre çalışıyor ve
    # bu smoke'un çalıştığı gün onların hepsi geçmişte. Boş olması BEKLENEN
    # ve raporun "yürürlükte olan" sözleşmesinin kendisi.
    for blok in guvenlik['plantback_blocks']:
        assert 'parcel_id' in blok and 'blocking' in blok, blok

    # --- 14) ÇAPRAZ KİRACI: A'nın kuralı B'yi KESMEZ ----------------------
    b = client.post('/api/companies', headers=h, json={'name':'E1B B'}).json()
    hb = dict(h, **{'X-Company-ID': str(b['id'])})
    assert client.get('/api/plant-protection-plantbacks',
                      headers=hb).json()['total'] == 0
    cb = client.post('/api/farms', headers=hb,
                     json={'code':'e1bb','name':'B Çiftlik'}).json()
    pb = client.post('/api/farm-parcels', headers=hb, json={
        'farm_id':cb['id'],'code':'e1bbp','name':'B Parsel',
        'area_decare':'10.0000'}).json()
    bsezon = sezon_yaz(client, hb, pb['id'], 2026, 'Ayçiçeği', '2026-05-01')
    assert bsezon.status_code == 201, bsezon.text
    assert bsezon.json()['plantback_warning'] is None, bsezon.text
    assert client.get('/api/field-safety', headers=hb).json()[
        'plantback_blocks'] == []

    # --- 15) EKİM GÜNÜ BOŞSA SUSULUYOR ------------------------------------
    gunsuz = client.post('/api/crop-seasons', headers=h, json={
        'parcel_id':parsel['id'],'season_year':2027,'crop':'Ayçiçeği'})
    assert gunsuz.status_code == 201, gunsuz.text
    assert gunsuz.json()['plantback_warning'] is None, gunsuz.text

    print('EKIM ARASI BEKLEME KILIDI TAMAM')
'''
