"""Serbest metin aramasının TEK ortak dikişi (H57 + H58).

İki kusur, iki araç:

* **H58 — joker sızıntısı.** Arama uçları ``f'%{q}%'`` kuruyordu; kullanıcının
  yazdığı ``%`` ve ``_`` LIKE jokeri olarak çalışıyordu (``q=%`` firmanın BÜTÜN
  satırlarını döndürüyordu). :func:`arama_deseni` ``\\``, ``%`` ve ``_``yi
  :data:`KACIS` ile kaçırır; her LIKE ``ESCAPE '\\'`` taşır.

* **H57 — Türkçe büyük/küçük harf.** SQLite ``LOWER()``/``UPPER()`` yalnız
  ASCII katlar ("YILMAZ" "Yılmaz"ı bulmuyordu); PostgreSQL'de ``/api/search``
  hiç katlamıyordu. Katlama iki tarafta da AYNI tabloyla yapılır: sorgu
  Python'da :func:`arama_katla` ile, kolon SQL'de :func:`katli_sql` ile.

TEK KURAL (ölçüldü, mercek NO-GO 0580dcc sonrası): katlama :data:`ARAMA_ESLESME`
tablosunun KARAKTER BAŞINA uygulanmasıdır ve BAŞKA HİÇBİR ŞEY değildir.

* Tablo üç parçadır: ``niyet._TR_ESLESME`` (Türkçe harfler), yaygın Latin-1
  aksanları (``â î û é è ê ë á à ä ã å í ì ï ó ò ô õ ø ú ù ñ ý ÿ`` ve
  büyükleri) ve ASCII ``a-z → A-Z``. Her hedef BÜYÜK ASCII harftir.
* ``UPPER()`` ve ``str.upper()`` KULLANILMAZ. İlk sürüm sorguyu Python
  ``.upper()`` ile (her harf), kolonu SQL ``UPPER()`` ile (SQLite'ta ve C
  harmanlı PG'de yalnız A-Z) büyütüyordu; tabloda olmayan her aksanlı harf
  iki tarafta FARKLI katlanıyordu: "Kâzım", "hâlâ", "Café" tabanda bulunup
  HEAD'de BOŞ dönüyordu. ASCII büyütme de tablonun parçası olunca SQL tarafı
  yalnız ``REPLACE`` zinciridir; ``REPLACE`` bayt-bayt karşılaştırır, yerel
  ayar/harmanlama (SQLite, PG C, PG ICU, tr_TR) sonucu DEĞİŞTİRMEZ.
* Karakter başına bir eşleme olduğu için katlama alt dizgiyi korur: ``q``
  bir adın AYNEN alt dizgisiyse katlanmışı da katlanmış adın alt dizgisidir.
  Tabanda birebir alt dizgiyle bulunan hiçbir kayıt kaybolamaz.
* Tabloda OLMAYAN harfler (``ß``, ``æ``, Kiril, Yunan...) iki tarafta da
  DOKUNULMADAN kalır: birebir yazılırsa bulunur, büyük/küçük farkı katlanmaz.

``niyet.tr_katla`` DEĞİŞMEDİ: WhatsApp niyet çözümü kendi kuralını korur
(``test_wa3_niyet``); bu modül yalnız onun Türkçe tablosunu okur. ``niyet``
saf standart kütüphanedir; bu içe aktarma DB/ağ çekmez.

``q`` HER ZAMAN bağlı parametredir; bu modül yalnız SABİT kolon adlarından
SQL ifadesi kurar, kullanıcı metnini SQL'e hiç koymaz. :func:`katli_sql`
çağrıları modül yüklenirken BİR KEZ yapılır (istek başına değil).
"""

from __future__ import annotations

from .whatsapp.niyet import _TR_ESLESME

__all__ = ["ARAMA_ESLESME", "KACIS", "arama_deseni", "arama_katla", "katli_sql"]

#: LIKE kaçış karakteri. SQL tarafında ``ESCAPE '\\'`` olarak yazılır.
KACIS = "\\"

# U+00C2..U+00C5 `chr()` ile yazılır: `test_backend_encoding` bu dört büyük harfi
# UTF-8->Latin-1 bozulmasının (mojibake) izi sayar ve düz yazılınca kırmızı yanar.
_AKSAN_ESLESME = (
    (chr(0xC2), "A"), ("â", "A"), ("Î", "I"), ("î", "I"), ("Û", "U"), ("û", "U"),
    ("É", "E"), ("é", "E"), ("È", "E"), ("è", "E"), ("Ê", "E"), ("ê", "E"),
    ("Ë", "E"), ("ë", "E"),
    ("Á", "A"), ("á", "A"), ("À", "A"), ("à", "A"), (chr(0xC4), "A"), ("ä", "A"),
    (chr(0xC3), "A"), ("ã", "A"), (chr(0xC5), "A"), ("å", "A"),
    ("Í", "I"), ("í", "I"), ("Ì", "I"), ("ì", "I"), ("Ï", "I"), ("ï", "I"),
    ("Ó", "O"), ("ó", "O"), ("Ò", "O"), ("ò", "O"), ("Ô", "O"), ("ô", "O"),
    ("Õ", "O"), ("õ", "O"), ("Ø", "O"), ("ø", "O"),
    ("Ú", "U"), ("ú", "U"), ("Ù", "U"), ("ù", "U"),
    ("Ñ", "N"), ("ñ", "N"), ("Ý", "Y"), ("ý", "Y"), ("ÿ", "Y"),
)

#: Katlamanın TEK tablosu: (kaynak karakter, BÜYÜK ASCII hedef). Hem
#: :func:`arama_katla` hem :func:`katli_sql` YALNIZ bunu uygular.
ARAMA_ESLESME: tuple[tuple[str, str], ...] = (
    tuple((kaynak, hedef.upper()) for kaynak, hedef in _TR_ESLESME)
    + _AKSAN_ESLESME
    + tuple((chr(k), chr(k - 32)) for k in range(ord("a"), ord("z") + 1))
)


def _tabloyu_dogrula() -> None:
    # Hedef hiçbir zaman kaynak değilse REPLACE zincirinin SIRASI önemsizdir
    # ve zincir `str.translate` ile karakteri karakterine aynıdır.
    kaynaklar = [k for k, _ in ARAMA_ESLESME]
    if len(set(kaynaklar)) != len(kaynaklar):
        raise RuntimeError("ARAMA_ESLESME: yinelenen kaynak")
    for kaynak, hedef in ARAMA_ESLESME:
        if len(kaynak) != 1 or len(hedef) != 1 or not ("A" <= hedef <= "Z"):
            raise RuntimeError(f"ARAMA_ESLESME: gecersiz cift {kaynak!r}->{hedef!r}")
        if "A" <= kaynak <= "Z" or kaynak in "'\\%_":
            raise RuntimeError(f"ARAMA_ESLESME: kaynak {kaynak!r} yasak")


_tabloyu_dogrula()
_CEVIRI = str.maketrans(dict(ARAMA_ESLESME))


def arama_katla(metin: str) -> str:
    """``metin``i :data:`ARAMA_ESLESME` ile karakter başına katlar."""

    return metin.translate(_CEVIRI)


def arama_deseni(q: str) -> str:
    """Kullanıcı metninden katlanmış, kaçırılmış bir İÇERİR kalıbı kurar.

    Kaçış katlamadan SONRA yapılır ama sıra fark etmez: tablo ``\\``, ``%``
    ve ``_``ye dokunmaz. Ters bölü ÖNCE kaçırılır, yoksa ``%``nin önüne konan
    kaçış karakteri ikinci kez kaçırılırdı.
    """

    katli = arama_katla(q.strip())
    kacisli = (
        katli.replace(KACIS, KACIS * 2)
        .replace("%", KACIS + "%")
        .replace("_", KACIS + "_")
    )
    return f"%{kacisli}%"


def katli_sql(kolon: str) -> str:
    """``kolon``u :func:`arama_katla` ile AYNI biçime indiren SQL ifadesi.

    ``kolon`` bir SABİT kolon ifadesidir (ör. ``"COALESCE(c.owner_name,'')"``),
    asla kullanıcı girdisi değil. Yalnız iç içe ``REPLACE``dir; ``UPPER``
    yoktur, bu yüzden SQLite ve PostgreSQL (C, ICU, tr_TR) aynı sonucu verir.
    Modül düzeyinde sabit kurmak için çağrılır, istek başına DEĞİL.
    """

    ifade = kolon
    for kaynak, hedef in ARAMA_ESLESME:
        ifade = f"REPLACE({ifade},'{kaynak}','{hedef}')"
    return ifade
