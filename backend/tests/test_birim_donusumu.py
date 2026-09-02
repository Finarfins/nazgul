"""Birim çözücüsünün birim testleri (`app/units.py`). ÇÖZÜCÜYÜ ÇAĞIRAN YOK.

Bu dosya, PR 1'de çözücünün TEK kapsamıdır — ne bir yol ne bir ekran onu
çağırıyor. Bu yüzden buradaki testlerin AYIRT EDİCİ olması, normalden daha
kritiktir: yanlış bir çözücü, başka hiçbir yerde kırmızı üretmez.

İki iddia AYRICA `test_birim_donusumu_postgresql.py`de gerçek PostgreSQL
NUMERIC(18,4)/NUMERIC(24,10) üzerinde ölçülüyor: (a) ÜRÜN ölçeği, (b) REDDİN
gerçekten reddetmesi. İkisi de MUTASYONLA kırmızıya çevrilerek gösterildi.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.units import (
    URUN_KUANTUM,
    BirimCozulemedi,
    UrunTemsilEdilemez,
    resolve,
    turkce_katla,
)


# ===========================================================================
# TÜRKÇE KATLAMA — düz `.upper()` BU DOSYAYI GEÇEMEZ
# ===========================================================================

def test_katlama_i_harfini_NOKTALI_BUYUTUR_duz_upper_GECEMEZ() -> None:
    """`.upper()` ile katlama arasındaki FARKI doğrudan ölçer.

    Bu testin değeri, katlamanın DOĞRU olduğunu göstermesi değil; düz
    `.upper()`ın YANLIŞ olduğunu göstermesidir. `turkce_katla`yı
    `str.upper`la değiştiren biri BURADA kırmızı alır.
    """
    assert turkce_katla("litre") == "LİTRE"
    assert "litre".upper() == "LITRE"
    assert turkce_katla("litre") != "litre".upper()

    assert turkce_katla("i") == "İ"
    assert turkce_katla("ı") == "I"
    # `ı` ve `i` katlandıktan SONRA da ayrı kalmalı; birleşselerdi
    # "MİLİLİTRE" ile hayali bir "MILILITRE" aynı anahtara düşerdi.
    assert turkce_katla("ı") != turkce_katla("i")


def test_katlama_ZATEN_BUYUK_olani_BOZMAZ() -> None:
    """Katlama idempotent olmalı: depoda büyük yazılmış kod da eşleşmeli."""
    for metin in ("LİTRE", "KG", "ÇUVAL", "TON"):
        assert turkce_katla(metin) == metin
        assert turkce_katla(turkce_katla(metin)) == metin


def test_girilen_birim_KUCUK_yazilsa_da_cozulur() -> None:
    """Katlama çözücünün İÇİNDE uygulanıyor, çağıranda değil."""
    assert resolve(Decimal("2"), "litre", "LİTRE")[0] == Decimal("2.0000")
    assert resolve(Decimal("2"), "  LiTrE  ", "litre")[0] == Decimal("2.0000")


def test_urun_katsayisinin_ANAHTARI_da_katlanir() -> None:
    """Depodaki `unit_code` küçük yazılmışsa da bulunmalı."""
    urun, katsayi = resolve(Decimal("2"), "ÇUVAL", "KG", {"çuval": Decimal("33.5")})
    assert urun == Decimal("67.0000")
    assert katsayi == Decimal("33.5")


# ===========================================================================
# OKUMA SIRASI — ÜRÜN SATIRI -> EVRENSEL HARİTA -> RED
# ===========================================================================

def test_URUN_SATIRI_evrensel_haritayi_EZER() -> None:
    """Okuma sırasının TEK ayırt edici testi.

    `TON` evrensel haritada 1000'dir. Ürün 900 beyan ederse 900 kazanmalı.
    Sıra ters çevrilirse (evrensel önce okunursa) burası kırmızı olur —
    başka hiçbir test bunu yakalamaz, çünkü diğer bütün durumlarda iki
    kaynaktan yalnız biri cevap veriyor.
    """
    urun, katsayi = resolve(Decimal("1"), "TON", "KG", {"TON": Decimal("900")})
    assert katsayi == Decimal("900"), "evrensel harita ürünün beyanını EZDİ"
    assert urun == Decimal("900.0000")

    # Beyan YOKKEN aynı çağrı evrensel değeri vermeli — yani 900 gerçekten
    # ÜRÜNDEN geldi, sabit bir tesadüf değil.
    assert resolve(Decimal("1"), "TON", "KG")[1] == Decimal("1000")


def test_EVRENSEL_harita_urun_beyani_YOKKEN_calisir() -> None:
    assert resolve(Decimal("2"), "TON", "KG") == (Decimal("2000.0000"), Decimal("1000"))
    assert resolve(Decimal("1500"), "GRAM", "KG")[0] == Decimal("1.5000")
    assert resolve(Decimal("2"), "KG", "TON")[0] == Decimal("0.0020")
    assert resolve(Decimal("500"), "ML", "LİTRE")[0] == Decimal("0.5000")


def test_TABAN_evrensel_haritada_OLMAK_ZORUNDA_DEGIL() -> None:
    """`çuval` tabanlı bir ürün geçerlidir; taban haritada aranmaz."""
    assert resolve(Decimal("3"), "ÇUVAL", "ÇUVAL")[0] == Decimal("3.0000")
    urun, katsayi = resolve(Decimal("2"), "TORBA", "ÇUVAL", {"TORBA": Decimal("0.5")})
    assert (urun, katsayi) == (Decimal("1.0000"), Decimal("0.5"))


# ===========================================================================
# RED — bu çözücünün VAR OLMA SEBEBİ
# ===========================================================================

def test_TANIMSIZ_birim_REDDEDILIR_1_ile_GECIRILMEZ() -> None:
    """Bilinmeyen bir birim 1.0 ile geçseydi hata GÖRÜNMEZ olurdu."""
    with pytest.raises(BirimCozulemedi) as hata:
        resolve(Decimal("50"), "ÇUVAL", "KG")
    assert hata.value.sebep == BirimCozulemedi.BIRIM_TANIMSIZ


def test_BOYUT_uyusmazligi_REDDEDILIR_yogunluk_UYDURULMAZ() -> None:
    """Litreyi kiloya çevirmek YOĞUNLUK ister; o evrensel değildir."""
    with pytest.raises(BirimCozulemedi) as hata:
        resolve(Decimal("1"), "LİTRE", "KG")
    assert hata.value.sebep == BirimCozulemedi.BOYUT_UYUSMAZLIGI

    # Ama ÜRÜN yoğunluğu beyan ederse çözülür — red evrensel haritanın
    # susmasıdır, kalıcı bir yasak değil.
    assert resolve(Decimal("1"), "LİTRE", "KG", {"LİTRE": Decimal("0.92")})[
        1
    ] == Decimal("0.92")


@pytest.mark.parametrize("taban", [None, "", "   "])
def test_TABAN_BILDIRILMEMIS_reddedilir_girilen_taban_SAYILMAZ(taban) -> None:
    """Sahip kararı 2: girileni taban saymak bir OLGU UYDURMAK olurdu."""
    with pytest.raises(BirimCozulemedi) as hata:
        resolve(Decimal("50"), "ÇUVAL", taban)
    assert hata.value.sebep == BirimCozulemedi.TABAN_BILDIRILMEMIS


def test_taban_NULL_iken_HICBIR_birim_kabul_edilmez() -> None:
    """Taban bilinmiyorsa "aynı birim" kısayolu da YOKTUR.

    Bu ayrım önemli: `base_unit is None` denetimi eşitlik kısayolundan SONRA
    yapılsaydı, tabanı bildirilmemiş bir üründe `entered_unit='KG'` sessizce
    geçerdi ve "KG cinsinden" olduğu UYDURULMUŞ olurdu.
    """
    with pytest.raises(BirimCozulemedi) as hata:
        resolve(Decimal("1"), "KG", None)
    assert hata.value.sebep == BirimCozulemedi.TABAN_BILDIRILMEMIS


@pytest.mark.parametrize("katsayi", [Decimal("0"), Decimal("-1")])
def test_POZITIF_OLMAYAN_katsayi_reddedilir(katsayi) -> None:
    """Sıfır her miktarı 0 yapardı; negatif girişi ÇIKIŞA çevirirdi."""
    with pytest.raises(BirimCozulemedi) as hata:
        resolve(Decimal("5"), "ÇUVAL", "KG", {"ÇUVAL": katsayi})
    assert hata.value.sebep == BirimCozulemedi.KATSAYI_GECERSIZ


def test_BOS_birim_reddedilir() -> None:
    with pytest.raises(BirimCozulemedi) as hata:
        resolve(Decimal("1"), "   ", "KG")
    assert hata.value.sebep == BirimCozulemedi.BIRIM_TANIMSIZ


# ===========================================================================
# DECIMAL-ONLY — float bir stok sayısına GİREMEZ
# ===========================================================================

def test_float_miktar_REDDEDILIR() -> None:
    with pytest.raises(BirimCozulemedi) as hata:
        resolve(0.1, "KG", "KG")  # type: ignore[arg-type]
    assert hata.value.sebep == BirimCozulemedi.KATSAYI_GECERSIZ


def test_float_KATSAYI_reddedilir() -> None:
    """Depodan `float` gelseydi ikili yuvarlama sessizce stoka girerdi."""
    with pytest.raises(BirimCozulemedi) as hata:
        resolve(Decimal("1"), "ÇUVAL", "KG", {"ÇUVAL": 33.5})  # type: ignore[dict-item]
    assert hata.value.sebep == BirimCozulemedi.KATSAYI_GECERSIZ


def test_evrensel_harita_DONMUS_disaridan_degistirilemez() -> None:
    """Harita bir modül sabitidir; bir çağıran ona birim EKLEYEMEZ."""
    from app.units import _EVRENSEL

    with pytest.raises(TypeError):
        _EVRENSEL["ÇUVAL"] = ("KUTLE", Decimal("33"))  # type: ignore[index]


# ===========================================================================
# ÖLÇEK — ÜRÜN 4 BASAMAK, GİRDİLER ASLA YUVARLANMAZ
# ===========================================================================

def test_URUN_dort_basamaga_ROUND_HALF_UP_ile_yuvarlanir() -> None:
    """Yarım YUKARI: bankacı yuvarlaması (Python varsayılanı) OLMAMALI.

    `0.00005` ROUND_HALF_EVEN'da `0.0000`, ROUND_HALF_UP'ta `0.0001` verir.
    Rounding argümanını kaldıran biri BURADA kırmızı alır.
    """
    urun, _ = resolve(Decimal("0.00005"), "KG", "KG")
    assert urun == Decimal("0.0001")
    assert urun.as_tuple().exponent == URUN_KUANTUM.as_tuple().exponent

    # 0.00015 -> 0.0002 (HALF_UP); HALF_EVEN de 0.0002 verirdi, bu yüzden
    # AYIRT EDİCİ olan yukarıdaki 0.00005'tir ve o burada duruyor.
    assert resolve(Decimal("0.00015"), "KG", "KG")[0] == Decimal("0.0002")


def test_URUN_HER_ZAMAN_tam_dort_basamak_tasir() -> None:
    """Ölçek girdinin ölçeğine göre DEĞİŞMEZ; sabittir."""
    for miktar in ("1", "1.5", "1.123456789", "0"):
        urun, _ = resolve(Decimal(miktar), "KG", "KG")
        assert urun.as_tuple().exponent == -4, f"{miktar} -> {urun}"


def test_KATSAYI_yuvarlanmadan_DONER_urun_olcegine_cekilmez() -> None:
    """Sahip kararı 1'in testi: katsayı bir KANIT, yuvarlanmış kanıt değildir.

    `0.0000012345` ürün ölçeğine (4) çekilseydi `0.0000` olurdu — yani
    katsayının kendisi YOK olurdu. Çözücünün quantize'ı ÜRÜNE uygulanır,
    katsayıya DEĞİL.
    """
    hassas = Decimal("0.0000012345")
    # 1 ÇUVAL burada 0.0000 ürün verir ve sıfır-ürün reddi (aşağıda) onu
    # DOĞRU OLARAK reddeder; bu testin iddiası KATSAYI olduğu için miktar
    # ürünü temsil edilebilir kılacak kadar büyütüldü (PG ikiziyle aynı).
    urun, katsayi = resolve(Decimal("1000000"), "ÇUVAL", "KG", {"ÇUVAL": hassas})
    assert urun == Decimal("1.2345")
    assert katsayi == hassas
    assert katsayi.as_tuple().exponent == hassas.as_tuple().exponent


def test_GIRILEN_MIKTAR_cozucude_hic_yuvarlanmaz() -> None:
    """Girdi 4 basamaktan uzunsa ÜRÜN yuvarlanır, girdi DEĞİL.

    Çözücü `entered_quantity`i döndürmüyor (çağıran zaten elinde tutuyor),
    ama ONU DEĞİŞTİRMEDİĞİ de ölçülmeli: `Decimal` değişmezdir, o yüzden
    burada ölçülen şey aynı nesnenin ürün hesabından sonra da AYNI kalması.
    """
    girdi = Decimal("1.123456789")
    urun, _ = resolve(girdi, "KG", "KG")
    assert girdi == Decimal("1.123456789")
    assert girdi.as_tuple().exponent == -9
    assert urun == Decimal("1.1235")


def test_urun_olcegi_TABANIN_sutunundan_gelir_KEYFI_DEGIL() -> None:
    """`URUN_KUANTUM`, `core_schema.QUANTITY`nin ölçeğiyle AYNI olmalı.

    İkisi ayrışırsa çözücünün ürünü gideceği sütuna sığmaz ya da sütunda
    sessizce yeniden yuvarlanır. Bağ burada AÇIKÇA ölçülüyor.
    """
    from app.core_schema import QUANTITY

    assert QUANTITY.scale == -URUN_KUANTUM.as_tuple().exponent == 4


# ===========================================================================
# SIFIR ÜRÜN — sıfır olmayan giriş SESSİZCE sıfıra düşemez (sahip kararı 3)
# ===========================================================================

def test_SIFIR_OLMAYAN_giris_SIFIR_urune_DUSERSE_REDDEDILIR_sessiz_sifir_YOK() -> None:
    """1 gram -> TON, 4 basamakta `0.0000` OLURDU. Çözücü bunu REDDEDER.

    PR 1 bunu "bilinen davranış" diye çivilemişti ve kararı sahibe
    bırakmıştı. Sahip kararı 3: RED. Düşman sessizliktir, hassasiyetsizlik
    değil — sıfır olmaması gereken bir sıfır, bu projenin aynı oturumda üç
    kez ısırıldığı görünmez-kayıp sınıfındandır.

    İstisna `BirimCozulemedi` AİLESİNDEDİR ama KENDİ ADI vardır: birim
    çözüldü, ürün temsil edilemedi. PR 2'nin çağıranı ikisini farklı
    yönlendirecek.

    AYIRT EDİCİ: `resolve` içindeki `if entered_quantity != 0 and urun == 0`
    reddi kaldırılınca aşağıdaki `pytest.raises` `DID NOT RAISE` ile KIRMIZI.
    """
    with pytest.raises(UrunTemsilEdilemez) as hata:
        resolve(Decimal("1"), "GRAM", "TON")
    assert hata.value.sebep == UrunTemsilEdilemez.URUN_TEMSIL_EDILEMEZ
    assert isinstance(hata.value, BirimCozulemedi), "aynı aileden olmalı"
    # Kanıt istisnanın ÜZERİNDE, yuvarlanmadan gider: miktar ve katsayı.
    assert hata.value.entered_quantity == Decimal("1")
    assert hata.value.factor_used == Decimal("0.000001")
    assert hata.value.entered_unit == "GRAM"
    assert hata.value.base_unit == "TON"

    # Negatif giriş de sıfır olmayan bir giriştir; `-0.0000` da sıfırdır.
    with pytest.raises(UrunTemsilEdilemez):
        resolve(Decimal("-1"), "GRAM", "TON")

    # Ürün katsayısı yoluyla da aynı red: 1 çuval x 0.0000012345 -> 0.0000.
    with pytest.raises(UrunTemsilEdilemez):
        resolve(Decimal("1"), "ÇUVAL", "KG", {"ÇUVAL": Decimal("0.0000012345")})


def test_SIFIR_URUN_reddinin_SINIRI_tam_kuantumdadir() -> None:
    """Red, ürün TEMSİL EDİLEBİLİR hale geldiği an susar; sözleşme değişmedi.

    49 gram -> 0.000049 ton -> `0.0000` (RED); 50 gram -> 0.00005 -> HALF_UP
    ile `0.0001` (GEÇER). Sınır tam `URUN_KUANTUM / 2`dedir, yani red
    ölçekten ayrı bir eşik UYDURMUYOR — ölçeğin kendisini uyguluyor.
    """
    with pytest.raises(UrunTemsilEdilemez):
        resolve(Decimal("49"), "GRAM", "TON")
    assert resolve(Decimal("50"), "GRAM", "TON")[0] == Decimal("0.0001")


@pytest.mark.parametrize(
    ("miktar", "birim", "taban", "katsayilar"),
    [
        ("0", "KG", "KG", None),
        ("0.0", "KG", "KG", None),
        ("0", "GRAM", "TON", None),
        ("0.000", "ÇUVAL", "KG", {"ÇUVAL": Decimal("33.5")}),
    ],
)
def test_GERCEK_SIFIR_giris_GERCEK_SIFIR_urun_verir_RED_YOK(
    miktar, birim, taban, katsayilar
) -> None:
    """Girilen bir sıfır GERÇEK bir giriştir ve gerçek bir sıfır üretir.

    Red koşulunun `entered_quantity != 0` yarısı tam olarak bunun için var:
    koşul yalnız `urun == 0` olsaydı sıfır stok sayımı girilemezdi. Buradaki
    dört durum, reddin GRAM->TON gibi kaba yollarda bile sıfır girişe
    dokunmadığını ölçüyor.
    """
    urun, _ = resolve(Decimal(miktar), birim, taban, katsayilar)
    assert urun == Decimal("0.0000")
    assert urun.as_tuple().exponent == -4
