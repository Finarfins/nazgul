"""F10-4a — `GET /api/lots/{lot_id}/recall-preview` CEVAP ŞEMASI.

`lot_izi_ozet.ozetle`nin döndürdüğü sözlüğün BİREBİR aynası; alan eklemez,
alan düşürmez. `extra="forbid"`: `ozetle` bir anahtar ekleyip buraya
yazılmazsa FastAPI onu SESSİZCE süzmez, cevap doğrulaması patlar ve
davranış testi kırmızıya döner.

Boşluk yazımı kaynağından gelir: veritabanında NULL olabilen sütun
(`document_no`, `stock_movements.reference_type`/`reference_id`) ya da
`dict.get` ile okunan değer (`name`, `warehouse_name`, `product_name`) ve
`tarih_metni` çıktısı `| None`dır; NOT NULL sütun doğrudan geçiyorsa
zorunludur.

Miktarlar `Decimal`dır ve JSON'da METİN olarak gider (API'nin geri kalanı
gibi); `types.gen.ts` onları `string` yazar.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

Kanal = Literal["orders", "delivery_notes"]


class _Kati(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LotOzeti(_Kati):
    lot_id: int
    product_id: int
    product_name: str | None
    unit: str | None
    lot_code: str
    expiry_date: str | None


class KardesLot(_Kati):
    lot_id: int
    warehouse_id: int
    warehouse_name: str | None
    quantity: Decimal


class AliciBelgesi(_Kati):
    source: Kanal
    document_id: int
    document_no: str | None
    date: str | None
    quantity_out: Decimal
    quantity_returned: Decimal


class Alici(_Kati):
    customer_id: int
    name: str | None
    #: `maskele_cari` çıktısı: maskeli rolde maskeli metin, telefonsuzda None.
    phone: str | None
    quantity_out: Decimal
    quantity_returned: Decimal
    quantity_net: Decimal
    first_date: str | None
    last_date: str | None
    channels: list[Kanal]
    documents: list[AliciBelgesi]
    has_phone: bool
    has_consent: bool
    consent_reason: str | None
    status: Literal["notifiable", "manual_pending"]


class PerakendeKovasi(_Kati):
    quantity_out: Decimal
    quantity_returned: Decimal
    quantity_net: Decimal
    document_count: int


class BoslukOrnegi(_Kati):
    """H117 — boşluğun arkasındaki TEK hareket. Cari yalnız KİMLİKLE; ad ve
    telefon `customers[]`in (maskeli) işidir."""

    reference_type: str | None
    reference_id: int | None
    document_no: str | None
    customer_id: int | None
    date: str | None
    quantity: Decimal


class Bosluk(_Kati):
    code: Literal["kaynaksiz_iade", "tarla_hareketi", "partisiz_transfer", "partisiz_hareket"]
    reference_type: str | None
    movement_type: str
    movement_count: int
    quantity: Decimal
    #: En çok 20, (tarih, id) sırasıyla.
    ornekler: list[BoslukOrnegi]
    #: `movement_count` örnek sayısından büyükse `True`.
    ornek_kesildi: bool


class Uyari(_Kati):
    """H116 — keşif §5.1 `uyarilar[]`. `warehouse_id` depo başına uyarıda
    dolu; `miktar` işaretlidir (mutabakatta stok − parti toplamı)."""

    kod: Literal["mutabakat_sapma", "kaynaksiz_iade", "defter_bosaldi"]
    mesaj: str
    warehouse_id: int | None
    miktar: Decimal | None


class DigerHareket(_Kati):
    reference_type: str | None
    reference_id: int | None
    movement_type: str
    quantity: Decimal
    date: str | None


class Denge(_Kati):
    received: Decimal
    customers_net: Decimal
    pos_retail_net: Decimal
    returned_to_supplier: Decimal
    other: Decimal
    on_hand: Decimal
    difference: Decimal


class GeriCagirmaOnizleme(_Kati):
    lot: LotOzeti
    siblings: list[KardesLot]
    customers: list[Alici]
    pos_retail: PerakendeKovasi
    gaps: list[Bosluk]
    other_movements: list[DigerHareket]
    balance: Denge
    uyarilar: list[Uyari]
