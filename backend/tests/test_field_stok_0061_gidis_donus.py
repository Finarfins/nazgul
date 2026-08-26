"""0061 GİDİŞ-DÖNÜŞ: şema daralır ve SIĞMAYAN durum `'DEAD'`e indirgenir.

--- NEDEN BU DOSYA VAR -------------------------------------------------------

0061 bu depoda ender bir şey yapar: gerçek bir `downgrade()` yazar ve o
`downgrade()` yalnız ŞEMA değil VERİ de yazar — 20 karakteri aşan her
`status`, daraltmadan ÖNCE `'DEAD'`e indirgenir.

Bu yol bugüne kadar HİÇBİR arka uçta KOŞMADI. Depoda `command.downgrade`
çağıran dört dosya var — `test_notifications_f2`,
`test_line_item_tenancy_slice1`, `test_stock_transfer_item_tenancy`,
`test_platform_backups_postgresql` — ve hiçbiri 0061'i çağırmıyor. Yani geri
alma yolu, deponun KENDİ gidiş-dönüş alışkanlığının dışında kalmış tek yeni
göçtü. Bu dosya onu alışkanlığın içine alır.

--- BU DOSYA NEYİ ÖLÇER ------------------------------------------------------

1. **ŞEMA GİDİŞ-DÖNÜŞ.** `YENI` -> `ESKI` -> `YENI`. Daralma da genişleme de
   gerçekten koşar; ikisi de göç dosyasının BİLDİRDİĞİ sayılarla karşılaştırılır.
2. **VERİ KOLU.** Sığmayan bir satır `'DEAD'` olur. Bu, `downgrade()`in
   ŞEMADAN AYRI ikinci işidir ve ölçülmezse hiç koşmadan kalır.
3. **SINIRIN KENDİSİ.** Sığan durumlar DEĞİŞMEZ. `WHERE length(status) > 20`
   koşulu olmadan sığan satırlar da yanardı; "sığmayan `DEAD` oldu" iddiası
   TEK BAŞINA o kusuru göremez, çünkü her şeyi `DEAD` yapan bir geri alma da
   o iddiayı geçer. Sınır iki taraftan birden ölçülür.
4. **KAYIP KALICI.** Yeniden `head`e çıkmak `'DEAD'` satırı eski değerine
   GERİ GETİRMEZ. Göç bunu "sessiz kesme yerine adı konmuş bir kayıp" diye
   bildiriyor; burada o bildirim bir ÖLÇÜYE bağlanır.

Örnek değil SINIF donduruluyor: sığan/sığmayan durumlar `field_stok_tuketici`
modülünün `DURUM_` sabitlerinden, eşik ise göç dosyasının kendi `ESKI`/`YENI`
sabitlerinden TÜRETİLİR. Elle yazılmış liste yok.

--- KULVAR SINIRI ------------------------------------------------------------

SQLite `VARCHAR` uzunluğunu YOK SAYAR. Yani burada DARALMANIN KENDİSİ bir şey
kanıtlamaz — kanıtlanan şey VERİ KOLUDUR. Daraltmanın gerçekten bir sınır
olduğu, ve UPDATE kolu olmadan `StringDataRightTruncation` verdiği yer PG
ikizidir: `test_field_stok_0061_postgresql.py`. İki dosya birlikte tek bir
iddiayı taşır; ayrı ayrı yarımdır.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

#: TEK gömülü metin. `import app.main` göç zincirini süreç başına BİR KEZ
#: kurar ve `command.downgrade` süreç genelinde şemayı değiştirir; iki de
#: taze bir veritabanı ister. Bu yüzden prob AYRI SÜREÇTE koşar (alt süreç
#: SQL yüzeyi `tests/test_tenant_scoping_guard.py` içinde sayılıdır).
_GIDIS_DONUS_PROBU = r'''
import importlib.util as _iu
import json, os, sys

sys.path.insert(0, os.environ["BACKEND"])
import app.main  # goc zincirini kosturur; bas = 0061
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text as _sql

from app.db import engine
from app import field_stok_tuketici as tuketici

_yol = os.path.join(
    os.environ["BACKEND"], "alembic", "versions",
    "20260824_0061_field_event_status_width.py",
)
_spec = _iu.spec_from_file_location("goc_0061", _yol)
goc = _iu.module_from_spec(_spec)
_spec.loader.exec_module(goc)


def genislik():
    for sutun in inspect(engine).get_columns(goc.TABLO):
        if sutun["name"] == goc.SUTUN:
            return getattr(sutun["type"], "length", None)
    raise AssertionError("PROB: %s.%s sutunu yok" % (goc.TABLO, goc.SUTUN))


# BILDIRILEN durumlar; elle liste YOK (bkz. test_field_stok_durum_genisligi).
durumlar = sorted(
    {d for ad, d in vars(tuketici).items()
     if ad.startswith("DURUM_") and isinstance(d, str)},
    key=lambda d: (len(d), d),
)
tasan = [d for d in durumlar if len(d) > goc.ESKI]
sigan = [d for d in durumlar if len(d) <= goc.ESKI]

# TOHUM. Her bildirilen durum icin BIR satir; id'ler 900'un ustunde durur ki
# onyukleme verisiyle carpismasin.
tohum = {}
oid = 900
for durum in tasan + sigan:
    oid += 1
    tohum[oid] = durum

Z = "2026-08-24T00:00:00"
with engine.begin() as baglanti:
    for kimlik, durum in tohum.items():
        baglanti.execute(
            _sql(
                """INSERT INTO field_integration_events(
                company_id,id,source_type,source_id,target,idempotency_key,
                status,attempts,created_at,updated_at)
                VALUES(1,:id,'field_activity',:id,'stock',:anahtar,:durum,0,:z,:z)"""
            ),
            {"id": kimlik, "anahtar": "gidis-donus:%d" % kimlik,
             "durum": durum, "z": Z},
        )


def oku():
    with engine.connect() as baglanti:
        satirlar = baglanti.execute(
            _sql(
                """SELECT id, status FROM field_integration_events
                WHERE company_id = 1 AND id > 900 ORDER BY id"""
            )
        ).all()
    return {str(kimlik): durum for kimlik, durum in satirlar}


yapilandirma = Config("alembic.ini")
once_genislik, once_satir = genislik(), oku()
command.downgrade(yapilandirma, goc.down_revision)
dar_genislik, dar_satir = genislik(), oku()
command.upgrade(yapilandirma, "head")
geri_genislik, geri_satir = genislik(), oku()

print("SONUC " + json.dumps({
    "bildirilen": {"eski": goc.ESKI, "yeni": goc.YENI,
                   "hedef": goc.down_revision},
    "tasan": tasan, "sigan": sigan,
    "olu": tuketici.DURUM_OLU,
    "tohum": {str(k): v for k, v in tohum.items()},
    "genislik": {"once": once_genislik, "dar": dar_genislik,
                 "geri": geri_genislik},
    "satir": {"once": once_satir, "dar": dar_satir, "geri": geri_satir},
}))
'''


def _prob(db_yolu: Path) -> dict:
    """Probu AYRI SÜREÇTE koşturur ve JSON sonucunu döndürür."""
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_yolu.as_posix()}"
    env["BACKEND"] = str(BACKEND)
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    env["ENVIRONMENT"] = "test"
    tamam = subprocess.run(
        [sys.executable, "-c", _GIDIS_DONUS_PROBU],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    satir = [s for s in tamam.stdout.splitlines() if s.startswith("SONUC ")]
    assert satir, tamam.stdout + "\n" + tamam.stderr
    return json.loads(satir[-1][len("SONUC "):])


def _olculebilir_mi(sonuc: dict) -> None:
    """FAIL CLOSED: tohum sınıfı boşsa bu koşum HİÇBİR ŞEY sınamıyordur."""
    assert sonuc["tasan"], (
        "Bildirilen hiçbir durum eski genişliği aşmıyor; `downgrade()`in VERİ "
        "kolu bu koşumda HİÇ tetiklenmedi ve bu dosya sessizce boşa döndü. "
        f"Bildirilenler: {sonuc['sigan']!r}"
    )
    assert sonuc["sigan"], (
        "Bildirilen her durum eski genişliği aşıyor; SINIRIN SIĞAN TARAFI "
        "ölçülemedi, yani 'her satırı DEAD yapan' bozuk bir geri alma bu "
        "dosyadan GEÇERDİ."
    )


def test_gidis_donus_sema_genisligini_geri_getiriyor(tmp_path: Path) -> None:
    """`YENI` -> `ESKI` -> `YENI`: daralma da genişleme de KOŞAR."""
    sonuc = _prob(tmp_path / "gidis-donus-sema.db")
    bildirilen, genislik = sonuc["bildirilen"], sonuc["genislik"]

    assert genislik["once"] == bildirilen["yeni"], (
        "Göç `YENI` genişliği bildiriyor ama zincirden kurulan şema onu "
        f"göstermiyor: {genislik['once']!r} != {bildirilen['yeni']!r}"
    )
    assert genislik["dar"] == bildirilen["eski"], (
        "GERİ ALMA sütunu eski genişliğine indirmedi: "
        f"{genislik['dar']!r} != {bildirilen['eski']!r}. Geri alma sonrası "
        "şema, göçün bildirdiği 'değişiklik öncesi' şekil DEĞİL."
    )
    assert genislik["geri"] == bildirilen["yeni"], (
        "Geri almadan sonra yeniden `head`e çıkmak genişliği geri getirmedi: "
        f"{genislik['geri']!r} != {bildirilen['yeni']!r}. Göç TEK YÖNLÜ "
        "koşuyor demektir."
    )


def test_geri_alma_SIGMAYANI_DEAD_yapar_SIGANA_DOKUNMAZ(tmp_path: Path) -> None:
    """Veri kolu VE sınırın kendisi: iki taraf birden ölçülür."""
    sonuc = _prob(tmp_path / "gidis-donus-veri.db")
    _olculebilir_mi(sonuc)
    tohum, dar, olu = sonuc["tohum"], sonuc["satir"]["dar"], sonuc["olu"]

    assert sonuc["satir"]["once"] == tohum, (
        "Tohum satırları geri almadan ÖNCE bile beklenen hâlde değil; "
        f"sonraki iddialar anlamsız olurdu: {sonuc['satir']['once']!r}"
    )

    tasan = {k: v for k, v in tohum.items() if v in sonuc["tasan"]}
    yanmayan = {k: dar.get(k) for k, v in tasan.items() if dar.get(k) != olu}
    assert not yanmayan, (
        f"SIĞMAYAN durum geri almadan sonra {olu!r} olmalıydı; şu satır(lar) "
        f"öyle değil: {yanmayan!r}. Bu satırlar daraltılmış sütuna SIĞMAZ — "
        "PostgreSQL'de daraltmanın kendisi StringDataRightTruncation verirdi."
    )

    sigan = {k: v for k, v in tohum.items() if v in sonuc["sigan"]}
    bozulan = {
        k: (v, dar.get(k)) for k, v in sigan.items() if dar.get(k) != v
    }
    assert not bozulan, (
        "SIĞAN durumlar geri almadan ETKİLENMEMELİ; `length(status) > "
        f"{sonuc['bildirilen']['eski']}` koşulu bu satırları KORUMALI. "
        f"Değişen(ler) (önce, sonra): {bozulan!r}"
    )


def test_geri_alma_KAYBI_yeniden_yukseltmek_geri_getirmiyor(
    tmp_path: Path,
) -> None:
    """Kayıp ADI KONMUŞ ve KALICI: `head`e dönmek eski değeri geri vermez."""
    sonuc = _prob(tmp_path / "gidis-donus-kayip.db")
    _olculebilir_mi(sonuc)
    tohum, geri, olu = sonuc["tohum"], sonuc["satir"]["geri"], sonuc["olu"]

    dirilen = {
        k: geri.get(k) for k, v in tohum.items()
        if v in sonuc["tasan"] and geri.get(k) != olu
    }
    assert not dirilen, (
        "Geri alma + yeniden yükseltme sonrasında sığmayan satır ESKİ "
        f"değerine dönmüş görünüyor: {dirilen!r}. Göç bunu kalıcı ve adı "
        "konmuş bir kayıp olarak bildiriyor; bu dosya o bildirimi ölçüyor."
    )
