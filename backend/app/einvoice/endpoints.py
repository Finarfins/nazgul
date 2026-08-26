"""Sağlayıcı tel-detayları — İzibiz DOĞRULANDI, Nes hâlâ ‹doğrulanacak›.

İki sağlayıcının uçları, operasyon adları, yanıt alan adları ve hata anahtar
kelimeleri burada, tek yerde durur; adaptörler (:mod:`app.einvoice.provider`)
bunları başka hiçbir yerden okumaz.

**İzibiz bölümü artık tahmin değildir.** Aşağıdaki her operasyon adı, gövde kök
elemanı, ad alanı ve alan adı ``efaturatest.izibiz.com.tr`` üzerinden indirilen
resmî WSDL/XSD'den okundu ve ``backend/sandbox/izibiz_smoke.py`` ile test
ortamına yapılan gerçek çağrılarla doğrulandı. Kanıt kaydı:
``docs/izibiz-sandbox-bulgular.md`` + ``backend/tests/fixtures/izibiz/``.

**Nes bölümü değişmedi**: hâlâ ‹doğrulanacak› yer tutucular, ``NES_BASE_URL``
boş, ``NES_ENDPOINTS_VERIFIED`` ``False``.

Bu dosyanın kuralları:

1. **Asla URL uydurma.** Bilinmeyen uç boş dizedir. Boş uç bir kusur değil,
   sözleşmedir: :func:`is_configured` ``False`` döner ve çağıran gürültülü bir
   ``FAILED`` üretir. Çağrı yapılmaz, sahte başarı üretilmez.
2. Test ortamının taban adresi burada **referans olarak** durur
   (:data:`IZIBIZ_TEST_BASE_URL`) ama varsayılan DEĞİLDİR: hangi ortama
   bağlanılacağı ``EINVOICE_BASE_URL`` ile operatörün açık kararıdır.
3. Doğrulanmamış her ayrıntı ‹doğrulanacak› ile işaretlenir ve kendi kapısına
   bağlanır (bkz. :data:`IZIBIZ_EFATURA_SUBMIT_VERIFIED`).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


# --- Ortak ---------------------------------------------------------------
#: Yapılandırılmamış bir uca çağrı denendiğinde dönen kullanıcı mesajı.
UNCONFIGURED_ENDPOINT_ERROR = "e-Fatura ucu yapılandırılmadı"

#: Yukarıdakinden farklı: taban URL VAR ama operasyon/alan adları hâlâ tahmin.
UNVERIFIED_ENDPOINT_ERROR = "e-Fatura uç noktaları resmî dokümandan doğrulanmadı"

#: Ayrıştırılan (XML/JSON) sağlayıcı gövdesi için sert tavan.
MAX_PARSED_RESPONSE_BYTES = 2 * 1024 * 1024
#: PDF'ler ikili ve meşru olarak daha büyük.
MAX_BINARY_RESPONSE_BYTES = 25 * 1024 * 1024


# =========================================================================
# İzibiz (SOAP/WS) — WSDL'den okundu, sandbox'ta doğrulandı
# =========================================================================
#: WSDL + canlı test çağrılarıyla doğrulandı (bkz. modül docstring'i).
IZIBIZ_ENDPOINTS_VERIFIED = True

#: ‹DOĞRULANACAK› DEĞİL ama yine de BOŞ: hangi ortama bağlanılacağı operatörün
#: kararıdır. ``EINVOICE_BASE_URL`` verilmedikçe adaptör hiçbir yere çağrı yapmaz.
IZIBIZ_BASE_URL = ""

#: Yalnız referans/dokümantasyon. Kod bunu varsayılan olarak KULLANMAZ; canlı
#: adres bilerek yazılmamıştır ki bir yazım hatası üretime çıkmasın.
IZIBIZ_TEST_BASE_URL = "https://efaturatest.izibiz.com.tr"


# --- Ortam kilidi --------------------------------------------------------
#: ``IZIBIZ_ENV`` değerleri. Başka hiçbir değer kabul edilmez.
IZIBIZ_ENV_TEST = "test"
IZIBIZ_ENV_LIVE = "live"
IZIBIZ_ENVIRONMENTS: tuple[str, ...] = (IZIBIZ_ENV_TEST, IZIBIZ_ENV_LIVE)
#: Ayarlanmamışsa en güvenli taraf. "Ayarlanmamış" ile "boş verilmiş" farklıdır:
#: ikincisi hatadır (bkz. :func:`izibiz_environment`).
IZIBIZ_DEFAULT_ENV = IZIBIZ_ENV_TEST

#: Ortam başına exact host allowlist. Alt alan adı, substring veya operatörün
#: yazdığı rastgele bir host kabul edilmez; kimlik bilgileri yalnız doğrulanmış
#: İzibiz TLS uçlarına gönderilebilir. Canlı allowlist bilinçli olarak boştur.
IZIBIZ_TEST_HOST_ALLOWLIST: tuple[str, ...] = ("efaturatest.izibiz.com.tr",)
IZIBIZ_LIVE_HOST_ALLOWLIST: tuple[str, ...] = ()

#: İzibiz'in bilinen CANLI uçları. Bunlar **her ortamda** reddedilir — canlıya
#: geçiş, bu listeden çıkarmayı gerektiren bilinçli ve tek yerde görülebilir bir
#: karardır. Sonucu: ``IZIBIZ_ENV=live`` bugün bu dört adrese ulaşamaz, yani
#: canlı kanal fiilen kapalıdır. Bilerek: bu artışın kapsamı sandbox.
IZIBIZ_LIVE_HOST_DENYLIST: tuple[str, ...] = (
    "authenticationws.izibiz.com.tr",
    "efaturaws.izibiz.com.tr",
    "earsivws.izibiz.com.tr",
    "api.izibiz.com.tr",
)

IZIBIZ_ENV_INVALID_ERROR = (
    "IZIBIZ_ENV geçersiz: {value!r}. Yalnız 'test' veya 'live' kabul edilir "
    "(ayarlanmamışsa 'test')."
)
IZIBIZ_HOST_UNRESOLVED_ERROR = "e-Fatura ucunun ana makine adı çözümlenemedi"
IZIBIZ_HTTPS_REQUIRED_ERROR = "e-Fatura ucu HTTPS kullanmalıdır"
IZIBIZ_URL_USERINFO_ERROR = "e-Fatura uç adresinde kullanıcı adı veya parola bulunamaz"
IZIBIZ_HTTPS_PORT_ERROR = "e-Fatura ucu yalnız standart HTTPS portunu kullanabilir"
IZIBIZ_LIVE_HOST_BLOCKED_ERROR = "CANLI e-Fatura ucu engellendi: {host}"
#: Aynı denylist, ``live`` ortamında farklı bir mesaj gerektirir. "CANLI UÇ
#: ENGELLENDİ" orada yanıltıcı: operatör zaten canlı moddadır ve mesajı bir
#: yapılandırma hatası sanır. Asıl söylenmesi gereken, engelin KASITLI olduğu
#: ve kaldırmanın nerede yapılacağı.
IZIBIZ_LIVE_ENV_DENYLIST_ERROR = (
    "IZIBIZ_ENV=live ama {host} hâlâ IZIBIZ_LIVE_HOST_DENYLIST içinde. "
    "Canlı faturaya geçiş bilinçli bir karardır: adresi denylist'ten çıkar ve "
    "bunu PR'da gerekçelendir."
)
IZIBIZ_TEST_ENV_LIVE_HOST_ERROR = (
    "IZIBIZ_ENV=test iken test dışı bir uca çağrı yapılamaz: {host}"
)
IZIBIZ_LIVE_ENV_TEST_HOST_ERROR = (
    "IZIBIZ_ENV=live iken sandbox ucuna çağrı yapılamaz: {host}"
)
IZIBIZ_LIVE_ENV_HOST_NOT_ALLOWED_ERROR = (
    "IZIBIZ_ENV=live iken allowlist dışı bir uca çağrı yapılamaz: {host}"
)


class IzibizEnvironmentError(ValueError):
    """``IZIBIZ_ENV`` okunamadı. Sessiz varsayılana düşmek yerine gürültü."""


def izibiz_environment(value: Any) -> str:
    """``IZIBIZ_ENV`` değerini doğrula.

    ``None`` (hiç ayarlanmamış) ⇒ :data:`IZIBIZ_DEFAULT_ENV`. Bunun dışında her
    şey — boş dize dâhil — doğrulanır; tanınmayan değer
    :class:`IzibizEnvironmentError` fırlatır.

    Boş dizeyi varsayılana çevirmemek bilinçli: ``IZIBIZ_ENV=`` yazan bir
    yapılandırma büyük ihtimalle yarım kalmış bir deploy'dur, "test demek
    istedim" değil.
    """
    if value is None:
        return IZIBIZ_DEFAULT_ENV
    text = str(value).strip().lower()
    if text not in IZIBIZ_ENVIRONMENTS:
        raise IzibizEnvironmentError(IZIBIZ_ENV_INVALID_ERROR.format(value=value))
    return text


def izibiz_endpoint_violation(url: str, environment: str) -> str | None:
    """Bu adrese bu ortamda çağrı yapılabilir mi? Yapılamazsa gerekçe döner.

    Üç kapı, sırayla:

    1. Ana makine adı çözümlenemiyorsa ret. Boş/bozuk URL "sorun yok" değildir.
    2. Bilinen canlı uçlar **her ortamda** reddedilir
       (:data:`IZIBIZ_LIVE_HOST_DENYLIST`). Gerekçe aynı olsa da mesaj ortama
       göre değişir: ``live`` ortamındaki operatör "ama ben canlı moddayım"
       diyeceği için ona engelin kasıtlı olduğu ve nereden kaldırılacağı
       söylenir.
    3. HTTPS zorunludur ve ortam ile adres exact allowlist üzerinden eşleşir.
       ``test`` substring'i ya da denylist dışında kalmak izin değildir. Canlı
       allowlist bugün boştur; canlı geçiş ayrı bir güvenlik kararıdır.

    ``None`` = izin. Çağıran bunu ağ çağrısının hemen öncesinde sorar.
    """
    try:
        parsed = urlsplit(url or "")
        port = parsed.port
    except ValueError:
        return IZIBIZ_HOST_UNRESOLVED_ERROR
    if parsed.scheme.lower() != "https":
        return IZIBIZ_HTTPS_REQUIRED_ERROR
    if parsed.username is not None or parsed.password is not None:
        return IZIBIZ_URL_USERINFO_ERROR
    if port not in (None, 443):
        return IZIBIZ_HTTPS_PORT_ERROR
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return IZIBIZ_HOST_UNRESOLVED_ERROR
    for blocked in IZIBIZ_LIVE_HOST_DENYLIST:
        if host == blocked or host.endswith(f".{blocked}"):
            if environment == IZIBIZ_ENV_LIVE:
                return IZIBIZ_LIVE_ENV_DENYLIST_ERROR.format(host=host)
            return IZIBIZ_LIVE_HOST_BLOCKED_ERROR.format(host=host)
    if environment == IZIBIZ_ENV_TEST and host not in IZIBIZ_TEST_HOST_ALLOWLIST:
        return IZIBIZ_TEST_ENV_LIVE_HOST_ERROR.format(host=host)
    if environment == IZIBIZ_ENV_LIVE and host not in IZIBIZ_LIVE_HOST_ALLOWLIST:
        if host in IZIBIZ_TEST_HOST_ALLOWLIST:
            return IZIBIZ_LIVE_ENV_TEST_HOST_ERROR.format(host=host)
        return IZIBIZ_LIVE_ENV_HOST_NOT_ALLOWED_ERROR.format(host=host)
    return None

#: Üç ayrı servis; hepsi aynı tabandan türer ve aynı SESSION_ID'yi paylaşır.
IZIBIZ_AUTH_PATH = "/AuthenticationWS"
IZIBIZ_EINVOICE_PATH = "/EInvoiceWS"
IZIBIZ_EARCHIVE_PATH = "/EIArchiveWS/EFaturaArchive"

#: Gövde ad alanları. (Eski tahmin ``http://tempuri.org/`` idi — yanlıştı.)
IZIBIZ_SOAP_NAMESPACE = "http://schemas.i2i.com/ei/wsdl"
IZIBIZ_SOAP_NAMESPACE_ARCHIVE = "http://schemas.i2i.com/ei/wsdl/archive"

#: WSDL binding'lerinde ``soapAction=""``. Başlık gönderilir ama BOŞ gönderilir;
#: operasyon adı taşımaz — hedef operasyonu gövdenin kök elemanı belirler.
IZIBIZ_SOAPACTION = ""

# --- Operasyonlar --------------------------------------------------------
IZIBIZ_OP_LOGIN = "Login"
IZIBIZ_OP_LOGOUT = "Logout"
IZIBIZ_OP_TAXPAYER = "CheckUser"
#: e-Fatura (EInvoiceWS)
IZIBIZ_OP_SUBMIT = "SendInvoice"
IZIBIZ_OP_STATUS = "GetInvoiceStatus"
IZIBIZ_OP_INBOX = "GetInvoice"
#: ``GetInvoicePDF`` diye bir operasyon YOK; belge çekme bunun üzerinden.
IZIBIZ_OP_PDF = "GetInvoiceWithType"
#: e-Arşiv (EIArchiveWS)
IZIBIZ_OP_SUBMIT_EARCHIVE = "WriteToArchiveExtended"
IZIBIZ_OP_STATUS_EARCHIVE = "GetEArchiveInvoiceStatus"
IZIBIZ_OP_PDF_EARCHIVE = "GetEArchiveInvoice"

#: Operasyon adı ≠ gövde kök elemanı. Çoğu ``<Ad>Request`` ama e-Arşiv yazımı
#: ``ArchiveInvoiceExtendedRequest``; türetme değil, tablo gerekiyor.
IZIBIZ_REQUEST_ELEMENT: dict[str, str] = {
    IZIBIZ_OP_LOGIN: "LoginRequest",
    IZIBIZ_OP_LOGOUT: "LogoutRequest",
    IZIBIZ_OP_TAXPAYER: "CheckUserRequest",
    IZIBIZ_OP_SUBMIT: "SendInvoiceRequest",
    IZIBIZ_OP_STATUS: "GetInvoiceStatusRequest",
    IZIBIZ_OP_INBOX: "GetInvoiceRequest",
    IZIBIZ_OP_PDF: "GetInvoiceWithTypeRequest",
    IZIBIZ_OP_SUBMIT_EARCHIVE: "ArchiveInvoiceExtendedRequest",
    IZIBIZ_OP_STATUS_EARCHIVE: "GetEArchiveInvoiceStatusRequest",
    IZIBIZ_OP_PDF_EARCHIVE: "GetEArchiveInvoiceRequest",
}

#: Hangi operasyon hangi servise gider. Kalanı EInvoiceWS'e.
IZIBIZ_AUTH_OPERATIONS: frozenset[str] = frozenset(
    {IZIBIZ_OP_LOGIN, IZIBIZ_OP_LOGOUT, IZIBIZ_OP_TAXPAYER}
)
IZIBIZ_ARCHIVE_OPERATIONS: frozenset[str] = frozenset(
    {IZIBIZ_OP_SUBMIT_EARCHIVE, IZIBIZ_OP_STATUS_EARCHIVE, IZIBIZ_OP_PDF_EARCHIVE}
)

# --- Oturum --------------------------------------------------------------
#: İzibiz dokümanı: SESSION_ID 8 saat geçerli. Ölçülen jeton ~5 KB'lık bir JWT.
IZIBIZ_TOKEN_TTL_SECONDS = 8 * 60 * 60
#: TTL'in son dakikaları riskli: uzun bir çağrı sırasında oturum düşerse istek
#: ortada kalır. Jeton, süresinin dolmasına bu kadar kala yenilenir.
IZIBIZ_TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60

# --- Yanıt alan adları ---------------------------------------------------
IZIBIZ_FIELD_SESSION = ("SESSION_ID",)
#: DİKKAT: İzibiz gönderim yanıtında ETTN'i (UUID) geri VERMEZ; yalnız kendi
#: belge kimliğini (``INVOICE_ID``) döner. ``external_id`` bu yüzden ETTN değil
#: sağlayıcı belge kimliğidir; istemci ETTN'i ``EInvoiceResult.uuid``'de durur.
IZIBIZ_FIELD_ETTN = ("INVOICE_ID",)
IZIBIZ_FIELD_STATUS = ("STATUS", "STATUS_CODE")
IZIBIZ_FIELD_REASON = ("STATUS_DESC", "STATUS_DESCRIPTION", "ERROR_SHORT_DES", "ERROR_LONG_DES")
IZIBIZ_FIELD_ERROR_CODE = ("ERROR_CODE",)
IZIBIZ_FIELD_PDF = ("CONTENT", "INVOICE")
#: Boolean bir mükellefiyet alanı YOK. ``CheckUser`` cevabı ``USER`` eleman
#: SAYISIYLA verir: ≥1 etiket ⇒ e-Fatura mükellefi, 0 ⇒ değil. Adaptör bu yüzden
#: bir alan değil, eleman sayısı okur.
IZIBIZ_FIELD_TAXPAYER_ELEMENT = "USER"
IZIBIZ_FIELD_TAXPAYER_RESPONSE = "CheckUserResponse"
#: İş sonucu zarfı — HTTP durumundan BAĞIMSIZ olarak her yanıtta aranır.
IZIBIZ_FIELD_ERROR_ENVELOPE = "ERROR_TYPE"
IZIBIZ_FIELD_RETURN_CODE = "RETURN_CODE"
#: Her İzibiz yanıtının gövdesinde bu sonekle biten bir kök eleman bulunur
#: (``LoginResponse``, ``ArchiveInvoiceExtendedResponse``, …). Yoksa elimizdeki
#: şey bir İzibiz yanıtı değildir — proxy hata sayfası, kesilmiş gövde, yanlış
#: uç. "Anlamadım" cevabı "sorun yok" cevabı olamaz.
IZIBIZ_RESPONSE_ELEMENT_SUFFIX = "Response"

# --- e-Arşiv gönderim parametreleri --------------------------------------
IZIBIZ_EARSIV_TYPE_NORMAL = "NORMAL"
IZIBIZ_EARSIV_TYPE_INTERNET = "INTERNET"
IZIBIZ_SUB_STATUS_DRAFT = "DRAFT"
IZIBIZ_SUB_STATUS_NEW = "NEW"

#: ‹DOĞRULANACAK› — e-Fatura GÖNDERİMİ (``SendInvoice``) sandbox'ta hiç
#: denenmedi; talimat salt-okumaydı. İstek gövdesi WSDL'den doğru kurulmuş olsa
#: da ``INVOICE.CONTENT``'in e-Fatura tarafında da ZIP'lenmiş mi yoksa çıplak
#: UBL mi beklendiği ve imza modelinin (İzibiz mali mührü mü, bizim mührümüz mü)
#: ne olduğu ÖLÇÜLMEDİ. Bu bayrak açılana kadar EFATURA kanalına gönderim
#: gürültülü biçimde reddedilir — tahminle canlı belge üretilmez.
IZIBIZ_EFATURA_SUBMIT_VERIFIED = False
#: e-Arşiv durum sorgusunun ön koşulu. ``GetEArchiveInvoiceStatus`` yalnız UUID
#: kabul eder; ETTN olmadan sorgu kurulamaz ve bu, ağa çıkmadan söylenir.
IZIBIZ_EARCHIVE_STATUS_NEEDS_ETTN = (
    "e-Arşiv durum sorgusu ETTN (UUID) gerektirir; sağlayıcı belge kimliği yeterli değil"
)

IZIBIZ_EFATURA_SUBMIT_ERROR = (
    "e-Fatura gönderimi (SendInvoice) sandbox'ta doğrulanmadı; şimdilik yalnız e-Arşiv açık"
)

# --- Hata kodu → sınıf ---------------------------------------------------
#: Sandbox'ta gözlenen İzibiz iş hata kodları. **HTTP 200 gövdesinde** gelirler,
#: bu yüzden HTTP durum koduna göre sınıflandırma bunları YAKALAYAMAZ:
#: ``classify_http(200)`` "başarı" der, oysa belge reddedilmiştir.
IZIBIZ_ERROR_CODE_CLASSES: dict[str, str] = {
    "10003": "VALIDATION",  # "Belge kontrolden geçemedi: …"
    "10007": "VALIDATION",  # "Zip bir dosya içermelidir."
    "10013": "VALIDATION",  # "Gönderilen istek geçersizdir. / INVALID XML"
}

#: İzibiz'e özgü durum eşlemesi. Genel :data:`PROVIDER_STATUS_ALIASES`'a
#: karışmaz: "105"/"130" gibi çıplak sayılar yalnız bu sağlayıcıda anlamlı.
#: Anahtarlar :func:`~app.einvoice.status.map_provider_status` normalleştirmesine
#: göre yazılır (Türkçe harfler katlanır, boşluk/tire/alt çizgi atılır).
IZIBIZ_STATUS_ALIASES: dict[str, str] = {
    # e-Arşiv sayısal kodları (sandbox'ta gözlendi)
    "105": "PENDING",  # TASLAK NUMARASI OLARAK EKLENDİ
    "120": "SENT",  # RAPORLANACAK
    "130": "ACCEPTED",  # RAPORLANDI — e-Arşiv'de terminal başarı budur
    # e-Arşiv metinleri
    "TASLAKNUMARASIOLARAKEKLENDI": "PENDING",
    "RAPORLANACAK": "SENT",
    "RAPORLANDI": "ACCEPTED",
    "RAPORLANDIIPTAL": "REJECTED",
    # e-Fatura metinleri
    "SENDSUCCEED": "SENT",
    "SENDWAITAPPLICATIONRESPONSE": "SENT",
    "RECEIVESUCCEED": "SENT",
    # Kasıtlı olarak YOK: tek başına "SUCCEED". Taşımanın başarılı olması
    # GİB'in belgeyi kabul ettiği anlamına gelmez.
}


# =========================================================================
# Nes (REST/JSON) — ‹doğrulanacak›, değişmedi
# =========================================================================
#: ‹DOĞRULANACAK› Resmî Nes REST kökü. BOŞ = doğrulanmadı → çağrı yapılmaz.
NES_BASE_URL = ""
NES_ENDPOINTS_VERIFIED = False
#: ‹doğrulanacak› Yollar (spec §3 tablosu).
NES_LOGIN_PATH = "/auth/login"
NES_SUBMIT_PATH = "/invoices"
NES_STATUS_PATH = "/invoices/{ettn}/status"
NES_PDF_PATH = "/invoices/{ettn}/pdf"
NES_TAXPAYER_PATH = "/taxpayers/{vkn}"
#: ‹doğrulanacak› Token TTL'i bilinmiyor. 0 = her çağrıdan önce yeniden Login.
NES_TOKEN_TTL_SECONDS = 0
#: ‹doğrulanacak› API key zorunlu mu, bilinmiyor. False = gönderilmez.
NES_REQUIRES_API_KEY = False
NES_API_KEY_HEADER = "X-Api-Key"
#: ‹doğrulanacak› JSON yanıtında aranacak anahtarlar (sırayla).
NES_FIELD_TOKEN = ("access_token", "accessToken", "token")
NES_FIELD_ETTN = ("ettn", "uuid", "invoiceUuid", "documentUuid")
NES_FIELD_STATUS = ("status", "invoiceStatus", "state", "statusCode")
NES_FIELD_REASON = ("statusDescription", "description", "message", "reason")
NES_FIELD_ERROR_CODE = ("errorCode", "code", "error")
NES_FIELD_PDF = ("pdf", "content", "data", "fileContent")
NES_FIELD_TAXPAYER = ("isEinvoiceUser", "is_efatura_user", "registered", "result")


# --- Sağlayıcı durum kodu → iç durum -------------------------------------
#: Sağlayıcının/GİB'in döndürdüğü durum metinlerinin iç durum makinesine
#: eşlemesi (spec §5). Kasıtlı olarak DAR tutuldu: burada olmayan her kod
#: "bilinmiyor" sayılır ve ASLA ``ACCEPTED``'a düşmez. Genel amaçlı
#: "SUCCESS"/"OK" gibi taşıma başarısı bildiren jetonlar bilerek YOKTUR —
#: taşımanın başarılı olması GİB'in kabul ettiği anlamına gelmez.
PROVIDER_STATUS_ALIASES: dict[str, str] = {
    "PENDING": "PENDING",
    "QUEUED": "PENDING",
    "PROCESSING": "PENDING",
    "ISLENIYOR": "PENDING",
    "BEKLIYOR": "PENDING",
    "SENT": "SENT",
    "DELIVERED": "SENT",
    "GONDERILDI": "SENT",
    # Keys are compared after normalisation (Turkish letters folded, spaces /
    # dashes / underscores stripped), so they must be written folded too.
    "GIBGONDERILDI": "SENT",
    "ACCEPTED": "ACCEPTED",
    "APPROVED": "ACCEPTED",
    "KABUL": "ACCEPTED",
    "KABULEDILDI": "ACCEPTED",
    "REJECTED": "REJECTED",
    "DECLINED": "REJECTED",
    "RED": "REJECTED",
    "REDDEDILDI": "REJECTED",
    "IADE": "REJECTED",
}

#: Gövde metninde aranan hata sınıfı ipuçları. HTTP kodu tek başına
#: sınıflandırmaya yetmediğinde kullanılır (spec §6).
PROVIDER_ERROR_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("DUPLICATE", "DUPLICATE"),
    ("ALREADY", "DUPLICATE"),
    ("MUKERRER", "DUPLICATE"),
    ("ZATEN", "DUPLICATE"),
    ("QUOTA", "QUOTA"),
    ("KONTOR", "QUOTA"),
    ("LIMIT", "QUOTA"),
    ("KREDI", "QUOTA"),
    ("MUKELLEF", "TAXPAYER"),
    ("TAXPAYER", "TAXPAYER"),
    ("UNAUTHORIZED", "AUTH"),
    ("YETKISIZ", "AUTH"),
    ("OTURUM", "AUTH"),
    ("SCHEMA", "VALIDATION"),
    ("SEMA", "VALIDATION"),
    ("VALIDATION", "VALIDATION"),
    ("ZORUNLU", "VALIDATION"),
)


def is_configured(endpoint: str | None) -> bool:
    """True only for an absolute HTTPS endpoint an operator explicitly set."""
    value = (endpoint or "").strip()
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def resolve_endpoint(configured: Any, fallback: str) -> str:
    """Operator configuration (``EINVOICE_BASE_URL``) wins over the placeholder.

    The placeholder is empty for both providers, so in practice this returns
    ``""`` unless the operator supplied a real endpoint themselves.
    """
    value = (str(configured).strip() if configured else "") or fallback
    return value.rstrip("/")


def izibiz_service_url(base_url: str, operation: str) -> str:
    """Operasyonun ait olduğu servisin tam adresi.

    Üç servis tek tabandan türer ve aynı ``SESSION_ID``'yi paylaşır; ayrı ayrı
    yapılandırılmazlar ki biri test biri canlı gösteren bir karışım imkânsız olsun.
    """
    root = (base_url or "").rstrip("/")
    if not root:
        return ""
    if operation in IZIBIZ_AUTH_OPERATIONS:
        return f"{root}{IZIBIZ_AUTH_PATH}"
    if operation in IZIBIZ_ARCHIVE_OPERATIONS:
        return f"{root}{IZIBIZ_EARCHIVE_PATH}"
    return f"{root}{IZIBIZ_EINVOICE_PATH}"


def izibiz_namespace(operation: str) -> str:
    """Gövde kök elemanının ad alanı — e-Arşiv operasyonları ayrı ad alanında."""
    if operation in IZIBIZ_ARCHIVE_OPERATIONS:
        return IZIBIZ_SOAP_NAMESPACE_ARCHIVE
    return IZIBIZ_SOAP_NAMESPACE
