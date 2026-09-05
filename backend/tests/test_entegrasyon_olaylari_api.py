"""Outbox OKUMA YÜZEYİ (açılış koşulu 2) — uçtan uca sözleşme.

--- NEYİ KANITLIYOR ---------------------------------------------------------

Yüzey üç şey iddia ediyor; üçü de burada İKİ YÖNDE ölçülüyor:

1. **KİRACI.** Uç yalnız çağıranın firmasının olaylarını döner. Mutlu yol tek
   başına yetmez: "hepsini döndüren" bir uç da A firmasının olaylarını
   gösterirdi ve yeşil kalırdı. Bu yüzden B firması KENDİ olayını yazar ve
   A'nın gördüğü kümede B'nin olayının BULUNMADIĞI, B'nin gördüğü kümede
   A'nınkilerin BULUNMADIĞI ayrı ayrı ölçülür.

2. **SAYILAR TÜKETİCİNİN YAZDIĞI ŞEYDİR.** Beklenen kova sayıları testte ELLE
   yazılmıyor: gerçek tüketici (`olaylari_isle`) koşturuluyor ve DÖNDÜRDÜĞÜ
   sayaç ile ucun `summary` çıktısı karşılaştırılıyor. Elle yazılsaydı test,
   tüketici ile yüzey birlikte kaydığında yeşil kalırdı.

3. **GEREKÇE METNİ YÜZEYDE GÖRÜNÜR.** `last_error` tüketicinin yazdığı metnin
   KENDİSİDİR; burada yeniden üretilmiyor, yalnız taşındığı doğrulanıyor.
   Kova adı "neden" sorusunu cevaplamaz — hangi kaydın düzeltileceğini
   söyleyen şey metindir.

--- BU DOSYANIN İDDİA ETMEDİĞİ ----------------------------------------------

`RECOVERY_FAILED` sınıfı BURADA ÖLÇÜLEMEZ ve ölçülmüyor: o olay veritabanında
hiçbir iz bırakmaz (`FIELD_STOK_OUTBOX_ACILIS_KOSULLARI.md`), dolayısıyla
okuma yüzeyi onu TAZE İŞ olarak gösterir. Bu bir kusur değil, belgelenmiş bir
sınırdır ve router başlığında da yazılıdır.

Senaryo TEK süreçte, KENDİ taze veritabanında koşar: göç zinciri, HTTP katmanı
ve tüketici aynı koşumda. Ortam varsayımı yoktur.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

#: Yüzeyin BAŞARISIZLIK kovaları, testte BAĞIMSIZ olarak yazılı. Router'dan
#: import edilseydi totoloji olurdu: demet oradan silinse test yine geçerdi.
BEKLENEN_BASARISIZ_KOVALAR = (
    "SKIPPED_SOURCE_NOT_VISIBLE",
    "SKIPPED_NO_PRODUCT",
    "SKIPPED_TABAN_BILDIRILMEMIS",
    "DEAD",
)


def test_basarisiz_kovalar_yuzeyde_DONDURULDU() -> None:
    """`failed_only` filtresinin kümesi bir KARARDIR; sessizce değişmemeli.

    Kümeden bir kova düşerse o kova "başarısız" sayılmayı bırakır ve ekran
    onu göstermez — yani olay yine görünmez olur, üstelik yeşil bir kapıdan
    geçerek. Bu, yüzeyin kapatmak için var olduğu kusurun ta kendisi.
    """
    sys.path.insert(0, str(BACKEND))
    from app.field_stok_tuketici import DURUM_UYGULANDI, TERMINAL_DURUMLAR
    from app.routers.entegrasyon_olaylari import TARLA

    assert TARLA.basarisiz_kovalar == BEKLENEN_BASARISIZ_KOVALAR, (
        "Yüzeyin başarısızlık kümesi DEĞİŞTİ. "
        f"ölçülen={TARLA.basarisiz_kovalar} bildirilen={BEKLENEN_BASARISIZ_KOVALAR}"
    )
    # TÜKETİCİYE BAĞLA: küme, terminal durumlardan `SENT` çıkarılmış hâli
    # OLMAK ZORUNDA. Tüketici yeni bir terminal kova eklerse bu satır kırılır
    # ve yüzey sessizce eksik kalmaz.
    assert set(TARLA.basarisiz_kovalar) == set(TERMINAL_DURUMLAR) - {
        DURUM_UYGULANDI
    }, (
        "Yüzeyin başarısızlık kümesi ile tüketicinin terminal kovaları AYRIŞTI. "
        f"terminal={TERMINAL_DURUMLAR} yuzey={TARLA.basarisiz_kovalar}"
    )


def test_ikinci_outbox_icin_betimleyici_YETERLI() -> None:
    """Alan bir PARAMETRE olmalı — ikinci tablo bir YENİDEN YAZMA olmamalı.

    Bu testin ölçtüğü şey bir niyet değil, bir YAPIDIR: sorgu/yanıt biçimi
    betimleyiciden türemeli ve sütun sözleşmesi FARKLI olan bir tablo (sürüde
    `error` var, `attempts`/`processed_at` YOK) aynı yanıt anahtarlarını
    verebilmeli. Sürü YÜZEYİ BURADA KURULMUYOR; kurulabilir olduğu ölçülüyor.
    """
    sys.path.insert(0, str(BACKEND))
    from app.routers.entegrasyon_olaylari import TARLA, OlayYuzeyi, _projeksiyon

    suru = OlayYuzeyi(
        alan="herd",
        etiket="herd",
        yol="/herd-integration-events",
        tablo="herd_integration_events",
        hata_sutunu="error",
        deneme_sutunu=None,
        islenme_sutunu=None,
        basarisiz_kovalar=(),
    )
    projeksiyon = _projeksiyon(suru)
    # Olmayan sütunlar NULL olarak seçilir; yanıt ANAHTARLARI değişmez.
    assert "NULL AS attempts" in projeksiyon, projeksiyon
    assert "NULL AS processed_at" in projeksiyon, projeksiyon
    assert "error AS last_error" in projeksiyon, projeksiyon
    # Tarla yüzeyi AYNI anahtarları GERÇEK sütunlardan verir.
    tarla = _projeksiyon(TARLA)
    assert "attempts AS attempts" in tarla, tarla
    assert "last_error AS last_error" in tarla, tarla
    assert "processed_at AS processed_at" in tarla, tarla


def _kos(kaynak: str, db_yolu: Path, imza: str) -> str:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_yolu.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    tamam = subprocess.run(
        [sys.executable, "-c", kaynak],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    assert imza in tamam.stdout, tamam.stdout
    return tamam.stdout


def test_okuma_yuzeyi_uctan_uca(tmp_path: Path) -> None:
    _kos(_SENARYO, tmp_path / "olay-yuzeyi.db", "YUZEY-TAMAM")


_SENARYO = r'''
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.field_stok_tuketici import olaylari_isle
from app.main import app

ADMIN_PW = 'OlayYuzey!12345'

# Tuketicinin DONDURDUGU sayacta veritabani DURUMU olmayan alanlar.
# `girdi` islenen olay sayisi; kalanlar dongu sonuclaridir ve satira yazilmaz.
SAYAC_ALANLARI = ('girdi', 'CLAIM_LOST', 'RECOVERY_FAILED',
                  'RECOVERY_ESCALATED', 'COMPANY_FAILED')
BASARISIZ = ('SKIPPED_SOURCE_NOT_VISIBLE', 'SKIPPED_NO_PRODUCT',
              'SKIPPED_TABAN_BILDIRILMEMIS', 'DEAD')


def admin_headers(client):
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
    return h, body


def tarla_kur(client, h, kod):
    """Ciftlik + parsel + sezon. Sezonun URUNU BILDIRILMEMIS (product_id NULL)."""
    c = client.post('/api/farms', headers=h, json={'code':kod,'name':'Ciftlik '+kod})
    assert c.status_code == 201, c.text
    p = client.post('/api/farm-parcels', headers=h,
                    json={'farm_id':c.json()['id'],'code':kod+'-p','name':'Parsel',
                          'area_decare':'50.0000'})
    assert p.status_code == 201, p.text
    s = client.post('/api/crop-seasons', headers=h,
                    json={'parcel_id':p.json()['id'],'season_year':2026,
                          'crop':'Bugday','started_on':'2026-03-01',
                          'planted_area_decare':'40.0000'})
    assert s.status_code == 201, s.text
    return s.json()


def hasat_yaz(client, h, sezon_id):
    r = client.post('/api/field-harvests', headers=h,
                    json={'season_id':sezon_id,'harvested_on':'2026-08-15',
                          'quantity':'50.0000','unit':'kg'})
    assert r.status_code == 201, r.text
    return r.json()


def faaliyet_yaz(client, h, sezon_id):
    r = client.post('/api/field-activities', headers=h,
                    json={'season_id':sezon_id,'activity_type':'sowing',
                          'performed_at':'2026-04-10T08:00:00+00:00'})
    assert r.status_code == 201, r.text
    return r.json()


with TestClient(app) as client:
    h, body = admin_headers(client)
    firma_a = int(h['X-Company-ID'])

    # --- A firmasi: yazici tarafi olaylari URETIR --------------------------
    sezon_a = tarla_kur(client, h, 'A1')
    # IKI hasat: en az bir kova SAYISI 1'den BUYUK olmali. Her kovada tek
    # satir olsaydi "kac tane" sorusu VAKUMDA olculurdu — COUNT(*) yerine
    # sabit 1 donduren bir hata da yesil kalirdi (olculdu: COUNT(DISTINCT
    # source_type) mutasyonu tek satirli kovalarda YAKALANMIYORDU).
    hasat_yaz(client, h, sezon_a['id'])
    hasat_yaz(client, h, sezon_a['id'])
    faaliyet_yaz(client, h, sezon_a['id'])

    # --- B firmasi: KENDI olayini yazar -----------------------------------
    b = client.post('/api/companies', headers=h, json={'name':'Olay B Firmasi'})
    assert b.status_code == 201, b.text
    firma_b = int(b.json()['id'])
    hb = dict(h, **{'X-Company-ID': str(firma_b)})
    sezon_b = tarla_kur(client, hb, 'B1')
    hasat_yaz(client, hb, sezon_b['id'])

    # --- TUKETICIYI GERCEKTEN KOSTUR --------------------------------------
    # Beklenen sayilar ELLE yazilmiyor; tuketicinin DONDURDUGU sayac
    # dogrudan kullanilir. Yalniz A icin kosuyor: B'nin olayi PENDING kalmali.
    with SessionLocal() as db:
        sayac_a = olaylari_isle(db, firma_a)
        db.commit()
    print('SAYAC_A %r' % (sayac_a,))
    assert int(sayac_a['girdi']) == 3, sayac_a

    # Tuketici kostuktan SONRA bir olay daha: PENDING kovasi da olculsun.
    faaliyet_yaz(client, h, sezon_a['id'])

    # --- 1) OZET: kovalar tuketicinin YAZDIGI ile ortusuyor ---------------
    o = client.get('/api/field-integration-events/summary', headers=h)
    assert o.status_code == 200, o.text
    ozet = o.json()
    print('OZET %r' % (ozet,))
    assert ozet['source'] == 'field', ozet

    kovalar = {(k['source_type'], k['status']): k['count'] for k in ozet['buckets']}
    # Tuketicinin sayaci kaynak tipi kirilimi TASIMAZ; durum bazinda topla.
    yuzey_durum = {}
    for (_kaynak, durum), adet in kovalar.items():
        yuzey_durum[durum] = yuzey_durum.get(durum, 0) + adet
    olculen = 0
    for durum, adet in sayac_a.items():
        if durum in SAYAC_ALANLARI or adet == 0:
            continue
        assert yuzey_durum.get(durum) == adet, (
            'YUZEY TUKETICIDEN AYRISTI: durum=%s tuketici=%r yuzey=%r'
            % (durum, adet, yuzey_durum.get(durum)))
        olculen += 1
    assert olculen >= 1, ('tuketici hicbir kova yazmadi; karsilastirma VAKUMDA',
                          sayac_a)
    # KARSILASTIRMA VAKUMDA OLMASIN: en az bir kovada BIRDEN COK satir olmali,
    # yoksa her sayiyi 1 donduren bir toplayici da bu testten gecerdi.
    assert max(kovalar.values()) > 1, (
        'her kovada tek satir var; kova SAYISI olculmemis olur', kovalar)

    # Tuketici islenen olay sayisini `girdi` ile bildirir; PENDING kalan tek
    # olay ondan SONRA yazildi.
    assert ozet['pending_total'] == 1, ozet
    assert ozet['total'] == int(sayac_a['girdi']) + 1, (ozet, sayac_a)
    assert ozet['failed_total'] == sum(
        adet for durum, adet in yuzey_durum.items() if durum in BASARISIZ), ozet
    # Kirilim GERCEKTEN kaynak tipi basina: iki kaynak tipi de gorunuyor.
    assert {k['source_type'] for k in ozet['buckets']} == {
        'field_activity', 'field_harvest'}, ozet
    # Birikimin YASI: belgenin acilis oncesi olcumu sayiyla degil yasla verilir.
    assert all(k['oldest_created_at'] for k in ozet['buckets']), ozet

    # --- 2) BASARISIZ LISTESI: GEREKCE METNI TASINIYOR -------------------
    l = client.get('/api/field-integration-events',
                   headers=h, params={'failed_only':'true'})
    assert l.status_code == 200, l.text
    liste = l.json()
    print('BASARISIZ %r' % ([(i['source_type'], i['status'], i['last_error'])
                             for i in liste['items']],))
    assert liste['items'], liste
    hasatlar = [i for i in liste['items'] if i['source_type'] == 'field_harvest']
    assert hasatlar, liste
    kayit = hasatlar[0]
    assert kayit['status'] == 'SKIPPED_NO_PRODUCT', kayit
    # Gerekce METNI: kova adi degil, DUZELTILECEK KAYDI soyleyen metin.
    assert kayit['last_error'], ('gerekce metni BOS; kova adi tek basina '
                                 'hangi kaydin duzeltilecegini soylemez', kayit)
    assert 'crop_seasons.product_id' in kayit['last_error'], kayit
    # Sozlesmedeki her anahtar var (ikinci tabloda da AYNI anahtarlar olacak).
    for alan in ('id','source_type','source_id','target','status','attempts',
                 'last_error','created_at','updated_at','processed_at'):
        assert alan in kayit, (alan, kayit)
    # Idempotency anahtari SIZDIRILMIYOR.
    assert 'idempotency_key' not in kayit, kayit
    # `failed_only` GERCEKTEN suzuyor: PENDING olay bu listede YOK.
    assert all(i['status'] != 'PENDING' for i in liste['items']), liste

    # --- 3) CAPRAZ KIRACI: IKI YONDE -------------------------------------
    ta = client.get('/api/field-integration-events', headers=h)
    assert ta.status_code == 200, ta.text
    a_kimlikler = {i['id'] for i in ta.json()['items']}

    tb = client.get('/api/field-integration-events', headers=hb)
    assert tb.status_code == 200, tb.text
    b_govde = tb.json()
    b_kimlikler = {i['id'] for i in b_govde['items']}

    print('A_IDS %r B_IDS %r' % (sorted(a_kimlikler), sorted(b_kimlikler)))
    assert a_kimlikler, 'A firmasinin olayi yok; test vakumda gecemez'
    assert b_kimlikler, 'B firmasinin olayi yok; test vakumda gecemez'
    assert not (a_kimlikler & b_kimlikler), (
        'CAPRAZ KIRACI SIZINTISI: ayni olay iki firmada da gorunuyor',
        sorted(a_kimlikler & b_kimlikler))
    assert b_govde['total'] == len(b_kimlikler), b_govde
    # B firmasinin olayi HIC islenmedi (tuketici yalniz A icin kostu):
    # B'nin ozeti bu yuzden tamamen PENDING olmali.
    ob = client.get('/api/field-integration-events/summary', headers=hb)
    assert ob.status_code == 200, ob.text
    b_ozet = ob.json()
    assert b_ozet['total'] == b_ozet['pending_total'] == len(b_kimlikler), b_ozet
    assert b_ozet['failed_total'] == 0, b_ozet

    # --- 4) FILTRE ve SAYFALAMA sozlesmesi -------------------------------
    f = client.get('/api/field-integration-events', headers=h,
                   params={'source_type':'field_harvest'})
    assert f.status_code == 200, f.text
    assert all(i['source_type'] == 'field_harvest' for i in f.json()['items']), f.text

    d = client.get('/api/field-integration-events', headers=h,
                   params={'status':'pending'})
    assert d.status_code == 200, d.text
    assert [i['status'] for i in d.json()['items']] == ['PENDING'], d.text

    s1 = client.get('/api/field-integration-events', headers=h,
                    params={'limit':1,'offset':0}).json()
    assert len(s1['items']) == 1 and s1['limit'] == 1 and s1['offset'] == 0, s1
    assert s1['total'] == len(a_kimlikler), s1
    # Sinirlar tarla listeleriyle AYNI: limit 200'u asamaz.
    assert client.get('/api/field-integration-events', headers=h,
                      params={'limit':201}).status_code == 422

print('YUZEY-TAMAM')
'''
