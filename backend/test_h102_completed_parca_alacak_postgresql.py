"""PostgreSQL ikizi: H102 — COMPLETED iş emrinde parça değişikliği alacağı uzlaştırır.

SQLite testi `tests/test_h102_completed_parca_alacak.py` aynı senaryoyu
(`tests/h102_completed_parca_senaryo.py`) geçici bir SQLite dosyasında alt
süreçte koşturuyor. Bu ikiz YALNIZ PG'de görünen şeyi ölçer: parça ucu iş
emrini `FOR UPDATE` ile tutarken uzlaştırma aynı satırı (`_lock_work_order`)
ve alacak belgesini `FOR UPDATE` ile yeniden alır — aynı işlemde kilitlenme
olmadan, `NUMERIC` tutarlar kuruşuna kadar önizlemeye eşit.

Mutasyon lehçeden bağımsızdır; yalnız SQLite dosyasında koşar.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.postgresql

#: HER KOŞU KENDİ ÖNEKİNİ KULLANIR — makine seri numarası firma içinde tekil.
KOSU = uuid4().hex[:8]


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("H102 ikizi APP_TEST_DATABASE_URL ister")
    return url


def _acilisa_cek() -> None:
    from tests.pg_ikiz_yardimci import acilisa_cek

    acilisa_cek()


@pytest.fixture(autouse=True)
def _acilis_sifresi():
    _url()
    _acilisa_cek()
    try:
        yield
    finally:
        _acilisa_cek()


@pytest.fixture(scope="module")
def olcum():
    url = _url()
    os.environ.setdefault("DATABASE_URL", url)
    from fastapi.testclient import TestClient

    from app.db import engine
    from app.main import app
    from tests.h102_completed_parca_senaryo import olc, oturum

    assert engine.dialect.name == "postgresql", engine.dialect.name
    _acilisa_cek()
    with TestClient(app) as c:
        out = olc(c, oturum(c, "H102ParcaPg123!"), KOSU)
    _acilisa_cek()
    return out


def test_pg_parca_degisikligi_alacagi_uzlastirir(olcum):
    from tests.h102_completed_parca_senaryo import aktif, satirlar

    for kurgu, adimlar in olcum.items():
        for ad, durum in adimlar.items():
            if "alacak" in durum:
                (tek,) = aktif(durum)
                assert tek["gross_amount"] == durum["onizleme"], (kurgu, ad, durum)
    x = olcum["X"]
    zincir = [("1", "reversed", "419.37", "0"), ("2", "posted", "-419.37", "1"), ("3", "posted", "424.76", "0")]
    assert satirlar(x["p3_eklenince"]) == satirlar(x["sifir_tutar_eklenince"]) == satirlar(x["fatura_sonrasi"]) == zincir
    son = x["fatura_sonrasi"]["alacak"][-1]
    assert (son["source"], son["invoice_id"]) == ("invoice", x["fatura"]["id"])
    assert x["faturadan_sonra_ekle"]["status"] == 409
    assert satirlar(olcum["Y"]["fatura_sonrasi"])[-1] == ("7", "posted", "419.37", "0")
