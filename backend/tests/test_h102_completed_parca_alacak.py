"""H102: COMPLETED iş emrinde parça değişikliği servis alacağını hemen uzlaştırır.

#165 merceği: COMPLETED'dan sonra eklenen parça önizlemeyi 419.37 → 424.76
yaptı ama alacak fatura kesilene kadar 419.37'de kaldı. H102 parça
ekle/güncelle/sil/iade uçlarından, iş emri COMPLETED iken
`reconcile_service_receivable`i çağırır (ilk doğum YOK:
`allow_initial_create=False`). Faturadan sonra parçalar donuktur (409) ve
fatura kaynaktır — motorun o davranışı ölçülüp olduğu gibi korundu.

Senaryo `tests/h102_completed_parca_senaryo.py`de (SQLite ve PG ikizi ortak);
burada geçici bir SQLite veritabanında ALT SÜREÇTE koşar. PG ikizi:
`test_h102_completed_parca_alacak_postgresql.py`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.h102_completed_parca_senaryo import BEKLENEN, aktif, satirlar

BACKEND = Path(__file__).resolve().parents[1]

_ALT_SUREC = r'''
import json, os, sys
if os.environ.get("H102_MUTASYON") == "1":
    # Mutasyon: parça uçlarındaki uzlaştırma çağrısını düşür (H102 öncesi).
    import app.routers.work_order_parts as wop
    wop._reconcile_completed_receivable = lambda *args, **kwargs: None
from fastapi.testclient import TestClient
from app.main import app
from tests.h102_completed_parca_senaryo import olc, oturum

with TestClient(app) as c:
    out = olc(c, oturum(c, "H102Parca123!"), "sqlite")
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=str)
print("H102_OLCUM_OK")
'''

R1 = ("1", "posted", "419.37", "0")
EKLENINCE = [("1", "reversed", "419.37", "0"), ("2", "posted", "-419.37", "1"), ("3", "posted", "424.76", "0")]


def _kos(tmp: Path, *, mutasyon: bool = False) -> dict:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp / 'h102.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["H102_MUTASYON"] = "1" if mutasyon else "0"
    cikti = tmp / "olcum.json"
    sonuc = subprocess.run(
        [sys.executable, "-c", _ALT_SUREC, str(cikti)],
        cwd=BACKEND, env=env, text=True, capture_output=True, timeout=300,
    )
    assert sonuc.returncode == 0 and "H102_OLCUM_OK" in sonuc.stdout, sonuc.stdout + "\n" + sonuc.stderr
    return json.loads(cikti.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def olcum(tmp_path_factory) -> dict:
    return _kos(tmp_path_factory.mktemp("h102"))


def test_1_her_adimda_onizleme_aktif_alacaga_esit(olcum):
    for kurgu, adimlar in olcum.items():
        for ad, durum in adimlar.items():
            if "alacak" not in durum:
                continue
            (tek,) = aktif(durum)
            assert tek["gross_amount"] == durum["onizleme"], (kurgu, ad, durum)


def test_2_completed_sonrasi_parca_eklemek_tam_bir_ters_bir_yeni_kayit(olcum):
    for kurgu in ("X", "Y", "Z"):
        assert satirlar(olcum[kurgu]["tamamlaninca"]) == [R1]
        assert satirlar(olcum[kurgu]["p3_eklenince"]) == EKLENINCE, kurgu
        assert olcum[kurgu]["p3_eklenince"]["onizleme"] == BEKLENEN["B+P3"]
        assert olcum[kurgu]["p3_eklenince"]["alacak"][1]["reason"] == "parts_reconciliation", kurgu


def test_3_ayni_tutari_birakan_ekleme_revizyon_yapmaz(olcum):
    x = olcum["X"]
    assert x["sifir_tutar_eklenince"]["onizleme"] == BEKLENEN["B+P3"]
    assert x["sifir_tutar_eklenince"]["alacak"] == x["p3_eklenince"]["alacak"]


def test_4_fatura_ayni_tutarda_revizyon_yapmaz_r3_faturaya_baglanir(olcum):
    x = olcum["X"]
    assert satirlar(x["fatura_sonrasi"]) == EKLENINCE
    son = x["fatura_sonrasi"]["alacak"][-1]
    assert (son["source"], son["invoice_id"], son["invoice_number"]) == \
           ("invoice", x["fatura"]["id"], x["fatura"]["invoice_number"])


def test_5_faturadan_sonra_parca_409_alacak_degismez(olcum):
    x = olcum["X"]
    assert x["faturadan_sonra_ekle"]["status"] == 409
    assert x["faturadan_sonra_ekle"]["alacak"] == x["fatura_sonrasi"]["alacak"]


def test_6_guncelle_ve_sil_alacagi_izler_silince_geri_doner(olcum):
    y = olcum["Y"]
    assert y["p3_guncellenince"]["onizleme"] == BEKLENEN["B+2P3"]
    assert satirlar(y["p3_guncellenince"]) == EKLENINCE[:2] + [
        ("3", "reversed", "424.76", "0"), ("4", "posted", "-424.76", "1"), ("5", "posted", "430.15", "0")]
    assert y["p3_silinince"]["onizleme"] == BEKLENEN["B"]
    assert satirlar(y["p3_silinince"])[4:] == [
        ("5", "reversed", "430.15", "0"), ("6", "posted", "-430.15", "1"), ("7", "posted", "419.37", "0")]
    # Fatura 419.37 → yeni satır yok; -R7 faturaya bağlanır.
    assert satirlar(y["fatura_sonrasi"]) == satirlar(y["p3_silinince"])
    son = y["fatura_sonrasi"]["alacak"][-1]
    assert (son["revision_no"], son["source"], son["invoice_id"]) == ("7", "invoice", y["fatura"]["id"])


def test_7_iade_edilen_parca_alacaktan_duser(olcum):
    z = olcum["Z"]
    assert z["p3_iade_edilince"]["onizleme"] == BEKLENEN["B"]
    assert satirlar(z["p3_iade_edilince"])[2:] == [
        ("3", "reversed", "424.76", "0"), ("4", "posted", "-424.76", "1"), ("5", "posted", "419.37", "0")]


def test_8_mutasyon_uzlastirma_cagrisi_yoksa_kirmizi(tmp_path):
    bozuk = _kos(tmp_path, mutasyon=True)
    # H102 öncesi: önizleme 424.76 derken alacak 419.37'de kalır.
    assert bozuk["X"]["p3_eklenince"]["onizleme"] == BEKLENEN["B+P3"]
    assert satirlar(bozuk["X"]["p3_eklenince"]) == [R1]
    with pytest.raises(AssertionError):
        test_1_her_adimda_onizleme_aktif_alacaga_esit(bozuk)
    with pytest.raises(AssertionError):
        test_2_completed_sonrasi_parca_eklemek_tam_bir_ters_bir_yeni_kayit(bozuk)
