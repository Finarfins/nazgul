"""Tarla Yönetimi V1 — istek/yanıt şemaları (mobil-erp#2, FAZ 2).

İKİ KURAL ŞEMA SEVİYESİNDE ZORLANIYOR:

1. **Toplam maliyet istemciden ALINMAZ.** ``FieldActivityInputWrite`` içinde
   ``total_cost`` alanı YOK; sunucu ``quantity * unit_cost`` ile Decimal olarak
   türetir. Alan burada olsaydı bir istemci hatası (veya kötü niyet) sezon
   maliyetini sessizce kaydırırdı ve raporlar yanlış çıkardı.

2. **Sürüm alanı zorunlu.** Güncelleme şemaları ``expected_updated_at`` ister.
   Tarla kayıtları sahada telefondan, ofiste masaüstünden düzenleniyor; son
   yazan kazanır kabul edilemez.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .units import turkce_katla

# Sahadaki `field.py` ile birebir aynı biçim — bkz. `_KuyrukKimligi`.
_OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")

# Alan ve miktarlar için üst sınır: 18,4 sütununa sığmalı.
MAX_MIKTAR = Decimal("99999999999999.9999")
MAX_TUTAR = Decimal("9999999999999999.99")

ACTIVITY_TYPES = frozenset(
    {"SOWING", "FERTILIZING", "SPRAYING", "IRRIGATION", "TILLAGE", "OTHER"}
)
SEASON_STATUSES = frozenset({"PLANNED", "ACTIVE", "HARVESTED", "CLOSED", "CANCELLED"})
LIFECYCLE_STATUSES = frozenset({"ACTIVE", "ARCHIVED"})
TASK_STATUSES = frozenset({"OPEN", "DONE", "CANCELLED"})


def _metin(value: str) -> str:
    temiz = " ".join(value.split())
    if not temiz:
        raise ValueError("Boş bırakılamaz")
    return temiz


class _Taban(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _KuyrukKimligi(_Taban):
    """Çevrimdışı kuyruktan gelebilen oluşturma isteklerinin tekrar koruması.

    ``operation_id`` İSTEĞE BAĞLI: paneldeki kullanıcı doğrudan yazarken kuyruk
    yok, kimlik de yok. Zorunlu yapmak, panelin her isteğe anlamsız bir kimlik
    uydurmasını gerektirirdi.

    Biçim sahadakiyle AYNI (``^[A-Za-z0-9_-]{8,64}$`), bilerek: iki kuyruk
    farklı biçim kabul etseydi ortak istemci yardımcıları ayrışır ve biri
    sessizce reddedilirdi.
    """

    operation_id: str | None = Field(default=None, min_length=8, max_length=64)

    @field_validator("operation_id")
    @classmethod
    def islem_kimligi(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _OPERATION_ID.fullmatch(value):
            raise ValueError("Geçersiz işlem kimliği")
        return value


class _SurumlüGuncelleme(_Taban):
    """İyimser kilit taşıyan güncelleme tabanı.

    ``expected_updated_at`` istemcinin GÖRDÜĞÜ sürümdür. Sunucudaki değerle
    tutmazsa 409 döner; böylece iki kişi aynı kaydı düzenlediğinde ikincisi
    birincinin yazdığını sessizce ezmez.
    """

    expected_updated_at: datetime


class FarmWrite(_Taban):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=180)
    customer_id: int | None = Field(default=None, gt=0)
    city: str | None = Field(default=None, max_length=80)
    district: str | None = Field(default=None, max_length=80)
    notes: str | None = None

    @field_validator("code")
    @classmethod
    def kod(cls, value: str) -> str:
        return _metin(value).upper()

    @field_validator("name")
    @classmethod
    def ad(cls, value: str) -> str:
        return _metin(value)


class FarmUpdate(FarmWrite, _SurumlüGuncelleme):
    status: str = "ACTIVE"

    @field_validator("status")
    @classmethod
    def durum(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in LIFECYCLE_STATUSES:
            raise ValueError("Geçersiz durum")
        return v


class ParcelWrite(_Taban):
    farm_id: int = Field(gt=0)
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=180)
    # Dekar. Sıfır ve negatif şemada da reddediliyor; veritabanındaki CHECK
    # son savunma, ilk savunma burası (kullanıcı anlaşılır hata görsün).
    area_decare: Decimal = Field(gt=0, le=MAX_MIKTAR)
    parcel_no: str | None = Field(default=None, max_length=40)
    block_no: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=80)
    district: str | None = Field(default=None, max_length=80)
    neighborhood: str | None = Field(default=None, max_length=120)
    boundary_geojson: str | None = None

    @field_validator("code")
    @classmethod
    def kod(cls, value: str) -> str:
        return _metin(value).upper()

    @field_validator("name")
    @classmethod
    def ad(cls, value: str) -> str:
        return _metin(value)


class ParcelUpdate(ParcelWrite, _SurumlüGuncelleme):
    status: str = "ACTIVE"

    @field_validator("status")
    @classmethod
    def durum(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in LIFECYCLE_STATUSES:
            raise ValueError("Geçersiz durum")
        return v


class SeasonWrite(_Taban):
    parcel_id: int = Field(gt=0)
    season_year: int = Field(ge=2000, le=2200)
    crop: str = Field(min_length=1, max_length=120)
    # ÜRÜNÜ SEZON BİLDİRİR, HASAT DEVRALIR (göç 20260827_0062). `crop` serbest
    # metin olarak KALIR — insanın okuduğu ad odur; `product_id` ise stok
    # defterine giden bağdır. İkisi ayrı şeyler olduğu için birbirinin yerine
    # geçmiyor.
    #
    # OPSİYONEL OLMAK ZORUNDA: ürünü bildirilmemiş sezonun hasadı tüketicide
    # adı konmuş `SKIPPED_NO_PRODUCT` kovasına düşer. Zorunlu yapmak, mevcut
    # sezonlara bir ürün UYDURMAYI dayatırdı.
    #
    # `gt=0` biçim kapısıdır, KİRACI KAPISI DEĞİL: ürünün çağıranın firmasına
    # ait olduğu yönlendiricide `_urun_dogrula` ile ölçülür — `ActivityInput`
    # ailesindeki `product_id` ile AYNI desen.
    product_id: int | None = Field(default=None, gt=0)
    variety: str | None = Field(default=None, max_length=120)
    started_on: date | None = None
    ended_on: date | None = None
    planted_area_decare: Decimal | None = Field(default=None, gt=0, le=MAX_MIKTAR)
    notes: str | None = None
    # ÇKS tek ürün: aynı parsele üçüncü yıl aynı ürün gerekçesiz GEÇMEZ
    # (uç kontrol eder). Hasattaki safety_override_reason ile aynı şekil.
    monoculture_override_reason: str | None = Field(default=None, max_length=255)

    @field_validator("crop")
    @classmethod
    def urun(cls, value: str) -> str:
        return _metin(value)


class SeasonUpdate(SeasonWrite, _SurumlüGuncelleme):
    status: str = "ACTIVE"

    @field_validator("status")
    @classmethod
    def durum(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in SEASON_STATUSES:
            raise ValueError("Geçersiz sezon durumu")
        return v


class ActivityInputNested(_Taban):
    """Faaliyetle AYNI istekte gönderilen girdi.

    ``ActivityInputWrite``dan tek farkı: kendi ``operation_id``si YOK. Tekrar
    koruması faaliyetin kimliğinden geliyor — iç içe girdiye ayrı kimlik
    vermek, aynı işlemin iki farklı kimlikle deftere yazılması demek olurdu.

    ``total_cost`` burada da YOK; sunucu türetir.
    """

    product_id: int | None = Field(default=None, gt=0)
    input_name: str = Field(min_length=1, max_length=180)
    quantity: Decimal = Field(gt=0, le=MAX_MIKTAR)
    unit: str = Field(min_length=1, max_length=32)
    unit_cost: Decimal | None = Field(default=None, ge=0, le=MAX_TUTAR)
    dose: Decimal | None = Field(default=None, gt=0, le=MAX_MIKTAR)
    dose_unit: str | None = Field(default=None, max_length=32)

    @field_validator("input_name", "unit")
    @classmethod
    def metin(cls, value: str) -> str:
        return _metin(value)


class ActivityWrite(_KuyrukKimligi):
    season_id: int = Field(gt=0)
    activity_type: str
    performed_at: datetime
    applied_area_decare: Decimal | None = Field(default=None, gt=0, le=MAX_MIKTAR)
    operator_user_id: int | None = Field(default=None, gt=0)
    machine_id: int | None = Field(default=None, gt=0)
    reentry_interval_days: int | None = Field(default=None, ge=0, le=3650)
    preharvest_interval_days: int | None = Field(default=None, ge=0, le=3650)
    # İŞÇİLİK VE MAKİNE SAATİ (Gerçek Maliyet FAZ 2, mobil-erp#24). Saat GİRİLİR,
    # oran GİRİLMEZ: oranı sunucu `cost_rates`ten çözüp satıra KOPYALAR ve o
    # kopya bir daha değişmez (bkz. `routers/farm.py._oran_kopyala`).
    labor_hours: Decimal | None = Field(default=None, gt=0, le=MAX_MIKTAR)
    machine_hours: Decimal | None = Field(default=None, gt=0, le=MAX_MIKTAR)
    # ORAN YİNE DE GÖNDERİLEBİLİR ve bu bir kaçış kapısı değil, deponun mevcut
    # deseni: `work_order_labor_lines` da açıkça gönderilen oranı kabul edip
    # yalnız yokluğunda varsayılana düşüyor. Gerekçe alanda somut — gündelikçi
    # ekibe o gün başka bir yevmiye ödenmiş olabilir ve tanımlı varsayılanı
    # geçici olarak değiştirmek geçmişteki bütün kayıtları etkilerdi.
    labor_hourly_rate: Decimal | None = Field(default=None, ge=0, le=MAX_TUTAR)
    machine_hourly_rate: Decimal | None = Field(default=None, ge=0, le=MAX_TUTAR)
    notes: str | None = None
    # Faaliyet alanı parseli aşıyorsa AÇIK gerekçe zorunlu (uç kontrol eder).
    area_override_reason: str | None = Field(default=None, max_length=255)
    # Tarlaya giriş yasağı dolmadan faaliyet: AÇIK gerekçe (uç kontrol eder).
    # Hasattaki safety_override_reason ile aynı şekil — sistemin bulduğu
    # metin ayrı sütuna (reentry_warning) yazılır.
    reentry_override_reason: str | None = Field(default=None, max_length=255)
    # AYNI İSTEKTE girdiler. Sahada ilaçlama girmek iki ayrı istek gerektiriyordu
    # (önce faaliyet, sonra girdi) ve ikisi AYRI İŞLEMDİ: arada kesilme olursa
    # girdisiz bir faaliyet kalıyor, sezon maliyeti sessizce eksik çıkıyordu.
    # Burada gönderilirse hepsi TEK işlemde yazılır.
    inputs: list[ActivityInputNested] | None = None

    @field_validator("activity_type")
    @classmethod
    def tur(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in ACTIVITY_TYPES:
            raise ValueError("Geçersiz faaliyet türü")
        return v

    @model_validator(mode="after")
    def saatsiz_oran_olmaz(self) -> "ActivityWrite":
        """Saati olmayan bir orana anlam veremeyiz.

        Oran tek başına yazılsaydı satırda maliyeti olmayan bir "birim fiyat"
        kalırdı; sonraki bir faz onu bir yerden saatle çarpmaya kalkarsa hangi
        saatle çarpacağı belirsiz olur. Reddetmek, saklayıp anlamını sonraya
        bırakmaktan iyidir.
        """
        if self.labor_hourly_rate is not None and self.labor_hours is None:
            raise ValueError("İşçilik oranı için işçilik saati de gerekli")
        if self.machine_hourly_rate is not None and self.machine_hours is None:
            raise ValueError("Makine oranı için makine saati de gerekli")
        return self


class ActivityInputWrite(_KuyrukKimligi):
    """Faaliyet girdisi.

    ``total_cost`` BİLEREK YOK — sunucu türetir (bkz. modül başlığı).
    """

    product_id: int | None = Field(default=None, gt=0)
    input_name: str = Field(min_length=1, max_length=180)
    quantity: Decimal = Field(gt=0, le=MAX_MIKTAR)
    unit: str = Field(min_length=1, max_length=32)
    unit_cost: Decimal | None = Field(default=None, ge=0, le=MAX_TUTAR)
    dose: Decimal | None = Field(default=None, gt=0, le=MAX_MIKTAR)
    dose_unit: str | None = Field(default=None, max_length=32)

    @field_validator("input_name", "unit")
    @classmethod
    def metin(cls, value: str) -> str:
        return _metin(value)


class HarvestWrite(_KuyrukKimligi):
    season_id: int = Field(gt=0)
    # Satılan miktar ve gelir ELLE giriliyor ve hasat miktarından AYRI: hasadın
    # tamamı satılmaz (tohumluk, yemlik, fire). Boş bırakılabilir — hasat
    # kaydedildiğinde satış henüz yapılmamış olur. Boş "sıfır" değil
    # "HENÜZ BİLİNMİYOR" demektir (bkz. migration 0047).
    sold_quantity: Decimal | None = Field(default=None, ge=0, le=MAX_MIKTAR)
    revenue_amount: Decimal | None = Field(default=None, ge=0, le=MAX_TUTAR)
    # İlaçlama sonrası hasat bekleme süresi dolmadan hasat kaydedilmek
    # isteniyorsa AÇIK gerekçe şart (uç kontrol eder, bkz. migration 0046).
    safety_override_reason: str | None = Field(default=None, max_length=255)
    harvested_on: date
    quantity: Decimal = Field(gt=0, le=MAX_MIKTAR)
    unit: str = Field(min_length=1, max_length=32)
    harvested_area_decare: Decimal | None = Field(default=None, gt=0, le=MAX_MIKTAR)
    quality_grade: str | None = Field(default=None, max_length=60)
    moisture_percent: Decimal | None = Field(default=None, ge=0, le=100)
    notes: str | None = None

    @field_validator("unit")
    @classmethod
    def birim(cls, value: str) -> str:
        return _metin(value)


class HarvestTicketDeductionWrite(_Taban):
    """Kantar fişindeki TEK bir kalite kesintisi satırı.

    ``label`` SERBEST METİN: kesinti adları alıcıdan alıcıya değişiyor ve bir
    enum uydurmak kağıtta yazan adı kaybedip yerine bizim sınıflandırmamızı
    koyardı (bkz. göç 0069).

    ``rate_percent`` YÜZDEDİR, miktar değil. Miktar olarak girilseydi brütten
    bağımsız olurdu ve brüt düzeltildiğinde net sessizce yanlış kalırdı.

    SONLULUK: `ge=0, le=100` sınırları sonlu olmayan bir girdiyi (NaN, sNaN,
    ±Infinity) Pydantic katmanında 422 yapıyor — ÖLÇÜLDÜ, varsayılmadı
    (`tests/test_kantar_fisi_sonluluk.py`). Oranın da brüt kadar korunması
    şart: sonsuz bir oran türetilen neti eksi sonsuza götürürdü.
    """

    label: str = Field(min_length=1, max_length=120)
    rate_percent: Decimal = Field(ge=0, le=100)

    @field_validator("label")
    @classmethod
    def etiket(cls, value: str) -> str:
        return _metin(value)


class HarvestTicketWrite(_Taban):
    """Kantar fişi — kağıtta ne yazıyorsa o.

    ``derived_net_quantity`` BİLEREK YOK: net sunucuda brütten ve oranlardan
    türetilir (aynı kural: ``ActivityInputWrite.total_cost``). İstemciden
    alınsaydı, hesabın kaynağı istemci olurdu.

    ``ticket_net_quantity`` İSTEMCİDEN GELİR ama TÜRETİME GİRMEZ — kağıdın
    kendi neti bir TANIKTIR ve yalnız karşılaştırılır (``net_mismatch``).

    ``base_quantity`` ve ``entered_factor`` DA İSTEMCİDEN ALINMAZ: ikisini de
    ``app/units.py``in ``resolve``ı üretir. İstemcinin gönderdiği bir katsayı,
    "o gün neye inanıldığının kanıtı" olmaktan çıkıp istemcinin iddiası
    olurdu.

    ``operation_id`` YOK — bu şema ``_KuyrukKimligi``den TÜREMİYOR ve bu bir
    karar: yeni bir kuyruk türü ``ck_farm_operations_kind`` CHECK'ini yeniden
    yazmayı gerektirirdi ve kantar fişi SAHA değil DEPO yolundan girilir.
    Tekrar koruması kağıdın kendi kimliğinden geliyor:
    ``UNIQUE(company_id, harvest_id, ticket_no)``. Numarasız fiş iki kez
    girilebilir; bedel göç 0069 başlığında adı konmuş hâliyle kabul
    edilmiştir.
    """

    harvest_id: int = Field(gt=0)
    # KALİTE KESİNTİLERİNDEN ÖNCEKİ ÜRÜN AĞIRLIĞI — araç+yük DEĞİL.
    # Bkz. göç 0069 başlığı: karıştırılması hata değil CEVAP üretir.
    #
    # `gt=0, le=MAX_MIKTAR` sınırları AYRICA sonluluk kapısıdır ve bu
    # ÖLÇÜLDÜ: Pydantic 2.13.5 bu sınırlarla "NaN"/"sNaN"/"Infinity"/
    # "-Infinity" girdilerinin DÖRDÜNÜ DE 422 yapıyor. Koruma kırılgandır
    # (bkz. `tests/test_kantar_fisi_sonluluk.py`): `_Taban`a
    # `allow_inf_nan=True` konsa "Infinity" `gt=0`ı GEÇERDİ. İkinci katman
    # `units.resolve`un kendi sonluluk kapısıdır.
    gross_entered_quantity: Decimal = Field(gt=0, le=MAX_MIKTAR)
    # GİRİLEN BİRİM — 0066'nın sözlüğü. Fişin miktarı hasadın biriminde
    # OLMAK ZORUNDA DEĞİL: kantar tonla tartar, hasat kilo tutulabilir.
    entered_unit: str = Field(min_length=1, max_length=40)
    ticket_net_quantity: Decimal | None = Field(default=None, ge=0, le=MAX_MIKTAR)
    ticket_no: str | None = Field(default=None, max_length=60)
    buyer_name: str | None = Field(default=None, max_length=180)
    plate: str | None = Field(default=None, max_length=20)
    weighed_at: datetime | None = None
    notes: str | None = None
    # Fiş ve kesintileri TEK istekte gelir: "toplam oran <= 100" SATIRLAR ARASI
    # bir kuraldır ve ancak hepsi bir aradayken doğrulanabilir. Kesintileri
    # tek tek ekleyen bir uç, aradaki her anda YARIM bir fiş bırakırdı.
    deductions: list[HarvestTicketDeductionWrite] = Field(
        default_factory=list, max_length=20
    )

    @field_validator("entered_unit")
    @classmethod
    def girilen_birim(cls, value: str) -> str:
        """KANONİK biçimde saklanır: "Ton"/"ton"/"TON" TEK değerdir.

        Sahip kararı (kantar fişi v2 incelemesi): ham dizgi HİÇBİR yerde
        tutulmaz. `products.base_unit` ile AYNI katlama (`units.turkce_katla`)
        ki iki sütun aynı birimi iki biçimde yazmasın; çözücü zaten katlanmış
        biçimi arar, katlamayı buraya taşımak yalnız SAKLANAN değeri de o
        biçime bağlar. Ölçüldü: katlama kaldırılınca
        `tests/test_kantar_fisi_sozlesme.py` senaryo 5 kırmızı ("Ton" olduğu
        gibi geri okunur).
        """
        return turkce_katla(_metin(value))

    @field_validator("ticket_no", "buyer_name", "plate")
    @classmethod
    def serbest_metin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        temiz = " ".join(value.split())
        # Boş metin ile "girilmemiş" AYNI ŞEY DEĞİL — ama boş bir dizgi de
        # bilgi taşımıyor. Boşluktan ibaret girdi None'a düşer ki
        # `ticket_no` tekilliği bir boşluk karakterine bağlanmasın.
        return temiz or None

    @field_validator("plate")
    @classmethod
    def plaka(cls, value: str | None) -> str | None:
        return None if value is None else value.upper()

    @model_validator(mode="after")
    def kesinti_toplami(self) -> "HarvestTicketWrite":
        """Kesinti toplamı %100'ü AŞAMAZ.

        Aşsaydı türetilen net NEGATİF olurdu. Negatif bir neti KIRPMAK (0'a
        çekmek) daha kötü olurdu: kırpma, veri girişi hatasını sessizce
        "sıfır ürün geldi" diye kaydeder.

        Etiketler de benzersiz olmalı: kağıtta aynı kesinti iki satırda
        yazmaz ve yazıyorsa hangisinin geçerli olduğu BİLİNMEZ.
        """
        toplam = sum((k.rate_percent for k in self.deductions), Decimal("0"))
        if toplam > 100:
            raise ValueError(
                f"Kesinti oranlarının toplamı %100'ü aşamaz (gelen: %{toplam})"
            )
        etiketler = [k.label.casefold() for k in self.deductions]
        if len(set(etiketler)) != len(etiketler):
            raise ValueError("Aynı kesinti adı bir fişte iki kez yazılamaz")
        return self


class TaskWrite(_Taban):
    title: str = Field(min_length=1, max_length=180)
    season_id: int | None = Field(default=None, gt=0)
    parcel_id: int | None = Field(default=None, gt=0)
    due_date: date | None = None
    priority: str = "NORMAL"
    assigned_user_id: int | None = Field(default=None, gt=0)
    notes: str | None = None

    @field_validator("title")
    @classmethod
    def baslik(cls, value: str) -> str:
        return _metin(value)

    @field_validator("priority")
    @classmethod
    def oncelik(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in {"LOW", "NORMAL", "HIGH"}:
            raise ValueError("Geçersiz öncelik")
        return v


class TaskUpdate(TaskWrite, _SurumlüGuncelleme):
    status: str = "OPEN"
    activity_id: int | None = Field(default=None, gt=0)

    @field_validator("status")
    @classmethod
    def durum(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in TASK_STATUSES:
            raise ValueError("Geçersiz görev durumu")
        return v


# ---------------------------------------------------------------------------
# BKÜ KATALOĞU (göç 20260901_0063)
# ---------------------------------------------------------------------------


class PlantProtectionProductWrite(_Taban):
    """Bir stok ürününün BKÜ etiketinden gelen bekleme süreleri.

    ``product_id`` ZORUNLU: ürüne bağlı olmayan bir katalog satırı hiçbir
    faaliyeti çözemez, yani doldurulup hiç kullanılmayan bir alan olurdu.

    ``crop`` BOŞ BIRAKILABİLİR ve boş bırakmak "bütün bitkiler" demektir.
    Sezonun bitkisiyle eşleşen satır varsa o, yoksa bu kullanılır — böylece
    firma tek satırla başlayıp gerektiğinde bitkiye özelleştirebiliyor.
    """

    product_id: int = Field(gt=0)
    crop: str = Field(default="", max_length=120)
    registration_no: str | None = Field(default=None, max_length=60)
    # Kataloğun VAR OLMA SEBEBİ; boş geçilemez. Üst sınır faaliyet şemasıyla
    # AYNI (3650) — iki yer farklı sınır koysaydı katalogdan çözülen bir değer
    # faaliyete yazılamaz ve hata kullanıcıya anlamsız görünürdü.
    preharvest_interval_days: int = Field(ge=0, le=3650)
    reentry_interval_days: int | None = Field(default=None, ge=0, le=3650)
    notes: str | None = None

    @field_validator("crop")
    @classmethod
    def bitki(cls, value: str) -> str:
        # Boş dize GEÇERLİ (bütün bitkiler); `_metin` boşu reddettiği için
        # burada yalnız boşluk sadeleştirmesi yapılıyor.
        return " ".join(value.split())

    @field_validator("registration_no")
    @classmethod
    def ruhsat(cls, value: str | None) -> str | None:
        if value is None:
            return None
        temiz = " ".join(value.split())
        return temiz or None


class PlantProtectionProductUpdate(PlantProtectionProductWrite, _SurumlüGuncelleme):
    status: str = "ACTIVE"

    @field_validator("status")
    @classmethod
    def durum(cls, value: str) -> str:
        v = _metin(value).upper()
        if v not in LIFECYCLE_STATUSES:
            raise ValueError("Geçersiz durum")
        return v
