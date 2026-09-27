"""Hesap planı eşlemesi — Tek Düzen varsayılanları KODDA, firma istisnaları TABLODA.

Keşif §4.2: `muhasebe_hesap_eslemeleri` BOŞSA her olay varsayılan koduna
yazılır; tabloya tohum YAZILMAZ (göç 0093 başlığı). Firma yalnız DEĞİŞTİRMEK
istediği olayı yazar.

--- ÇÖZÜM SIRASI (en özelden en genele) -------------------------------------

    (olay, oran, taraf) → (olay, oran, *) → (olay, *, taraf) → (olay, *, *)
    → VARSAYILANLAR[olay]

Oran-özel eşlem (`391.20`) taraf-özelden ÖNCE gelir: KDV hesabı orana göre
açılır (K3), taraf ayrımı ise v1'de yalnız cari hesabında anlamlıdır.

--- ALT HESAP YOK (K3) -------------------------------------------------------

`120.01.<cari>` üretilmez. Fiş ana hesapla yazılır; cari adı açıklamada, VKN
fişin `cari_vkn` alanında taşınır (maskeye tabi).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Numeric, String, and_, bindparam, cast, func, insert, or_, select, update
from sqlalchemy.orm import Session

from .schema import OLAYLAR, TARAF_TIPLERI, muhasebe_hesap_eslemeleri

#: Tek Düzen varsayılanları (keşif §4.2). `SERVIS_GELIR` 600 (K3; 602 firma
#: seçimi). `POS` 108 DOĞRULANMADI — firmalar 102 alt hesabı da kullanır.
VARSAYILANLAR: dict[str, str] = {
    "SATIS_CARI": "120",
    "SATIS_GELIR": "600",
    "SATIS_KDV": "391",
    "SATIS_IADE": "610",
    "ALIS_CARI": "320",
    "ALIS_STOK": "153",
    "ALIS_KDV": "191",
    "ALIS_IADE": "153",
    "SERVIS_GELIR": "600",
    "MUSTAHSIL_STOPAJ": "360",
    "MUSTAHSIL_BAGKUR": "361",
    "KASA": "100",
    "BANKA": "102",
    "POS": "108",
    "ALINAN_CEK": "101",
    "VADE_FARKI_GELIR": "642",
}
if tuple(VARSAYILANLAR) != OLAYLAR:  # pragma: no cover - modül yüklenirken kapı
    raise RuntimeError("VARSAYILANLAR olay kümesiyle birebir olmalı")

#: `^\d{3}(\.\d{2})*$` — ASCII rakam: Python'un `\d`si Unicode rakamı da
#: (`٣`) kabul ederdi ve müşavirin programı onu okuyamazdı.
HESAP_KODU_DESENI = r"^[0-9]{3}(\.[0-9]{2})*$"
_HESAP_KODU = re.compile(HESAP_KODU_DESENI)

ORAN_KUANTUMU = Decimal("0.01")


def oran_normalize(oran: object) -> Decimal | None:
    """Oranın TEK biçimi: iki ondalık (`20.00`). `None` = orandan bağımsız."""
    if oran is None:
        return None
    return Decimal(str(oran)).quantize(ORAN_KUANTUMU)


def hesap_kodu_gecerli(kod: str) -> bool:
    return bool(_HESAP_KODU.fullmatch(kod or ""))


@dataclass(frozen=True)
class Esleme:
    olay: str
    kdv_orani: Decimal | None
    taraf_tipi: str | None
    hesap_kodu: str


class HesapPlani:
    """Bir firmanın çözülmüş hesap planı; SAF (SQL'i `plan_oku` yapar)."""

    def __init__(self, eslemeler: list[Esleme] | tuple[Esleme, ...] = ()) -> None:
        self._harita: dict[tuple, str] = {}
        for e in eslemeler:
            self._harita[(e.olay, oran_normalize(e.kdv_orani), e.taraf_tipi)] = e.hesap_kodu

    def hesap(self, olay: str, oran: object = None, taraf: str | None = None) -> str:
        if olay not in VARSAYILANLAR:
            raise ValueError(f"Bilinmeyen muhasebe olayı: {olay}")
        o = oran_normalize(oran)
        for anahtar in ((olay, o, taraf), (olay, o, None), (olay, None, taraf), (olay, None, None)):
            kod = self._harita.get(anahtar)
            if kod is not None:
                return kod
        return VARSAYILANLAR[olay]


def eslemeleri_oku(db: Session, cid: int) -> list[dict]:
    satirlar = db.execute(
        select(muhasebe_hesap_eslemeleri.c.olay, muhasebe_hesap_eslemeleri.c.kdv_orani, muhasebe_hesap_eslemeleri.c.taraf_tipi, muhasebe_hesap_eslemeleri.c.hesap_kodu, muhasebe_hesap_eslemeleri.c.updated_at)
        .where(muhasebe_hesap_eslemeleri.c.company_id == cid)
        .order_by(muhasebe_hesap_eslemeleri.c.olay, muhasebe_hesap_eslemeleri.c.kdv_orani, muhasebe_hesap_eslemeleri.c.taraf_tipi)
    ).mappings().all()
    return [dict(s) for s in satirlar]


def plan_oku(db: Session, cid: int) -> HesapPlani:
    return HesapPlani(
        [
            Esleme(s["olay"], s["kdv_orani"], s["taraf_tipi"], s["hesap_kodu"])
            for s in eslemeleri_oku(db, cid)
        ]
    )


def esleme_yaz(
    db: Session,
    cid: int,
    olay: str,
    kdv_orani: object,
    taraf_tipi: str | None,
    hesap_kodu: str,
) -> tuple[str | None, str]:
    """Tek eşlemi yazar (varsa günceller). Döner: (önceki kod, yeni kod).

    Arama anahtarı iki kısmi tekilin (göç 0093) BİREBİR aynısıdır: `kdv_orani`
    NULL'u NULL'la, `taraf_tipi` `COALESCE(…,'')` ile eşleşir. Yarışta ikinci
    INSERT tekile çarpar; çağıran `IntegrityError`ı 409'a çevirir.
    """
    if olay not in OLAYLAR:
        raise ValueError(f"Bilinmeyen muhasebe olayı: {olay}")
    if taraf_tipi is not None and taraf_tipi not in TARAF_TIPLERI:
        raise ValueError(f"Bilinmeyen taraf tipi: {taraf_tipi}")
    if not hesap_kodu_gecerli(hesap_kodu):
        raise ValueError(f"Geçersiz hesap kodu: {hesap_kodu}")
    oran = oran_normalize(kdv_orani)
    # CAST ŞART: psycopg3 `Numeric` bağına `::NUMERIC` YAZMAZ ve PG `$n IS NULL`
    # için tip çıkaramaz (`AmbiguousParameter`, PG ikizinde ölçüldü; SQLite'ta
    # görünmez). `String` bağı `::VARCHAR` alır, ona gerek yok.
    p_oran = cast(bindparam("p_oran", oran, type_=Numeric(9, 4)), Numeric(9, 4))
    p_taraf = bindparam("p_taraf", taraf_tipi, type_=String)
    mevcut = db.execute(
        select(muhasebe_hesap_eslemeleri.c.id, muhasebe_hesap_eslemeleri.c.hesap_kodu).where(
            muhasebe_hesap_eslemeleri.c.company_id == cid,
            muhasebe_hesap_eslemeleri.c.olay == olay,
            or_(and_(p_oran.is_(None), muhasebe_hesap_eslemeleri.c.kdv_orani.is_(None)), muhasebe_hesap_eslemeleri.c.kdv_orani == p_oran),
            func.coalesce(muhasebe_hesap_eslemeleri.c.taraf_tipi, "") == func.coalesce(p_taraf, ""),
        )
    ).mappings().first()
    an = datetime.now(timezone.utc)
    if mevcut is None:
        db.execute(
            insert(muhasebe_hesap_eslemeleri).values(
                company_id=cid,
                olay=olay,
                kdv_orani=oran,
                taraf_tipi=taraf_tipi,
                hesap_kodu=hesap_kodu,
                updated_at=an,
            )
        )
        return None, hesap_kodu
    if mevcut["hesap_kodu"] != hesap_kodu:
        db.execute(
            update(muhasebe_hesap_eslemeleri)
            .where(muhasebe_hesap_eslemeleri.c.company_id == cid, muhasebe_hesap_eslemeleri.c.id == mevcut["id"])
            .values(hesap_kodu=hesap_kodu, updated_at=an)
        )
    return mevcut["hesap_kodu"], hesap_kodu
