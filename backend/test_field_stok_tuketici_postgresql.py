"""PostgreSQL ikizi: outbox tüketicisinin YARIŞ ve ETKİ güvenceleri.

--- BU İKİZİN SOMUT SEBEBİ ---------------------------------------------------

SQLite yazmaları VERİTABANI düzeyinde seri hâle getirir. Yani iki tüketicinin
aynı olayı kapma yarışı SQLite'ta OLUŞAMAZ ve orada alınan yeşil, kodun değil
ORTAMIN özelliğidir. Yarış güvencesi ancak gerçek eşzamanlı oturumlar veren
bir arka uçta ölçülebilir.

Ölçüldü (yerel PG 16.4, iki AYRI süreç): kazanan `SENT`, kaybeden
`CLAIM_LOST`, toplam hareket 1. Aynı prob SQLite'ta kaybedeni HİÇ olay
görmemiş hâlde bırakıyor (`girdi=0`) — sonuç aynı, MEKANİZMA farklı. Bu fark
tam olarak bu dosyanın var olma sebebidir.

Burada ölçülen üç şey:

* **YARIŞ.** İki süreç, tek PENDING olay, tek hareket.
* **ETKİ KISITI.** Aynı olay+ürün için ikinci hareket veritabanınca
  REDDEDİLİR (kısmi benzersiz indeks). Kısıt olay satırını değil ETKİYİ
  korur; olay satırı zaten benzersizdi ve yarışı engellemiyordu.
* **ROWCOUNT.** "Kazandım mı" kararı koşullu UPDATE'in rowcount'una
  dayanıyor. #81/#88 ölçtü: `INSERT ... RETURNING` sqlite3'te 0, psycopg'de 1
  döndürüyor. Koşullu UPDATE'in bu sapmayı TAŞIMADIĞI, karara güvenilen
  arka uçta ölçülmelidir — varsayılmamalı.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent

_URL = (
    os.environ.get("FIELD_CONSUMER_TEST_DATABASE_URL")
    or os.environ.get("APP_TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL", "")
)

# SEMA'ya bakilir, URL'nin TAMAMINA DEGIL. Onceki hali `"postgresql" in _URL`
# idi ve SQLite kulvarinda YANLIS POZITIF veriyordu: `run_isolated_tests.py`
# her dosyaya dosya adindan turetilmis bir calisma alani acar
# (`_worker_directory` -> `path.stem`) ve DATABASE_URL'i o dizine bakan bir
# sqlite yoluna kurar. Bu dosyanin adi "postgresql" ile bittigi icin sqlite
# URL'sinin KENDISI "postgresql" alt dizesini iceriyor, guard da kulvari PG
# saniyordu. Olculdu: guard atlamiyor, 11 test kosuyor, 6'si `SHOW
# lock_timeout` / `FOR UPDATE` gibi PG-ozel SQL'de sqlite3 sozdizimi hatasiyla
# duyuyordu. Dogrudan `pytest <dosya>` cagrisinda yol adi farkli oldugu icin
# kusur GORUNMUYORDU; yalnizca kulvarin kendi secim yolunda uretiliyordu.
pytestmark = pytest.mark.skipif(
    not _URL.startswith("postgresql"),
    reason="PostgreSQL ikizi: gerçek PG URL'si gerekiyor",
)


_KURULUM = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from sqlalchemy import text as _sql
from app.db import SessionLocal
import app.main
Z = '2026-08-01T00:00:00'
with SessionLocal() as db:
    db.execute(_sql("DELETE FROM stock_movements"))
    db.execute(_sql("DELETE FROM field_integration_events"))
    db.execute(_sql("DELETE FROM field_activity_inputs"))
    db.execute(_sql("DELETE FROM field_activities"))
    depo = db.execute(_sql(
        "SELECT id FROM warehouses WHERE company_id = 1 AND is_active "
        "ORDER BY is_default DESC, id")).scalars().first()
    assert depo is not None, 'KURULUM: aktif depo yok'
    if not db.execute(_sql("SELECT id FROM products WHERE id = 10")).first():
        db.execute(_sql(
            "INSERT INTO products (id,name,purchase_price,sale_price,vat_rate,"
            "stock,unit,price_per,active,critical_stock,minimum_stock,company_id) "
            "VALUES (10,'Tohum',0,0,0,'200.0000','kg','unit',true,0,0,1)"))
        db.execute(_sql(
            "INSERT INTO warehouse_stocks (company_id,warehouse_id,product_id,"
            "quantity,critical_stock,reserved_quantity) "
            "VALUES (1,:w,10,'200.0000',0,0)"), {"w": depo})
    for tablo, sql in (
        ("farms", "INSERT INTO farms (id,company_id,code,name,status,created_at,"
                  "updated_at) VALUES (1,1,'f1','C','ACTIVE',:z,:z)"),
        ("farm_parcels", "INSERT INTO farm_parcels (id,company_id,farm_id,code,"
                         "name,area_decare,status,created_at,updated_at) "
                         "VALUES (1,1,1,'p1','P','40.0000','ACTIVE',:z,:z)"),
        ("crop_seasons", "INSERT INTO crop_seasons (id,company_id,parcel_id,"
                         "season_year,crop,status,created_at,updated_at) "
                         "VALUES (1,1,1,2026,'B','ACTIVE',:z,:z)"),
    ):
        if not db.execute(_sql("SELECT id FROM %s WHERE id = 1" % tablo)).first():
            db.execute(_sql(sql), {"z": Z})
    db.execute(_sql(
        "INSERT INTO field_activities (id,company_id,season_id,activity_type,"
        "performed_at,status,created_at,updated_at) "
        "VALUES (1,1,1,'SOWING',:z,'RECORDED',:z,:z)"), {"z": Z})
    db.execute(_sql(
        "INSERT INTO field_activity_inputs (id,company_id,activity_id,product_id,"
        "input_name,quantity,unit,created_at,updated_at) "
        "VALUES (1,1,1,10,'g','50.0000','kg',:z,:z)"), {"z": Z})
    db.execute(_sql(
        "INSERT INTO field_integration_events (id,company_id,source_type,"
        "source_id,target,idempotency_key,status,attempts,created_at,updated_at) "
        "VALUES (1,1,'field_activity',1,'stock','field_activity:1:stock',"
        "'PENDING',0,:z,:z)"), {"z": Z})
    db.commit()
print("KURULUM-TAMAM")
'''

_TUKETICI = r'''
import os, sys, time
sys.path.insert(0, os.environ["BACKEND"])
from app.db import SessionLocal
from app.field_stok_tuketici import olaylari_isle
hedef = float(os.environ["BASLA"])
while time.time() < hedef:
    time.sleep(0.001)
with SessionLocal() as db:
    print("SAYAC %r" % (olaylari_isle(db, 1),))
'''

_RAPOR = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from sqlalchemy import text as _sql
from app.db import SessionLocal
with SessionLocal() as db:
    h = db.execute(_sql("SELECT COUNT(*) FROM stock_movements WHERE "
                        "reference_type = 'field_integration_event'")).scalar_one()
    d = db.execute(_sql("SELECT status FROM field_integration_events "
                        "WHERE id = 1")).scalar_one()
print("HAREKET %d DURUM %s" % (h, d))
'''

_IKINCI_HAREKET = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from sqlalchemy import text as _sql
from app.db import SessionLocal
with SessionLocal() as db:
    m = db.execute(_sql(
        "SELECT company_id, product_id, reference_id FROM stock_movements "
        "WHERE reference_type = 'field_integration_event' LIMIT 1")).first()
    assert m is not None, 'PROB GECERSIZ: kopyalanacak hareket yok'
    try:
        db.execute(_sql(
            "INSERT INTO stock_movements(product_id,movement_type,quantity,"
            "movement_date,reference_type,reference_id,note,company_id,"
            "warehouse_id) VALUES(:p,'FIELD','-1.0000','2026-01-01',"
            "'field_integration_event',:r,'ikinci',:c,1)"),
            {"p": m[1], "r": m[2], "c": m[0]})
        db.commit()
        print("IKINCI-HAREKET-KABUL")
    except Exception as e:
        db.rollback()
        print("IKINCI-HAREKET-RED %s" % type(e).__name__)
'''

_ROWCOUNT = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from sqlalchemy import text as _sql
from app.db import SessionLocal
with SessionLocal() as db:
    db.execute(_sql("DROP TABLE IF EXISTS talep_olcum"))
    db.execute(_sql("CREATE TABLE talep_olcum (id INTEGER PRIMARY KEY, "
                    "status VARCHAR(20))"))
    db.execute(_sql("INSERT INTO talep_olcum (id,status) VALUES (1,'PENDING')"))
    db.commit()
    SQL = ("UPDATE talep_olcum SET status = 'CLAIMED' "
           "WHERE id = :id AND status = 'PENDING'")
    ilk = db.execute(_sql(SQL), {"id": 1}).rowcount
    ikinci = db.execute(_sql(SQL), {"id": 1}).rowcount
    db.commit()
print("ROWCOUNT %r %r" % (ilk, ikinci))
'''


def _ortam(ek: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": _URL, "BACKEND": str(BACKEND),
        "PYTHONPATH": str(BACKEND), "PYTHONIOENCODING": "utf-8",
    })
    if ek:
        env.update(ek)
    return env


def _kos(kaynak: str, ek: dict[str, str] | None = None) -> str:
    tamam = subprocess.run(
        [sys.executable, "-c", kaynak], cwd=BACKEND, env=_ortam(ek),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    return tamam.stdout


def test_iki_tuketici_yarisirken_TEK_hareket_yaziliyor() -> None:
    """İki AYRI SÜREÇ, tek PENDING olay: tam olarak bir hareket."""
    import time

    assert "KURULUM-TAMAM" in _kos(_KURULUM)
    basla = str(time.time() + 3.0)
    surecler = [
        subprocess.Popen(
            [sys.executable, "-c", _TUKETICI], cwd=BACKEND,
            env=_ortam({"BASLA": basla}), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace",
        )
        for _ in range(2)
    ]
    ciktilar = []
    for surec in surecler:
        cikti, hata = surec.communicate(timeout=600)
        assert surec.returncode == 0, cikti + "\n" + hata
        ciktilar.append(cikti.strip())

    rapor = _kos(_RAPOR)
    kazanan = [c for c in ciktilar if "'SENT': 1" in c]
    kaybeden = [c for c in ciktilar if "'CLAIM_LOST': 1" in c]
    assert "HAREKET 1 " in rapor, (
        "YARIŞ KAYBEDİLDİ: bir olay için tek hareket bekleniyordu. İki "
        "tüketici aynı olayı kapmış ve envanter İKİ KEZ oynamış olabilir. "
        f"rapor={rapor!r} surecler={ciktilar!r}"
    )
    assert len(kazanan) == 1, (
        f"tam olarak BİR kazanan bekleniyordu: {ciktilar!r}")
    assert len(kaybeden) == 1, (
        "kaybeden süreç talebi KAYBETTİĞİNİ bildirmeli; bildirmiyorsa yarış "
        f"hiç oluşmamış olabilir: {ciktilar!r}")


def test_ETKI_kisiti_ikinci_hareketi_REDDEDIYOR_pg() -> None:
    """Benzersizlik ETKİYİ korur: aynı olay+ürün için ikinci hareket olmaz."""
    assert "KURULUM-TAMAM" in _kos(_KURULUM)
    _kos(_TUKETICI, {"BASLA": "0"})
    cikti = _kos(_IKINCI_HAREKET)
    assert "IKINCI-HAREKET-RED" in cikti, (
        "ETKİ KORUNMUYOR: aynı olay+ürün için ikinci stok hareketi "
        f"veritabanına yazılabildi. çıktı={cikti!r}"
    )


def test_kosullu_UPDATE_rowcount_PG_de_guvenilir() -> None:
    """"Kazandım mı" kararının dayandığı sayı, güvenilen arka uçta ölçülür."""
    cikti = _kos(_ROWCOUNT).strip()
    assert cikti.endswith("ROWCOUNT 1 0"), (
        "Koşullu UPDATE rowcount'u beklenen (1, 0) değil. Talep kararı bu "
        f"sayıya dayanıyor. ölçülen={cikti!r}"
    )


# --- KAYNAĞI GÖRÜNMEYEN OLAY: SÜTUN GENİŞLİĞİ SESSİZ DEĞİL ------------------
#
# `SKIPPED_SOURCE_NOT_VISIBLE` 26 karakter; `status` 0044'te VARCHAR(20)
# açılmıştı. SQLite `VARCHAR` uzunluğunu YOK SAYAR — bu yolun SQLite ikizi
# yeşildi ve yeşil, kodun değil ORTAMIN özelliğiydi. Gerçek PG'de aynı yazma
# `StringDataRightTruncation` verir: işlem geri alınır, olay `PENDING` kalır
# ve SONRAKİ HER DÖNGÜ aynı olayda çöker — o kiracının kuyruğu KALICI olarak
# durur. Bu ikizin diğer üç senaryosu (yarış, benzersizlik, rowcount) bu yolu
# HİÇ yürütmüyordu.
_GORUNMEZ_KURULUM = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from sqlalchemy import text as _sql
from app.db import SessionLocal
import app.main
Z = '2026-08-01T00:00:00'
YOK = 987654321  # bu kiracıda BÖYLE bir field_activities satırı YOK
with SessionLocal() as db:
    db.execute(_sql("DELETE FROM stock_movements"))
    db.execute(_sql("DELETE FROM field_integration_events"))
    yok_mu = db.execute(_sql(
        "SELECT id FROM field_activities WHERE id = :s"), {"s": YOK}).first()
    assert yok_mu is None, 'KURULUM GECERSIZ: kaynak satiri VAR, yetim degil'
    db.execute(_sql(
        "INSERT INTO field_integration_events (id,company_id,source_type,"
        "source_id,target,idempotency_key,status,attempts,created_at,updated_at) "
        "VALUES (7,1,'field_activity',:s,'stock','field_activity:yetim:stock',"
        "'PENDING',0,:z,:z)"), {"z": Z, "s": YOK})
    db.commit()
print("GORUNMEZ-KURULUM-TAMAM")
'''

_GORUNMEZ_RAPOR = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from sqlalchemy import text as _sql
from app.db import SessionLocal
with SessionLocal() as db:
    d = db.execute(_sql(
        "SELECT status FROM field_integration_events WHERE id = 7")).scalar_one()
    h = db.execute(_sql("SELECT COUNT(*) FROM stock_movements WHERE "
                        "reference_type = 'field_integration_event'")).scalar_one()
print("DURUM %s HAREKET %d" % (d, h))
'''


def test_gorunmeyen_kaynak_kovasi_PG_de_YAZILABILIYOR() -> None:
    """26 karakterlik durum sütuna sığmalı; sığmazsa kuyruk KALICI durur."""
    assert "GORUNMEZ-KURULUM-TAMAM" in _kos(_GORUNMEZ_KURULUM)
    sayac = _kos(_TUKETICI, {"BASLA": "0"})
    assert "'SKIPPED_SOURCE_NOT_VISIBLE': 1" in sayac, (
        "Kaynağı görünmeyen olay ADI KONMUŞ kovaya düşmedi; sayaç bu koşumda "
        f"o kovayı hiç saymadı: {sayac!r}"
    )
    rapor = _kos(_GORUNMEZ_RAPOR).strip()
    assert "DURUM SKIPPED_SOURCE_NOT_VISIBLE " in rapor, (
        "Olay PostgreSQL'de terminalleşmedi. `status` sütunu 26 karakterlik "
        "bu durumu taşıyamıyorsa yazma StringDataRightTruncation ile geri "
        "alınır, olay PENDING kalır ve SONRAKİ HER DÖNGÜ aynı olayda çöker — "
        f"o kiracının kuyruğu KALICI olarak durur. rapor={rapor!r}"
    )
    assert rapor.endswith("HAREKET 0"), (
        "Olmayan bir kaynak için envanter OYNAMAMALI: " + rapor
    )


# TALEBIN KENDISI PATLARSA. `_talep_et` yarisan bir KOSULLU UPDATE'tir ve
# kilit catismasi onun SIRADAN basarisizligidir. Kilit BASKA bir baglantidan
# `FOR UPDATE` ile alinir, tuketici oturumuna `lock_timeout` konur: yani
# basarisizlik BELIRLENIMCIDIR (yaris DEGIL) ve gercek PG kilit yoneticisinden
# gelir. SQLite'ta bu yol HIC olculemez: orada oturumlar arasi satir kilidi ve
# `lock_timeout` yoktur.
_TALEP_KILIT = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from sqlalchemy import text as _sql
from app.db import SessionLocal
from app.field_stok_tuketici import olaylari_isle

Z = '2026-08-01T00:00:00'
# Kilitli olayin ARKASINA saglam bir olay koy: kuyrugun DEVAM ettigini olcer.
with SessionLocal() as db:
    db.execute(_sql(
        "INSERT INTO field_integration_events (id,company_id,source_type,"
        "source_id,target,idempotency_key,status,attempts,created_at,updated_at) "
        "VALUES (2,1,'field_activity',99999,'stock','talep-kilit:99999:stock',"
        "'PENDING',0,:z,:z) ON CONFLICT (id) DO NOTHING"), {"z": Z})
    db.commit()

kilit = SessionLocal()
kilit.execute(_sql(
    "SELECT id FROM field_integration_events WHERE id = 1 FOR UPDATE")).first()
try:
    with SessionLocal() as db:
        db.execute(_sql("SET SESSION lock_timeout = '400ms'"))
        db.commit()
        try:
            s = olaylari_isle(db, 1)
            print("SAYAC %r" % (s,))
            # KOVAYI ADIYLA BILDIR. `SAYAC %r` YETMEZ: uzerinde hicbir sey
            # assert edilmezse mekanizma HIC atesnlenmeden test yesil kalir.
            print("KURTARILAMADI %d" % s.get('RECOVERY_FAILED', -1))
            print("UYGULANAN %d" % s.get('SENT', -1))
            print("KORUNUM %d %d" % (
                s['girdi'], sum(v for k, v in s.items() if k != 'girdi')))
        except AssertionError:
            print("KORUNUM-PATLADI")
        except Exception as e:
            print("KACAN %s" % type(e).__name__)
finally:
    kilit.rollback()
    kilit.close()

with SessionLocal() as db:
    print("OLAY1 %s" % db.execute(_sql(
        "SELECT status FROM field_integration_events WHERE id = 1")).scalar_one())
    print("OLAY2 %s" % db.execute(_sql(
        "SELECT status FROM field_integration_events WHERE id = 2")).scalar_one())
'''


def test_talep_PATLARSA_kuyruk_DEVAM_eder_ve_korunum_TUTAR() -> None:
    """Talep KORUMANIN İÇİNDE mi? Kilit çatışmasıyla ÖLÇÜLÜR.

    ÖLÇÜLEN KUSUR: `_talep_et` ve onun `db.rollback()`u olay başına `try`ın
    DIŞINDA duruyordu. Orada patlayan bir olay HİÇBİR kovayı artırmadan
    `olaylari_isle`den kaçıyor, korunum assert'ine hiç varılmıyor ve
    `tum_firmalari_isle` ölüyordu — yani SIRADAKİ HER FİRMA ve kilitli olayın
    ARKASINDAKİ HER OLAY işlenmeden kalıyordu.
    """
    assert "KURULUM-TAMAM" in _kos(_KURULUM)
    cikti = _kos(_TALEP_KILIT)

    assert "KACAN" not in cikti, (
        "TALEP KORUMANIN DIŞINDA: kilit çatışması `olaylari_isle`den DIŞARI "
        "kaçtı. Bu olay hiçbir kovayı artırmaz, korunum assert'ine varılmaz "
        "ve tüm-firmalar döngüsü ölür; kilitli olayın arkasındaki kuyruk "
        f"işlenmeden kalır. çıktı={cikti!r}"
    )
    assert "KORUNUM-PATLADI" not in cikti, (
        "Talep patladığında korunum denklemi TUTMADI: bir olay alındı ama "
        f"hiçbir kovaya yazılmadı. çıktı={cikti!r}"
    )
    assert "KORUNUM 2 2" in cikti, (
        "KORUNUM İHLALİ: iki olay alındı ama toplam iki kovaya yazılmadı. "
        f"Her çıkış TAM OLARAK BİR terim artırmalı. çıktı={cikti!r}"
    )
    assert "OLAY2 SKIPPED_SOURCE_NOT_VISIBLE" in cikti, (
        "KUYRUK DURDU: talebi patlayan olayın ARKASINDAKİ olay hiç "
        "işlenmedi. Bir olayın talep hatası kuyruğu durdurmamalı. "
        f"çıktı={cikti!r}"
    )

    # --- MEKANİZMA GERÇEKTEN ATEŞLENDİ Mİ? --------------------------------
    #
    # Yukarıdaki dört assert'in HEPSİ, kilit hiç ısırmasa da tutar: o hâlde
    # olay 1 sıradan biçimde talep edilip UYGULANIR, `KORUNUM 2 2` yine
    # doğrudur, `OLAY2` yine işlenir ve hiçbir istisna kaçmaz. Yani test
    # YEŞİL kalırken sınadığı yol HİÇ koşmamış olur — üstelik talep `try`ın
    # DIŞINA geri konsa bile. Ölçülen kova bu yüzden ADIYLA bağlanıyor.
    assert "KURTARILAMADI 1" in cikti, (
        "MEKANİZMA ATEŞLENMEDİ: kilit çatışması beklenen `RECOVERY_FAILED` "
        "kovasını üretmedi. Ya `FOR UPDATE` kilidi tutmuyor ya `lock_timeout` "
        "tüketici oturumuna uygulanmıyor; her iki hâlde de bu test talebin "
        "KORUMA İÇİNDE olduğunu ÖLÇMÜYOR, yalnızca öyle görünüyor. "
        f"çıktı={cikti!r}"
    )
    assert "UYGULANAN 0" in cikti, (
        "KİLİTLİ OLAY UYGULANMIŞ: olay 1 `SENT` olmuş. Kilit ısırsaydı talep "
        f"hiç kazanılamazdı; bu koşum sınadığı yolu koşmamış. çıktı={cikti!r}"
    )
    assert "OLAY1 PENDING" in cikti, (
        "Talebi patlayan olay TERMİNAL olmuş; kurtarma yazımı da kilide "
        "takıldığı için satır DEĞİŞMEDEN `PENDING` kalmalıydı. "
        f"çıktı={cikti!r}"
    )


# BIR FIRMANIN DONGUSU DUSERSE SIRADAKI FIRMALAR NE OLUR? Oturum GERCEKTEN
# oldurulerek olculur: `pg_terminate_backend` ile tuketicinin backend'i
# dusurulur ve dusen ilk ifade, firma 1'in olay SELECT'i olur. Uretimin
# veritabani uygulama kabinin DISINDA oldugu icin islem ortasinda kopan
# baglanti uzak bir ihtimal degildir. SQLite'ta bu yol HIC olculemez.
_FIRMA_DUSTU = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from sqlalchemy import event, text as _sql
from app.db import SessionLocal, engine
from app.field_stok_tuketici import tum_firmalari_isle

Z = '2026-08-01T00:00:00'
# IKI FIRMA. `ORDER BY id` yuzunden 1 ONCE islenir; oldurulen o olur ve
# 2 "SIRADAKI HER FIRMA" rolunu oynar.
with SessionLocal() as db:
    db.execute(_sql("DELETE FROM field_integration_events"))
    db.execute(_sql(
        "INSERT INTO companies (id,name,is_active,created_at) "
        "VALUES (2,'Ikinci Firma',true,:z) ON CONFLICT (id) DO NOTHING"), {"z": Z})
    for oid, firma in ((1, 1), (2, 2)):
        db.execute(_sql(
            "INSERT INTO field_integration_events (id,company_id,source_type,"
            "source_id,target,idempotency_key,status,attempts,created_at,"
            "updated_at) VALUES (:i,:c,'field_activity',99999,'stock',:k,"
            "'PENDING',0,:z,:z)"),
            {"i": oid, "c": firma, "k": "firma-dustu:%d" % oid, "z": Z})
    db.commit()

# OLDURUCU AYRI BIR BAGLANTI. Tuketicinin backend'ini o dusurur.
oldurucu = SessionLocal()
oldurucu.execute(_sql("SELECT 1"))
durum = {"vuruldu": 0, "pid": None}

@event.listens_for(engine, "before_cursor_execute")
def _vur(conn, cursor, statement, parameters, context, executemany):
    # YALNIZ BIR KEZ ve yalnizca olay SELECT'inde: firma 1'in ilk ifadesi.
    if durum["vuruldu"] or "field_integration_events" not in statement:
        return
    durum["vuruldu"] = 1
    oldurucu.execute(_sql("SELECT pg_terminate_backend(:p)"), {"p": durum["pid"]})
    oldurucu.commit()

with SessionLocal() as db:
    durum["pid"] = db.execute(_sql("SELECT pg_backend_pid()")).scalar_one()
    try:
        s = tum_firmalari_isle(db)
        print("SAYAC %r" % (s,))
        print("FIRMA_DUSTU %d" % s.get("COMPANY_FAILED", -1))
        # KORUNUM: `COMPANY_FAILED` bir OLAY kovasi degildir ve denklemin
        # DISINDADIR; dusen firma iki yana da katki vermez.
        print("KORUNUM %d %d" % (s['girdi'], sum(
            v for k, v in s.items() if k not in ('girdi', 'COMPANY_FAILED'))))
    except AssertionError:
        print("KORUNUM-PATLADI")
    except Exception as e:
        print("KACAN %s" % type(e).__name__)
oldurucu.close()

with SessionLocal() as db:
    for oid in (1, 2):
        print("OLAY%d %s" % (oid, db.execute(_sql(
            "SELECT status FROM field_integration_events WHERE id = :i"),
            {"i": oid}).scalar_one()))
'''


def test_bir_firma_DUSERSE_siradaki_firmalar_ISLENIR_ve_dusus_SAYILIR() -> None:
    """Ölü bir oturum, id'si BÜYÜK firmaları alıp götürmemeli.

    ÖLÇÜLEN AÇIK: `_kurtar` kendi yazımını atlatır, ama ikinci `db.rollback()`
    de patlarsa oturum KULLANILAMAZ kalır. O noktada `tum_firmalari_isle`
    içindeki bir sonraki `olaylari_isle` daha ilk SELECT'te patlıyordu;
    istisna HER İKİ korunum assert'ini de atlayarak dışarı kaçıyor ve id'si
    büyük olan her firma o döngüde HİÇ işlenmiyordu — sayılmadan.
    """
    assert "KURULUM-TAMAM" in _kos(_KURULUM)
    cikti = _kos(_FIRMA_DUSTU)

    assert "KACAN" not in cikti, (
        "FİRMA DÖNGÜSÜ KORUMASIZ: ölü oturum `tum_firmalari_isle`den DIŞARI "
        "kaçtı. Bu kaçış her iki korunum assert'ini de atlar ve id'si büyük "
        f"olan HER FİRMA o döngüde işlenmeden kalır. çıktı={cikti!r}"
    )
    assert "KORUNUM-PATLADI" not in cikti, (
        f"Bir firma düştüğünde korunum denklemi TUTMADI. çıktı={cikti!r}"
    )
    assert "FIRMA_DUSTU 1" in cikti, (
        "DÜŞÜŞ SAYILMADI: aç kalan bir döngü ancak ÇIKARILABİLİR olurdu "
        f"(günlükteki bir izden). Sayılması gerekiyordu. çıktı={cikti!r}"
    )
    assert "KORUNUM 1 1" in cikti, (
        "KORUNUM İHLALİ: düşen firma denklemin İKİ YANINA da katkı vermemeli; "
        "işlenen tek olay (firma 2) tam olarak bir kovaya yazılmalı. "
        f"çıktı={cikti!r}"
    )
    assert "OLAY2 SKIPPED_SOURCE_NOT_VISIBLE" in cikti, (
        "SIRADAKİ FİRMA İŞLENMEDİ: düşen firmanın ARDINDAKİ firma hiç "
        "işlenmedi. Düzeltmenin bütün amacı buydu. "
        f"çıktı={cikti!r}"
    )
    assert "OLAY1 PENDING" in cikti, (
        "Düşen firmanın olayı DEĞİŞMEDEN `PENDING` kalmalıydı; bir sonraki "
        f"döngüde yeniden alınır. çıktı={cikti!r}"
    )


# TAZE OTURUMUN YAZIMI SINIRLI MI? Bu, `_taze_oturumda_kurtar`in KENDI
# oturumu icindir. Once OLCULDU (PG 16.4): bu bastan ONCE tek sinir, asagidaki
# `_TALEP_KILIT` probunun TUKETICI oturumuna koydugu `SET SESSION lock_timeout`
# idi; taze oturum havuzdan BASKA bir baglanti alir ve onu DEVRALMAZ. O yuzden
# BURADA HICBIR `SET` YOKTUR — ne oturumda ne sunucuda — ve prob sunucunun
# `lock_timeout`unun GERCEKTEN 0 oldugunu ONCE dogrular. Boylece olculen sinir
# ORTAMIN degil KODUN ozelligidir. Sinir yoksa bu UPDATE tek thread'li
# zamanlayicinin ICINDE sonsuza kadar bekler ve HIC dongu satiri uretmez.
_TAZE_KILIT = r'''
import os, sys, time
sys.path.insert(0, os.environ["BACKEND"])
from sqlalchemy import text as _sql
from app.db import SessionLocal
from app import field_stok_tuketici as C

Z = '2026-08-01T00:00:00'
with SessionLocal() as db:
    # SUNUCUNUN KENDI USTLERI: 0 olmalari probun gecerlilik KOSULUDUR.
    print("SUNUCU lock=%s ifade=%s" % (
        db.execute(_sql("SHOW lock_timeout")).scalar_one(),
        db.execute(_sql("SHOW statement_timeout")).scalar_one()))
    # KAYNAKTAKI ustler: assert'in esigi BURADAN turer, sabit yazilmaz.
    print("USTLER lock=%d ifade=%d" % (
        C.KILIT_ZAMAN_ASIMI_MS, C.IFADE_ZAMAN_ASIMI_MS))
    db.execute(_sql("DELETE FROM field_integration_events"))
    db.execute(_sql(
        "INSERT INTO field_integration_events (id,company_id,source_type,"
        "source_id,target,idempotency_key,status,attempts,created_at,updated_at)"
        " VALUES (1,1,'field_activity',99999,'stock','tazekilit:1','PENDING',1,:z,:z)"),
        {"z": Z})
    db.commit()

kilit = SessionLocal()
kilit.execute(_sql("SELECT id FROM field_integration_events WHERE id=1 FOR UPDATE")).first()
try:
    # IKI YOL DA TAZE OTURUMDA YAZAR: tavan ALTI (`_denemeyi_kaydet`) ve tavan
    # DOLU (`_olayi_sonlandir`). Ikisi de sinirli olmali.
    for etiket, deneme in (("ALTI", 1), ("DOLU", C.AZAMI_DENEME)):
        with SessionLocal() as db:
            t0 = time.perf_counter()
            kova = C._taze_oturumda_kurtar(db, 1, 1, deneme, C.AZAMI_DENEME, "prob")
            print("YOL %s kova=%s sure=%.3f" % (
                etiket, kova, time.perf_counter() - t0))
finally:
    kilit.rollback(); kilit.close()

with SessionLocal() as db:
    r = db.execute(_sql(
        "SELECT status, attempts FROM field_integration_events WHERE id=1")).first()
    print("SATIR %s %d" % (r[0], int(r[1])))
'''


def test_TAZE_oturum_kilit_altinda_SINIRLI_surede_REDDEDIYOR() -> None:
    """Eskalasyonun yazımı SONSUZA KADAR bekleyebilir mi? ÖLÇÜLÜR.

    ÖLÇÜLEN KUSUR: `_taze_oturumda_kurtar` taze bir oturum açar ve oraya
    HİÇBİR üst konmuyordu. Bu başın PG ikizinde bu yol yine de "reddediyor"
    görünüyordu — ama o sınır `_TALEP_KILIT` probunun TÜKETİCİ oturumuna
    koyduğu `SET SESSION lock_timeout`tan geliyordu ve taze oturum havuzdan
    BAŞKA bir bağlantı alır. Yani ölçülen şey kodun değil TESTİN özelliğiydi.
    Sunucunun varsayılanı (`lock_timeout = 0`) altında bu UPDATE, tek
    thread'li zamanlayıcının İÇİNDE sonsuza kadar bekler — ve o hâl hiç döngü
    satırı üretmediği için, yerine geçtiği gürültülü sonsuz yeniden denemeden
    DAHA AZ görünürdür.

    Bu prob bu yüzden HİÇBİR `SET` içermez ve sunucunun üstlerinin 0 olduğunu
    ÖNCE doğrular: geriye kalan tek sınır kaynaktakidir.
    """
    assert "KURULUM-TAMAM" in _kos(_KURULUM)
    cikti = _kos(_TAZE_KILIT)

    # --- PROBUN GECERLILIK KOSULU ----------------------------------------
    assert "SUNUCU lock=0 ifade=0" in cikti, (
        "PROB GEÇERSİZ: sunucunun kendi `lock_timeout`/`statement_timeout`u 0 "
        "DEĞİL. O hâlde aşağıda ölçülen sınır KODUN değil ORTAMIN özelliği "
        f"olabilir ve bu test hiçbir şey kanıtlamaz. çıktı={cikti!r}"
    )
    for etiket in ("ALTI", "DOLU"):
        assert f"YOL {etiket} kova=RECOVERY_FAILED" in cikti, (
            f"TAZE oturumun `{etiket}` yolu kilit altında ADI KONMUŞ kovayla "
            f"reddetmedi. çıktı={cikti!r}"
        )
    # --- SINIR GERCEKTEN ISIRDI MI ---------------------------------------
    import re

    sureler = [float(x) for x in re.findall(r"YOL \w+ kova=\S+ sure=([\d.]+)", cikti)]
    assert len(sureler) == 2, f"iki yol da ölçülmedi. çıktı={cikti!r}"
    ustler = re.search(r"USTLER lock=(\d+) ifade=(\d+)", cikti)
    assert ustler, f"kaynaktaki üstler bildirilmedi. çıktı={cikti!r}"
    # ESIK KAYNAKTAN TURER: iki üstün TOPLAMI, yani hangi üst ısırırsa ısırsın
    # (ve süreç başlama payı da dâhil) geçerli bir tavan. Sabit yazılsaydı
    # kaynaktaki değer büyüdüğünde test SEBEPSİZ kırmızıya dönerdi.
    ust = (int(ustler.group(1)) + int(ustler.group(2))) / 1000.0
    for sure in sureler:
        assert sure < ust, (
            f"TAZE oturum {sure:.1f} sn bekledi; kaynaktaki üst ({ust:.1f} sn) "
            "ISIRMADI. Sınır yoksa bu bekleme SONSUZDUR ve zamanlayıcı tek "
            f"thread'lidir. çıktı={cikti!r}"
        )
    assert "SATIR PENDING 1" in cikti, (
        "Kilit altında yazamayan kurtarma satırı DEĞİŞTİRMİŞ. Deneme "
        "YAKILMAMALI ve olay `PENDING` kalmalıydı: kilit KÜRESEL bir durumdur, "
        f"olayın zehirli olmasından kaynaklanmaz. çıktı={cikti!r}"
    )


# KURTARMA TAVAN YAZIMI BASKA BIR ISCININ SONUCUNU EZER MI? IKI OTURUM,
# BELIRLENIMCI SIRA (yaris YOK): once B olayi bitirir (`SENT`), sonra A kendi
# ESKI anlik goruntusuyle tavan yazimina gelir. `--workers N` ile N tuketici
# kosar, yani bu sira uretimde OLUSABILIR.
#
# BU TEHLIKE ONCEDEN VARDI: `_kurtar`in tavan yazimi bu bastan ONCE de kosulsuz
# ve `attempts`i MUTLAK yaziyordu. Bu dal onu YARATMADI, `_taze_oturumda_kurtar`
# ile IKINCI bir yola TASIDI. Prob bu yuzden IKI yolu da olcer ve degisiklik bir
# GENISLETME degil DARALTMADIR.
_TAVAN_EZME = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from sqlalchemy import text as _sql
from app.db import SessionLocal
from app import field_stok_tuketici as C

Z = '2026-08-01T00:00:00'
AZAMI = C.AZAMI_DENEME

def _kur():
    with SessionLocal() as db:
        db.execute(_sql("DELETE FROM field_integration_events"))
        db.execute(_sql(
            "INSERT INTO field_integration_events (id,company_id,source_type,"
            "source_id,target,idempotency_key,status,attempts,created_at,"
            "updated_at) VALUES (1,1,'field_activity',99999,'stock','ezme:1',"
            "'PENDING',:a,:z,:z)"), {"z": Z, "a": AZAMI - 1})
        db.commit()

def _durum():
    with SessionLocal() as db:
        r = db.execute(_sql(
            "SELECT status, attempts FROM field_integration_events WHERE id=1"
        )).first()
        return r[0], int(r[1])

for etiket, cagir in (
    ("YEREL", lambda db: C._kurtar(db, 1, 1, AZAMI, AZAMI, "prob")),
    ("TAZE", lambda db: C._taze_oturumda_kurtar(db, 1, 1, AZAMI, AZAMI, "prob")),
):
    _kur()
    # OTURUM B (baska bir isci): olayi TALEP edip BITIRIYOR.
    with SessionLocal() as b:
        assert C._talep_et(b, 1, 1), "B talebi kazanmaliydi"
        C._olayi_sonlandir(b, 1, 1, C.DURUM_UYGULANDI, None, AZAMI)
        b.commit()
    print("%s B-SONRASI %s %d" % ((etiket,) + _durum()))
    # OTURUM A (bu isci): ESKI goruntusuyle tavan yazimina geliyor.
    with SessionLocal() as a:
        kova = cagir(a)
    d, n = _durum()
    print("%s A-SONRASI kova=%s durum=%s attempts=%d" % (etiket, kova, d, n))
'''


def test_kurtarma_TAVAN_yazimi_TERMINAL_satiri_EZMIYOR() -> None:
    """Tavan yazımı BAŞKA bir işçinin bitirdiği olayı geri alabilir mi?

    ÖLÇÜLEN TEHLİKE: kurtarma kolunun tavan yazımı KOŞULSUZDU ve `attempts`i
    döngü başındaki anlık görüntüden türeyen MUTLAK bir değerle eziyordu.
    `--workers N` ile iki işçi aynı olayın anlık görüntüsünü okuyabilir; biri
    olayı talep edip `SENT` yaparken diğeri kendi ESKİ görüntüsüyle tavan
    yazımına gelir ve UYGULANMIŞ olayı `DEAD`e çevirip sayacı geri yürütür.

    Bu tehlike bu daldan ÖNCE de vardı (`_kurtar`); bu dal onu ikinci bir yola
    (`_taze_oturumda_kurtar`) taşıdı. Aşağıdaki iki yol da ölçülür — yani
    yapılan şey bir genişletme değil DARALTMADIR.

    BELİRLENİMCİ: iki oturum SIRAYLA koşar, yarış yoktur.
    """
    assert "KURULUM-TAMAM" in _kos(_KURULUM)
    cikti = _kos(_TAVAN_EZME)

    for etiket in ("YEREL", "TAZE"):
        # Ön koşul: B gerçekten bitirmiş olmalı, yoksa prob bir şey ölçmez.
        assert f"{etiket} B-SONRASI SENT" in cikti, (
            f"PROB GEÇERSİZ ({etiket}): diğer işçi olayı bitiremedi, yani "
            f"ezilecek bir terminal satır hiç oluşmadı. çıktı={cikti!r}"
        )
        assert f"{etiket} A-SONRASI kova=DEAD durum=SENT" in cikti, (
            f"TERMİNAL SATIR EZİLDİ ({etiket}): başka bir işçinin UYGULADIĞI "
            "olay, bu işçinin eski görüntüsüyle `DEAD` yazıldı. Tavan yazımı "
            f"`status` koşuluna bağlı olmalıydı. çıktı={cikti!r}"
        )
        assert f"{etiket} A-SONRASI kova=DEAD durum=SENT attempts=" in cikti
    # Sayac GERI YURUMEDI: B'nin yazdigi deger duruyor.
    import re

    for etiket in ("YEREL", "TAZE"):
        once = int(re.search(rf"{etiket} B-SONRASI SENT (\d+)", cikti).group(1))
        sonra = int(re.search(
            rf"{etiket} A-SONRASI kova=DEAD durum=SENT attempts=(\d+)", cikti
        ).group(1))
        assert sonra == once, (
            f"SAYAÇ GERİ YÜRÜDÜ ({etiket}): {once} -> {sonra}. Tavan yazımı "
            f"`attempts`i MUTLAK değil BAĞIL yazmalıydı. çıktı={cikti!r}"
        )


# OTURUM ZEHIRLI AMA VERITABANI AYAKTA. Bu sinifta `_kurtar`in YEREL yazimi
# patlar, ama TAZE bir oturum yazabilir (OLCULDU). Eskalasyon olmadan
# `attempts` HIC artmaz, `AZAMI_DENEME` HIC dolmaz ve olay SONSUZA KADAR
# yeniden denenir. Burada tavanin bu yoldan da ULASILABILIR oldugu, ve ERKEN
# dolmadigi, iki yonde olculur. SQLite'ta bu yol HIC olculemez.
_ZEHIRLI_OTURUM = r'''
import os, sys
sys.path.insert(0, os.environ["BACKEND"])
from sqlalchemy import event, text as _sql
from app.db import SessionLocal, engine
from app import field_stok_tuketici as C

Z = '2026-08-01T00:00:00'
AZAMI = C.AZAMI_DENEME
with SessionLocal() as db:
    db.execute(_sql("DELETE FROM field_integration_events"))
    db.execute(_sql(
        "INSERT INTO field_integration_events (id,company_id,source_type,"
        "source_id,target,idempotency_key,status,attempts,created_at,updated_at)"
        " VALUES (1,1,'field_activity',99999,'stock','zehirli:1','PENDING',0,:z,:z)"),
        {"z": Z})
    db.commit()

katil = SessionLocal()
katil.execute(_sql("SELECT 1"))
kur = {"acik": False, "pid": None}

# TALEP UPDATE'i BASARIYLA kostuktan SONRA oldurulur. ZAMANLAMA BELIRLEYICI
# (olculdu): ifade KOSMADAN once oldurulurse SQLAlchemy baglantiyi gecersiz
# sayar, `db.rollback()` sunucuya HIC gitmeden basarili olur ve YEREL kurtarma
# yazimi taze bir havuz baglantisiyla calisir -> `RETRY_SCHEDULED`. Islem ICINDE
# is yapildiktan SONRA oldurulurse `ROLLBACK` OLU sokete gitmek zorundadir ve
# `_kurtar`in ILK adimi patlar -> incelenen sinif budur.
@event.listens_for(engine, "after_cursor_execute")
def _vur(conn, cursor, statement, parameters, context, executemany):
    if not kur["acik"] or "field_integration_events" not in statement:
        return
    if not statement.lstrip().upper().startswith("UPDATE"):
        return
    kur["acik"] = False
    katil.execute(_sql("SELECT pg_terminate_backend(:p)"), {"p": kur["pid"]})
    katil.commit()

# TETIKLEYICI ISTISNA VERITABANI HATASI OLMAMALI. ZAMANLAMA BELIRLEYICI
# (olculdu): eger olduruleN baglantiya once BIR IFADE giderse SQLAlchemy
# baglantiyi GECERSIZ isaretler ve `db.rollback()` sunucuya hic gitmeden
# basarili olur. Incelenen sinif, kopusun ANCAK `ROLLBACK` tarafindan fark
# edildigi haldir: yani islem is yapmisken soket olur (RDS failover, NAT/idle
# reaper) ve ardindan VERITABANI DISI bir hata olusur. Kaynak okuyucusu bu
# yuzden veritabanina DOKUNMADAN patlatiliyor.
_eski_kaynak = C._KAYNAK["field_activity"]

def _patlat(db, firma, sid):
    raise ValueError("kaynak okuyucu ZEHIR (veritabanina DOKUNMAZ)")

C._KAYNAK["field_activity"] = (_eski_kaynak[0], _eski_kaynak[1], _patlat)


def _durum():
    with SessionLocal() as db:
        r = db.execute(_sql(
            "SELECT status, attempts FROM field_integration_events WHERE id=1")).first()
        return (r[0], int(r[1])) if r else ("YOK", -1)

# TAVANI BILDIR: testin tur assert'leri BU degerden turer, sabit yazilmaz.
print("AZAMI %d" % AZAMI)

for tur in range(1, AZAMI + 2):
    with SessionLocal() as db:
        kur["pid"] = db.execute(_sql("SELECT pg_backend_pid()")).scalar_one()
        kur["acik"] = True
        try:
            s = C.olaylari_isle(db, 1)
        except Exception as e:
            print("KACAN %s" % type(e).__name__); break
    kur["acik"] = False
    d, a = _durum()
    kova = [k for k, v in s.items() if k != 'girdi' and v]
    print("TUR %d girdi=%d kova=%s durum=%s attempts=%d" % (
        tur, s['girdi'], ",".join(sorted(kova)) or "-", d, a))
    # KORUNUM her turda tutmali
    print("KORUNUM %d %d %d" % (tur, s['girdi'], sum(
        v for k, v in s.items() if k != 'girdi')))

print("DB-AYAKTA %s" % katil.execute(_sql("SELECT 'EVET'")).scalar_one())
katil.close()
'''


def test_ZEHIRLI_OTURUMDA_tavan_ULASILABILIR_ve_ERKEN_dolmaz() -> None:
    """Kurtarma yazımı yerelde patlarken olay yine de tavana varmalı.

    ÖLÇÜLEN AÇIK: `RECOVERY_FAILED` olayı `PENDING` ve `attempts`i DEĞİŞMEMİŞ
    bırakır. Kusur oturumdaysa (bağlantısı ölmüş ama veritabanı AYAKTA) her
    döngüde aynı şey olur: deneme hiç artmaz, `AZAMI_DENEME` hiç dolmaz ve
    olay SONSUZA KADAR yeniden denenir — bu PR'ın var oluş sebebi olan tavan
    tam da bu sınıfı emekli edemez.
    """
    assert "KURULUM-TAMAM" in _kos(_KURULUM)
    cikti = _kos(_ZEHIRLI_OTURUM)

    assert "KACAN" not in cikti, f"olaylari_isle DIŞARI kaçtı. çıktı={cikti!r}"
    assert "DB-AYAKTA EVET" in cikti, (
        "PROB GEÇERSİZ: veritabanı ayakta DEĞİLDİ, yani ölçülen şey 'oturum "
        f"zehirli ama DB sağlam' sınıfı değil. çıktı={cikti!r}"
    )
    # MEKANİZMA ATEŞLENDİ Mİ: yerel yazım patlayıp TAZE oturum devraldı mı?
    assert "RECOVERY_ESCALATED" in cikti, (
        "MEKANİZMA ATEŞLENMEDİ: hiçbir turda eskalasyon olmadı. Ya backend "
        "öldürülemedi ya da yerel kurtarma yazımı patlamadı; her iki hâlde de "
        f"bu test tavanın bu yoldan ulaşılabilirliğini ÖLÇMÜYOR. çıktı={cikti!r}"
    )
    # --- TUR BEKLENTILERI `AZAMI_DENEME`DEN TURER -----------------------
    #
    # 1/2/3/4 SABIT YAZILMAZ. Tavan bir SABITTIR (`AZAMI_DENEME`) ve degeri
    # degistiginde bu assert'ler kaynakla birlikte hareket etmeli; aksi hâlde
    # tavan 5 yapildiginda test, olcmek istedigi seyi degil eski sayilari
    # sinar ve SEBEPSIZ kirmiziya doner. Prob tavani ADIYLA bildiriyor.
    import re

    azami_es = re.search(r"AZAMI (\d+)", cikti)
    assert azami_es, f"prob tavanı bildirmedi. çıktı={cikti!r}"
    azami = int(azami_es.group(1))
    assert azami >= 2, (
        f"Bu prob en az iki turluk bir tavan ölçebilir; AZAMI_DENEME={azami}"
    )

    # ERKEN DOLMAZ: tavandan ONCEKI her turda olay PENDING kalir ve deneme
    # tam olarak BIR artar.
    for tur in range(1, azami):
        assert (
            f"TUR {tur} girdi=1 kova=RECOVERY_ESCALATED durum=PENDING "
            f"attempts={tur}" in cikti
        ), (
            f"TAVAN ERKEN DOLDU ya da deneme BİRİKMEDİ: tur {tur} sonunda "
            f"attempts {tur} ve durum PENDING olmalıydı (AZAMI_DENEME="
            f"{azami}). çıktı={cikti!r}"
        )
    # TAVAN DOLDU: olay OLU.
    assert (
        f"TUR {azami} girdi=1 kova=DEAD durum=DEAD attempts={azami}" in cikti
    ), (
        f"TAVAN ULAŞILAMAZ: {azami} denemeden sonra olay hâlâ ölmedi. "
        "Eskalasyon yalnız denemeyi yazıp tavan kararını ölü oturuma "
        f"bırakırsa olay tavana varır ama HİÇ emekli olamaz. çıktı={cikti!r}"
    )
    # TAVANDAN SONRA: artik SECILMIYOR.
    assert f"TUR {azami + 1} girdi=0" in cikti, (
        "OLAY HÂLÂ SEÇİLİYOR: terminal olduktan sonra kuyruktan düşmeliydi. "
        f"çıktı={cikti!r}"
    )
    for tur in range(1, azami + 2):
        assert f"KORUNUM {tur} " in cikti
    assert f"KORUNUM {azami + 1} 0 0" in cikti, (
        f"boş turda korunum tutmadı. çıktı={cikti!r}"
    )


# TALEBIN KENDISI SINIRLI MI? Bu, TUKETICININ KENDI oturumu icindir.
#
# NEDEN AYRI BIR PROB: yukaridaki `_TALEP_KILIT` ayni yolu zaten kosuyor, ama
# tuketici oturumuna `SET SESSION lock_timeout = '400ms'` KOYARAK. Yani orada
# olculen sinir KODUN degil TESTIN ozelligidir — `_TAZE_KILIT`in kayda gecirdigi
# tuzagin ta kendisi. Bu prob bu yuzden HICBIR `SET` icermez (ne oturumda ne
# sunucuda) ve sunucunun ustlerinin GERCEKTEN 0 oldugunu ONCE dogrular. Sinir
# yoksa `_talep_et` tek thread'li zamanlayicinin ICINDE sonsuza kadar bekler ve
# tuketici, sinirli olan eskalasyona HIC VARAMAZ.
#
# AYIRT EDICI: olayin `source_id`si YOKTUR. Kilit ISIRMAZSA olay saniye altinda
# `SKIPPED_SOURCE_NOT_VISIBLE` olur — yani "kilit hic tutmadi" hali, olculen
# `RECOVERY_FAILED` halinden ADIYLA ayrilir ve test sessizce yesil kalamaz.
_TALEP_USTU = r'''
import os, sys, time
sys.path.insert(0, os.environ["BACKEND"])
from sqlalchemy import text as _sql
from app.db import SessionLocal
from app import field_stok_tuketici as C
from app.field_stok_tuketici import olaylari_isle

Z = '2026-08-01T00:00:00'
with SessionLocal() as db:
    # SUNUCUNUN KENDI USTLERI: 0 olmalari probun gecerlilik KOSULUDUR.
    print("SUNUCU lock=%s ifade=%s" % (
        db.execute(_sql("SHOW lock_timeout")).scalar_one(),
        db.execute(_sql("SHOW statement_timeout")).scalar_one()))
    # KAYNAKTAKI ustler: esik BURADAN turer, sabit yazilmaz.
    print("USTLER lock=%d ifade=%d" % (
        C.KILIT_ZAMAN_ASIMI_MS, C.IFADE_ZAMAN_ASIMI_MS))
    db.execute(_sql("DELETE FROM field_integration_events"))
    db.execute(_sql(
        "INSERT INTO field_integration_events (id,company_id,source_type,"
        "source_id,target,idempotency_key,status,attempts,created_at,updated_at)"
        " VALUES (1,1,'field_activity',99999,'stock','talepustu:1','PENDING',0,:z,:z)"),
        {"z": Z})
    db.commit()

kilit = SessionLocal()
kilit.execute(_sql(
    "SELECT id FROM field_integration_events WHERE id=1 FOR UPDATE")).first()
try:
    with SessionLocal() as db:
        t0 = time.perf_counter()
        try:
            sayac = olaylari_isle(db, 1)
            print("KURTARILAMADI %d" % sayac.get('RECOVERY_FAILED', -1))
            print("GORUNMEZ %d" % sayac.get('SKIPPED_SOURCE_NOT_VISIBLE', -1))
            print("UYGULANAN %d" % sayac.get('SENT', -1))
            print("KORUNUM %d %d" % (
                sayac['girdi'], sum(v for k, v in sayac.items() if k != 'girdi')))
        except AssertionError:
            print("KORUNUM-PATLADI")
        except Exception as e:
            print("KACAN %s" % type(e).__name__)
        print("SURE %.3f" % (time.perf_counter() - t0))
finally:
    kilit.rollback(); kilit.close()

with SessionLocal() as db:
    r = db.execute(_sql(
        "SELECT status, attempts FROM field_integration_events WHERE id=1")).first()
    print("SATIR %s %d" % (r[0], int(r[1])))
'''


def test_TALEP_kilit_altinda_SINIRLI_surede_REDDEDIYOR() -> None:
    """Tüketicinin KENDİ oturumu talepte sonsuza kadar bekler mi? ÖLÇÜLÜR.

    ÖLÇÜLEN KUSUR: kurtarma yolunun üstü (`_taze_oturumda_kurtar`) sınırlıydı
    ama ona GİDEN yol değildi. `_talep_et` yarışan bir KOŞULLU UPDATE'tir ve
    sunucunun varsayılanı (`lock_timeout = 0`) altında, başka bir oturum aynı
    satırı tutuyorsa SONSUZA KADAR bekler — tek thread'li zamanlayıcının
    İÇİNDE. Yani sınırlı eskalasyonun ÖNÜNDE sınırsız bir kapı vardı ve
    tüketici o eskalasyona HİÇ VARAMADAN asılı kalırdı: ateşlenemeyen bir
    mekanizma.

    Bu prob HİÇBİR `SET` içermez ve sunucunun üstlerinin 0 olduğunu ÖNCE
    doğrular; böylece ölçülen sınır ORTAMIN değil KODUN özelliğidir.
    """
    assert "KURULUM-TAMAM" in _kos(_KURULUM)
    cikti = _kos(_TALEP_USTU)

    assert "SUNUCU lock=0 ifade=0" in cikti, (
        "PROB GEÇERSİZ: sunucunun kendi `lock_timeout`/`statement_timeout`u 0 "
        "değil. O hâlde ölçülen sınır kodun değil ORTAMIN özelliği olurdu. "
        f"çıktı={cikti!r}"
    )
    assert "KACAN" not in cikti, (
        "TALEP KORUMANIN DIŞINDA: kilit çatışması `olaylari_isle`den DIŞARI "
        f"kaçtı. çıktı={cikti!r}"
    )
    assert "KORUNUM-PATLADI" not in cikti, (
        f"Talep patladığında korunum denklemi TUTMADI. çıktı={cikti!r}"
    )
    assert "KORUNUM 1 1" in cikti, (
        f"KORUNUM İHLALİ: bir olay alındı, bir kovaya yazılmadı. çıktı={cikti!r}"
    )

    # --- MEKANİZMA GERÇEKTEN ATEŞLENDİ Mİ? --------------------------------
    #
    # Kilit ısırmasaydı olay saniye altında `SKIPPED_SOURCE_NOT_VISIBLE`
    # olurdu (`source_id` YOK). Kova ADIYLA bağlanıyor ki test sınadığı yolu
    # koşmadan yeşil kalamasın.
    assert "KURTARILAMADI 1" in cikti, (
        "MEKANİZMA ATEŞLENMEDİ: kilit çatışması beklenen `RECOVERY_FAILED` "
        "kovasını üretmedi. Ya `FOR UPDATE` kilidi tutmuyor ya talep üstün "
        f"ALTINDA değil. çıktı={cikti!r}"
    )
    assert "GORUNMEZ 0" in cikti, (
        "KİLİT HİÇ ISIRMAMIŞ: olay sıradan biçimde talep edilip yetim "
        f"kovasına düşmüş. Bu koşum sınadığı yolu koşmamış. çıktı={cikti!r}"
    )

    # --- SINIR: SONLU ve SABİTLERDEN TÜREYEN bir tavanın ALTINDA -----------
    #
    # Üç bacak da kilidi bekler: `_talep_et`, `_kurtar`ın aynı oturumdaki
    # yazımı ve `_taze_oturumda_kurtar`ın yazımı. Tavan bu yüzden `lock_timeout`un
    # KATI olarak türetilir, sabit yazılmaz.
    import re

    ust_ms = int(re.search(r"USTLER lock=(\d+)", cikti).group(1))
    sure = float(re.search(r"SURE ([0-9.]+)", cikti).group(1))
    assert sure >= ust_ms / 1000.0, (
        f"ÇOK HIZLI: {sure:.3f} sn tek bir kilit beklemesinden bile kısa; "
        f"kilit ısırmamış olabilir. çıktı={cikti!r}"
    )
    assert sure <= 4 * ust_ms / 1000.0 + 10.0, (
        f"SINIRSIZ: talep {sure:.3f} sn bekledi; `lock_timeout` uygulanmıyor. "
        f"çıktı={cikti!r}"
    )

    # --- DENEME YAKILMADI --------------------------------------------------
    #
    # Kilit KÜRESEL ve geçici bir durumdur (başka bir işçinin uçuştaki
    # talebi). Masum olayı tavana sürmek yanlış olurdu.
    assert "SATIR PENDING 0" in cikti, (
        "DENEME YAKILDI ya da olay TERMINAL oldu: kilit çatışması küresel bir "
        f"durumdur, olayı tavana sürmemeli. çıktı={cikti!r}"
    )


# DONGUNUN KENDISI SINIRLI MI? OLCULDU (bu ustlerle, bu makine): 20 CATISMALI
# olay SINIRSIZ dongude 180.58 sn surdu — olay basina 9.03 sn (uc kilit
# bekleme bacagi x 3 sn), dogrusal. 30 sn'lik aralik donguler ARASINDAKI
# beklemedir; dongunun kendisi sinirsiz buyuyebiliyordu. Bu prob AYNI sekli
# kurar (>=20 catismali olay, tek dongu) ve VARSAYILAN sinirlarla olcer:
# hicbir parametre GECIRILMEZ, yani olculen sey testin degil KODUN sinirdir.
_DONGU_BUTCESI = r'''
import os, sys, time
sys.path.insert(0, os.environ["BACKEND"])
from sqlalchemy import text as _sql
from app.db import SessionLocal
from app import field_stok_tuketici as C

Z = '2026-08-01T00:00:00'
N = 20
with SessionLocal() as db:
    # SUNUCUNUN KENDI USTLERI: 0 olmalari probun gecerlilik KOSULUDUR.
    print("SUNUCU lock=%s ifade=%s" % (
        db.execute(_sql("SHOW lock_timeout")).scalar_one(),
        db.execute(_sql("SHOW statement_timeout")).scalar_one()))
    # KAYNAKTAKI sinirlar: esikler BURADAN turer, sabit yazilmaz.
    print("SINIRLAR butce=%s parti=%d kilit=%d ifade=%d" % (
        C.DONGU_SURE_BUTCESI_SANIYE, C.AZAMI_PARTI,
        C.KILIT_ZAMAN_ASIMI_MS, C.IFADE_ZAMAN_ASIMI_MS))
    db.execute(_sql("DELETE FROM stock_movements"))
    db.execute(_sql("DELETE FROM field_integration_events"))
    for i in range(1, N + 1):
        db.execute(_sql(
            "INSERT INTO field_integration_events (id,company_id,source_type,"
            "source_id,target,idempotency_key,status,attempts,created_at,"
            "updated_at) VALUES (:i,1,'field_activity',:s,'stock',:k,"
            "'PENDING',0,:z,:z)"), {"i": i, "s": 900000 + i,
                                    "k": "butce:%d" % i, "z": Z})
    db.commit()

def _bekleyen():
    with SessionLocal() as db:
        return db.execute(_sql(
            "SELECT COUNT(*) FROM field_integration_events "
            "WHERE status = 'PENDING'")).scalar_one()

# CATISMA: baska bir oturum HER olay satirini tutuyor. Tuketicinin talebi
# (ve kurtarma yazimlari) kilitte bekler — olculen 9.03 sn/olay sekli.
kilit = SessionLocal()
kilit.execute(_sql(
    "SELECT id FROM field_integration_events FOR UPDATE")).all()
try:
    t0 = time.perf_counter()
    with SessionLocal() as db:
        s1 = C.tum_firmalari_isle(db)
    sure1 = time.perf_counter() - t0
finally:
    kilit.rollback(); kilit.close()

print("DONGU1 sure=%.2f girdi=%d kurtarilamadi=%d" % (
    sure1, s1['girdi'], s1.get('RECOVERY_FAILED', -1)))
print("KORUNUM1 %d %d" % (s1['girdi'], sum(
    v for k, v in s1.items() if k not in ('girdi', 'COMPANY_FAILED'))))
print("BEKLEYEN1 %d" % _bekleyen())

# KILIT KALKTI: kalanlar SONRAKI dongude islenmeli.
t0 = time.perf_counter()
with SessionLocal() as db:
    s2 = C.tum_firmalari_isle(db)
sure2 = time.perf_counter() - t0
print("DONGU2 sure=%.2f girdi=%d" % (sure2, s2['girdi']))
print("KORUNUM2 %d %d" % (s2['girdi'], sum(
    v for k, v in s2.items() if k not in ('girdi', 'COMPANY_FAILED'))))
print("BEKLEYEN2 %d" % _bekleyen())
with SessionLocal() as db:
    print("TERMINAL %d" % db.execute(_sql(
        "SELECT COUNT(*) FROM field_integration_events "
        "WHERE status = 'SKIPPED_SOURCE_NOT_VISIBLE'")).scalar_one())
'''


def test_dongu_SURE_BUTCESI_catismada_ISIRIYOR_ve_kalan_SONRAKI_dongude() -> None:
    """20 çatışmalı olayda döngü artık 180 sn DEĞİL: bütçe keser, kalan sonra.

    ÖLÇÜLEN KUSUR: `tum_firmalari_isle` hiçbir sınır geçirmiyordu ve 20
    çatışmalı olay TEK döngüde 180.58 sn sürdü (9.03 sn/olay, doğrusal,
    SINIRSIZ) — 30 sn'lik aralığa karşı. Bu prob aynı şekli kurar ve
    VARSAYILAN sınırlarla ölçer; eşikler kaynaktaki sabitlerden türer.

    İKİ YÖN: bütçe kaldırılırsa döngü ~9 sn x 20 = ~180 sn sürer ve türetilen
    tavanı (bütçe + tek olayın kendi üstleri) AŞAR — kırmızı. Bütçe dururken
    döngü tavanın altında biter, alınmayan olaylar `PENDING` kalır ve kilit
    kalkınca BİR SONRAKİ döngü hepsini işler — yeşil.
    """
    assert "KURULUM-TAMAM" in _kos(_KURULUM)
    cikti = _kos(_DONGU_BUTCESI)

    # --- PROBUN GECERLILIK KOSULU ----------------------------------------
    assert "SUNUCU lock=0 ifade=0" in cikti, (
        "PROB GEÇERSİZ: sunucunun kendi üstleri 0 değil; ölçülen sınır KODUN "
        f"değil ORTAMIN özelliği olabilir. çıktı={cikti!r}"
    )
    import re

    sinirlar = re.search(
        r"SINIRLAR butce=([\d.]+) parti=(\d+) kilit=(\d+) ifade=(\d+)", cikti)
    assert sinirlar, f"prob sınırları bildirmedi. çıktı={cikti!r}"
    butce = float(sinirlar.group(1))
    kilit_sn = int(sinirlar.group(3)) / 1000.0
    ifade_sn = int(sinirlar.group(4)) / 1000.0

    d1 = re.search(r"DONGU1 sure=([\d.]+) girdi=(\d+) kurtarilamadi=(\d+)", cikti)
    assert d1, f"döngü 1 ölçülmedi. çıktı={cikti!r}"
    sure1, girdi1, kurtarilamadi1 = (
        float(d1.group(1)), int(d1.group(2)), int(d1.group(3)))

    # --- MEKANİZMA GERÇEKTEN ATEŞLENDİ Mİ? -------------------------------
    #
    # NEGATİF KONTROL DOĞRULANIR: kilit ısırmasaydı olaylar saniye altında
    # `SKIPPED_SOURCE_NOT_VISIBLE` olurdu (source_id YOK) ve girdi 20 olurdu.
    # Alınan HER olayın (a) `RECOVERY_FAILED` kovasına düştüğü ve (b) en az
    # bir kilit beklemesi ÖDEDİĞİ ölçülür.
    assert girdi1 >= 1, f"döngü hiç olay almadı. çıktı={cikti!r}"
    assert kurtarilamadi1 == girdi1, (
        "ÇATIŞMA OLUŞMADI: alınan olayların hepsi kilit altında değildi; bu "
        f"koşum süre bütçesini ÇATIŞMA altında ölçmüyor. çıktı={cikti!r}"
    )
    assert sure1 >= girdi1 * kilit_sn, (
        f"ÇOK HIZLI: {sure1:.2f} sn, {girdi1} olayın birer kilit beklemesinden "
        f"bile kısa; kilit ısırmamış olabilir. çıktı={cikti!r}"
    )

    # --- SINIR: SINIRSIZ 180 SN'NİN YERİNE TÜRETİLEN TAVAN ----------------
    #
    # Tavan = bütçe + tek uçuştaki olayın kendi üstleri (üç bacak x
    # (kilit+ifade)). Ölçülen sınırsız taban 180.58 sn bu tavanın ÇOK üstünde:
    # bütçe kaldırılırsa bu assert kırmızıdır.
    tavan = butce + 3 * (kilit_sn + ifade_sn)
    assert sure1 <= tavan, (
        f"SINIRSIZ DÖNGÜ: {sure1:.2f} sn, türetilen tavanın ({tavan:.1f} sn) "
        f"üstünde. Ölçülen sınırsız taban 180.58 sn idi. çıktı={cikti!r}"
    )
    assert girdi1 < 20, (
        "BÜTÇE HİÇ KESMEDİ: 20 çatışmalı olayın hepsi tek döngüde alındı; ya "
        f"çatışma yoktu ya sınır çalışmıyor. çıktı={cikti!r}"
    )

    # --- KALANLAR DÜŞMEZ, ERTELENİR --------------------------------------
    #
    # Çatışmalı olay `RECOVERY_FAILED` kalır (PENDING, deneme yanmaz), yani
    # döngü 1'den sonra 20 olayın 20'si de PENDING olmalı; kilit kalkınca
    # döngü 2 HEPSİNİ işler ve kuyruk boşalır.
    assert "BEKLEYEN1 20" in cikti, (
        f"döngü 1 sonrası bekleyen sayısı beklenen değil. çıktı={cikti!r}"
    )
    assert "DONGU2 " in cikti
    assert re.search(r"DONGU2 sure=[\d.]+ girdi=20", cikti), (
        "KALANLAR SONRAKİ DÖNGÜDE ALINMADI: kilit kalktıktan sonra döngü 2 "
        f"20 olayın hepsini almalıydı. çıktı={cikti!r}"
    )
    assert "BEKLEYEN2 0" in cikti, (
        f"kuyruk boşalmadı; sınır olayları DÜŞÜRMÜŞ olabilir. çıktı={cikti!r}"
    )
    assert "TERMINAL 20" in cikti, (
        f"20 olayın hepsi terminalleşmeliydi. çıktı={cikti!r}"
    )
    # KORUNUM iki dongude de ic assert'lerden gecti (KACAN/KORUNUM-PATLADI
    # yok); satirlar yine de esitligi ACIKCA bildirmeli.
    korunum = dict(re.findall(r"KORUNUM(\d) (\d+ \d+)", cikti))
    assert korunum.get("1", "").split() and len(set(korunum["1"].split())) == 1, (
        f"döngü 1 korunum denklemi tutmadı. çıktı={cikti!r}"
    )
    assert korunum.get("2", "").split() and len(set(korunum["2"].split())) == 1, (
        f"döngü 2 korunum denklemi tutmadı. çıktı={cikti!r}"
    )
