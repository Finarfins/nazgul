"""H97 sıfır tutarlı fatura senaryosu — SQLite testi ve PG ikizi ORTAK.

#162/#165 belge iskontosu matrahı AŞARSA 422 `ISKONTO_TOPLAMI_ASIYOR`
veriyordu; matraha EŞİT iskonto (%100 ya da FIXED == iskontosuz matrah)
201 ile sıfır tutarlı fatura kesiyordu. Şef kararı (H97): eşit de reddedilir,
aynı kod ve aynı gövdeyle. Harman Zamanı sıfır tutarlı fatura kesmez; bedelsiz
servis bu dilimin kapsamı dışında.

Tek yer `billing_service.price_service_lines`; önizleme ve fatura onu çağırır,
bu senaryo ikisini de HTTP'den ölçer:

* **A — yalnız işçilik** (`h91_onizleme_senaryo._yalniz_iscilik`): 2 × 100,
  %20, garanti YOK → matrah 200.00.
* **B — F9-5 kurgusu** (`tamamlanmis_is_emri`): üç satır, %30 garanti →
  matrah 501.00.

Alt süreç betiğinde SQL YOK; bu modülde de YOK.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from tests.f9_5_servis_kdv_senaryo import D, dunya, oturum, tamamlanmis_is_emri
from tests.h91_onizleme_senaryo import _yalniz_iscilik

__all__ = ["ESIT", "D", "olc", "oturum", "sinirin_alti_beklenen"]

#: (iş emri kurgusu, ad, tür, değer, matrah) — iskonto matraha TAM eşit.
ESIT: tuple[tuple[str, str, str, str, str], ...] = (
    ("A", "A_yuzde100", "PERCENT", "100", "200.00"),
    ("A", "A_sabit200", "FIXED", "200", "200.00"),
    ("B", "B_yuzde100", "PERCENT", "100", "501.00"),
    ("B", "B_sabit501", "FIXED", "501.00", "501.00"),
)

#: A'da matrahın bir kuruş altı: kalan matrah 0.01.
SINIRIN_ALTI = {"global_discount_type": "FIXED", "global_discount_value": "199.99"}


def sinirin_alti_beklenen() -> dict[str, Decimal]:
    """0.01 matrah, %20 işçilik KDV'si — Decimal ile, kuruş ROUND_HALF_UP."""
    kurus = Decimal("0.01")
    matrah = Decimal("200.00") - Decimal("199.99")
    kdv = (matrah * Decimal("20") / Decimal("100")).quantize(kurus, rounding=ROUND_HALF_UP)
    return {"matrah": matrah, "tax": kdv, "grand_total": (matrah + kdv).quantize(kurus, rounding=ROUND_HALF_UP)}


def _yanit(r) -> dict[str, Any]:
    return {"status": r.status_code, "body": r.json()}


def olc(c, h: dict, ek: str) -> dict:
    """Eşit iskontoyu önizlemede VE faturada dene; sonra sınırın altını kes."""
    w = dunya(c, h, ek)
    is_emri = {"A": _yalniz_iscilik(c, w), "B": tamamlanmis_is_emri(c, w)}
    out: dict[str, Any] = {"onizleme": {}, "fatura": {}}
    for kurgu, ad, tur, deger, _matrah in ESIT:
        iskonto = {"global_discount_type": tur, "global_discount_value": deger}
        out["onizleme"][ad] = _yanit(c.get(f"/api/work-orders/{is_emri[kurgu]}/invoice", headers=w["h"], params=iskonto))
        out["fatura"][ad] = _yanit(c.post("/api/invoices/generate", headers=w["h"],
                                          json={"work_order_id": is_emri[kurgu], **iskonto}))
    # Reddedilen A iş emri yarım iz bırakmadıysa matrahın bir kuruş altıyla kesilir.
    out["alti_onizleme"] = _yanit(c.get(f"/api/work-orders/{is_emri['A']}/invoice", headers=w["h"], params=SINIRIN_ALTI))
    out["alti_fatura"] = _yanit(c.post("/api/invoices/generate", headers=w["h"],
                                       json={"work_order_id": is_emri["A"], **SINIRIN_ALTI}))
    return out
