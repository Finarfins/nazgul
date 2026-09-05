"""C2 — KANTAR NETİ STOĞA. Fişin defterde açtığı FARK, sayıyla çivilenmiş.

Kardeş dosya `test_kantar_fisi_defter.py` SENKRON yolun sessizliğini ölçüyor
("POST hareket yazmaz"). BU DOSYA tüketicinin yazdığı DÜZELTMENİN SAYISINI
ölçüyor. İkisi ayrı çünkü ayrı şeyler kırılır: biri istek yolunda bir sızıntı,
öteki aritmetikte bir hata.

--- ÖLÇÜLEN FORMÜL ----------------------------------------------------------

    delta = Σ(fiş netleri, TABAN birimde)
          − hasadın TABAN miktarı
          − BU HASAT için daha önce yazılmış fiş düzeltmeleri

Üçüncü terim olmadan ikinci fiş birinciyi TEKRAR sayar. Aşağıdaki SENARYO
3 tam olarak o terimi ölçüyor: kaldırılırsa delta 500 değil 1200 çıkar.

--- NİYE MİKTAR DEĞİL FARK --------------------------------------------------

Hasat yazıldığında deftere hasadın BİLDİRDİĞİ miktar girdi. Kantar sonradan
başka bir sayı söylüyor. "Fişin netini de yaz" aynı ürünü İKİ KEZ üretirdi;
"hasadın satırını güncelle" ise geçmişi bugünün inancına göre yeniden yazardı
(sahip kuralı 1: düzeltme YENİ BİR SATIRDIR). Kalan tek doğru cevap FARKTIR.

--- ARİTMETİK, AÇIK AÇIK ----------------------------------------------------

SENARYO 1  hasat 1000 KG, fiş yok
           -> hareket +1000.0000

SENARYO 2  fiş: 2 TON brüt, kesinti %10 + %5 (TOPLAMSAL, sıralı DEĞİL)
           taban        = 2 × 1000            = 2000.0000 KG
           net          = 2000 − 2000×0.15    = 1700.0000 KG
           delta        = 1700 − 1000 − 0     =  700.0000  -> hareket +700

SENARYO 3  ikinci fiş: 500 KG brüt, kesinti YOK
           fiş netleri  = 1700 + 500          = 2200.0000 KG
           zaten yazılan= 700
           delta        = 2200 − 1000 − 700   =  500.0000  -> hareket +500
           DEFTER TOPLAMI = 1000 + 700 + 500  = 2200 = fişlerin neti. ✓

SENARYO 4  aynı olay YENİDEN tüketilir -> delta 0 -> SATIR YOK
SENARYO 5  ürünün `base_unit`i YOK      -> SKIPPED_TABAN_BILDIRILMEMIS, 0 satır
SENARYO 6  hasat 2 "ton", taban "kg"    -> hareket 2000 (1000×), 2 DEĞİL
SENARYO 7  A'nın fişi B'nin stoğuna DOKUNMAZ
SENARYO 8  bayrak KAPALI -> olay PENDING, hareket YOK

--- SENARYO 6 BİR DÜZELTME DEĞİL, BİR KAPI ---------------------------------

Hasat yolu `field_harvests.quantity`yi HAM yazıyordu ve bu ölçülmüş bir 1000×
riskiydi: hareket defteri birim TAŞIMIYOR, miktarın ürünün TABAN biriminde
olduğu VARSAYILIYORDU ve varsayım hiçbir yerde sınanmıyordu. Bayrak üretimde
KAPALI olduğu için CANLI VERİ YOK — yani düzeltilecek bir geçmiş de yok. Bu
senaryo, sessizce ham yazmaya dönüşü kırmızı yapar.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# STATİK KAPI — veritabanı yok.
# ---------------------------------------------------------------------------


def test_tuketici_stok_hareketini_ASLA_guncellemez_silmez() -> None:
    """Tüketici `stock_movements`a yalnız INSERT eder (sahip kuralı 1).

    NİYE KAPI: düzeltmeyi "hasadın satırını UPDATE ederek" yapmak, bu dilimin
    en cazip ve en yanlış çözümüdür — tek satır, temiz görünür ve defteri
    O GÜN neye inanıldığının kanıtı olmaktan çıkarır. DELETE aynısının daha
    sertidir. İkisi de burada, davranış testinden ÖNCE kırılır.

    NE İDDİA ETMEZ: tüketicinin DOLAYLI bir yoldan (başka modül üzerinden)
    güncelleme yapmadığını. Ölçtüğü şey, hatanın pratikte aldığı biçim olan
    doğrudan ifadedir. `adjust_warehouse_stock`/`sync_product_stock`
    `warehouse_stocks`/`products` TOPLAMLARINI günceller — DEFTERİ değil; bu
    kapı `stock_movements` sözcüğünü taşıyan ifadeleri arar.
    """
    kaynak = (BACKEND / "app" / "field_stok_tuketici.py").read_text(encoding="utf-8")
    suclu = [
        satir.strip()
        for satir in re.findall(
            r"(?is)\b(?:UPDATE|DELETE\s+FROM)\s+stock_movements\b[^\"']*", kaynak
        )
    ]
    assert suclu == [], (
        "Tüketici stok defterini GÜNCELLİYOR ya da SİLİYOR: "
        f"{suclu}. Düzeltme YENİ BİR SATIRDIR; defterin üzerine yazmak, o gün "
        "neye inanıldığının kanıtını yok eder (sahip kuralı 1)."
    )


# ---------------------------------------------------------------------------
# DAVRANIŞ — taze veritabanı, göç zinciri, HTTP katmanı, tüketici.
# ---------------------------------------------------------------------------


def run_neti_stoga_smoke(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-c", _SMOKE],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "KANTAR NETI STOGA TAMAM" in completed.stdout, completed.stdout


def test_kantar_neti_stoga_sqlite(tmp_path: Path) -> None:
    run_neti_stoga_smoke(f"sqlite:///{(tmp_path / 'kantar-neti.db').as_posix()}")


_SMOKE = r'''
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text as _sql

from app.config import settings
from app.db import SessionLocal
from app.field_stok_tuketici import olaylari_isle
from app.main import app

# AILENIN ORTAK SIFRESI — bkz. `test_kantar_fisi_defter.py`. PG ikizinde ayni
# veritabaninda ardisik kosuluyor ve bootstrap `admin`in sifresi paylasiliyor.
ADMIN_PW = 'KantarFisi!123'
URUN = 4201          # taban birimi KG olan urun
URUN_TABANSIZ = 4202  # taban birimi YOK — SENARYO 5
URUN_TON = 4203      # taban birimi KG, hasadi "ton" ile giriliyor — SENARYO 6


def admin_headers(client):
    for candidate in ('admin123', ADMIN_PW):
        login = client.post('/api/auth/login',
                            json={'username':'admin','password':candidate})
        if login.status_code == 200:
            break
    assert login.status_code == 200, login.text
    body = login.json()
    h = {'Authorization':'Bearer '+body['access_token'],
         'X-Company-ID':str(body['companies'][0]['id'])}
    if candidate != ADMIN_PW:
        ch = client.post('/api/auth/change-password', headers=h,
                         json={'current_password':candidate,'new_password':ADMIN_PW})
        assert ch.status_code == 200, ch.text
        h['Authorization'] = 'Bearer '+ch.json()['access_token']
    return h, int(body['companies'][0]['id'])


def urun_yaz(cid, urun_id, taban):
    """Urun HAM SQL ile: bu dosyanin konusu urun ucu degil, defter.

    `active` BOOLEAN olarak baglaniyor — PostgreSQL boolean sutununa tamsayi
    kabul etmez (kardes dosyada PG ikizi bu kusuru yakalamisti).
    """
    with SessionLocal() as db:
        db.execute(_sql(
            "INSERT INTO products (id,name,purchase_price,sale_price,vat_rate,"
            "stock,unit,price_per,active,critical_stock,minimum_stock,company_id,"
            "base_unit) VALUES (:i,'Bugday',0,0,0,'0.0000','kg','unit',:a,0,0,"
            ":c,:b)"),
            {'i': urun_id, 'c': cid, 'a': True, 'b': taban})
        db.commit()


def hareketler(cid, urun_id):
    """BU URUNE ait defter satirlari, KIRACI KAPSAMLI, id sirasinda."""
    with SessionLocal() as db:
        return [dict(r) for r in db.execute(_sql(
            """SELECT id,quantity,reference_type,reference_id,note,warehouse_id
               FROM stock_movements
               WHERE company_id = :c AND product_id = :p ORDER BY id"""),
            {'c': cid, 'p': urun_id}).mappings().all()]


def miktarlar(cid, urun_id):
    return [Decimal(str(s['quantity'])) for s in hareketler(cid, urun_id)]


def olay(cid, fis_id):
    with SessionLocal() as db:
        return db.execute(_sql(
            """SELECT id,status,last_error FROM field_integration_events
               WHERE company_id=:c AND source_type='field_harvest_ticket'
                 AND source_id=:s"""), {'c': cid, 's': fis_id}).mappings().first()


def tuket(cid):
    with SessionLocal() as db:
        sayac = olaylari_isle(db, cid)
        db.commit()
        return sayac


with TestClient(app) as client:
    h, cid = admin_headers(client)

    # BAYRAK: uretimde KAPALI ve bu kosumda da KAPALI. Tuketici asagida ELLE
    # cagriliyor; zamanlayici HIC calismiyor. SENARYO 8'in olcumu budur.
    assert settings.field_stock_outbox_enabled is False, (
        'Bu kosum bayragin KAPALI oldugu varsayimi uzerine kurulu')

    # Bekleyenleri tuket ve TABANI al: PG ikizinde ayni veritabani ve ayni
    # firma paylasiliyor, sayaclar sifirdan baslamayabilir.
    tuket(cid)

    urun_yaz(cid, URUN, 'KG')
    urun_yaz(cid, URUN_TABANSIZ, None)
    urun_yaz(cid, URUN_TON, 'KG')

    ciftlik = client.post('/api/farms', headers=h,
                          json={'code':'kn','name':'Kantar Net'}).json()
    parsel = client.post('/api/farm-parcels', headers=h,
                         json={'farm_id':ciftlik['id'],'code':'knp','name':'P',
                               'area_decare':'100.0000'}).json()

    def sezon_ac(kod, urun_id, yil):
        p = client.post('/api/farm-parcels', headers=h,
                        json={'farm_id':ciftlik['id'],'code':kod,'name':kod,
                              'area_decare':'100.0000'}).json()
        cevap = client.post('/api/crop-seasons', headers=h,
                            json={'parcel_id':p['id'],'season_year':yil,
                                  'crop':'Bugday','product_id':urun_id,
                                  'started_on':'2026-03-01',
                                  'planted_area_decare':'100.0000'})
        assert cevap.status_code == 201, cevap.text
        return cevap.json()

    def hasat_ac(sezon, miktar, birim='KG'):
        cevap = client.post('/api/field-harvests', headers=h,
                            json={'season_id':sezon['id'],
                                  'harvested_on':'2026-07-10',
                                  'quantity':miktar,'unit':birim})
        assert cevap.status_code == 201, cevap.text
        return cevap.json()

    def fis_ac(hasat, brut, birim, kesintiler, no=None):
        govde = {'harvest_id':hasat['id'],'gross_entered_quantity':brut,
                 'entered_unit':birim,
                 'deductions':[{'label':'K%d' % i,'rate_percent':o}
                               for i, o in enumerate(kesintiler)]}
        if no:
            govde['ticket_no'] = no
        cevap = client.post('/api/field-harvest-tickets', headers=h, json=govde)
        assert cevap.status_code == 201, cevap.text
        return cevap.json()

    # ================= SENARYO 1: FISSIZ HASAT -> +1000 ===================
    sezon = sezon_ac('kn1', URUN, 2026)
    hasat = hasat_ac(sezon, '1000')
    sayac = tuket(cid)
    assert sayac['SENT'] == 1, sayac
    assert miktarlar(cid, URUN) == [Decimal('1000.0000')], hareketler(cid, URUN)

    # ================= SENARYO 2: 2 TON, %10+%5 -> DELTA +700 =============
    # 2 TON = 2000 KG taban; net = 2000 - 2000*0.15 = 1700; 1700-1000 = 700.
    fis1 = fis_ac(hasat, '2', 'TON', ['10', '5'], no='KN-1')
    assert fis1['base_quantity'] == '2000.0000', fis1
    # OKUMA YUZEYININ NETI GIRILEN BIRIMDE (1.7 TON), DEFTERINKI TABANDA
    # (1700 KG). Ayni formul, ayri olcek — ve bu bir KARARDIR: okuma yuzeyi
    # kagidin dilinde konusur (operator TON yazdi, TON gorur), defter ise
    # urunun TABAN biriminde tutulur. Ikisi burada YAN YANA civileniyor ki
    # biri otekine kaydiginda (or. defter 1.7 yazmaya baslasa) kirmizi olsun.
    assert fis1['derived_net_quantity'] == '1.7000', fis1

    # SENKRON YOL SESSIZ (kardes dosyanin iddiasi, burada da dogrulanıyor).
    assert miktarlar(cid, URUN) == [Decimal('1000.0000')], hareketler(cid, URUN)
    assert olay(cid, fis1['id'])['status'] == 'PENDING', olay(cid, fis1['id'])

    sayac = tuket(cid)
    assert sayac['SENT'] == 1, sayac
    assert miktarlar(cid, URUN) == [Decimal('1000.0000'), Decimal('700.0000')], (
        'DELTA 700 DEGIL', hareketler(cid, URUN))
    duzeltme = hareketler(cid, URUN)[1]
    assert duzeltme['reference_type'] == 'field_integration_event', duzeltme
    assert duzeltme['reference_id'] == olay(cid, fis1['id'])['id'], duzeltme

    # ================= SENARYO 3: IKINCI FIS 500 KG %0 -> DELTA +500 ======
    # Fis netleri 1700+500 = 2200; zaten yazilan 700; 2200-1000-700 = 500.
    # `zaten_duzeltilmis` terimi DUSERSE burasi 1200 olur ve kirmizi yanar.
    fis2 = fis_ac(hasat, '500', 'KG', [], no='KN-2')
    assert fis2['derived_net_quantity'] == '500.0000', fis2
    sayac = tuket(cid)
    assert sayac['SENT'] == 1, sayac
    assert miktarlar(cid, URUN) == [
        Decimal('1000.0000'), Decimal('700.0000'), Decimal('500.0000')], (
        'IKINCI FIS BIRINCIYI TEKRAR SAYDI', hareketler(cid, URUN))
    # DEFTER TOPLAMI = FISLERIN NETI. Iddianin tamami tek satirda.
    assert sum(miktarlar(cid, URUN)) == Decimal('2200.0000'), hareketler(cid, URUN)

    # ================= SENARYO 4: AYNI OLAY YENIDEN -> SATIR YOK ==========
    # Olay ELLE `PENDING`e cekilip yeniden tuketiliyor: gercek bir tekrar
    # teslimin (at-least-once) taklidi. Delta artik SIFIR cunku kendi yazdigi
    # duzeltme `zaten_duzeltilmis` icinde GORUNUYOR.
    onceki = hareketler(cid, URUN)
    with SessionLocal() as db:
        db.execute(_sql(
            "UPDATE field_integration_events SET status='PENDING' "
            "WHERE company_id=:c AND id=:i"),
            {'c': cid, 'i': olay(cid, fis2['id'])['id']})
        db.commit()
    sayac = tuket(cid)
    assert sayac['SENT'] == 1, sayac
    assert hareketler(cid, URUN) == onceki, (
        'TEKRAR TESLIM IKINCI BIR SATIR YAZDI', onceki, hareketler(cid, URUN))
    assert olay(cid, fis2['id'])['status'] == 'SENT', olay(cid, fis2['id'])

    # ================= SENARYO 5: TABAN BIRIM YOK -> ADI KONMUS KOVA ======
    sezon_t = sezon_ac('kn5', URUN_TABANSIZ, 2026)
    # Hasat olayi da ayni kovaya duser: taban birim yoksa hasat da cevrilemez.
    hasat_t = hasat_ac(sezon_t, '1000')
    sayac = tuket(cid)
    assert sayac['SKIPPED_TABAN_BILDIRILMEMIS'] == 1, sayac
    assert miktarlar(cid, URUN_TABANSIZ) == [], hareketler(cid, URUN_TABANSIZ)
    # Fis ucu zaten 422 verir (units.py sahip karari 2) — outbox yolu ile
    # etkilesimli yolun AYNI olguyu reddettigi burada da gorunuyor.
    red = client.post('/api/field-harvest-tickets', headers=h,
                      json={'harvest_id':hasat_t['id'],
                            'gross_entered_quantity':'1000','entered_unit':'KG',
                            'deductions':[]})
    assert red.status_code == 422, red.text
    assert red.json()['detail']['sebep'] == 'TABAN_BILDIRILMEMIS', red.text

    # ================= SENARYO 6: HASAT "ton", TABAN "kg" -> 1000x ========
    # HAM YAZILSAYDI 2 cikardi. Cikan 2000 ise cevrildi.
    sezon_ton = sezon_ac('kn6', URUN_TON, 2026)
    hasat_ton = hasat_ac(sezon_ton, '2', 'ton')
    sayac = tuket(cid)
    assert sayac['SENT'] == 1, sayac
    assert miktarlar(cid, URUN_TON) == [Decimal('2000.0000')], (
        'HASAT MIKTARI TABAN BIRIME CEVRILMEDI (1000x riski)',
        hareketler(cid, URUN_TON))

    # ================= SENARYO 7: A'NIN FISI B'NIN STOGUNA DOKUNMAZ ======
    # Ikinci firma HAM SQL ile aciliyor: konu kayit ucu degil, KIRACI SINIRI.
    # B'nin kendi urunu var ve A'nin bekleyen bir fis olayi var. B icin
    # kosturulan tuketici A'nin olayini NE GORUR NE TUKETIR.
    with SessionLocal() as db:
        bid = int(db.execute(_sql(
            'INSERT INTO companies(name,is_active,created_at) '
            'VALUES(:n,TRUE,:t) RETURNING id'),
            {'n': 'Kantar B', 't': '2026-01-01 00:00:00+00:00'}).scalar_one())
        db.commit()
    urun_yaz(bid, 4299, 'KG')

    # A'da bekleyen bir fis olayi birak (tuketilmemis).
    fis_a = fis_ac(hasat, '10', 'KG', [], no='KN-7')
    assert olay(cid, fis_a['id'])['status'] == 'PENDING', olay(cid, fis_a['id'])
    a_once = hareketler(cid, URUN)

    sayac_b = tuket(bid)
    assert sayac_b['girdi'] == 0, ('B TUKETICISI A NIN OLAYINI GORDU', sayac_b)
    assert miktarlar(bid, 4299) == [], hareketler(bid, 4299)
    assert olay(cid, fis_a['id'])['status'] == 'PENDING', (
        'B KOSUMU A NIN OLAYINI TUKETTI', olay(cid, fis_a['id']))
    assert hareketler(cid, URUN) == a_once, (
        'B KOSUMU A NIN DEFTERINI OYNATTI', a_once, hareketler(cid, URUN))

    # A kendi olayini tuketince duzeltme A'DA olusur, B yine SIFIR.
    # Fis netleri 1700+500+10 = 2210; zaten yazilan 1200; delta = 10.
    tuket(cid)
    assert sum(miktarlar(cid, URUN)) == Decimal('2210.0000'), hareketler(cid, URUN)
    assert miktarlar(bid, 4299) == [], hareketler(bid, 4299)

    # ================= SENARYO 8: BAYRAK KAPALI -> PENDING, HAREKET YOK ===
    # Bayrak zamanlayiciyi kapatir; olaylar BIRIKIR. Yukaridaki her tuketim
    # ELLE cagrildi. Burada tuketici CAGRILMIYOR ve olay PENDING kaliyor.
    fis_bekleyen = fis_ac(hasat, '100', 'KG', [], no='KN-8')
    assert settings.field_stock_outbox_enabled is False
    assert olay(cid, fis_bekleyen['id'])['status'] == 'PENDING', (
        'BAYRAK KAPALIYKEN OLAY ISLENDI', olay(cid, fis_bekleyen['id']))
    assert sum(miktarlar(cid, URUN)) == Decimal('2210.0000'), (
        'BAYRAK KAPALIYKEN DEFTER OYNADI', hareketler(cid, URUN))

    # ============ EK: POST /api/products TABAN BIRIMI YAZIYOR ============
    # C2 ONCESI: `ProductCreate` `ProductUpdate`ten turedigi icin `base_unit`
    # gövdede KABUL EDILIYOR ama INSERT'in sutun listesinde YOK — deger
    # SESSIZCE dusuyordu. Istemci 201 aliyor, kart taban birimsiz doguyor ve
    # o urunun ILK kantar fisi TABAN_BILDIRILMEMIS ile reddediliyordu.
    # Kurallar PUT ile AYNI ve ucu de burada olculuyor.
    def urun_govde(**ek):
        govde = {'name':'C2 Urun','purchase_price':'0','sale_price':'0',
                 'vat_rate':'0','unit':'kg','stock':'0'}
        govde.update(ek)
        return govde

    def taban_oku(urun_id):
        with SessionLocal() as db:
            return db.execute(_sql(
                'SELECT base_unit FROM products WHERE company_id=:c AND id=:i'),
                {'c': cid, 'i': urun_id}).scalar()

    # BACAK 1 — VERILDI: yazilir ve `turkce_katla` ile KANONIKLESIR.
    # Kucuk harf giriliyor: cozucu kapali kumeyi BUYUK harfle arar.
    y1 = client.post('/api/products', headers=h, json=urun_govde(base_unit='kg'))
    assert y1.status_code == 201, y1.text
    assert taban_oku(y1.json()['id']) == 'KG', (
        'POST TABAN BIRIMI YAZMADI YA DA KATLAMADI', taban_oku(y1.json()['id']))

    # BACAK 2 — HIC GONDERILMEDI: sutun NULL doğar ve `unit`ten KOPYALANMAZ.
    # Kopyalansaydi ("kg" -> "KG") hic bildirilmemis bir olgu bildirilmis
    # gorunurdu ve taban birimsiz urunun fisi 422 ALMAZDI — yani
    # `test_kantar_fisi_sozlesme.py`nin taban-birim bacagi SESSIZCE olurdu.
    y2 = client.post('/api/products', headers=h, json=urun_govde())
    assert y2.status_code == 201, y2.text
    assert taban_oku(y2.json()['id']) is None, (
        'POST TABAN BIRIMI `unit`TEN KOPYALADI', taban_oku(y2.json()['id']))

    # BACAK 3 — ACIK NULL ve BOSLUK: ikisi de 422, PUT ile AYNI kod.
    for bos in (None, '   ', ''):
        red = client.post('/api/products', headers=h,
                          json=urun_govde(base_unit=bos))
        assert red.status_code == 422, ('BOS TABAN BIRIM KABUL EDILDI', bos, red.text)
        ayrinti = red.json()['detail']
        # Bos dizgi Pydantic'in `max_length`ine takilmaz; kodu BIZIM
        # kapimizdan gelmeli, yoksa mesaj cagirana hicbir sey soylemez.
        if isinstance(ayrinti, dict):
            assert ayrinti['code'] == 'TABAN_BIRIM_SILINEMEZ', (bos, ayrinti)

    print('KANTAR NETI STOGA TAMAM')
'''
