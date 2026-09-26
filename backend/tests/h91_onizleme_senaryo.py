"""H91 önizleme == fatura senaryosu — SQLite testi ve PG ikizi ORTAK.

F9-5-fix (#162) faturada işçiliğe `SERVICE_LABOR_VAT_RATE` ekledi ama
faturalama ÖNİZLEMESİ (`GET /api/work-orders/{id}/invoice`) ve COMPLETED'da
doğan servis alacağı işçiliği %0 KDV'yle fiyatlamayı sürdürdü: müşteri
önizlemede 200, faturada 240 gördü. H91 ikisini TEK fonksiyona
(`billing_service.price_service_lines`) bağladı; bu senaryo aynı iş emrinin
önizlemesini (fatura kesilmeden ÖNCE) ve faturasını yan yana ölçer.

Kurgular (elle türetilmiş beklenenler `BEKLENEN`de):

* **A — yalnız işçilik:** 2 saat × 100, garanti YOK.
* **B — işçilik + parça:** F9-5 kurgusu (`tests/f9_5_servis_kdv_senaryo.py`):
  2 × 150 işçilik, P1 2 × 100 %10 satır iskontosu %20 KDV, P2 3 × 7 %10 KDV,
  %30 kısmi garanti.
* **C — B + %10 belge iskontosu.**
* **D — B, COMPLETED ile fatura arasında P3 satırı eklenir** (1 × 7, %10)
  (`POST /parts`; COMPLETED terminal DEĞİL — yalnız DELIVERED/CANCELLED,
  `TERMINAL_WORK_ORDER_STATES`). Parça ekleme alacağı uzlaştırmaz; fatura
  kesilince alacak faturaya göre REVİZE edilmeli.

Her şey HTTP'den; yalnız alacak belgelerini okumak için SQL kullanılır (bu
modülde, alt süreç betiğinde SQL YOK).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from tests.f9_5_servis_kdv_senaryo import D, dunya, oturum, tamamlanmis_is_emri

__all__ = ["BEKLENEN", "D", "dunya", "hatalar", "olc", "oturum"]

#: Elle türetilmiş rakamlar (kuruş, ROUND_HALF_UP; satır başına yuvarlama).
BEKLENEN: dict[str, dict[str, str]] = {
    # 2 × 100 = 200 matrah; KDV %20 = 40.00; toplam 240.00; garanti yok.
    "A": {"labor": "200.00", "labor_tax": "40.00", "tax": "40.00", "grand_total": "240.00",
          "customer_amount": "240.00", "warranty_amount": "0.00", "global_discount_base": "0.00"},
    # İşçilik 300 + 60 = 360.00; P1 200 − 20 = 180 + 36 = 216.00; P2 21 + 2.10
    # = 23.10. Toplam 599.10, KDV 60 + 36 + 2.10 = 98.10. Garanti %30 kalem
    # başına: 108.00 + 64.80 + 6.93 = 179.73; müşteri 599.10 − 179.73 = 419.37.
    "B": {"labor": "300.00", "labor_tax": "60.00", "tax": "98.10", "grand_total": "599.10",
          "customer_amount": "419.37", "warranty_amount": "179.73", "global_discount_base": "0.00"},
    # Matrah 300 + 180 + 21 = 501; %10 = 50.10 → paylar 30.00 / 18.00 / 2.10.
    # İşçilik 270 + 54.00 = 324.00; P1 162 + 32.40 = 194.40; P2 18.90 + 1.89
    # = 20.79. Toplam 539.19, KDV 88.29. Garanti: 97.20 + 58.32 + 6.24
    # (20.79 × 0.3 = 6.237) = 161.76; müşteri 539.19 − 161.76 = 377.43.
    "C": {"labor": "300.00", "labor_tax": "54.00", "tax": "88.29", "grand_total": "539.19",
          "customer_amount": "377.43", "warranty_amount": "161.76", "global_discount_base": "50.10"},
    # D: tamamlanınca B (419.37 alacak). Ek P3 satırı 1 × 7 = 7.00 + %10 =
    # 7.70; toplam 599.10 + 7.70 = 606.80, KDV 98.10 + 0.70 = 98.80; garanti
    # 179.73 + 2.31 = 182.04; müşteri 606.80 − 182.04 = 424.76.
    "D": {"labor": "300.00", "labor_tax": "60.00", "tax": "98.80", "grand_total": "606.80",
          "customer_amount": "424.76", "warranty_amount": "182.04", "global_discount_base": "0.00"},
}

ISKONTO_C = {"global_discount_type": "PERCENT", "global_discount_value": "10"}


def _yalniz_iscilik(c, w: dict) -> int:
    h = w["h"]
    wo = c.post("/api/work-orders", headers=h, json={
        "machine_id": w["makine"]["id"], "customer_id": w["musteri"]["id"], "technician_id": w["uid"],
        "actual_hours": "2", "labor_rate": "100", "warranty_type": "NONE", "warranty_percent": "0",
    }).json()
    for durum in ("IN_PROGRESS", "COMPLETED"):
        r = c.patch(f"/api/work-orders/{wo['id']}/status", headers=h, json={"status": durum})
        assert r.status_code == 200, r.text
    return int(wo["id"])


def _alacak_belgeleri(cid: int, is_emri: int) -> list[dict[str, str]]:
    from sqlalchemy import text

    from app.db import SessionLocal

    with SessionLocal() as db:
        satirlar = db.execute(text(
            """SELECT revision_no,status,gross_amount,reversal_of_document_id
            FROM receivable_charge_documents
            WHERE company_id=:cid AND work_order_id=:wid AND charge_type='service_fee'
            ORDER BY revision_no"""), {"cid": cid, "wid": is_emri}).mappings().all()
    return [{"revision_no": str(s["revision_no"]), "status": str(s["status"]),
             "gross_amount": format(D(s["gross_amount"]).quantize(Decimal("0.01")), "f"),
             "reversal": "1" if s["reversal_of_document_id"] is not None else "0"} for s in satirlar]


def _tamamlanmisa_parca_ekle(c, w: dict, is_emri: int) -> None:
    """D kurgusu: COMPLETED iş emrine API'den P3 satırı (1 × 7, %10).

    Aynı ürün+depo bir iş emrinde bir kez kayıtlı olabilir; P3 ayrı üründür.
    """
    p3 = c.post("/api/products", headers=w["h"], json={
        "name": "H91 P3", "purchase_price": "1", "sale_price": "7", "vat_rate": "10", "stock": "50", "unit": "Adet",
    }).json()
    r = c.post(f"/api/work-orders/{is_emri}/parts", headers=w["h"], json={
        "product_id": p3["id"], "warehouse_id": w["depo"]["id"], "quantity": "1",
        "unit_price": "7", "discount": "0", "tax_rate": "10",
    })
    assert r.status_code in (200, 201), r.text


def _onizleme(c, w: dict, is_emri: int, iskonto: dict | None = None) -> Any:
    return c.get(f"/api/work-orders/{is_emri}/invoice", headers=w["h"], params=iskonto or {})


def _kurgu(c, w: dict, cid: int, is_emri: int, iskonto: dict | None = None, *, once=None) -> dict:
    """Önizleme → (isteğe bağlı değişiklik) → fatura; alacak zinciri iki anda."""
    tamamlaninca = _alacak_belgeleri(cid, is_emri)
    if once is not None:
        once()
    r = _onizleme(c, w, is_emri, iskonto)
    assert r.status_code == 200, r.text
    onizleme = r.json()
    fatura = c.post("/api/invoices/generate", headers=w["h"], json={"work_order_id": is_emri, **(iskonto or {})})
    assert fatura.status_code == 201, fatura.text
    return {"onizleme": onizleme, "fatura": fatura.json(),
            "alacak_tamamlaninca": tamamlaninca, "alacak_fatura_sonrasi": _alacak_belgeleri(cid, is_emri)}


def olc(c, h: dict, ek: str) -> dict:
    """Dört kurguyu ölç; JSON'a dökülebilir sözlük döner."""
    w = dunya(c, h, ek)
    cid = int(w["h"]["X-Company-ID"])
    out: dict[str, Any] = {
        "A": _kurgu(c, w, cid, _yalniz_iscilik(c, w)),
        "B": _kurgu(c, w, cid, tamamlanmis_is_emri(c, w)),
        "C": _kurgu(c, w, cid, tamamlanmis_is_emri(c, w), ISKONTO_C),
    }
    d = tamamlanmis_is_emri(c, w)
    out["D"] = _kurgu(c, w, cid, d, once=lambda: _tamamlanmisa_parca_ekle(c, w, d))
    # Matrahı (501.00) aşan belge iskontosu önizlemede de faturadaki 422'dir.
    asan = tamamlanmis_is_emri(c, w)
    r = _onizleme(c, w, asan, {"global_discount_type": "FIXED", "global_discount_value": "550"})
    out["asan_onizleme"] = {"status": r.status_code, "body": r.json()}
    return out


def _para(value: Any) -> str:
    return format(D(value).quantize(Decimal("0.01")), "f")


def hatalar(out: dict) -> list[str]:
    """Elle türetilmiş beklenenlere göre sapmalar — boş liste = yeşil.

    Her sapma ``<kurgu>:<taraf>:<alan>`` ile başlar (taraf: onizleme, fatura,
    alacak); mutasyon testi hangi tarafın kırmızıya döndüğünü buradan okur.
    """
    sonuc: list[str] = []

    def bak(etiket: str, simdi: Any, beklenen: str) -> None:
        if _para(simdi) != beklenen:
            sonuc.append(f"{etiket}: {_para(simdi)} != {beklenen}")

    for ad, bek in BEKLENEN.items():
        o, f = out[ad]["onizleme"], out[ad]["fatura"]
        ot, ft = o["totals"], f["totals"]
        for alan in ("labor", "labor_tax", "tax", "grand_total", "global_discount_base"):
            bak(f"{ad}:onizleme:{alan}", ot[alan], bek[alan])
        bak(f"{ad}:onizleme:customer_amount", o["warranty"]["customer_amount"], bek["customer_amount"])
        bak(f"{ad}:onizleme:warranty_amount", o["warranty"]["warranty_amount"], bek["warranty_amount"])
        for alan in ("labor", "tax", "grand_total", "global_discount_base", "customer_amount", "warranty_amount"):
            bak(f"{ad}:fatura:{alan}", ft[alan], bek[alan])
        isc = sum((D(k["tax_amount"]) for k in f["items"] if k["item_type"] == "LABOR"), Decimal("0"))
        bak(f"{ad}:fatura:labor_tax", isc, bek["labor_tax"])
        # Asıl H91 iddiası: aynı iş emri, aynı iskonto → önizleme == fatura.
        for alan in ("grand_total", "tax", "global_discount", "global_discount_base"):
            if _para(ot[alan]) != _para(ft[alan]):
                sonuc.append(f"{ad}:esitlik:{alan}: onizleme {_para(ot[alan])} != fatura {_para(ft[alan])}")
        # Alacak, faturanın müşteri payına eşit (TRY, kur 1).
        aktif = [b for b in out[ad]["alacak_fatura_sonrasi"] if b["status"] == "posted" and b["reversal"] == "0"]
        if len(aktif) != 1:
            sonuc.append(f"{ad}:alacak:aktif belge sayısı {len(aktif)}")
        else:
            bak(f"{ad}:alacak:fatura_sonrasi", aktif[0]["gross_amount"], bek["customer_amount"])
    return sonuc
