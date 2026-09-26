"""PostgreSQL ikizi: F9-5-fix (K8) servis faturası KDV'si — H79 + H80.

SQLite testi `tests/test_f9_5_fix_servis_kdv.py` aynı senaryoyu
(`tests/f9_5_servis_kdv_senaryo.py`, yalnız HTTP) geçici bir SQLite
dosyasında koşturuyor. Bu ikiz YALNIZ PG'de görünen şeyi ölçer:

* **`NUMERIC` gidiş-dönüşü.** `invoice_items.tax_amount/total/
  discount_amount` PG'de `NUMERIC`tir ve sürücüden `Decimal` gelir; SQLite
  `float` döndürür. Satır özdeşliği (`total == ham − discount_amount +
  tax_amount`) ve `tax_amount == round(oran × matrah)` kuruşuna kadar, PG
  sütunundan okunup uçtan dönen değerle tutuyor mu — ölçülen o.
* **İskontosuz yolun TABAN'a göre farkı PG'de de YALNIZ işçilik KDV'si.**
  Aynı altın görünüm (`TABAN_ISKONTOSUZ`), aynı izinli fark kümesi.

Seed (H82) burada KOŞTURULMAZ: `build_demo` temiz bir demo veritabanı ister
ve paylaşık PG şemasına 60 satış + 25 alış yazardı; seed SQLite testi ölçer.
"""
from __future__ import annotations

import os
from decimal import Decimal
from uuid import uuid4

import pytest

pytestmark = pytest.mark.postgresql

#: HER KOŞU KENDİ ÖNEKİNİ KULLANIR — makine seri numarası firma içinde tekil.
KOSU = uuid4().hex[:8]


def _url() -> str:
    url = os.environ.get("APP_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("F9-5-fix ikizi APP_TEST_DATABASE_URL ister")
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
def faturalar():
    url = _url()
    os.environ.setdefault("DATABASE_URL", url)
    from fastapi.testclient import TestClient

    from app.db import engine
    from app.main import app
    from tests.f9_5_servis_kdv_senaryo import dunya, fatura_kes, oturum, tamamlanmis_is_emri

    assert engine.dialect.name == "postgresql", engine.dialect.name
    from tests.pg_ikiz_yardimci import acilisa_cek

    acilisa_cek()
    with TestClient(app) as c:
        h = oturum(c, "K8ServisPg123!")
        w = dunya(c, h, KOSU)
        out = {
            "iskontosuz": fatura_kes(c, w, tamamlanmis_is_emri(c, w)),
            "yuzde10": fatura_kes(
                c, w, tamamlanmis_is_emri(c, w), global_discount_type="PERCENT", global_discount_value="10"),
            "yuzde12_5": fatura_kes(
                c, w, tamamlanmis_is_emri(c, w), global_discount_type="PERCENT", global_discount_value="12.5"),
        }
    acilisa_cek()
    return out


def test_pg_numeric_kdv_ozdesligi_ve_iscilik_orani(faturalar):
    from app.invoice_service import SERVICE_LABOR_VAT_RATE
    from tests.f9_5_servis_kdv_senaryo import D, kdv_ozdesligi

    for ad, fatura in faturalar.items():
        kdv_ozdesligi(fatura)
        (labor,) = [k for k in fatura["items"] if k["item_type"] == "LABOR"]
        assert D(labor["tax_rate"]) == SERVICE_LABOR_VAT_RATE, ad
    totals = faturalar["yuzde10"]["totals"]
    assert (D(totals["tax"]), D(totals["grand_total"]), D(totals["global_discount"]),
            D(totals["global_discount_base"])) == (
        Decimal("88.29"), Decimal("539.19"), Decimal("59.91"), Decimal("50.10"))


def test_pg_iskontosuz_yol_tabana_gore_yalniz_iscilik_kdvsi(faturalar):
    from tests.f9_5_servis_kdv_senaryo import IZINLI_DEGISIMLER, IZINLI_EKLENENLER, para_gorunumu, tabana_gore_fark

    degisen, eklenen = tabana_gore_fark(para_gorunumu(faturalar["iskontosuz"]))
    assert degisen == IZINLI_DEGISIMLER
    assert eklenen == IZINLI_EKLENENLER
