"""İzibiz tel sözleşmesi — GERÇEK sandbox yanıtlarıyla, ağa çıkmadan.

``backend/tests/fixtures/izibiz/`` altındaki gövdeler uydurma değildir:
``efaturatest.izibiz.com.tr`` üzerinden yapılan gerçek çağrılardan
``backend/sandbox/izibiz_smoke.py --capture`` ile kaydedildi (oturum jetonu, VKN
ve görüntüleme anahtarları diske yazılmadan önce maskelendi). Bu dosya onları
sahte bir taşımadan geri oynatır: **hiçbir test ağ açmaz**, ama ölçtüğü şey
gerçek sağlayıcı davranışıdır.

Kanıtlanan sözleşme maddeleri:

1. **İş hatası HTTP 200 gövdesinde gelir.** İzibiz belge reddini SOAP Fault ya
   da 5xx ile bildirmez; ``<ERROR_TYPE>`` ile bildirir ve HTTP durumu 200'dür.
   ``response.ok``'a güvenen bir adaptör reddedilmiş bir belge için
   "gönderildi" der. ``test_mutation_*`` bunun testlerle gerçekten
   yakalandığını, ayrıştırmayı devre dışı bırakarak kanıtlar.
2. Gönderim yanıtı ETTN taşımaz — ``INVOICE_ID`` döner.
3. Mükellefiyet boolean değil, ``USER`` etiket sayısıdır.
4. İstek zarfı: ``schemas.i2i.com`` ad alanı, ``<Ad>Request`` kök elemanı,
   BOŞ ``SOAPAction``, gövdede ``REQUEST_HEADER/SESSION_ID``.
"""

from __future__ import annotations

import base64
import io
import zipfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.einvoice import (
    FAILED,
    PENDING,
    SENT,
    UNRESOLVED,
    EInvoiceError,
    IzibizEInvoiceProvider,
    build_einvoice_payload,
)
from app.einvoice import endpoints as wire
from app.einvoice import transport as transport_module
from app.einvoice.transport import HttpResponse
from app.einvoice.ubl_xml import SENDING_TYPE_CODE, XSLT_DOCUMENT_TYPE, build_invoice_xml, package_invoice


FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "izibiz"
BASE_URL = "https://efaturatest.izibiz.com.tr"
USERNAME = "sandbox-kullanici"
PASSWORD = "sandbox-parola"


def fixture(name: str) -> HttpResponse:
    """Kaydedilmiş gerçek yanıt. HTTP kodu dosya adında (hepsi 200 — asıl mesele bu)."""
    path = FIXTURES / name
    body = path.read_bytes()
    status = int(path.name.rsplit(".", 2)[-2])
    return HttpResponse(status, body)


class FakeTransport:
    """Kayıt tutan, senaryo oynatan taşıma. Soket açmaz."""

    def __init__(self, script: list[object] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[SimpleNamespace] = []

    def request(self, method: str, url: str, *, body=None, headers=None, **kwargs) -> HttpResponse:
        self.calls.append(SimpleNamespace(method=method, url=url, body=body, headers=headers or {}))
        item = self.script.pop(0) if self.script else HttpResponse(200, b"<Response/>")
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, HttpResponse)
        return item

    def last_body(self) -> str:
        return (self.calls[-1].body or b"").decode("utf-8")

    def bodies(self) -> list[str]:
        return [(call.body or b"").decode("utf-8", errors="replace") for call in self.calls]


def settings_double() -> SimpleNamespace:
    return SimpleNamespace(
        einvoice_provider="izibiz",
        einvoice_username=USERNAME,
        einvoice_password=PASSWORD,
        einvoice_base_url=BASE_URL,
        einvoice_sender_vkn="1111111111",
        einvoice_api_key=None,
        einvoice_endpoints_verified=False,  # kod içi bayrak zaten True
    )


def provider(transport: FakeTransport) -> IzibizEInvoiceProvider:
    return IzibizEInvoiceProvider(settings_double(), transport)


def earsiv_payload(**overrides) -> dict:
    payload = build_einvoice_payload(
        {
            "invoice_number": "SNG2026000000001",
            "currency": "TRY",
            "company_id": 7,
            "invoice_id": 42,
            "issued_at": "2026-07-29 14:04:04+03:00",
            "is_efatura_user": False,  # → EARSIV kanalı
            "company": {"id": 7, "name": "Sungur Tarım A.Ş.", "tax_number": "1111111111"},
            "customer": {"name": "Deneme Müşteri", "tax_number": "11111111111"},
            "totals": {"tax": Decimal("20.00"), "grand_total": Decimal("120.00")},
            "items": [
                {
                    "description": "Sandbox Test Kalemi",
                    "qty": Decimal("1"),
                    "unit_price": Decimal("100"),
                    "tax_rate": Decimal("20"),
                    "tax_amount": Decimal("20.00"),
                    "total": Decimal("120.00"),
                }
            ],
        }
    )
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport_module, "_sleep", lambda seconds: None)


@pytest.fixture()
def login_ok() -> HttpResponse:
    return fixture("Login.200.xml")


# ==========================================================================
# 1) HTTP 200 + ERROR_TYPE — asıl mesele
# ==========================================================================
def test_the_recorded_rejection_really_is_an_http_200() -> None:
    """Testin ölçtüğü şeyin var olduğunu kanıtla: ret 200 ile geliyor.

    Bu kaydırılırsa (ör. sağlayıcı ileride 500'e geçerse) aşağıdaki testler
    yanlış şeyi ölçmeye başlar; o yüzden önce varsayım sabitleniyor.
    """
    rejection = fixture("WriteToArchiveExtended-fault.200.xml")
    assert rejection.status_code == 200
    assert rejection.ok is True  # ← taşıma "başarılı" diyor
    body = rejection.body.decode("utf-8")
    assert "<ERROR_CODE>10007</ERROR_CODE>" in body
    assert "Zip bir dosya içermelidir." in body
    assert "Fault" not in body  # SOAP Fault DEĞİL


def test_submit_treats_a_200_error_type_as_a_failure(login_ok: HttpResponse) -> None:
    transport = FakeTransport([login_ok, fixture("WriteToArchiveExtended-fault.200.xml")])

    result = provider(transport).submit(earsiv_payload())

    assert result.status == FAILED
    assert result.external_id is None
    # Sınıflandırma HTTP kodundan değil, gövdedeki ERROR_CODE'dan geliyor:
    # classify_http(200) bunu asla VALIDATION diye adlandıramazdı.
    assert result.error == "Fatura e-Fatura biçimine uymuyor: Zip bir dosya içermelidir.."
    assert result.raw["error_class"] == "VALIDATION"


def test_query_status_treats_a_200_error_type_as_unresolved(login_ok: HttpResponse) -> None:
    """Sorguda iş hatası ``UNRESOLVED``'dır — zarf sağlayıcıda, bilgi bizde eksik."""
    transport = FakeTransport([login_ok] + [fixture("WriteToArchiveExtended-fault.200.xml")] * 3)

    result = provider(transport).query_status("SNG2026000000001", channel="EARSIV", uuid="X" * 36)

    assert result.status == UNRESOLVED
    assert result.status != FAILED  # ETTN'i inkâr etmiyor
    assert result.external_id == "SNG2026000000001"
    assert result.error


def test_check_taxpayer_raises_on_a_200_error_type(login_ok: HttpResponse) -> None:
    transport = FakeTransport([login_ok] + [fixture("WriteToArchiveExtended-fault.200.xml")] * 3)

    with pytest.raises(EInvoiceError):
        provider(transport).check_taxpayer("2222222222")


def test_a_nonzero_return_code_is_a_failure_even_with_a_document_id(login_ok: HttpResponse) -> None:
    """``RETURN_CODE`` sıfırdan farklıysa belge kimliği gelmiş olsa bile hata.

    Gövde, kaydedilmiş BAŞARILI yanıttan türetildi: tek fark ``RETURN_CODE``.
    """
    ok_body = fixture("WriteToArchiveExtended.200.xml").body.decode("utf-8")
    degraded = ok_body.replace("<RETURN_CODE>0</RETURN_CODE>", "<RETURN_CODE>9</RETURN_CODE>")
    transport = FakeTransport([login_ok, HttpResponse(200, degraded.encode("utf-8"))])

    result = provider(transport).submit(earsiv_payload())

    assert result.status == FAILED
    assert result.external_id is None


# ==========================================================================
# 2) MUTASYON KANITI — ayrıştırma kaldırılınca ne olur?
# ==========================================================================
def test_mutation_removing_the_error_parse_turns_a_rejection_into_a_fake_success(
    monkeypatch: pytest.MonkeyPatch, login_ok: HttpResponse
) -> None:
    """Kanca sökülürse reddedilmiş bir belge "gönderildi" olur.

    Bu, yukarıdaki testlerin yük taşıdığının kanıtı: ``_business_failure``
    ayrıştırması kaldırıldığında adaptör ``RETURN_CODE=9`` diyen bir yanıtı
    ``PENDING`` + gerçek bir ``external_id`` ile döndürür — tam olarak
    engellenmek istenen sessiz sahte başarı.
    """
    ok_body = fixture("WriteToArchiveExtended.200.xml").body.decode("utf-8")
    degraded = ok_body.replace("<RETURN_CODE>0</RETURN_CODE>", "<RETURN_CODE>9</RETURN_CODE>")
    response = HttpResponse(200, degraded.encode("utf-8"))

    # Önce sağlam hâl: hata.
    intact = provider(FakeTransport([login_ok, response])).submit(earsiv_payload())
    assert intact.status == FAILED

    # Şimdi kancayı sök (ayrıştırmayı "kaldır").
    monkeypatch.setattr(IzibizEInvoiceProvider, "_business_failure", lambda self, response: None)
    mutated = provider(FakeTransport([login_ok, response])).submit(earsiv_payload())

    assert mutated.status == PENDING  # ← sahte başarı
    assert mutated.external_id == "SNG2026210141633"
    assert intact.status != mutated.status


def test_mutation_removing_the_error_parse_loses_the_real_reason(
    monkeypatch: pytest.MonkeyPatch, login_ok: HttpResponse
) -> None:
    """Gerçek ret gövdesinde mutasyon, sınıfı ve gerekçeyi kaybettirir.

    Sonuç yine ``FAILED`` kalır (ETTN yok) ama artık ``VALIDATION`` demez ve
    "Zip bir dosya içermelidir." gerekçesini taşımaz — operatör neyi
    düzelteceğini bilemez.
    """
    rejection = fixture("WriteToArchiveExtended-fault.200.xml")

    intact = provider(FakeTransport([login_ok, rejection])).submit(earsiv_payload())
    assert intact.raw["error_class"] == "VALIDATION"
    assert "Zip bir dosya içermelidir." in (intact.error or "")

    monkeypatch.setattr(IzibizEInvoiceProvider, "_business_failure", lambda self, response: None)
    mutated = provider(FakeTransport([login_ok, rejection])).submit(earsiv_payload())

    assert mutated.raw["error_class"] != "VALIDATION"
    assert "Zip bir dosya içermelidir." not in (mutated.error or "")


def test_mutation_trusting_response_ok_would_accept_the_rejection() -> None:
    """``response.ok`` tek başına neden yetmez — doğrudan gösterim."""
    rejection = fixture("WriteToArchiveExtended-fault.200.xml")
    assert rejection.ok is True
    # Ama iş düzeyinde başarısız:
    failure = provider(FakeTransport())._business_failure(rejection)
    assert failure is not None
    assert failure[0] == "VALIDATION"


# ==========================================================================
# 3) Başarılı yollar — kaydedilmiş gerçek gövdelerle
# ==========================================================================
def test_login_reads_the_session_id_from_the_real_response(login_ok: HttpResponse) -> None:
    transport = FakeTransport([login_ok, fixture("WriteToArchiveExtended.200.xml")])

    result = provider(transport).submit(earsiv_payload())

    assert result.status == PENDING
    login_body = transport.bodies()[0]
    assert "<izb:LoginRequest" in login_body
    assert f'xmlns:izb="{wire.IZIBIZ_SOAP_NAMESPACE}"' in login_body
    assert "<SESSION_ID>-1</SESSION_ID>" in login_body  # login anında yer tutucu
    assert transport.calls[0].url.endswith(wire.IZIBIZ_AUTH_PATH)


def test_submit_external_id_is_the_provider_document_id_not_the_ettn(
    login_ok: HttpResponse,
) -> None:
    """Sözleşme #2: yanıt ETTN taşımaz, ``INVOICE_ID`` taşır."""
    ok = fixture("WriteToArchiveExtended.200.xml")
    assert b"UUID" not in ok.body  # gerçekten yok
    payload = earsiv_payload()
    transport = FakeTransport([login_ok, ok])

    result = provider(transport).submit(payload)

    assert result.external_id == "SNG2026210141633"
    assert result.uuid == payload["uuid"]  # istemci ETTN'i ayrı alanda korunur
    assert result.external_id != result.uuid


def test_earchive_status_105_maps_to_pending(login_ok: HttpResponse) -> None:
    """``STATUS=105`` çıplak bir sayı; yalnız İzibiz sözlüğünde anlamlı."""
    transport = FakeTransport([login_ok, fixture("GetEArchiveInvoiceStatus.200.xml")])

    result = provider(transport).query_status(
        "SNG2026210141633", channel="EARSIV", uuid="034EE590-0D2F-4291-9B71-4AA1060FFA7E"
    )

    assert result.status == PENDING
    assert transport.calls[-1].url.endswith(wire.IZIBIZ_EARCHIVE_PATH)
    assert "<izb:GetEArchiveInvoiceStatusRequest" in transport.last_body()
    assert f'xmlns:izb="{wire.IZIBIZ_SOAP_NAMESPACE_ARCHIVE}"' in transport.last_body()


def test_efatura_status_text_maps_through_the_izibiz_vocabulary(login_ok: HttpResponse) -> None:
    """``"RECEIVE - SUCCEED"`` → SENT. Boşluk/tire normalleştirmesi çalışıyor."""
    transport = FakeTransport([login_ok, fixture("GetInvoiceStatus.200.xml")])

    result = provider(transport).query_status("TST2026000000001", channel="EFATURA")

    assert result.status == SENT
    assert transport.calls[-1].url.endswith(wire.IZIBIZ_EINVOICE_PATH)
    # Anahtar ÖZNİTELİK olarak taşınır, alt eleman olarak değil.
    assert '<INVOICE ID="TST2026000000001"/>' in transport.last_body()


def test_earchive_status_without_an_ettn_refuses_instead_of_querying_wrongly(
    login_ok: HttpResponse,
) -> None:
    """e-Arşiv durum sorgusu yalnız UUID kabul eder; belge kimliği yollamak
    boş bir yanıt üretir ve bu "belge yok" gibi okunurdu.

    Ret **login'den önce** verilir: eskiden önce oturum açılıp sonra sorgunun
    kurulamadığı fark ediliyordu, yani her başarısız sorgu sağlayıcıda boşuna
    bir oturum açıyordu. Artık hiçbir ağ çağrısı yapılmıyor.
    """
    transport = FakeTransport([login_ok])

    result = provider(transport).query_status("SNG2026210141633", channel="EARSIV")

    assert result.status == UNRESOLVED
    assert result.external_id == "SNG2026210141633"
    assert wire.IZIBIZ_EARCHIVE_STATUS_NEEDS_ETTN in (result.error or "")
    # Hiçbir çağrı yok — login bile açılmadı.
    assert transport.calls == []


def test_check_taxpayer_counts_tags_because_there_is_no_boolean_field(
    login_ok: HttpResponse,
) -> None:
    """Sözleşme #3: kayıtlı alıcı ≥1 ``USER`` döner, kayıtsız hiç döndürmez."""
    registered = fixture("CheckUser.200.xml")
    assert b"IS_EFATURA_USER" not in registered.body  # boolean alan gerçekten yok

    transport = FakeTransport([login_ok, registered])
    assert provider(transport).check_taxpayer("1111111111") == {"is_efatura_user": True}

    empty = HttpResponse(
        200,
        b'<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>'
        b'<ns3:CheckUserResponse xmlns:ns3="http://schemas.i2i.com/ei/wsdl"/>'
        b"</S:Body></S:Envelope>",
    )
    transport = FakeTransport([login_ok, empty])
    assert provider(transport).check_taxpayer("1111111111") == {"is_efatura_user": False}


@pytest.mark.parametrize(
    "body",
    [
        b"",  # boş gövde
        b"<html>bakim</html>",  # yanıt bile değil
        b'<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body/></S:Envelope>',
    ],
)
def test_check_taxpayer_zero_tags_is_only_an_answer_when_the_response_is_real(
    body: bytes, login_ok: HttpResponse
) -> None:
    """"0 etiket" cevaptır, ama yalnız gerçekten ``CheckUserResponse`` geldiyse.

    Boş/bozuk bir gövde de "0 etiket" gösterir; onu ``False`` saymak, hiç kimseye
    sormadan alıcıyı e-Arşiv'e yönlendirmek olurdu.
    """
    transport = FakeTransport([login_ok] + [HttpResponse(200, body)] * 3)

    with pytest.raises(EInvoiceError):
        provider(transport).check_taxpayer("1111111111")


def test_logout_style_return_code_zero_is_not_a_failure() -> None:
    """``RETURN_CODE=0`` başarı; fail-closed yorum yanlış pozitif üretmiyor."""
    assert provider(FakeTransport())._business_failure(fixture("Logout.200.xml")) is None
    assert provider(FakeTransport())._business_failure(fixture("CheckUser.200.xml")) is None
    assert (
        provider(FakeTransport())._business_failure(fixture("GetEArchiveInvoiceStatus.200.xml"))
        is None
    )


# ==========================================================================
# 4) İstek zarfı ve servis yönlendirmesi
# ==========================================================================
def test_the_envelope_matches_the_wsdl_not_the_old_guesses(login_ok: HttpResponse) -> None:
    transport = FakeTransport([login_ok, fixture("WriteToArchiveExtended.200.xml")])

    provider(transport).submit(earsiv_payload())
    body = transport.last_body()

    # Eski tahminlerin hiçbiri kalmadı.
    assert "tempuri.org" not in body
    assert transport.calls[-1].headers["SOAPAction"] == '""'
    # Kök eleman operasyon adı DEĞİL, ``<Ad>Request``.
    assert "<izb:ArchiveInvoiceExtendedRequest" in body
    assert "<WriteToArchiveExtended" not in body
    # Oturum gövdede, başlıkta değil.
    assert "<REQUEST_HEADER><SESSION_ID>" in body
    assert "SESSION_ID" not in str(transport.calls[-1].headers)
    # Alt elemanlar NİTELİKSİZ: varsayılan ad alanı verilmiyor.
    assert 'xmlns="' not in body


def test_each_operation_goes_to_its_own_service() -> None:
    assert wire.izibiz_service_url(BASE_URL, wire.IZIBIZ_OP_LOGIN).endswith("/AuthenticationWS")
    assert wire.izibiz_service_url(BASE_URL, wire.IZIBIZ_OP_TAXPAYER).endswith("/AuthenticationWS")
    assert wire.izibiz_service_url(BASE_URL, wire.IZIBIZ_OP_SUBMIT).endswith("/EInvoiceWS")
    assert wire.izibiz_service_url(BASE_URL, wire.IZIBIZ_OP_STATUS).endswith("/EInvoiceWS")
    assert wire.izibiz_service_url(BASE_URL, wire.IZIBIZ_OP_SUBMIT_EARCHIVE).endswith(
        "/EIArchiveWS/EFaturaArchive"
    )
    # Taban boşsa hiçbir yere gidilmez.
    assert wire.izibiz_service_url("", wire.IZIBIZ_OP_SUBMIT) == ""


def test_the_session_survives_eight_hours_minus_a_safety_margin(login_ok: HttpResponse) -> None:
    """TTL 8 saat; adaptör son dakikaları güvenlik payı olarak kesiyor."""
    ttl = provider(FakeTransport())._token_ttl()
    assert ttl == wire.IZIBIZ_TOKEN_TTL_SECONDS - wire.IZIBIZ_TOKEN_REFRESH_MARGIN_SECONDS
    assert 0 < ttl < wire.IZIBIZ_TOKEN_TTL_SECONDS

    # Aynı adaptörde ikinci çağrı yeniden login açmaz.
    transport = FakeTransport(
        [login_ok, fixture("WriteToArchiveExtended.200.xml"), fixture("GetEArchiveInvoiceStatus.200.xml")]
    )
    adapter = provider(transport)
    adapter.submit(earsiv_payload())
    adapter.query_status("SNG2026210141633", channel="EARSIV", uuid="034EE590-0D2F-4291-9B71-4AA1060FFA7E")
    login_calls = [body for body in transport.bodies() if "LoginRequest" in body]
    assert len(login_calls) == 1


# ==========================================================================
# 5) UBL — üç ampirik kural
# ==========================================================================
def test_rule_1_the_content_is_a_single_file_zip() -> None:
    """``ERROR_CODE=10007 "Zip bir dosya içermelidir."``"""
    package = package_invoice(earsiv_payload())

    assert package[:2] == b"PK"  # ZIP sihirli baytları
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = archive.namelist()
        assert names == ["SNG2026000000001.xml"]
        assert archive.read(names[0]).startswith(b'<?xml')


def test_rule_1_the_package_is_byte_identical_across_builds() -> None:
    """Aynı fatura → aynı paket. Aksi hâlde idempotent yeniden gönderim,
    sağlayıcıya farklı bir belge gibi görünürdü."""
    assert package_invoice(earsiv_payload()) == package_invoice(earsiv_payload())


def test_rule_2_the_document_carries_its_own_xslt() -> None:
    """``ERROR_CODE=10013 "Belge içerisinde şablon bulunamamıştır."``"""
    xml = build_invoice_xml(earsiv_payload()).decode("utf-8")

    assert f"<cbc:DocumentType>{XSLT_DOCUMENT_TYPE}</cbc:DocumentType>" in xml
    assert "<cbc:EmbeddedDocumentBinaryObject" in xml
    # Gömülü içerik gerçekten bir XSLT (base64 çözülünce).
    start = xml.index('filename="SNG2026000000001.xslt">') + len('filename="SNG2026000000001.xslt">')
    encoded = xml[start : xml.index("</cbc:EmbeddedDocumentBinaryObject>", start)]
    assert b"xsl:stylesheet" in base64.b64decode(encoded)


def test_rule_3_the_sending_type_is_declared() -> None:
    """``ERROR_CODE=10003 "eArşiv belge gönderim şekli boş olamaz"``"""
    xml = build_invoice_xml(earsiv_payload()).decode("utf-8")

    assert f"<cbc:DocumentTypeCode>{SENDING_TYPE_CODE}</cbc:DocumentTypeCode>" in xml
    assert "<cbc:DocumentType>ELEKTRONIK</cbc:DocumentType>" in xml


def test_the_ubl_totals_still_equal_the_invoice_to_the_kurus() -> None:
    """XML'e çevirmek tutar sözleşmesini bozmamalı."""
    xml = build_invoice_xml(earsiv_payload()).decode("utf-8")

    assert '<cbc:PayableAmount currencyID="TRY">120.00</cbc:PayableAmount>' in xml
    assert '<cbc:TaxExclusiveAmount currencyID="TRY">100.00</cbc:TaxExclusiveAmount>' in xml
    assert "<cbc:TaxAmount currencyID=\"TRY\">20.00</cbc:TaxAmount>" in xml
    assert "e+" not in xml.lower()  # hiçbir tutar float değil


def test_an_eleven_digit_identifier_becomes_a_person_not_a_company() -> None:
    """TCKN'de unvan değil ad/soyad; VKN'de unvan. Uzunluktan, tahminle değil."""
    xml = build_invoice_xml(earsiv_payload()).decode("utf-8")

    assert '<cbc:ID schemeID="TCKN">11111111111</cbc:ID>' in xml
    assert "<cac:Person>" in xml
    assert '<cbc:ID schemeID="VKN">1111111111</cbc:ID>' in xml  # satıcı


def test_the_ubl_builder_refuses_to_emit_a_half_document() -> None:
    from app.einvoice import UblBuildError

    with pytest.raises(UblBuildError):
        build_invoice_xml(earsiv_payload(uuid=None))
    with pytest.raises(UblBuildError):
        build_invoice_xml(earsiv_payload(lines=[]))
    with pytest.raises(UblBuildError):
        # 9 haneli kimlik ne VKN ne TCKN — sessizce VKN varsayılmaz.
        build_invoice_xml(earsiv_payload(customer={"vkn_tckn": "123456789", "name": "X"}))
