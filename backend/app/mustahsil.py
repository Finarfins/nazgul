"""Müstahsil makbuzunun ARİTMETİĞİ — saf, SQL'siz, oturumsuz.

Bu modül veritabanına DOKUNMAZ ve bir `Session` görmez. Gerekçe: makbuzun
sayıları bir sözleşmedir ve sözleşme, onu saklayan tablodan BAĞIMSIZ
sınanabilmelidir. Uç (`routers/mustahsil.py`) bu fonksiyonları çağırır ve
DÖNENİ yazar; kendi başına hiçbir tutar hesaplamaz.

--- FORMÜL ------------------------------------------------------------------

Satır başına:

    line_gross            = base_quantity × unit_price
    withholding_amount    = line_gross × withholding_rate / 100
    social_security_amount= line_gross × social_security_rate / 100
    line_net              = line_gross − withholding_amount − social_security_amount

Başlıkta:

    gross_amount          = Σ line_gross
    withholding_total     = Σ withholding_amount
    social_security_total = Σ social_security_amount
    net_payable           = Σ line_net

--- KDV YOKTUR VE BU BİR EKSİKLİK DEĞİLDİR ---------------------------------

Müstahsil makbuzunda KDV HANESİ YOKTUR: satıcı çiftçi KDV mükellefi
değildir. `transactions.py`in alım formülü KDV'yi FİYATA DAHİL kabul eder
ve tutardan AYRIŞTIRIR; o formülü buraya kopyalamak, olmayan bir vergiyi
düşer ve `net_payable` sessizce EKSİK çıkardı. Buradaki `unit_price`
KDV'siz DEĞİL, KDV'sizdir çünkü ortada KDV YOKTUR — ikisi aynı şey değil:
birincisi bir ayrıştırma, ikincisi bir yokluk.

--- YUVARLAMA SATIRDA, BİR KEZ; BAŞLIKTA ASLA ------------------------------

Dört satır değeri de ROUND_HALF_UP ile `MONEY_QUANTUM`a (0.01) yuvarlanır.
Başlığın toplamları YUVARLANMIŞ satır değerlerinin toplamıdır ve TEKRAR
yuvarlanmaz.

Niye tekrar yuvarlanmıyor: yuvarlanmış sayıların toplamı zaten 0.01'in
katıdır, yani ikinci bir `quantize` hiçbir şeyi değiştirmez — ama kodu
okuyan biri onu "gerekli" sanır ve bir gün toplamı YUVARLANMAMIŞ satırlar
üzerinden almaya kalkarsa (`Σ(q×p)` sonra yuvarla) başlık ile satırların
toplamı AYRIŞIR. n satırda her biri 0.005 sapan bir makbuz, kendi içinde
tutmayan bir kağıttır: `net_payable ≠ Σ line_net` olur ve bunu kimse fark
etmez çünkü iki sayı da "doğru yuvarlanmış" görünür.

Bu yüzden `makbuz_topla` toplamı SATIR SONUÇLARINDAN alır ve `money()`
ÇAĞIRMAZ. Bu bir ihmal değil, KARARDIR.

--- `line_net` ÇIKARMAYLA BULUNUR, YENİDEN ÇARPILMAZ ------------------------

`line_net = gross − withholding − ss` — yani ZATEN YUVARLANMIŞ üç sayının
farkı. Alternatif `gross × (1 − (w+ss)/100)` idi ve REDDEDİLDİ: o hesap
`line_net + withholding + ss = line_gross` eşitliğini BOZABİLİR (üç ayrı
yuvarlama bağımsız yapılınca 0.01 açık kalır). Makbuzun okuyucusu bu
eşitliği gözle denetler; tutmayan bir kağıt açıklanamaz.

--- SONLULUK KAPISI KARŞILAŞTIRMADAN ÖNCE ----------------------------------

`Decimal("NaN")` bir `Decimal`dir ve tip kapısından GEÇER; `NaN >= 0` da
FALSE'tur, yani "negatif mi" diye soran bir kapı NaN'ı NEGATİF SANIP
reddeder — doğru sonuç, YANLIŞ sebeple. Sonsuzluk ise aralık kapısından
GEÇER (`Infinity >= 0` TRUE) ve `quantize` üzerinde `InvalidOperation`
atar — o da bu modülün sözleşmesinin DIŞINDADIR.

Bu yüzden sonluluk HER alanda, aralık karşılaştırmasından ÖNCE sorulur ve
reddin tamamı `MustahsilHatasi` AİLESİNİN İÇİNDEDİR. Aynı duruş
`app/units.py`in `MIKTAR_SONLU_DEGIL` kapısıdır.

--- ORANLAR BURADA SABİT DEĞİLDİR -------------------------------------------

Bu modülde yasal bir stopaj ya da Bağ-Kur oranı SABİTİ YOKTUR. Oran
ÇAĞIRANDAN gelir ve satırın üstünde saklanır. Gerekçe göç 0070'in
başlığındadır: oran tebliğe ve ürün cinsine göre değişir; koda gömülen bir
sayı, tebliğ değiştiği gün hangi satırın hangi oranla yazıldığını
OKUNAMAZ hâle getirirdi.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from .money import HUNDRED, MONEY_QUANTUM, ZERO

_ORAN_TAVANI = Decimal("100")


class MustahsilHatasi(Exception):
    """Hesap bir olgu UYDURMAK yerine durdu. `sebep` hangisi olduğunu söyler.

    `units.BirimCozulemedi` ile aynı biçim: sebepler ÇAĞIRAN için ayırt
    edicidir, çünkü uç onları farklı 422 gövdelerine çevirir.
    """

    SAYI_SONLU_DEGIL = "SAYI_SONLU_DEGIL"
    ORAN_ARALIK_DISI = "ORAN_ARALIK_DISI"
    MIKTAR_GECERSIZ = "MIKTAR_GECERSIZ"
    FIYAT_GECERSIZ = "FIYAT_GECERSIZ"

    def __init__(self, sebep: str, mesaj: str) -> None:
        super().__init__(f"{sebep}: {mesaj}")
        self.sebep = sebep


@dataclass(frozen=True)
class SatirSonucu:
    """Bir kalemin dört para değeri; hepsi 0.01'e yuvarlanmış."""

    line_gross: Decimal
    withholding_amount: Decimal
    social_security_amount: Decimal
    line_net: Decimal


@dataclass(frozen=True)
class MakbuzSonucu:
    """Başlığın dört toplamı; SATIRLARIN toplamı, YENİDEN yuvarlanmamış."""

    gross_amount: Decimal
    withholding_total: Decimal
    social_security_total: Decimal
    net_payable: Decimal


def _para(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _sonlu(value: Decimal, alan: str) -> Decimal:
    """Tip VE sonluluk kapısı — aralık sorularının HEPSİNDEN önce.

    `float` KABUL EDİLMEZ: ikili kayan nokta bir para tutarına giremez.
    """
    if not isinstance(value, Decimal):
        raise MustahsilHatasi(
            MustahsilHatasi.SAYI_SONLU_DEGIL,
            f"{alan} Decimal olmalı, {type(value).__name__} verildi.",
        )
    if not value.is_finite():
        raise MustahsilHatasi(
            MustahsilHatasi.SAYI_SONLU_DEGIL,
            f"{alan} SONLU değil: {value}. NaN ve sonsuzluk ÖLÇÜLMEMİŞ "
            "sayılardır; bir makbuza giremezler. Aralık kapısı bunu "
            "yakalayamaz çünkü NaN hiçbir karşılaştırmada TRUE vermez.",
        )
    return value


def _oran(value: Decimal, alan: str) -> Decimal:
    _sonlu(value, alan)
    if value < ZERO or value > _ORAN_TAVANI:
        raise MustahsilHatasi(
            MustahsilHatasi.ORAN_ARALIK_DISI,
            f"{alan} 0 ile 100 arasında olmalı, {value} verildi.",
        )
    return value


def satir_hesapla(
    base_quantity: Decimal,
    unit_price: Decimal,
    withholding_rate: Decimal,
    social_security_rate: Decimal,
) -> SatirSonucu:
    """Bir kalemin brütünü, iki kesintisini ve netini hesaplar.

    ``base_quantity`` ÜRÜNÜN TABAN BİRİMİNDEDİR (0066); bu fonksiyon birim
    çözmez — çağıran `units.resolve` ile çözüp SONUCU buraya verir.

    Dört değer de 0.01'e ROUND_HALF_UP ile yuvarlanır ve
    ``line_net + withholding + ss == line_gross`` eşitliği KORUNUR (net,
    çarpımla değil ÇIKARMAYLA bulunur — bkz. başlık).
    """
    _sonlu(base_quantity, "base_quantity")
    _sonlu(unit_price, "unit_price")
    if base_quantity < ZERO:
        raise MustahsilHatasi(
            MustahsilHatasi.MIKTAR_GECERSIZ,
            f"base_quantity negatif olamaz: {base_quantity}.",
        )
    if unit_price < ZERO:
        raise MustahsilHatasi(
            MustahsilHatasi.FIYAT_GECERSIZ,
            f"unit_price negatif olamaz: {unit_price}.",
        )
    stopaj_orani = _oran(withholding_rate, "withholding_rate")
    sgk_orani = _oran(social_security_rate, "social_security_rate")

    brut = _para(base_quantity * unit_price)
    # Kesintiler YUVARLANMIŞ brütten hesaplanır, ham çarpımdan değil:
    # makbuzu okuyan kişi elinde brütü görür ve oranı ONA uygular. Ham
    # çarpımdan gitmek, gözle denetlenemeyen bir kuruş üretebilirdi.
    stopaj = _para(brut * stopaj_orani / HUNDRED)
    sgk = _para(brut * sgk_orani / HUNDRED)
    net = brut - stopaj - sgk
    return SatirSonucu(
        line_gross=brut,
        withholding_amount=stopaj,
        social_security_amount=sgk,
        line_net=net,
    )


def makbuz_topla(satirlar: list[SatirSonucu]) -> MakbuzSonucu:
    """Başlığın toplamları — SATIRLARIN toplamı, YENİDEN YUVARLANMAZ.

    Burada `money()` ÇAĞRILMAZ ve bu bir ihmal DEĞİLDİR: girdiler zaten
    0.01'in katıdır, ikinci bir yuvarlama hiçbir şeyi değiştirmez ama onu
    "gerekli" sanan biri bir gün toplamı yuvarlanmamış satırlardan almaya
    kalkar ve başlık satırlarla AYRIŞIR. Bkz. başlıktaki bölüm.

    Boş liste dört SIFIR verir, reddedilmez: kalemsiz bir taslak makbuz
    geçerli bir durumdur (kalem zorunluluğu `issue` kapısındadır, burada
    değil).
    """
    brut = sum((s.line_gross for s in satirlar), ZERO)
    stopaj = sum((s.withholding_amount for s in satirlar), ZERO)
    sgk = sum((s.social_security_amount for s in satirlar), ZERO)
    net = sum((s.line_net for s in satirlar), ZERO)
    return MakbuzSonucu(
        gross_amount=brut,
        withholding_total=stopaj,
        social_security_total=sgk,
        net_payable=net,
    )
