"""Canonical JSON response contracts for the POS surface.

KANONİK SERİLEŞTİRME KARARI (test-infrastructure hattı):
JSON gövdesinde para ve miktar alanları HER ZAMAN sabit ölçekli STRING'dir —
para 2 ondalık ("12.50"), miktar 4 ondalık ("6.0000"). Ham dict dönen uçlar
FastAPI'nin jsonable_encoder'ı yüzünden Decimal'i JSON SAYISINA çeviriyordu ve
SQLite (TEXT kolon → string) ile PostgreSQL (NUMERIC → sayı) farklı şekiller
üretiyordu; prod POS çökmesi (d.replace is not a function) tam bu tutarsızlıktan
çıktı. response_model + Decimal, pydantic v2'de string üretir; MoneyOut /
QuantityOut bunu ölçek garantisiyle sabitler.

Karar "sayı"ya dönerse tek çevirme noktası burasıdır: MoneyOut/QuantityOut
serializer'ını (ve pattern şemasını) değiştirmek yeterlidir — router'lara ve
frontend üretimine dokunulmaz.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, PlainSerializer, WithJsonSchema

from .money import money, quantity

MONEY_JSON_PATTERN = r"^-?\d+\.\d{2}$"
QUANTITY_JSON_PATTERN = r"^-?\d+\.\d{4}$"

# Eski kayıtlarda sale_price/stock NULL olabilir; ham dict yolu None'ı olduğu
# gibi geçiriyordu. response_model doğrulaması None'ı reddedip yeni bir 500
# sınıfı üretmesin: Decimal helper'larla aynı sözleşme (None -> 0) uygulanır.
_none_to_zero = BeforeValidator(lambda value: 0 if value is None else value)

MoneyOut = Annotated[
    Decimal,
    _none_to_zero,
    PlainSerializer(
        lambda value: format(money(value), "f"), return_type=str, when_used="json"
    ),
    WithJsonSchema({"type": "string", "pattern": MONEY_JSON_PATTERN}),
]

QuantityOut = Annotated[
    Decimal,
    _none_to_zero,
    PlainSerializer(
        lambda value: format(quantity(value), "f"), return_type=str, when_used="json"
    ),
    WithJsonSchema({"type": "string", "pattern": QUANTITY_JSON_PATTERN}),
]


class QuickPickItem(BaseModel):
    product_id: int
    product_name: str
    product_code: str | None
    unit_price: MoneyOut
    current_stock: QuantityOut
    times_sold: int
    total_quantity: QuantityOut


class PosLookupProduct(BaseModel):
    id: int
    name: str
    code: str | None
    unit_price: MoneyOut
    stock: QuantityOut
    warehouse_id: int


class PosSaleResult(BaseModel):
    sale_id: int
    customer_id: int | None
    customer_name: str | None
    subtotal: MoneyOut
    vat_total: MoneyOut
    grand_total: MoneyOut
    discount_amount: MoneyOut
    final_total: MoneyOut
    paid_amount: MoneyOut
    remaining_amount: MoneyOut
