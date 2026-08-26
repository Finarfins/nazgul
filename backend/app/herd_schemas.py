"""Hayvancılık V1 — istek/yanıt şemaları (mobil-erp#17, FAZ 2).

ÜÇ KURAL ŞEMA SEVİYESİNDE:

1. **Küpe numarası ENGELLENMEZ, UYARILIR.** Biçim kontrolü burada bir
   `ValueError` fırlatmıyor; uç, kaydı kabul edip yanıtta `warnings` döndürüyor.
   Sebebi konu #17'de: küpe standardı şu an geçişte (28.06.2026 yönetmelik
   değişikliği, il kodlu küpeler 31.12.2026'ya kadar) ve kontrol basamağı
   algoritması açık kaynaklarda yok. Katı kural GEÇERLİ küpeyi reddederdi.

2. **Türetilen değer istemciden alınmaz.** Yaş, buzağılama aralığı, aşı
   durumu ve gecikme sunucuda hesaplanır; şemalarda karşılıkları YOK.

3. **Güncellemede iyimser kilit.** Hayvan kaydı ahırda telefondan, ofiste
   masaüstünden düzenleniyor; son yazan kazanır kabul edilemez.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_validator,
)

# Sahadaki `field.py` ve tarladaki `farm_schemas.py` ile AYNI biçim.
_OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")

MAX_MIKTAR = Decimal("99999999999999.9999")
MAX_TUTAR = Decimal("9999999999999999.99")

DECIMAL_2_JSON_PATTERN = r"^-?\d+\.\d{2}$"
Decimal2Out = Annotated[
    Decimal,
    PlainSerializer(
        lambda value: format(value, ".2f"), return_type=str, when_used="json"
    ),
    WithJsonSchema({"type": "string", "pattern": DECIMAL_2_JSON_PATTERN}),
]

SPECIES = frozenset({"CATTLE", "BUFFALO", "SHEEP", "GOAT"})
GROUP_SPECIES = SPECIES | {"MIXED"}
SEX = frozenset({"FEMALE", "MALE"})
ANIMAL_STATUS = frozenset({"ACTIVE", "SOLD", "DEAD", "SLAUGHTERED", "ARCHIVED"})
ACQUISITION = frozenset({"BORN", "PURCHASED", "GIFTED", "OTHER"})
LIFECYCLE = frozenset({"ACTIVE", "ARCHIVED"})
BREEDING_METHOD = frozenset({"AI", "NATURAL", "EMBRYO_TRANSFER"})
BREEDING_RESULT = frozenset({"UNKNOWN", "PREGNANT", "NOT_PREGNANT", "ABORTED"})
BIRTH_OUTCOME = frozenset({"LIVE", "STILLBORN", "ABORTION"})
MOVEMENT_KIND = frozenset(
    {"PURCHASE", "SALE", "DEATH", "SLAUGHTER", "TRANSFER_IN", "TRANSFER_OUT"}
)

#: Hayvanı sürüden ÇIKARAN hareketler. Hareket kaydedilince hayvanın durumu
#: da değişmeli, yoksa "kaç hayvanım var" sorusu satılmış hayvanları da sayar.
MOVEMENT_TO_STATUS = {
    "SALE": "SOLD",
    "DEATH": "DEAD",
    "SLAUGHTER": "SLAUGHTERED",
    "TRANSFER_OUT": "ARCHIVED",
}

#: Bugünkü yaygın biçim: TR + rakamlar. UYARI ÜRETMEK İÇİN, reddetmek için
#: DEĞİL. Yeni karekod/turkuaz küpeler bu desene uymayabilir ve uymadığında
#: kullanıcı kaydını yine de yapabilmeli.
_KUPE_BEKLENEN = re.compile(r"^TR\d{10,14}$")


def kupe_uyarisi(ear_tag: str | None) -> str | None:
    """Küpe numarası beklenen biçimde değilse AÇIKLAYICI bir uyarı döndürür.

    `None` = uyarı yok. Bu fonksiyon hiçbir zaman hata fırlatmaz: küpe
    standardı geçişte olduğu için "beklenmeyen biçim" ile "geçersiz" aynı şey
    değil (bkz. modül başlığı).
    """
    if ear_tag is None:
        return None
    temiz = ear_tag.strip()
    if not temiz:
        return None
    if _KUPE_BEKLENEN.match(temiz):
        return None
    return (
        f"Küpe numarası ({temiz}) alışılmış biçimde değil (TR + 10-14 rakam). "
        "Kayıt yapıldı; küpe standardı 2026'da değiştiği için bu bir hata "
        "olmayabilir, yine de kontrol edin."
    )


def _metin(value: str) -> str:
    temiz = " ".join(value.split())
    if not temiz:
        raise ValueError("Boş bırakılamaz")
    return temiz


class _Taban(BaseModel):
    model_config = ConfigDict(extra="forbid")


#: FAZ 2'de ÇEVRİMDIŞI KUYRUK KİMLİĞİ YOK — ve bu bilinçli.
#:
#: Ahırda/merada girilen kayıtlar (aşı, doğum, tartım, sağım) ileride şebeke
#: olmadan tutulacak ve `operation_id` ile tekrar korumasına bağlanacak. Ama
#: DEFTER (tekrarı yakalayan tablo) bu fazda yazılmıyor.
#:
#: Şemaya şimdiden `operation_id` alanı koymak, istemciye "tekrar gönderim
#: güvenli" demek olurdu — oysa hiçbir şey onu yakalamıyor. Kabul edilen ama
#: uygulanmayan bir koruma alanı, hiç olmamasından KÖTÜDÜR: mükerrer aşı ve
#: mükerrer doğum kaydı sessizce oluşur.
#:
#: Alan, defteriyle BİRLİKTE eklenecek (tarla modülünde FAZ 4'te böyle
#: yapıldı: migration 0045 + uçlar aynı PR'da).
_KuyrukKimligi = _Taban


class _SurumluGuncelleme(_Taban):
    expected_updated_at: datetime


class GroupWrite(_Taban):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=180)
    species: str | None = None
    location: str | None = Field(default=None, max_length=180)
    # Bireysel kayıt tutulmayan sürüde baş sayısı. Bireysel kayıt varsa BOŞ
    # kalmalı; ikisi birden dolu olursa çifte sayım olur (uç kontrol eder).
    head_count: int | None = Field(default=None, ge=0, le=1_000_000)
    notes: str | None = None

    @field_validator("code")
    @classmethod
    def kod(cls, value: str) -> str:
        return _metin(value).upper()

    @field_validator("name")
    @classmethod
    def ad(cls, value: str) -> str:
        return _metin(value)

    @field_validator("species")
    @classmethod
    def tur(cls, value: str | None) -> str | None:
        if value is None:
            return None
        v = _metin(value).upper()
        if v not in GROUP_SPECIES:
            raise ValueError("Geçersiz tür")
        return v


class GroupUpdate(GroupWrite, _SurumluGuncelleme):
    status: str = "ACTIVE"

    @field_validator("status")
    @classmethod
    def durum(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in LIFECYCLE:
            raise ValueError("Geçersiz durum")
        return v


class AnimalWrite(_KuyrukKimligi):
    # Küpe NULLABLE: küçükbaş sürülerinde bireysel kayıt tutulmayabilir.
    ear_tag: str | None = Field(default=None, max_length=32)
    name: str | None = Field(default=None, max_length=120)
    species: str
    breed: str | None = Field(default=None, max_length=120)
    sex: str
    birth_date: date | None = None
    acquisition: str = "BORN"
    acquired_on: date | None = None
    group_id: int | None = Field(default=None, gt=0)
    mother_id: int | None = Field(default=None, gt=0)
    father_id: int | None = Field(default=None, gt=0)
    notes: str | None = None

    @field_validator("ear_tag")
    @classmethod
    def kupe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        temiz = " ".join(value.split()).upper()
        return temiz or None

    @field_validator("species")
    @classmethod
    def tur(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in SPECIES:
            raise ValueError("Geçersiz tür")
        return v

    @field_validator("sex")
    @classmethod
    def cinsiyet(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in SEX:
            raise ValueError("Geçersiz cinsiyet")
        return v

    @field_validator("acquisition")
    @classmethod
    def edinim(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in ACQUISITION:
            raise ValueError("Geçersiz edinim türü")
        return v


class AnimalUpdate(AnimalWrite, _SurumluGuncelleme):
    status: str = "ACTIVE"

    @field_validator("status")
    @classmethod
    def durum(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in ANIMAL_STATUS:
            raise ValueError("Geçersiz durum")
        return v


class VaccinationWrite(_KuyrukKimligi):
    animal_id: int = Field(gt=0)
    # Aşı adı SERBEST METİN: şap ve brusella zorunlu ama işletmeye göre
    # septisemi, IBR, BVD gibi başkaları da yapılıyor. Kapalı liste,
    # kullanıcıyı yaptığı aşıyı kaydedemez hâle getirirdi.
    vaccine: str = Field(min_length=1, max_length=120)
    # MAKİNE OKUNUR kod (FAZ 4). Yukarıdaki serbest metin ADI taşır, bu alan
    # HESABI: yaşa bağlı takvim "bu hayvanın şapı en son ne zaman yapıldı"
    # sorusunu cevaplamak zorunda ve serbest metinden tahmin — 'Şap', 'FMD',
    # 'şap aşısı' — sessizce yanlış cevap üretir (bkz. migration 0050).
    #
    # KAPALI LİSTE DEĞİL: yalnız takvimi olan kodlar (FMD, BRUCELLA) hesaba
    # girer; işletme kendi kodunu da yazabilir, hesaba girmez. Kapalı liste
    # yapmak, takvimi olmayan bir aşıyı kaydedilemez hâle getirirdi.
    vaccine_code: str | None = Field(default=None, max_length=40)
    applied_on: date
    dose_no: int | None = Field(default=None, gt=0, le=50)
    next_due_on: date | None = None
    veterinarian: str | None = Field(default=None, max_length=180)
    batch_no: str | None = Field(default=None, max_length=60)
    notes: str | None = None

    @field_validator("vaccine")
    @classmethod
    def asi(cls, value: str) -> str:
        return _metin(value)

    @field_validator("vaccine_code")
    @classmethod
    def kod(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _metin(value).upper() or None


class BreedingWrite(_KuyrukKimligi):
    animal_id: int = Field(gt=0)
    bred_on: date
    method: str = "AI"
    sire_code: str | None = Field(default=None, max_length=120)
    sire_animal_id: int | None = Field(default=None, gt=0)
    technician: str | None = Field(default=None, max_length=180)
    notes: str | None = None

    @field_validator("method")
    @classmethod
    def yontem(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in BREEDING_METHOD:
            raise ValueError("Geçersiz tohumlama yöntemi")
        return v


class BreedingUpdate(BreedingWrite, _SurumluGuncelleme):
    result: str = "UNKNOWN"
    checked_on: date | None = None
    expected_birth_on: date | None = None

    @field_validator("result")
    @classmethod
    def sonuc(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in BREEDING_RESULT:
            raise ValueError("Geçersiz gebelik sonucu")
        return v


class OffspringNested(_Taban):
    """Doğumla AYNI istekte oluşturulan yavru.

    Kendi `operation_id`si YOK: tekrar koruması doğumun kimliğinden geliyor.
    Ayrı kimlik, aynı işlemin iki kimlikle deftere yazılması demek olurdu.
    """

    ear_tag: str | None = Field(default=None, max_length=32)
    name: str | None = Field(default=None, max_length=120)
    sex: str
    notes: str | None = None

    @field_validator("sex")
    @classmethod
    def cinsiyet(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in SEX:
            raise ValueError("Geçersiz cinsiyet")
        return v

    @field_validator("ear_tag")
    @classmethod
    def kupe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split()).upper() or None


class BirthWrite(_KuyrukKimligi):
    mother_id: int = Field(gt=0)
    breeding_id: int | None = Field(default=None, gt=0)
    birth_date: date
    outcome: str = "LIVE"
    difficulty: int | None = Field(default=None, ge=1, le=4)
    notes: str | None = None
    # Yavrular AYNI istekte oluşturulur (tek işlem). Boş bırakılabilir:
    # küçükbaş sürüsünde bireysel yavru kaydı tutulmayabilir; o zaman
    # `offspring_count` sayıyı taşır.
    offspring: list[OffspringNested] | None = None
    offspring_count: int | None = Field(default=None, ge=0, le=20)

    @field_validator("outcome")
    @classmethod
    def sonuc(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in BIRTH_OUTCOME:
            raise ValueError("Geçersiz doğum sonucu")
        return v


class WeightWrite(_KuyrukKimligi):
    animal_id: int = Field(gt=0)
    weighed_on: date
    weight_kg: Decimal = Field(gt=0, le=MAX_MIKTAR)
    notes: str | None = None


class MilkYieldWrite(_KuyrukKimligi):
    animal_id: int | None = Field(default=None, gt=0)
    group_id: int | None = Field(default=None, gt=0)
    milked_on: date
    session: str | None = Field(default=None, max_length=20)
    quantity_liters: Decimal = Field(gt=0, le=MAX_MIKTAR)
    notes: str | None = None


class MovementWrite(_KuyrukKimligi):
    animal_id: int = Field(gt=0)
    kind: str
    moved_on: date
    amount: Decimal | None = Field(default=None, ge=0, le=MAX_TUTAR)
    counterparty: str | None = Field(default=None, max_length=180)
    reason: str | None = Field(default=None, max_length=255)
    notes: str | None = None

    @field_validator("kind")
    @classmethod
    def tur(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in MOVEMENT_KIND:
            raise ValueError("Geçersiz hareket türü")
        return v


class HerdFertilityThresholds(_Taban):
    target_calving_interval_days: int
    problem_calving_interval_days: int
    service_period_low_days: int
    service_period_high_days: int


class HerdFertilitySummary(_Taban):
    calving_interval_sample: int
    avg_calving_interval_days: int | None
    problem_interval_count: int
    age_at_first_calving_sample: int
    avg_age_at_first_calving_days: int | None
    service_period_sample: int
    avg_service_period_days: int | None
    never_calved: int
    births_without_breeding_link: int


class HerdFertilityItem(_Taban):
    animal_id: int
    ear_tag: str | None
    name: str | None
    species: str
    calving_count: int
    avg_calving_interval_days: int | None
    last_calving_interval_days: int | None
    age_at_first_calving_days: int | None
    service_period_days: int | None
    services_per_conception: Decimal2Out | None
    interval_is_problem: bool
    last_calving_on: date


class HerdFertilityResponse(_Taban):
    as_of: date
    thresholds: HerdFertilityThresholds
    summary: HerdFertilitySummary
    items: list[HerdFertilityItem]
