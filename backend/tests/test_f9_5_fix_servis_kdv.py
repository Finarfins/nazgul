"""F9-5-fix (K8): servis faturası KDV'si — H79, H80, H82.

Keşif `docs/f9-5-muhasebe-disa-aktarim-kesif-2026-09-24.md` §1.3 İKİ kusur
ölçtü, §7.0b bu dilimi tarif etti:

* **KUSUR 1 (H79)** — `LABOR` satırı `tax_rate=0` ile kuruluyordu. Artık
  `invoice_service.SERVICE_LABOR_VAT_RATE` (20; firma ayarı YOK, çekirdek
  şemanın `vat_rate` server_default'u) taşıyor. Hukuki oran DOĞRULANMADI.
* **KUSUR 2 (H80)** — genel iskonto payı `total`dan düşüyor ama
  `tax_amount = line.tax` kalıyordu; yani iskonto KDV matrahını DÜŞÜRMÜYORDU.
  Artık pay satır MATRAHINA dağıtılıyor ve `tax_amount = round(oran × (matrah
  − pay))`, `compute_line` ile aynı kuruş yuvarlamasıyla.
* **H82** — `seed_demo_data.py` başlık KDV'sini `ara toplam × 0.20` ile
  yazıyordu; satır KDV'lerinin toplamından kuruş kayıyordu.

Faturalar SQL'siz senaryoyla (`tests/f9_5_servis_kdv_senaryo.py`) geçici bir
SQLite veritabanında ALT SÜREÇTE kesilir (uygulama ayarları içe aktarma anında
donar). PG ikizi: `test_f9_5_fix_servis_kdv_postgresql.py`.

Kesilmiş faturalar YENİDEN HESAPLANMAZ (`docs/financial-rounding-policy.md`
:26-29); bu dosya yalnız YENİ faturayı ölçer.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from tests.f9_5_servis_kdv_senaryo import (
    IZINLI_DEGISIMLER,
    TABAN_ISKONTOSUZ,
    D,
    duzlestir,
    kalem_matrahi,
    kdv_ozdesligi,
    kurus,
    para_gorunumu,
)

BACKEND = Path(__file__).resolve().parents[1]

_ALT_SUREC = r'''
import json, sys
from fastapi.testclient import TestClient
from app.main import app
from tests.f9_5_servis_kdv_senaryo import oturum, dunya, tamamlanmis_is_emri, fatura_kes

with TestClient(app) as c:
    h = oturum(c, "K8Servis123!")
    w = dunya(c, h, "sqlite")
    out = {
        "iskontosuz": fatura_kes(c, w, tamamlanmis_is_emri(c, w)),
        "yuzde10": fatura_kes(c, w, tamamlanmis_is_emri(c, w), global_discount_type="PERCENT", global_discount_value="10"),
        "yuzde12_5": fatura_kes(c, w, tamamlanmis_is_emri(c, w), global_discount_type="PERCENT", global_discount_value="12.5"),
        "sabit10": fatura_kes(c, w, tamamlanmis_is_emri(c, w, garanti="NONE", yuzde="0"), global_discount_type="FIXED", global_discount_value="10"),
    }
    import io, pdfplumber
    pdf = c.get(f"/api/invoices/{out['yuzde10']['id']}/pdf", headers=w["h"])
    assert pdf.status_code == 200, pdf.text
    with pdfplumber.open(io.BytesIO(pdf.content)) as belge:
        out["pdf_satirlari"] = [satir for sayfa in belge.pages for satir in (sayfa.extract_text() or "").splitlines()]
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=str)
print("K8_FATURALAR_OK")
'''


@pytest.fixture(scope="module")
def faturalar(tmp_path_factory) -> dict:
    tmp = tmp_path_factory.mktemp("k8")
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp / 'k8.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    cikti = tmp / "faturalar.json"
    sonuc = subprocess.run(
        [sys.executable, "-c", _ALT_SUREC, str(cikti)],
        cwd=BACKEND, env=env, text=True, capture_output=True, timeout=300,
    )
    assert sonuc.returncode == 0 and "K8_FATURALAR_OK" in sonuc.stdout, sonuc.stdout + "\n" + sonuc.stderr
    return json.loads(cikti.read_text(encoding="utf-8"))


def _labor(fatura: dict) -> dict:
    (kalem,) = [k for k in fatura["items"] if k["item_type"] == "LABOR"]
    return kalem


# (1) İşçilik KDV'si — iskontosuz --------------------------------------------

def test_1_iscilik_kdvsi_firma_oranindan_ve_kurusu_kurusuna(faturalar):
    from app.invoice_service import SERVICE_LABOR_VAT_RATE

    fatura = faturalar["iskontosuz"]
    labor = _labor(fatura)
    assert D(labor["tax_rate"]) == SERVICE_LABOR_VAT_RATE == Decimal("20")
    matrah = kalem_matrahi(labor)
    assert matrah == Decimal("300.00")
    assert D(labor["tax_amount"]) == kurus(matrah * SERVICE_LABOR_VAT_RATE / 100) == Decimal("60.00")
    # Σ tax_amount == Σ round(oran × matrah), her satır kendi oranıyla.
    beklenen = sum((kurus(kalem_matrahi(k) * D(k["tax_rate"]) / 100) for k in fatura["items"]), Decimal("0"))
    assert sum((D(k["tax_amount"]) for k in fatura["items"]), Decimal("0")) == beklenen == Decimal("98.10")
    assert D(fatura["totals"]["tax"]) == beklenen
    kdv_ozdesligi(fatura)


# (2) %10 belge iskontosu KDV matrahını düşürür ------------------------------

def test_2_belge_iskontosu_kdv_matrahini_dusurur(faturalar):
    fatura = faturalar["yuzde10"]
    kdv_ozdesligi(fatura)
    # Matrah 300 + 180 + 21 = 501; %10 = 50.10, matraha oranla 30.00/18.00/2.10.
    beklenen = {
        # item_type, açıklama: (discount_amount, tax_amount, total)
        "Servis İşçiliği": ("30.00", "54.00", "324.00"),
        "K8 P1": ("38.00", "32.40", "194.40"),
        "K8 P2": ("2.10", "1.89", "20.79"),
    }
    for kalem in fatura["items"]:
        indirim, kdv, toplam = beklenen[kalem["description"]]
        assert (kurus(D(kalem["discount_amount"])), kurus(D(kalem["tax_amount"])), kurus(D(kalem["total"]))) == (
            D(indirim), D(kdv), D(toplam)), kalem
    totals = fatura["totals"]
    assert D(totals["tax"]) == Decimal("88.29")
    assert D(totals["grand_total"]) == Decimal("539.19")
    # Başlık `global_discount` ÖDENECEKTEKİ düşüş: 599.10 − 539.19. PDF'in
    # "Ara Toplam" satırı `grand_total + global_discount`tır; KDV'li brüt budur.
    assert D(totals["global_discount"]) == Decimal("59.91")
    assert D(totals["grand_total"]) + D(totals["global_discount"]) == Decimal("599.10")
    # `totals.labor` NET kalır (önizleme ile aynı): 2 × 150.
    assert D(totals["labor"]) == Decimal("300.00")


def test_2b_kurus_kalanli_yuzde_ve_sabit_iskonto_ozdeslikleri(faturalar):
    for ad in ("yuzde12_5", "sabit10"):
        kdv_ozdesligi(faturalar[ad])
        totals = faturalar[ad]["totals"]
        # Ödenecekteki düşüş + genel toplam == iskontosuz KDV'li brüt (360 + 216 + 23.10).
        assert D(totals["grand_total"]) + D(totals["global_discount"]) == Decimal("599.10"), ad
    # FIXED değer MATRAH iskontosudur: satırlara düşen belge payları tam 10.00.
    sabit = faturalar["sabit10"]
    satir_iskontosu = {"Servis İşçiliği": Decimal("0"), "K8 P1": Decimal("20.00"), "K8 P2": Decimal("0")}
    paylar = sum((D(k["discount_amount"]) - satir_iskontosu[k["description"]] for k in sabit["items"]), Decimal("0"))
    assert kurus(paylar) == Decimal("10.00")


# (3) İskontosuz yol: TABAN'a göre YALNIZ işçilik KDV alanları değişti --------

def test_3_iskontosuz_yol_tabana_gore_yalniz_iscilik_kdvsi(faturalar):
    taban = dict(duzlestir(TABAN_ISKONTOSUZ))
    simdi = dict(duzlestir(para_gorunumu(faturalar["iskontosuz"])))
    assert set(taban) == set(simdi), (set(taban) ^ set(simdi))
    degisen = {yol: (taban[yol], simdi[yol]) for yol in taban if taban[yol] != simdi[yol]}
    assert degisen == IZINLI_DEGISIMLER
    # Parça satırları BAYT-AYNI.
    assert para_gorunumu(faturalar["iskontosuz"])["kalemler"][1:] == TABAN_ISKONTOSUZ["kalemler"][1:]


# PDF: Ara Toplam − Global İndirim == Genel Toplam (işçilik KDV'si dahil) ----

def test_pdf_toplamlari_toplaniyor(faturalar):
    import re

    def tutar(onek: str) -> Decimal:
        (satir,) = [s for s in faturalar["pdf_satirlari"] if s.startswith(onek)]
        return D(re.search(r"(-?\d+\.\d{2})", satir).group(1))

    ara, indirim, genel = tutar("Ara Toplam:"), tutar("Global İndirim:"), tutar("Genel Toplam:")
    assert (ara, indirim, genel) == (Decimal("599.10"), Decimal("59.91"), Decimal("539.19"))
    assert ara - indirim == genel == D(faturalar["yuzde10"]["totals"]["grand_total"])


# (5) Seed: başlık KDV'si == Σ satır KDV'si, 60/60 satış + 25/25 alış ---------

_SEED = r'''
import sys
from seed_demo_data import build_demo
sayilar = build_demo(sys.argv[1])
assert sayilar["sales"] == 60 and sayilar["purchases"] == 25, sayilar
print("K8_SEED_OK")
'''


def test_5_seed_basliklari_satir_toplamina_esit(tmp_path):
    from sqlalchemy import create_engine, func, select

    from app.core_schema import order_items, orders, purchase_items, purchases

    url = f"sqlite:///{(tmp_path / 'seed.db').as_posix()}"
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    env["PYTHONPATH"] = str(BACKEND)
    sonuc = subprocess.run(
        [sys.executable, "-c", _SEED, url], cwd=BACKEND, env=env, text=True, capture_output=True, timeout=300,
    )
    assert sonuc.returncode == 0 and "K8_SEED_OK" in sonuc.stdout, sonuc.stdout + "\n" + sonuc.stderr

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            for baslik, kalem, fk, kalem_adi in (
                (orders, order_items, order_items.c.order_id, "satış"),
                (purchases, purchase_items, purchase_items.c.purchase_id, "alış"),
            ):
                satirlar = conn.execute(
                    select(
                        baslik.c.id, baslik.c.subtotal, baslik.c.vat_total, baslik.c.grand_total,
                        func.count(kalem.c.id), func.sum(kalem.c.line_subtotal),
                        func.sum(kalem.c.line_vat), func.sum(kalem.c.line_total),
                    ).join(kalem, fk == baslik.c.id).group_by(
                        baslik.c.id, baslik.c.subtotal, baslik.c.vat_total, baslik.c.grand_total,
                    )
                ).all()
                kayan = [
                    r for r in satirlar
                    if (kurus(D(r[1])), kurus(D(r[2])), kurus(D(r[3])))
                    != (kurus(D(r[5])), kurus(D(r[6])), kurus(D(r[7])))
                ]
                beklenen = 60 if kalem_adi == "satış" else 25
                assert len(satirlar) == beklenen, (kalem_adi, len(satirlar))
                assert kayan == [], (kalem_adi, f"{len(kayan)}/{beklenen} başlık satırlarından kayıyor", kayan[:5])
    finally:
        engine.dispose()
