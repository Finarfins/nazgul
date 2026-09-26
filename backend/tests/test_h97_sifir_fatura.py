"""H97: matraha EŞİT belge iskontosu da 422 `ISKONTO_TOPLAMI_ASIYOR`.

#162 matrahı AŞAN iskontoyu 422'ye çevirdi, #165 önizlemeyi aynı fonksiyona
(`billing_service.price_service_lines`) bağladı. Kalan açık: iskonto matraha
TAM eşitse (%100 ya da FIXED == iskontosuz matrah) fatura 201 ile SIFIR
tutarla kesiliyordu. Şef kararı: eşit de reddedilir — kod aynı, gövde aynı
(`taxable_base`, `discount_type`, `discount_value`), mesaj "aşamaz veya eşit
olamaz". Sıfır tutarlı fatura Harman Zamanı'nın kestiği bir belge değil;
bedelsiz servis kapsam dışı.

`DiscountEngine` DEĞİŞMEDİ (genel motor, `test_enterprise_invoices.py`
onu ayrıca ölçer); `>=` koruması `price_service_lines`ta, motorun `>`
reddinin hemen ardında. Önizleme ve fatura ikisi de o fonksiyondan geçtiği
için ikisi de 422 verir — aşağıda İKİSİ de ölçülür.

Senaryo `tests/h97_sifir_fatura_senaryo.py`de (SQLite ve PG ikizi ortak);
burada geçici bir SQLite veritabanında ALT SÜREÇTE koşar. PG ikizi:
`test_h97_sifir_fatura_postgresql.py`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from tests.h97_sifir_fatura_senaryo import ESIT, D, sinirin_alti_beklenen

BACKEND = Path(__file__).resolve().parents[1]

_ALT_SUREC = r'''
import json, sys
from fastapi.testclient import TestClient
from app.main import app
from tests.h97_sifir_fatura_senaryo import olc, oturum

with TestClient(app) as c:
    out = olc(c, oturum(c, "H97SifirFatura123!"), "sqlite")
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=str)
print("H97_OLCUM_OK")
'''


@pytest.fixture(scope="module")
def olcum(tmp_path_factory) -> dict:
    tmp = tmp_path_factory.mktemp("h97")
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp / 'h97.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    cikti = tmp / "olcum.json"
    sonuc = subprocess.run(
        [sys.executable, "-c", _ALT_SUREC, str(cikti)],
        cwd=BACKEND, env=env, text=True, capture_output=True, timeout=300,
    )
    assert sonuc.returncode == 0 and "H97_OLCUM_OK" in sonuc.stdout, sonuc.stdout + "\n" + sonuc.stderr
    return json.loads(cikti.read_text(encoding="utf-8"))


@pytest.mark.parametrize("taraf", ["onizleme", "fatura"])
@pytest.mark.parametrize(("kurgu", "ad", "tur", "deger", "matrah"), ESIT, ids=[e[1] for e in ESIT])
def test_1_matraha_esit_iskonto_onizlemede_ve_faturada_422(olcum, taraf, kurgu, ad, tur, deger, matrah):
    sonuc = olcum[taraf][ad]
    assert sonuc["status"] == 422, (taraf, ad, sonuc)
    detay = sonuc["body"]["detail"]
    assert detay["code"] == "ISKONTO_TOPLAMI_ASIYOR", (taraf, ad, detay)
    # Gövde #162'deki "aşan" gövdesiyle aynı alanları taşır.
    assert (D(detay["taxable_base"]), detay["discount_type"], D(detay["discount_value"])) == (
        D(matrah), tur, D(deger)), (taraf, ad, detay)
    assert detay["message"].endswith("aşamaz veya eşit olamaz."), detay


def test_2_matrahin_bir_kurus_alti_kesilir_ve_rakam_decimal_ile_tutar(olcum):
    bek = sinirin_alti_beklenen()
    # Elle: 0.01 × %20 = 0.002 → 0.00 KDV; genel toplam 0.01.
    assert (bek["tax"], bek["grand_total"]) == (Decimal("0.00"), Decimal("0.01"))
    onizleme, fatura = olcum["alti_onizleme"], olcum["alti_fatura"]
    assert onizleme["status"] == 200, onizleme
    assert fatura["status"] == 201, fatura
    for ad, totals in (("onizleme", onizleme["body"]["totals"]), ("fatura", fatura["body"]["totals"])):
        assert (D(totals["tax"]), D(totals["grand_total"]), D(totals["global_discount_base"])) == (
            bek["tax"], bek["grand_total"], Decimal("199.99")), (ad, totals)
    # Reddedilen dört istek yarım iz bırakmadı: aynı A iş emri faturalandı.
    assert D(fatura["body"]["totals"]["customer_amount"]) == bek["grand_total"]
