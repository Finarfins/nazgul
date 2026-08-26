"""§5.5 içerik kapısı — izin-verilen-içerik modeli, fail-closed.

Tasarım sözleşmesi (``TASARIM_SERVIS_HATIRLATMA_BILDIRIM_F0.md`` §5.5):

1. **Kara liste değil, izin listesi.** "URL ara ve reddet" yaklaşımı atlatma
   yöntemlerinin sonsuz kuyruğuna karşı kaybeder. Önce izin verilen karakter
   kümesi uygulanır; küme dışındaki her karakter reddi tetikler.
2. **Kontrol normalize edilmiş NİHAİ içerik üzerinde çalışır** — şablon gövdesi
   değil, değişkenler yerleştirildikten sonra sağlayıcıya gidecek tam metin.
3. **Belirsizlik reddir.** Çözülemeyen bir kodlama, tanınmayan bir karakter ya
   da ayrıştırılamayan bir yapı "izin var" anlamına gelmez.

v1'de hiçbir şablonun meşru link ihtiyacı yoktur; bu yüzden model fonksiyonel
kayıp yaratmaz. Faz-C'de ödeme linki geldiğinde kapı, imzalı ve allow-list'lenmiş
**tek** bir alan adı için açılır — genel URL izni olarak değil.
"""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import unquote


class ContentRejected(ValueError):
    """İçerik kapısına takılan metin. Mesaj kullanıcıya gösterilebilir."""

    def __init__(self, category: str, matched: str, context: str) -> None:
        self.category = category
        # Denetime giden parçalar zaten kırpılmış ve maskelenmiştir.
        self.matched = matched
        self.context = context
        super().__init__(_REJECTION_MESSAGES.get(category, _REJECTION_MESSAGES["OTHER"]))


_REJECTION_MESSAGES = {
    "CHARSET": "İçerikte izin verilmeyen karakter var",
    "SCHEME": "İçerikte bağlantı adresi (şema) bulunamaz",
    "WWW": "İçerikte 'www.' ile başlayan adres bulunamaz",
    "DOMAIN": "İçerikte alan adı bulunamaz",
    "IP": "İçerikte IP adresi bulunamaz",
    "HTML_ATTR": "İçerikte bağlantı özniteliği bulunamaz",
    "ENCODING": "İçerik çözülemeyen iç içe kodlama taşıyor",
    "OTHER": "İçerik güvenlik denetiminden geçemedi",
}


# ---------------------------------------------------------------------------
# İzin verilen karakter kümesi
# ---------------------------------------------------------------------------
# Türkçe harfler + rakam + boşluk + §5.5'te sayılan sınırlı noktalama.
# ':' ve '/' bilinçli olarak izinlidir (saat, tarih, kesir); tek başına yeterli
# olmadıkları için altındaki açık yasak kontrolleri eklenir.
_ALLOWED_PUNCTUATION = ".,:;!?'\"()-/\n\r\t "
_ALLOWED_LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Şapkalı harfler kaçış dizisiyle yazılır: depo genelindeki mojibake tarayıcısı
# U+00C2 karakterini bozuk kodlama işareti sayar ve buradaki meşru şapkalı harf
# kümesiyle yanlış pozitif çakışırdı.
_TURKISH_LETTERS = "çÇğĞıİöÖşŞüÜ" + "\u00e2\u00c2\u00ee\u00ce\u00fb\u00db"
_ALLOWED_CHARS = frozenset(
    _ALLOWED_LETTERS + _TURKISH_LETTERS + "0123456789" + _ALLOWED_PUNCTUATION
)

# Tipografik eşdeğerler, karakter kümesi kontrolünden ÖNCE düz karşılıklarına
# indirgenir. Hepsi zaten izinli bir karaktere eşlenir; kapıyı zayıflatmaz,
# yalnız gerçek metinlerde kaçınılmaz olan kıvrık tırnak / uzun tire yüzünden
# meşru şablonların reddedilmesini önler.
_TYPOGRAPHIC_FOLDS = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-",
        "—": "-", "―": "-", "−": "-",
        "…": "...",
        " ": " ", " ": " ", " ": " ", " ": " ",
    }
)

# Şema sözcükleri. ``hxxp`` gibi kasıtlı bozmalar da listededir; amaç metnin
# "link gibi görünmesini" engellemektir, yalnız çalışan linkleri değil.
_SCHEME_WORDS = (
    "https",
    "http",
    "hxxps",
    "hxxp",
    "ftps",
    "ftp",
    "mailto",
    "javascript",
    "data",
    "tel",
    "sms",
    "whatsapp",
    "file",
)

# ---------------------------------------------------------------------------
# Normalizasyon zinciri
# ---------------------------------------------------------------------------
def _strip_invisibles(value: str) -> str:
    """Sıfır-genişlik ve kontrol karakterlerini kaldırır.

    Bunlar bölünmüş şema gizlemenin en ucuz yoludur (``http\u200b://``).
    Yeni satır ve sekme korunur; onlar meşru metin ayraçlarıdır.
    """
    kept: list[str] = []
    for char in value:
        if char in "\n\r\t":
            kept.append(char)
            continue
        category = unicodedata.category(char)
        # Cf: format (zero-width, soft hyphen, bidi), Cc: control,
        # Co/Cn: private use / unassigned — hiçbiri meşru mesaj içeriği değildir.
        if category in {"Cf", "Cc", "Co", "Cn"}:
            continue
        kept.append(char)
    return "".join(kept)


def _decode_layers(value: str, *, max_rounds: int = 5) -> str:
    """URL-encode ve HTML entity çözümünü sabit noktaya kadar yineler.

    ``%68%74%74%70`` → ``http``, ``&#104;`` → ``h``. Tek tur yeterli değildir:
    çift kodlama (``%2568``) iki tur ister. ``max_rounds`` sonunda hâlâ değişen
    girdi, meşru bir mesajın asla taşımayacağı derinlikte iç içe kodlama
    demektir ve açıkça reddedilir — sabit noktaya ulaşmamış bir metni "çözüldü"
    sayıp denetlemek, kalan katmanların gizlediğini görmemek olurdu.
    """
    current = value
    for _ in range(max_rounds):
        decoded = html.unescape(unquote(current))
        if decoded == current:
            return current
        current = decoded
    raise ContentRejected("ENCODING", current[:60], current[:100])


def normalize_for_gate(value: str) -> str:
    """§5.5 normalizasyon zinciri — kontrolden önce, bu sırayla.

    NFKC → görünmez karakter temizliği → yinelemeli decode → tekrar temizlik
    → küçük harf → boşluk daraltma.

    Decode sonrası temizlik ikinci kez yapılır: kodlanmış bir sıfır-genişlik
    karakter (``%E2%80%8B``) ancak çözüldükten sonra görünür hâle gelir.
    """
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = _strip_invisibles(normalized)
    normalized = _decode_layers(normalized)
    normalized = unicodedata.normalize("NFKC", normalized)
    normalized = _strip_invisibles(normalized)
    normalized = normalized.casefold()
    return re.sub(r"[ \t]+", " ", normalized)


# ---------------------------------------------------------------------------
# Yasak desenleri
# ---------------------------------------------------------------------------
def _spaced(word: str) -> str:
    """``http`` → harfler arasında 0-3 ayraç kabul eden desen.

    ``h t t p``, ``h.t.t.p``, ``h-t-t-p`` hepsi yakalanır.
    """
    return r"[\s\W_]{0,3}".join(re.escape(char) for char in word)


_SCHEME_RE = re.compile(
    "|".join(rf"{_spaced(word)}[\s\W_]{{0,3}}:" for word in _SCHEME_WORDS)
)
_WWW_RE = re.compile(rf"{_spaced('www')}[\s\W_]{{0,3}}\.")
# Şema işaretini ayraçsız da yakalamak için alfasayısala indirgenmiş metin.
_COMPRESSED_MARKERS = ("http", "hxxp", "mailto", "javascript")

_DOMAIN_RE = re.compile(r"(?<![\w.])([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\.([a-z]{2,24})(?![\w])")
_DOTTED_IP_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.])")
_HEX_IP_RE = re.compile(r"(?<![\w])0x[0-9a-f]{6,8}(?![\w])")
_DECIMAL_IP_RE = re.compile(r"(?<![\w.])([1-9]\d{7,9})(?![\w.])")

_HTML_ATTR_RE = re.compile(
    r"(?:href|src|srcset|action|formaction|background|poster|data-[a-z0-9-]+)"
    r"[\s\W_]{0,3}=|url[\s\W_]{0,3}\(|@import"
)


def _reject(category: str, matched: str, haystack: str, start: int, end: int) -> None:
    context = haystack[max(0, start - 20) : end + 20]
    raise ContentRejected(category, matched[:60], context[:100])


def fold_typographic(value: str) -> str:
    """Kıvrık tırnak / uzun tire gibi eşdeğerleri düz karşılığına indirger."""
    return unicodedata.normalize("NFKC", value or "").translate(_TYPOGRAPHIC_FOLDS)


def _check_charset(original: str) -> None:
    for index, char in enumerate(original):
        if char in _ALLOWED_CHARS:
            continue
        # NFKC sonrası hâlâ küme dışındaysa reddedilir. Emoji, kutu çizimi,
        # homoglif alfabeler ve tanınmayan işaretler buraya düşer.
        _reject("CHARSET", char, original, index, index + 1)


def _check_schemes(normalized: str) -> None:
    match = _SCHEME_RE.search(normalized)
    if match:
        _reject("SCHEME", match.group(0), normalized, match.start(), match.end())

    compressed = re.sub(r"[^a-z0-9]", "", normalized)
    for marker in _COMPRESSED_MARKERS:
        position = compressed.find(marker)
        if position != -1:
            _reject("SCHEME", marker, compressed, position, position + len(marker))


def _check_www_and_domains(normalized: str) -> None:
    match = _WWW_RE.search(normalized)
    if match:
        _reject("WWW", match.group(0), normalized, match.start(), match.end())

    # İzin-listesi felsefesiyle tutarlı olarak TLD listesinden BAĞIMSIZ:
    # alan adı gibi ayrışan her token fail-closed reddedilir. Bilinen TLD
    # listesine bakmak kara listeye geri dönmek olurdu — her yeni TLD bir
    # delik açardı. Meşru Türkçe mesaj metni "sozcuk.sozcuk" biçiminde
    # boşluksuz nokta bitişiği taşımaz; tarih/saat/tutar kalıpları sayısal
    # olduğu için bu desene zaten girmez.
    for candidate in _DOMAIN_RE.finditer(normalized):
        _reject(
            "DOMAIN",
            candidate.group(0),
            normalized,
            candidate.start(),
            candidate.end(),
        )


def _check_ip_literals(normalized: str) -> None:
    for match in _DOTTED_IP_RE.finditer(normalized):
        octets = match.group(1).split(".")
        if all(octet.isdigit() and int(octet) <= 255 for octet in octets):
            _reject("IP", match.group(1), normalized, match.start(), match.end())

    match = _HEX_IP_RE.search(normalized)
    if match:
        _reject("IP", match.group(0), normalized, match.start(), match.end())

    # Ondalık IP (``2130706433``). Baştaki sıfır dışlanır ve aralık kontrolü
    # yapılır; bu, telefon numaralarının yanlışlıkla reddedilmesini önler:
    # ``05321112233`` sıfırla başlar, ``5321112233`` IPv4 üst sınırını aşar.
    for match in _DECIMAL_IP_RE.finditer(normalized):
        value = int(match.group(1))
        if 16777216 <= value <= 4294967295:
            _reject("IP", match.group(1), normalized, match.start(), match.end())


def _check_html_constructs(normalized: str) -> None:
    match = _HTML_ATTR_RE.search(normalized)
    if match:
        _reject("HTML_ATTR", match.group(0), normalized, match.start(), match.end())


def assert_content_allowed(value: str, *, channel: str = "SMS") -> str:
    """İçeriği §5.5 kapısından geçirir; geçemezse :class:`ContentRejected`.

    Döndürülen değer normalize edilmiş metindir — çağıran taraf isterse
    denetim için saklayabilir. Gönderilecek metin **orijinaldir**; normalize
    hâli yalnız kontrol amaçlıdır.
    """
    text_value = fold_typographic(value or "")
    _check_charset(text_value)
    normalized = normalize_for_gate(text_value)
    _check_schemes(normalized)
    _check_www_and_domains(normalized)
    _check_ip_literals(normalized)
    # HTML yapıları düz metin kontrolüyle yakalanmaz; ayrı ve bağımsız denetim.
    # SMS kanalında da uygulanır: gövdeye gömülü bir ``href=`` zaten kuşkuludur.
    _check_html_constructs(normalized)
    return normalized


def is_content_allowed(value: str, *, channel: str = "SMS") -> bool:
    """Kapıyı istisna yükseltmeden çalıştırır (önizleme/rozet için)."""
    try:
        assert_content_allowed(value, channel=channel)
    except ContentRejected:
        return False
    return True
