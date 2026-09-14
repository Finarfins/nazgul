"""Çek/senet portföyünün DURUM MAKİNESİ (CS1). SAF: I/O yok, DB yok.

Geçiş tablosu şefin kararıdır (``docs/prompts/CS1``) ve BURADA TEK
yerdedir; uç katmanı yalnız ``gecis_dogrula``yı çağırır.

    portfoyde        -> tahsile_verildi | ciro_edildi | iade
    tahsile_verildi  -> tahsil_edildi | karsiliksiz | portfoyde
                        (portfoyde = banka işlem yapmadan iade etti)
    karsiliksiz      -> iade  (müşteriye geri verildi)
    tahsil_edildi, ciro_edildi, iade -> SON DURUM (çıkış yok)

Tabloda OLMAYAN her geçiş — aynı duruma "geçiş" dahil — 409
``CEK_GECIS_GECERSIZ``dir. Matris 6x6 = 36 çift; 7'si izinli, 29'u değil
(``tests/test_cs1_cek_senet.py`` 29'unu da HTTP üzerinden dener).

CİRO YALNIZ ALINAN EVRAKTA: firmanın KENDİ verdiği çeki ciro etmesi anlamsızdır
(ciro, elde tutulan bir alacak senedinin devridir). Bu bir durum kuralı
DEĞİL yön kuralıdır ve ayrı kodla (``CEK_CIRO_YALNIZ_ALINAN``) 409 döner.

MUHASEBE YOK (karar 1, CS2): geçiş ``payments``a, ``finance_transactions``a
ya da borç belgelerine YAZMAZ. Ciro edilen tedarikçiye ödeme ve karşılıksız
çek masrafı CS2'nin işidir (karar 3 ve 5); burada yalnız DURUM ve ciro
alanları kaydedilir.
"""
from __future__ import annotations

from typing import Final

PORTFOYDE: Final = "portfoyde"
TAHSILE_VERILDI: Final = "tahsile_verildi"
TAHSIL_EDILDI: Final = "tahsil_edildi"
CIRO_EDILDI: Final = "ciro_edildi"
KARSILIKSIZ: Final = "karsiliksiz"
IADE: Final = "iade"

#: Kapalı küme — göç ``20260914_0085::DURUMLAR`` ile BİREBİR aynı (kapı).
DURUMLAR: Final[tuple[str, ...]] = (
    PORTFOYDE, TAHSILE_VERILDI, TAHSIL_EDILDI, CIRO_EDILDI, KARSILIKSIZ, IADE,
)
TURLER: Final[tuple[str, ...]] = ("cek", "senet")
YONLER: Final[tuple[str, ...]] = ("alinan", "verilen")

#: Kaynak -> gidilebilecek hedeflerin TAM kümesi.
GECISLER: Final[dict[str, frozenset[str]]] = {
    PORTFOYDE: frozenset({TAHSILE_VERILDI, CIRO_EDILDI, IADE}),
    TAHSILE_VERILDI: frozenset({TAHSIL_EDILDI, KARSILIKSIZ, PORTFOYDE}),
    KARSILIKSIZ: frozenset({IADE}),
    TAHSIL_EDILDI: frozenset(),
    CIRO_EDILDI: frozenset(),
    IADE: frozenset(),
}

SON_DURUMLAR: Final[frozenset[str]] = frozenset(k for k, v in GECISLER.items() if not v)

MUHASEBE_HEDEFLERI: Final[frozenset[str]] = frozenset({CIRO_EDILDI, KARSILIKSIZ, IADE})
"""Yalnız :data:`MUHASEBE_ROLLERI`nin gidebildiği hedefler (H48, karar "Çek/senet 4").

Uç izni ``payments``tır ve ``satis`` onu taşır: çekle tahsil edilen satış bir
satıştır, satış çeki portföye ALIR ve portföyü OKUR. Ama bu üç geçiş bir
muhasebe kararıdır, tahsilat değil:

* ``ciro_edildi`` — alacak senedini üçüncü kişiye DEVREDER; ciro anahtarı
  açıksa tedarikçiye ödeme yazar (CS2).
* ``karsiliksiz`` — çekin ödenmediğini TESCİL eder; ödemeli evrakta müşteriye
  borç belgesi açar (CS2).
* ``iade`` — evrakı cariye geri verir; aynı borç belgesi yolunu açar.

``tahsil_edildi`` BİLEREK yok: bankanın ödediğini kaydetmek satışın kapattığı
tahsilattır, bir değerleme ya da devir kararı değildir. ``tahsile_verildi`` ve
``portfoyde`` da yok: ikisi de evrakın elde mi bankada mı olduğunu söyler,
cariye dokunmaz. İleride eklenecek bir silme/iptal hedefi BU kümeye girer.
"""

MUHASEBE_ROLLERI: Final[frozenset[str]] = frozenset({"admin", "yonetici", "muhasebe"})

GECIS_GECERSIZ: Final = "CEK_GECIS_GECERSIZ"
CIRO_YALNIZ_ALINAN: Final = "CEK_CIRO_YALNIZ_ALINAN"
DURUM_ROL_YETKISIZ: Final = "CEK_DURUM_ROL_YETKISIZ"


def rol_hedefe_gidebilir_mi(rol: str, hedef: str) -> bool:
    """``hedef`` riskli bir geçişse yalnız muhasebe rolleri; değilse herkes.

    Uç iznini (``payments``) GEÇMİŞ bir istek için sorulur; bu kural onu
    DARALTIR, genişletmez. Boş rol (çözülemeyen istek) riskli hedefe gidemez.
    """
    return hedef not in MUHASEBE_HEDEFLERI or rol in MUHASEBE_ROLLERI

#: Tahsilatın aktarılabileceği hesap tipleri. ``finance_engine.ACCOUNT_TYPES``
#: içindeki ``pos`` BİLEREK yok: çek POS'a tahsil edilmez.
TAHSIL_HESAP_TIPLERI: Final[frozenset[str]] = frozenset({"cash", "bank"})


class GecisHatasi(Exception):
    """Geçiş reddedildi. ``kod`` yanıttaki ``code``dur; durum hep 409."""

    def __init__(self, kod: str, mesaj: str) -> None:
        super().__init__(mesaj)
        self.kod = kod
        self.mesaj = mesaj


def izinli_mi(kaynak: str, hedef: str) -> bool:
    return hedef in GECISLER.get(kaynak, frozenset())


def gecis_dogrula(kaynak: str, hedef: str, *, yon: str) -> None:
    """Geçiş izinliyse sessiz döner; değilse :class:`GecisHatasi`.

    Sıra bilinçli: önce durum tablosu, sonra yön kuralı. Son durumdaki bir
    verilen çek için ``ciro_edildi`` istenirse cevap "geçiş geçersiz"dir —
    evrak zaten kapanmıştır, yön sorusu anlamsızdır.
    """
    if kaynak not in GECISLER or hedef not in GECISLER:
        raise GecisHatasi(GECIS_GECERSIZ, f"Bilinmeyen portföy durumu: {kaynak} -> {hedef}")
    if not izinli_mi(kaynak, hedef):
        raise GecisHatasi(
            GECIS_GECERSIZ, f"İzin verilmeyen portföy geçişi: {kaynak} -> {hedef}"
        )
    if hedef == CIRO_EDILDI and yon != "alinan":
        raise GecisHatasi(
            CIRO_YALNIZ_ALINAN, "Yalnız alınan (müşteri) evrakı ciro edilebilir"
        )
