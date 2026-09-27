"""H102 COMPLETED iş emrinde parça değişikliği → servis alacağı — SQLite testi ve PG ikizi ORTAK.

#165 (H91) merceği ölçtü: COMPLETED'dan SONRA `POST /work-orders/{id}/parts`
ile eklenen parça önizlemeyi değiştiriyor (419.37 → 424.76) ama servis alacağı
fatura kesilene kadar COMPLETED anındaki tutarda kalıyordu. #165'ten beri
alacak KDV dahil müşteri payı ve `reconcile_service_receivable` tutar
değişince ters kayıt + yeni belge atıyor; H102 parça ekle/güncelle/sil/iade
uçlarından, iş emri COMPLETED iken onu çağırır.

Kurgular (hepsi F9-5 `B` iş emri: tamamlanınca 419.37 alacak):

* **X — ekle, sıfır tutarlı ekle, fatura:** P3 (1 × 7, %10) → 424.76 (tam bir
  ters kayıt + bir yeni belge); 0 fiyatlı P4 → tutar aynı, YENİ SATIR YOK;
  fatura 424.76 → revizyon YOK, `-R3` faturaya bağlanır; faturadan sonra
  parça eklemek 409 (`ensure_work_order_unbilled`), alacak değişmez.
* **Y — ekle, güncelle, sil:** P3 → 424.76; P3 miktarı 2 → 430.15; P3 silinir
  → 419.37 (geri); fatura 419.37 → revizyon yok, son belge faturaya bağlanır.
* **Z — ekle, iade:** P3 → 424.76; P3 iade (RETURNED faturaya girmez) → 419.37.

Her adımda önizlemenin müşteri payı == aktif alacak. Her şey HTTP'den; yalnız
alacak belgelerini okumak için SQL kullanılır (bu modülde, alt süreç
betiğinde SQL YOK).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from tests.f9_5_servis_kdv_senaryo import D, dunya, oturum, tamamlanmis_is_emri
from tests.h91_onizleme_senaryo import _alacak_belgeleri

__all__ = ["BEKLENEN", "D", "aktif", "olc", "oturum", "satirlar"]

#: Elle türetilmiş müşteri payları (B kurgusu + P3 satırı; garanti %30 kalem başına).
BEKLENEN = {
    # 252.00 + 151.20 + 16.17
    "B": "419.37",
    # P3 1 × 7 = 7.00 + %10 = 7.70; garanti 2.31; müşteri +5.39
    "B+P3": "424.76",
    # P3 2 × 7 = 14.00 + %10 = 15.40; garanti 4.62; müşteri +10.78
    "B+2P3": "430.15",
}


def _p(c, w: dict, ad: str, fiyat: str) -> dict:
    return c.post("/api/products", headers=w["h"], json={
        "name": ad, "purchase_price": "1", "sale_price": fiyat, "vat_rate": "10", "stock": "50", "unit": "Adet",
    }).json()


def _govde(w: dict, urun: dict, miktar: str, fiyat: str) -> dict:
    return {"product_id": urun["id"], "warehouse_id": w["depo"]["id"], "quantity": miktar,
            "unit_price": fiyat, "discount": "0", "tax_rate": "10"}


def _ekle(c, w: dict, is_emri: int, urun: dict, miktar: str = "1", fiyat: str = "7") -> int:
    r = c.post(f"/api/work-orders/{is_emri}/parts", headers=w["h"], json=_govde(w, urun, miktar, fiyat))
    assert r.status_code in (200, 201), r.text
    return int(r.json()["id"])


def _durum(c, w: dict, cid: int, is_emri: int) -> dict:
    r = c.get(f"/api/work-orders/{is_emri}/invoice", headers=w["h"])
    assert r.status_code == 200, r.text
    return {
        "onizleme": format(D(r.json()["warranty"]["customer_amount"]).quantize(Decimal("0.01")), "f"),
        "alacak": _alacak_belgeleri(cid, is_emri),
    }


def _fatura(c, w: dict, is_emri: int) -> dict:
    r = c.post("/api/invoices/generate", headers=w["h"], json={"work_order_id": is_emri})
    assert r.status_code == 201, r.text
    return {"id": str(r.json()["id"]), "invoice_number": r.json()["invoice_number"]}


def olc(c, h: dict, ek: str) -> dict:
    """Üç kurguyu adım adım ölç; JSON'a dökülebilir sözlük döner."""
    w = dunya(c, h, f"h102-{ek}")
    cid = int(w["h"]["X-Company-ID"])
    out: dict[str, Any] = {}

    x = tamamlanmis_is_emri(c, w)
    p3 = _p(c, w, "H102 P3", "7")
    p4 = _p(c, w, "H102 P4 bedelsiz", "0")
    adim = {"tamamlaninca": _durum(c, w, cid, x)}
    _ekle(c, w, x, p3)
    adim["p3_eklenince"] = _durum(c, w, cid, x)
    _ekle(c, w, x, p4, fiyat="0")
    adim["sifir_tutar_eklenince"] = _durum(c, w, cid, x)
    adim["fatura"] = _fatura(c, w, x)
    adim["fatura_sonrasi"] = _durum(c, w, cid, x)
    p5 = _p(c, w, "H102 P5", "7")
    r = c.post(f"/api/work-orders/{x}/parts", headers=w["h"], json=_govde(w, p5, "1", "7"))
    adim["faturadan_sonra_ekle"] = {"status": r.status_code, **_durum(c, w, cid, x)}
    out["X"] = adim

    y = tamamlanmis_is_emri(c, w)
    adim = {"tamamlaninca": _durum(c, w, cid, y)}
    parca = _ekle(c, w, y, p3)
    adim["p3_eklenince"] = _durum(c, w, cid, y)
    r = c.put(f"/api/work-orders/{y}/parts/{parca}", headers=w["h"], json=_govde(w, p3, "2", "7"))
    assert r.status_code == 200, r.text
    adim["p3_guncellenince"] = _durum(c, w, cid, y)
    r = c.delete(f"/api/work-orders/{y}/parts/{parca}", headers=w["h"])
    assert r.status_code == 204, r.text
    adim["p3_silinince"] = _durum(c, w, cid, y)
    adim["fatura"] = _fatura(c, w, y)
    adim["fatura_sonrasi"] = _durum(c, w, cid, y)
    out["Y"] = adim

    z = tamamlanmis_is_emri(c, w)
    adim = {"tamamlaninca": _durum(c, w, cid, z)}
    parca = _ekle(c, w, z, p3)
    adim["p3_eklenince"] = _durum(c, w, cid, z)
    r = c.post(f"/api/work-orders/{z}/parts/{parca}/return", headers=w["h"])
    assert r.status_code == 200, r.text
    adim["p3_iade_edilince"] = _durum(c, w, cid, z)
    out["Z"] = adim
    return out


def satirlar(durum: dict) -> list[tuple[str, str, str, str]]:
    """Alacak zinciri (revizyon, durum, tutar, ters mi) — karşılaştırma görünümü."""
    return [(b["revision_no"], b["status"], b["gross_amount"], b["reversal"]) for b in durum["alacak"]]


def aktif(durum: dict) -> list[dict[str, str]]:
    return [b for b in durum["alacak"] if b["status"] == "posted" and b["reversal"] == "0"]
