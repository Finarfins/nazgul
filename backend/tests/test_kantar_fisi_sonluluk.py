"""SONLU OLMAYAN SAYILAR kantar fişi yoluna GİREMEZ — ÜÇ KATMAN, ÜÇ MUTASYON.

Bu dosya bir HATA DÜZELTMESİ DEĞİL, bir SÖZLEŞMEDİR. Ölçüldü ve raporlandı:
bugün hiçbir katman 500 üretmiyor. Ama korumanın İKİ katmanı da BAŞKA bir
kararın yan ürünü, yani kırılgan:

  (a) ŞEMA — Pydantic 2.13.5 dördünü de 422 yapıyor ve koruma İKİ
      BAĞIMSIZ nöbetçiden geliyor. ÖLÇÜLDÜ (dört kombinasyon, tek tabloda):

          allow_inf_nan  sınır      NaN   Infinity   -Infinity
          --------------------------------------------------
          varsayılan     gt+le      422   422        422
          varsayılan     gt         422   422        422
          True           gt+le      422   422        422
          True           gt         422   KABUL      422

      Yani "Infinity" YALNIZ İKİSİ BİRDEN bozulursa geçer: `allow_inf_nan`
      açılacak VE üst sınır (`le=MAX_MIKTAR`) kaldırılacak. Tek başına
      `allow_inf_nan=True` YETMEZ, çünkü `Infinity <= MAX_MIKTAR` YANLIŞTIR
      ve üst sınır onu bağımsız olarak eler.

      NaN ve -Infinity HER durumda 422 kalır ve sebepleri AYRIDIR: `NaN > 0`
      bir cevap değil bir HATADIR (sınır "geçmedi" sayar), `-Infinity > 0`
      ise düpedüz yanlıştır. Bu ASİMETRİ aşağıda ayrıca çivilendi ki
      mutasyon koşulduğunda "niye yalnız Infinity düştü?" sorusu kayıttan
      cevaplansın.

  (b) ÇÖZÜCÜ — `units.resolve` kendi sonluluk kapısını taşıyor ve reddi
      AİLE İÇİNDEDİR (`BirimCozulemedi.MIKTAR_SONLU_DEGIL`). (a) gevşetilse
      bile bu katman tutar; `decimal.InvalidOperation` DIŞARI SIZMAZ.

  (c) UÇ — ikisinin BİRLEŞİK sonucu: istek 4xx alır, gövde `sebep` taşır ve
      HİÇBİR SATIR YAZILMAZ. Üçüncü katman ayrıca ölçülüyor çünkü (a) ve (b)
      ayrı ayrı yeşilken uç yine de yarım bir satır bırakabilirdi — çözüm
      SQL'den ÖNCE çağrılmazsa.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.farm_schemas import HarvestTicketDeductionWrite, HarvestTicketWrite
from app.units import BirimCozulemedi, resolve

SONLU_OLMAYANLAR = ["NaN", "sNaN", "Infinity", "-Infinity"]


# ===========================================================================
# (a) ŞEMA KATMANI
# ===========================================================================

@pytest.mark.parametrize("ham", SONLU_OLMAYANLAR)
def test_SEMA_brut_SONLU_OLMAYANI_REDDEDER(ham: str) -> None:
    """`gross_entered_quantity` dördünü de 422 yapar.

    MUTASYON (ÖLÇÜLDÜ, İKİSİ BİRDEN gerekir): `_Taban`ın `ConfigDict`ine
    `allow_inf_nan=True` ekle VE `gross_entered_quantity`den
    `le=MAX_MIKTAR`ı kaldır -> YALNIZ `[Infinity]` durumu KIRMIZI olur
    (1 failed / 13 passed). Tek başına `allow_inf_nan=True` HİÇBİR ŞEYİ
    kırmızıya çevirmez — üst sınır bağımsız bir nöbetçidir ve ölçüm bunu
    gösterdi.
    """
    with pytest.raises(ValidationError):
        HarvestTicketWrite(
            harvest_id=1, gross_entered_quantity=ham, entered_unit="KG"
        )


@pytest.mark.parametrize("ham", SONLU_OLMAYANLAR)
def test_SEMA_kesinti_ORANI_da_SONLU_OLMAYANI_REDDEDER(ham: str) -> None:
    """Oran da brüt kadar korunur.

    Sonsuz bir oran türetilen neti EKSİ SONSUZA götürürdü; brütü koruyup
    oranı korumamak, kapıyı yarım açık bırakmak olurdu.
    """
    with pytest.raises(ValidationError):
        HarvestTicketDeductionWrite(label="rutubet", rate_percent=ham)


def test_SEMA_asimetrisi_NaN_ile_Infinity_AYNI_SEBEPLE_DUSMUYOR() -> None:
    """ASİMETRİ, ADIYLA: üç değer ÜÇ AYRI nöbetçiye takılıyor.

    ÖLÇÜLDÜ, akıl yürütülmedi:

      * `NaN`       -> ALT sınır. `Decimal("NaN") > 0` bir cevap değil bir
        HATADIR (`InvalidOperation`), sınır onu "geçmedi" sayar. Bu yüzden
        NaN `allow_inf_nan` ne olursa olsun 422 kalır.
      * `-Infinity` -> ALT sınır, düpedüz: `-Inf > 0` YANLIŞTIR.
      * `Infinity`  -> ÜST sınır. `Inf > 0` DOĞRUDUR, yani alt sınırı GEÇER;
        onu eleyen şey `le=MAX_MIKTAR`dır (`Inf <= MAX` yanlıştır).

    Bu, mutasyon tablosunun açıklamasıdır: `allow_inf_nan=True` tek başına
    hiçbir şeyi kırmızıya çeviremez, çünkü üç değerin üçü de HÂLÂ bir
    sınıra takılır. Yalnız ÜST sınır da kaldırılırsa `Infinity` geçer.
    """
    import decimal

    # `Infinity` ALT sınırı geçer — onu eleyen şey ÜST sınırdır.
    assert Decimal("Infinity") > 0
    assert not (Decimal("Infinity") <= Decimal("99999999999999.9999"))
    # `-Infinity` alt sınırda düpedüz düşer.
    assert not (Decimal("-Infinity") > 0)
    # `NaN > 0` bir cevap DEĞİL, bir HATADIR — sınır onu "geçmedi" sayar.
    with pytest.raises(decimal.InvalidOperation):
        _ = Decimal("NaN") > 0


# ===========================================================================
# (b) ÇÖZÜCÜ KATMANI — (a) gevşetilirse ARKA DURAK
# ===========================================================================

@pytest.mark.parametrize("ham", SONLU_OLMAYANLAR)
def test_COZUCU_SONLU_OLMAYANI_AILE_ICINDE_REDDEDER(ham: str) -> None:
    """`units.resolve` -> `BirimCozulemedi(MIKTAR_SONLU_DEGIL)`.

    AİLE İÇİ olması şart: `decimal.InvalidOperation` sızsaydı uç 500
    verirdi ve red, belgelenmiş `except BirimCozulemedi:` sözleşmesinden
    KAÇARDI.

    MUTASYON: `units.resolve`daki `is_finite()` kapısını kaldır -> bu dört
    durum KIRMIZI olur.
    """
    with pytest.raises(BirimCozulemedi) as hata:
        resolve(Decimal(ham), "KG", "KG")
    assert hata.value.sebep == BirimCozulemedi.MIKTAR_SONLU_DEGIL, hata.value.sebep


def test_COZUCU_reddi_InvalidOperation_SIZDIRMIYOR() -> None:
    """Red `BirimCozulemedi` ailesinin İÇİNDE; `decimal` istisnası dışarı çıkmaz."""
    import decimal

    for ham in SONLU_OLMAYANLAR:
        try:
            resolve(Decimal(ham), "KG", "KG")
        except BirimCozulemedi:
            pass
        except decimal.InvalidOperation as exc:  # pragma: no cover
            pytest.fail(f"{ham}: aile DIŞI istisna sızdı: {exc!r}")
