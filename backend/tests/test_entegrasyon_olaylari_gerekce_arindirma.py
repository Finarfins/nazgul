"""Okuma yüzeyi SAKLANMIŞ istisna metnini SIZDIRMAZ — kürate metni ise TAŞIR.

--- BU DOSYA NEDEN VAR ------------------------------------------------------

Depo bu sızıntı sınıfını ZATEN yasaklıyor ve ölçüyor:
`test_v2_9_error_sanitization.py` ve `test_v2_9_router_error_sanitization.py`
`uq_private` / `uq_secret` kanaryalarını diker ve yanıtta BULUNMADIKLARINI
iddia eder. Ama o iki kapı CANLI istisnaları izler: bir `raise` ile yanıt
arasındaki yolu tutarlar.

Tüketici (`app/field_stok_tuketici.py`) istisnayı YAKALAR, metnini
`field_integration_events.last_error` sütununa YAZAR ve normal döner — canlı
bir istisna hiç kalmaz. Metin sonra BAŞKA bir istekte, BAŞKA bir uçtan, sıradan
bir 200 yanıtı içinde çıkar. İki mevcut kapı bu yolu YAPISAL OLARAK göremez.
Bu dosya o boşluğu kapatır ve kardeşlerinin desenini birebir izler: kanarya
dik, yanıtta ARAMA, YOKLUĞUNU iddia et.

--- İKİ YÖN ----------------------------------------------------------------

Tek yönlü bir test burada YETMEZ. `last_error`ı toptan karartan bir uç da
"kanarya yok" testinden geçerdi — ve yüzeyin BÜTÜN değerini yok ederdi, çünkü
bu ekranın var oluş sebebi kürate gerekçenin ("hangi kaydı düzelteceksin")
kullanıcıya ulaşmasıdır. Bu yüzden aynı koşumda İKİ ŞEY birden ölçülür:

  * ham istisna metninin parçaları yanıtta YOK,
  * kürate gerekçe yanıtta AYNEN VAR.

Ayrıca ARINDIRMANIN VERİTABANINA DOKUNMADIĞI ölçülür: ham metin operatör için
satırda KALIR. Kaybı olmayan bir kazanç iddiası, ölçülmeden yazılmamalı.

--- İKİ TAŞIYICI: `DEAD` NADİRDİR, `PENDING` YAYGINDIR ----------------------

Bu koşum önce YALNIZ bir `DEAD` satır dikiyordu ve bu, ölçülen dağılımın
YANLIŞ ucuydu. Tüketicinin gerçek başarısızlık şekilleri sayıldı: altısından
BEŞİ `DEAD`e HİÇ VARMAZ. `_kurtar` tavan dolmadan `_denemeyi_kaydet` çağırır,
olay `PENDING` kalır (koşumun kovası `RETRY_SCHEDULED`) ve mesaj sütuna AYNEN
— önekten ÖNCEKİ parça OLMADAN, yani önekle BAŞLAYARAK — yazılır. `DEAD`e
yalnız deneme tavanı yolu ulaşır.

ÖLÇÜLDÜ, VE BU YÜZDEN BU SATIRLAR VAR: arındırma `if status == "DEAD"` ile
daraltıldığında tek taşıyıcılı koşum YEŞİL kalıyordu (4 passed) — `PENDING`
satır ise ham metnin TAMAMINI sunuyordu. Yani kanarya, kapatmak için var
olduğu sızıntının EN YAYGIN taşıyıcısını hiç görmüyordu. Artık iki taşıyıcı da
AYRI kanarya parçalarıyla dikiliyor: hangi taşıyıcının sızdırdığı kırmızının
metninden okunur.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

#: Kürate gerekçe — `field_stok_tuketici._URUNSUZ_GEREKCE['field_harvest']`in
#: ÜRETTİĞİ metin, BURADA BAĞIMSIZ olarak yazılı. Tüketiciden import edilseydi
#: totoloji olurdu: metin oradan silinse test yine geçerdi.
BEKLENEN_KURATE_GEREKCE = (
    "sezonun ürünü bildirilmemiş; hasat stok taşıyamaz "
    "(field_harvests -> crop_seasons.product_id NULL)"
)

#: Kanarya parçaları. Kardeş dosyalardaki `uq_secret` / `uq_private` ile AYNI
#: rolde: yanıtta görünürlerse iç ayrıntı dışarı çıkmış demektir.
KANARYALAR = (
    "uq_secret_field_event",   # kısıt ADI
    "SECRET_TABLE_kolonu",     # iç tablo/sütun ADI
    "IntegrityError",          # sürücü / istisna SINIFI
)


def test_saklanmis_istisna_metni_yuzeyden_SIZMAZ(tmp_path: Path) -> None:
    veritabani = tmp_path / "gerekce-arindirma.db"
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
    assert "ARINDIRMA-TAMAM" in tamam.stdout, tamam.stdout


_SENARYO = r'''
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app

ADMIN_PW = 'Arindirma!12345'

KURATE = ('sezonun ürünü bildirilmemiş; hasat stok taşıyamaz '
          '(field_harvests -> crop_seasons.product_id NULL)')

# TUKETICININ KENDI ONEKI. Ham istisna sutuna YALNIZ bu onekle girer;
# senaryo o yazimi BIREBIR taklit eder (tuketiciyi patlatmak yerine, cunku
# olculecek sey OKUMA yolu — yazma yolu zaten kendi testlerinde olculuyor).
HAM = ('deneme tavani asildi (3): beklenmeyen hata: IntegrityError: '
       '(psycopg.errors.UniqueViolation) duplicate key value violates unique '
       'constraint "uq_secret_field_event"\nDETAIL:  Key '
       '(company_id, reference_id, product_id)=(1, 1, 960001) already exists.\n'
       '[SQL: INSERT INTO SECRET_TABLE_kolonu(a,b,c) VALUES(...)]')

KANARYALAR = ('uq_secret_field_event', 'SECRET_TABLE_kolonu', 'IntegrityError')

# YAYGIN TASIYICI: ILK denemede patlayan olay. `_kurtar` tavan dolmadan
# `_denemeyi_kaydet` cagirir; olay PENDING kalir ve mesaj ONEKLE BASLAYARAK
# aynen yazilir (onek ONCESI parca YOK). Kanarya parcalari `DEAD` satirinkinden
# AYRI: boylece hangi tasiyicinin sizdirdigi kirmizinin metninden okunur.
# Istisna SINIFI da AYRI (`OperationalError`): iki metin ayni parcayi
# tasisaydi kirmizinin "hangi tasiyici sizdirdi" etiketi YALAN olurdu.
HAM_BEKLEYEN = ('beklenmeyen hata: OperationalError: '
                '(psycopg.errors.LockNotAvailable) could not obtain lock on '
                'row in relation "BEKLEYEN_TABLO_kolonu"\n'
                '[SQL: UPDATE BEKLEYEN_TABLO_kolonu SET uq_bekleyen_field_event'
                '=%(x)s WHERE company_id=%(c)s AND id=%(i)s]\n'
                '[parameters: {\'x\': 1, \'c\': 1, \'i\': 960002}]')

KANARYALAR_BEKLEYEN = (
    'uq_bekleyen_field_event',   # kisit / sutun ADI
    'BEKLEYEN_TABLO_kolonu',     # ic tablo/sutun ADI
    'OperationalError',          # surucu / istisna SINIFI
)

# Onekten SONRASININ yerine gecen SABIT cumle. Kurate metin gibi BURADA
# BAGIMSIZ yazili: ucdan import edilseydi totoloji olurdu.
BEKLENEN_YERINE = ('beklenmeyen bir hata (ayrıntı yalnız sunucu günlüğünde ve '
                   'veritabanında)')


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
    firma = int(h['X-Company-ID'])

    # UC SATIR: NADIR tasiyici (DEAD), kurate gerekce, ve YAYGIN tasiyici
    # (PENDING) — sonuncusu olmadan `status == 'DEAD'` daraltmasi YESIL gecer.
    with SessionLocal() as db:
        # `attempts` de tasiyiciyla TUTARLI: tavan yolu 3, ilk denemede
        # patlayan PENDING satir 1. Yanlis sayac, satirin hangi yoldan
        # geldigini yalanlardi.
        for kaynak_id, durum, deneme, metin in (
                (901, 'DEAD', 3, HAM),
                (902, 'SKIPPED_NO_PRODUCT', 3, KURATE),
                (903, 'PENDING', 1, HAM_BEKLEYEN)):
            db.execute(text(
                "INSERT INTO field_integration_events"
                "(company_id,source_type,source_id,target,status,attempts,"
                " last_error,idempotency_key,created_at,updated_at) "
                "VALUES(:c,'field_harvest',:s,'stock',:d,:n,:e,:k,"
                " CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),
                {'c': firma, 's': kaynak_id, 'd': durum, 'n': deneme,
                 'e': metin, 'k': 'arindirma-%d' % kaynak_id})
        db.commit()

    r = client.get('/api/field-integration-events', headers=h)
    assert r.status_code == 200, r.text
    govde = r.text

    # --- YON 1: KANARYA YOK, HER IKI TASIYICIDA DA ------------------------
    tasiyicilar = ([('DEAD', k) for k in KANARYALAR]
                   + [('PENDING', k) for k in KANARYALAR_BEKLEYEN])
    for tasiyici, kanarya in tasiyicilar:
        assert kanarya not in govde, (
            'SAKLANMIS ISTISNA METNI YUZEYDEN SIZDI: %r yanitta bulundu; '
            'tasiyici satirin durumu %s. Yanit: %s'
            % (kanarya, tasiyici, govde))

    # --- YON 2: KURATE GEREKCE AYNEN VAR ---------------------------------
    # Bu satir olmadan test, `last_error`i toptan karartan bir uctan da
    # gecerdi — ve ekranin butun degeri o metindedir.
    kalemler = {int(k['source_id']): k for k in r.json()['items']}
    assert kalemler[902]['last_error'] == KURATE, (
        'KURATE GEREKCE BOZULDU (yuzey degersizlesti): %r'
        % (kalemler[902]['last_error'],))

    # Onekten ONCEKI kurate parca da korunmali: kacinci denemede kapandigi.
    assert 'deneme tavani asildi (3)' in kalemler[901]['last_error'], (
        'Tuketicinin KENDI cumlesi de silinmis: %r'
        % (kalemler[901]['last_error'],))

    # YAYGIN TASIYICI: metnin TAMAMI onekten sonra geldigi icin sunulan sey
    # SABIT cumlenin KENDISIDIR. Esitlik iddiasi govde taramasindan DAHA
    # KESKIN bir kirmizi verir: daraltilmis bir arindirmada burada ham metnin
    # KENDISI gorunur.
    assert kalemler[903]['last_error'] == BEKLENEN_YERINE, (
        'PENDING tasiyici arindirilmadi (YAYGIN sekil!): %r'
        % (kalemler[903]['last_error'],))

    # --- YON 3: VERITABANI ADLI DEGERI KAYBETMEDI ------------------------
    with SessionLocal() as db:
        satirda = db.execute(text(
            "SELECT last_error FROM field_integration_events "
            "WHERE company_id=:c AND source_id=901"), {'c': firma}).scalar()
    assert 'uq_secret_field_event' in satirda, (
        'Arindirma VERITABANINA sizdi; adli deger kayboldu: %r' % (satirda,))

    print('ARINDIRMA-TAMAM')
'''
