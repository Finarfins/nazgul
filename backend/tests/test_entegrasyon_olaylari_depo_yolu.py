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
