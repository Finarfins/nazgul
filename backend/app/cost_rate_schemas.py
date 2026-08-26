"""Gerçek Maliyet V1 — FAZ 1: saatlik maliyet oranı şemaları.

Konu: mobil-erp#24.

İKİ KURAL ŞEMA SEVİYESİNDE:

1. **Oran SIFIR olabilir, negatif olamaz.** Kendi arazisinde kendi traktörüyle
   çalışan bir işletme "makine maliyeti 0" demek isteyebilir ve bu geçerli bir
   cevaptır. Negatif oran ise her zaman veri hatasıdır ve toplam maliyeti
   sessizce düşürürdü.

2. **Bir oran satırı YA makineye YA kullanıcıya bağlanır, ya da hiçbirine.**
   İkisi birden verilirse hangi orana bakılacağı belirsizleşir; şema burada
   reddediyor, veritabanı da CHECK ile son savunmayı yapıyor.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Tutar üst sınırı — para sütunu NUMERIC(18,2).
MAX_ORAN = Decimal("9999999999999999.99")

KIND = frozenset({"LABOR", "MACHINE"})
LIFECYCLE = frozenset({"ACTIVE", "ARCHIVED"})


def _metin(value: str) -> str:
    temiz = " ".join(value.split())
    if not temiz:
        raise ValueError("Boş bırakılamaz")
    return temiz


class _Taban(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CostRateWrite(_Taban):
    kind: str
    label: str = Field(min_length=1, max_length=180)
    # ORAN SIFIR OLABİLİR: `gt=0` DEĞİL `ge=0`. Bkz. modül başlığı.
    hourly_rate: Decimal = Field(ge=0, le=MAX_ORAN)
    machine_id: int | None = Field(default=None, gt=0)
    user_id: int | None = Field(default=None, gt=0)
    notes: str | None = None

    @field_validator("kind")
    @classmethod
    def tur(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in KIND:
            raise ValueError("Geçersiz maliyet türü")
        return v

    @field_validator("label")
    @classmethod
    def ad(cls, value: str) -> str:
        return _metin(value)

    @model_validator(mode="after")
    def hedef_tutarli(self) -> CostRateWrite:
        """Hedef tek olmalı ve türle tutarlı olmalı.

        Veritabanında da CHECK var; buradaki kontrol kullanıcıya ham bir kısıt
        hatası yerine ne yaptığını söyleyen bir mesaj veriyor.
        """
        if self.machine_id is not None and self.user_id is not None:
            raise ValueError(
                "Bir oran hem makineye hem kişiye bağlanamaz; hangisinin "
                "geçerli olduğu belirsiz kalır"
            )
        if self.kind == "MACHINE" and self.user_id is not None:
            raise ValueError("Makine oranı bir kişiye bağlanamaz")
        if self.kind == "LABOR" and self.machine_id is not None:
            raise ValueError("İşçilik oranı bir makineye bağlanamaz")
        return self


class CostRateUpdate(CostRateWrite):
    # İyimser kilit: oran ofiste iki kişi tarafından düzenlenebiliyor ve son
    # yazan kazanır kabul edilemez — oran, geçmiş maliyetin dayanağı.
    expected_updated_at: datetime
    status: str = "ACTIVE"

    @field_validator("status")
    @classmethod
    def durum(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in LIFECYCLE:
            raise ValueError("Geçersiz durum")
        return v
