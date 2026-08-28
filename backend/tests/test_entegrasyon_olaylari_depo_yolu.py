"""İKİNCİ YAZIM YERİ: `except RuntimeError` kolu ÖNEKSİZ yazar — ve GÜVENLİ.

--- DÜZELTİLEN İDDİA --------------------------------------------------------

Arındırmanın gerekçesi (`routers/entegrasyon_olaylari._HAM_ISTISNA_ONEKI`
başlığı) "ham istisna `last_error`e TEK bir yazım yerinden girer" diyordu.
YANLIŞTI. Sayıldı, İKİ yer var:

  1. beklenmeyen istisna kolu — mesajı `beklenmeyen hata: ` ÖNEĞİYLE kurar;
     arındırmanın gördüğü ve kestiği yer burasıdır.
  2. `field_stok_tuketici._bir_olayi_isle` içinde `default_warehouse`
     çağrısını saran `except RuntimeError` kolu — `str(hata)`yı ÖNEKSİZ,
     yani arındırmanın DOKUNMADIĞI bir biçimde yazar.

--- NEDEN BU DOSYA VAR ------------------------------------------------------

İkinci yer BUGÜN zararsızdır: sardığı tek çağrı `inventory.default_warehouse`
ve o fonksiyonun TEK `raise`i sabit bir yazılı metindir —
`RuntimeError("Aktif depo bulunamadı")`. SQLAlchemy/psycopg hataları
`RuntimeError` DEĞİLDİR, o kola hiç düşmez.

Ama bu, ARINDIRMANIN GÜVENLİK SAVININ tamamının BAŞKA BİR MODÜLDEKİ bir
olguya dayanması demektir; ve o olgu SINANMIYORDU. `default_warehouse` bir gün
tanıya yardım etmek için sorguyu, firma kimliğini ya da sürücü hatasını
metnine katarsa (bu depoda tam olarak böyle "yardımcı" mesajlar var), o metin
ÖNEKSİZ olduğu için arındırmanın YANINDAN GEÇER ve okuma yüzeyinden AYNEN
çıkar. Hiçbir kapı kırmızı olmazdı: kanarya dosyası öneği OLAN metni ölçer,
bu yol ise öneksizdir.

Bu dosya o olguyu ÖLÇÜLEBİLİR yapar: yolu GERÇEKTEN koşturur ve HTTP'den
sunulan metnin TAM OLARAK o sabit cümle olduğunu — ve içinde SQL / istisna /
kısıt izi BULUNMADIĞINI — iddia eder. `default_warehouse` daha zengin bir
`RuntimeError` atarsa bu kapı KIRMIZI olur.

--- BEKLENEN METİN BURADA BAĞIMSIZ YAZILI -----------------------------------

`inventory.py`den import EDİLMEZ: edilseydi totoloji olurdu — metin orada
zenginleştirilse test yine geçerdi. Ölçülmek istenen şey tam olarak metnin
DEĞİŞMEDİĞİDİR.

--- İKİNCİ AYAK: ARIZA ÖLÇÜLÜR, DAR `except` DEĞİL --------------------------

Yukarıdaki kapı yalnız BİRİNCİ ayağı (metnin sabitliğini) ölçer. Güvenlik ise
İKİ ayağa dayanıyordu; İKİNCİSİ şuydu: `except RuntimeError` YETERİNCE DARDIR,
yani psycopg / SQLAlchemy hataları o kola HİÇ düşmez. O ayak HİÇBİR YERDE
SINANMIYORDU. ÖLÇÜLDÜ: tek kelime (`except RuntimeError` -> `except Exception`)
değiştirildiğinde `default_warehouse` içindeki GERÇEK bir sürücü arızası
ÖNEKSİZ yazılıyor, arındırma onu GÖRMÜYOR ve HTTP çağıranına
`(sqlite3.OperationalError) ... [SQL: SELECT warehouses.id ...] [parameters:
(1,)]` AYNEN sunuluyordu — bütün koşum YEŞİL kalarak.

Bu dosyadaki İKİ YENİ KAPI o boşluğu İKİ AYRI ŞEKİLDE kapatır ve BİLİNÇLİ
olarak birbirinden BAĞIMSIZDIR:

  * `test_GERCEK_surucu_arizasi_sunulan_metne_SIZMIYOR` bir `except` kolunun
    GENİŞLİĞİNİ ölçmez — ölçülemez şeyi ölçmeye çalışmaz. İLİŞKİYİ ADIYLA
    YOK EDER (`ALTER TABLE warehouses RENAME TO ...`), yani `default_warehouse`
    içinde UYDURMA DEĞİL GERÇEK bir `OperationalError` doğurur ve DIŞARI
    ÇIKAN METNİ ölçer. Kol genişletilse de daraltılsa da ölçülen şey aynıdır.
    Tüketicinin iç adlarından HİÇBİRİNİ import ETMEZ: bu bilinçlidir — kapı,
    süzgeç HİÇ var olmayan bir ağaçta da (yani asıl kusurun kendisinde)
    KIRMIZI olabilmelidir.
  * `test_depo_kolu_KURATE_OLMAYAN_metni_ISARETLIYOR` süzgecin KENDİSİNİ
    ölçer: kürate metin AYNEN geçer, kürate OLMAYAN her metin işaretlenir ve
    arındırmadan sonra iz taşımaz. Kürate kümenin `inventory.py`deki gerçek
    `raise` ile BAĞI da aynı koşumda KOŞTURULARAK ölçülür; küme eskirse
    kürate cümle işaretlenir ve operatör onu GÖREMEZ olurdu.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

#: `inventory.default_warehouse`ın TEK `raise`inin metni, BURADA BAĞIMSIZ.
BEKLENEN_DEPO_GEREKCESI = "Aktif depo bulunamadı"


def test_ONEKSIZ_yazim_yeri_ham_ayrinti_TASIMIYOR(tmp_path: Path) -> None:
    veritabani = tmp_path / "depo-yolu.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    tamam = subprocess.run(
        [sys.executable, "-c", _SENARYO],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    assert "DEPO-YOLU-TAMAM" in tamam.stdout, tamam.stdout


_SENARYO = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

from app.db import SessionLocal
from app.inventory import default_warehouse
from app.main import app
from app.field_stok_tuketici import olaylari_isle

FIRMA = 1
ZAMAN = '2026-08-01T00:00:00'
URUN = 10
OLAY = 7101
ADMIN_PW = 'DepoYolu!12345'

# `inventory.default_warehouse`in TEK `raise`inin metni, BAGIMSIZ yazili.
BEKLENEN = 'Aktif depo bulunamadı'

# SUNULAN METINDE BULUNMAMASI GEREKENLER. Kardes kapilarin (`uq_secret`,
# `uq_private`) rolunde ama burada TEK BIR kanarya dikilemez: bu yolda metni
# UYDURMUYORUZ, `default_warehouse` ne yazarsa o cikiyor. Bu yuzden kanarya
# yerine SINIF izleri araniyor.
YASAK_IZLER = (
    'SQL', 'SELECT', 'INSERT', 'UPDATE',      # ham ifade
    'psycopg', 'sqlite3', 'Error', 'Traceback',  # surucu / istisna sinifi
    'constraint', 'uq_', '[parameters',       # kisit ve bagli parametreler
    'company_id',                             # ic sutun adi
)


def _yaz(db, sql, **p):
    db.execute(_sql(sql), p)


def kur(db):
    """Faaliyet stok TASIYABILIR durumda; EKSIK olan tek sey AKTIF DEPO."""
    depo = default_warehouse(db, FIRMA)
    _yaz(db, "INSERT INTO products (id,name,purchase_price,sale_price,vat_rate,"
             "stock,unit,price_per,active,critical_stock,minimum_stock,company_id) "
             "VALUES (:i,'Tohum',0,0,0,'200.0000','kg','unit',:aktif,0,0,:c)",
         i=URUN, c=FIRMA, aktif=True)
    _yaz(db, "INSERT INTO warehouse_stocks (company_id,warehouse_id,product_id,"
             "quantity,critical_stock,reserved_quantity) "
             "VALUES (:c,:w,:p,'200.0000',0,0)", c=FIRMA, w=depo, p=URUN)
    _yaz(db, "INSERT INTO farms (id,company_id,code,name,status,created_at,"
             "updated_at) VALUES (1,:c,'f1','Ciftlik','ACTIVE',:z,:z)",
         c=FIRMA, z=ZAMAN)
    _yaz(db, "INSERT INTO farm_parcels (id,company_id,farm_id,code,name,"
             "area_decare,status,created_at,updated_at) "
             "VALUES (1,:c,1,'p1','Parsel','40.0000','ACTIVE',:z,:z)",
         c=FIRMA, z=ZAMAN)
    _yaz(db, "INSERT INTO crop_seasons (id,company_id,parcel_id,season_year,crop,"
             "status,created_at,updated_at) "
             "VALUES (1,:c,1,2026,'Bugday','ACTIVE',:z,:z)", c=FIRMA, z=ZAMAN)
    _yaz(db, "INSERT INTO field_activities (id,company_id,season_id,activity_type,"
             "performed_at,status,created_at,updated_at) "
             "VALUES (1,:c,1,'SOWING',:z,'RECORDED',:z,:z)", c=FIRMA, z=ZAMAN)
    _yaz(db, "INSERT INTO field_activity_inputs (id,company_id,activity_id,"
             "product_id,input_name,quantity,unit,created_at,updated_at) "
             "VALUES (1,:c,1,:p,'girdi','5.0000','kg',:z,:z)",
         c=FIRMA, p=URUN, z=ZAMAN)
    _yaz(db, "INSERT INTO field_integration_events (id,company_id,source_type,"
             "source_id,target,idempotency_key,status,attempts,created_at,"
             "updated_at) VALUES (:o,:c,'field_activity',1,'stock',"
             "'field_activity:1:stock','PENDING',0,:z,:z)",
         o=OLAY, c=FIRMA, z=ZAMAN)
    # AKTIF DEPO KALMASIN. `default_warehouse` yalniz AKTIF depoya bakar;
    # kalemler okunduktan SONRA, hareket yazilmadan ONCE patlar — yani olay
    # tam olarak ikinci yazim yerinden gecer.
    _yaz(db, "UPDATE warehouses SET is_active = :pasif WHERE company_id = :c",
         pasif=False, c=FIRMA)


with SessionLocal() as db:
    kur(db)
    db.commit()

# Kurulumun HEDEFI VURDUGUNU once dogrula: aktif depo GERCEKTEN kalmadi.
with SessionLocal() as db:
    try:
        default_warehouse(db, FIRMA)
    except RuntimeError:
        pass
    else:
        raise AssertionError(
            'Kurulum hedefi vurmadi: hala aktif depo var, bu kosum ikinci '
            'yazim yerine HIC ugramaz ve HICBIR SEY olcmez.')

# --- TUKETICIYI GERCEKTEN KOSTUR -----------------------------------------
with SessionLocal() as db:
    sayac = olaylari_isle(db, FIRMA)

assert sayac['DEAD'] == 1, (
    'Beklenen yol olusmadi (DEAD bekleniyordu). Sayac: %r' % (sayac,))

with SessionLocal() as db:
    durum, saklanan = db.execute(_sql(
        "SELECT status, last_error FROM field_integration_events "
        "WHERE company_id=:c AND id=:i"), {'c': FIRMA, 'i': OLAY}).one()

assert durum == 'DEAD', 'Beklenen kova DEAD degil: %r' % (durum,)

# Bu yol ONEKSIZDIR — arindirma ona DOKUNMAZ. Bunu ACIKCA olc: kapinin
# gerekcesi tam olarak budur.
assert 'beklenmeyen hata: ' not in saklanan, (
    'Bu yol artik ONEK tasiyor; kapinin gerekcesi (arindirmanin yanindan '
    'gecen ikinci yazim yeri) gecersizlesti: %r' % (saklanan,))

# --- SUNULAN METIN: TAM OLARAK O SABIT CUMLE -----------------------------
with TestClient(app) as client:
    login = client.post('/api/auth/login',
                        json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    body = login.json()
    h = {'Authorization':'Bearer '+body['access_token'],
         'X-Company-ID':str(body['companies'][0]['id'])}
    ch = client.post('/api/auth/change-password', headers=h,
                     json={'current_password':'admin123','new_password':ADMIN_PW})
    assert ch.status_code == 200, ch.text
    h['Authorization'] = 'Bearer '+ch.json()['access_token']

    r = client.get('/api/field-integration-events', headers=h)
    assert r.status_code == 200, r.text
    kalemler = {int(k['source_id']): k for k in r.json()['items']}
    sunulan = kalemler[1]['last_error']

assert sunulan == BEKLENEN, (
    'ARINDIRMANIN YANINDAN GECEN yazim yerinin metni DEGISTI. Bu yol oneksiz '
    'oldugu icin `_gerekceyi_arindir` ona DOKUNMAZ: burada ne yazarsa okuma '
    'yuzeyinden AYNEN cikar.\n'
    '  beklenen: %r\n  sunulan : %r' % (BEKLENEN, sunulan))

for iz in YASAK_IZLER:
    assert iz not in sunulan, (
        'Oneksiz yazim yeri IC AYRINTI sizdirdi: %r sunulan metinde bulundu. '
        'Metin: %r' % (iz, sunulan))

print('DEPO-YOLU-TAMAM')
'''


#: Sürücü arızası koşumunda HTTP'den sunulan metnin TAMAMI, BURADA BAĞIMSIZ
#: yazılı. Uçtan import EDİLMEZ: edilseydi sabit cümle zayıflatıldığında bu
#: kapı yine yeşil kalırdı. İşaretten ÖNCEKİ parça (`deneme tavani asildi
#: (1): `) tüketicinin KENDİ cümlesidir ve KORUNMASI beklenir — kaçıncı
#: denemede kapandığı bilgisi kaybolmaz.
BEKLENEN_ARIZA_METNI = (
    "deneme tavani asildi (1): "
    "beklenmeyen bir hata (ayrıntı yalnız sunucu günlüğünde ve veritabanında)"
)


def test_GERCEK_surucu_arizasi_sunulan_metne_SIZMIYOR(tmp_path: Path) -> None:
    veritabani = tmp_path / "depo-yolu-ariza.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    tamam = subprocess.run(
        [sys.executable, "-c", _SENARYO_ARIZA],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    assert "DEPO-ARIZA-TAMAM" in tamam.stdout, tamam.stdout


def test_depo_kolu_KURATE_OLMAYAN_metni_ISARETLIYOR(tmp_path: Path) -> None:
    veritabani = tmp_path / "depo-yolu-suzgec.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{veritabani.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    tamam = subprocess.run(
        [sys.executable, "-c", _SENARYO_SUZGEC],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    assert "DEPO-SUZGEC-TAMAM" in tamam.stdout, tamam.stdout


#: Beklenen metin senaryoya SABİT olarak enjekte edilir; alt süreç onu
#: yeniden yazmaz, yani BAĞIMSIZ kopya TEKTİR.
_SENARYO_ARIZA = ("BEKLENEN = %r\n" % (BEKLENEN_ARIZA_METNI,)) + r'''
from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

from app.db import SessionLocal
from app.inventory import default_warehouse
from app.main import app
from app.field_stok_tuketici import olaylari_isle

FIRMA = 1
ZAMAN = '2026-08-01T00:00:00'
URUN = 10
OLAY = 7102
ADMIN_PW = 'DepoAriza!12345'

# ILISKIYI ADIYLA YOK ETMEK icin kullanilan ad. Uygulamanin HICBIR yerinde
# gecmez ve kosum sonunda GERI alinir.
GIZLI_AD = 'warehouses_arizali_kosum'

# Sunulan metinde BULUNMAMASI gerekenler: surucu / ham SQL / kisit sinifi.
YASAK_IZLER = (
    '[SQL:', '[parameters', 'psycopg', 'sqlite3', 'INSERT INTO', 'SELECT ',
    'Error)', 'constraint "', 'DETAIL', 'warehouses', 'company_id',
)

# VAKUM KARSITI: SAKLANAN metnin GERCEKTEN ham ayrinti tasidigini gosterir.
# Bu izler olmadan "arindirma calisti" demek HICBIR SEY olcmezdi.
HAM_IZLER = ('[SQL:', '[parameters', 'no such table')


def _yaz(db, sql, **p):
    db.execute(_sql(sql), p)


def kur(db):
    """Faaliyet stok TASIYABILIR; AKTIF DEPO da VAR. Bozulacak olan ILISKI."""
    depo = default_warehouse(db, FIRMA)
    _yaz(db, "INSERT INTO products (id,name,purchase_price,sale_price,vat_rate,"
             "stock,unit,price_per,active,critical_stock,minimum_stock,company_id) "
             "VALUES (:i,'Tohum',0,0,0,'200.0000','kg','unit',:aktif,0,0,:c)",
         i=URUN, c=FIRMA, aktif=True)
    _yaz(db, "INSERT INTO warehouse_stocks (company_id,warehouse_id,product_id,"
             "quantity,critical_stock,reserved_quantity) "
             "VALUES (:c,:w,:p,'200.0000',0,0)", c=FIRMA, w=depo, p=URUN)
    _yaz(db, "INSERT INTO farms (id,company_id,code,name,status,created_at,"
             "updated_at) VALUES (1,:c,'f1','Ciftlik','ACTIVE',:z,:z)",
         c=FIRMA, z=ZAMAN)
    _yaz(db, "INSERT INTO farm_parcels (id,company_id,farm_id,code,name,"
             "area_decare,status,created_at,updated_at) "
             "VALUES (1,:c,1,'p1','Parsel','40.0000','ACTIVE',:z,:z)",
         c=FIRMA, z=ZAMAN)
    _yaz(db, "INSERT INTO crop_seasons (id,company_id,parcel_id,season_year,crop,"
             "status,created_at,updated_at) "
             "VALUES (1,:c,1,2026,'Bugday','ACTIVE',:z,:z)", c=FIRMA, z=ZAMAN)
    _yaz(db, "INSERT INTO field_activities (id,company_id,season_id,activity_type,"
             "performed_at,status,created_at,updated_at) "
             "VALUES (1,:c,1,'SOWING',:z,'RECORDED',:z,:z)", c=FIRMA, z=ZAMAN)
    _yaz(db, "INSERT INTO field_activity_inputs (id,company_id,activity_id,"
             "product_id,input_name,quantity,unit,created_at,updated_at) "
             "VALUES (1,:c,1,:p,'girdi','5.0000','kg',:z,:z)",
         c=FIRMA, p=URUN, z=ZAMAN)
    _yaz(db, "INSERT INTO field_integration_events (id,company_id,source_type,"
             "source_id,target,idempotency_key,status,attempts,created_at,"
             "updated_at) VALUES (:o,:c,'field_activity',1,'stock',"
             "'field_activity:1:stock','PENDING',0,:z,:z)",
         o=OLAY, c=FIRMA, z=ZAMAN)


with SessionLocal() as db:
    kur(db)
    db.commit()

# --- GERCEK SURUCU ARIZASI: ILISKIYI ADIYLA YOK ET -----------------------
# UYDURMA DEGIL: `default_warehouse`in SELECT'i veritabaniNCA reddedilir.
with SessionLocal() as db:
    db.execute(_sql("PRAGMA legacy_alter_table=ON"))
    db.execute(_sql("ALTER TABLE warehouses RENAME TO %s" % GIZLI_AD))
    db.commit()

# Kurulumun HEDEFI VURDUGUNU dogrula: gelen sey RuntimeError DEGIL, SURUCU
# katmanindan bir istisnadir. Kapi bir `except` kolunun GENISLIGINI olcmez —
# olculemez seyi olcmeye calismaz; DISARI CIKAN METNI olcer.
with SessionLocal() as db:
    try:
        default_warehouse(db, FIRMA)
    except RuntimeError as h:
        raise AssertionError(
            'Kurulum hedefi vurmadi: GERCEK bir surucu arizasi bekleniyordu, '
            'gelen KURATE bir RuntimeError: %r' % (h,))
    except Exception as h:
        metin = str(h)
        for iz in HAM_IZLER:
            assert iz in metin, (
                'Surucu istisnasi ham ayrinti TASIMIYOR (%r yok); iddia '
                'VAKUMDA gecerdi. Metin: %r' % (iz, metin))
    else:
        raise AssertionError(
            'Kurulum hedefi vurmadi: iliski yok edildigi halde '
            '`default_warehouse` patlamadi.')

# --- TUKETICIYI GERCEKTEN KOSTUR ----------------------------------------
# `azami_deneme=1`: ilk basarisizlik tavani doldurur, olay TERMINAL olur ve
# metin `last_error`e KALICI yazilir.
with SessionLocal() as db:
    sayac = olaylari_isle(db, FIRMA, azami_deneme=1)

assert sayac['DEAD'] == 1, (
    'Beklenen yol olusmadi (DEAD bekleniyordu). Sayac: %r' % (sayac,))

# ILISKIYI GERI KOY: okuma yolu kosumu arizadan ETKILENMESIN.
with SessionLocal() as db:
    db.execute(_sql("PRAGMA legacy_alter_table=ON"))
    db.execute(_sql("ALTER TABLE %s RENAME TO warehouses" % GIZLI_AD))
    db.commit()

with SessionLocal() as db:
    durum, saklanan = db.execute(_sql(
        "SELECT status, last_error FROM field_integration_events "
        "WHERE company_id=:c AND id=:i"), {'c': FIRMA, 'i': OLAY}).one()

assert durum == 'DEAD', 'Beklenen kova DEAD degil: %r' % (durum,)

# VAKUM KARSITI: SUTUNA GIREN metin GERCEKTEN ham ayrinti tasiyor. Adli deger
# veritabaninda KALIR; kapatilan tek sey HTTP yoludur.
for iz in HAM_IZLER:
    assert iz in saklanan, (
        'Saklanan metin ham ayrinti TASIMIYOR (%r yok); arindirmanin kesecegi '
        'bir sey olmadigi icin asagidaki iddia VAKUMDA gecerdi. Metin: %r'
        % (iz, saklanan))

# --- SUNULAN METIN: TAM OLARAK SABIT CUMLE ------------------------------
with TestClient(app) as client:
    login = client.post('/api/auth/login',
                        json={'username':'admin','password':'admin123'})
    assert login.status_code == 200, login.text
    body = login.json()
    h = {'Authorization':'Bearer '+body['access_token'],
         'X-Company-ID':str(body['companies'][0]['id'])}
    ch = client.post('/api/auth/change-password', headers=h,
                     json={'current_password':'admin123','new_password':ADMIN_PW})
    assert ch.status_code == 200, ch.text
    h['Authorization'] = 'Bearer '+ch.json()['access_token']

    r = client.get('/api/field-integration-events', headers=h)
    assert r.status_code == 200, r.text
    kalemler = {int(k['id']): k for k in r.json()['items']}
    sunulan = kalemler[OLAY]['last_error']
    yanit = r.text

# ONCE SIZINTI, SONRA BICIM. Sira BILINCLI: sizinti olan bir kosumda hata
# mesaji SIZINTIYI gostermeli, bicim farkini degil.
for iz in YASAK_IZLER:
    assert iz not in sunulan, (
        'GERCEK bir surucu arizasindan dogan IC AYRINTI sunulan metne SIZDI: '
        '%r bulundu. Bu kapinin butun gerekcesi budur: `default_warehouse` '
        'icinde dogan bir ORM/surucu istisnasi HTTP cagirana AYNEN cikamaz.\n'
        'Metin: %r' % (iz, sunulan))
    assert iz not in yanit, (
        'Surucu arizasi YANITIN BASKA bir alanindan sizdi: %r. Yanit: %s'
        % (iz, yanit))

assert sunulan == BEKLENEN, (
    'Sizinti YOK ama sunulan metin beklenen SABIT CUMLE degil. Tasiyici '
    'degismis olabilir (ornegin olay `_kurtar` yerine dogrudan sonlandiriliyor '
    've deneme sayaci oneki dusuyor). Bu bilgi kaybi da adi konarak '
    'gozden gecirilmelidir.\n'
    '  beklenen: %r\n  sunulan : %r' % (BEKLENEN, sunulan))

print('DEPO-ARIZA-TAMAM')
'''


_SENARYO_SUZGEC = r'''
from sqlalchemy import text as _sql

from app.db import SessionLocal
from app.inventory import default_warehouse
# SEMAYI KURAR. `app.main` import edilmeden goc zinciri kosmaz ve bu senaryo
# BOS bir veritabanina bakar; asagidaki `default_warehouse` cagrisi o zaman
# KURATE `RuntimeError` yerine "no such table" alir ve olcum ANLAMINI KAYBEDER.
from app.main import app  # noqa: F401
from app.field_stok_tuketici import KURATE_DEPO_GEREKCELERI, _depo_gerekcesi
from app.routers.entegrasyon_olaylari import _gerekceyi_arindir

FIRMA = 1

# Suzgecten gecen metnin arindirmadan SONRA tasimamasi gerekenler.
YASAK_IZLER = (
    '[SQL:', '[parameters', 'psycopg', 'sqlite3', 'INSERT INTO', 'SELECT ',
    'Error)', 'constraint "', 'DETAIL', 'no such table',
)

# --- KURATE KUME ILE GERCEK `raise` BAGLI MI? ----------------------------
# `KURATE_DEPO_GEREKCELERI` tuketicide BAGIMSIZ yazilidir; `inventory.py`
# metni degistirirse kume SESSIZCE eskiyebilirdi. Burada GERCEKTEN
# kosturularak baglanir. Bag KOPARSA kurate cumle ISARETLENIR ve operator
# HANGI KAYDI duzeltecegini artik GOREMEZ — yani suzgecin kendisi zarar verir.
with SessionLocal() as db:
    db.execute(_sql("UPDATE warehouses SET is_active = :p WHERE company_id = :c"),
               {'p': False, 'c': FIRMA})
    db.commit()

with SessionLocal() as db:
    try:
        default_warehouse(db, FIRMA)
    except RuntimeError as h:
        assert str(h) in KURATE_DEPO_GEREKCELERI, (
            '`default_warehouse` KURATE KUMEDE OLMAYAN bir RuntimeError atti; '
            'kume ESKIDI. O metin artik ISARETLENIR ve okuma yuzeyinde SABIT '
            'cumleye indirgenir, yani operator onu GOREMEZ.\n'
            '  atilan: %r\n  kume  : %r'
            % (str(h), sorted(KURATE_DEPO_GEREKCELERI)))
    else:
        raise AssertionError(
            'Kurulum hedefi vurmadi: aktif depo birakilmadigi halde '
            '`default_warehouse` patlamadi; bu kosum HICBIR SEY olcmuyor.')

assert KURATE_DEPO_GEREKCELERI, 'KURATE kume BOS; suzgec her metni isaretler.'

# --- SUZGEC: KURATE gecer, KURATE OLMAYAN ISARETLENIR --------------------
for kurate in sorted(KURATE_DEPO_GEREKCELERI):
    assert _depo_gerekcesi(RuntimeError(kurate)) == kurate, (
        'KURATE gerekce AYNEN gecmedi; okuma yuzeyi degersizlesir: %r'
        % (_depo_gerekcesi(RuntimeError(kurate)),))
    assert _gerekceyi_arindir(_depo_gerekcesi(RuntimeError(kurate))) == kurate, (
        'KURATE gerekce ARINDIRMADAN gecemedi: %r'
        % (_gerekceyi_arindir(_depo_gerekcesi(RuntimeError(kurate))),))

# KURATE OLMAYAN metin. `RuntimeError`in ALT SINIFLARI da bu kola duser
# (`RecursionError`, `NotImplementedError` ikisi de RuntimeError alt sinifidir),
# bu yuzden temsili tasiyici olarak surucu metnini tasiyan bir RuntimeError
# kullanilir.
_yabanci = RuntimeError('(sqlite3.OperationalError) no such table: warehouses '
                        '[SQL: SELECT warehouses.id] [parameters: (1,)]')
assert _depo_gerekcesi(_yabanci) != str(_yabanci), (
    'KURATE OLMAYAN metin ONEKSIZ gecti: bu kol yeniden bir HAM ISTISNA yazim '
    'yeridir ve arindirmanin YANINDAN gecer. Uretilen: %r'
    % (_depo_gerekcesi(_yabanci),))

_arinmis = _gerekceyi_arindir(_depo_gerekcesi(_yabanci))
for iz in YASAK_IZLER:
    assert iz not in _arinmis, (
        'Suzgecten gecen KURATE OLMAYAN metin arindirmadan SONRA hala %r '
        'tasiyor: %r' % (iz, _arinmis))

print('DEPO-SUZGEC-TAMAM')
'''
