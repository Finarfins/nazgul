"""Müstahsil makbuzu — istek/yanıt şemaları (D1).

İKİ KURAL ŞEMA SEVİYESİNDE ZORLANIYOR:

1. **Tutarlar istemciden ALINMAZ.** Yazma şemalarında `line_gross`,
   `withholding_amount`, `social_security_amount`, `line_net` ve başlığın
   dört toplamı YOKTUR. Sunucu hepsini `app/mustahsil.py`in saf
   fonksiyonlarıyla türetir. Alan burada olsaydı bir istemci hatası (ya da
   kötü niyet) stopajı sessizce sıfırlayabilirdi ve makbuz vergi dairesine
   yanlış giderdi. Aynı duruş `farm_schemas.py`in `total_cost` kuralıdır.

2. **Oranlar ZORUNLUDUR ve varsayılanları YOKTUR.** `withholding_rate` ile
   `social_security_rate` `Field(...)` ile zorunlu. Varsayılan bir oran
   koymak (0 dahil) kodda yasal bir sabit tutmak demekti; göç 0070'in
   başlığı bunu reddetti. Kullanıcı kesinti istemiyorsa 0'ı KENDİ yazar —
   ve o zaman "kesinti yoktu" bir KARAR olarak kayda geçer, bir varsayılanın
   sessiz sonucu olarak değil.

`Decimal` alanlar `str`den ayrıştırılır (`float` KABUL EDİLMEZ): ikili
kayan nokta bir para tutarına ya da orana giremez.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Ölçeklerin üst sınırları — göç 0070'in sütunlarına sığmalı.
MAX_MIKTAR = Decimal("99999999999999.9999")
MAX_TUTAR = Decimal("9999999999999999.99")

RECEIPT_STATUSES = frozenset({"draft", "issued", "cancelled"})


def _sonlu(value: Decimal, alan: str) -> Decimal:
    """NaN/sonsuzluk kapısı — `ge=`/`le=` KARŞILAŞTIRMALARINDAN önce.

    Pydantic'in `ge=0` kısıtı NaN'ı reddeder ama YANLIŞ sebeple ("0'dan
    küçük" der, oysa NaN karşılaştırılamazdır) ve `Decimal("Infinity")`
    `le=` üst sınırıyla yakalansa bile hata metni olguyu SÖYLEMEZ. Kapı
    burada, adı konmuş hâliyle duruyor.
    """
    if not value.is_finite():
        raise ValueError(
            f"{alan} SONLU bir sayı olmalıdır; NaN ve sonsuzluk bir makbuza "
            "giremez."
        )
    return value


class _Taban(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProducerReceiptItemWrite(_Taban):
    """Bir makbuz kalemi. TÜREV TUTAR ALANI YOKTUR (bkz. başlık, kural 1)."""

    product_id: int | None = None
    description: str | None = Field(default=None, max_length=300)
    entered_quantity: Decimal = Field(..., gt=0, le=MAX_MIKTAR)
    entered_unit: str = Field(..., min_length=1, max_length=40)
    unit_price: Decimal = Field(..., ge=0, le=MAX_TUTAR)
    # ZORUNLU, VARSAYILANSIZ — bkz. başlık, kural 2.
    withholding_rate: Decimal = Field(..., ge=0, le=100)
    social_security_rate: Decimal = Field(..., ge=0, le=100)
    # Kullanıcının fişin önerisini EZMESİ. Verilmezse fişten türetilen net
    # kullanılır; verilirse İKİSİ DE saklanır (`ticket_net_snapshot`).
    base_quantity_override: Decimal | None = Field(
        default=None, gt=0, le=MAX_MIKTAR
    )

    @field_validator(
        "entered_quantity",
        "unit_price",
        "withholding_rate",
        "social_security_rate",
        "base_quantity_override",
        mode="after",
    )
    @classmethod
    def _sonluluk(cls, value: Decimal | None, info) -> Decimal | None:
        if value is None:
            return None
        return _sonlu(value, info.field_name)


class ProducerReceiptWrite(_Taban):
    """Yeni makbuz. HER ZAMAN `draft` doğar; numara `issue` ile gelir."""

    supplier_id: int
    purchase_id: int | None = None
    ticket_id: int | None = None
    note: str | None = Field(default=None, max_length=2000)
    items: list[ProducerReceiptItemWrite] = Field(default_factory=list)


class ProducerReceiptItemView(_Taban):
    id: int
    product_id: int | None
    description: str | None
    # Miktarlar ve tutarlar SABİT ÖLÇEKLİ METİN döner: JSON `number`a
    # çevirmek onları ikili kayan noktadan geçirirdi.
    entered_quantity: str
    entered_unit: str
    entered_factor: str
    base_quantity: str
    ticket_net_snapshot: str | None
    unit_price: str
    line_gross: str
    withholding_rate: str
    withholding_amount: str
    social_security_rate: str
    social_security_amount: str
    line_net: str


class ProducerReceiptView(_Taban):
    id: int
    supplier_id: int
    purchase_id: int | None
    ticket_id: int | None
    receipt_no: str | None
    issued_at: datetime | None
    gross_amount: str
    withholding_total: str
    social_security_total: str
    net_payable: str
    status: str
    note: str | None
    items: list[ProducerReceiptItemView]
