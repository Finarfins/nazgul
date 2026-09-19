"""Serbest metin aramasının TEK ortak dikişi (H57 + H58).

İki kusur, iki araç:

* **H58 — joker sızıntısı.** Arama uçları ``f'%{q}%'`` kuruyordu; kullanıcının
  yazdığı ``%`` ve ``_`` LIKE jokeri olarak çalışıyordu (``q=%`` firmanın BÜTÜN
  satırlarını döndürüyordu). :func:`arama_deseni` ``\\``, ``%`` ve ``_``yi
  :data:`KACIS` ile kaçırır; her LIKE ``ESCAPE '\\'`` taşır.

* **H57 — Türkçe büyük/küçük harf.** SQLite ``LOWER()``/``UPPER()`` yalnız
  ASCII katlar ("YILMAZ" "Yılmaz"ı bulmuyordu); PostgreSQL'de ``/api/search``
  hiç katlamıyordu. Katlama iki tarafta da AYNI eşlemeyle yapılır: sorgu
  Python'da :func:`tr_katla` ile, kolon SQL'de :func:`katli_sql` ile.

SEÇİM (ölçüldü): katlama ``app.whatsapp.niyet.tr_katla``dır — Türkçe
harfleri ASCII karşılığına indirip büyütür. Sıkı Türkçe katlama (İ→i, I→ı)
"yilmaz" ile "Yılmaz"ı EŞLEMEZ; ASCII katlama dört yazımı da ("yılmaz",
"YILMAZ", "Yilmaz", "yilmaz") aynı ``YILMAZ``a indirir. Fonksiyon TAŞINMADI:
``niyet.py``nin uygulama modülü içe aktarmadığı testle çivili
(``test_wa3_niyet``), bu yüzden yön tersine — bu modül ``niyet``ten alır.
``niyet`` saf standart kütüphanedir; bu içe aktarma DB/ağ çekmez.

``q`` HER ZAMAN bağlı parametredir; bu modül yalnız SABİT kolon adlarından
SQL ifadesi kurar, kullanıcı metnini SQL'e hiç koymaz.
"""

from __future__ import annotations

from .whatsapp.niyet import _TR_ESLESME, tr_katla

__all__ = ["KACIS", "arama_deseni", "katli_sql", "tr_katla"]

#: LIKE kaçış karakteri. SQL tarafında ``ESCAPE '\\'`` olarak yazılır.
KACIS = "\\"


def arama_deseni(q: str) -> str:
    """Kullanıcı metninden katlanmış, kaçırılmış bir İÇERİR kalıbı kurar.

    Kaçış katlamadan SONRA yapılır ama sıra fark etmez: ``tr_katla`` yalnız
    harflere dokunur. Ters bölü ÖNCE kaçırılır, yoksa ``%``nin önüne konan
    kaçış karakteri ikinci kez kaçırılırdı.
    """

    katli = tr_katla(q.strip())
    kacisli = (
        katli.replace(KACIS, KACIS * 2)
        .replace("%", KACIS + "%")
        .replace("_", KACIS + "_")
    )
    return f"%{kacisli}%"


def katli_sql(kolon: str) -> str:
    """``kolon``u :func:`tr_katla` ile AYNI biçime indiren SQL ifadesi.

    ``kolon`` bir SABİT kolon ifadesidir (ör. ``"COALESCE(c.owner_name,'')"``),
    asla kullanıcı girdisi değil. Türkçe harfler ÖNCE ASCII'ye indirilir
    (SQLite ``UPPER`` yalnız ASCII bilir), SONRA büyütülür. Dıştaki son
    ``İ``→``I`` değişimi Türkçe yerel ayarlı bir PostgreSQL'e karşı: orada
    ``UPPER('i')`` ``'İ'`` verir.
    """

    ifade = kolon
    for kaynak, hedef in _TR_ESLESME:
        ifade = f"REPLACE({ifade},'{kaynak}','{hedef}')"
    return f"REPLACE(UPPER({ifade}),'İ','I')"
