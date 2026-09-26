"""PostgreSQL ikizi: H91 — önizleme ve servis alacağı işçilik KDV'si faturayla aynı.

SQLite testi `tests/test_h91_onizleme_iscilik_kdv.py` aynı senaryoyu
(`tests/h91_onizleme_senaryo.py`) geçici bir SQLite dosyasında alt süreçte
koşturuyor. Bu ikiz YALNIZ PG'de görünen şeyi ölçer:

* **`NUMERIC` gidiş-dönüşü.** Önizleme `Decimal` hesaplar, fatura kalemleri
  ve `receivable_charge_documents.gross_amount` PG'de `NUMERIC`tir. Önizleme
  == fatura == alacak eşitliği kuruşuna kadar PG sütunundan okunan değerle
  tutuyor mu — ölçülen o.
* **`FOR SHARE`/`FOR UPDATE` altında aynı fiyat.** `build_invoice_summary`
  PG'de iş emrini `FOR SHARE` ile kilitler; alacak uzlaştırması `FOR UPDATE`.
  Aynı kurgular (A/B/C/D), aynı elle türetilmiş beklenenler.

Mutasyon ve AST kapısı lehçeden bağımsızdır; yalnız SQLite dosyasında koşar.
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
        pytest.skip("H91 ikizi APP_TEST_DATABASE_URL ister")
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
    from tests.h91_onizleme_senaryo import olc, oturum

    assert engine.dialect.name == "postgresql", engine.dialect.name
    _acilisa_cek()
    with TestClient(app) as c:
        out = olc(c, oturum(c, "H91OnizlemePg123!"), KOSU)
    _acilisa_cek()
    return out


def test_pg_onizleme_fatura_ve_alacak_elle_turetilen_rakamlarda(olcum):
    from tests.h91_onizleme_senaryo import hatalar

    assert hatalar(olcum) == []


def test_pg_alacak_revizyon_zinciri(olcum):
    from tests.h91_onizleme_senaryo import BEKLENEN

    for kurgu in ("A", "B"):
        tamam = olcum[kurgu]["alacak_tamamlaninca"]
        sonra = olcum[kurgu]["alacak_fatura_sonrasi"]
        assert [b["gross_amount"] for b in tamam] == [BEKLENEN[kurgu]["customer_amount"]], tamam
        assert len(sonra) == len(tamam) == 1
        assert [(b["revision_no"], b["status"], b["gross_amount"], b["reversal"]) for b in sonra] == \
               [(b["revision_no"], b["status"], b["gross_amount"], b["reversal"]) for b in tamam]
    for kurgu, son in (("C", "377.43"), ("D", "424.76")):
        assert [(b["revision_no"], b["status"], b["gross_amount"], b["reversal"])
                for b in olcum[kurgu]["alacak_fatura_sonrasi"]] == [
            ("1", "reversed", "419.37", "0"), ("2", "posted", "-419.37", "1"), ("3", "posted", son, "0")], kurgu


def test_pg_matrahi_asan_iskonto_onizlemede_de_422(olcum):
    asan = olcum["asan_onizleme"]
    assert asan["status"] == 422 and asan["body"]["detail"]["code"] == "ISKONTO_TOPLAMI_ASIYOR", asan


def test_pg_fatura_sonrasi_alacak_ve_yaslandirma_baglantisi(olcum):
    from decimal import Decimal
    for kurgu in ("A", "B"):
        f = olcum[kurgu]["fatura"]
        fid = str(f["id"])
        fno = f["invoice_number"]
        tamam = olcum[kurgu]["alacak_tamamlaninca"]
        sonra = olcum[kurgu]["alacak_fatura_sonrasi"]
        assert len(sonra) == len(tamam) == 1
        assert sonra[0]["revision_no"] == "1" and sonra[0]["status"] == "posted"
        assert tamam[0]["source"] == "computed" and tamam[0]["invoice_id"] == ""
        assert sonra[0]["source"] == "invoice" and sonra[0]["invoice_id"] == fid
        assert sonra[0]["invoice_number"] == fno
        cid = int(f["customer"]["id"])
        musteri = next(c for c in olcum[kurgu]["yaslandirma"]["customers"] if c["customer_id"] == cid)
        wo_no = olcum[kurgu]["work_order_no"]
        belge = next(d for d in musteri["documents"] if d["document_type"] == "service_fee" and d["document_no"] == f"{wo_no}-R1")
        assert Decimal(str(belge["remaining"])) == Decimal(str(f["totals"]["customer_amount"]))

    for kurgu in ("C", "D"):
        f = olcum[kurgu]["fatura"]
        fid = str(f["id"])
        fno = f["invoice_number"]
        sonra = olcum[kurgu]["alacak_fatura_sonrasi"]
        assert len(sonra) == 3
        assert sonra[0]["status"] == "reversed"
        assert sonra[1]["status"] == "posted" and sonra[1]["reversal"] == "1"
        assert sonra[2]["status"] == "posted" and sonra[2]["reversal"] == "0"
        assert sonra[2]["revision_no"] == "3"
        assert sonra[2]["source"] == "invoice" and sonra[2]["invoice_id"] == fid
        assert sonra[2]["invoice_number"] == fno
        cid = int(f["customer"]["id"])
        musteri = next(c for c in olcum[kurgu]["yaslandirma"]["customers"] if c["customer_id"] == cid)
        wo_no = olcum[kurgu]["work_order_no"]
        belge = next(d for d in musteri["documents"] if d["document_type"] == "service_fee" and d["document_no"] == f"{wo_no}-R3")
        assert Decimal(str(belge["remaining"])) == Decimal(str(f["totals"]["customer_amount"]))
