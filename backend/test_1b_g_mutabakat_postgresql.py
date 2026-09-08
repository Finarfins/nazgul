"""Faz 1B-G parti mutabakati GERCEK PostgreSQL diyalektinde.

SQLite ikizinin davranis betigi AYNEN yeniden kosuluyor (AST ile okunuyor,
KOPYALANMIYOR — iki kopya ayrisirdi ve ayristigi gun hangisinin olctugu
sorulamazdi), ustune YALNIZ PostgreSQL'in soyleyebilecegi seyler ekleniyor:

  * `NUMERIC(18,4)` TOPLAMI ONDALIK, KAYAN NOKTA DEGIL. `SUM(quantity)`
    PostgreSQL'de `Decimal` doner; SQLite ayni sutun icin `float`
    dondurebilir ve `0.1 + 0.1 + 0.1 != 0.3` SESSIZ bir `SAPMA` uydururdu.
    Ikizin bu bolumu tam o sahte sapmayi ariyor ve BULMAMASI gerekiyor.
  * `UNION` sürücü kümesi ve `GROUP BY`in ISLEVSEL BAGIMLILIK kurali gercek
    planlayicida sinaniyor. PostgreSQL `GROUP BY`da SQLite'tan DAR'dir; dar
    olana gore yazilmis metnin gercekten kostugu ancak BURADA olculur.
  * Enjekte edilen TEK sapma gercek diyalektte de TEK kaliyor.

GOC YOKTUR ve bu dosya sema DEGISTIRMEZ; yalniz okur.
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
    parti_temizle(engine, lot_code_prefixes=["MG-", "HASAT-"])
    _acilisa_cek(engine)
    try:
        yield
    finally:
        parti_temizle(engine, lot_code_prefixes=["MG-", "HASAT-"])
        _acilisa_cek(engine)
        engine.dispose()


def _postgres_url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("APP_TEST_DATABASE_URL is not configured")
    assert url.startswith("postgresql"), "1B-G PostgreSQL ikizi PostgreSQL kullanmali"
    return url


def _sqlite_twin_source() -> str:
    """SQLite ikizinin davranis betigini AST ile OKU, kopyalama.

    Kopyalansaydi iki metin ayrisirdi ve ayristiklari gun "PG ikizi SQLite'in
    olctugunu mu olcuyor" sorusu SORULAMAZDI. Okuma, o soruyu yapisal olarak
    ortadan kaldiriyor.
    """
    path = BACKEND / "tests" / "test_1b_g_mutabakat.py"
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
def test_PARTI_MUTABAKATI_GERCEK_DIYALEKTTE_postgresql(acilis) -> None:
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
    assert "1B-G POSTGRESQL OK" in completed.stdout


_PG = r'''
from app.db import engine

assert engine.dialect.name == 'postgresql', engine.dialect.name

# =========================================================================
# SUTUNLAR GERCEKTEN `NUMERIC(18,4)` MI
#
# Ikizin geri kalani "toplam ondalik" DIYE varsayiyor; burasi o varsayimi
# SEMADAN okuyor. Sutun bir gun `double precision`a cevrilirse asagidaki
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
# ENJEKTE EDILEN TEK SAPMA GERCEK DIYALEKTTE DE TEK
#
# SQLite ikizi bunu zaten olctu; burada olculen sey, `UNION` surucu kumesinin
# ve `GROUP BY`in gercek planlayicida AYNI cevabi vermesidir. PostgreSQL'in
# islevsel bagimlilik kurali SQLite'inkinden DAR'dir: alt sorgu SECILEN her
# sutunu gruplamasaydi metin SQLite'ta koser, BURADA hata verirdi.
# =========================================================================
pg_rapor = rapor(limit=1000)
assert pg_rapor['counts']['SAPMA'] == 1, pg_rapor['counts']
assert sapmalar() == {bozuk_cift}, (sapmalar(), bozuk_cift)

# =========================================================================
# ONDALIK TOPLAM: UC KEZ 0.1, KAYAN NOKTADA 0.30000000000000004
#
# BU BOLUM SAHTE BIR `SAPMA` ARIYOR VE BULMAMALI. `SUM(quantity)` kayan
# noktaya dusseydi parti toplami 0.3'e ESIT OLMAZDI ve rapor, defteri DOGRU
# olan bir cifti sapma diye gosterirdi — yani mutabakatin kendisi bir yalan
# uretirdi. `NUMERIC` toplami bunu yapisal olarak imkansiz kilar.
#
# Uc AYRI belge: alis semasi ayni urunu tek belgede iki satirda REDDEDER.
# =========================================================================
urun_ondalik = urun_ac('Mutabakat PG - Ondalik')
for sira in (1, 2, 3):
    alis([kalem(urun_ondalik, '0.1000', 'MG-PG-%d' % sira, UZAK)])

with SessionLocal() as db:
    ham = db.execute(text(
        "SELECT SUM(quantity) FROM product_lots "
        "WHERE company_id=:cid AND product_id=:pid"
    ), {'cid': cid, 'pid': urun_ondalik}).scalar_one()
    stok_ham = db.execute(text(
        "SELECT quantity FROM warehouse_stocks "
        "WHERE company_id=:cid AND product_id=:pid AND warehouse_id=:wid"
    ), {'cid': cid, 'pid': urun_ondalik, 'wid': depo_a}).scalar_one()
# TIP KAPISI: `float` gelseydi asagidaki esitlik SESSIZCE bozulurdu.
assert isinstance(ham, Decimal), type(ham)
assert isinstance(stok_ham, Decimal), type(stok_ham)
assert ham == Decimal('0.3000'), ham
assert stok_ham == Decimal('0.3000'), stok_ham
# Ve kayan noktanin ne yapacagi ADIYLA yazili: bu esitsizlik, yukaridaki
# esitligin NEDEN onemli oldugunun kanitidir.
assert 0.1 + 0.1 + 0.1 != 0.3

ondalik_kovalar = kovalar()
assert ondalik_kovalar[(urun_ondalik, depo_a)] == 'ESIT', \
    ondalik_kovalar[(urun_ondalik, depo_a)]

# Ondalik cift eklendi ve SAPMA HALA TEK: sahte sapma URETILMEDI.
son = rapor(limit=1000)
assert son['counts']['SAPMA'] == 1, son['counts']
assert sapmalar() == {bozuk_cift}, sapmalar()

# =========================================================================
# DORDUNCU BASAMAK: KUANTUMUN TA KENDISI
#
# `NUMERIC(18,4)` dorduncu basamaga kadar tasir. 0.0001'lik bir fark GERCEK
# bir farktir ve rapor onu GORMEK zorundadir — yuvarlanip yutulursa mutabakat
# kendi kuantumunun altinda KOR olur.
# =========================================================================
with SessionLocal() as db:
    kucuk = db.execute(text(
        "SELECT id FROM product_lots WHERE company_id=:cid AND lot_code='MG-PG-1'"
    ), {'cid': cid}).scalar_one()
    db.execute(text(
        "UPDATE product_lots SET quantity=quantity+0.0001 WHERE id=:id"
    ), {'id': kucuk})
    db.commit()

kuantum = rapor(limit=1000)
assert kuantum['counts']['SAPMA'] == 2, kuantum['counts']
assert sapmalar() == {bozuk_cift, (urun_ondalik, depo_a)}, sapmalar()

kucuk_satir = satir_getir(urun_ondalik, depo_a)
assert Decimal(str(kucuk_satir['fark'])) == Decimal('-0.0001'), kucuk_satir

print('1B-G POSTGRESQL OK')
'''
