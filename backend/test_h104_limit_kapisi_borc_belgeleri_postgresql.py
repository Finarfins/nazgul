"""PostgreSQL ikizi: H104 — kredi limiti kapısı borç belgelerini kartla aynı sayar.

SQLite testi `tests/test_h104_limit_kapisi_borc_belgeleri.py` aynı senaryoyu
(`tests/h104_limit_senaryo.py`) geçici bir SQLite dosyasında koşturuyor. Bu
ikiz YALNIZ PG'de görünen şeyi ölçer:

* **`FOR UPDATE` altında aynı toplam.** `_credit_exposure` PG'de cari satırını
  kilitler; borç belgesi toplamı aynı işlemde paylaşılan yardımcıdan okunur.
* **`period_end<=:as_of` bağı.** `as_of` bir Python `date`; PG'de `DATE`
  sütunuyla karşılaştırılır (SQLite'ta metin karşılaştırmasıdır).
* **`NUMERIC` toplam.** Kapı == kart == eski kart formülü eşitliği kuruşuna
  kadar PG sütunundan okunan değerle tutuyor mu.

Kaynak kapısı (AST) lehçeden bağımsızdır; yalnız SQLite dosyasında koşar.
Kurgu kendi firmasını açar ve bootstrap yöneticisine dokunmaz.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.postgresql

#: HER KOŞU KENDİ FİRMASINI AÇAR — paylaşılan şemada komşu satırlar toplamlara girmez.
KOSU = uuid4().hex[:8]


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("H104 ikizi APP_TEST_DATABASE_URL ister")
    return url


@pytest.fixture(scope="module")
def kurgu():
    url = _url()
    os.environ.setdefault("DATABASE_URL", url)
    os.environ.setdefault("PAYMENT_ALLOCATION_ENGINE_ENABLED", "true")
    from fastapi.testclient import TestClient

    from app.db import engine
    from app.main import app
    from tests.h104_limit_senaryo import Kurgu

    assert engine.dialect.name == "postgresql", engine.dialect.name
    with TestClient(app) as c:
        yield Kurgu(engine, c, KOSU)


def test_pg_P2_karsiliksiz_cek_limiti_asar_409(kurgu):
    from tests.h104_limit_senaryo import p2_karsiliksiz_cek

    p2_karsiliksiz_cek(kurgu)


def test_pg_TERSLENMIS_karsiliksiz_cek_limiti_asmaz_201(kurgu):
    from tests.h104_limit_senaryo import terslenmis_karsiliksiz_cek

    terslenmis_karsiliksiz_cek(kurgu)


def test_pg_vade_farki_limiti_asar_409(kurgu):
    from tests.h104_limit_senaryo import vade_farki

    vade_farki(kurgu)


def test_pg_yonetici_onayi_satis_403_admin_201_ve_kayit(kurgu):
    from tests.h104_limit_senaryo import yonetici_onayi

    yonetici_onayi(kurgu)


def test_pg_kapi_bakiyesi_kart_bakiyesine_ESIT(kurgu):
    from tests.h104_limit_senaryo import kapi_kart_esitligi

    kapi_kart_esitligi(kurgu)
