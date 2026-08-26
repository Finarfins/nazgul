"""İzibiz TEST (sandbox) duman testi — üretim yolunda DEĞİLDİR.

Bu betik ``app/einvoice/*`` içindeki hiçbir şeyi çağırmaz ve hiçbir şeyi
değiştirmez. Amacı tek bir şey: İzibiz'in **test** ortamına gerçek bir SOAP
çağrısı yapıp WSDL'den okunan operasyon/alan adlarının doğru olduğunu kanıtlamak
(``app/einvoice/endpoints.py`` içindeki ‹doğrulanacak› tahminlerin yerine ne
konacağını belirlemek).

Çalıştırma::

    python backend/sandbox/izibiz_smoke.py            # login + taslak + durum + logout
    python backend/sandbox/izibiz_smoke.py --login-only

Kimlik bilgileri ``backend/.env.izibiz.local`` (veya ``...local.txt``)
dosyasından okunur; betikte gömülü değer yoktur ve hiçbir kimlik bilgisi
(SESSION_ID dâhil) ekrana tam olarak yazılmaz.

CANLI KİLİDİ: her uç nokta, çağrı yapılmadan önce :func:`_assert_test_endpoint`
süzgecinden geçer. Ana makine adı "test" içermiyorsa veya bilinen bir canlı uç
ise betik çalışmayı reddeder.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree
from xml.sax.saxutils import escape as xml_escape

# --------------------------------------------------------------------------
# Uçlar — YALNIZCA test
# --------------------------------------------------------------------------
AUTH_URL = "https://efaturatest.izibiz.com.tr/AuthenticationWS"
EINVOICE_URL = "https://efaturatest.izibiz.com.tr/EInvoiceWS"
EARCHIVE_URL = "https://efaturatest.izibiz.com.tr/EIArchiveWS/EFaturaArchive"

NS_WSDL = "http://schemas.i2i.com/ei/wsdl"
NS_ARCHIVE = "http://schemas.i2i.com/ei/wsdl/archive"

#: Bilinen canlı uçlar. Bunlardan biri hedeflenirse betik durur.
LIVE_HOST_DENYLIST = (
    "authenticationws.izibiz.com.tr",
    "efaturaws.izibiz.com.tr",
    "earsivws.izibiz.com.tr",
    "api.izibiz.com.tr",
    "portal.izibiz.com.tr",
)

TIMEOUT = 60


class SmokeError(RuntimeError):
    """Duman testini durduran her şey."""


# --------------------------------------------------------------------------
# Güvenlik
# --------------------------------------------------------------------------
def _assert_test_endpoint(url: str) -> str:
    """Uç noktanın test ortamı olduğunu kanıtla; olmazsa çalışmayı reddet."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise SmokeError(f"Uç nokta çözümlenemedi: {url!r}")
    if host in LIVE_HOST_DENYLIST:
        raise SmokeError(f"CANLI UÇ ENGELLENDİ: {host}")
    if "test" not in host:
        raise SmokeError(
            f"Uç noktanın ana makine adı 'test' içermiyor ({host}); "
            "bu betik yalnızca sandbox'a çağrı yapar."
        )
    return url


def mask(value: str | None, keep: int = 6) -> str:
    """Sırrın yalnız ilk ``keep`` karakteri; gerisi maskelenir."""
    text = str(value or "")
    if not text:
        return "<boş>"
    return f"{text[:keep]}…({len(text)} karakter)"


_SECRETS: list[str] = []

#: Sır değil ama depoya girmemesi gereken değerler (VKN gibi). Yalnız
#: ``--capture`` çıktısında maskelenir; çalışma zamanı ekranında değil.
_CAPTURE_MASK: list[str] = []


def scrub(text: str) -> str:
    """Bilinen her sırrı metinden sil (hata gövdeleri ekrana basılmadan önce)."""
    out = text
    for secret in _SECRETS:
        if secret and len(secret) >= 4:
            out = out.replace(secret, "«gizli»")
    return out


# --------------------------------------------------------------------------
# Yapılandırma
# --------------------------------------------------------------------------
def load_credentials() -> dict[str, str]:
    """``backend/.env.izibiz.local[.txt]`` dosyasını oku.

    Hem satır başına bir ``K=V`` hem de tek satırda virgülle ayrılmış
    ``K=V, K=V`` biçimini kabul eder — dosyayı kimin nasıl yazdığına bağlı
    kalmamak için.
    """
    base = Path(__file__).resolve().parent.parent
    candidates = [
        base / ".env.izibiz.local",
        base / ".env.izibiz.local.txt",
    ]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        raise SmokeError(
            "Kimlik dosyası bulunamadı: " + " veya ".join(str(p) for p in candidates)
        )

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for chunk in line.split(","):
            if "=" not in chunk:
                continue
            key, _, value = chunk.partition("=")
            values[key.strip().upper()] = value.strip().strip('"').strip("'")

    missing = [k for k in ("IZIBIZ_USER", "IZIBIZ_PASS", "IZIBIZ_VKN") if not values.get(k)]
    if missing:
        raise SmokeError(f"{path.name} içinde eksik anahtar(lar): {', '.join(missing)}")

    # Ortam değişkeni dosyayı ezer (CI/geçici deneme için).
    for key in ("IZIBIZ_USER", "IZIBIZ_PASS", "IZIBIZ_VKN"):
        values[key] = os.environ.get(key) or values[key]

    _SECRETS.extend([values["IZIBIZ_USER"], values["IZIBIZ_PASS"]])
    _CAPTURE_MASK.append(values["IZIBIZ_VKN"])
    return values


# --------------------------------------------------------------------------
# SOAP taşıması
# --------------------------------------------------------------------------
#: ``--capture DİZİN`` verildiğinde her ham yanıt buraya, maskelenmiş yazılır.
_CAPTURE_DIR: Path | None = None


def _capture(operation: str, status: int, payload: bytes) -> None:
    """Ham yanıtı sözleşme testleri için sakla — sırlar maskelenmiş hâlde.

    Maskeleme diske yazmadan ÖNCE yapılır; fixture depoya girecek.
    """
    if _CAPTURE_DIR is None:
        return
    text = scrub(payload.decode("utf-8", errors="replace"))
    # Değer bazlı scrub, login yanıtındaki jetonu daha sır listesine girmeden
    # yakalayamaz; şekil bazlı ikinci geçiş onu da kapatır.
    text = re.sub(r"(<(?:\w+:)?SESSION_ID\s*>)[^<]+(</)", r"\1oturum-jetonu\2", text, flags=re.I)
    text = re.sub(r"(webValidationKey=)[0-9a-fA-F]+", r"\1web-anahtari", text)
    # VKN kimlik dosyasından gelir: fixture depoya girdiği için o da maskelenir.
    for value in _CAPTURE_MASK:
        text = text.replace(value, "9999999999"[: len(value)])
    _CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    target = _CAPTURE_DIR / f"{operation}.{status}.xml"
    target.write_text(text, encoding="utf-8")
    print(f"       [fixture] {target.name} ({len(text)} karakter)")


def soap_call(url: str, body: str, *, operation: str) -> bytes:
    """Tek bir document/literal SOAP çağrısı. SOAPAction boştur (WSDL'den)."""
    _assert_test_endpoint(url)
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soapenv:Header/>"
        f"<soapenv:Body>{body}</soapenv:Body>"
        "</soapenv:Envelope>"
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=envelope,
        method="POST",
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '""',
            "Accept": "text/xml, multipart/related",
            "User-Agent": "sungur-tarim-erp-izibiz-smoke/1.0",
        },
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:
            payload = response.read()
            _capture(operation, response.status, payload)
            return payload
    except urllib.error.HTTPError as exc:  # SOAP Fault de buraya düşer (HTTP 500)
        payload = exc.read()
        _capture(operation, exc.code, payload)
        detail = describe_fault(payload)
        raise SmokeError(f"{operation}: HTTP {exc.code} — {detail}") from None
    except urllib.error.URLError as exc:
        raise SmokeError(f"{operation}: ağ hatası — {exc.reason}") from None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse(payload: bytes) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(payload.decode("utf-8", errors="replace"))
    except ElementTree.ParseError as exc:
        raise SmokeError(f"Yanıt XML olarak okunamadı: {exc}") from None


def find_text(root: ElementTree.Element, name: str) -> str | None:
    for element in root.iter():
        if _local(element.tag) == name:
            text = (element.text or "").strip()
            if text:
                return text
    return None


def find_all(root: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [e for e in root.iter() if _local(e.tag) == name]


def describe_fault(payload: bytes) -> str:
    """SOAP Fault / ERROR_TYPE gövdesinden okunabilir bir özet çıkar."""
    try:
        root = parse(payload)
    except SmokeError:
        return scrub(payload[:600].decode("utf-8", errors="replace"))
    parts = []
    for name in ("ERROR_CODE", "ERROR_SHORT_DES", "ERROR_LONG_DES", "faultstring", "faultcode"):
        value = find_text(root, name)
        if value:
            parts.append(f"{name}={value}")
    return scrub("; ".join(parts) or payload[:600].decode("utf-8", errors="replace"))


def raise_on_error(root: ElementTree.Element, operation: str) -> None:
    """Gövde 200 dönse bile ``ERROR_TYPE`` varsa bu bir başarısızlıktır."""
    for element in find_all(root, "ERROR_TYPE"):
        code = find_text(element, "ERROR_CODE")
        short = find_text(element, "ERROR_SHORT_DES")
        long_desc = find_text(element, "ERROR_LONG_DES")
        if code or short:
            raise SmokeError(scrub(f"{operation}: ERROR_CODE={code} {short or ''} {long_desc or ''}".strip()))


def request_header(session_id: str) -> str:
    return (
        "<REQUEST_HEADER>"
        f"<SESSION_ID>{xml_escape(session_id)}</SESSION_ID>"
        "<APPLICATION_NAME>SungurTarimERP-Smoke</APPLICATION_NAME>"
        "<CHANNEL_NAME>WS</CHANNEL_NAME>"
        "</REQUEST_HEADER>"
    )


# --------------------------------------------------------------------------
# İşlemler
# --------------------------------------------------------------------------
def login(username: str, password: str) -> str:
    body = (
        f'<ws:LoginRequest xmlns:ws="{NS_WSDL}">'
        # Login'de oturum henüz yok; şema SESSION_ID'yi zorunlu kıldığı için
        # yer tutucu gönderilir.
        "<REQUEST_HEADER><SESSION_ID>-1</SESSION_ID>"
        "<APPLICATION_NAME>SungurTarimERP-Smoke</APPLICATION_NAME></REQUEST_HEADER>"
        f"<USER_NAME>{xml_escape(username)}</USER_NAME>"
        f"<PASSWORD>{xml_escape(password)}</PASSWORD>"
        "</ws:LoginRequest>"
    )
    root = parse(soap_call(AUTH_URL, body, operation="Login"))
    raise_on_error(root, "Login")
    session_id = find_text(root, "SESSION_ID")
    if not session_id:
        raise SmokeError("Login: yanıtta SESSION_ID yok")
    _SECRETS.append(session_id)
    return session_id


def logout(session_id: str) -> str | None:
    body = (
        f'<ws:LogoutRequest xmlns:ws="{NS_WSDL}">'
        f"{request_header(session_id)}"
        "</ws:LogoutRequest>"
    )
    root = parse(soap_call(AUTH_URL, body, operation="Logout"))
    raise_on_error(root, "Logout")
    return find_text(root, "RETURN_CODE")


def check_user(session_id: str, identifier: str) -> list[dict[str, str]]:
    """GİB e-Fatura mükellef sorgusu (EFATURA ↔ EARSIV yönlendirmesi için)."""
    body = (
        f'<ws:CheckUserRequest xmlns:ws="{NS_WSDL}">'
        f"{request_header(session_id)}"
        f"<USER><IDENTIFIER>{xml_escape(identifier)}</IDENTIFIER></USER>"
        "<DOCUMENT_TYPE>INVOICE</DOCUMENT_TYPE>"
        "</ws:CheckUserRequest>"
    )
    root = parse(soap_call(AUTH_URL, body, operation="CheckUser"))
    raise_on_error(root, "CheckUser")
    users = []
    for element in find_all(root, "USER"):
        users.append(
            {
                "IDENTIFIER": find_text(element, "IDENTIFIER") or "",
                "ALIAS": find_text(element, "ALIAS") or "",
                "TITLE": find_text(element, "TITLE") or "",
                "TYPE": find_text(element, "TYPE") or "",
            }
        )
    return users


def zip_single(name: str, payload: bytes) -> bytes:
    """Tek girdili, deterministik bir ZIP üret (mtime sabit ⇒ tekrar üretilebilir)."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, payload)
    return buffer.getvalue()


def get_inbox(session_id: str, *, days: int = 7, limit: int = 5) -> list[dict[str, str]]:
    """EInvoiceWS gelen kutusu — salt okuma; hiçbir şey göndermez, işaretlemez.

    ``READ_INCLUDED=true`` bilerek verilir: varsayılan davranış okunmamışları
    okundu saymak olabileceği için, sorgu belgelerin okundu bilgisini
    değiştirmemeli.
    """
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    body = (
        f'<ws:GetInvoiceRequest xmlns:ws="{NS_WSDL}">'
        f"{request_header(session_id)}"
        "<INVOICE_SEARCH_KEY>"
        f"<LIMIT>{limit}</LIMIT>"
        "<DATE_TYPE>CREATE</DATE_TYPE>"  # şemadaki enum: ISSUE | CREATE
        f"<START_DATE>{start.isoformat()}</START_DATE>"
        f"<END_DATE>{end.isoformat()}</END_DATE>"
        "<READ_INCLUDED>true</READ_INCLUDED>"
        "<DIRECTION>IN</DIRECTION>"
        "</INVOICE_SEARCH_KEY>"
        "<HEADER_ONLY>Y</HEADER_ONLY>"
        "</ws:GetInvoiceRequest>"
    )
    root = parse(soap_call(EINVOICE_URL, body, operation="GetInvoice"))
    raise_on_error(root, "GetInvoice")
    rows = []
    for element in find_all(root, "INVOICE"):
        header = next((c for c in element if _local(c.tag) == "HEADER"), element)
        rows.append(
            {
                # UUID/ID gövdede değil, INVOICE elemanının ÖZNİTELİĞİNDE durur.
                "UUID": element.get("UUID") or "",
                "ID": element.get("ID") or "",
                "SENDER": find_text(header, "SENDER") or "",
                "ISSUE_DATE": find_text(header, "ISSUE_DATE") or "",
                "STATUS": find_text(header, "STATUS") or "",
                "PROFILEID": find_text(header, "PROFILEID") or "",
            }
        )
    return rows


def get_invoice_status(session_id: str, invoice_uuid: str) -> dict[str, str]:
    """e-Fatura durum sorgusu. ``INVOICE`` elemanı UUID'yi ÖZNİTELİK olarak alır."""
    body = (
        f'<ws:GetInvoiceStatusRequest xmlns:ws="{NS_WSDL}">'
        f"{request_header(session_id)}"
        f'<INVOICE UUID="{xml_escape(invoice_uuid)}"/>'
        "</ws:GetInvoiceStatusRequest>"
    )
    root = parse(soap_call(EINVOICE_URL, body, operation="GetInvoiceStatus"))
    raise_on_error(root, "GetInvoiceStatus")
    return {
        "STATUS": find_text(root, "STATUS") or "",
        "STATUS_CODE": find_text(root, "STATUS_CODE") or "",
        "STATUS_DESCRIPTION": find_text(root, "STATUS_DESCRIPTION") or "",
        "GIB_STATUS_CODE": find_text(root, "GIB_STATUS_CODE") or "",
    }


def write_to_archive_extended(
    session_id: str, *, invoice_uuid: str, invoice_id: str, ubl_xml: bytes, draft: bool
) -> dict[str, str | None]:
    """e-Arşiv faturayı İzibiz'e yükle (``SUB_STATUS=DRAFT`` → taslak).

    ``INVOICE_CONTENT`` düz UBL değil, **tek dosyalık bir ZIP'in** base64'üdür —
    çıplak XML gönderildiğinde servis ``ERROR_CODE=10007 "Zip bir dosya
    içermelidir."`` ile reddediyor.
    """
    content = base64.b64encode(zip_single(f"{invoice_id}.xml", ubl_xml)).decode("ascii")
    body = (
        f'<arc:ArchiveInvoiceExtendedRequest xmlns:arc="{NS_ARCHIVE}">'
        f"{request_header(session_id)}"
        "<ArchiveInvoiceExtendedContent>"
        "<INVOICE_PROPERTIES>"
        "<EARSIV_FLAG>Y</EARSIV_FLAG>"
        "<EARSIV_PROPERTIES>"
        "<EARSIV_TYPE>NORMAL</EARSIV_TYPE>"
        "<EARSIV_EMAIL_FLAG>N</EARSIV_EMAIL_FLAG>"
        f"<SUB_STATUS>{'DRAFT' if draft else 'NEW'}</SUB_STATUS>"
        "<EARCHIVE_TEST_FLAG>Y</EARCHIVE_TEST_FLAG>"
        "<VALIDATION_FLAG>Y</VALIDATION_FLAG>"
        "</EARSIV_PROPERTIES>"
        "<PDF_PROPERTIES><EARSIV_PDF_FLAG>N</EARSIV_PDF_FLAG></PDF_PROPERTIES>"
        "<ARCHIVE_NOTE>sandbox smoke</ARCHIVE_NOTE>"
        f"<INVOICE_CONTENT>{content}</INVOICE_CONTENT>"
        "</INVOICE_PROPERTIES>"
        "</ArchiveInvoiceExtendedContent>"
        "</arc:ArchiveInvoiceExtendedRequest>"
    )
    root = parse(soap_call(EARCHIVE_URL, body, operation="WriteToArchiveExtended"))
    raise_on_error(root, "WriteToArchiveExtended")
    return {
        "INVOICE_ID": find_text(root, "INVOICE_ID") or invoice_id,
        "WEB_KEY": find_text(root, "WEB_KEY"),
        "RETURN_CODE": find_text(root, "RETURN_CODE"),
        "UUID": invoice_uuid,
    }


def get_earchive_status(session_id: str, invoice_uuid: str) -> list[dict[str, str]]:
    body = (
        f'<arc:GetEArchiveInvoiceStatusRequest xmlns:arc="{NS_ARCHIVE}">'
        f"{request_header(session_id)}"
        f"<UUID>{xml_escape(invoice_uuid)}</UUID>"
        "</arc:GetEArchiveInvoiceStatusRequest>"
    )
    root = parse(soap_call(EARCHIVE_URL, body, operation="GetEArchiveInvoiceStatus"))
    raise_on_error(root, "GetEArchiveInvoiceStatus")
    rows = []
    for element in find_all(root, "HEADER"):
        rows.append(
            {
                "INVOICE_ID": find_text(element, "INVOICE_ID") or "",
                "UUID": find_text(element, "UUID") or "",
                "STATUS": find_text(element, "STATUS") or "",
                "STATUS_DESC": find_text(element, "STATUS_DESC") or "",
                "PROFILE": find_text(element, "PROFILE") or "",
                "WEB_KEY": find_text(element, "WEB_KEY") or "",
            }
        )
    return rows


# --------------------------------------------------------------------------
# UBL-TR 2.1 e-Arşiv fatura (asgari, uydurma müşteri, 1 kalem)
# --------------------------------------------------------------------------
#: UBL-TR, belgenin kendi görselleştirme şablonunu taşımasını ister. Şablon
#: gömülü değilken İzibiz ``ERROR_CODE=10013 "Belge içerisinde şablon
#: bulunamamıştır."`` diyerek reddediyor. Asgari ama geçerli bir XSLT yeter.
_XSLT_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
    xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:n1="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
    exclude-result-prefixes="n1 cbc cac">
  <xsl:output method="html" encoding="UTF-8" indent="yes"/>
  <xsl:template match="/">
    <html><head><title>Fatura</title></head><body>
      <h2>e-Arsiv Fatura</h2>
      <p>Fatura No: <xsl:value-of select="/n1:Invoice/cbc:ID"/></p>
      <p>ETTN: <xsl:value-of select="/n1:Invoice/cbc:UUID"/></p>
      <p>Tarih: <xsl:value-of select="/n1:Invoice/cbc:IssueDate"/></p>
      <table border="1" cellspacing="0" cellpadding="3">
        <tr><th>Aciklama</th><th>Miktar</th><th>Birim Fiyat</th><th>Tutar</th></tr>
        <xsl:for-each select="/n1:Invoice/cac:InvoiceLine">
          <tr>
            <td><xsl:value-of select="cac:Item/cbc:Name"/></td>
            <td><xsl:value-of select="cbc:InvoicedQuantity"/></td>
            <td><xsl:value-of select="cac:Price/cbc:PriceAmount"/></td>
            <td><xsl:value-of select="cbc:LineExtensionAmount"/></td>
          </tr>
        </xsl:for-each>
      </table>
      <p>Odenecek Tutar:
        <xsl:value-of select="/n1:Invoice/cac:LegalMonetaryTotal/cbc:PayableAmount"/>
      </p>
    </body></html>
  </xsl:template>
</xsl:stylesheet>
"""


def build_ubl(
    *,
    invoice_uuid: str,
    invoice_id: str,
    supplier_vkn: str,
    issued: dt.datetime,
    unit_price: str = "100.00",
    vat_rate: str = "20",
) -> bytes:
    net = "100.00"
    vat = "20.00"
    gross = "120.00"
    issue_date = issued.strftime("%Y-%m-%d")
    issue_time = issued.strftime("%H:%M:%S")
    xslt_b64 = base64.b64encode(_XSLT_TEMPLATE.encode("utf-8")).decode("ascii")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2">
  <ext:UBLExtensions><ext:UBLExtension><ext:ExtensionContent/></ext:UBLExtension></ext:UBLExtensions>
  <cbc:UBLVersionID>2.1</cbc:UBLVersionID>
  <cbc:CustomizationID>TR1.2</cbc:CustomizationID>
  <cbc:ProfileID>EARSIVFATURA</cbc:ProfileID>
  <cbc:ID>{invoice_id}</cbc:ID>
  <cbc:CopyIndicator>false</cbc:CopyIndicator>
  <cbc:UUID>{invoice_uuid}</cbc:UUID>
  <cbc:IssueDate>{issue_date}</cbc:IssueDate>
  <cbc:IssueTime>{issue_time}</cbc:IssueTime>
  <cbc:InvoiceTypeCode>SATIS</cbc:InvoiceTypeCode>
  <cbc:Note>Sandbox duman testi — gercek bir ticari islem degildir.</cbc:Note>
  <cbc:DocumentCurrencyCode>TRY</cbc:DocumentCurrencyCode>
  <cbc:LineCountNumeric>1</cbc:LineCountNumeric>
  <cac:AdditionalDocumentReference>
    <cbc:ID>{invoice_id}</cbc:ID>
    <cbc:IssueDate>{issue_date}</cbc:IssueDate>
    <cbc:DocumentTypeCode>SendingType</cbc:DocumentTypeCode>
    <cbc:DocumentType>ELEKTRONIK</cbc:DocumentType>
  </cac:AdditionalDocumentReference>
  <cac:AdditionalDocumentReference>
    <cbc:ID>{invoice_id}</cbc:ID>
    <cbc:IssueDate>{issue_date}</cbc:IssueDate>
    <cbc:DocumentType>XSLT</cbc:DocumentType>
    <cac:Attachment>
      <cbc:EmbeddedDocumentBinaryObject mimeCode="application/xml" encodingCode="Base64"
          characterSetCode="UTF-8" filename="{invoice_id}.xslt">{xslt_b64}</cbc:EmbeddedDocumentBinaryObject>
    </cac:Attachment>
  </cac:AdditionalDocumentReference>
  <cac:Signature>
    <cbc:ID schemeID="VKN_TCKN">{supplier_vkn}</cbc:ID>
    <cac:SignatoryParty>
      <cac:PartyIdentification><cbc:ID schemeID="VKN">{supplier_vkn}</cbc:ID></cac:PartyIdentification>
      <cac:PostalAddress>
        <cbc:CitySubdivisionName>Merkez</cbc:CitySubdivisionName>
        <cbc:CityName>Sivas</cbc:CityName>
        <cac:Country><cbc:Name>Turkiye</cbc:Name></cac:Country>
      </cac:PostalAddress>
    </cac:SignatoryParty>
    <cac:DigitalSignatureAttachment>
      <cac:ExternalReference><cbc:URI>#Signature</cbc:URI></cac:ExternalReference>
    </cac:DigitalSignatureAttachment>
  </cac:Signature>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cbc:WebsiteURI/>
      <cac:PartyIdentification><cbc:ID schemeID="VKN">{supplier_vkn}</cbc:ID></cac:PartyIdentification>
      <cac:PartyName><cbc:Name>Sandbox Test Satici</cbc:Name></cac:PartyName>
      <cac:PostalAddress>
        <cbc:StreetName>Test Caddesi No 1</cbc:StreetName>
        <cbc:CitySubdivisionName>Merkez</cbc:CitySubdivisionName>
        <cbc:CityName>Sivas</cbc:CityName>
        <cac:Country><cbc:Name>Turkiye</cbc:Name></cac:Country>
      </cac:PostalAddress>
      <cac:PartyTaxScheme><cac:TaxScheme><cbc:Name>Merkez</cbc:Name></cac:TaxScheme></cac:PartyTaxScheme>
      <cac:Contact><cbc:ElectronicMail>sandbox@example.invalid</cbc:ElectronicMail></cac:Contact>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty>
    <cac:Party>
      <cac:PartyIdentification><cbc:ID schemeID="TCKN">11111111111</cbc:ID></cac:PartyIdentification>
      <cac:PostalAddress>
        <cbc:StreetName>Ornek Sokak No 2</cbc:StreetName>
        <cbc:CitySubdivisionName>Merkez</cbc:CitySubdivisionName>
        <cbc:CityName>Sivas</cbc:CityName>
        <cac:Country><cbc:Name>Turkiye</cbc:Name></cac:Country>
      </cac:PostalAddress>
      <cac:Person>
        <cbc:FirstName>Deneme</cbc:FirstName>
        <cbc:FamilyName>Musteri</cbc:FamilyName>
      </cac:Person>
    </cac:Party>
  </cac:AccountingCustomerParty>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="TRY">{vat}</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="TRY">{net}</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="TRY">{vat}</cbc:TaxAmount>
      <cbc:Percent>{vat_rate}</cbc:Percent>
      <cac:TaxCategory>
        <cac:TaxScheme>
          <cbc:Name>KDV</cbc:Name>
          <cbc:TaxTypeCode>0015</cbc:TaxTypeCode>
        </cac:TaxScheme>
      </cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="TRY">{net}</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="TRY">{net}</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="TRY">{gross}</cbc:TaxInclusiveAmount>
    <cbc:AllowanceTotalAmount currencyID="TRY">0.00</cbc:AllowanceTotalAmount>
    <cbc:PayableAmount currencyID="TRY">{gross}</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity unitCode="C62">1</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="TRY">{net}</cbc:LineExtensionAmount>
    <cac:TaxTotal>
      <cbc:TaxAmount currencyID="TRY">{vat}</cbc:TaxAmount>
      <cac:TaxSubtotal>
        <cbc:TaxableAmount currencyID="TRY">{net}</cbc:TaxableAmount>
        <cbc:TaxAmount currencyID="TRY">{vat}</cbc:TaxAmount>
        <cbc:Percent>{vat_rate}</cbc:Percent>
        <cac:TaxCategory>
          <cac:TaxScheme>
            <cbc:Name>KDV</cbc:Name>
            <cbc:TaxTypeCode>0015</cbc:TaxTypeCode>
          </cac:TaxScheme>
        </cac:TaxCategory>
      </cac:TaxSubtotal>
    </cac:TaxTotal>
    <cac:Item><cbc:Name>Sandbox Test Kalemi</cbc:Name></cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="TRY">{unit_price}</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>
</Invoice>
""".encode("utf-8")


def next_invoice_id(prefix: str, issued: dt.datetime, serial: int) -> str:
    """UBL-TR fatura numarası: 3 harf + 4 haneli yıl + 9 hane."""
    if not re.fullmatch(r"[A-Z]{3}", prefix):
        raise SmokeError("Fatura seri öneki 3 büyük harf olmalı")
    return f"{prefix}{issued.year}{serial:09d}"


# --------------------------------------------------------------------------
# Akış
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="İzibiz TEST duman testi")
    parser.add_argument("--login-only", action="store_true", help="Yalnız Login/Logout")
    parser.add_argument("--send", action="store_true", help="Taslak değil, gerçek gönderim (SUB_STATUS=NEW)")
    parser.add_argument("--prefix", default="SNG", help="Fatura seri öneki (3 büyük harf)")
    parser.add_argument("--serial", type=int, default=None, help="Fatura sıra numarası")
    parser.add_argument("--dump-ubl", action="store_true", help="Üretilen UBL'i yazdır ve çık")
    parser.add_argument(
        "--capture",
        metavar="DİZİN",
        default=None,
        help="Ham yanıtları (maskelenmiş) sözleşme testi fixture'ı olarak bu dizine yaz",
    )
    args = parser.parse_args(argv)

    global _CAPTURE_DIR
    if args.capture:
        _CAPTURE_DIR = Path(args.capture)

    for url in (AUTH_URL, EINVOICE_URL, EARCHIVE_URL):
        _assert_test_endpoint(url)
    print(f"[kilit] uçlar test ortamı olarak doğrulandı: {urlparse(AUTH_URL).hostname}")

    credentials = load_credentials()
    issued = dt.datetime.now()
    # %j%H%M%S ⇒ en fazla 9 hane (366235959); %m%d%H%M%S aralık dışına taşardı.
    serial = args.serial if args.serial is not None else int(issued.strftime("%j%H%M%S"))
    invoice_id = next_invoice_id(args.prefix, issued, serial)
    invoice_uuid = str(uuid.uuid4()).upper()

    if args.dump_ubl:
        sys.stdout.write(
            build_ubl(
                invoice_uuid=invoice_uuid,
                invoice_id=invoice_id,
                supplier_vkn=credentials["IZIBIZ_VKN"],
                issued=issued,
            ).decode("utf-8")
        )
        return 0

    session_id = login(credentials["IZIBIZ_USER"], credentials["IZIBIZ_PASS"])
    print(f"[1/4] Login OK — SESSION_ID={mask(session_id)}")

    try:
        if args.login_only:
            return 0

        try:
            users = check_user(session_id, credentials["IZIBIZ_VKN"])
            print(f"[1b ] CheckUser({mask(credentials['IZIBIZ_VKN'], 3)}) → {len(users)} etiket")
            for user in users[:5]:
                print(f"       ALIAS={user['ALIAS']} TYPE={user['TYPE']} TITLE={user['TITLE'][:40]}")
        except SmokeError as exc:
            print(f"[1b ] CheckUser atlandı: {exc}")

        try:
            inbox = get_inbox(session_id)
            print(f"[1c ] GetInvoice (gelen kutusu, son 7 gün, salt okuma) → {len(inbox)} fatura")
            for row in inbox[:3]:
                print(f"       SENDER={row['SENDER']} {row['ISSUE_DATE']} STATUS={row['STATUS']}")
            uuids = [row["UUID"] for row in inbox if row["UUID"]]
            if uuids:
                efatura_status = get_invoice_status(session_id, uuids[0])
                print(
                    f"       GetInvoiceStatus({uuids[0][:8]}…) → STATUS={efatura_status['STATUS']} "
                    f"CODE={efatura_status['STATUS_CODE']} GIB={efatura_status['GIB_STATUS_CODE']}"
                )
        except SmokeError as exc:
            print(f"[1c ] GetInvoice atlandı: {exc}")

        if _CAPTURE_DIR is not None:
            # Hata gövdesi de sözleşmenin parçası: bilerek ZIP'siz içerik
            # göndererek 10007 fault'unu kaydet. Bu çağrı BAŞARISIZ olmalı.
            try:
                bad = (
                    f'<arc:ArchiveInvoiceExtendedRequest xmlns:arc="{NS_ARCHIVE}">'
                    f"{request_header(session_id)}"
                    "<ArchiveInvoiceExtendedContent><INVOICE_PROPERTIES>"
                    "<EARSIV_FLAG>Y</EARSIV_FLAG>"
                    "<EARSIV_PROPERTIES><EARSIV_TYPE>NORMAL</EARSIV_TYPE>"
                    "<SUB_STATUS>DRAFT</SUB_STATUS></EARSIV_PROPERTIES>"
                    "<INVOICE_CONTENT>"
                    + base64.b64encode(b"<Invoice/>").decode("ascii")
                    + "</INVOICE_CONTENT>"
                    "</INVOICE_PROPERTIES></ArchiveInvoiceExtendedContent>"
                    "</arc:ArchiveInvoiceExtendedRequest>"
                )
                # Not: İzibiz iş hatalarını HTTP 200 + <ERROR_TYPE> ile döner,
                # SOAP Fault ile değil. Bu yüzden ret ancak gövde okunarak görülür.
                raise_on_error(
                    parse(soap_call(EARCHIVE_URL, bad, operation="WriteToArchiveExtended-fault")),
                    "WriteToArchiveExtended-fault",
                )
                print("       UYARI: geçersiz içerik reddedilmedi — fault fixture'ı alınamadı")
            except SmokeError as exc:
                print(f"       [fault] beklenen ret alındı: {exc}")

        ubl = build_ubl(
            invoice_uuid=invoice_uuid,
            invoice_id=invoice_id,
            supplier_vkn=credentials["IZIBIZ_VKN"],
            issued=issued,
        )
        print(f"[2/4] e-Arşiv {'gönderim' if args.send else 'TASLAK'} → ID={invoice_id} UUID={invoice_uuid}")
        result = write_to_archive_extended(
            session_id,
            invoice_uuid=invoice_uuid,
            invoice_id=invoice_id,
            ubl_xml=ubl,
            draft=not args.send,
        )
        print(f"       RETURN_CODE={result['RETURN_CODE']} INVOICE_ID={result['INVOICE_ID']} WEB_KEY={result['WEB_KEY']}")

        rows = get_earchive_status(session_id, invoice_uuid)
        print(f"[3/4] GetEArchiveInvoiceStatus → {len(rows)} kayıt")
        for row in rows:
            print(
                f"       INVOICE_ID={row['INVOICE_ID']} UUID={row['UUID']} "
                f"STATUS={row['STATUS']} ({row['STATUS_DESC']}) PROFILE={row['PROFILE']}"
            )
        if not rows:
            print("       UYARI: durum sorgusu boş döndü — fatura henüz işlenmemiş olabilir.")
        return 0
    finally:
        try:
            code = logout(session_id)
            print(f"[4/4] Logout OK — RETURN_CODE={code}")
        except SmokeError as exc:
            print(f"[4/4] Logout başarısız: {exc}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeError as exc:
        print(f"DURDU: {exc}", file=sys.stderr)
        sys.exit(2)
