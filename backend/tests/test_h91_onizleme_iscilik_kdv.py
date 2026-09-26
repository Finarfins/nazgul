"""H91: faturalama önizlemesi ve servis alacağı işçilik KDV'sini faturayla AYNI hesaplar.

F9-5-fix (#162) faturada işçiliğe `SERVICE_LABOR_VAT_RATE` (%20) ekledi;
önizleme (`GET /api/work-orders/{id}/invoice`) ve COMPLETED'da doğan servis
alacağı işçiliği %0 ile fiyatlamaya devam etti — #162 merceği farkı ölçtü:
tam olarak işçilik KDV'si (önizleme 200, fatura 240).

H91 fiyatlamayı TEK fonksiyona çıkardı: `billing_service.price_service_lines`.
`build_invoice_summary` onu çağırır; `generate_invoice` özetin fiyatlanmış
kalemlerini yazar, aritmetiğin ikinci kopyası yoktur (AST kapısı aşağıda).
Önizleme GET'i isteğe bağlı `global_discount_type/value` sorgu parametresi
alır (yeni uç YOK), böylece iskontolu önizleme de faturaya eşittir.

Alacak: COMPLETED anındaki alacak artık KDV dahil müşteri payıdır (müşteri
brütü borçlanır). Fatura kesilince tutar/kur/vade aynıysa revizyon YAPILMAZ
(`service_receivable_engine._same_charge`); değiştiyse (iskonto, kur, ya da
tamamlanma ile fatura arasında değişen parça) ters kayıt + yeni belge.

Senaryo `tests/h91_onizleme_senaryo.py`de (SQLite ve PG ikizi ortak); burada
geçici bir SQLite veritabanında ALT SÜREÇTE koşar. PG ikizi:
`test_h91_onizleme_iscilik_kdv_postgresql.py`.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from tests.h91_onizleme_senaryo import BEKLENEN, D, hatalar

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"

_ALT_SUREC = r'''
import json, os, sys
if os.environ.get("H91_MUTASYON") == "1":
    # Paylaşılan fonksiyonu boz: işçilik KDV'sini %0'a geri çek (H91 öncesi
    # önizleme hatası). price_service_lines oranı modül globalinden okur.
    from decimal import Decimal
    import app.billing_service as bs
    bs.SERVICE_LABOR_VAT_RATE = Decimal("0")
if os.environ.get("H91_MUTASYON_UPDATE_YOK") == "1":
    # Mutasyon: ayni tutarda fatura kesildiginde guncellemeyi dusur
    import app.service_receivable_engine as sre
    sre._update_same_charge_document = lambda *args, **kwargs: None
from fastapi.testclient import TestClient
from app.main import app
from tests.h91_onizleme_senaryo import olc, oturum

with TestClient(app) as c:
    out = olc(c, oturum(c, "H91Onizleme123!"), "sqlite")
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=str)
print("H91_OLCUM_OK")
'''


def _kos(tmp: Path, *, mutasyon: bool = False, update_yok: bool = False) -> dict:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp / 'h91.db').as_posix()}"
    env["PYTHONPATH"] = str(BACKEND)
    env["H91_MUTASYON"] = "1" if mutasyon else "0"
    env["H91_MUTASYON_UPDATE_YOK"] = "1" if update_yok else "0"
    cikti = tmp / "olcum.json"
    sonuc = subprocess.run(
        [sys.executable, "-c", _ALT_SUREC, str(cikti)],
        cwd=BACKEND, env=env, text=True, capture_output=True, timeout=300,
    )
    assert sonuc.returncode == 0 and "H91_OLCUM_OK" in sonuc.stdout, sonuc.stdout + "\n" + sonuc.stderr
    return json.loads(cikti.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def olcum(tmp_path_factory) -> dict:
    return _kos(tmp_path_factory.mktemp("h91"), mutasyon=False)


# (1) Önizleme == fatura, üç kurgu + D ----------------------------------------

def test_1_onizleme_fatura_ve_alacak_elle_turetilen_rakamlarda(olcum):
    assert hatalar(olcum) == []


@pytest.mark.parametrize("kurgu", ["A", "B", "C"])
def test_2_onizleme_grand_total_fatura_grand_total_ve_kalem_toplami(olcum, kurgu):
    o, f = olcum[kurgu]["onizleme"], olcum[kurgu]["fatura"]
    assert D(o["totals"]["grand_total"]) == D(f["totals"]["grand_total"]) == D(BEKLENEN[kurgu]["grand_total"])
    # Önizlemenin KDV'si = faturanın satır KDV'lerinin toplamı (işçilik dahil).
    assert sum((D(k["tax_amount"]) for k in f["items"]), Decimal("0")) == D(o["totals"]["tax"])


def test_3_onizleme_isciligi_net_gosterir_kdvsini_ayri_satirda(olcum):
    # `totals.labor` NET kalır (F9-5 testi ve UI "İşçilik" satırı bunu okur);
    # işçilik KDV'si yeni `labor_tax` satırıdır: 2 × 150 × %20 = 60.00.
    t = olcum["B"]["onizleme"]["totals"]
    assert (D(t["labor"]), D(t["labor_tax"])) == (Decimal("300.00"), Decimal("60.00")), t


# (2) Alacak ------------------------------------------------------------------

def test_4_completed_alacagi_kdv_dahil_ve_fatura_revizyonu_bos(olcum):
    for kurgu in ("A", "B"):
        tamam = olcum[kurgu]["alacak_tamamlaninca"]
        sonra = olcum[kurgu]["alacak_fatura_sonrasi"]
        # COMPLETED: tek belge, müşteri BRÜTÜ borçlanır (A 240.00, B 419.37).
        assert [b["gross_amount"] for b in tamam] == [BEKLENEN[kurgu]["customer_amount"]], tamam
        # Fatura aynı tutarı söyler → revizyon YOK (ters kayıt + yeni belge çifti yok; defter satır sayısı değişmez).
        assert len(sonra) == len(tamam) == 1
        assert [(b["revision_no"], b["status"], b["gross_amount"], b["reversal"]) for b in sonra] == \
               [(b["revision_no"], b["status"], b["gross_amount"], b["reversal"]) for b in tamam]


def test_5_iskonto_alacagi_faturaya_gore_revize_eder(olcum):
    tamam = olcum["C"]["alacak_tamamlaninca"]
    sonra = olcum["C"]["alacak_fatura_sonrasi"]
    # COMPLETED'da iskonto bilinmez: B ile aynı 419.37. Fatura %10 iskontolu
    # 377.43 → +419.37 −419.37 +377.43.
    assert [b["gross_amount"] for b in tamam] == ["419.37"]
    assert [(b["revision_no"], b["status"], b["gross_amount"], b["reversal"]) for b in sonra] == [
        ("1", "reversed", "419.37", "0"), ("2", "posted", "-419.37", "1"), ("3", "posted", "377.43", "0")]


def test_6_tamamlanma_ile_fatura_arasinda_degisen_parca_alacagi_revize_eder(olcum):
    tamam = olcum["D"]["alacak_tamamlaninca"]
    sonra = olcum["D"]["alacak_fatura_sonrasi"]
    # Tamamlanınca B rakamı (419.37); ek P3 satırı (7.70) sonrası fatura 424.76.
    assert [b["gross_amount"] for b in tamam] == ["419.37"]
    assert [(b["revision_no"], b["status"], b["gross_amount"], b["reversal"]) for b in sonra] == [
        ("1", "reversed", "419.37", "0"), ("2", "posted", "-419.37", "1"), ("3", "posted", "424.76", "0")]


def test_7_matrahi_asan_iskonto_onizlemede_de_422(olcum):
    asan = olcum["asan_onizleme"]
    assert asan["status"] == 422, asan
    detay = asan["body"]["detail"]
    assert detay["code"] == "ISKONTO_TOPLAMI_ASIYOR" and detay["taxable_base"] == "501.00", detay


# (3) Mutasyon: paylaşılan fonksiyon bozulunca İKİ taraf da kırmızı -----------

def test_8_mutasyon_paylasilan_fonksiyon_bozulunca_onizleme_ve_fatura_kirmizi(tmp_path):
    bozuk = hatalar(_kos(tmp_path, mutasyon=True))
    for kurgu in ("A", "B", "C", "D"):
        assert any(h.startswith(f"{kurgu}:onizleme:labor_tax") for h in bozuk), (kurgu, bozuk)
        assert any(h.startswith(f"{kurgu}:fatura:labor_tax") for h in bozuk), (kurgu, bozuk)
        assert any(h.startswith(f"{kurgu}:onizleme:grand_total") for h in bozuk), (kurgu, bozuk)
        assert any(h.startswith(f"{kurgu}:fatura:grand_total") for h in bozuk), (kurgu, bozuk)
    # İkisi aynı (bozuk) fonksiyondan geçtiği için birbirine hâlâ eşit: ikinci
    # bir kopya olsaydı burada "esitlik" sapması görünürdü.
    assert not [h for h in bozuk if ":esitlik:" in h], bozuk


# (4) AST kapısı: aritmetiğin tek kopyası -------------------------------------

_FIYAT_ADLARI = {"compute_line", "distribute_amount", "DiscountEngine", "SERVICE_LABOR_VAT_RATE"}


def _agac(ad: str) -> ast.Module:
    return ast.parse((APP / ad).read_text(encoding="utf-8"))


def _adlar(dugum: ast.AST) -> list[str]:
    return [d.id if isinstance(d, ast.Name) else d.attr
            for d in ast.walk(dugum) if isinstance(d, (ast.Name, ast.Attribute))]


def _islev(agac: ast.Module, ad: str) -> ast.FunctionDef:
    (bulunan,) = [d for d in agac.body if isinstance(d, ast.FunctionDef) and d.name == ad]
    return bulunan


def _cagrilar(dugum: ast.AST, ad: str) -> list[ast.Call]:
    return [d for d in ast.walk(dugum) if isinstance(d, ast.Call)
            and ((isinstance(d.func, ast.Name) and d.func.id == ad)
                 or (isinstance(d.func, ast.Attribute) and d.func.attr == ad))]


def test_9_fatura_ve_onizleme_ucu_fiyat_aritmetigi_tasimiyor():
    # invoice_service ve önizleme ucu hiçbir fiyatlama yapı taşına dokunmaz
    # (SERVICE_LABOR_VAT_RATE yalnız yeniden ihraç: import + __all__ metni).
    for ad in ("invoice_service.py", "routers/work_order_billing.py"):
        agac = _agac(ad)
        govde = [d for d in agac.body if not isinstance(d, (ast.ImportFrom, ast.Import, ast.Assign))]
        kullanilan = {a for d in govde for a in _adlar(d)} & _FIYAT_ADLARI
        assert not kullanilan, (ad, kullanilan)
    # generate_invoice özeti TEK kez, belge iskontosuyla ister.
    (cagri,) = _cagrilar(_islev(_agac("invoice_service.py"), "generate_invoice"), "build_invoice_summary")
    assert {k.arg for k in cagri.keywords} == {"discount_type", "discount_value"}


def test_10_billing_service_de_aritmetik_yalniz_price_service_lines_icinde():
    agac = _agac("billing_service.py")
    fiyat = _islev(agac, "price_service_lines")
    for ad in ("distribute_amount", "DiscountEngine", "SERVICE_LABOR_VAT_RATE"):
        disarida = [d for d in agac.body if d is not fiyat
                    and not isinstance(d, (ast.ImportFrom, ast.Import, ast.Assign))
                    and ad in _adlar(d)]
        assert not disarida, (ad, [getattr(d, "name", d) for d in disarida])
    # Özet fiyatlamayı TEK kez çağırır ve payable rakamlarını ondan alır.
    assert len(_cagrilar(_islev(agac, "build_invoice_summary"), "price_service_lines")) == 1


# (5) Alacak ↔ fatura bağlantısı ve yaşlandırma --------------------------------

def test_11_fatura_sonrasi_alacak_ve_yaslandirma_baglantisi(olcum):
    """Fatura sonrası alacak bağlantısı ve yaşlandırma raporu kontrolü.

    Aynı tutarda fatura kesildiğinde (A, B): defter satır sayısı değişmez
    (tek -R1 satırı), -R1 satırı faturayı referanslar, yaşlandırma raporu
    fatura numarasını (-R1 satırında) ve genel toplamı gösterir.
    Değişen tutarda (C iskonto, D ek parça): tam olarak bir ters kayıt + bir
    yeniden kayıt (+/-/+), repost satırı faturayı referanslar.
    """
    for kurgu in ("A", "B"):
        f = olcum[kurgu]["fatura"]
        fid = str(f["id"])
        fno = f["invoice_number"]
        tamam = olcum[kurgu]["alacak_tamamlaninca"]
        sonra = olcum[kurgu]["alacak_fatura_sonrasi"]
        # Defter satır sayısı DEĞİŞMEZ (tek -R1 satırı)
        assert len(sonra) == len(tamam) == 1
        assert sonra[0]["revision_no"] == "1" and sonra[0]["status"] == "posted"
        # COMPLETED'da faturayı bilmez; fatura kesilince -R1 faturayı referanslar
        assert tamam[0]["source"] == "computed" and tamam[0]["invoice_id"] == ""
        assert sonra[0]["source"] == "invoice" and sonra[0]["invoice_id"] == fid
        assert sonra[0]["invoice_number"] == fno
        # Yaşlandırma raporu: -R1 satırı faturanın genel toplamını taşır
        cid = int(f["customer"]["id"])
        musteri = next(c for c in olcum[kurgu]["yaslandirma"]["customers"] if c["customer_id"] == cid)
        wo_no = olcum[kurgu]["work_order_no"]
        belge = next(d for d in musteri["documents"] if d["document_type"] == "service_fee" and d["document_no"] == f"{wo_no}-R1")
        assert D(belge["remaining"]) == D(f["totals"]["customer_amount"])

    for kurgu in ("C", "D"):
        f = olcum[kurgu]["fatura"]
        fid = str(f["id"])
        fno = f["invoice_number"]
        sonra = olcum[kurgu]["alacak_fatura_sonrasi"]
        assert len(sonra) == 3
        # Tam olarak bir ters kayıt + bir yeniden kayıt
        assert sonra[0]["status"] == "reversed"
        assert sonra[1]["status"] == "posted" and sonra[1]["reversal"] == "1"
        assert sonra[2]["status"] == "posted" and sonra[2]["reversal"] == "0"
        assert sonra[2]["revision_no"] == "3"
        # Repost faturayı referanslar
        assert sonra[2]["source"] == "invoice" and sonra[2]["invoice_id"] == fid
        assert sonra[2]["invoice_number"] == fno
        cid = int(f["customer"]["id"])
        musteri = next(c for c in olcum[kurgu]["yaslandirma"]["customers"] if c["customer_id"] == cid)
        wo_no = olcum[kurgu]["work_order_no"]
        belge = next(d for d in musteri["documents"] if d["document_type"] == "service_fee" and d["document_no"] == f"{wo_no}-R3")
        assert D(belge["remaining"]) == D(f["totals"]["customer_amount"])


def test_12_mutasyon_ayni_tutarda_guncelleme_olmazsa_kirmizi(tmp_path):
    """Mutasyon: aynı tutarda fatura kesildiğinde UPDATE kaldırılırsa -R1 faturaya bağlanmaz."""
    bozuk_olcum = _kos(tmp_path, update_yok=True)
    for kurgu in ("A", "B"):
        sonra = bozuk_olcum[kurgu]["alacak_fatura_sonrasi"]
        # UPDATE düşürüldüğünde -R1 faturayı referanslamaz (hâlâ "computed" kalır)
        assert sonra[0]["source"] != "invoice" or sonra[0]["invoice_id"] == "", \
            f"Mutasyon başarısız: UPDATE yokken {kurgu} faturaya bağlanmış görünüyor"

