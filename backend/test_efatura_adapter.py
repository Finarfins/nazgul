"""Gerçek e-Fatura adaptörü (İzibiz / Nes) — Increment 1.

Sağlayıcının HTTP taşıması mock'lanır (``app.einvoice.transport.HttpTransport``
yerine kayıt tutan bir çift geçilir), böylece gerçek uç noktalar bilinmeden de
durum makinesinin HER geçişi, hata sınıflandırması, retry politikası ve kimlik
sızıntısı korumaları uçtan uca doğrulanır.

Bu dosyanın asıl işi ``docs/efatura-adapter-spec.md`` §1 ve §5'in kurallarını
KANITLAMAK:

1. ``query_status()`` koşulsuz ``ACCEPTED`` dönmez — boş/bilinmeyen/hatalı
   yanıtta asla kabul üretmez (``test_query_status_never_accepts_*``).
2. ``submit()`` ``external_id``'yi uydurmaz — yanıt ETTN taşımıyorsa FAILED,
   ETTN'siz gelen ret de ``REJECTED`` değil ``FAILED``'dır.
3. Hiçbir metot sessiz sahte başarı üretmez — **NoOp dahil**. Uç
   yapılandırılmamışsa çağrı bile yapılmaz; dönüş tipi hatayı taşıyamayan
   ``fetch_pdf``/``check_taxpayer`` boş/varsayılan değer yerine hata fırlatır.
4. ``FAILED`` yalnız gönderim-anı terminalidir. Sorgu hatası ETTN'i koruyarak
   ``UNRESOLVED`` döner; kendisiyle çelişen "ETTN yok" iddiası üretmez.
5. Kimlik bilgileri ``error``/``raw``/log'a sızmaz — **oturum jetonu dahil**.
"""

from __future__ import annotations

import base64
import json
import logging
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.einvoice import (
    ACCEPTED,
    FAILED,
    NONE,
    PENDING,
    QUERYABLE,
    REJECTED,
    SENT,
    UNCONFIGURED_ENDPOINT_ERROR,
    UNRESOLVED,
    UNVERIFIED_ENDPOINT_ERROR,
    EInvoiceError,
    IzibizEInvoiceProvider,
    NesEInvoiceProvider,
    NoOpEInvoiceProvider,
    advance_status,
    build_client_ettn,
    build_einvoice_payload,
    get_einvoice_provider,
    map_provider_status,
    missing_required_fields,
    resolve_channel,
)
from app.einvoice import endpoints as wire
from app.einvoice import transport as transport_module
from app.einvoice.errors import ERROR_MESSAGES_TR, classify_http
from app.einvoice.transport import (
    CONNECT_TIMEOUT_SECONDS,
    READ_TIMEOUT_SECONDS,
    RETRY_ATTEMPTS,
    HttpResponse,
    TransportError,
)


USERNAME = "efatura-kullanici"
PASSWORD = "cok-gizli-parola-123"
BASE_URL = "https://efaturatest.izibiz.com.tr"


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------
class FakeTransport:
    """Records every request and replays a scripted response queue."""

    def __init__(self, script: list[object] | None = None, default: object | None = None) -> None:
        self.script = list(script or [])
        self.default = default if default is not None else HttpResponse(200, b"{}")
        self.calls: list[SimpleNamespace] = []

    def request(self, method: str, url: str, *, body=None, headers=None, **kwargs) -> HttpResponse:
        self.calls.append(
            SimpleNamespace(method=method, url=url, body=body, headers=headers or {}, kwargs=kwargs)
        )
        item = self.script.pop(0) if self.script else self.default
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, HttpResponse)
        return item

    def urls(self, needle: str) -> list[SimpleNamespace]:
        return [call for call in self.calls if needle in call.url]

    def bodies(self) -> str:
        return " ".join(
            (call.body or b"").decode("utf-8", errors="replace") + json.dumps(call.headers)
            for call in self.calls
        )


def settings_double(
    provider: str = "nes", *, base_url: str | None = BASE_URL, verified: bool = True
) -> SimpleNamespace:
    return SimpleNamespace(
        einvoice_provider=provider,
        einvoice_username=USERNAME,
        einvoice_password=PASSWORD,
        einvoice_base_url=base_url,
        einvoice_sender_vkn="1111111111",
        einvoice_api_key=None,
        # Uçlar hâlâ ‹doğrulanacak›; testler adaptörün DAVRANIŞINI ölçtüğü için
        # operatör onayını taklit eder. Kapının kendisi ayrıca test edilir
        # (test_configured_url_alone_does_not_unlock_unverified_wire_details).
        einvoice_endpoints_verified=verified,
    )


def json_response(payload: dict, status: int = 200) -> HttpResponse:
    return HttpResponse(status, json.dumps(payload).encode("utf-8"))


def soap_response(fields: dict, status: int = 200) -> HttpResponse:
    body = "".join(f"<{key}>{value}</{key}>" for key, value in fields.items())
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soap:Body><Response>{body}</Response></soap:Body></soap:Envelope>"
    )
    return HttpResponse(status, envelope.encode("utf-8"))


NES_LOGIN_OK = json_response({"access_token": "oturum-jetonu"})
IZIBIZ_LOGIN_OK = soap_response({"SESSION_ID": "oturum-jetonu"})
ETTN = "3f2c1a90-0000-4000-8000-000000000001"


def nes_provider(transport: FakeTransport, **kwargs) -> NesEInvoiceProvider:
    return NesEInvoiceProvider(settings_double("nes", **kwargs), transport)


def izibiz_provider(transport: FakeTransport, **kwargs) -> IzibizEInvoiceProvider:
    return IzibizEInvoiceProvider(settings_double("izibiz", **kwargs), transport)


def valid_payload(**overrides) -> dict:
    payload = build_einvoice_payload(
        {
            "invoice_number": "SUN2026000000001",
            "currency": "TRY",
            "company_id": 7,
            "invoice_id": 42,
            "issued_at": "2026-07-25 10:11:12+00:00",
            "is_efatura_user": True,
            "company": {"id": 7, "name": "Sungur Tarım A.Ş.", "tax_number": "1111111111"},
            "customer": {"name": "Alıcı Ltd.", "tax_number": "2222222222"},
            "totals": {"tax": Decimal("18.00"), "grand_total": Decimal("118.00")},
            "items": [
                {
                    "description": "Servis",
                    "qty": Decimal("1"),
                    "unit_price": Decimal("100"),
                    "tax_rate": Decimal("18"),
                    "tax_amount": Decimal("18.00"),
                    "total": Decimal("118.00"),
                }
            ],
        }
    )
    payload.update(overrides)
    return payload


def earsiv_payload(**overrides) -> dict:
    """Alıcısı mükellef OLMAYAN fatura → e-Arşiv kanalı (İzibiz'de açık olan yol)."""
    payload = build_einvoice_payload(
        {
            "invoice_number": "SUN2026000000002",
            "currency": "TRY",
            "company_id": 7,
            "invoice_id": 43,
            "issued_at": "2026-07-25 10:11:12+00:00",
            "is_efatura_user": False,
            "company": {"id": 7, "name": "Sungur Tarım A.Ş.", "tax_number": "1111111111"},
            "customer": {"name": "Deneme Müşteri", "tax_number": "22222222222"},
            "totals": {"tax": Decimal("20.00"), "grand_total": Decimal("120.00")},
            "items": [
                {
                    "description": "Servis",
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
    """Keep the 1s/2s/4s backoff honest but instant inside the suite."""
    monkeypatch.setattr(transport_module, "_sleep", lambda seconds: None)


# --------------------------------------------------------------------------
# Fabrika sözleşmesi — #131'den değişmedi
# --------------------------------------------------------------------------
def test_factory_contract_is_unchanged() -> None:
    assert isinstance(get_einvoice_provider(SimpleNamespace()), NoOpEInvoiceProvider)
    assert isinstance(get_einvoice_provider(SimpleNamespace(einvoice_provider="noop")), NoOpEInvoiceProvider)
    assert isinstance(get_einvoice_provider(SimpleNamespace(einvoice_provider="bogus")), NoOpEInvoiceProvider)
    # İsim var, kimlik yok → hâlâ NoOp (yarım yapılandırma iç akışı bozamaz).
    assert isinstance(get_einvoice_provider(SimpleNamespace(einvoice_provider="izibiz")), NoOpEInvoiceProvider)
    assert isinstance(
        get_einvoice_provider(SimpleNamespace(einvoice_provider="izibiz", einvoice_username="u")),
        NoOpEInvoiceProvider,
    )
    # İsim + kimlik → gerçek adaptör.
    assert isinstance(get_einvoice_provider(settings_double("IZIBIZ")), IzibizEInvoiceProvider)
    assert isinstance(get_einvoice_provider(settings_double("nes")), NesEInvoiceProvider)


def test_noop_stays_inert() -> None:
    provider = NoOpEInvoiceProvider()
    assert provider.submit({"any": "payload"}).status == NONE
    assert provider.query_status(ETTN).status == NONE


def test_noop_fetch_pdf_raises_instead_of_returning_empty_bytes() -> None:
    """``b""`` çağrı yerinde "boş PDF başarısı" gibi okunur — sessiz sahte başarı."""
    provider = NoOpEInvoiceProvider()
    with pytest.raises(EInvoiceError) as excinfo:
        provider.fetch_pdf(ETTN)
    assert "yapılandırılmamış" in excinfo.value.message


def test_noop_check_taxpayer_refuses_to_decide_the_channel() -> None:
    """Kesin ``False`` "cevabım yok" değil, "bu alıcı mükellef değil" cevabıdır.

    Hiç kimseye sormamış bir sağlayıcının bu cevabı kanal kararına ulaşırsa belge
    sessizce e-Arşiv'e yönlenir. Varsayılanın ``True`` olmaması gerekli ama
    yeterli değil: NoOp kanalı hiç belirlememeli.
    """
    provider = NoOpEInvoiceProvider()
    with pytest.raises(EInvoiceError):
        provider.check_taxpayer("2222222222")


def test_no_noop_method_returns_a_value_that_reads_as_an_answer() -> None:
    """Regresyon bekçisi: NoOp'un hiçbir metodu sessiz bir "cevap" üretmesin."""
    provider = NoOpEInvoiceProvider()
    # Durum bildiren metotlar NONE döner — bu doğru ve dürüst bir cevap.
    assert provider.submit({}).status == NONE
    assert provider.submit({}).external_id is None
    assert provider.query_status(ETTN).status == NONE
    # Soru soran metotlar cevap üretemez, gürültülü hata verir.
    for call in (lambda: provider.fetch_pdf(ETTN), lambda: provider.check_taxpayer("1111111111")):
        with pytest.raises(EInvoiceError):
            call()


# --------------------------------------------------------------------------
# ‹doğrulanacak› uçlar: boş uç = sahte başarı DEĞİL, gürültülü FAILED
# --------------------------------------------------------------------------
def test_no_endpoint_url_is_ever_guessed() -> None:
    """Uçlar DOĞRULANDI ama taban adres yine de boş: ortam seçimi operatörün.

    İzibiz'in tel detayları artık resmî WSDL'den okundu (bkz.
    ``test_izibiz_wire_contract.py``), ama bu "nereye bağlanılacağı" sorusunu
    cevaplamaz. Test ve canlı adresleri aynı şemayı konuşur; koda gömülü bir
    varsayılan, bir yazım hatasının üretime çıkması demektir.
    """
    assert wire.IZIBIZ_BASE_URL == ""
    assert wire.NES_BASE_URL == ""
    # Test adresi yalnız referans olarak durur ve varsayılan DEĞİLDİR.
    assert "test" in wire.IZIBIZ_TEST_BASE_URL
    assert wire.resolve_endpoint(None, wire.IZIBIZ_BASE_URL) == ""
    assert wire.izibiz_service_url("", wire.IZIBIZ_OP_LOGIN) == ""
    assert wire.is_configured("") is False
    assert wire.is_configured("efatura.example") is False
    assert wire.is_configured("http://efatura.example") is False
    assert wire.is_configured("https://user:pass@efatura.example") is False
    assert wire.is_configured("https://[bad") is False
    assert wire.is_configured("https://efatura.example:bad") is False
    assert wire.is_configured("https://efatura.example") is True


@pytest.mark.parametrize("factory", [nes_provider, izibiz_provider])
def test_unconfigured_endpoint_fails_loudly_without_any_call(factory) -> None:
    transport = FakeTransport()
    provider = factory(transport, base_url=None)  # placeholder boş → uç yok

    result = provider.submit(valid_payload())
    assert result.status == FAILED
    assert result.error == UNCONFIGURED_ENDPOINT_ERROR
    assert result.external_id is None

    status = provider.query_status(ETTN)
    assert status.status != ACCEPTED
    assert status.status == UNRESOLVED   # FAILED degil: zarf hakkinda iddia yok
    assert status.error == UNCONFIGURED_ENDPOINT_ERROR

    with pytest.raises(EInvoiceError):
        provider.fetch_pdf(ETTN)
    with pytest.raises(EInvoiceError):
        provider.check_taxpayer("2222222222")

    # Hiç ağ çağrısı yapılmadı: uydurma bir uca istek gitmez.
    assert transport.calls == []


# --------------------------------------------------------------------------
# submit — spec §1/2: ETTN uydurulmaz, otomatik retry yok
# --------------------------------------------------------------------------
def test_submit_takes_external_id_from_the_provider_response() -> None:
    transport = FakeTransport([NES_LOGIN_OK, json_response({"ettn": ETTN, "status": "PENDING"}, 201)])
    payload = valid_payload()

    result = nes_provider(transport).submit(payload)

    assert result.status == PENDING
    assert result.external_id == ETTN
    assert result.uuid == payload["uuid"]  # istemci ETTN'i (idempotency anahtarı)
    assert result.channel == "EFATURA"
    assert result.error is None


def test_submit_without_an_ettn_in_the_response_fails_instead_of_inventing_one() -> None:
    # 2xx ama ETTN yok: reddedilen Jules dalının uydurma external_id ürettiği yer.
    transport = FakeTransport([NES_LOGIN_OK, json_response({"message": "alindi"}, 200)])

    result = nes_provider(transport).submit(valid_payload())

    assert result.status == FAILED
    assert result.external_id is None
    assert "ETTN_YOK" in result.error


def test_submit_is_never_retried_on_a_network_error() -> None:
    transport = FakeTransport([NES_LOGIN_OK, TransportError("bağlantı koptu")])

    result = nes_provider(transport).submit(valid_payload())

    assert result.status == FAILED
    assert result.external_id is None
    assert result.error == ERROR_MESSAGES_TR["NETWORK"]
    # Tek gönderim denemesi: çift fatura riski nedeniyle otomatik retry YOK.
    assert len(transport.urls(wire.NES_SUBMIT_PATH)) == 1


def test_submit_rejects_an_incomplete_ubl_payload_before_touching_the_network() -> None:
    transport = FakeTransport()
    payload = valid_payload()
    payload["customer"] = {"name": "Alıcı Ltd."}  # VKN yok
    payload["profile_id"] = None  # mükellefiyet bilinmiyor

    result = nes_provider(transport).submit(payload)

    assert result.status == FAILED
    assert "alıcı VKN/TCKN" in result.error
    assert "e-Fatura profili" in result.error
    assert transport.calls == []


def test_submit_maps_an_auth_failure_to_the_fixed_turkish_message() -> None:
    transport = FakeTransport([json_response({"error": "invalid credentials"}, 401)])

    result = nes_provider(transport).submit(valid_payload())

    assert result.status == FAILED
    assert result.error == ERROR_MESSAGES_TR["AUTH"]
    assert result.external_id is None


def test_submit_reports_a_provider_rejection_with_its_reason() -> None:
    transport = FakeTransport(
        [NES_LOGIN_OK, json_response({"status": "REJECTED", "description": "Alıcı VKN hatalı", "ettn": ETTN})]
    )

    result = nes_provider(transport).submit(valid_payload())

    assert result.status == REJECTED
    assert result.error == "Alıcı VKN hatalı"


def test_a_rejection_without_an_ettn_is_a_failed_send_not_a_gib_verdict() -> None:
    """§5: ``REJECTED`` GİB'in VAR OLAN bir zarfa verdiği terminal karardır.

    ETTN çıkmadan gelen ret, GİB'e hiç ulaşmamış bir gönderimin reddidir → o
    ``FAILED``'dır. Aksi halde var olmayan bir belge hakkında terminal karar
    iddia edilir ve meşru bir yeniden gönderim kilitlenirdi.
    """
    transport = FakeTransport(
        [NES_LOGIN_OK, json_response({"status": "REJECTED", "description": "Şema hatası"})]
    )

    result = nes_provider(transport).submit(valid_payload())

    assert result.status == FAILED
    assert result.status != REJECTED
    assert result.external_id is None          # invariant: FAILED ⇒ ETTN yok
    assert "Şema hatası" in result.error
    # FAILED terminal değil: yeniden gönderim yolu açık kalır.
    assert advance_status(result.status, PENDING) == PENDING


def test_a_rejection_with_an_ettn_stays_a_terminal_rejection() -> None:
    transport = FakeTransport(
        [NES_LOGIN_OK, json_response({"status": "REJECTED", "description": "GİB reddi", "ettn": ETTN})]
    )

    result = nes_provider(transport).submit(valid_payload())

    assert result.status == REJECTED
    assert result.external_id == ETTN
    assert advance_status(result.status, PENDING) == REJECTED   # terminal


def test_configured_url_alone_does_not_unlock_unverified_wire_details() -> None:
    """Base URL vermek, tahmin edilen yol/alan adlarını meşrulaştırmaz.

    Operatör ``EINVOICE_BASE_URL`` ayarladığı anda login path'i, operasyon
    adları ve alan adları canlı sağlayıcıya karşı doğruymuş gibi kullanılırdı.
    İki kapı da açılmalı: gerçek URL **ve** doğrulanmış tel-detay onayı.
    """
    transport = FakeTransport()
    provider = nes_provider(transport, verified=False)   # URL var, onay yok

    submitted = provider.submit(valid_payload())
    assert submitted.status == FAILED
    assert submitted.error == UNVERIFIED_ENDPOINT_ERROR
    assert provider.query_status(ETTN).error == UNVERIFIED_ENDPOINT_ERROR
    with pytest.raises(EInvoiceError):
        provider.check_taxpayer("2222222222")
    assert transport.calls == []      # tahminlerle canlı çağrı yapılmadı

    # Nes hâlâ ‹doğrulanacak›; bu testin ölçtüğü kapı odur.
    assert wire.NES_ENDPOINTS_VERIFIED is False
    # İzibiz artık doğrulandı — kapı tek yönlü değil, sağlayıcı başına.
    assert wire.IZIBIZ_ENDPOINTS_VERIFIED is True
    # …ama e-Fatura GÖNDERİMİ ayrı bir kapının arkasında: bir uç doğrulanmış
    # olması, o uçtaki HER işlemin denenmiş olduğu anlamına gelmez.
    assert wire.IZIBIZ_EFATURA_SUBMIT_VERIFIED is False


def test_submit_maps_a_duplicate_to_the_duplicate_message() -> None:
    transport = FakeTransport([NES_LOGIN_OK, json_response({"errorCode": "DUPLICATE"}, 409)])

    result = nes_provider(transport).submit(valid_payload())

    assert result.status == FAILED
    assert result.error == ERROR_MESSAGES_TR["DUPLICATE"]


# --------------------------------------------------------------------------
# query_status — spec §1: ASLA koşulsuz ACCEPTED
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("PENDING", PENDING),
        ("PROCESSING", PENDING),
        ("SENT", SENT),
        ("GÖNDERİLDİ", SENT),
        ("ACCEPTED", ACCEPTED),
        ("KABUL", ACCEPTED),
        ("REJECTED", REJECTED),
    ],
)
def test_query_status_is_derived_only_from_the_provider_answer(token: str, expected: str) -> None:
    transport = FakeTransport([NES_LOGIN_OK, json_response({"status": token})])

    result = nes_provider(transport).query_status(ETTN)

    assert result.status == expected
    assert result.external_id == ETTN


@pytest.mark.parametrize(
    "response",
    [
        json_response({}),  # boş gövde
        HttpResponse(200, b""),  # gövde yok
        HttpResponse(200, b"<html>bakim</html>"),  # ayrıştırılamayan gövde
        json_response({"status": "OK"}),  # taşıma başarısı ≠ GİB kabulü
        json_response({"status": "SUCCESS"}),
        json_response({"status": "BILINMEYEN_KOD"}),
        json_response({"status": "ACCEPTED"}, 500),  # gövde kabul diyor ama HTTP 5xx
        json_response({"error": "sunucu"}, 503),
    ],
)
def test_query_status_never_accepts_an_empty_unknown_or_failed_answer(response: HttpResponse) -> None:
    """Regresyon bekçisi: hiçbir mock koşulsuz ACCEPTED üretemez."""
    transport = FakeTransport([NES_LOGIN_OK, response, response, response])

    result = nes_provider(transport).query_status(ETTN)

    assert result.status != ACCEPTED
    assert result.status == UNRESOLVED
    assert result.error


def test_a_failing_query_never_claims_the_envelope_does_not_exist() -> None:
    """§5 invariantı: ``FAILED`` = "gönderim başarısız, ETTN yok".

    Bir SORGU hatası bunu iddia edemez — çağrının kendisi o ETTN'i taşıyor.
    Sorgu-anı hataları ``UNRESOLVED`` döner: ETTN korunur, durum makinesi
    belgeyi olduğu yerde bırakır, hata yine de gürültülüdür.
    """
    failures = [
        FakeTransport([NES_LOGIN_OK, TransportError("ag"), TransportError("ag"), TransportError("ag")]),
        FakeTransport([NES_LOGIN_OK] + [json_response({}, 503)] * 3),
        FakeTransport([NES_LOGIN_OK, json_response({"error": "yetkisiz"}, 401)]),
        FakeTransport([NES_LOGIN_OK, json_response({"status": "ANLAMSIZ_KOD"})]),
        FakeTransport([json_response({"error": "login reddedildi"}, 401)]),
    ]
    for transport in failures:
        result = nes_provider(transport).query_status(ETTN)
        assert result.status == UNRESOLVED, result
        assert result.status != FAILED, result       # çelişki üretilmiyor
        assert result.status != ACCEPTED, result
        assert result.external_id == ETTN, result    # zarf kaydı kaybolmuyor
        assert result.error, result
        # Durum makinesi açısından bu bir geçiş değil: belge yerinde kalır.
        for current in (PENDING, SENT, ACCEPTED, REJECTED):
            assert advance_status(current, result.status) == current


@pytest.mark.parametrize("token", ["FAILED", "NONE"])
def test_the_provider_cannot_smuggle_a_submit_terminal_into_a_query_result(token: str) -> None:
    """Sağlayıcı düz ``{"status": "FAILED"}`` derse bu geçip gitmemeli.

    ``FAILED`` ve ``NONE`` zarfı OLMAYAN bir belgeyi tarif eder ("gönderim hiç
    inmedi, ETTN yok" / "hiç gönderilmemiş"). Oysa sorgu tam da bir ETTN hakkında
    yapılıyor: bu yanıtlar sorulan ETTN'i inkâr eder. Hata yollarında kaldırılan
    ``FAILED+external_id`` çelişkisi, sağlayıcının kelime seçimiyle geri
    gelmemeli.
    """
    transport = FakeTransport([NES_LOGIN_OK, json_response({"status": token})])

    result = nes_provider(transport).query_status(ETTN)

    assert result.status == UNRESOLVED
    assert result.status != FAILED
    assert result.status != ACCEPTED
    assert result.external_id == ETTN     # zarf kaydı korunuyor
    assert result.error
    # Durum makinesi açısından geçiş değil: saklanan belge yerinde kalır.
    for current in (PENDING, SENT, ACCEPTED, REJECTED):
        assert advance_status(current, result.status) == current


def test_query_status_only_ever_reports_a_live_envelope_state() -> None:
    """Regresyon bekçisi: sorgu sonucu ya dört canlı durumdan biri ya UNRESOLVED."""
    assert QUERYABLE == {PENDING, SENT, ACCEPTED, REJECTED}
    assert FAILED not in QUERYABLE and NONE not in QUERYABLE
    for token, expected in (("PENDING", PENDING), ("SENT", SENT), ("ACCEPTED", ACCEPTED),
                            ("REJECTED", REJECTED), ("FAILED", UNRESOLVED), ("NONE", UNRESOLVED),
                            ("BILINMEYEN", UNRESOLVED), ("", UNRESOLVED)):
        transport = FakeTransport([NES_LOGIN_OK, json_response({"status": token, "ettn": ETTN})])
        result = nes_provider(transport).query_status(ETTN)
        assert result.status == expected, (token, result)
        assert result.status in QUERYABLE or result.status == UNRESOLVED
        assert result.external_id == ETTN


def test_query_status_carries_the_rejection_reason() -> None:
    transport = FakeTransport(
        [NES_LOGIN_OK, json_response({"status": "REJECTED", "statusDescription": "GİB: şema hatası"})]
    )

    result = nes_provider(transport).query_status(ETTN)

    assert result.status == REJECTED
    assert result.error == "GİB: şema hatası"


def test_query_status_requires_an_external_id() -> None:
    transport = FakeTransport()
    result = nes_provider(transport).query_status("")
    assert result.status == FAILED
    assert transport.calls == []


def test_idempotent_calls_retry_three_times_with_backoff() -> None:
    transient = json_response({"error": "gecici"}, 503)
    transport = FakeTransport([NES_LOGIN_OK, transient, transient, transient])

    result = nes_provider(transport).query_status(ETTN)

    assert result.status == UNRESOLVED
    assert result.status != ACCEPTED
    assert len(transport.urls("/status")) == RETRY_ATTEMPTS == 3


def test_a_transient_failure_that_recovers_is_reported_from_the_final_answer() -> None:
    transport = FakeTransport(
        [NES_LOGIN_OK, json_response({}, 503), json_response({"status": "ACCEPTED"})]
    )

    result = nes_provider(transport).query_status(ETTN)

    assert result.status == ACCEPTED
    assert len(transport.urls("/status")) == 2


def test_timeout_policy_matches_the_spec() -> None:
    assert CONNECT_TIMEOUT_SECONDS == 5.0
    assert READ_TIMEOUT_SECONDS == 30.0


# --------------------------------------------------------------------------
# Durum makinesi (spec §5)
# --------------------------------------------------------------------------
def test_state_machine_moves_forward_only_and_terminal_states_are_final() -> None:
    assert advance_status(NONE, PENDING) == PENDING
    assert advance_status(PENDING, SENT) == SENT
    assert advance_status(SENT, ACCEPTED) == ACCEPTED
    assert advance_status(SENT, REJECTED) == REJECTED
    # Geri dönüş yok.
    assert advance_status(SENT, PENDING) == SENT
    assert advance_status(ACCEPTED, PENDING) == ACCEPTED
    assert advance_status(ACCEPTED, REJECTED) == ACCEPTED
    assert advance_status(REJECTED, ACCEPTED) == REJECTED
    # Gönderim hatası yalnızca hiç gönderilmemiş belgede FAILED üretir.
    assert advance_status(NONE, FAILED) == FAILED
    assert advance_status(FAILED, PENDING) == PENDING
    # Gönderilmiş bir belgeyi başarısız bir SORGU geri alamaz.
    assert advance_status(SENT, FAILED) == SENT
    assert advance_status(ACCEPTED, FAILED) == ACCEPTED


def test_status_mapping_never_invents_acceptance() -> None:
    assert map_provider_status("KABUL EDİLDİ") == ACCEPTED
    assert map_provider_status("İŞLENİYOR") == PENDING
    for unknown in (None, "", "OK", "SUCCESS", "200", "TAMAM", "?"):
        assert map_provider_status(unknown) != ACCEPTED


# --------------------------------------------------------------------------
# check_taxpayer (spec §3) — varsayılan True DEĞİL
# --------------------------------------------------------------------------
def test_check_taxpayer_reads_the_registration_from_the_provider() -> None:
    transport = FakeTransport([NES_LOGIN_OK, json_response({"isEinvoiceUser": True})])
    assert nes_provider(transport).check_taxpayer("2222222222") == {"is_efatura_user": True}

    transport = FakeTransport([NES_LOGIN_OK, json_response({"isEinvoiceUser": False})])
    assert nes_provider(transport).check_taxpayer("2222222222") == {"is_efatura_user": False}


@pytest.mark.parametrize(
    "response",
    [json_response({"error": "liste yok"}, 500), json_response({}), json_response({"isEinvoiceUser": "belki"})],
)
def test_check_taxpayer_raises_when_the_list_cannot_be_read(response: HttpResponse) -> None:
    transport = FakeTransport([NES_LOGIN_OK, response, response, response])
    provider = nes_provider(transport)

    with pytest.raises(EInvoiceError) as excinfo:
        provider.check_taxpayer("2222222222")
    # Sessizce EFATURA'ya yönlendirme yok.
    assert "is_efatura_user" not in str(excinfo.value)


def test_check_taxpayer_network_failure_raises_instead_of_defaulting_to_true() -> None:
    error = TransportError("ulaşılamıyor")
    transport = FakeTransport([NES_LOGIN_OK, error, error, error])

    with pytest.raises(EInvoiceError) as excinfo:
        nes_provider(transport).check_taxpayer("2222222222")
    assert excinfo.value.message == ERROR_MESSAGES_TR["NETWORK"]


# --------------------------------------------------------------------------
# fetch_pdf
# --------------------------------------------------------------------------
def test_fetch_pdf_accepts_a_base64_field_or_a_raw_pdf_body() -> None:
    encoded = base64.b64encode(b"%PDF-1.7 govde").decode("ascii")
    transport = FakeTransport([NES_LOGIN_OK, json_response({"pdf": encoded})])
    assert nes_provider(transport).fetch_pdf(ETTN).startswith(b"%PDF-")

    transport = FakeTransport([NES_LOGIN_OK, HttpResponse(200, b"%PDF-1.7 ham")])
    assert nes_provider(transport).fetch_pdf(ETTN) == b"%PDF-1.7 ham"


def test_fetch_pdf_raises_instead_of_returning_empty_bytes() -> None:
    transport = FakeTransport([NES_LOGIN_OK, json_response({"pdf": ""}), json_response({}), json_response({})])
    with pytest.raises(EInvoiceError):
        nes_provider(transport).fetch_pdf(ETTN)


# --------------------------------------------------------------------------
# Kimlik bilgisi sızıntısı
# --------------------------------------------------------------------------
def test_credentials_never_reach_error_raw_or_the_log(caplog: pytest.LogCaptureFixture) -> None:
    # Sağlayıcı, gönderdiğimiz kimlik bilgilerini gövdesinde geri yansıtsa bile
    # audit alanına ve kullanıcı mesajına sızmamalı.
    echo = json_response({"error": f"login failed for {USERNAME}/{PASSWORD}"}, 401)
    transport = FakeTransport([echo])

    with caplog.at_level(logging.DEBUG):
        result = nes_provider(transport).submit(valid_payload())

    serialised = json.dumps({"error": result.error, "raw": result.raw}, ensure_ascii=False)
    assert PASSWORD not in serialised
    assert USERNAME not in serialised
    assert PASSWORD not in caplog.text
    assert USERNAME not in caplog.text
    assert BASE_URL not in serialised  # uç adresi de kullanıcı yüzeyine çıkmaz
    # Kimlik bilgisi gerçekten gönderilmişti (yani test boş bir yolu ölçmüyor).
    assert PASSWORD in transport.bodies()


def test_session_token_never_reaches_raw_even_when_the_provider_echoes_it() -> None:
    """Asıl açık buydu: ``_secrets`` yalnız yapılandırma kimliklerini kapsıyordu.

    Oturum jetonu login yanıtından gelir, yani yapılandırmada yoktur. Sağlayıcı
    onu bir operasyon yanıtında yankılarsa maskesiz olarak audit ``raw``'ına
    düşerdi. Jeton, var olduğu anda hassas değer olarak kaydedilmeli.
    """
    token = "gizli-oturum-jetonu-XYZ"
    transport = FakeTransport(
        [
            json_response({"access_token": token}),
            # Sağlayıcı jetonu operasyon yanıtında geri yansıtıyor.
            json_response({"status": "PENDING", "sessionId": token, "ettn": ETTN}),
        ]
    )

    result = nes_provider(transport).query_status(ETTN)

    serialised = json.dumps({"error": result.error, "raw": result.raw}, ensure_ascii=False)
    assert token not in serialised
    assert "***" in serialised          # yankı maskelenmiş olarak duruyor
    assert result.status == PENDING     # jeton maskelendi ama yanıt yine de okundu
    # Jeton gerçekten gönderilmişti (test boş bir yolu ölçmüyor).
    assert token in transport.bodies()


def test_an_unseen_token_shape_is_masked_too() -> None:
    """Hiç elimize geçmemiş bir jeton bile şekline göre maskelenir.

    Değer tabanlı redaksiyon yalnız bildiğimiz sırları yakalar; sağlayıcı yanıt
    içinde YENİ bir jeton üretirse (bizim hiç saklamadığımız) o da audit'e
    düşmemeli.
    """
    minted = "yepyeni-bearer-jetonu-123456"
    transport = FakeTransport(
        [NES_LOGIN_OK, json_response({"status": "SENT", "access_token": minted})]
    )

    result = nes_provider(transport).query_status(ETTN)

    assert minted not in json.dumps(result.raw, ensure_ascii=False)
    assert result.status == SENT


def test_izibiz_session_id_is_masked_in_the_audit_body() -> None:
    session = "IZIBIZ-SESSION-98765"
    transport = FakeTransport(
        [
            soap_response({"SESSION_ID": session}),
            soap_response({"STATUS": "ACCEPTED", "SESSION_ID": session}),
        ]
    )

    result = izibiz_provider(transport).query_status(ETTN)

    assert session not in json.dumps(result.raw, ensure_ascii=False)
    assert result.status == ACCEPTED


def test_error_messages_do_not_leak_raw_provider_bodies() -> None:
    body = {"error": "<html>Internal Server Error at https://gizli.host/path</html>"}
    failure = json_response(body, 500)
    transport = FakeTransport([NES_LOGIN_OK, failure, failure, failure])

    result = nes_provider(transport).query_status(ETTN)

    assert result.error == ERROR_MESSAGES_TR["NETWORK"]
    assert "gizli.host" not in (result.error or "")
    # Ham gövde yalnızca audit için raw içinde durur.
    assert "gizli.host" in json.dumps(result.raw)


def test_error_classification_covers_the_spec_table() -> None:
    assert classify_http(401) == "AUTH"
    assert classify_http(403) == "AUTH"
    assert classify_http(409) == "DUPLICATE"
    assert classify_http(429) == "QUOTA"
    assert classify_http(503) == "NETWORK"
    assert classify_http(400, "zorunlu alan eksik") == "VALIDATION"
    assert classify_http(400, "kontor yetersiz") == "QUOTA"
    assert classify_http(418) == "UNKNOWN"
    assert set(ERROR_MESSAGES_TR) == {
        "AUTH",
        "VALIDATION",
        "TAXPAYER",
        "DUPLICATE",
        "QUOTA",
        "NETWORK",
        "UNKNOWN",
    }


# --------------------------------------------------------------------------
# İzibiz (SOAP) — aynı sözleşme, farklı taşıma
# --------------------------------------------------------------------------
def test_izibiz_submit_and_poll_follow_the_same_state_machine() -> None:
    # İzibiz gönderim yanıtı ETTN taşımaz; sağlayıcı belge kimliği döner.
    transport = FakeTransport(
        [IZIBIZ_LOGIN_OK, soap_response({"RETURN_CODE": "0", "INVOICE_ID": "SNG2026000000001"})]
    )
    result = izibiz_provider(transport).submit(earsiv_payload())
    assert result.status == PENDING
    assert result.external_id == "SNG2026000000001"
    assert result.uuid == earsiv_payload()["uuid"]  # istemci ETTN'i korunur
    # WSDL binding'i soapAction="" diyor: başlık operasyon adı TAŞIMAZ.
    assert transport.calls[-1].headers["SOAPAction"] == '""'
    assert "ArchiveInvoiceExtendedRequest" in (transport.calls[-1].body or b"").decode("utf-8")

    transport = FakeTransport([IZIBIZ_LOGIN_OK, soap_response({"STATUS": "ACCEPTED"})])
    assert izibiz_provider(transport).query_status(ETTN).status == ACCEPTED

    transport = FakeTransport(
        [IZIBIZ_LOGIN_OK, soap_response({"STATUS": "REDDEDİLDİ", "STATUS_DESCRIPTION": "GİB reddi"})]
    )
    rejected = izibiz_provider(transport).query_status(ETTN)
    assert rejected.status == REJECTED
    assert rejected.error == "GİB reddi"


def test_izibiz_login_without_a_session_id_fails_as_auth() -> None:
    transport = FakeTransport([soap_response({"MESSAGE": "no session"})])

    result = izibiz_provider(transport).submit(earsiv_payload())

    assert result.status == FAILED
    assert result.error == ERROR_MESSAGES_TR["AUTH"]
    assert result.external_id is None


def test_izibiz_refuses_to_submit_an_efatura_it_has_never_sent_for_real() -> None:
    """Uç doğrulanmış olması, o uçtaki her işlemin denenmiş olduğu demek değil.

    ``SendInvoice`` sandbox'ta hiç çalıştırılmadı. Okuma yollarının aksine
    yanlış bir gönderim CANLI BİR BELGE üretir ve geri alınamaz; bu yüzden
    e-Fatura kanalı kendi kapısının arkasında durur.
    """
    transport = FakeTransport()
    result = izibiz_provider(transport).submit(valid_payload())  # kanal = EFATURA

    assert result.status == FAILED
    assert result.error == wire.IZIBIZ_EFATURA_SUBMIT_ERROR
    assert result.external_id is None
    assert transport.calls == []  # login bile açılmadı


def test_izibiz_query_status_never_accepts_a_soap_fault() -> None:
    fault = HttpResponse(500, b"<soap:Fault><faultstring>hata</faultstring></soap:Fault>")
    transport = FakeTransport([IZIBIZ_LOGIN_OK, fault, fault, fault])

    result = izibiz_provider(transport).query_status(ETTN)

    assert result.status != ACCEPTED
    assert result.status == UNRESOLVED


# --------------------------------------------------------------------------
# UBL / ETTN (spec §4, §7)
# --------------------------------------------------------------------------
def test_payload_totals_stay_equal_to_the_invoice_to_the_kurus() -> None:
    payload = valid_payload()
    assert Decimal(payload["monetary_total"]["payable_amount"]) == Decimal("118.00")
    assert Decimal(payload["monetary_total"]["tax_total"]) == Decimal("18.00")
    assert Decimal(payload["monetary_total"]["tax_exclusive_amount"]) == Decimal("100.00")
    assert Decimal(payload["monetary_total"]["line_extension_amount"]) == Decimal("100.00")
    assert payload["tax_subtotals"] == [
        {"tax_rate": "18.0000", "taxable_amount": "100.00", "tax_amount": "18.00"}
    ]
    # Hiçbir tutar float değil; JSON'a string olarak serileşir.
    serialised = json.dumps(payload)
    assert "e+" not in serialised.lower()
    assert isinstance(serialised, str)


def test_ubl_header_fields_required_by_the_spec_are_present() -> None:
    payload = valid_payload()
    assert payload["ubl_version"] == "UBL-TR 1.2"
    assert payload["profile_id"] == "TICARIFATURA"
    assert payload["invoice_type_code"] == "SATIS"
    assert payload["issue_date"] == "2026-07-25"
    assert payload["issue_time"] == "10:11:12"
    assert payload["document_currency_code"] == "TRY"
    assert payload["lines"][0]["unit_code"] == "C62"
    assert payload["lines"][0]["line_extension_amount"] == "100.00"
    assert missing_required_fields(payload) == []


def test_client_ettn_is_stable_and_never_invented() -> None:
    first = build_client_ettn(7, 42)
    assert first == build_client_ettn(7, 42)  # aynı belge → aynı ETTN (idempotency)
    assert first != build_client_ettn(8, 42)  # şirket farklıysa farklı
    assert first != build_client_ettn(7, 43)
    assert build_client_ettn(None, 42) is None  # kimlik yoksa uydurma yok
    assert build_client_ettn(7, None) is None


def test_channel_is_never_defaulted_to_efatura() -> None:
    assert resolve_channel(True) == ("EFATURA", "TICARIFATURA")
    assert resolve_channel(False) == ("EARSIV", "EARSIVFATURA")
    # Mükellefiyet bilinmiyorsa kanal da profil de boş kalır (sessiz EFATURA yok).
    assert resolve_channel(None) == (None, None)
    unknown = build_einvoice_payload({"invoice_number": "A-1", "company": {}, "customer": {}, "totals": {}})
    assert unknown["channel"] is None and unknown["profile_id"] is None
    assert "e-Fatura profili (alıcı mükellefiyeti belirlenmedi)" in missing_required_fields(unknown)
