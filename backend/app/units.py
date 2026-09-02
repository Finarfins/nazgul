"""Girilen birimi ürünün TABAN birimine çeviren çözücü. ÇAĞIRANI YOKTUR.

Konu: BİRİM DÖNÜŞÜMÜ, PR 1 — çözücü ve deposu, hiçbir şeye bağlanmadan.

--- BU DOSYA NEDEN ÇAĞIRANDAN ÖNCE YAZILDI --------------------------------

Bir çözücünün ŞEKLİ ve ÖLÇEĞİ, onu çağıran ilk yol yazılmadan ÖNCE
çivilenmelidir. Ters sıranın bilinen sonucu şudur: çağıran önce gelirse
dönüşüm o çağıranın ihtiyacına göre şekillenir, ikinci çağıran geldiğinde
ölçek zaten bir yerde donmuştur ve ikisi sessizce ayrışır.

Bu dosyanın TEK iddiası sayısaldır: ÜRÜN ölçeği 4 basamaktır, yuvarlama
ROUND_HALF_UP'tır, ve GİRDİLER ASLA YUVARLANMAZ. İddia sayısal olduğu için
kanıtı da sayısal olmak zorundadır — `test_birim_donusumu_postgresql.py`
gerçek PostgreSQL NUMERIC(18,4) üzerinde AYIRT EDİCİ olduğu ÖLÇÜLEREK
gösterildi (quantize kaldırılınca KIRMIZI, geri konunca YEŞİL).

--- OKUMA SIRASI: ÜRÜN SATIRI -> EVRENSEL HARİTA -> RED --------------------

1. `product_unit_factors` satırı (firmanın KENDİ beyanı) HER ZAMAN önce
   okunur. "1 çuval = 33 kg" evrensel bir olgu değildir, o firmanın o ürün
   için beyanıdır ve evrensel bir kuralın onu EZMESİ olamaz.
2. Evrensel harita (aşağıda, DONMUŞ): yalnız metrik ilişkiler. `1 TON =
   1000 KG` kimsenin beyanı değildir, ölçü sisteminin kendisidir.
3. İkisi de cevap veremiyorsa REDDEDİLİR. Uydurulmaz.

3. adım bu dosyanın VAR OLMA SEBEBİDİR. Bir çözücünün "bilmiyorum"
diyebilmesi, doğru çevirebilmesi kadar önemlidir: bilinmeyen bir birimi 1.0
katsayısıyla geçirmek, ÖLÇÜLMEMİŞ bir sayıyı ÖLÇÜLMÜŞ gibi stoka yazardı ve
bu, dönüşümün HİÇ OLMAMASINDAN daha kötüdür — hata artık görünmez olurdu.

--- `base_unit IS NULL` REDDEDİLİR, VARSAYILMAZ ---------------------------

Taban bildirilmemiş bir üründe taban dışı bir birim girilirse çözücü
`TABAN_BILDIRILMEMIS` ile reddeder. Girilen birimi taban SAYMAK (yani
katsayı 1 ile geçmek) reddedildi: bu bir OLGU UYDURMAK olurdu ve bütün bu
tasarım tam olarak onu yapmamak için vardır.

--- NEDEN İSTİSNA, NEDEN DÖNÜŞ DEĞERİ DEĞİL -------------------------------

Red bir SENTINEL ile dönseydi (None, 0, ya da `(None, None)`) çağıran onu
sessizce sayı yerine koyabilirdi. İstisna YAKALANMAK ZORUNDADIR. Bu, iki
çağıranın iki farklı davranışını da mümkün kılar ve sahip kararı ikisini
ayırdı: outbox ADI KONMUŞ bir atlama kovasına düşürür, etkileşimli yazma
REDDEDER. İkisi de `BirimCozulemedi.sebep` üzerinden ayırt edilir.

--- TÜRKÇE KATLAMA: BU KOPYA GEÇİCİDİR ------------------------------------

`unit_code` SERBEST METİNDİR ve kapalı bir liste GEREKMEZ, çünkü çözücü
zaten ne evrensel ne de beyan edilmiş olan her şeyi reddediyor. Ama
karşılaştırma Türkçe katlanmak ZORUNDA:

    "litre".upper() -> "LITRE"    ama Türkçe "LİTRE" ister
    "i".upper()     -> "I"        ama Türkçe "İ" ister

DİKKAT — BU KATLAMANIN İKİNCİ BİR KOPYASI VARDIR VE BU DURUM GEÇİCİDİR.
`claude/bitki-esitligi-tr` dalı (`f1e8ca4`, PR #25) AYNI katlamayı
`_bitki_esit` için kuruyor. O dal bu iş başlarken `origin/develop`e
BİRLEŞMEMİŞTİ (ölçüldü: `git log --oneline origin/develop | grep -i bitki`
BOŞ döndü), bu yüzden burada en küçük doğru katlama yazıldı.

O dal indiğinde BU KOPYA SİLİNMELİ ve ortak olan çağrılmalıdır. Gerekçe
tarihseldir ve bu deponun KENDİ hatasıdır: İ/ı katlaması üç katmanda
BİRBİRİNDEN AYRIŞTI ve ayrışmanın başlangıcı tam olarak "ikinci bir kopya"
idi.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType
from typing import Mapping

# --- ÜRÜN ÖLÇEĞİ ------------------------------------------------------------
# `products.stock` ve `stock_movements.quantity` NUMERIC(18,4)'tür
# (`core_schema.QUANTITY`). Çözücünün ÜRÜNÜ o sütuna girecek sayıdır, bu
# yüzden ölçek ORADAN gelir; burada seçilmiş bir sayı DEĞİLDİR.
URUN_KUANTUM = Decimal("0.0001")

# --- EVRENSEL HARİTA (DONMUŞ) ----------------------------------------------
# Yalnız ölçü sisteminin KENDİSİ. Firmaya, ürüne ya da sezona göre değişen
# hiçbir şey buraya giremez — öyle bir şey `product_unit_factors`a aittir.
#
# Katsayılar bir BOYUT içindeki ortak birime göredir; çevrim iki katsayının
# ORANIDIR, yani "TON -> KG" ile "KG -> TON" ayrı ayrı YAZILMAZ. Tersinin
# elle yazılması, iki yönün birbirinden ayrışabildiği bir yer açardı.
_KUTLE = "KUTLE"
_HACIM = "HACIM"
_SAYI = "SAYI"

_EVRENSEL: Mapping[str, tuple[str, Decimal]] = MappingProxyType(
    {
        # kütle — ortak birim KG
        "KG": (_KUTLE, Decimal("1")),
        "KİLOGRAM": (_KUTLE, Decimal("1")),
        "GRAM": (_KUTLE, Decimal("0.001")),
        "G": (_KUTLE, Decimal("0.001")),
        "MİLİGRAM": (_KUTLE, Decimal("0.000001")),
        "TON": (_KUTLE, Decimal("1000")),
        # hacim — ortak birim LİTRE
        "LİTRE": (_HACIM, Decimal("1")),
        "LT": (_HACIM, Decimal("1")),
        "L": (_HACIM, Decimal("1")),
        "MİLİLİTRE": (_HACIM, Decimal("0.001")),
        "ML": (_HACIM, Decimal("0.001")),
        # sayı — ortak birim ADET
        "ADET": (_SAYI, Decimal("1")),
    }
)


class BirimCozulemedi(Exception):
    """Çözücü bir olgu UYDURMAK yerine durdu. `sebep` hangisi olduğunu söyler.

    Sebepler çağıranlar için AYIRT EDİCİDİR: outbox bunları adı konmuş
    atlama kovalarına, etkileşimli yazma ise redde çevirir.
    """

    TABAN_BILDIRILMEMIS = "TABAN_BILDIRILMEMIS"
    BIRIM_TANIMSIZ = "BIRIM_TANIMSIZ"
    BOYUT_UYUSMAZLIGI = "BOYUT_UYUSMAZLIGI"
    KATSAYI_GECERSIZ = "KATSAYI_GECERSIZ"

    def __init__(self, sebep: str, mesaj: str) -> None:
        super().__init__(f"{sebep}: {mesaj}")
        self.sebep = sebep


def turkce_katla(metin: str) -> str:
    """Türkçe büyütme: `i -> İ`, `ı -> I`, gerisi standart.

    GEÇİCİ KOPYA — bkz. başlıktaki "TÜRKÇE KATLAMA" bölümü. `f1e8ca4`
    indiğinde bu fonksiyon SİLİNİR ve ortak olan çağrılır.

    `"ı".upper()` zaten `"I"` verdiği için tek düzeltilmesi gereken `i`dir;
    değişim `.upper()`tan ÖNCE yapılır, çünkü sonra yapılsaydı `i` çoktan
    `I` olmuş ve `ı`dan AYIRT EDİLEMEZ hale gelmiş olurdu.
    """
    return metin.strip().replace("i", "İ").upper()


def _katsayi(
    birim: str,
    taban: str,
    urun_katsayilari: Mapping[str, Decimal],
) -> Decimal:
    """Okuma sırasını UYGULAR: ürün satırı -> evrensel harita -> RED."""
    # --- 1. ÜRÜNÜN KENDİ BEYANI, HER ZAMAN ÖNCE ----------------------------
    if birim in urun_katsayilari:
        katsayi = urun_katsayilari[birim]
        if not isinstance(katsayi, Decimal):
            raise BirimCozulemedi(
                BirimCozulemedi.KATSAYI_GECERSIZ,
                f"{birim!r} katsayısı Decimal değil: {type(katsayi).__name__}. "
                "Bu çözücü float KABUL ETMEZ; ikili kayan nokta bir stok "
                "sayısına giremez.",
            )
        if katsayi <= 0:
            raise BirimCozulemedi(
                BirimCozulemedi.KATSAYI_GECERSIZ,
                f"{birim!r} katsayısı pozitif değil: {katsayi}",
            )
        return katsayi

    # --- 2. EVRENSEL HARİTA -------------------------------------------------
    girilen = _EVRENSEL.get(birim)
    taban_kaydi = _EVRENSEL.get(taban)
    if girilen is None or taban_kaydi is None:
        raise BirimCozulemedi(
            BirimCozulemedi.BIRIM_TANIMSIZ,
            f"{birim!r} -> {taban!r}: ne ürünün beyanında ne de evrensel "
            "haritada. Çözücü katsayı UYDURMAZ.",
        )
    if girilen[0] != taban_kaydi[0]:
        raise BirimCozulemedi(
            BirimCozulemedi.BOYUT_UYUSMAZLIGI,
            f"{birim!r} ({girilen[0]}) -> {taban!r} ({taban_kaydi[0]}): "
            "boyutlar farklı. Hacmi kütleye çevirmek YOĞUNLUK ister ve "
            "yoğunluk evrensel DEĞİLDİR — o, ürünün beyanına aittir.",
        )
    # Oran: iki katsayı da ortak birime göredir.
    return girilen[1] / taban_kaydi[1]


def resolve(
    entered_quantity: Decimal,
    entered_unit: str,
    base_unit: str | None,
    product_factors: Mapping[str, Decimal] | None = None,
) -> tuple[Decimal, Decimal]:
    """Girilen miktarı ürünün taban birimine çevirir.

    Döner: ``(base_quantity, factor_used)``.

    ``base_quantity`` ÜRÜNDÜR ve 4 basamağa ROUND_HALF_UP ile yuvarlanır —
    gideceği sütun NUMERIC(18,4)'tür ve yuvarlamayı VERİTABANINA BIRAKMAK onu
    diyalektin insafına bırakmak olurdu.

    ``factor_used`` VERİLDİĞİ GİBİ döner, YUVARLANMAZ. ``entered_quantity`` de
    hiçbir noktada yuvarlanmaz. Gerekçe sahip kararı 1'dir: yanlış çıkan bir
    katsayı ASLA yeniden hesaplanmaz, düzeltme YENİ BİR SATIRDIR — yani
    hareketin üzerinde duran katsayı, o gün NEYE İNANILDIĞININ tek kanıtıdır
    ve yuvarlanmış bir kanıt kanıt değildir.

    ``float`` KABUL EDİLMEZ: ikili kayan nokta bir stok sayısına giremez.
    """
    if not isinstance(entered_quantity, Decimal):
        raise BirimCozulemedi(
            BirimCozulemedi.KATSAYI_GECERSIZ,
            "entered_quantity Decimal olmalı, "
            f"{type(entered_quantity).__name__} verildi.",
        )

    girilen_birim = turkce_katla(entered_unit)
    if not girilen_birim:
        raise BirimCozulemedi(BirimCozulemedi.BIRIM_TANIMSIZ, "entered_unit boş.")

    katlanmis_katsayilar = {
        turkce_katla(anahtar): deger
        for anahtar, deger in (product_factors or {}).items()
    }

    # --- TABAN BİLDİRİLMEMİŞ: REDDET, VARSAYMA ----------------------------
    if base_unit is None or not base_unit.strip():
        raise BirimCozulemedi(
            BirimCozulemedi.TABAN_BILDIRILMEMIS,
            f"ürünün taban birimi bildirilmemiş, girilen birim "
            f"{girilen_birim!r}. Girileni taban SAYMAK bir olgu uydurmak "
            "olurdu; sahip kararı 2 bunu reddetti.",
        )
    taban_birim = turkce_katla(base_unit)

    # Taban birimin KENDİSİ her zaman 1'dir; harita ARANMAZ, çünkü tabanın
    # evrensel haritada olması GEREKMEZ — "çuval" tabanlı bir ürün geçerlidir.
    if girilen_birim == taban_birim:
        katsayi = Decimal("1")
    else:
        katsayi = _katsayi(girilen_birim, taban_birim, katlanmis_katsayilar)

    urun = (entered_quantity * katsayi).quantize(URUN_KUANTUM, rounding=ROUND_HALF_UP)
    return urun, katsayi
