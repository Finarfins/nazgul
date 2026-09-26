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
    IZINLI_EKLENENLER,
    TABAN_ISKONTOSUZ,
    D,
    kalem_matrahi,
    kdv_ozdesligi,
    kurus,
    para_gorunumu,
    tabana_gore_fark,
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
    # İskonto matrahı (501.00) aşarsa 422 — KDV'li brütün (599.10) ALTINDA
    # kalan 550 de, %150 de. Reddedilen iş emri sonra iskontosuz kesilebilir.
    reddedilen = tamamlanmis_is_emri(c, w)
    out["asan"] = {}
    for ad, tur, deger in (("sabit550", "FIXED", "550"), ("sabit700", "FIXED", "700"), ("yuzde150", "PERCENT", "150")):
        r = c.post("/api/invoices/generate", headers=w["h"], json={
            "work_order_id": reddedilen, "global_discount_type": tur, "global_discount_value": deger})
        out["asan"][ad] = {"status": r.status_code, "body": r.json()}
    out["reddedilen_sonra"] = fatura_kes(c, w, reddedilen)
    import io, pdfplumber
    for ad in ("yuzde10", "sabit10"):
        pdf = c.get(f"/api/invoices/{out[ad]['id']}/pdf", headers=w["h"])
        assert pdf.status_code == 200, pdf.text
        with pdfplumber.open(io.BytesIO(pdf.content)) as belge:
            out["pdf_" + ad] = [satir for sayfa in belge.pages for satir in (sayfa.extract_text() or "").splitlines()]
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
    # Başlık `global_discount` ÖDENECEKTEKİ düşüş: 599.10 − 539.19 (KDV'li).
    # MATRAHA düşen iskonto ayrı anahtarda: 501 × %10 = 50.10 (PDF "İskonto").
    assert D(totals["global_discount"]) == Decimal("59.91")
    assert D(totals["global_discount_base"]) == Decimal("50.10")
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
    degisen, eklenen = tabana_gore_fark(para_gorunumu(faturalar["iskontosuz"]))
    assert degisen == IZINLI_DEGISIMLER
    # EK anahtarlar: matraha düşen belge iskontosu (burada 0.00) ve H91'in
    # önizlemeyle ortak `labor_tax`ı (60.00). Yeniden ad YOK.
    assert eklenen == IZINLI_EKLENENLER
    # Parça satırları BAYT-AYNI.
    assert para_gorunumu(faturalar["iskontosuz"])["kalemler"][1:] == TABAN_ISKONTOSUZ["kalemler"][1:]


# PDF (DÜZELTME 1): Matrah → İskonto → KDV Matrahı → KDV → Genel Toplam -------
# KDVK m.25: iskonto KDV'den ÖNCE düşer. Önceki blok "Ara Toplam (KDV dahil) −
# Global İndirim 12,00" basıyordu; 10 yazan kullanıcı 12 görüyordu.

_PDF_ETIKETLERI = ("Matrah:", "İskonto:", "KDV Matrahı:", "KDV:", "Genel Toplam:")


def _pdf_toplamlari(satirlar: list[str]) -> tuple[Decimal, ...]:
    import re

    def tutar(onek: str) -> Decimal:
        (satir,) = [s for s in satirlar if s.startswith(onek)]
        return D(re.search(r"(-?\d+\.\d{2})", satir).group(1))

    # Sıra da ölçülür: etiketler PDF'te tam bu sırayla geçer.
    sira = [next(i for i, s in enumerate(satirlar) if s.startswith(onek)) for onek in _PDF_ETIKETLERI]
    assert sira == sorted(sira), (sira, satirlar)
    return tuple(tutar(onek) for onek in _PDF_ETIKETLERI)


def _pdf_ozdeslikleri(matrah, iskonto, kdv_matrahi, kdv, genel) -> None:
    assert matrah - iskonto == kdv_matrahi, (matrah, iskonto, kdv_matrahi)
    assert kdv_matrahi + kdv == genel, (kdv_matrahi, kdv, genel)


def test_pdf_sabit_iskonto_girilen_tutari_basar(faturalar):
    fatura = faturalar["sabit10"]
    matrah, iskonto, kdv_matrahi, kdv, genel = _pdf_toplamlari(faturalar["pdf_sabit10"])
    _pdf_ozdeslikleri(matrah, iskonto, kdv_matrahi, kdv, genel)
    # Kullanıcı 10 yazdı; PDF 10,00 basar (KDV'li 12,00 DEĞİL).
    assert iskonto == Decimal("10.00") == D(fatura["totals"]["global_discount_base"])
    assert matrah == Decimal("501.00")  # Σ satır neti, iskontodan ÖNCE: 300 + 180 + 21
    assert kdv == D(fatura["totals"]["tax"])
    assert genel == D(fatura["totals"]["grand_total"])


def test_pdf_yuzde_iskonto_matrah_etkisini_basar(faturalar):
    fatura = faturalar["yuzde10"]
    toplamlar = _pdf_toplamlari(faturalar["pdf_yuzde10"])
    _pdf_ozdeslikleri(*toplamlar)
    assert toplamlar == (Decimal("501.00"), Decimal("50.10"), Decimal("450.90"), Decimal("88.29"), Decimal("539.19"))
    assert toplamlar[1] == D(fatura["totals"]["global_discount_base"])


@pytest.mark.parametrize(
    "ad, totals, beklenen",
    [
        # F9-5-fix ÖNCESİ iskontosuz fatura (TABAN): işçilik KDV'si 0.
        ("eski_iskontosuz",
         {"labor": "300.00", "parts": "239.10", "tax": "38.10", "global_discount": "0.00", "grand_total": "539.10"},
         ("501.00", "0.00", "501.00", "38.10", "539.10")),
        # F9-5-fix ÖNCESİ %10 iskontolu fatura: pay KDV'li satır toplamından
        # düşüyor, KDV aynı kalıyordu (539.10 × %10 = 53.91). Bu faturada
        # `global_discount_base` YOK; `global_discount` girilen tutardır.
        ("eski_yuzde10",
         {"labor": "300.00", "parts": "239.10", "tax": "38.10", "global_discount": "53.91", "grand_total": "485.19"},
         ("501.00", "53.91", "447.09", "38.10", "485.19")),
    ],
)
def test_pdf_eski_faturalar_tutarli_basilir(ad, totals, beklenen):
    import io

    import pdfplumber

    from app.invoice_pdf import build_invoice_pdf

    fatura = {
        "invoice_number": "FTR-ESKI-1", "currency": "TRY", "created_at": "2026-01-01 10:00:00",
        "company_snapshot": json.dumps({"name": "Harman"}), "customer_snapshot": json.dumps({"name": "M"}),
        "machine_snapshot": json.dumps({"brand": "B", "model": "M"}),
        "work_order_snapshot": json.dumps({"work_order_no": "IE-1", "status": "COMPLETED"}),
        "totals_snapshot": json.dumps({**totals, "customer_amount": totals["grand_total"], "warranty_amount": "0.00"}),
        "warranty_snapshot": json.dumps({"type": "NONE"}), "technician_snapshot": json.dumps({"display_name": "T"}),
        "payment_terms": None, "notes": None,
    }
    with pdfplumber.open(io.BytesIO(build_invoice_pdf(fatura, []))) as belge:
        satirlar = [s for sayfa in belge.pages for s in (sayfa.extract_text() or "").splitlines()]
    toplamlar = _pdf_toplamlari(satirlar)
    _pdf_ozdeslikleri(*toplamlar)
    assert toplamlar == tuple(D(x) for x in beklenen), ad


# (4) İskonto matrahı aşarsa 422 ISKONTO_TOPLAMI_ASIYOR (500 DEĞİL) ----------

def test_4_iskonto_matrahi_asarsa_422(faturalar):
    for ad, tur, deger in (("sabit550", "FIXED", "550"), ("sabit700", "FIXED", "700"), ("yuzde150", "PERCENT", "150")):
        sonuc = faturalar["asan"][ad]
        assert sonuc["status"] == 422, (ad, sonuc)
        detay = sonuc["body"]["detail"]
        assert detay["code"] == "ISKONTO_TOPLAMI_ASIYOR", (ad, detay)
        assert (D(detay["taxable_base"]), detay["discount_type"], D(detay["discount_value"])) == (
            Decimal("501.00"), tur, D(deger)), (ad, detay)
    # Red yarım iz bırakmadı: aynı iş emri sonra iskontosuz kesilebildi.
    kdv_ozdesligi(faturalar["reddedilen_sonra"])
    assert D(faturalar["reddedilen_sonra"]["totals"]["grand_total"]) == Decimal("599.10")


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
