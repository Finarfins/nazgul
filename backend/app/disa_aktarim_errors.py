"""Kiracı dışa aktarımının ADI KONMUŞ hataları.

``backup_errors.py`` ile aynı aile biçimi: sade istisna sınıfları, sınır
noktasında HTTP yanıtına çevriliyor. Fark, her sınıfın GÖVDEYE yazılan
KARARLI bir ``kod`` taşımasıdır — istemci hata METNİNİ ayrıştırmak zorunda
kalmasın diye. Metin çevrilebilir ve değişebilir; ``kod`` sözleşmedir.
"""
from __future__ import annotations


class DisaAktarimError(RuntimeError):
    """Dışa aktarımı DURDURAN her hatanın ortak atası."""

    kod = "EXPORT_FAILED"


class SonluOlmayanSayiError(DisaAktarimError):
    """Veritabanında sonlu OLMAYAN bir sayı bulundu (NaN / ±Infinity).

    NEDEN SESSİZCE GEÇİLMİYOR: ``json.dumps`` bu değerleri standart DIŞI
    ``NaN``/``Infinity`` sözcükleriyle yazar; katı bir okuyucu dosyanın
    TAMAMINI reddeder. ``null``a çevirmek ise veriyi KAYBEDERDİ ve kayıp
    sessiz olurdu — kiracı, eksildiğini bilmediği bir dosya alırdı.

    NEREDE OLABİLİR — ÖLÇÜLDÜ, VARSAYILMADI: SQLite bu değeri HİÇ saklayamaz;
    sürücü ``Decimal("NaN")``ı ``nan``a çevirir, SQLite onu ``NULL`` yapar ve
    ``NOT NULL`` kısıtı düşer (``IntegrityError``). PostgreSQL'de ise
    ``numeric`` türü ``'NaN'`` değerini KABUL EDER. Yani bu hata pratikte
    YALNIZ PostgreSQL'de doğabilir ve kapı oraya bakıyor.
    """

    kod = "EXPORT_NON_FINITE_NUMBER"

    def __init__(self, tablo: str, sutun: str, deger: object) -> None:
        self.tablo = tablo
        self.sutun = sutun
        self.deger = deger
        super().__init__(
            f"Dışa aktarım durduruldu: {tablo}.{sutun} sonlu olmayan bir sayı "
            f"taşıyor ({deger!r}); JSON bu değeri temsil edemez"
        )
