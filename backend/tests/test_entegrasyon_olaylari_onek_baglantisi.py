"""ARINDIRMANIN DAYANDIĞI ÖNEK, İKİ MODÜL ARASINDA BAĞLIYDI — ARTIK BAĞLI.

--- ÖLÇÜLEN BOŞLUK ----------------------------------------------------------

Okuma yüzeyinin arındırması (`routers/entegrasyon_olaylari._gerekceyi_arindir`)
saklanmış metni bir İŞARETTEN keser: `_HAM_ISTISNA_ONEKI`. O işareti YAZAN
kod BAŞKA bir modüldedir (`app/field_stok_tuketici.py`, beklenmeyen istisna
kolundaki biçim dizgisi). İki taraf arasında HİÇBİR BAĞ YOKTU.

ÖLÇÜLDÜ: tüketicinin biçim dizgisindeki TEK KELİME değiştirildiğinde
(`"beklenmeyen hata: %s: %s"` -> `"beklenmedik hata: %s: %s"`) 49 adet
`test_field_stok_*` testi ve okuma yüzeyinin 4 testi YEŞİL kaldı — ve sızıntı
uçtan uca YENİDEN AÇILDI. Kanarya dosyası bu değişikliği GÖREMEZ, çünkü öneki
KENDİ kopyasından yazar: sentetik satır hâlâ eski öneki taşır, arındırma onu
hâlâ keser, kanarya hâlâ yeşildir. Yani kapı, kapatmak için var olduğu kaçışa
KÖRDÜR.

--- NEDEN "GERÇEKTEN KOŞTURMA" ŞEKLİ SEÇİLDİ --------------------------------

İki şekil düşünüldü:

  1. Ucun sabitini import et ve tüketicinin BİÇİM DİZGİSİNİN o sabiti
     içerdiğini iddia et.
  2. Tüketiciyi GERÇEK bir veritabanı hatasına sür ve YAZDIĞI metnin ucun
     sabitiyle BAŞLADIĞINI iddia et.

İkincisi seçildi. Birincisi ölçülen kaçışı yakalar ama bir SEVİYE YUKARISINI
kaçırır: biçim dizgisi doğru kalıp `%` ile birleştirilme yeri değişse (ya da
metin `_kisalt` gibi bir aradan geçerken başına bir şey eklense) statik iddia
YİNE YEŞİL kalır, sütuna giren metin ise önekle BAŞLAMAZ. Ölçülen şey burada
biçim dizgisi değil, SÜTUNA GERÇEKTEN GİREN METİNDİR — arındırmanın gördüğü
tek şey de odur.

Ayrıca bu şekil kapıyı VAKUMDAN kurtarır: metnin önekten sonra GERÇEKTEN ham
ayrıntı taşıdığı (`[SQL: ...`, `INSERT INTO stock_movements`) da AYNI koşumda
ölçülür. Ham ayrıntı taşımayan bir metin üzerinde "arındırma çalıştı" demek
hiçbir şey ölçmezdi.

--- KULLANILAN GERÇEK HATA --------------------------------------------------

Göç 0060'ın kısmi benzersiz indeksi (`uq_stock_movements_field_event`):
olay + ürün başına EN FAZLA BİR hareket. Koşum o hareketi ÖNCEDEN yazar, sonra
tüketiciyi koşturur; tüketicinin `INSERT`i veritabanınca REDDEDİLİR. Bu, uydurma
değil ÖLÇÜLMÜŞ bir şekildir ve `_kurtar` onu tavan dolmadan `PENDING`
bırakır (koşumun kovası `RETRY_SCHEDULED`) — yani YAYGIN taşıyıcıdır.

ÖLÇÜLDÜ, gerçek PostgreSQL 16.13 üzerinde, sütuna giren metin:

    beklenmeyen hata: IntegrityError: (psycopg.errors.UniqueViolation)
    duplicate key value violates unique constraint
    "uq_stock_movements_field_event"
    DETAIL:  Key (company_id, reference_id, product_id)=(1, 7001, 10) already
    exists.
    [SQL: INSERT INTO stock_movements( ...

Kısıt ADI, GERÇEK satır anahtar DEĞERLERİ ve ham SQL. SQLite'ta aynı yol aynı
öneki ve aynı sınıfı (`IntegrityError`) yazar, ayrıntı diyalektin kendi
metnidir. Bu dosya varsayılan kulvarda (SQLite) koşar; ölçülen şey DİYALEKTE
BAĞLI DEĞİLDİR — önek tüketicinin KENDİ biçim dizgisinden gelir.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_tuketicinin_YAZDIGI_metin_ucun_ONEKIYLE_basliyor(tmp_path: Path) -> None:
    veritabani = tmp_path / "onek-baglantisi.db"
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
    assert "ONEK-BAGLANTISI-TAMAM" in tamam.stdout, tamam.stdout


_SENARYO = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

from app.db import SessionLocal
from app.inventory import default_warehouse
from app.main import app
from app.field_stok_tuketici import olaylari_isle
# BAGIN KENDISI: onek ve yerine gecen cumle UCTAN gelir, burada YENIDEN
# YAZILMAZ. Kanarya dosyasi kendi kopyasini yazar (ve yazmalidir: orada
# olculen sey UCUN davranisi). Burada olculen sey IKI MODULUN BAGIDIR, yani
# tam olarak ayni nesneye bakilmasi GEREKIR.
from app.routers.entegrasyon_olaylari import (
    _HAM_ISTISNA_ONEKI, _HAM_ISTISNA_YERINE, _gerekceyi_arindir,
)

FIRMA = 1
ZAMAN = '2026-08-01T00:00:00'
URUN = 10
OLAY = 7001
ADMIN_PW = 'OnekBag!12345'

# ONEKTEN SONRA GERCEKTEN HAM AYRINTI OLDUGUNU gosteren parcalar. Diyalekte
# BAGLI DEGILDIR: ikisi de SQLAlchemy'nin kendi sarmalayicisindan gelir.
HAM_IZLER = ('[SQL:', 'INSERT INTO stock_movements')


def _yaz(db, sql, **p):
    db.execute(_sql(sql), p)


def kur(db):
    """Goc zinciri BIR firma ve BIR depo birakiyor; kurulum onlari KULLANIR."""
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
    # CAKISAN HAREKET. Goc 0060'in kismi benzersiz indeksi tuketicinin
    # INSERT'ini REDDEDER: uydurma degil, OLCULMUS bir basarisizlik sekli.
    _yaz(db, "INSERT INTO stock_movements (product_id,movement_type,quantity,"
             "movement_date,reference_type,reference_id,note,company_id,"
             "warehouse_id) VALUES (:p,'FIELD','-1.0000',:z,"
             "'field_integration_event',:o,'onceden',:c,:w)",
         p=URUN, z=ZAMAN, o=OLAY, c=FIRMA, w=depo)


with SessionLocal() as db:
    kur(db)
    db.commit()

# --- TUKETICIYI GERCEKTEN KOSTUR -----------------------------------------
with SessionLocal() as db:
    sayac = olaylari_isle(db, FIRMA)

assert sayac['RETRY_SCHEDULED'] == 1, (
    'Beklenen basarisizlik sekli olusmadi; kurulum artik hedefi vurmuyor '
    've bu kosum HICBIR SEY olcmuyor. Sayac: %r' % (sayac,))

with SessionLocal() as db:
    durum, saklanan = db.execute(_sql(
        "SELECT status, last_error FROM field_integration_events "
        "WHERE company_id=:c AND id=:i"), {'c': FIRMA, 'i': OLAY}).one()

assert durum == 'PENDING', (
    'YAYGIN tasiyici bekleniyordu (PENDING), gelen: %r' % (durum,))

# --- VAKUM KARSITI: metin GERCEKTEN ham ayrinti tasiyor -------------------
for iz in HAM_IZLER:
    assert iz in saklanan, (
        'Saklanan metin ham ayrinti TASIMIYOR (%r yok); bu koşumda '
        'arindirilacak bir sey olmadigi icin iddia VAKUMDA gecerdi. '
        'Metin: %r' % (iz, saklanan))

# --- BAGIN KENDISI --------------------------------------------------------
assert saklanan.startswith(_HAM_ISTISNA_ONEKI), (
    'TUKETICI ILE UC ARASINDAKI BAG KOPTU: tuketicinin `last_error`e YAZDIGI '
    'metin, arindirmanin kestigi ONEKLE BASLAMIYOR. Arindirma bu metne '
    'DOKUNMAZ ve ham istisna metni okuma yuzeyinden AYNEN cikar.\n'
    '  ucun onegi (routers/entegrasyon_olaylari._HAM_ISTISNA_ONEKI): %r\n'
    '  tuketicinin yazdigi (app/field_stok_tuketici.py): %r'
    % (_HAM_ISTISNA_ONEKI, saklanan))

# Onekle BASLADIGINA gore arindirma metnin TAMAMINI degistirir: bu tasiyicida
# onekten ONCE tuketicinin kendi cumlesi YOKTUR.
assert _gerekceyi_arindir(saklanan) == _HAM_ISTISNA_YERINE, (
    'Arindirma bu metni SABIT cumleye indirmedi: %r'
    % (_gerekceyi_arindir(saklanan),))

# --- UCTAN UCA: HTTP yanitinda ham ayrinti YOK ----------------------------
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
    for iz in HAM_IZLER:
        assert iz not in r.text, (
            'GERCEK bir veritabani hatasindan dogan ham metin yuzeyden SIZDI: '
            '%r yanitta bulundu. Yanit: %s' % (iz, r.text))

print('ONEK-BAGLANTISI-TAMAM')
'''
