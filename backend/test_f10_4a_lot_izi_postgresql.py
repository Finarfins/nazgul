"""F10-4a parti geri çağırma önizlemesi GERÇEK PostgreSQL diyalektinde.

SQLite ikizinin davranış betiği AYNEN yeniden koşuluyor (AST ile okunuyor,
KOPYALANMIYOR — 1B-G ikizinin kalıbı), üstüne YALNIZ PostgreSQL'in
söyleyebileceği şeyler ekleniyor:

  * `GROUP BY` + `min(id)` ile sıralama gerçek planlayıcıda. PostgreSQL
    seçilen her gruplanmamış sütunu REDDEDER; `lot_izi`nin hop 2 ve boşluk
    sorguları SQLite'ta geçip burada kırılabilecek biçimdedir.
  * `IN` genişleyen parametresi (`lot_id IN (...)`) psycopg3'te.
  * `expiry_date` PostgreSQL'de `date` DÖNER (SQLite `str`); önizleme yine
    `YYYY-MM-DD` metni vermeli (`lot_izi_ozet.tarih_metni`).
  * `SUM(quantity)` `NUMERIC` -> `Decimal`; denge farkı TAM sıfır.

GÖÇ YOKTUR ve bu dosya şema DEĞİŞTİRMEZ.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parent

#: Betiğin açtığı parti kodları — iki uçtan temizlenir (paylaşılan PG'de
#: kalan parti satırı göç zinciri testini kırar).
#: H116/H117 betiğe dört parti ekledi (boşaldı, örnek, temiz; bütçe #179'dan).
PARTI_KODLARI = [
    "L-RECALL", "L-OTEKI", "L-HEDEF", "L-BUTCE", "L-BOSALDI", "L-ORNEK", "L-TEMIZ",
]


def _postgres_url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is not configured")
    assert url.startswith("postgresql"), "F10-4a PostgreSQL ikizi PostgreSQL kullanmali"
    return url


@pytest.fixture()
def acilis():
    """Açılış şifresi + parti temizliği, İKİ UÇTAN (1B-G ikizinin aynısı)."""
    url = _postgres_url()
    from sqlalchemy import create_engine
    from tests.pg_ikiz_yardimci import acilisa_cek, parti_temizle

    engine = create_engine(url)
    parti_temizle(engine, lot_codes=PARTI_KODLARI)
    acilisa_cek(engine)
    try:
        yield
    finally:
        parti_temizle(engine, lot_codes=PARTI_KODLARI)
        acilisa_cek(engine)
        engine.dispose()


def _sqlite_ikizi() -> str:
    """SQLite ikizinin davranış betiğini AST ile OKU, kopyalama."""
    yol = BACKEND / "tests" / "test_f10_4a_lot_izi.py"
    agac = ast.parse(yol.read_text(encoding="utf-8"))
    dugum = next(
        d for d in agac.body
        if isinstance(d, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_DAVRANIS" for t in d.targets)
    )
    return ast.literal_eval(dugum.value)


@pytest.mark.postgresql
def test_GERI_CAGIRMA_ONIZLEMESI_GERCEK_DIYALEKTTE_postgresql(acilis) -> None:
    env = os.environ.copy()
    url = _postgres_url()
    env["DATABASE_URL"] = url
    env["APP_TEST_DATABASE_URL"] = url
    env["REQUIRE_PG"] = "1"
    env["PYTHONPATH"] = str(BACKEND)
    betik = _sqlite_ikizi().replace("print('F10-4A OK')", "") + _PG
    tamamlandi = subprocess.run(
        [sys.executable, "-c", betik],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    assert tamamlandi.returncode == 0, tamamlandi.stdout + "\n" + tamamlandi.stderr
    assert "F10-4A POSTGRESQL OK" in tamamlandi.stdout


_PG = r'''
from datetime import date as _date

from app.db import engine
from app.lot_izi import parti_izi_oku

assert engine.dialect.name == 'postgresql', engine.dialect.name

# HAM TİPLER: PG `date` ve `Decimal` döndürür; önizleme ikisini de SQLite
# ikiziyle AYNI metne/sayıya indirmeli (yukarıdaki iddialar zaten geçti).
with SessionLocal() as db:
    iz = parti_izi_oku(db, cid, kok_id)
assert isinstance(iz.kok['expiry_date'], _date), type(iz.kok['expiry_date'])
assert all(isinstance(h['quantity'], Decimal) for h in iz.hareketler), [
    type(h['quantity']) for h in iz.hareketler]
assert all(isinstance(p['quantity'], Decimal) for p in iz.partisiz), [
    type(p['quantity']) for p in iz.partisiz]
# H116/H117: örnek satırları ve mutabakat sayıları da `Decimal`; SQL örnek
# sırası (tarih, id) gerçek planlayıcıda da korunur (davranış bölümü zaten
# ters tarihli 25 iadeyle ölçtü).
assert all(isinstance(o['quantity'], Decimal) for o in iz.ornekler), iz.ornekler
assert all(isinstance(m['stok'], Decimal) for m in iz.mutabakat), iz.mutabakat
with SessionLocal() as db:
    iz_ornek = parti_izi_oku(db, cid, ornek_lot)
assert [(o['movement_date'], o['id']) for o in iz_ornek.ornekler] == sorted(
    (o['movement_date'], o['id']) for o in iz_ornek.ornekler)
# Hop 2 `min(id)` sırası: ilk grup ALIŞTIR (partiyi açan belge).
assert iz.hareketler[0]['reference_type'] == 'purchases', iz.hareketler[0]

# BELİRLENİMCİLİK: aynı soru iki kez AYNI cevabı verir (sıra dahil).
assert ok(onizleme(kok_id)) == ok(onizleme(kok_id))

print('F10-4A POSTGRESQL OK')
'''
