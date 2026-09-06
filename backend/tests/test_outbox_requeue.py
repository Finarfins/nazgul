"""Outbox YENİDEN KUYRUKLAMA (açılış koşulu 3) — uçtan uca sözleşme.

--- NEYİ KANITLIYOR ---------------------------------------------------------

`FIELD_STOK_OUTBOX_ACILIS_KOSULLARI.md` üçüncü koşulu: "Tüketici yalnız
`PENDING` seçer; `SKIPPED_*`/`DEAD` yazılan satır bir daha ASLA seçilmez ve onu
`PENDING`e döndüren hiçbir mekanizma yok." `POST
/api/field-integration-events/{id}/requeue` o mekanizmadır ve burada altı şey
İKİ YÖNDE ölçülüyor:

1. **HER TERMİNAL KOVA GERİ ALINABİLİR.** Dördü de (`SKIPPED_SOURCE_NOT_
   VISIBLE`, `SKIPPED_NO_PRODUCT`, `SKIPPED_TABAN_BILDIRILMEMIS`, `DEAD`)
   ayrı ayrı üretilip ayrı ayrı geri alınıyor. Kovalar ELLE yazılmıyor:
   gerçek tüketici (`olaylari_isle`) koşturuluyor ve onun YAZDIĞI durum geri
   alınıyor.

2. **`SENT` GERİ ALINAMAZ.** Gönderilmiş olayın stok hareketi YAZILMIŞTIR ve
   tüketici `stock_movements` satırlarını hiçbir yolda UPDATE/DELETE etmez;
   yeniden gönderim "tekrar denemek" değil İKİNCİ BİR HAREKET yazmayı denemek
   olurdu. 409 + `EVENT_ALREADY_SENT` ölçülüyor VE hareket sayısının
   kımıldamadığı ayrıca ölçülüyor — tek başına durum kodu, reddin ETKİSİZ
   olduğunu söylemez.

3. **TERMİNAL OLMAYAN SATIRA DOKUNULMAZ.** `PENDING` (kuyrukta) ve `CLAIMED`
   (bir tüketici tarafından talep edilmiş) satırlar 409 alır. Bu, koşullu
   UPDATE'in `status IN (<terminal>)` yükleminin ÜRÜNÜDÜR; yüklem düşerse
   uç, uçuştaki bir işlemin altından satır çeker.

4. **KİRACI.** Başka firmanın olayı 404 alır ve — mutlu yol tek başına
   yetmediği için — o olayın durumunun DEĞİŞMEDİĞİ ayrıca ölçülür.

5. **DENETİM İZİ.** `requeued_by`/`requeued_at` SÜTUNU YOK (bu dilimde göç
   yok); kimin hangi olayı hangi durumdan geri aldığı `activity_logs`ta
   `field_event.requeued` satırı olarak durur ve `details` ÖNCEKİ durumu VE
   ÖNCEKİ `attempts` değerini taşır.

6. **GERİ ALINAN OLAY GERÇEKTEN AKAR.** `SKIPPED_TABAN_BILDIRILMEMIS` olayı,
   ürün kartına taban birim yazıldıktan sonra geri alınıyor ve ÜRETİM
   YOLUNUN KENDİSİ — `FIELD_STOCK_OUTBOX_ENABLED=true` ile açılan zamanlayıcı
   thread'i — onu `SENT` yapıyor ve stok hareketini yazıyor. Doğrudan
   `olaylari_isle` çağrısı bu bacakta BİLEREK kullanılmıyor: uç ile üretimde
   onu tüketecek şey arasındaki bağ, ancak bayrak AÇIKKEN ölçülebilir.

--- BU DOSYANIN İDDİA ETMEDİĞİ ----------------------------------------------

YARIŞ BURADA ÖLÇÜLEMEZ. SQLite yazmaları veritabanı düzeyinde seri hâle
getirir; "yeniden kuyruklama ile talebin yarışması" ancak gerçek eşzamanlı
oturumlar veren bir arka uçta oluşur. O bacak PG ikizinde
(`test_field_stok_tuketici_postgresql.py`) ölçülüyor ve orada olması bu
dosyanın bir eksiği değil, kulvar ayrımıdır.

Senaryolar TEK süreçte, KENDİ taze veritabanında koşar: göç zinciri, HTTP
katmanı ve tüketici aynı koşumda. Ortam varsayımı yoktur.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

#: Ucun İZİN VERDİĞİ durumlar, testte BAĞIMSIZ olarak yazılı. Router'dan
#: import edilseydi totoloji olurdu: demet oradan silinse test yine geçerdi.
BEKLENEN_IZINLI = (
    "SKIPPED_SOURCE_NOT_VISIBLE",
    "SKIPPED_NO_PRODUCT",
    "SKIPPED_TABAN_BILDIRILMEMIS",
    "DEAD",
)


def test_izin_farm_manage_OLCULDU() -> None:
    """Yazma ucu okuma yüzeyinin `farm.view`ine DÜŞMEZ.

    Okuma yüzeyinin kendi notu bunu şart koşuyordu: "yeniden kuyruklama ...
    geldiği gün kendi YAZMA iznini gerektirir — `farm.view` ona yetmez."
    Altı rolün altısı da `farm.view` taşıyor; uç oraya düşseydi kuyruğu
    GÖREBİLEN herkes onu OYNATABİLİR olurdu.

    İkinci yön aynı ölçümde: yol `/api/field` ile BAŞLIYOR, yani
    `_FARM_PATH_PREFIXES`ten düşseydi sessizce `field_service`e inerdi — bu
    depoda iki kez yaşanmış tuzak.
    """
    sys.path.insert(0, str(BACKEND))
    from app.auth import required_permission

    assert (
        required_permission("POST", "/api/field-integration-events/1/requeue")
        == "farm.manage"
    )
    # OKUMA aynı yolda `farm.view` KALIYOR: yazma ucu okumayı daraltmadı.
    assert required_permission("GET", "/api/field-integration-events") == "farm.view"


def test_izinli_kume_TUKETICIYE_BAGLI() -> None:
    """İzin verilen küme = terminal durumlar EKSİ `SENT`. Tek kaynaktan.

    Küme `failed_only` ekranını kuran demetin AYNISIDIR. İki ayrı yerde
    yazılsaydı, tüketici yeni bir terminal kova eklediğinde (C2
    `SKIPPED_TABAN_BILDIRILMEMIS`i ekledi) ekran onu GÖSTERİR ama uç geri
    ALAMAZDI — yani "düzeltilebilir" denen bir olay düzeltilemez kalırdı.
    """
    sys.path.insert(0, str(BACKEND))
    from app.field_stok_tuketici import (
        DURUM_BEKLIYOR,
        DURUM_UYGULANDI,
        TERMINAL_DURUMLAR,
    )
    from app.routers.entegrasyon_olaylari import _BEKLIYOR, TARLA

    assert TARLA.basarisiz_kovalar == BEKLENEN_IZINLI, TARLA.basarisiz_kovalar
    assert set(TARLA.basarisiz_kovalar) == set(TERMINAL_DURUMLAR) - {
        DURUM_UYGULANDI
    }, (TERMINAL_DURUMLAR, TARLA.basarisiz_kovalar)
    # Ucun yazdığı durum, tüketicinin SEÇTİĞİ durum olmak ZORUNDA. Ayrışırsa
    # geri alınan olay hiçbir zaman seçilmez ve uç sessizce hiçbir şey yapmaz.
    assert _BEKLIYOR == DURUM_BEKLIYOR, (_BEKLIYOR, DURUM_BEKLIYOR)


def test_deneme_sutunsuz_yuzey_UC_ALAMAZ() -> None:
    """`attempts` sütunu olmayan yüzeye bu uç KAPALI.

    Sıfırlanacak sayaç yoksa tavan gerekçesi de tutmaz; bayrak sessizce
    açılamasın diye koşul kayıt fonksiyonunda duruyor. `herd_integration_
    events`in `attempts` sütunu YOK (bkz. router başlığındaki sütun ölçümü).
    """
    sys.path.insert(0, str(BACKEND))
    import pytest

    from app.routers.entegrasyon_olaylari import (
        OlayYuzeyi,
        kaydet_yeniden_kuyrukla,
    )

    suru = OlayYuzeyi(
        alan="herd_probu",
        etiket="herd",
        yol="/herd-integration-events-probu",
        tablo="herd_integration_events",
        hata_sutunu="error",
        deneme_sutunu=None,
        islenme_sutunu=None,
        basarisiz_kovalar=("DEAD",),
        yeniden_kuyruklanabilir=True,
    )
    with pytest.raises(ValueError):
        kaydet_yeniden_kuyrukla(suru)


def test_katalogda_yeniden_kuyruklama_TIPI_VAR() -> None:
    """Denetim satırı yazılamazsa uç da düşer; tip kataloğa GİRMİŞ olmalı.

    `log_activity` katalog dışı bir `action_type`ı `ValueError` ile reddeder
    ve o istisna işlemi düşürür — yani katalog girdisi eksikse uç 500 verir,
    sessizce denetimsiz yeniden kuyruklamaz. Bu satır o girdiyi çiviler.
    """
    sys.path.insert(0, str(BACKEND))
    from app.activity_log import ACTION_TYPES, RESOURCE_TYPES

    assert "field_event.requeued" in ACTION_TYPES
    assert ACTION_TYPES["field_event.requeued"]
    assert "field_integration_event" in RESOURCE_TYPES


def _kos(kaynak: str, db_yolu: Path, imza: str, ek: dict[str, str] | None = None) -> str:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_yolu.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    if ek:
        env.update(ek)
    tamam = subprocess.run(
        [sys.executable, "-c", kaynak],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    assert imza in tamam.stdout, tamam.stdout
    return tamam.stdout


def test_yeniden_kuyruklama_sozlesmesi(tmp_path: Path) -> None:
    _kos(_SENARYO, tmp_path / "requeue.db", "REQUEUE-TAMAM")


def test_taban_duzeltilince_olay_UCTAN_UCA_AKIYOR(tmp_path: Path) -> None:
    """Bayrak AÇIK: geri alınan olayı ÜRETİM zamanlayıcısı `SENT` yapıyor."""
    _kos(
        _SENARYO_AKIS,
        tmp_path / "requeue-akis.db",
        "AKIS-TAMAM",
        {
            "FIELD_STOCK_OUTBOX_ENABLED": "true",
            # Varsayılan 30 sn; test bir döngüyü BEKLEYEBİLMELİ.
            "FIELD_STOCK_OUTBOX_INTERVAL_SECONDS": "1",
        },
    )


_ORTAK = r'''
from decimal import Decimal

from sqlalchemy import text as _sql
from fastapi.testclient import TestClient

from app.db import SessionLocal

ADMIN_PW = 'OlayKuyruk!12345'

#: Ucun izin verdigi kova sayisi; alt surecte BAGIMSIZ yazili (dis dosyadaki
#: `BEKLENEN_IZINLI` ile ayni kume).
BEKLENEN_IZINLI_ICERIDE = ('SKIPPED_SOURCE_NOT_VISIBLE', 'SKIPPED_NO_PRODUCT',
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


def urun_yaz(client, h, ad, kod):
    r = client.post('/api/products', headers=h,
                    json={'name':ad,'product_code':kod,'unit':'kg'})
    assert r.status_code == 201, r.text
    return int(r.json()['id'])


def sezon_yaz(client, h, kod, urun_id=None):
    """Çiftlik + parsel + sezon. `urun_id` None ise sezonun ÜRÜNÜ YOK."""
    c = client.post('/api/farms', headers=h, json={'code':kod,'name':'C '+kod})
    assert c.status_code == 201, c.text
    p = client.post('/api/farm-parcels', headers=h,
                    json={'farm_id':c.json()['id'],'code':kod+'-p','name':'P',
                          'area_decare':'50.0000'})
    assert p.status_code == 201, p.text
    govde = {'parcel_id':p.json()['id'],'season_year':2026,'crop':'Bugday',
             'started_on':'2026-03-01','planted_area_decare':'40.0000'}
    if urun_id is not None:
        govde['product_id'] = urun_id
    s = client.post('/api/crop-seasons', headers=h, json=govde)
    assert s.status_code == 201, s.text
    return int(s.json()['id'])


def hasat_yaz(client, h, sezon_id, miktar='50.0000'):
    r = client.post('/api/field-harvests', headers=h,
                    json={'season_id':sezon_id,'harvested_on':'2026-08-15',
                          'quantity':miktar,'unit':'kg'})
    assert r.status_code == 201, r.text
    return int(r.json()['id'])


def olay_satiri(cid, kaynak_tipi, kaynak_id):
    with SessionLocal() as db:
        return db.execute(_sql(
            "SELECT id,status,attempts,last_error,processed_at "
            "FROM field_integration_events WHERE company_id=:c "
            "AND source_type=:t AND source_id=:s"),
            {"c":cid,"t":kaynak_tipi,"s":kaynak_id}).mappings().one()


def olay_durumu(cid, olay_id):
    with SessionLocal() as db:
        return db.execute(_sql(
            "SELECT status FROM field_integration_events "
            "WHERE company_id=:c AND id=:i"),
            {"c":cid,"i":olay_id}).scalar_one()


def hareket_sayisi():
    with SessionLocal() as db:
        return int(db.execute(_sql(
            "SELECT COUNT(*) FROM stock_movements "
            "WHERE reference_type='field_integration_event'")).scalar_one())


# --- UCUN GERCEKTEN KOSTURDUGU SQL ----------------------------------------
#
# NEDEN GEREKLI. Iki yuklem SQLite'ta DAVRANIŞLA olculemez, cunku ikisi de
# ON SELECT kapisi tarafindan ONCEDEN kapatiliyor:
#
#   * `company_id=:cid` (UPDATE'te)  — capraz kiraci istek zaten SELECT'te
#     404 aliyor; UPDATE'ten dusurulse bile hicbir senaryo kirmizi olmaz.
#   * `status IN (<terminal>)`       — `PENDING`/`CLAIMED` satir zaten
#     SELECT'ten sonraki dalda 409 aliyor; yuklem YALNIZ iki ifade ARASINDA
#     satir degisirse konusur ve o pencere SQLite'ta ACILMAZ (yazmalar
#     veritabani duzeyinde seri).
#
# Ikisi de SAVUNMANIN IKINCI HATTI ve ikisi de gercek: yuklemin DAVRANIS
# kaniti PG ikizinde (`test_field_stok_tuketici_postgresql.py`, YENIDEN
# KUYRUKLAMA bacagi) aliniyor. Burada YAPISAL olarak civileniyor — ucun
# CALISTIRDIGI metin yakalanip okunuyor, kaynak dosya OKUNMUYOR: bir gun
# sorgu baska bir yardimcidan gelse bile bu kapi ayni seyi olcer.
class SqlKaydi:
    def __init__(self):
        self.ifadeler = []
        from sqlalchemy import event as _event
        from app.db import engine as _engine
        self._engine = _engine
        _event.listen(_engine, "before_cursor_execute", self._al)

    def _al(self, conn, cursor, statement, parameters, context, executemany):
        if "field_integration_events" in statement:
            self.ifadeler.append(" ".join(statement.split()))

    def temizle(self):
        self.ifadeler = []

    def guncellemeler(self):
        return [i for i in self.ifadeler if i.upper().startswith("UPDATE")]
'''


_SENARYO = r'''
''' + _ORTAK + r'''
from app.field_stok_tuketici import olaylari_isle
from app.main import app


def tuketiciyi_kostur(cid):
    with SessionLocal() as db:
        sayac = olaylari_isle(db, cid)
        db.commit()
    return sayac


with TestClient(app) as client:
    h, body = admin_headers(client)
    cid = int(h['X-Company-ID'])

    # ---------------------------------------------------------------------
    # DÖRT TERMİNAL KOVAYI GERÇEKTEN ÜRET
    # ---------------------------------------------------------------------
    # 1) SKIPPED_NO_PRODUCT — sezonun ürünü bildirilmemiş.
    s_urunsuz = sezon_yaz(client, h, 'A1')
    hs_urunsuz = hasat_yaz(client, h, s_urunsuz)

    # 2) SKIPPED_TABAN_BILDIRILMEMIS — ürün BELLİ, taban birimi bildirilmemiş.
    urun = urun_yaz(client, h, 'Bugday', 'BGD-1')
    s_tabansiz = sezon_yaz(client, h, 'A2', urun_id=urun)
    hs_tabansiz = hasat_yaz(client, h, s_tabansiz)

    # 3) DEAD — deneme tavanı. `attempts` TAVANIN BİR ALTINA çekiliyor;
    #    tüketici `attempts + 1 > 3` ile olayı tavan kolundan ölü yazar.
    s_olu = sezon_yaz(client, h, 'A3')
    hs_olu = hasat_yaz(client, h, s_olu)
    olu_olay = int(olay_satiri(cid, 'field_harvest', hs_olu)['id'])
    with SessionLocal() as db:
        db.execute(_sql("UPDATE field_integration_events SET attempts=3 "
                        "WHERE company_id=:c AND id=:i"),
                   {"c":cid,"i":olu_olay})
        db.commit()

    # 4) SKIPPED_SOURCE_NOT_VISIBLE — kaynak satırı YOK. Bu kovanın uygulama
    #    yolu yok (yazıcı kaynağıyla birlikte yazar), o yüzden olay satırı
    #    doğrudan yazılıyor; tüketici onu YETİM olarak bulup kovaya atacak.
    with SessionLocal() as db:
        db.execute(_sql(
            "INSERT INTO field_integration_events (company_id,source_type,"
            "source_id,target,idempotency_key,status,attempts,created_at,"
            "updated_at) VALUES (:c,'field_harvest',999777,'stock',"
            "'field_harvest:999777:stock','PENDING',0,:z,:z)"),
            {"c":cid,"z":'2026-08-01T00:00:00'})
        db.commit()
    yetim_olay = int(olay_satiri(cid, 'field_harvest', 999777)['id'])

    # 5) SENT — ürün BELLİ ve taban birimi YAZILI: olay hareketi yazıp biter.
    urun_tam = urun_yaz(client, h, 'Arpa', 'ARP-1')
    yaz = client.put('/api/products/%d' % urun_tam, headers=h,
                     json={'name':'Arpa','unit':'kg','base_unit':'kg'})
    assert yaz.status_code == 200, yaz.text
    s_tam = sezon_yaz(client, h, 'A5', urun_id=urun_tam)
    hs_tam = hasat_yaz(client, h, s_tam)

    sayac = tuketiciyi_kostur(cid)
    print('SAYAC %r' % (sayac,))

    # Kovalar ELLE yazılmadı: tüketicinin YAZDIĞI durumlar okunuyor.
    beklenen = {
        hs_urunsuz: 'SKIPPED_NO_PRODUCT',
        hs_tabansiz: 'SKIPPED_TABAN_BILDIRILMEMIS',
        hs_olu: 'DEAD',
        999777: 'SKIPPED_SOURCE_NOT_VISIBLE',
        hs_tam: 'SENT',
    }
    olaylar = {}
    for kaynak_id, durum in beklenen.items():
        satir = olay_satiri(cid, 'field_harvest', kaynak_id)
        assert satir['status'] == durum, (kaynak_id, durum, dict(satir))
        olaylar[durum] = dict(satir)
    print('KOVALAR %r' % ({k:v['status'] for k,v in olaylar.items()},))

    # Tavan kolunun `attempts`i: 4 (3 + 1). Bu sayı, aşağıdaki SIFIRLAMA
    # kararının gerekçesinin ta kendisi — korunsaydı geri alınan olay bir
    # sonraki döngüde 5 > 3 ile YENİDEN ölürdü.
    assert int(olaylar['DEAD']['attempts']) == 4, olaylar['DEAD']
    hareket_once = hareket_sayisi()
    assert hareket_once == 1, ('SENT olay hareket yazmadı; prob geçersiz',
                               hareket_once)

    kayit = SqlKaydi()

    # ---------------------------------------------------------------------
    # 1) HER TERMİNAL KOVA GERİ ALINIYOR
    # ---------------------------------------------------------------------
    for durum in ('SKIPPED_NO_PRODUCT', 'SKIPPED_TABAN_BILDIRILMEMIS',
                  'DEAD', 'SKIPPED_SOURCE_NOT_VISIBLE'):
        kayit.temizle()
        satir = olaylar[durum]
        oid = int(satir['id'])
        r = client.post('/api/field-integration-events/%d/requeue' % oid,
                        headers=h)
        assert r.status_code == 200, (durum, r.text)
        govde = r.json()
        assert govde['previous_status'] == durum, govde
        assert govde['status'] == 'PENDING', govde
        assert govde['id'] == oid, govde
        with SessionLocal() as db:
            sonra = db.execute(_sql(
                "SELECT status,attempts,last_error,processed_at FROM "
                "field_integration_events WHERE company_id=:c AND id=:i"),
                {"c":cid,"i":oid}).mappings().one()
        print('GERI_ALINDI %s -> %r' % (durum, dict(sonra)))
        assert sonra['status'] == 'PENDING', (durum, dict(sonra))
        # `attempts` SIFIRLANIR: aksi hâlde tavanı dolmuş olay geri alınmış
        # SAYILIR ama bir sonraki döngüde hemen yeniden ölürdü.
        assert int(sonra['attempts']) == 0, (durum, dict(sonra))
        # `last_error` KORUNUR: geri alınan olayın NEDEN düştüğü kaybolmaz.
        assert sonra['last_error'] == satir['last_error'], (durum, dict(sonra))
        assert sonra['last_error'], (durum, 'gerekce metni bos', dict(sonra))
        # `processed_at` KORUNUR: `attempts=0` yazıldıktan sonra geri alınmış
        # satırı HİÇ denenmemiş satırdan ayıran TEK sütun budur.
        assert sonra['processed_at'] is not None, (durum, dict(sonra))
        # UCUN KOSTURDUGU UPDATE: iki yuklem de METINDE. Ikisi de bu kulvarda
        # DAVRANISLA olculemez (bkz. `SqlKaydi` basligi); yapisal kapi burada.
        guncelleme = kayit.guncellemeler()
        assert len(guncelleme) == 1, (durum, kayit.ifadeler)
        print('UPDATE %s' % guncelleme[0])
        # Metin SURUCU duzeyinde yakalaniyor, yani DEGERLER yer tutucudur
        # (`?`) ve isimleri gorunmez; olculen sey YUKLEMIN VARLIGIDIR.
        assert 'WHERE company_id=' in guncelleme[0], (durum, guncelleme[0])
        assert 'status IN (' in guncelleme[0], (durum, guncelleme[0])
        # Yer tutucu SAYISI izin verilen kova sayisi kadar: kume kirpilirsa
        # (ornegin C2'nin ekledigi kova dusurulurse) bu satir kirilir.
        icerik = guncelleme[0].split('status IN (')[1].split(')')[0]
        assert icerik.count(',') + 1 == len(BEKLENEN_IZINLI_ICERIDE), (
            durum, guncelleme[0])

    # ---------------------------------------------------------------------
    # 2) `SENT` GERİ ALINAMAZ — VE REDDİN ETKİSİ YOK
    # ---------------------------------------------------------------------
    gonderilmis = int(olaylar['SENT']['id'])
    r = client.post('/api/field-integration-events/%d/requeue' % gonderilmis,
                    headers=h)
    assert r.status_code == 409, r.text
    ayrinti = r.json()['detail']
    print('SENT_RED %r' % (ayrinti,))
    assert ayrinti['code'] == 'EVENT_ALREADY_SENT', ayrinti
    assert olay_durumu(cid, gonderilmis) == 'SENT', 'RED SATIRI OYNATMIS'
    assert hareket_sayisi() == hareket_once, 'RED HAREKET YAZMIS/SILMIS'

    # ---------------------------------------------------------------------
    # 3) TERMİNAL OLMAYAN SATIR: `PENDING` ve `CLAIMED`
    # ---------------------------------------------------------------------
    # Yukarıda geri alınan olaylardan biri artık PENDING.
    bekleyen = int(olaylar['DEAD']['id'])
    r = client.post('/api/field-integration-events/%d/requeue' % bekleyen,
                    headers=h)
    assert r.status_code == 409, r.text
    assert r.json()['detail']['code'] == 'EVENT_NOT_TERMINAL', r.text
    assert olay_durumu(cid, bekleyen) == 'PENDING', 'RED SATIRI OYNATMIS'

    with SessionLocal() as db:
        db.execute(_sql("UPDATE field_integration_events SET status='CLAIMED' "
                        "WHERE company_id=:c AND id=:i"),
                   {"c":cid,"i":bekleyen})
        db.commit()
    r = client.post('/api/field-integration-events/%d/requeue' % bekleyen,
                    headers=h)
    assert r.status_code == 409, r.text
    print('CLAIMED_RED %r' % (r.json()['detail'],))
    assert r.json()['detail']['code'] == 'EVENT_NOT_TERMINAL', r.text
    # UÇUŞTAKİ İŞLEMİN ALTINDAN SATIR ÇEKİLMEDİ.
    assert olay_durumu(cid, bekleyen) == 'CLAIMED', 'TALEP EDILMIS SATIR OYNADI'
    with SessionLocal() as db:
        db.execute(_sql("UPDATE field_integration_events SET status='DEAD' "
                        "WHERE company_id=:c AND id=:i"),
                   {"c":cid,"i":bekleyen})
        db.commit()

    # ---------------------------------------------------------------------
    # 4) ÇAPRAZ KİRACI: 404 ve DOKUNULMAMIŞ SATIR
    # ---------------------------------------------------------------------
    b = client.post('/api/companies', headers=h, json={'name':'Kuyruk B'})
    assert b.status_code == 201, b.text
    hb = dict(h, **{'X-Company-ID': str(int(b.json()['id']))})
    r = client.post('/api/field-integration-events/%d/requeue' % bekleyen,
                    headers=hb)
    assert r.status_code == 404, r.text
    print('CAPRAZ %d' % r.status_code)
    assert olay_durumu(cid, bekleyen) == 'DEAD', 'CAPRAZ KIRACI SATIRI OYNATMIS'
    # Var olmayan olay da 404 (kimlik SIZDIRILMIYOR: iki hâl AYNI cevap).
    assert client.post('/api/field-integration-events/99887766/requeue',
                       headers=h).status_code == 404

    # ---------------------------------------------------------------------
    # 5) DENETİM İZİ: `requeued_by` sütunu yok, satır var
    # ---------------------------------------------------------------------
    with SessionLocal() as db:
        kayitlar = db.execute(_sql(
            "SELECT resource_id,summary,details,user_id FROM activity_logs "
            "WHERE company_id=:c AND action_type='field_event.requeued' "
            "ORDER BY id"), {"c":cid}).mappings().all()
    print('DENETIM %d %r' % (len(kayitlar), [k['summary'] for k in kayitlar]))
    # Dört başarılı geri alma, dört satır. Reddedilenler satır YAZMAZ.
    assert len(kayitlar) == 4, kayitlar
    for kayit in kayitlar:
        assert kayit['user_id'], ('denetim satiri KULLANICISIZ', dict(kayit))
        assert 'previous_status' in (kayit['details'] or ''), dict(kayit)
        assert 'previous_attempts' in (kayit['details'] or ''), dict(kayit)
    olu_kayit = [k for k in kayitlar
                 if int(k['resource_id']) == int(olaylar['DEAD']['id'])]
    assert len(olu_kayit) == 1, kayitlar
    # SATIRDAN SİLİNEN `attempts` DEFTERDE DURUYOR: 4.
    assert '"previous_attempts": 4' in olu_kayit[0]['details'], olu_kayit
    assert '"previous_status": "DEAD"' in olu_kayit[0]['details'], olu_kayit

    # ---------------------------------------------------------------------
    # 6) GERİ ALINAN OLAY TÜKETİCİ TARAFINDAN SEÇİLİYOR
    # ---------------------------------------------------------------------
    # Tavanı dolmuş olay yeniden PENDING; `attempts` sıfırlandığı için bu
    # döngüde tavan koluna DÜŞMEZ, kaynağına bakılır.
    with SessionLocal() as db:
        db.execute(_sql("UPDATE field_integration_events SET status='PENDING',"
                        "attempts=0 WHERE company_id=:c AND id=:i"),
                   {"c":cid,"i":bekleyen})
        db.commit()
    sayac2 = tuketiciyi_kostur(cid)
    print('SAYAC2 %r' % (sayac2,))
    assert int(sayac2['girdi']) >= 1, sayac2
    # Ürünü hâlâ bildirilmemiş sezonun hasadı: ölü DEĞİL, ÜRÜNSÜZ. Yani olay
    # gerçekten YENİDEN İŞLENDİ; tavan kolundan sessizce geçmedi.
    assert olay_durumu(cid, bekleyen) == 'SKIPPED_NO_PRODUCT', (
        'geri alinan olay YENIDEN ISLENMEDI', olay_durumu(cid, bekleyen))

print('REQUEUE-TAMAM')
'''


_SENARYO_AKIS = r'''
''' + _ORTAK + r'''
import time

from app.config import settings
from app.main import app

# BAYRAK GERÇEKTEN AÇIK: uç ile onu üretimde tüketecek şey arasındaki bağ
# ancak burada ölçülebilir. Doğrudan `olaylari_isle` çağrısı bu dosyanın
# DİĞER senaryosunda kullanılıyor; burada BİLEREK kullanılmıyor.
assert settings.field_stock_outbox_enabled is True, 'BAYRAK ACIK DEGIL'


def bekle(kosul, saniye=90.0):
    vade = time.monotonic() + saniye
    while time.monotonic() < vade:
        deger = kosul()
        if deger is not None:
            return deger
        time.sleep(0.2)
    return None


with TestClient(app) as client:
    h, body = admin_headers(client)
    cid = int(h['X-Company-ID'])

    # Ürün BELLİ, taban birimi BİLDİRİLMEMİŞ: olay `SKIPPED_TABAN_BILDIRILMEMIS`.
    urun = urun_yaz(client, h, 'Bugday', 'BGD-AKIS')
    sezon = sezon_yaz(client, h, 'AK1', urun_id=urun)
    hasat = hasat_yaz(client, h, sezon, miktar='1000.0000')

    durum = bekle(lambda: (lambda s: s if s != 'PENDING' else None)(
        olay_satiri(cid, 'field_harvest', hasat)['status']))
    print('ILK_DURUM %r' % (durum,))
    assert durum == 'SKIPPED_TABAN_BILDIRILMEMIS', durum
    olay = int(olay_satiri(cid, 'field_harvest', hasat)['id'])
    assert hareket_sayisi() == 0, 'kova hareket yazmis'

    # ÇARE ÜRÜN KARTIDIR (belgenin kendi cümlesi): taban birim yazılıyor.
    yaz = client.put('/api/products/%d' % urun, headers=h,
                     json={'name':'Bugday','unit':'kg','base_unit':'kg'})
    assert yaz.status_code == 200, yaz.text

    # Taban düzeldi ama olay TERMİNAL: tüketici onu bir daha ASLA seçmez.
    # Koşul 3'ün var olma sebebi tam olarak bu satırdır.
    time.sleep(3.0)
    assert olay_durumu(cid, olay) == 'SKIPPED_TABAN_BILDIRILMEMIS', (
        'olay kendiliginden geri gelmis; prob gecersiz')

    r = client.post('/api/field-integration-events/%d/requeue' % olay, headers=h)
    assert r.status_code == 200, r.text
    print('GERI_ALINDI %r' % (r.json(),))

    # ÜRETİM YOLU: zamanlayıcı thread'i olayı seçip `SENT` yapmalı.
    son = bekle(lambda: (lambda s: s if s not in ('PENDING','CLAIMED') else None)(
        olay_durumu(cid, olay)))
    print('SON_DURUM %r HAREKET %r' % (son, hareket_sayisi()))
    assert son == 'SENT', ('geri alinan olay AKMADI', son)
    assert hareket_sayisi() == 1, hareket_sayisi()
    with SessionLocal() as db:
        miktar = db.execute(_sql(
            "SELECT quantity FROM stock_movements WHERE "
            "reference_type='field_integration_event' AND reference_id=:i"),
            {"i":olay}).scalar_one()
    # 1000 kg, taban birim KG: katsayı 1. Ham yazım da 1000 verirdi, ama bu
    # bacağın iddiası çeviri değil AKIŞ — hareketin YAZILDIĞI ve olayın
    # kapandığı.
    assert Decimal(str(miktar)) == Decimal('1000.0000'), miktar

print('AKIS-TAMAM')
'''
