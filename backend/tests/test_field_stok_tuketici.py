"""FAZ 4 — outbox TÜKETİCİSİ: olaylar stok hareketine dönüşür.

--- ÖLÇÜLEN KUSUR ------------------------------------------------------------

Yazıcı tarafı #81 ve #88 ile kapandı: her faaliyet ve her hasat yazımı TAM BİR
olay üretiyor. Olayları KİMSE OKUMUYORDU. İki firmalık gerçek veride 10 olay
PENDING, attempts=0; defterdeki her hareket açılış bakiyesi ya da satış.
Hiçbir yerde hata YOK — sistem sadece yanlış cevap veriyor:

    ürün          fiziksel gerçek   sistem (önce)   sistem (sonra)
    buğday tohumu       -200 kg          200 kg        -200 kg
    gübre                400 kg         1000 kg         400 kg

--- BU DOSYANIN ASIL İDDİASI -------------------------------------------------

**YÖN, SESSİZ YANLIŞ CEVABIN KAYNAĞIDIR.** Ters işaret hata vermez; envanteri
artırması gerekirken azaltır (ya da tersi) ve rapor "doğru" görünür. Bu yüzden
yön kaynak başına ÖLÇÜLÜR ve mutasyonla çürütülür.

Diğer senaryolar bu iddianın vakumda geçmemesi için var:

* TEKRAR: aynı olayı iki kez işlemek stoğu iki kez oynatmamalı.
* YETİM: kaynak satırı olmayan olay UYGULANMAMALI — olmayan bir faaliyet için
  envanter düşmek, kusurun ta kendisini üretirdi.
* KİRACI: hareketin firması olayın firmasıdır; kaynak başka firmanınsa olay
  uygulanamaz.
* ÖLÜ KOVA: sonsuz yeniden deneme kuyruğu sessizce büyütür; deneme tavanı ve
  terminal kova ADI KONMUŞ bir karardır ve iki yönde dondurulmuştur.
* KORUNUM: her PENDING olay ya uygulanır ya adı konmuş bir kovaya düşer.

--- ORTAM VARSAYIMI YOK ------------------------------------------------------

Kurgu veriler SENTETİKTİR ve her senaryo KENDİ taze veritabanını göç
zincirinden kurar. Test ne çalışma dizinine, ne ref varlığına, ne de HEAD'in
anlamına dair bir şey varsayar.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

# Terminal kovalar burada İKİ YÖNDE dondurulur: yeni bir kova eklemek de
# çıkarmak da bu dosyayı kırar. Adı konmamış bir artık durum, bu projenin bu
# hafta dört kez bulduğu fail-open kaçışının yaşadığı yerdir.
# `FAILED_TENANT` BİLEREK YOK. Ölçüldü: bir olayın kaynağının BAŞKA firmada
# olduğunu görebilmek için kiracı sınırının DIŞINI okumak gerekiyor
# (`company_id <> :company_id`) ve bu deponun statik kiracı kapısı — RLS'in
# bildirilmiş yerine geçen kapı — tam olarak bunu reddediyor. Yani "yetim" ile
# "başka firmanın" ayrımı, ancak kapının yasakladığı bir okumayla yapılabilir.
# Bu yüzden ikisi TEK gözlenebilir durumda birleşir: kaynak BU KİRACIDA
# görünmüyor. Kiracı güvencesi kaybolmaz — kaynak okuması kapsamlı olduğu için
# başka firmanın satırı için hareket YAZILAMAZ; ölçülen sonuç sıfır harekettir.
BEKLENEN_TERMINAL_DURUMLAR = (
    "SENT",
    "SKIPPED_SOURCE_NOT_VISIBLE",
    "SKIPPED_NO_PRODUCT",
    "DEAD",
)
BEKLENEN_TERMINAL_SAYISI = 4


_KURULUM = r'''
from decimal import Decimal

from sqlalchemy import text as _sql

from app.db import SessionLocal
import app.main  # göç zincirini koşturur
from app.field_stok_tuketici import olaylari_isle, tum_firmalari_isle

FIRMA = 1
DIGER_FIRMA = 2
ZAMAN = '2026-08-01T00:00:00'


def _yaz(db, sql, **p):
    db.execute(_sql(sql), p)


def kur(db):
    """SENTETİK veri. Göç zinciri BİR firma ve BİR depo bırakıyor (ölçüldü:
    companies=1 'Ana Firma', warehouses=1 'Merkez Depo'); bu yüzden kurulum
    boş veritabanı VARSAYMAZ — var olanı kullanır, eksik olanı yaratır."""
    var = {r[0] for r in db.execute(_sql("SELECT id FROM companies")).all()}
    if DIGER_FIRMA not in var:
        _yaz(db, "INSERT INTO companies (id,name,is_active,negative_stock_policy,"
                 "credit_limit_policy,farm_area_override_policy,"
                 "farm_early_harvest_policy,farm_spraying_dose_required,"
                 "service_parts_mode,created_at) "
                 "VALUES (:i,:n,1,'BLOCK','BLOCK','BLOCK','BLOCK',0,"
                 "'IMMEDIATE',:z)", i=DIGER_FIRMA, n='Firma B', z=ZAMAN)
    depolu = {r[0] for r in db.execute(
        _sql("SELECT company_id FROM warehouses WHERE is_active = 1")).all()}
    for fid in (FIRMA, DIGER_FIRMA):
        if fid not in depolu:
            _yaz(db, "INSERT INTO warehouses (company_id,name,is_default,"
                     "is_active,warehouse_type) VALUES (:c,:n,1,1,'FIXED')",
                 c=fid, n='Depo %d' % fid)
    # Açılış bakiyeleri: ölçülen kusurdaki iki ürün.
    depo = db.execute(_sql(
        "SELECT id FROM warehouses WHERE company_id = :c AND is_active = 1 "
        "ORDER BY is_default DESC, id"), {'c': FIRMA}).scalars().first()
    for pid, ad, acilis in ((10, 'Bugday Tohumu', '200.0000'),
                            (11, 'Gubre', '1000.0000')):
        _yaz(db, "INSERT INTO products (id,name,purchase_price,sale_price,vat_rate,"
                 "stock,unit,price_per,active,critical_stock,minimum_stock,company_id) "
                 "VALUES (:i,:n,0,0,0,:s,'kg','unit',1,0,0,:c)",
             i=pid, n=ad, s=acilis, c=FIRMA)
        _yaz(db, "INSERT INTO warehouse_stocks (company_id,warehouse_id,product_id,"
                 "quantity,critical_stock,reserved_quantity) "
                 "VALUES (:c,:w,:p,:s,0,0)",
             c=FIRMA, w=depo, p=pid, s=acilis)
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
    # İkinci firmanın kendi sezonu — çapraz kiracı senaryosu için.
    _yaz(db, "INSERT INTO farms (id,company_id,code,name,status,created_at,"
             "updated_at) VALUES (2,:c,'f2','Ciftlik B','ACTIVE',:z,:z)",
         c=DIGER_FIRMA, z=ZAMAN)
    _yaz(db, "INSERT INTO farm_parcels (id,company_id,farm_id,code,name,"
             "area_decare,status,created_at,updated_at) "
             "VALUES (2,:c,2,'p2','Parsel B','10.0000','ACTIVE',:z,:z)",
         c=DIGER_FIRMA, z=ZAMAN)
    _yaz(db, "INSERT INTO crop_seasons (id,company_id,parcel_id,season_year,crop,"
             "status,created_at,updated_at) "
             "VALUES (2,:c,2,2026,'Arpa','ACTIVE',:z,:z)", c=DIGER_FIRMA, z=ZAMAN)


def faaliyet(db, aid, firma=FIRMA):
    _yaz(db, "INSERT INTO field_activities (id,company_id,season_id,activity_type,"
             "performed_at,status,created_at,updated_at) "
             "VALUES (:i,:c,:sez,'SOWING',:z,'RECORDED',:z,:z)",
         i=aid, c=firma, sez=(1 if firma == FIRMA else 2), z=ZAMAN)


def girdi(db, gid, aid, urun, miktar, firma=FIRMA):
    _yaz(db, "INSERT INTO field_activity_inputs (id,company_id,activity_id,product_id,"
             "input_name,quantity,unit,created_at,updated_at) "
             "VALUES (:i,:c,:a,:p,'girdi',:q,'kg',:z,:z)",
         i=gid, c=firma, a=aid, p=urun, q=miktar, z=ZAMAN)


def hasat(db, hid, firma=FIRMA):
    _yaz(db, "INSERT INTO field_harvests (id,company_id,season_id,harvested_on,"
             "quantity,unit,status,created_at,updated_at) "
             "VALUES (:i,:c,:sez,'2026-08-15','50.0000','kg','RECORDED',:z,:z)",
         i=hid, c=firma, sez=(1 if firma == FIRMA else 2), z=ZAMAN)


def olay(db, oid, tip, sid, firma=FIRMA, deneme=0):
    _yaz(db, "INSERT INTO field_integration_events (id,company_id,source_type,"
             "source_id,target,idempotency_key,status,attempts,created_at,updated_at) "
             "VALUES (:i,:c,:t,:s,'stock',:k,'PENDING',:d,:z,:z)",
         i=oid, c=firma, t=tip, s=sid, k='%s:%d:stock' % (tip, sid), d=deneme, z=ZAMAN)


def stok(db, urun):
    return db.execute(_sql("SELECT stock FROM products WHERE id = :p"),
                      {'p': urun}).scalar_one()


def hareket_sayisi(db):
    return db.execute(_sql(
        "SELECT COUNT(*) FROM stock_movements WHERE reference_type = "
        "'field_integration_event'")).scalar_one()


def durumlar(db):
    return dict(db.execute(_sql(
        "SELECT status, COUNT(*) FROM field_integration_events GROUP BY status"
    )).all())
'''


def _kos(kaynak: str, db_yolu: Path, imza: str) -> str:
    """Senaryoyu AYRI süreçte, KENDİ taze veritabanında koşar."""
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_yolu.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    tamam = subprocess.run(
        [sys.executable, "-c", _KURULUM + kaynak],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="strict", timeout=600,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    assert imza in tamam.stdout, tamam.stdout
    return tamam.stdout


def _kos_kirmizi(kaynak: str, db_yolu: Path, beklenen: str) -> None:
    """Senaryo KIRMIZI olmalı VE kırmızı, kapının KENDİ metnini taşımalı."""
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_yolu.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    tamam = subprocess.run(
        [sys.executable, "-c", _KURULUM + kaynak],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="strict", timeout=600,
    )
    cikti = tamam.stdout + "\n" + tamam.stderr
    assert "MUTASYON-KURULDU" in tamam.stdout, (
        "Mutasyon uygulanmadı; bu koşum sınamıyor.\n" + cikti)
    assert tamam.returncode != 0, ("Beklenen kırmızı GELMEDİ.\n" + cikti)
    assert beklenen in cikti, (
        "Kırmızı geldi ama kapının KENDİ metni yok; başka bir yerden "
        f"(kısıt, doğrulama, çökme) gelmiş olabilir. Beklenen: {beklenen!r}\n"
        + cikti)


def test_terminal_durumlar_DONDURULDU() -> None:
    """Kova kümesi iki yönde dondurulmuş bir KARARDIR."""
    from app import field_stok_tuketici as tuketici

    assert tuketici.TERMINAL_DURUMLAR == BEKLENEN_TERMINAL_DURUMLAR, (
        "Terminal kova kümesi DEĞİŞTİ. Her kova bir karardır: adı, sayısı ve "
        "anlamı burada dondurulur. "
        f"ölçülen={tuketici.TERMINAL_DURUMLAR} bildirilen="
        f"{BEKLENEN_TERMINAL_DURUMLAR}"
    )
    assert len(tuketici.TERMINAL_DURUMLAR) == BEKLENEN_TERMINAL_SAYISI


def test_yon_KAYNAK_BASINA_turetiliyor() -> None:
    """Girdi TÜKETİR (-1), hasat ÜRETİR (+1). Yönün tek kaynağı bu sözlük."""
    from decimal import Decimal

    from app import field_stok_tuketici as tuketici

    for tip, beklenen in BEKLENEN_YON.items():
        assert tuketici._KAYNAK[tip][1] == Decimal(beklenen), (
            f"YÖN BEYANI İHLALİ: kaynak={tip} bildirilen yön "
            f"{YON_ADI[beklenen]} olmalı; modül {tuketici._KAYNAK[tip][1]} diyor."
        )


def test_kabul_olcutu_stok_DOGRU_yone_gidiyor(tmp_path: Path) -> None:
    """ÖLÇÜLEN KUSUR: buğday -200, gübre 400 olmalı."""
    _kos(r'''
with SessionLocal() as db:
    kur(db)
    faaliyet(db, 1)
    girdi(db, 1, 1, 10, '400.0000')   # 400 kg tohum EKİLDİ (açılış 200)
    girdi(db, 2, 1, 11, '600.0000')   # 600 kg gübre KULLANILDI
    olay(db, 1, 'field_activity', 1)
    db.commit()

    onceki = (stok(db, 10), stok(db, 11))
    sayac = olaylari_isle(db, FIRMA)
    db.commit()
    sonraki = (Decimal(str(stok(db, 10))), Decimal(str(stok(db, 11))))

print('ONCE  bugday=%s gubre=%s' % onceki)
print('SONRA bugday=%s gubre=%s' % sonraki)
print('SAYAC %r' % (sayac,))
assert sonraki[0] == Decimal('-200.0000'), ('bugday', sonraki[0])
assert sonraki[1] == Decimal('400.0000'), ('gubre', sonraki[1])
assert sayac['SENT'] == 1, sayac
print('KABUL-OLCUTU-TAMAM')
''', tmp_path / "kabul.db", "KABUL-OLCUTU-TAMAM")


def test_tekrar_stogu_IKI_KEZ_oynatmiyor(tmp_path: Path) -> None:
    """Aynı olayı iki kez işlemek hareketi iki kez yazmamalı."""
    _kos(r'''
with SessionLocal() as db:
    kur(db)
    faaliyet(db, 1)
    girdi(db, 1, 1, 10, '50.0000')
    olay(db, 1, 'field_activity', 1)
    db.commit()

    ilk = olaylari_isle(db, FIRMA)
    db.commit()
    hareket_ilk, stok_ilk = hareket_sayisi(db), Decimal(str(stok(db, 10)))

    ikinci = olaylari_isle(db, FIRMA)
    db.commit()
    hareket_ikinci, stok_ikinci = hareket_sayisi(db), Decimal(str(stok(db, 10)))

print('ILK    sayac=%r hareket=%d stok=%s' % (ilk, hareket_ilk, stok_ilk))
print('IKINCI sayac=%r hareket=%d stok=%s' % (ikinci, hareket_ikinci, stok_ikinci))
assert hareket_ilk == 1, hareket_ilk
assert hareket_ikinci == 1, ('tekrar stok oynatti', hareket_ikinci)
assert stok_ikinci == stok_ilk, (stok_ilk, stok_ikinci)
assert ikinci['girdi'] == 0, ikinci
print('TEKRAR-TAMAM')
''', tmp_path / "tekrar.db", "TEKRAR-TAMAM")


def test_yetim_olay_UYGULANMIYOR(tmp_path: Path) -> None:
    """Kaynak satırı olmayan olay envanteri OYNATMAMALI."""
    _kos(r'''
with SessionLocal() as db:
    kur(db)
    olay(db, 1, 'field_activity', 999)   # boyle bir faaliyet YOK
    db.commit()

    sayac = olaylari_isle(db, FIRMA)
    db.commit()
    d = durumlar(db)

print('SAYAC %r' % (sayac,))
print('DURUMLAR %r' % (d,))
print('HAREKET %d' % hareket_sayisi(db))
assert sayac['SKIPPED_SOURCE_NOT_VISIBLE'] == 1, sayac
assert hareket_sayisi(db) == 0, 'yetim olay envanteri oynatti'
assert d.get('SKIPPED_SOURCE_NOT_VISIBLE') == 1, d
print('YETIM-TAMAM')
''', tmp_path / "yetim.db", "YETIM-TAMAM")


def test_capraz_kiraci_olay_UYGULANMIYOR(tmp_path: Path) -> None:
    """Olayın firması ile kaynağın firması ayrıysa hareket YAZILMAZ."""
    _kos(r'''
with SessionLocal() as db:
    kur(db)
    faaliyet(db, 1, firma=DIGER_FIRMA)
    girdi(db, 1, 1, 10, '50.0000', firma=DIGER_FIRMA)
    olay(db, 1, 'field_activity', 1, firma=FIRMA)   # olay A, kaynak B
    db.commit()

    sayac = olaylari_isle(db, FIRMA)
    db.commit()

print('SAYAC %r' % (sayac,))
print('HAREKET %d' % hareket_sayisi(db))
assert sayac['SKIPPED_SOURCE_NOT_VISIBLE'] == 1, sayac
assert hareket_sayisi(db) == 0, 'capraz kiraci hareket yazildi'
print('KIRACI-TAMAM')
''', tmp_path / "kiraci.db", "KIRACI-TAMAM")


def test_hasat_URUNSUZ_kovasina_dusuyor(tmp_path: Path) -> None:
    """Hasadın ürüne giden yolu YOK; uydurmak yerine adı konmuş kovaya düşer."""
    _kos(r'''
with SessionLocal() as db:
    kur(db)
    hasat(db, 1)
    olay(db, 1, 'field_harvest', 1)
    db.commit()

    sayac = olaylari_isle(db, FIRMA)
    db.commit()

print('SAYAC %r' % (sayac,))
assert sayac['SKIPPED_NO_PRODUCT'] == 1, sayac
assert hareket_sayisi(db) == 0, 'urun bagi yokken hareket yazildi'
print('URUNSUZ-TAMAM')
''', tmp_path / "urunsuz.db", "URUNSUZ-TAMAM")


def test_deneme_tavani_OLU_kovasina_dusuruyor(tmp_path: Path) -> None:
    """Sonsuz yeniden deneme yok: tavana varan olay ÖLÜ kovasına düşer."""
    _kos(r'''
with SessionLocal() as db:
    kur(db)
    faaliyet(db, 1)
    girdi(db, 1, 1, 10, '50.0000')
    olay(db, 1, 'field_activity', 1, deneme=3)   # tavan 3
    db.commit()

    sayac = olaylari_isle(db, FIRMA, azami_deneme=3)
    db.commit()
    d = durumlar(db)

print('SAYAC %r' % (sayac,))
print('DURUMLAR %r' % (d,))
assert sayac['DEAD'] == 1, sayac
assert hareket_sayisi(db) == 0, 'olu olay envanteri oynatti'
print('OLU-TAMAM')
''', tmp_path / "olu.db", "OLU-TAMAM")


def test_korunum_girdi_KOVALARIN_toplamina_esit(tmp_path: Path) -> None:
    """Her PENDING olay ya uygulanır ya adı konmuş bir kovaya düşer."""
    _kos(r'''
with SessionLocal() as db:
    kur(db)
    faaliyet(db, 1)
    girdi(db, 1, 1, 10, '10.0000')
    hasat(db, 2)
    faaliyet(db, 3, firma=DIGER_FIRMA)
    olay(db, 1, 'field_activity', 1)          # uygulanir
    olay(db, 2, 'field_harvest', 2)           # urunsuz
    olay(db, 3, 'field_activity', 999)        # yetim
    olay(db, 4, 'field_activity', 3)          # kiraci -> ORPHAN kovasi
    faaliyet(db, 4)
    girdi(db, 2, 4, 10, '10.0000')
    olay(db, 5, 'field_activity', 4, deneme=9)  # olu (KENDI kaynagi, ayri anahtar)
    db.commit()

    sayac = tum_firmalari_isle(db, azami_deneme=3)
    db.commit()

print('SAYAC %r' % (sayac,))
kovalar = sum(sayac[k] for k in
              ('SENT','SKIPPED_SOURCE_NOT_VISIBLE','SKIPPED_NO_PRODUCT','DEAD'))
assert sayac['girdi'] == 5, sayac
assert kovalar == 5, (kovalar, sayac)
assert sayac['SENT'] == 1 and sayac['SKIPPED_NO_PRODUCT'] == 1, sayac
assert sayac['SKIPPED_SOURCE_NOT_VISIBLE'] == 2, sayac  # yetim + capraz kiraci
assert sayac['DEAD'] == 1, sayac
print('KORUNUM-TAMAM')
''', tmp_path / "korunum.db", "KORUNUM-TAMAM")


# ---------------------------------------------------------------------------
# YÖN KAPISI — kaynak başına, ADIYLA
# ---------------------------------------------------------------------------
# Ters işaret HATA VERMEZ. Envanteri düşürmesi gerekirken artırır ve rapor
# "çalışıyor" görünür. Bu yüzden yön, uygulanan hareketin İŞARETİ üzerinden
# kaynak başına ölçülür ve kırmızı, HANGİ kaynağın HANGİ yönde yanlış
# olduğunu SÖYLER.
#
# Bugün yalnız `field_activity` hareket üretebiliyor: `field_harvests`ten
# `products`a giden yol YOK (ölçüldü, c9d3eb1). Hasadın yönü yine de
# bildirilir ve hareket üretemediği ADI KONMUŞ kovayla birlikte çapalanır;
# şema hasadı bir ürüne bağladığı gün bu testin hasat satırı ölçülebilir hâle
# gelir ve kova boşalır.
YON_ADI = {-1: "TÜKETİM (eksi)", 1: "ÜRETİM (artı)"}

# YÖN BURADA DONDURULUR, MODÜLDEN OKUNMAZ. Ölçüldü: beklentiyi `_KAYNAK`tan
# okuyan bir kapı, işareti çeviren mutasyonda beyanı da birlikte okuduğu için
# YEŞİL kalıyordu — kendi kendini doğrulayan bir totoloji. Beklenti bağımsız
# olmak zorunda.
BEKLENEN_YON = {"field_activity": -1, "field_harvest": 1}


def test_yon_UYGULANAN_HAREKETIN_isaretinde_dogrulaniyor(tmp_path: Path) -> None:
    """field_activity TÜKETİR: yazılan hareketin işareti EKSİ olmalı."""
    from decimal import Decimal

    from app import field_stok_tuketici as tuketici

    beklenen_yon = BEKLENEN_YON["field_activity"]
    assert tuketici._KAYNAK["field_activity"][1] == Decimal(beklenen_yon), (
        "YÖN BEYANI İHLALİ: kaynak=field_activity bildirilen yön "
        f"{YON_ADI[beklenen_yon]} olmalı; modül "
        f"{tuketici._KAYNAK['field_activity'][1]} diyor."
    )
    cikti = _kos(r'''
with SessionLocal() as db:
    kur(db)
    faaliyet(db, 1)
    girdi(db, 1, 1, 10, '25.0000')
    olay(db, 1, 'field_activity', 1)
    db.commit()
    olaylari_isle(db, FIRMA)
    db.commit()
    isaretler = db.execute(_sql(
        "SELECT quantity FROM stock_movements "
        "WHERE reference_type = 'field_integration_event'")).scalars().all()

print('ISARETLER %r' % ([str(x) for x in isaretler],))
assert isaretler, 'hic hareket yazilmadi; yon olculemez'
print('YON-OLCULDU')
''', tmp_path / "yon.db", "YON-OLCULDU")

    satir = [s for s in cikti.splitlines() if s.startswith("ISARETLER")][0]
    olculen = [Decimal(x.strip(" '\"")) for x in
               satir.split("[", 1)[1].rstrip("]").split(",") if x.strip(" '\"")]
    assert olculen, "hareket yok; bu test vakumda geçemez"
    for miktar in olculen:
        assert (miktar < 0) == (beklenen_yon < 0), (
            "YÖN İHLALİ: kaynak=field_activity bildirilen yön="
            f"{YON_ADI[beklenen_yon]} ama yazılan hareket {miktar}. "
            "Bir tarla girdisi stok TÜKETİR; ters işaret hata vermez, "
            "envanteri YANLIŞ YÖNDE oynatır ve rapor doğru görünür."
        )


def test_hasat_yonu_BILDIRILMIS_ve_kovasi_ADI_KONMUS() -> None:
    """Hasat ÜRETİR; bugün ürün bağı olmadığı için adı konmuş kovaya düşer."""
    from decimal import Decimal

    from app import field_stok_tuketici as tuketici

    assert tuketici._KAYNAK["field_harvest"][1] == Decimal("1"), (
        "Hasat stok ÜRETİR; bildirilen yön artı olmalı"
    )
    assert tuketici.DURUM_URUNSUZ in tuketici.TERMINAL_DURUMLAR, (
        "hasadın düştüğü kova terminal kümede bildirilmemiş"
    )


def test_ETKI_kisiti_ikinci_hareketi_REDDEDIYOR(tmp_path: Path) -> None:
    """Benzersizlik OLAY SATIRINI değil, ETKİYİ korumalı.

    Olay satırı zaten benzersizdi (`company_id, idempotency_key`) ve yarışı
    ENGELLEMİYORDU: iki tüketici kendi hareketini yazar, ikisi de olayı SENT
    yapar, olay tablosunda hiçbir ihlal görünmez, envanter iki kez düşer.
    Bu yüzden kısıt HAREKETE kondu. Burada uygulama mantığı DEVRE DIŞI
    bırakılıp doğrudan ikinci bir hareket yazılmaya çalışılıyor: veritabanı
    reddetmezse güvence uygulama mantığına bağlı demektir.
    """
    _kos(r'''
with SessionLocal() as db:
    kur(db)
    faaliyet(db, 1)
    girdi(db, 1, 1, 10, '25.0000')
    olay(db, 1, 'field_activity', 1)
    db.commit()
    olaylari_isle(db, FIRMA)

with SessionLocal() as db:
    m = db.execute(_sql(
        "SELECT company_id, product_id, reference_id FROM stock_movements "
        "WHERE reference_type = 'field_integration_event' LIMIT 1")).first()
    assert m is not None, 'PROB GECERSIZ: kopyalanacak hareket yok'
    reddedildi = False
    try:
        db.execute(_sql(
            "INSERT INTO stock_movements(product_id,movement_type,quantity,"
            "movement_date,reference_type,reference_id,note,company_id,warehouse_id) "
            "VALUES(:p,'FIELD','-1.0000','2026-01-01','field_integration_event',"
            ":r,'ikinci',:c,1)"), {'p': m[1], 'r': m[2], 'c': m[0]})
        db.commit()
    except Exception as e:
        reddedildi = True
        db.rollback()
        print('REDDEDEN %s' % type(e).__name__)

with SessionLocal() as db:
    toplam = db.execute(_sql(
        "SELECT COUNT(*) FROM stock_movements WHERE "
        "reference_type = 'field_integration_event'")).scalar_one()

print('HAREKET %d' % toplam)
assert reddedildi, ('ETKI KORUNMUYOR: ayni olay+urun icin IKINCI hareket '
                   'veritabanina yazilabildi; benzersizlik yalnizca olay '
                   'satirini koruyor')
assert toplam == 1, ('bir olay icin birden fazla hareket var', toplam)
print('ETKI-KISITI-TAMAM')
''', tmp_path / "etki.db", "ETKI-KISITI-TAMAM")


def test_talep_KAYBEDEN_hicbir_sey_yazmiyor(tmp_path: Path) -> None:
    """Talebi kaybeden tüketici hareket YAZMAZ ve sayacında görünür.

    Burada yarış SİMÜLE EDİLMEZ: olay önceden talep edilmiş (CLAIMED)
    bırakılır, yani ikinci tüketicinin göreceği durum tam olarak yarışı
    kaybettiği andaki durumdur.
    """
    _kos(r'''
with SessionLocal() as db:
    kur(db)
    faaliyet(db, 1)
    girdi(db, 1, 1, 10, '25.0000')
    olay(db, 1, 'field_activity', 1)
    db.commit()
    # Başka bir tüketici ZATEN talep etmiş olsun.
    db.execute(_sql("UPDATE field_integration_events SET status = 'CLAIMED' "
                    "WHERE id = 1"))
    db.commit()
    sayac = olaylari_isle(db, FIRMA)
    db.commit()
    hareket = hareket_sayisi(db)

print('SAYAC %r' % (sayac,))
print('HAREKET %d' % hareket)
assert sayac['girdi'] == 0, ('CLAIMED olay PENDING gibi secildi', sayac)
assert hareket == 0, ('talebi kaybeden hareket yazdi', hareket)
print('TALEP-KAYBEDEN-TAMAM')
''', tmp_path / "talep.db", "TALEP-KAYBEDEN-TAMAM")


def test_oturum_siniri_KONAMAZSA_istisna_YUKARI_cikar() -> None:
    """FAIL CLOSED'un DILIMI: `_oturumu_sinirla` ustu koyamazsa ISTISNA ATAR.

    ÖLÇÜLEN KUSUR: fonksiyon her istisnayı yutup normal dönüyordu ve akış,
    kilit/ifade üstü OLMADAN yazmaya devam ediyordu — yani üstün tam olarak
    koruyacağı koşulda (bozuk oturum, erişilemeyen sunucu) yazım SINIRSIZ
    koşuyordu. Bu test İKİ YÖNLÜdür: yutma geri gelirse (`except` + `return`)
    `pytest.raises` KIRMIZI olur; fail-closed dururken YEŞİLDİR. Kardeşi
    `test_ust_KONAMAYINCA_yazim_HIC_denenmiyor` bu istisnanın çağıran
    zincirde HANGİ kovaya düştüğünü ölçer — ikisi birlikte tam kanıttır.
    """
    from types import SimpleNamespace

    from app import field_stok_tuketici as tuketici

    class PatlayanOturum:
        """PG lehcesi bildirir; her `execute` (yani `set_config`) PATLAR."""

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, *_a, **_k):
            raise RuntimeError("set_config KONAMADI (prob)")

    with pytest.raises(RuntimeError, match="set_config KONAMADI"):
        tuketici._oturumu_sinirla(PatlayanOturum())

    class BasaranOturum:
        """PG lehcesi; `set_config` cagrilarini SAYAR ve basarir."""

        def __init__(self) -> None:
            self.cagri = 0

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, *_a, **_k):
            self.cagri += 1

    basaran = BasaranOturum()
    tuketici._oturumu_sinirla(basaran)
    assert basaran.cagri == 2, (
        "iki ust (kilit + ifade) da konmali", basaran.cagri)

    class SqliteOturum:
        """SQLite lehcesi: `set_config` YOKTUR, fonksiyon DOKUNMADAN doner."""

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        def execute(self, *_a, **_k):
            raise AssertionError("SQLite oturumunda set_config kosmamali")

    tuketici._oturumu_sinirla(SqliteOturum())


def test_ust_KONAMAYINCA_yazim_HIC_denenmiyor(tmp_path: Path) -> None:
    """FAIL CLOSED'un ETKİSİ: sınır kurulamazsa YAZIM OLMAZ.

    Sınırın kurulamaması burada SEAM'den enjekte edilir (`_oturumu_sinirla`
    yerine atan bir eş); gerçek fonksiyonun o seam'den GERÇEKTEN attığı,
    kardeş test `test_oturum_siniri_KONAMAZSA_istisna_YUKARI_cikar` ile ayrı
    ölçülür. Burada ölçülen, atışın çağıran zincirdeki SONUCUdur: olay ADI
    KONMUŞ `RECOVERY_FAILED` kovasına düşer (kusur olayın değil PLATFORMUN;
    kilit/bakımla aynı politika — deneme YAKILMAZ), veritabanında HİÇBİR ŞEY
    değişmez, korunum denklemi tutar ve stok YERİNDEN OYNAMAZ.
    """
    _kos(r'''
from app import field_stok_tuketici as C

def _konamiyor(_oturum):
    raise RuntimeError("ust KONAMADI (enjekte)")

C._oturumu_sinirla = _konamiyor

with SessionLocal() as db:
    kur(db)
    faaliyet(db, 1)
    girdi(db, 1, 1, 10, '50.0000')
    olay(db, 1, 'field_activity', 1)
    db.commit()

    onceki_stok = Decimal(str(stok(db, 10)))
    sayac = olaylari_isle(db, FIRMA)
    db.commit()

with SessionLocal() as db:
    satir = db.execute(_sql(
        "SELECT status, attempts FROM field_integration_events WHERE id = 1"
    )).one()
    hareket = hareket_sayisi(db)
    sonraki_stok = Decimal(str(stok(db, 10)))

print('SAYAC %r' % (sayac,))
print('SATIR %s %d' % (satir[0], int(satir[1])))
assert sayac['girdi'] == 1, sayac
assert sayac['RECOVERY_FAILED'] == 1, (
    'FAIL CLOSED DEGIL: sinir kurulamayan olay adi konmus kovaya dusmedi', sayac)
assert sayac['SENT'] == 0, ('sinirsiz yazim UYGULANMIS', sayac)
assert satir[0] == 'PENDING', ('olay degisti', satir)
assert int(satir[1]) == 0, ('deneme YAKILDI; platform kusuru olaya yazilmis', satir)
assert hareket == 0, ('SINIRSIZ YAZIM GECTI: ust kurulamadan hareket yazildi', hareket)
assert sonraki_stok == onceki_stok, (onceki_stok, sonraki_stok)
print('FAIL-CLOSED-TAMAM')
''', tmp_path / "failclosed.db", "FAIL-CLOSED-TAMAM")


def test_kosullu_UPDATE_rowcount_SURUCUYE_BAGLI_DEGIL(tmp_path: Path) -> None:
    """"Kazandım mı" kararının dayandığı sayı ÖLÇÜLÜR.

    #81/#88 ölçtü: `INSERT ... RETURNING` sqlite3'te rowcount 0, psycopg'de 1.
    Talep kararı KOŞULLU UPDATE'in rowcount'una dayanıyor; o biçimin aynı
    sapmayı taşımadığı KOŞULDUĞU ARKA UÇTA ölçülmeli — varsayılmamalı.
    """
    _kos(r'''
with SessionLocal() as db:
    db.execute(_sql("DROP TABLE IF EXISTS talep_olcum"))
    db.execute(_sql("CREATE TABLE talep_olcum (id INTEGER PRIMARY KEY, status VARCHAR(20))"))
    db.execute(_sql("INSERT INTO talep_olcum (id, status) VALUES (1, 'PENDING')"))
    db.commit()
    SQL = ("UPDATE talep_olcum SET status = 'CLAIMED' "
           "WHERE id = :id AND status = 'PENDING'")
    ilk = db.execute(_sql(SQL), {'id': 1}).rowcount
    ikinci = db.execute(_sql(SQL), {'id': 1}).rowcount
    db.commit()

print('ROWCOUNT ilk=%r ikinci=%r' % (ilk, ikinci))
assert ilk == 1, ('kosullu UPDATE ilk cagrida 1 dondurmedi', ilk)
assert ikinci == 0, ('kosullu UPDATE ikinci cagrida 0 dondurmedi; "kazandim mi" '
                     'karari bu sayiya dayaniyor', ikinci)
print('ROWCOUNT-TAMAM')
''', tmp_path / "rowcount.db", "ROWCOUNT-TAMAM")
