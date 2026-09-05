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

--- SONLU OLMAYAN SAYILAR: NaN, Inf, -Inf, sNaN --------------------------

`Decimal` OLMAK sonlu olmak DEĞİLDİR. `Decimal("NaN")` bir `Decimal`dir,
`isinstance` kapısından geçer ve `Decimal(<kullanıcı metni>)` ile üretilebilir
— PR 2'nin çağıranı istek gövdesinden tam olarak böyle ayrıştıracaktır.

ÖLÇÜLEN KUSUR (fix'ten ÖNCE, bu dalın kendi ağacında):

    resolve(Decimal("NaN"), "KG", "KG")  ->  (Decimal('NaN'), Decimal('1'))

istisna YOK. O NaN gerçek PostgreSQL 16.14'te `products.stock`a YAZILDI ve
NaN olarak geri okundu. Üç katman birden kaçırıyordu, üçü de ölçüldü:

  1. Kapı TİP kapısıydı, SONLULUK kapısı değil.
  2. Sıfır-ürün reddi `urun == 0` sorar; `NaN == 0` FALSE'tur, yani NaN
     reddin İÇİNDEN değil ÜSTÜNDEN geçer.
  3. Inf/-Inf/sNaN `quantize`ta `decimal.InvalidOperation` atıyordu — o da
     `BirimCozulemedi` AİLESİNİN DIŞINDADIR ve bu dosyanın her iki çağıran
     için belgelediği `except BirimCozulemedi:` sözleşmesinden KAÇAR.
     Gerçek bir girdinin kaçtığı bir aile sözleşme değildir.

İKİ FARKLI `sebep`, VE BU BİLİNÇLİ BİR KARARDIR. PR 2 `sebep` üzerinden
yönlendirir (outbox'ta adı konmuş atlama kovası, etkileşimli yazmada red),
bu yüzden yanlış yönlendiren bir sebep REDDETSE BİLE kusurdur:

  * MİKTAR sonlu değilse -> `MIKTAR_SONLU_DEGIL` (YENİ). Bu bir GİRDİ
    kusurudur: sayıyı o an bir insan yazdı. Çaresi operatörün YENİDEN
    GİRMESİDİR — etkileşimli yazmada 4xx, outbox'ta o kayda ait atlama.
    Hiçbir defter satırı bozuk DEĞİLDİR.
  * KATSAYI sonlu değilse -> `KATSAYI_GECERSIZ` (MEVCUT, yeni değil). Bu bir
    DEFTER kusurudur: `product_unit_factors` satırı bozuktur ve operatörün
    yeniden girmesi onu DÜZELTMEZ; birinin DEFTERİ düzeltmesi gerekir. Çare
    mevcut geçersiz-katsayı vakalarıyla (Decimal olmayan, pozitif olmayan)
    AYNIDIR, o yüzden aynı kovaya girer.

NİYE KATSAYI İÇİN YENİ SEBEP AÇILMADI — ÖLÇÜLMÜŞ GEREKÇE: `-Inf` katsayısı
FIX'TEN ÖNCE BİLE doğru kovaya düşüyordu (`-Inf <= 0` TRUE'dur, ölçüldü),
yani `KATSAYI_GECERSIZ`. Sonlu olmayan katsayılara ayrı bir sebep açmak
`-Inf`i KOVA DEĞİŞTİRTİRDİ: bugün doğru yönlendirilen bir girdi, kusuru
düzelten bir yamada yönünü değiştirirdi. Tek bir çare iki kovaya bölünmez.

VERİTABANI KATMANI AYRICA ONARILDI: `CHECK (factor > 0)` sonlu olmayanı
YAKALAMIYORDU (PostgreSQL `NaN`ı her sonlu sayının üstüne sıralar). Bkz.
göç `20260902_0066`; kısıt artık `factor <> 'NaN'::numeric` de içeriyor.

--- SIFIR OLMAYAN GİRİŞ SIFIR ÜRÜNE DÜŞERSE REDDEDİLİR --------------------

`1 GRAM -> TON` 4 basamakta `0.0000`dır. PR 1 bunu ölçüp BULGU olarak
adlandırmış ve kararı sahibe bırakmıştı; sahip kararı 3 REDDİR. Dar koşul:

    entered_quantity != 0  AND  base_quantity == 0   ->  raise

Ayrı bir istisna (`UrunTemsilEdilemez`): birim ÇÖZÜLDÜ, ürün TEMSİL
EDİLEMİYOR. Bunlar farklı hatalardır ve PR 2'nin çağıranı ikisini farklı
yönlendirmek isteyecektir. Gerekçe, kayıt talimatı değil akıl yürütmeyi
taşısın diye:

1. DÜŞMAN SESSİZLİKTİR, HASSASİYETSİZLİK DEĞİL. Sıfır olmaması gereken bir
   sıfır SESSİZ bir kayıptır; bu proje aynı oturumda o sınıftan üç kez
   ısırıldı (1000x hasat hatası, tabana yuvarlanan PHI günü, yarıda kesilen
   ifadeden sonra kaybolan satırlar).
2. BU ÇÖZÜCÜYÜ HENÜZ KİMSE ÇAĞIRMIYOR. Bugün raise etmenin maliyeti sıfırdır.
3. RED, KARARI KORUR. PR 2'nin çağıranı yakalar ve seçer — outbox için adı
   konmuş atlama kovası, etkileşimli yazma için 4xx. `base_unit IS NULL`
   için zaten karar verilmiş şeklin aynısı.
4. SESSİZ SIFIR KARARI KAPATIR. Kurtarma yoktur. Red kurtarılabilir:
   operatör temsil edilebilir bir birimle yeniden girer.
5. SÖZLEŞMEYİ DEĞİŞTİRMEZ. "Ürün 4 basamaktır" duruyor. Red o sözleşme
   uygulanmadan ÖNCE, 4 basamağın girdiyi HİÇ temsil edemediği durumda ateşler.

Gerçek bir sıfır GERÇEK bir giriştir ve gerçek bir sıfır ürün verir:
`resolve(0, "KG", "KG")` reddedilmez. Koşulun `entered_quantity != 0`
yarısı tam olarak bunun içindir.

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

DİKKAT — BU KATLAMANIN BİR KARDEŞİ VARDIR VE İKİSİ BİLEREK AYRIDIR.
`claude/bitki-esitligi-tr` dalı (`f1e8ca4`, PR #25) `_bitki_katla`yı
`routers/farm.py`de kurdu ve o dal `origin/develop`e BİRLEŞTİ (ölçüldü:
`caf4114`, "Merge pull request #25"). Bu dosyanın ilk hâli "o dal indiğinde
bu kopya silinir, ortak olan çağrılır" diyordu. ÖLÇÜLDÜ VE YANLIŞ ÇIKTI:
iki katlama AYNI DENKLİĞİ ÜRETMİYOR, dolayısıyla BİRLEŞTİRİLEMEZ.

  * Bu fonksiyon YUKARI katlar (`.replace("i", "İ")` + `.upper()`):
    KAPALI bir kümedeki BİRİM KODU aramasıdır, sözleşmesi kanonik BÜYÜK
    biçimdir.
  * `_bitki_katla` AŞAĞI katlar (`.lower()`, `I`+U+0307 ve `İ` özel
    işlemiyle): SERBEST METİN bitki adı EŞİTLİĞİDİR, sözleşmesi ön yüzün
    `toLocaleLowerCase('tr')`ıyla İKİZ kalmaktır.
  * Ayrıştıkları iki çift ölçüldü (bkz. `routers/farm.py`, `_bitki_katla`
    üstündeki yorum): "Weißkohl"/"weisskohl" YUKARIDA eşit, AŞAĞIDA değil
    (`.upper()` ß'yi "SS"e açar); "I"+U+0307+"stanbul"/"İstanbul" AŞAĞIDA
    eşit, YUKARIDA değil. "İncir"/"incir" gibi örtüşen çiftlere bakıp
    "aynılar" demek MÜMKÜN ve YANLIŞTIR.

Birini diğerine çağırtmak ya da ortak bir yardımcıda toplamak DAVRANIŞ
DEĞİŞİKLİĞİDİR ve kendi PR'ını, kendi kayıp analizini gerektirir. Tarihsel
gerekçe yine geçerli — İ/ı katlaması bu depoda üç katmanda AYRIŞMIŞTI — ama
çare "tek kopya" değil, "iki kopyanın FARKI adıyla ve ölçümüyle kayıtlı".
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
    MIKTAR_SONLU_DEGIL = "MIKTAR_SONLU_DEGIL"

    def __init__(self, sebep: str, mesaj: str) -> None:
        super().__init__(f"{sebep}: {mesaj}")
        self.sebep = sebep


class UrunTemsilEdilemez(BirimCozulemedi):
    """Birim ÇÖZÜLDÜ ama ürün 4 basamakta TEMSİL EDİLEMİYOR: sıfır olmayan
    bir giriş sıfır ürüne düşüyordu. Sahip kararı 3 — bkz. başlık.

    `BirimCozulemedi` ailesindedir (aynı `except` onu da yakalar, `.sebep`
    üzerinden de ayırt edilir) ama KENDİ ADI vardır, çünkü "birimi
    bilmiyorum" ile "birimi biliyorum, sayı sığmıyor" farklı hatalardır ve
    çağıran ikisini farklı yönlendirecektir.

    Girilen miktar ve kullanılan katsayı YUVARLANMADAN taşınır: red bir
    kayıt olayıdır ve kanıtı yanında gitmelidir.
    """

    URUN_TEMSIL_EDILEMEZ = "URUN_TEMSIL_EDILEMEZ"

    def __init__(
        self,
        entered_quantity: Decimal,
        entered_unit: str,
        base_unit: str,
        factor_used: Decimal,
    ) -> None:
        super().__init__(
            self.URUN_TEMSIL_EDILEMEZ,
            f"{entered_quantity} {entered_unit!r} -> {base_unit!r}: katsayı "
            f"{factor_used} ile ürün {URUN_KUANTUM} ölçeğinde sıfıra düştü. "
            "Sıfır olmayan bir giriş sıfır ürüne dönüşemez; sessiz bir sıfır "
            "yerine red. Temsil edilebilir bir birimle yeniden girin.",
        )
        self.entered_quantity = entered_quantity
        self.entered_unit = entered_unit
        self.base_unit = base_unit
        self.factor_used = factor_used


def turkce_katla(metin: str) -> str:
    """Türkçe büyütme: `i -> İ`, `ı -> I`, gerisi standart.

    KARDEŞİ VAR, BİRLEŞTİRİLMEZ — bkz. başlıktaki "TÜRKÇE KATLAMA" bölümü.
    `f1e8ca4` (PR #25) `origin/develop`e İNDİ ve ölçüm "ortak olan
    çağrılır" planını çürüttü: `routers/farm.py`deki `_bitki_katla` AŞAĞI
    katlar ve iki katlama aynı denkliği üretmiyor. Bu fonksiyon KALIR.

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
        # --- SONLULUK, `<= 0` KARŞILAŞTIRMASINDAN ÖNCE --------------------
        # SIRA ZORUNLUDUR, üslup değildir: Python'da `Decimal("NaN") <= 0`
        # KARŞILAŞTIRMANIN KENDİSİ `decimal.InvalidOperation` ATAR. Denetim
        # `<= 0`dan SONRA konsaydı NaN ona HİÇ ULAŞAMAZ ve aile dışı istisna
        # sızmaya DEVAM ederdi. ÖLÇÜLDÜ (fix'ten önce): NaN/sNaN katsayısı
        # `InvalidOperation`, Inf ise `quantize`ta yine `InvalidOperation`;
        # YALNIZ `-Inf` (çünkü `-Inf <= 0` TRUE'dur) zaten doğru kovadaydı.
        #
        # SEBEP `KATSAYI_GECERSIZ`, YENİ BİR SEBEP DEĞİL — bkz. başlıktaki
        # "SONLU OLMAYAN SAYILAR": çaresi mevcut geçersiz-katsayı vakalarıyla
        # AYNIDIR (defter satırı düzeltilir) ve `-Inf` bugün zaten bu kovada;
        # ayrı bir sebep onu KOVA DEĞİŞTİRTİRDİ.
        if not katsayi.is_finite():
            raise BirimCozulemedi(
                BirimCozulemedi.KATSAYI_GECERSIZ,
                f"{birim!r} katsayısı SONLU değil: {katsayi}. Bir katsayı "
                "firmanın ÖLÇÜLMÜŞ beyanıdır; NaN ve sonsuzluk beyan değildir.",
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

    Sıfır olmayan bir giriş 4 basamakta sıfıra düşerse ``UrunTemsilEdilemez``
    atılır (sahip kararı 3). Sıfır giriş sıfır ürün verir, reddedilmez.

    SONLU OLMAYAN girdiler REDDEDİLİR ve reddin tamamı ``BirimCozulemedi``
    AİLESİNİN İÇİNDEDİR — ``decimal.InvalidOperation`` DIŞARI SIZMAZ. Miktar
    ``MIKTAR_SONLU_DEGIL``, katsayı ``KATSAYI_GECERSIZ`` sebebiyle reddedilir;
    ikisinin AYRI olmasının gerekçesi başlıktaki "SONLU OLMAYAN SAYILAR"
    bölümündedir (biri GİRDİ kusuru, öteki DEFTER kusuru — çareleri farklı).
    """
    if not isinstance(entered_quantity, Decimal):
        raise BirimCozulemedi(
            BirimCozulemedi.KATSAYI_GECERSIZ,
            "entered_quantity Decimal olmalı, "
            f"{type(entered_quantity).__name__} verildi.",
        )
    # --- SONLULUK KAPISI, TİP KAPISININ YANINDA ---------------------------
    # `Decimal` OLMAK yetmez: `Decimal("NaN")`, `Decimal("Infinity")`,
    # `Decimal("-Infinity")` ve `Decimal("sNaN")` hepsi `Decimal`dir ve tip
    # kapısından GEÇER. Bunlar `Decimal(<kullanıcı metni>)` ile ÜRETİLEBİLİR
    # — PR 2'nin çağıranı gövdeden tam olarak böyle ayrıştıracaktır.
    #
    # NİYE SIFIR-ÜRÜN REDDİ BUNU YAKALAMAZ: o red `urun == 0` sorar ve
    # `NaN == 0` FALSE'tur (`NaN != 0` da TRUE'dur), yani NaN reddin İÇİNDEN
    # DEĞİL ÜSTÜNDEN geçer. ÖLÇÜLDÜ (fix'ten önce):
    #     resolve(Decimal("NaN"), "KG", "KG") -> (Decimal('NaN'), Decimal('1'))
    # istisna YOK — ve o NaN gerçek PostgreSQL 16.14'te `products.stock`a
    # yazılıp NaN olarak geri okundu.
    #
    # NİYE `quantize`ın kendi hatasına GÜVENİLMEZ: Inf/-Inf/sNaN için
    # `quantize` `decimal.InvalidOperation` atar, o da `BirimCozulemedi`
    # AİLESİNİN DIŞINDADIR ve belgelenmiş `except BirimCozulemedi:`
    # sözleşmesinden KAÇAR. Gerçek bir girdinin kaçtığı bir aile sözleşme
    # değildir; kapı bu yüzden burada, ailenin İÇİNDE.
    if not entered_quantity.is_finite():
        raise BirimCozulemedi(
            BirimCozulemedi.MIKTAR_SONLU_DEGIL,
            f"entered_quantity SONLU değil: {entered_quantity}. NaN ve "
            "sonsuzluk ÖLÇÜLMEMİŞ sayılardır; stoka giremezler. Sıfır-ürün "
            "reddi bunu yakalayamaz çünkü NaN hiçbir şeye EŞİT DEĞİLDİR.",
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

    # --- SIFIR OLMAYAN GİRİŞ SIFIR ÜRÜNE DÜŞTÜ: REDDET, SESSİZ SIFIR YOK ----
    # Sözleşme uygulandı (ürün 4 basamak), sonuç girdiyi HİÇ temsil etmiyor.
    # Gerçek bir sıfır giriş (`entered_quantity == 0`) buradan GEÇER; onun
    # sıfır ürünü gerçektir.
    if entered_quantity != 0 and urun == 0:
        raise UrunTemsilEdilemez(entered_quantity, girilen_birim, taban_birim, katsayi)
    return urun, katsayi
