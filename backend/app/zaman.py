"""Yanıt gövdesindeki `*_at` damgalarını TEK bir tel biçimine indirger.

H73: iki modül aynı işe ihtiyaç duyduğu için buraya çıkarıldı —
`routers/despatch_notes.py` (#153, H66) ve `routers/invoices.py`. Ölçülen
sorun her ikisinde de aynıydı; aynı sütun iki lehçede üç ayrı dize veriyordu:

* SQLite ham `text()` okuması: METİN, boşluklu
  (`"2026-09-24 21:22:20.054785+00:00"`);
* PostgreSQL `TIMESTAMPTZ`: `datetime` döner ama OTURUM diliminde —
  FastAPI onu `"2026-09-25T00:22:25.411476+03:00"` diye yazar (ISO ama UTC
  DEĞİL);
* `str(datetime)`: boşluklu ve oturum dilimli (`"... 00:22:25+03:00"`).

Hepsi AYNI anı gösterir; `utc_iso` hepsini
`"2026-09-24T21:22:20.054785+00:00"` biçimine çeker. Duyarlılık KIRPILMAZ.

KAPSAM DIŞI (bilerek): `cost_rates._utc` bir sürüm KARŞILAŞTIRMASI için
`datetime` döndürür, metin değil; `activity_log._iso_utc` `Z` sonekiyle yazar
ve kendi sözleşmesi (frontend ActivityLog) vardır. İkisi de burada birleşmez.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc(deger: Any) -> datetime | None:
    """SQLite naive döndürür (yazarken UTC'ye normalleştirilmişti), PG oturum
    diliminde döndürür; ikisi de UTC'ye çekilir."""
    if deger is None:
        return None
    if not isinstance(deger, datetime):
        deger = datetime.fromisoformat(str(deger).replace("Z", "+00:00"))
    if deger.tzinfo is None:
        deger = deger.replace(tzinfo=timezone.utc)
    return deger.astimezone(timezone.utc)


def utc_iso(deger: Any) -> str | None:
    """`utc(deger).isoformat()`; `None` -> `None`."""
    an = utc(deger)
    return an.isoformat() if an else None


def zamanlari_iso(satir: dict) -> dict:
    """Satırın ÜST DÜZEY `*_at` alanlarını `utc_iso`dan geçirir (yeni sözlük).

    İç içe JSON'a (ör. faturanın `work_order` anlık görüntüsü) DOKUNMAZ: o,
    belge kesildiği anda donmuş bir içeriktir, sütun değil.
    """
    return {
        ad: (utc_iso(deger) if ad.endswith("_at") and deger is not None else deger)
        for ad, deger in satir.items()
    }
