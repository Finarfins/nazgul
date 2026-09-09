"""Faz 1B-H lot-suz yazicilar GERCEK PostgreSQL diyalektinde.

SQLite ikizinin davranis betigi AYNEN yeniden kosuluyor (AST ile OKUNUYOR,
KOPYALANMIYOR — iki kopya ayrisirdi ve ayristigi gun hangisinin olctugu
sorulamazdi), ustune YALNIZ PostgreSQL'in soyleyebilecegi seyler ekleniyor:

  * `_parti_takipli_mi`nin `LIMIT 1`i GERCEK PLANLAYICIDA kosuyor. Yuklem
    SQLite'ta sessizce gecebilecek bir bicimde yazilsaydi (ornegin `SELECT 1
    ... LIMIT 1` yerine toplama bakan bir metin), red kapisi burada bozulurdu.
    Kapinin YAZILDIGI gibi kostugu ancak gercek diyalektte olculur.

  * RED TUM ISTEGI GERI ALIYOR MU: `db.rollback()` SQLite'ta da cagriliyor
    ama gercek bir islem yoneticisi altinda olculmemisti. Toplu stok yazimi
    once BIR urunu yazip sonra digerinde 409 aliyor; PostgreSQL'de ilk
    yazmanin gercekten geri alindigi SATIR SAYARAK dogrulaniyor.

  * `ProductCreate.lot_code` ile acilan partinin miktari `NUMERIC(18,4)`
    ONDALIGIDIR, kayan nokta DEGIL. Acilis stogu ondalikli girildiginde
    parti toplami ile stok TAM esit kalmali; float olsaydi ikisi sessizce
    ayrisir ve rapor SAHTE bir `SAPMA` uydururdu.

  * Excel ice aktarmasinin ISARET dali (artı/eksi) gercek diyalektte de
    partiyi dogru yonde kimildatiyor ve `_parti_dus`un `WHERE quantity>=`
    korumasi PostgreSQL'in `CHECK` kisitiyla CAKISMIYOR.

GOC YOKTUR ve bu dosya sema DEGISTIRMEZ; yalniz okur ve yazar.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parent


def _acilisa_cek(engine=None) -> None:
    """Admin sifresini ACILIS DURUMUNA (`admin123` + `must_change_password`) yaz."""
    from tests.pg_ikiz_yardimci import acilisa_cek

    acilisa_cek(engine)


@pytest.fixture()
def acilis():
    """Acilis sifresi + parti temizligi, IKI UCTAN.

    ATLAMA ONCE gelir: `_postgres_url` yapilandirma yoksa testi ATLAR ve
    fixture hicbir seye dokunmaz.
    """
    url = _postgres_url()
    from sqlalchemy import create_engine
    from tests.pg_ikiz_yardimci import parti_temizle

    engine = create_engine(url)
    parti_temizle(engine, lot_code_prefixes=["H1-", "H1C-", "H4-", "H5-", "H7-", "H8-", "H9-"])
    _acilisa_cek(engine)
    try:
        yield
    finally:
        parti_temizle(engine, lot_code_prefixes=["H1-", "H1C-", "H4-", "H5-", "H7-", "H8-", "H9-"])
        _acilisa_cek(engine)
        engine.dispose()


def _postgres_url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is not configured")
    assert url.startswith("postgresql"), "1B-H PostgreSQL ikizi PostgreSQL kullanmali"
    return url


def _sqlite_twin_source() -> str:
    """SQLite ikizinin davranis betigini AST ile OKU, kopyalama.

    Kopyalansaydi iki metin ayrisirdi ve ayristiklari gun "PG ikizi SQLite'in
    olctugunu mu olcuyor" sorusu SORULAMAZDI. Okuma, o soruyu yapisal olarak
    ortadan kaldiriyor.
    """
    path = BACKEND / "tests" / "test_1b_h_lotsuz_yazici.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_DAVRANIS"
            for target in item.targets
        )
    )
    return ast.literal_eval(node.value)


@pytest.mark.postgresql
def test_LOTSUZ_YAZICILAR_GERCEK_DIYALEKTTE_postgresql(acilis) -> None:
    env = os.environ.copy()
    url = _postgres_url()
    env["DATABASE_URL"] = url
    env["APP_TEST_DATABASE_URL"] = url
    env["REQUIRE_PG"] = "1"
    env["PYTHONPATH"] = str(BACKEND)
    script = _sqlite_twin_source() + _PG
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "1B-H POSTGRESQL OK" in completed.stdout


_PG = r'''
from decimal import Decimal

from sqlalchemy import text

from app.db import SessionLocal, engine

assert engine.dialect.name == 'postgresql', engine.dialect.name

# =========================================================================
# SUTUN GERCEKTEN `NUMERIC(18,4)` MI
#
# Asagidaki ondalik bolum "toplam ondalik" DIYE varsayiyor; burasi o
# varsayimi SEMADAN okuyor. Sutun bir gun `double precision`a cevrilirse
# ondalik bolum hala yesil kalabilirdi (kucuk sayilarda float de tutar) ve
# kusur ancak URETIMDE, buyuk bir defterde gorunurdu.
# =========================================================================
with SessionLocal() as db:
    tipler = dict(db.execute(text(
        "SELECT table_name || '.' || column_name, "
        "       data_type || '(' || numeric_precision || ',' || numeric_scale || ')' "
        "FROM information_schema.columns "
        "WHERE table_schema='public' AND column_name='quantity' "
        "  AND table_name IN ('product_lots','warehouse_stocks')"
    )).all())
assert tipler == {
    'product_lots.quantity': 'numeric(18,4)',
    'warehouse_stocks.quantity': 'numeric(18,4)',
}, tipler


# =========================================================================
# (PG-1) RED TUM ISTEGI GERI ALIYOR — SATIR SAYARAK
#
# SQLite ikizi bunu API cevabindan okudu (stok kimildamadi). Burada olculen
# sey AYNI OLGUNUN VERITABANINDAKI hali: toplu yazim once PARTISIZ urunu
# yazip sonra PARTILI uzerinde 409 aliyor ve `except HTTPException` dalindaki
# `db.rollback()` ILK yazmayi da geri almali. Gercek bir islem yoneticisi
# altinda olculmemisti; SQLite'in gevsek islem semantigi bu kusuru
# gizleyebilirdi.
# =========================================================================
def hareket_sayisi(pid):
    with SessionLocal() as db:
        return db.execute(text(
            "SELECT COUNT(*) FROM stock_movements "
            "WHERE company_id=:cid AND product_id=:pid AND movement_type='bulk'"
        ), {'cid': cid, 'pid': pid}).scalar_one()


def depo_stogu(pid, wid):
    with SessionLocal() as db:
        return db.execute(text(
            "SELECT quantity FROM warehouse_stocks "
            "WHERE company_id=:cid AND product_id=:pid AND warehouse_id=:wid"
        ), {'cid': cid, 'pid': pid, 'wid': wid}).scalar_one()


# SIRA ONEMLI: partisiz urun listede ONCE geliyor, yani reddin dustugu anda
# onun hareketi ZATEN yazilmisti. Ters sirada test hicbir sey olcmezdi.
onceki_hareket = hareket_sayisi(u1b)
onceki_stok = depo_stogu(u1b, depo_a)
pg_red = toplu([u1b, u1], 4242)
assert pg_red.status_code == 409, (pg_red.status_code, pg_red.text)
assert pg_red.json()['detail']['code'] == 'LOT_TAKIPLI_URUN_LOTSUZ_YAZILAMAZ', pg_red.text
assert hareket_sayisi(u1b) == onceki_hareket, (hareket_sayisi(u1b), onceki_hareket)
assert depo_stogu(u1b, depo_a) == onceki_stok, (depo_stogu(u1b, depo_a), onceki_stok)


# =========================================================================
# (PG-2) `_parti_takipli_mi` GERCEK PLANLAYICIDA: TUKENMIS PARTI DE KAPATIR
#
# `u7`nin partisi SIFIRA indi ama SATIRI DURUYOR (goc 0067'nin karari). Kapi
# SATIR SAYISINA baktigi icin o urune lot-suz yazma HALA REDDEDILMELI.
# SQLite ikizi bu senaryoyu davranisla olcmuyor (statik kapi olcuyor); gercek
# diyalektte davranisla da soruluyor — `quantity>0` diye daraltilmis bir
# yuklem BURADA kirmizi olur.
# =========================================================================
with SessionLocal() as db:
    kalan = db.execute(text(
        "SELECT COUNT(*), COALESCE(SUM(quantity),0) FROM product_lots "
        "WHERE company_id=:cid AND product_id=:pid"
    ), {'cid': cid, 'pid': u7}).first()
assert kalan[0] == 1, kalan          # satir DURUYOR
assert kalan[1] == Decimal('0.0000'), kalan   # ama TUKENMIS

tukenmis_red = guncelle(u7, 'H7 Tukenen', 50)
assert tukenmis_red.status_code == 409, (tukenmis_red.status_code, tukenmis_red.text)
assert tukenmis_red.json()['detail']['code'] == \
    'LOT_TAKIPLI_URUN_LOTSUZ_YAZILAMAZ', tukenmis_red.text
assert stok(u7) == 0.0, stok(u7)


# =========================================================================
# (PG-3) ACILIS PARTISI ONDALIK: SAHTE `SAPMA` URETILMIYOR
#
# `ProductCreate.lot_code` ile acilan partinin miktari stogun TA KENDISIDIR.
# Ikisi ayri sutunlarda durur ve kayan noktaya duserlerse 0.1+0.1+0.1 gibi
# bir toplam ikisini sessizce ayristirirdi — rapor, defteri DOGRU olan bir
# cifti `SAPMA` diye gosterirdi. Yani mutabakatin kendisi yalan uretirdi.
# =========================================================================
u_ondalik = ok(urun_ac('H8 Ondalik Acilis', stok='0.3000', kod='H8-LOT'))['id']
with SessionLocal() as db:
    parti_ham = db.execute(text(
        "SELECT SUM(quantity) FROM product_lots "
        "WHERE company_id=:cid AND product_id=:pid"
    ), {'cid': cid, 'pid': u_ondalik}).scalar_one()
    stok_ham = db.execute(text(
        "SELECT quantity FROM warehouse_stocks "
        "WHERE company_id=:cid AND product_id=:pid AND warehouse_id=:wid"
    ), {'cid': cid, 'pid': u_ondalik, 'wid': depo_a}).scalar_one()
# TIP KAPISI: `float` gelseydi asagidaki esitlik SESSIZCE bozulurdu.
assert isinstance(parti_ham, Decimal), type(parti_ham)
assert isinstance(stok_ham, Decimal), type(stok_ham)
assert parti_ham == Decimal('0.3000'), parti_ham
assert stok_ham == Decimal('0.3000'), stok_ham
assert kovalar()[(u_ondalik, depo_a)] == 'ESIT', kovalar()[(u_ondalik, depo_a)]


# =========================================================================
# (PG-4) EXCEL ISARET DALI GERCEK DIYALEKTTE
#
# Bir Excel AZALTMASI `_parti_dus`e duser ve o, korumayi YAZMANIN KENDI
# `WHERE`unde tasir (`quantity>=:miktar`). PostgreSQL'de 0067'nin `CHECK`i
# de var: koruma calismasaydi burada `IntegrityError` cikardi — yani
# SQLite'ta SESSIZ olan kusur BURADA gurultulu. Ikisinin CAKISMADIGI, yani
# dogru miktarin duzgunce dustugu, ancak gercek diyalektte olculur.
# =========================================================================
AD_PG = 'H9 Excel PG ' + KOSU
KOD_PG = 'H9-' + KOSU
ok(excel([[AD_PG, KOD_PG, '2.5000', 'H9-LOT']], BASLIK_KODLU))
u9 = urun_bul(AD_PG)
assert lot_toplami(u9) == 2.5, partiler(u9)

# AZALTMA: 2.5 -> 1.0, yani partiden 1.5 DUSER.
ok(excel([[AD_PG, KOD_PG, '1.0000', 'H9-LOT']], BASLIK_KODLU))
with SessionLocal() as db:
    kalan9 = db.execute(text(
        "SELECT quantity FROM product_lots "
        "WHERE company_id=:cid AND product_id=:pid AND lot_code='H9-LOT'"
    ), {'cid': cid, 'pid': u9}).scalar_one()
assert kalan9 == Decimal('1.0000'), kalan9
assert kovalar()[(u9, depo_a)] == 'ESIT', kovalar()[(u9, depo_a)]

# ELDE OLMAYANI DUSMEK 409'DUR, `IntegrityError` DEGIL: koruma yazmanin
# `WHERE`unde, yani veritabaninin reddinden ONCE. `CHECK` ateslenseydi
# operatore NE YAPACAGINI soylemeyen bir 500 donerdi.
asiri = excel([[AD_PG, KOD_PG, '-5.0000', 'H9-LOT']], BASLIK_KODLU)
assert asiri.status_code == 409, (asiri.status_code, asiri.text)
assert asiri.json()['detail']['code'] == 'LOT_MIKTARI_EKSIYE_DUSER', asiri.text
with SessionLocal() as db:
    hala = db.execute(text(
        "SELECT quantity FROM product_lots "
        "WHERE company_id=:cid AND product_id=:pid AND lot_code='H9-LOT'"
    ), {'cid': cid, 'pid': u9}).scalar_one()
assert hala == Decimal('1.0000'), hala


# =========================================================================
# (PG-5) BOS CIFT ELEMESI GERCEK DIYALEKTTE: SAPMA HALA TEK
#
# Eleme Python'da yapiliyor ama besleyen sayilar SQL'den geliyor ve
# `stok == 0` karsilastirmasi `money.quantity` kuantumunda olculuyor.
# PostgreSQL `NUMERIC`i `Decimal`, SQLite ayni sutun icin `float` verebilir;
# eleme yanlis tarafa dusseydi ya eksi bakiye kaybolur ya gurultu kalirdi.
# =========================================================================
pg_son = rapor(limit=1000)
assert pg_son['bos_ciftler'] >= 1, pg_son
pg_sapan = {c for c, k in kovalar().items() if k == 'SAPMA'}
assert pg_sapan == {(u6, depo_a)}, pg_sapan
assert set(pg_son['counts']) == {'ESIT', 'LOTSUZ_TASARIM', 'SAPMA'}, pg_son['counts']

print('1B-H POSTGRESQL OK', pg_son['counts'],
      'bos_ciftler', pg_son['bos_ciftler'], 'total', pg_son['total'])
'''
