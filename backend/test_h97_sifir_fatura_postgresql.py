"""PostgreSQL ikizi: H97 — matraha EŞİT belge iskontosu da 422.

SQLite testi `tests/test_h97_sifir_fatura.py` aynı senaryoyu
(`tests/h97_sifir_fatura_senaryo.py`) alt süreçte koşturuyor. Bu ikiz TEK
vakayı PG'de ölçer: `NUMERIC` matrah (iş emri ve parça satırları PG'den
okunur) %100 iskontoya TAM eşitken önizleme ve fatura ikisi de 422 verir;
`build_invoice_summary`nin `FOR SHARE` kilidi altında red yarım iz bırakmaz.
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
        pytest.skip("H97 ikizi APP_TEST_DATABASE_URL ister")
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
    from tests.h97_sifir_fatura_senaryo import olc, oturum

    assert engine.dialect.name == "postgresql", engine.dialect.name
    _acilisa_cek()
    with TestClient(app) as c:
        out = olc(c, oturum(c, "H97SifirFaturaPg123!"), KOSU)
    _acilisa_cek()
    return out


def test_pg_yuzde100_onizlemede_ve_faturada_422(olcum):
    for taraf in ("onizleme", "fatura"):
        sonuc = olcum[taraf]["B_yuzde100"]
        assert sonuc["status"] == 422, (taraf, sonuc)
        detay = sonuc["body"]["detail"]
        assert (detay["code"], detay["taxable_base"], detay["discount_type"]) == (
            "ISKONTO_TOPLAMI_ASIYOR", "501.00", "PERCENT"), (taraf, detay)
