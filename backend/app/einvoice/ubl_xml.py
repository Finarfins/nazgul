"""UBL-TR 2.1 XML üretimi ve İzibiz'in beklediği paketleme.

:mod:`app.einvoice.ubl` faturayı bir **sözlüğe** çevirir (tutar sözleşmesi,
istemci ETTN'i, kanal kararı). Bu modül o sözlüğü **tel üstünde gerçekten kabul
edilen** bayta çevirir.

Üç kural şemada YAZMIYOR. Üçü de sandbox'ta reddedilerek bulundu; kaynağı
her birinin yanında, İzibiz'in kendi hata koduyla birlikte duruyor
(kayıt: ``docs/izibiz-sandbox-bulgular.md`` §4, hata gövdesi fixture'ı:
``backend/tests/fixtures/izibiz/WriteToArchiveExtended-fault.200.xml``):

1. **İçerik çıplak XML değil, tek dosyalık bir ZIP'tir.**
   Çıplak UBL gönderildiğinde: ``ERROR_CODE=10007 "Zip bir dosya içermelidir."``
   → :func:`package_invoice`
2. **Belge kendi görselleştirme XSLT'sini taşımalıdır.**
   Şablonsuz gönderimde: ``ERROR_CODE=10013 "Belge içerisinde şablon
   bulunamamıştır."`` → :data:`DEFAULT_XSLT`, ``DocumentType=XSLT`` referansı
3. **Gönderim şekli açıkça bildirilmelidir.**
   Referanssız gönderimde: ``ERROR_CODE=10003 "eArşiv belge gönderim şekli boş
   olamaz"`` → ``DocumentTypeCode=SendingType`` + ``DocumentType=ELEKTRONIK``

İmza: e-Arşiv'de ``INVOICE_CONTENT`` **imzasız** UBL kabul eder; mali mührü
İzibiz sunucu tarafında atar (sandbox'ta doğrulandı — 5 belge imzasız kabul
edildi). e-Fatura tarafının imza modeli ölçülmedi; bkz.
:data:`~app.einvoice.endpoints.IZIBIZ_EFATURA_SUBMIT_VERIFIED`.
"""

from __future__ import annotations

import base64
import zipfile
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape as xml_escape
from xml.sax.saxutils import quoteattr


#: UBL-TR fatura numarası: 3 harf + 4 haneli yıl + 9 hane = 16 karakter.
INVOICE_ID_LENGTH = 16

#: ``AdditionalDocumentReference`` ayrımı — GİB e-Arşiv kılavuzu değerleri.
SENDING_TYPE_CODE = "SendingType"
SENDING_TYPE_ELECTRONIC = "ELEKTRONIK"
SENDING_TYPE_PAPER = "KAGIT"
XSLT_DOCUMENT_TYPE = "XSLT"

#: TCKN 11 hane, VKN 10. Şema kimliği buna göre seçilir; tahmin yok.
TCKN_LENGTH = 11

_NS = (
    'xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
    'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
    'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" '
    'xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"'
)

#: Asgari ama geçerli görselleştirme şablonu. Kurumsal bir tasarım değil —
#: kural 2'yi karşılamak ve belgenin kendi kendine görüntülenebilir olmasını
#: sağlamak için var. Operatör kendi XSLT'sini verirse o kullanılır.
DEFAULT_XSLT = """<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
    xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:n1="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
    exclude-result-prefixes="n1 cbc cac">
  <xsl:output method="html" encoding="UTF-8" indent="yes"/>
  <xsl:template match="/">
    <html><head><title>Fatura</title></head><body>
      <h2>e-Fatura / e-Arsiv Fatura</h2>
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


class UblBuildError(ValueError):
    """UBL üretilemedi — eksik/uyumsuz alan. Yarım belge üretmektense hata."""


def _text(value: Any) -> str:
    return xml_escape("" if value is None else str(value))


def _identifier_scheme(number: str) -> str:
    """VKN mi TCKN mi — uzunluktan. Bilinmiyorsa VKN varsayılmaz, hata verilir."""
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    if len(digits) == TCKN_LENGTH:
        return "TCKN"
    if len(digits) == 10:
        return "VKN"
    raise UblBuildError(f"VKN/TCKN 10 veya 11 haneli olmalı: {len(digits)} hane")


def _party(
    *,
    number: str,
    name: str,
    address: Any,
    tax_office: Any = None,
    email: Any = None,
) -> str:
    """Tek bir taraf (satıcı/alıcı). TCKN ise gerçek kişi, VKN ise tüzel kişi."""
    scheme = _identifier_scheme(number)
    digits = "".join(ch for ch in str(number) if ch.isdigit())
    street = _text(address or "")
    parts = [
        "<cac:Party>",
        f'<cac:PartyIdentification><cbc:ID schemeID="{scheme}">{_text(digits)}'
        "</cbc:ID></cac:PartyIdentification>",
    ]
    if scheme == "VKN":
        parts.append(f"<cac:PartyName><cbc:Name>{_text(name)}</cbc:Name></cac:PartyName>")
    parts.append(
        "<cac:PostalAddress>"
        f"<cbc:StreetName>{street}</cbc:StreetName>"
        "<cbc:CitySubdivisionName>-</cbc:CitySubdivisionName>"
        "<cbc:CityName>-</cbc:CityName>"
        "<cac:Country><cbc:Name>Türkiye</cbc:Name></cac:Country>"
        "</cac:PostalAddress>"
    )
    if scheme == "VKN":
        parts.append(
            "<cac:PartyTaxScheme><cac:TaxScheme>"
            f"<cbc:Name>{_text(tax_office or '-')}</cbc:Name>"
            "</cac:TaxScheme></cac:PartyTaxScheme>"
        )
    else:
        # Gerçek kişide unvan yerine ad/soyad. Tek kelimelik adda soyad boş
        # bırakılmaz — şema zorunlu tutuyor.
        words = str(name or "").split()
        first = " ".join(words[:-1]) if len(words) > 1 else (words[0] if words else "-")
        family = words[-1] if len(words) > 1 else "-"
        parts.append(
            "<cac:Person>"
            f"<cbc:FirstName>{_text(first)}</cbc:FirstName>"
            f"<cbc:FamilyName>{_text(family)}</cbc:FamilyName>"
            "</cac:Person>"
        )
    if email:
        parts.append(f"<cac:Contact><cbc:ElectronicMail>{_text(email)}</cbc:ElectronicMail></cac:Contact>")
    parts.append("</cac:Party>")
    return "".join(parts)


def _amount(currency: str, value: Any) -> str:
    return f'currencyID={quoteattr(currency)}>{_text(value)}'


def build_invoice_xml(
    payload: dict[str, Any],
    *,
    xslt: str | None = None,
    sending_type: str = SENDING_TYPE_ELECTRONIC,
) -> bytes:
    """``build_einvoice_payload`` çıktısını UBL-TR 2.1 XML'e çevir.

    Eksik zorunlu alanda :class:`UblBuildError` fırlatır; yarım bir belge
    üretip sağlayıcının reddetmesini beklemez.
    """
    payload = payload or {}
    invoice_id = str(payload.get("invoice_number") or "").strip()
    ettn = str(payload.get("uuid") or "").strip()
    profile = str(payload.get("profile_id") or "").strip()
    issue_date = str(payload.get("issue_date") or "").strip()
    if not invoice_id or not ettn or not profile or not issue_date:
        raise UblBuildError("UBL için fatura no, ETTN, profil ve tarih zorunlu")

    issue_time = str(payload.get("issue_time") or "00:00:00").strip()
    currency = str(payload.get("currency") or "TRY").upper()
    supplier = payload.get("supplier") or {}
    customer = payload.get("customer") or {}
    lines = payload.get("lines") or []
    if not lines:
        raise UblBuildError("UBL için en az bir fatura satırı zorunlu")
    totals = payload.get("monetary_total") or {}
    subtotals = payload.get("tax_subtotals") or []
    supplier_vkn = "".join(ch for ch in str(supplier.get("vkn") or "") if ch.isdigit())

    def tax_subtotal(taxable: Any, tax: Any, rate: Any) -> str:
        return (
            "<cac:TaxSubtotal>"
            f"<cbc:TaxableAmount {_amount(currency, taxable)}</cbc:TaxableAmount>"
            f"<cbc:TaxAmount {_amount(currency, tax)}</cbc:TaxAmount>"
            f"<cbc:Percent>{_text(rate)}</cbc:Percent>"
            "<cac:TaxCategory><cac:TaxScheme>"
            "<cbc:Name>KDV</cbc:Name><cbc:TaxTypeCode>0015</cbc:TaxTypeCode>"
            "</cac:TaxScheme></cac:TaxCategory>"
            "</cac:TaxSubtotal>"
        )

    xslt_b64 = base64.b64encode((xslt or DEFAULT_XSLT).encode("utf-8")).decode("ascii")

    body = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f"<Invoice {_NS}>",
        "<ext:UBLExtensions><ext:UBLExtension><ext:ExtensionContent/></ext:UBLExtension></ext:UBLExtensions>",
        "<cbc:UBLVersionID>2.1</cbc:UBLVersionID>",
        "<cbc:CustomizationID>TR1.2</cbc:CustomizationID>",
        f"<cbc:ProfileID>{_text(profile)}</cbc:ProfileID>",
        f"<cbc:ID>{_text(invoice_id)}</cbc:ID>",
        "<cbc:CopyIndicator>false</cbc:CopyIndicator>",
        f"<cbc:UUID>{_text(ettn)}</cbc:UUID>",
        f"<cbc:IssueDate>{_text(issue_date)}</cbc:IssueDate>",
        f"<cbc:IssueTime>{_text(issue_time)}</cbc:IssueTime>",
        f"<cbc:InvoiceTypeCode>{_text(payload.get('invoice_type_code') or 'SATIS')}</cbc:InvoiceTypeCode>",
        f"<cbc:DocumentCurrencyCode>{_text(currency)}</cbc:DocumentCurrencyCode>",
        f"<cbc:LineCountNumeric>{len(lines)}</cbc:LineCountNumeric>",
        # Kural 3 — gönderim şekli.
        "<cac:AdditionalDocumentReference>"
        f"<cbc:ID>{_text(invoice_id)}</cbc:ID>"
        f"<cbc:IssueDate>{_text(issue_date)}</cbc:IssueDate>"
        f"<cbc:DocumentTypeCode>{SENDING_TYPE_CODE}</cbc:DocumentTypeCode>"
        f"<cbc:DocumentType>{_text(sending_type)}</cbc:DocumentType>"
        "</cac:AdditionalDocumentReference>",
        # Kural 2 — gömülü şablon.
        "<cac:AdditionalDocumentReference>"
        f"<cbc:ID>{_text(invoice_id)}</cbc:ID>"
        f"<cbc:IssueDate>{_text(issue_date)}</cbc:IssueDate>"
        f"<cbc:DocumentType>{XSLT_DOCUMENT_TYPE}</cbc:DocumentType>"
        "<cac:Attachment>"
        '<cbc:EmbeddedDocumentBinaryObject mimeCode="application/xml" encodingCode="Base64"'
        f' characterSetCode="UTF-8" filename="{_text(invoice_id)}.xslt">{xslt_b64}'
        "</cbc:EmbeddedDocumentBinaryObject>"
        "</cac:Attachment>"
        "</cac:AdditionalDocumentReference>",
        # İmza bloğu: mührü sağlayıcı atar, ama UBL-TR yapıyı yine de ister.
        "<cac:Signature>"
        f'<cbc:ID schemeID="VKN_TCKN">{_text(supplier_vkn)}</cbc:ID>'
        "<cac:SignatoryParty>"
        f'<cac:PartyIdentification><cbc:ID schemeID="VKN">{_text(supplier_vkn)}</cbc:ID>'
        "</cac:PartyIdentification>"
        "<cac:PostalAddress><cbc:CitySubdivisionName>-</cbc:CitySubdivisionName>"
        "<cbc:CityName>-</cbc:CityName>"
        "<cac:Country><cbc:Name>Türkiye</cbc:Name></cac:Country></cac:PostalAddress>"
        "</cac:SignatoryParty>"
        "<cac:DigitalSignatureAttachment><cac:ExternalReference>"
        "<cbc:URI>#Signature</cbc:URI>"
        "</cac:ExternalReference></cac:DigitalSignatureAttachment>"
        "</cac:Signature>",
        "<cac:AccountingSupplierParty>",
        _party(
            number=supplier.get("vkn"),
            name=supplier.get("name"),
            address=supplier.get("address"),
            tax_office=supplier.get("tax_office"),
        ),
        "</cac:AccountingSupplierParty>",
        "<cac:AccountingCustomerParty>",
        _party(
            number=customer.get("vkn_tckn"),
            name=customer.get("name"),
            address=customer.get("address"),
        ),
        "</cac:AccountingCustomerParty>",
        "<cac:TaxTotal>",
        f"<cbc:TaxAmount {_amount(currency, totals.get('tax_total'))}</cbc:TaxAmount>",
    ]
    for subtotal in subtotals:
        body.append(
            tax_subtotal(
                subtotal.get("taxable_amount"), subtotal.get("tax_amount"), subtotal.get("tax_rate")
            )
        )
    body.extend(
        [
            "</cac:TaxTotal>",
            "<cac:LegalMonetaryTotal>",
            f"<cbc:LineExtensionAmount {_amount(currency, totals.get('line_extension_amount'))}"
            "</cbc:LineExtensionAmount>",
            f"<cbc:TaxExclusiveAmount {_amount(currency, totals.get('tax_exclusive_amount'))}"
            "</cbc:TaxExclusiveAmount>",
            f"<cbc:TaxInclusiveAmount {_amount(currency, totals.get('tax_inclusive_amount'))}"
            "</cbc:TaxInclusiveAmount>",
            f"<cbc:PayableAmount {_amount(currency, totals.get('payable_amount'))}</cbc:PayableAmount>",
            "</cac:LegalMonetaryTotal>",
        ]
    )
    for line in lines:
        body.append(
            "<cac:InvoiceLine>"
            f"<cbc:ID>{_text(line.get('id'))}</cbc:ID>"
            f'<cbc:InvoicedQuantity unitCode={quoteattr(str(line.get("unit_code") or "C62"))}>'
            f"{_text(line.get('quantity'))}</cbc:InvoicedQuantity>"
            f"<cbc:LineExtensionAmount {_amount(currency, line.get('line_extension_amount'))}"
            "</cbc:LineExtensionAmount>"
            "<cac:TaxTotal>"
            f"<cbc:TaxAmount {_amount(currency, line.get('tax_amount'))}</cbc:TaxAmount>"
            + tax_subtotal(
                line.get("line_extension_amount"), line.get("tax_amount"), line.get("tax_rate")
            )
            + "</cac:TaxTotal>"
            f"<cac:Item><cbc:Name>{_text(line.get('name'))}</cbc:Name></cac:Item>"
            # UBL-TR ``PriceAmount`` KDV HARİÇ birim fiyat ister. Bizim
            # ``unit_price`` alanımız KDV DAHİL; dahil değeri buraya yazmak
            # satırı kendi içinde tutarsız bırakıyordu:
            # miktar × PriceAmount = 120.00 iken LineExtensionAmount = 100.00.
            # Dönüşüm ``ubl.py::_unit_price_excl_vat`` içinde, matrahtan.
            f"<cac:Price><cbc:PriceAmount "
            f"{_amount(currency, line.get('unit_price_excl_vat', line.get('unit_price')))}"
            "</cbc:PriceAmount></cac:Price>"
            "</cac:InvoiceLine>"
        )
    body.append("</Invoice>")
    return "".join(body).encode("utf-8")


def zip_single(name: str, payload: bytes) -> bytes:
    """Tek girdili, **deterministik** ZIP.

    Zaman damgası sabit: aynı fatura her seferinde bayt bayt aynı paketi
    üretsin ki idempotent bir yeniden gönderim sağlayıcıya farklı bir belge
    gibi görünmesin.
    """
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, payload)
    return buffer.getvalue()


def package_invoice(
    payload: dict[str, Any], *, xslt: str | None = None, sending_type: str = SENDING_TYPE_ELECTRONIC
) -> bytes:
    """Kural 1: gönderilecek içerik = tek dosyalık ZIP'in **ham baytları**.

    Base64'e çevirmek çağıranın işi (SOAP gövdesine gömerken), böylece aynı
    paket hem e-Arşiv hem e-Fatura yolunda yeniden kullanılabilir.
    """
    xml = build_invoice_xml(payload, xslt=xslt, sending_type=sending_type)
    return zip_single(f"{payload.get('invoice_number')}.xml", xml)
