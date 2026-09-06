"""Avans, makbuz ödemesi ve borsa tescili — istek şemaları (D2).

D1'in (`mustahsil_schemas.py`) İKİ KURALI BURADA DA GEÇERLİDİR ve aynı
sebeple:

1. **Türev tutarlar istemciden ALINMAZ.** Mahsup edilen avans toplamı
   (`advance_applied_total`), kalan avans (`remaining_amount`) ve vergi
   yükümlülüğünün tutarı hiçbir yazma şemasında YOKTUR — üçü de sunucuda
   türetilir. Alan burada olsaydı bir istemci "avansımdan 10.000 mahsup
   ettim" diyebilir ve çiftçiye ödenecek net sessizce sıfırlanabilirdi.

2. **`Decimal` alanlar `str`den ayrıştırılır, `float` KABUL EDİLMEZ.**
   İkili kayan nokta bir para tutarına giremez.

`_sonlu` kapısı D1'den İTHAL EDİLİYOR, KOPYALANMIYOR: NaN/sonsuzluk
reddinin ikinci bir kopyası, biri düzeltildiğinde ötekini SESSİZCE eski
hâlinde bırakırdı.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .mustahsil_schemas import MAX_TUTAR, _sonlu

# Yükümlülüğün CİNSİ kapalı bir kümedir; göç 0071'in CHECK'iyle AYNI iki
# değer. Makbuzun iki kesintisi dışında bir tür YOKTUR.
TAX_LIABILITY_KINDS = frozenset({"withholding", "social_security"})


class _Taban(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SupplierAdvanceWrite(_Taban):
    """Çiftçiye verilen avans. Bir `payments` satırı DOĞURUR.

    `amount` KESİN OLARAK POZİTİFTİR: sıfır tutarlı bir avans, kasadan
    hiçbir şey çıkmadan defterde bir satır bırakırdı; negatifi ise avansın
    TERSİ (bir tahsilat) olurdu ve o bu ucun işi DEĞİLDİR.
    """

    amount: Decimal = Field(..., gt=0, le=MAX_TUTAR)
    payment_method: str = Field(..., max_length=30)
    account_id: int | None = None
    payment_date: str = Field(..., min_length=1, max_length=30)
    note: str | None = None

    @field_validator("amount", mode="after")
    @classmethod
    def _amount_sonlu(cls, value: Decimal) -> Decimal:
        return _sonlu(value, "amount")


class ProducerReceiptPaymentWrite(_Taban):
    """Kesilmiş makbuzun NAKİT BORCUNA yapılan ödeme.

    Tutarın ÜST SINIRI şemada YOKTUR ve olamaz: sınır
    `net_payable − advance_applied_total − o ana kadar ödenen` olup
    VERİTABANINDAN okunur. Şemaya sabit bir tavan yazmak, sınırı iki yerde
    tutup birini eskitirdi.
    """

    amount: Decimal = Field(..., gt=0, le=MAX_TUTAR)
    payment_method: str = Field(..., max_length=30)
    account_id: int | None = None
    payment_date: str = Field(..., min_length=1, max_length=30)
    note: str | None = None

    @field_validator("amount", mode="after")
    @classmethod
    def _amount_sonlu(cls, value: Decimal) -> Decimal:
        return _sonlu(value, "amount")


class ExchangeRegistrationWrite(_Taban):
    """Makbuzun borsa tescili. Bir makbuz için EN FAZLA BİR kez.

    `fee_amount` SIFIR OLABİLİR (`ge=0`): tescil ücreti alınmayan hâller
    vardır ve sıfır ücreti YASAKLAMAK, ücretsiz tescili kaydedilemez
    yapardı. Yukarıdaki iki şemanın `gt=0` duruşundan BİLEREK ayrılıyor.

    `registered_on` bir `date`tir, `datetime` DEĞİL: borsa tescili GÜN
    hassasiyetinde bir olgudur ve saat uydurmak, saat dilimi taşımayan bir
    değere yerel saat ATFETMEK olurdu.
    """

    registration_no: str = Field(..., min_length=1, max_length=60)
    exchange_name: str = Field(..., min_length=1, max_length=120)
    registered_on: date
    fee_amount: Decimal = Field(default=Decimal("0"), ge=0, le=MAX_TUTAR)
    note: str | None = None

    @field_validator("fee_amount", mode="after")
    @classmethod
    def _fee_sonlu(cls, value: Decimal) -> Decimal:
        return _sonlu(value, "fee_amount")
