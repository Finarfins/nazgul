"""PostgreSQL ikizi: göç `20260915_0088` (H17) GERÇEK PG 16'da up->down->up.

Üç şey ölçülür:

1. ``upgrade head`` sonrası ``ix_auth_rate_limits_attempted_at`` VAR ve
   YALNIZ ``attempted_at`` sütunundadır (bileşik değil).
2. ``downgrade`` 0087'ye indirince indeks YOK; yeniden ``upgrade`` geri getirir.
   0039'un bileşik indeksi iki yönde de YERİNDE kalır (yanlış indeksi düşüren
   bir ``downgrade`` burada kırmızıdır).
3. Süpürme yüklemi (``attempted_at < :cutoff``) indeksi GERÇEKTEN kullanabilir:
   ``enable_seqscan=off`` altında plan yeni indekse iner. Birkaç satırlık bir
   test tablosunda planlayıcı serbest bırakılırsa haklı olarak ardışık taramayı
   seçer; ölçülen şey maliyet değil indeksin YÜKLEMİ KARŞILADIĞIDIR (maliyet
   ölçümü göçün başlığında, 100 000 satırla).

TEMİZLİK: satır yazmaz; ``finally`` şemayı başa geri getirir.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

BACKEND = Path(__file__).resolve().parent

pytestmark = pytest.mark.postgresql

GOC = "20260915_0088"
ONCEKI = "20260915_0087"
#: ZINCIRIN BASI — `GOC`tan AYRI; her yeni gocle KIMILDAR (bkz. E1 ikizi).
BAS = "20260915_0089"  # E4b-2: baş 0089 (bu dosyanın göçü GOC=0088)
TABLO = "auth_rate_limits"
INDEKS = "ix_auth_rate_limits_attempted_at"
BILESIK = "ix_auth_rate_limits_action_ip_time"


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("H17 ikizi APP_TEST_DATABASE_URL ister")
    return url


@pytest.fixture()
def motor():
    yapilandirma = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(_url())
    command.upgrade(yapilandirma, "head")
    try:
        yield engine, yapilandirma
    finally:
        command.upgrade(yapilandirma, "head")
        engine.dispose()


def _indeksler(engine) -> dict[str, list[str]]:
    inspector = inspect(engine)
    return {i["name"]: list(i["column_names"]) for i in inspector.get_indexes(TABLO)}


def test_SEMA_BASI_ve_INDEKS_VAR(motor) -> None:
    engine, _ = motor
    with engine.connect() as baglanti:
        surumler = baglanti.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    assert surumler == [BAS], surumler
    indeksler = _indeksler(engine)
    assert indeksler.get(INDEKS) == ["attempted_at"], indeksler
    assert indeksler.get(BILESIK) == ["action", "ip_address", "attempted_at"], indeksler


def test_DOWNGRADE_DUSURUR_UPGRADE_GERI_GETIRIR(motor) -> None:
    engine, yapilandirma = motor
    command.downgrade(yapilandirma, ONCEKI)
    indeksler = _indeksler(engine)
    assert INDEKS not in indeksler, indeksler
    assert BILESIK in indeksler, "downgrade YANLIŞ indeksi düşürdü"

    command.upgrade(yapilandirma, GOC)
    indeksler = _indeksler(engine)
    assert indeksler.get(INDEKS) == ["attempted_at"], indeksler
    assert BILESIK in indeksler


def test_SUPURME_YUKLEMI_INDEKSI_KULLANIR(motor) -> None:
    engine, _ = motor
    with engine.begin() as baglanti:
        baglanti.exec_driver_sql("SET LOCAL enable_seqscan = off")
        plan = "\n".join(
            baglanti.exec_driver_sql(
                "EXPLAIN (COSTS OFF) DELETE FROM auth_rate_limits"
                " WHERE attempted_at < now() - interval '1 hour'"
            ).scalars()
        )
    assert INDEKS in plan, plan
