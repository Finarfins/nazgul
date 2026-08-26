"""DÖNGÜ SINIRI: `tum_firmalari_isle` artık SINIRSIZ değil.

--- ÖLÇÜLEN KUSUR ------------------------------------------------------------

`olaylari_isle` bir `sinir` kabul ediyordu; `tum_firmalari_isle` onu HİÇ
GEÇMİYORDU ve her PENDING satırı yüklüyordu. ÖLÇÜLDÜ (PG 16.4): 20 çatışmalı
olay TEK döngüde 180.58 saniye sürdü — olay başına 9.03 sn, doğrusal ve
SINIRSIZ — 30 saniyelik aralığa karşı. `field_stock_outbox_interval_seconds`
1..3600'e doğrulanıyor, yani ayar kodun TUTAMADIĞI bir kadans vaat ediyordu.

--- BU DOSYA NEYİ ÖLÇER ------------------------------------------------------

İki sınırdan PARTİ SINIRINI (`AZAMI_PARTI`) ve "alınmayan olay SONRAKİ
döngüde alınır" güvencesini — belirlenimci olarak, duvar saatine bakmadan:

* Kapasitenin ÜSTÜNDE olay kuyruğa konur; TEK döngü tam olarak `AZAMI_PARTI`
  olay alır, kalanlar `PENDING` kalır.
* İKİNCİ döngü kalanları alır ve kuyruk boşalır — yani sınır olayları
  DÜŞÜRMEZ, ERTELER.
* Korunum denklemi iki döngüde de tutar; `girdi` ALINAN olayları sayar.

Eşikler SABİT YAZILMAZ, `AZAMI_PARTI`den türer: kaynaktaki değer değişince bu
dosya sebepsiz kırmızıya dönmez. SÜRE bütçesi (öteki sınır) burada ölçülmez —
SQLite'ta çatışma üretilemez; onun ölçümü PostgreSQL ikizindedir
(`test_field_stok_tuketici_postgresql.py`).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _kos(kaynak: str, db_yolu: Path, imza: str) -> str:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_yolu.as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["PYTHONIOENCODING"] = "utf-8"
    env["ENVIRONMENT"] = "test"
    tamam = subprocess.run(
        [sys.executable, "-c", kaynak],
        cwd=BACKEND, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="strict", timeout=600,
    )
    assert tamam.returncode == 0, tamam.stdout + "\n" + tamam.stderr
    assert imza in tamam.stdout, tamam.stdout
    return tamam.stdout


def test_parti_siniri_ISIRIYOR_ve_kalan_SONRAKI_dongude_isleniyor(
    tmp_path: Path,
) -> None:
    """Kapasite üstü kuyruk: döngü 1 tam `AZAMI_PARTI` alır, döngü 2 kalanı."""
    _kos(r'''
from sqlalchemy import text as _sql

import app.main  # goc zincirini kosturur
from app import field_stok_tuketici as C
from app.db import SessionLocal

Z = '2026-08-01T00:00:00'
FAZLA = 3
N = C.AZAMI_PARTI + FAZLA
print("PARTI %d N %d" % (C.AZAMI_PARTI, N))

with SessionLocal() as db:
    for i in range(1, N + 1):
        # Kaynak satiri BILEREK yok: her olay tek adimda terminal
        # `SKIPPED_SOURCE_NOT_VISIBLE` olur; olculen sey SINIRDIR, kaynak
        # cozumlemesi degil.
        db.execute(_sql(
            "INSERT INTO field_integration_events (id,company_id,source_type,"
            "source_id,target,idempotency_key,status,attempts,created_at,"
            "updated_at) VALUES (:i,1,'field_activity',:s,'stock',:k,"
            "'PENDING',0,:z,:z)"),
            {"i": i, "s": 900000 + i, "k": "parti:%d" % i, "z": Z})
    db.commit()

def _durum_sayilari(db):
    return dict(db.execute(_sql(
        "SELECT status, COUNT(*) FROM field_integration_events GROUP BY status"
    )).all())

with SessionLocal() as db:
    s1 = C.tum_firmalari_isle(db)
    db.commit()
    d1 = _durum_sayilari(db)
    kalan_idler = [r[0] for r in db.execute(_sql(
        "SELECT id FROM field_integration_events WHERE status = 'PENDING' "
        "ORDER BY id")).all()]

print("DONGU1 girdi=%d" % s1['girdi'])
print("KORUNUM1 %d %d" % (s1['girdi'], sum(
    v for k, v in s1.items() if k not in ('girdi', 'COMPANY_FAILED'))))
print("BEKLEYEN1 %d" % d1.get('PENDING', 0))
print("KALAN_IDLER %r" % (kalan_idler,))

with SessionLocal() as db:
    s2 = C.tum_firmalari_isle(db)
    db.commit()
    d2 = _durum_sayilari(db)

print("DONGU2 girdi=%d" % s2['girdi'])
print("KORUNUM2 %d %d" % (s2['girdi'], sum(
    v for k, v in s2.items() if k not in ('girdi', 'COMPANY_FAILED'))))
print("BEKLEYEN2 %d" % d2.get('PENDING', 0))
print("TERMINAL %d" % d2.get('SKIPPED_SOURCE_NOT_VISIBLE', 0))

assert s1['girdi'] == C.AZAMI_PARTI, (
    'SINIR ISIRMADI: dongu 1 %d olay aldi, %d beklenirdi'
    % (s1['girdi'], C.AZAMI_PARTI))
assert d1.get('PENDING', 0) == FAZLA, d1
# ORDER BY id: ertelenen olaylar kuyrugun SONUNDAKILER olmali.
assert kalan_idler == list(range(N - FAZLA + 1, N + 1)), kalan_idler
assert s2['girdi'] == FAZLA, (
    'KALANLAR SONRAKI DONGUDE ALINMADI: dongu 2 %d olay aldi, %d beklenirdi'
    % (s2['girdi'], FAZLA))
assert d2.get('PENDING', 0) == 0, ('kuyruk bosalmadi', d2)
assert d2.get('SKIPPED_SOURCE_NOT_VISIBLE', 0) == N, d2
print("PARTI-SINIRI-TAMAM")
''', tmp_path / "parti.db", "PARTI-SINIRI-TAMAM")
