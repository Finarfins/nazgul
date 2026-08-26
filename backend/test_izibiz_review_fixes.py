"""PR #192 inceleme turu — dört düzeltmenin sözleşmesi.

ChatGPT mantık incelemesinin bulduğu üç KIRMIZI + bir kısmi madde. Her bölüm
düzeltmenin **davranışını** sabitler ve mutasyon testiyle düzeltmenin gerçekten
yük taşıdığını gösterir (düzeltmeyi geri al → test kırmızı).

Hiçbir test ağ açmaz.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.einvoice import (
    FAILED,
    PENDING,
    UNRESOLVED,
    EInvoiceError,
    IzibizEInvoiceProvider,
    build_einvoice_payload,
    build_invoice_xml,
)
from app.einvoice import endpoints as wire
from app.einvoice import transport as transport_module
from app.einvoice.provider import _HttpEInvoiceProvider
from app.einvoice.transport import HttpResponse
from app.money import money


BASE_URL = "https://efaturatest.izibiz.com.tr"

LOGIN_OK = (
    b'<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>'
    b'<ns3:LoginResponse xmlns:ns3="http://schemas.i2i.com/ei/wsdl">'
    b"<SESSION_ID>oturum-{n}</SESSION_ID></ns3:LoginResponse></S:Body></S:Envelope>"
)
AUTH_FAULT = (
    b'<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>'
    b'<GetEArchiveInvoiceStatusResponse xmlns="http://schemas.i2i.com/ei/wsdl/archive">'
    b'<ERROR_TYPE xmlns=""><ERROR_CODE>9999</ERROR_CODE>'
    b"<ERROR_SHORT_DES>Oturum bulunamadi</ERROR_SHORT_DES></ERROR_TYPE>"
    b"</GetEArchiveInvoiceStatusResponse></S:Body></S:Envelope>"
)
STATUS_OK = (
    b'<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>'
    b'<GetEArchiveInvoiceStatusResponse xmlns="http://schemas.i2i.com/ei/wsdl/archive">'
    b'<INVOICE xmlns=""><HEADER><STATUS>105</STATUS></HEADER></INVOICE>'
    b'<REQUEST_RETURN xmlns=""><RETURN_CODE>0</RETURN_CODE></REQUEST_RETURN>'
    b"</GetEArchiveInvoiceStatusResponse></S:Body></S:Envelope>"
)
ETTN = "034EE590-0D2F-4291-9B71-4AA1060FFA7E"


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def _explode(*args, **kwargs):
        raise AssertionError("Bu test soket açmaya çalıştı")

    monkeypatch.setattr(socket, "socket", _explode)
    monkeypatch.setattr(socket, "create_connection", _explode)


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport_module, "_sleep", lambda seconds: None)


class ScriptTransport:
    """Sırayla yanıt veren, çağrıları kaydeden taşıma."""

    def __init__(self, script: list[bytes] | None = None, default: bytes = b"<Response/>") -> None:
        self.script = list(script or [])
        self.default = default
        self.calls: list[SimpleNamespace] = []

    def request(self, method: str, url: str, *, body=None, headers=None, **kwargs) -> HttpResponse:
        text = (body or b"").decode("utf-8", errors="replace")
        self.calls.append(SimpleNamespace(url=url, body=text))
        payload = self.script.pop(0) if self.script else self.default
        return HttpResponse(200, payload)

    def logins(self) -> int:
        return sum(1 for call in self.calls if "LoginRequest" in call.body)


def make(transport, env: str = "test") -> IzibizEInvoiceProvider:
    settings = SimpleNamespace(
        einvoice_provider="izibiz",
        einvoice_username="kullanici",
        einvoice_password="parola",
        einvoice_base_url=BASE_URL,
        einvoice_sender_vkn="1111111111",
        einvoice_api_key=None,
        einvoice_endpoints_verified=False,
        izibiz_env=env,
    )
    return IzibizEInvoiceProvider(settings, transport)


def earsiv_payload(items=None, totals=None, **overrides) -> dict:
    value = build_einvoice_payload(
        {
            "invoice_number": "SNG2026000000001",
            "currency": "TRY",
            "company_id": 7,
            "invoice_id": 42,
            "issued_at": "2026-07-29 14:04:04+03:00",
            "is_efatura_user": False,
            "company": {"id": 7, "name": "Sungur Tarım A.Ş.", "tax_number": "1111111111"},
            "customer": {"name": "Deneme Müşteri", "tax_number": "11111111111"},
            "totals": totals or {"tax": Decimal("20.00"), "grand_total": Decimal("120.00")},
            "items": items
            or [
                {
                    "description": "Kalem",
                    "qty": Decimal("1"),
                    "unit_price": Decimal("120"),  # KDV DAHİL
                    "tax_rate": Decimal("20"),
                    "tax_amount": Decimal("20.00"),
                    "total": Decimal("120.00"),
                }
            ],
        }
    )
    value.update(overrides)
    return value


# ==========================================================================
# 1) _business_failure() fail-closed
# ==========================================================================
UNREADABLE_BODIES = [
    pytest.param(b"", id="bos-govde"),
    pytest.param(b"   \n  ", id="yalnizca-bosluk"),
    pytest.param(b"<Envelope><Body><Login", id="bozuk-xml"),
    pytest.param(b"<html><body>502 Bad Gateway</body></html>", id="proxy-hata-sayfasi"),
    pytest.param(
        b'<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body/></S:Envelope>',
        id="bos-soap-govdesi",
    ),
    pytest.param(b"<Something><Else>veri</Else></Something>", id="beklenmeyen-kok"),
]


@pytest.mark.parametrize("body", UNREADABLE_BODIES)
def test_an_unreadable_200_body_is_a_failure_not_a_success(body: bytes) -> None:
    """"Okuyamadım" cevabı "sorun yok" cevabı değildir.

    ``_business_failure()`` seviyesinde ölçülüyor — ``check_taxpayer()``
    üzerinden değil — ki başka bir katmanın tesadüfen yakalaması sonucu
    gizlemesin.
    """
    failure = make(ScriptTransport())._business_failure(HttpResponse(200, body))

    assert failure is not None, "okunamayan gövde başarı sayıldı"
    error_class, message = failure
    # UNKNOWN, NETWORK DEĞİL: NETWORK idempotent yollarda yeniden denemeyi
    # tetikler, oysa okunamayan gövde tekrar sorulmakla okunur hâle gelmez.
    assert error_class == "UNKNOWN"
    assert error_class != "NETWORK"
    assert message.strip()


def test_error_type_without_an_error_code_is_still_a_failure() -> None:
    """``ERROR_TYPE`` var ama kodu yok — yine de hata."""
    body = (
        b'<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>'
        b'<ArchiveInvoiceExtendedResponse xmlns="http://schemas.i2i.com/ei/wsdl/archive">'
        b'<ERROR_TYPE xmlns=""><INTL_TXN_ID>1</INTL_TXN_ID></ERROR_TYPE>'
        b"</ArchiveInvoiceExtendedResponse></S:Body></S:Envelope>"
    )
    failure = make(ScriptTransport())._business_failure(HttpResponse(200, body))

    assert failure is not None
    assert failure[0] == "UNKNOWN"


def test_a_zero_return_code_does_not_override_a_present_error_type() -> None:
    """Çelişki: ``RETURN_CODE=0`` ama ``ERROR_TYPE`` dolu → hata kazanır."""
    body = (
        b'<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>'
        b'<ArchiveInvoiceExtendedResponse xmlns="http://schemas.i2i.com/ei/wsdl/archive">'
        b'<REQUEST_RETURN xmlns=""><RETURN_CODE>0</RETURN_CODE></REQUEST_RETURN>'
        b'<ERROR_TYPE xmlns=""><ERROR_CODE>10007</ERROR_CODE>'
        b"<ERROR_SHORT_DES>Zip bir dosya icermelidir.</ERROR_SHORT_DES></ERROR_TYPE>"
        b"</ArchiveInvoiceExtendedResponse></S:Body></S:Envelope>"
    )
    failure = make(ScriptTransport())._business_failure(HttpResponse(200, body))

    assert failure is not None
    assert failure[0] == "VALIDATION"
    assert "Zip bir dosya icermelidir." in failure[1]


def test_a_real_success_body_is_still_not_a_failure() -> None:
    """Fail-closed sıkılaştırması yanlış pozitif üretmiyor."""
    assert make(ScriptTransport())._business_failure(HttpResponse(200, STATUS_OK)) is None


def test_mutation_the_unreadable_body_guard_is_what_makes_submit_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Düzeltmeyi geri al → boş gövde "gönderildi" olur mu?

    Kanca sökülünce boş gövdeli bir 200 yanıtı ``_business_failure`` tarafından
    ``None`` (sorun yok) sayılır. ``submit`` o noktadan sonra belge kimliği
    aramaya devam eder; bulamayınca yine ``FAILED`` döner ama gerekçe artık
    "yanıt okunamadı" değil "ETTN yok" olur — operatör yanlış yeri arar.
    """
    empty = ScriptTransport([LOGIN_OK.replace(b"{n}", b"1"), b""])
    intact = make(empty).submit(earsiv_payload())
    assert intact.status == FAILED
    assert "BOS_YANIT" in (intact.error or "")

    monkeypatch.setattr(IzibizEInvoiceProvider, "_business_failure", lambda self, response: None)
    mutated = make(ScriptTransport([LOGIN_OK.replace(b"{n}", b"1"), b""])).submit(earsiv_payload())

    assert "BOS_YANIT" not in (mutated.error or "")
    assert intact.error != mutated.error


# ==========================================================================
# 2) UBL — PriceAmount KDV hariç, satır kendi içinde mutabık
# ==========================================================================
def _lines_of(payload: dict) -> list[dict]:
    return payload["lines"]


def test_price_amount_times_quantity_equals_line_extension_amount() -> None:
    """Sözleşme: her satırda ``PriceAmount × Quantity == LineExtensionAmount``.

    Eskiden ``PriceAmount`` KDV DAHİL yazılıyordu: 1 × 120.00 = 120.00 iken
    ``LineExtensionAmount`` 100.00'dı — satır kendi içinde mutabık değildi.
    """
    payload = earsiv_payload()
    for line in _lines_of(payload):
        price = Decimal(line["unit_price_excl_vat"])
        qty = Decimal(line["quantity"])
        assert money(price * qty) == Decimal(line["line_extension_amount"])
        # Ve dahil fiyat ayrı alanda korunuyor (kaybolmadı).
        assert Decimal(line["unit_price"]) == Decimal("120.00")
        assert price == Decimal("100.000000")


def test_the_taxable_base_comes_from_the_formula_not_the_snapshot() -> None:
    """Matrah ``line_total / (1 + oran/100)``; anlık görüntüdeki ``tax_amount``
    yanlış bile olsa belgeyi bozamaz."""
    payload = earsiv_payload(
        items=[
            {
                "description": "Kalem",
                "qty": Decimal("1"),
                "unit_price": Decimal("120"),
                "tax_rate": Decimal("20"),
                # Kasıtlı olarak YANLIŞ — formül bunu kullanmamalı.
                "tax_amount": Decimal("99.00"),
                "total": Decimal("120.00"),
            }
        ]
    )
    line = _lines_of(payload)[0]

    assert Decimal(line["line_extension_amount"]) == Decimal("100.00")
    assert Decimal(line["tax_amount"]) == Decimal("20.00")


def test_sum_of_line_extension_equals_tax_exclusive_with_rounding_residue() -> None:
    """``Σ LineExtensionAmount == TaxExclusiveAmount`` — ARTIK BIRAKAN veriyle.

    Tam bölünen veri (360 / 1.20 = 300.00) bu testi anlamsız kılardı: yuvarlama
    artığı hiç doğmazdı, yani test ölçmek istediği şey bozuk olsa da geçerdi.
    Üç farklı oran, üçü de sonsuz ondalık üretiyor::

        10.00 / 1.20 = 8.3333… → 8.33
        10.00 / 1.10 = 9.0909… → 9.09
        10.00 / 1.01 = 9.9009… → 9.90
                                 ─────
                                 27.32
    """
    payload = earsiv_payload(
        items=[
            {
                "description": "KDV 20",
                "qty": Decimal("1"),
                "unit_price": Decimal("10.00"),
                "tax_rate": Decimal("20"),
                "tax_amount": Decimal("1.67"),
                "total": Decimal("10.00"),
            },
            {
                "description": "KDV 10",
                "qty": Decimal("1"),
                "unit_price": Decimal("10.00"),
                "tax_rate": Decimal("10"),
                "tax_amount": Decimal("0.91"),
                "total": Decimal("10.00"),
            },
            {
                "description": "KDV 1",
                "qty": Decimal("1"),
                "unit_price": Decimal("10.00"),
                "tax_rate": Decimal("1"),
                "tax_amount": Decimal("0.10"),
                "total": Decimal("10.00"),
            },
        ],
        totals={"tax": Decimal("2.68"), "grand_total": Decimal("30.00")},
    )
    bases = [Decimal(line["line_extension_amount"]) for line in _lines_of(payload)]

    assert bases == [Decimal("8.33"), Decimal("9.09"), Decimal("9.90")]
    assert sum(bases) == Decimal("27.32")
    assert Decimal(payload["monetary_total"]["tax_exclusive_amount"]) == Decimal("27.32")
    assert Decimal(payload["monetary_total"]["line_extension_amount"]) == Decimal("27.32")
    # Her satır kendi içinde de mutabık.
    for line in _lines_of(payload):
        assert money(
            Decimal(line["unit_price_excl_vat"]) * Decimal(line["quantity"])
        ) == Decimal(line["line_extension_amount"])


def test_an_indivisible_quantity_reconciles_to_the_kurus() -> None:
    """qty 7, dahil 100.00, %18 → matrah 84.75; PriceAmount × 7 kuruşa 84.75.

    100.00 / 1.18 = 84.745762… → 84.75
    84.75 / 7     = 12.107142857… → 12.107143 (6 hane)
    12.107143 × 7 = 84.750001 → kuruşa 84.75
    """
    payload = earsiv_payload(
        items=[
            {
                "description": "Bolunmez miktar",
                "qty": Decimal("7"),
                "unit_price": Decimal("14.29"),
                "tax_rate": Decimal("18"),
                "tax_amount": Decimal("15.25"),
                "total": Decimal("100.00"),
            }
        ],
        totals={"tax": Decimal("15.25"), "grand_total": Decimal("100.00")},
    )
    line = _lines_of(payload)[0]

    assert Decimal(line["line_extension_amount"]) == Decimal("84.75")
    assert Decimal(line["unit_price_excl_vat"]) == Decimal("12.107143")
    assert money(Decimal(line["unit_price_excl_vat"]) * Decimal("7")) == Decimal("84.75")
    assert Decimal(payload["monetary_total"]["tax_exclusive_amount"]) == Decimal("84.75")


def test_an_indivisible_unit_price_still_reconciles_to_the_kurus() -> None:
    """100.00 / 3 bölünmüyor; 6 haneli ``PriceAmount`` yuvarlamayı kurtarıyor.

    2 haneyle 33.33 × 3 = 99.99 ≠ 100.00 olurdu.
    """
    payload = earsiv_payload(
        items=[
            {
                "description": "Bolunmez",
                "qty": Decimal("3"),
                "unit_price": Decimal("40"),
                "tax_rate": Decimal("20"),
                "tax_amount": Decimal("20.00"),
                "total": Decimal("120.00"),
            }
        ]
    )
    line = _lines_of(payload)[0]

    assert Decimal(line["line_extension_amount"]) == Decimal("100.00")
    assert Decimal(line["unit_price_excl_vat"]) == Decimal("33.333333")
    assert money(Decimal(line["unit_price_excl_vat"]) * Decimal(line["quantity"])) == Decimal("100.00")


def test_a_zero_rated_line_keeps_its_full_amount_as_the_base() -> None:
    payload = earsiv_payload(
        items=[
            {
                "description": "KDV yok",
                "qty": Decimal("2"),
                "unit_price": Decimal("50"),
                "tax_rate": Decimal("0"),
                "tax_amount": Decimal("0.00"),
                "total": Decimal("100.00"),
            }
        ],
        totals={"tax": Decimal("0.00"), "grand_total": Decimal("100.00")},
    )
    line = _lines_of(payload)[0]

    assert Decimal(line["line_extension_amount"]) == Decimal("100.00")
    assert Decimal(line["tax_amount"]) == Decimal("0.00")
    assert Decimal(line["unit_price_excl_vat"]) == Decimal("50.000000")


def test_the_xml_carries_the_vat_exclusive_price() -> None:
    """Düzeltme sözlükte kalmıyor, XML'e çıkıyor."""
    xml = build_invoice_xml(earsiv_payload()).decode("utf-8")

    assert '<cbc:PriceAmount currencyID="TRY">100.000000</cbc:PriceAmount>' in xml
    assert '<cbc:PriceAmount currencyID="TRY">120.00</cbc:PriceAmount>' not in xml
    assert '<cbc:LineExtensionAmount currencyID="TRY">100.00</cbc:LineExtensionAmount>' in xml


def _assert_amounts_are_decimal(payload: dict) -> None:
    """Payload'daki HER tutarın tipini doğrula.

    Testin kendi yerel kopyasına değil, üretim kodunun ÜRETTİĞİ değerlere
    bakar. Bu yüzden ayrı bir yardımcı: aynı iddia hem sağlam hem mutasyona
    uğramış üretim koduna uygulanabilsin.
    """
    monetary = payload["monetary_total"]
    for name, raw in monetary.items():
        assert isinstance(raw, str), f"{name} dize değil: {type(raw).__name__}"
        assert not isinstance(raw, float), f"{name} float"
        value = Decimal(raw)
        assert value == value.quantize(Decimal("0.01")), f"{name} kuruş çözünürlüğünde değil"

    for line in payload["lines"]:
        for name in (
            "unit_price",
            "unit_price_excl_vat",
            "tax_amount",
            "line_extension_amount",
            "line_total",
        ):
            raw = line[name]
            assert isinstance(raw, str), f"{name} dize değil: {type(raw).__name__}"
            assert not isinstance(raw, float), f"{name} float"
            Decimal(raw)
        # Bölme sonucu olan tek alan bile tam Decimal: kuyruğunda ikili
        # yaklaşıklık artığı yok.
        excl = Decimal(line["unit_price_excl_vat"])
        assert excl == excl.quantize(Decimal("0.000001"))

    # UBL ``TaxSubtotal`` blokları ayrı bir üretim yolundan (``tax_subtotals``
    # sözlüğü → ``_amount``) çıkar; satır ve ``monetary_total`` yolları temiz
    # olsa bile bu blok float sızdırabilir. Her oran kovasının iki tutarını da
    # denetime al.
    for subtotal in payload["tax_subtotals"]:
        for name in ("taxable_amount", "tax_amount"):
            raw = subtotal[name]
            assert isinstance(raw, str), f"tax_subtotals.{name} dize değil: {type(raw).__name__}"
            assert not isinstance(raw, float), f"tax_subtotals.{name} float"
            value = Decimal(raw)
            assert value == value.quantize(Decimal("0.01")), (
                f"tax_subtotals.{name} kuruş çözünürlüğünde değil"
            )


def test_every_amount_in_the_chain_is_a_decimal_not_a_float() -> None:
    """Metin araması yetersizdi: normal biçimlenen bir float da geçerdi.

    Şimdi ÜRETİM KODUNUN ürettiği her tutarın tipi doğrulanıyor. Testin gerçekten
    üretimi ölçtüğünün kanıtı bir alt testte: üretimdeki tutar biçimleyici float
    döndürecek şekilde değiştirildiğinde bu iddia düşüyor.
    """
    payload = earsiv_payload()

    _assert_amounts_are_decimal(payload)

    xml = build_invoice_xml(payload).decode("utf-8")
    assert "e+" not in xml.lower()
    assert "e-0" not in xml.lower()


def test_mutation_a_float_returning_amount_helper_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kontrol değişkeni: üretimdeki ``_amount`` float döndürsün.

    ``ubl._amount`` payload'daki tutarları biçimleyen tek yer. Float döndürmeye
    zorlandığında payload artık dize taşımaz ve
    :func:`_assert_amounts_are_decimal` düşer. Eski test bunu yakalayamazdı:
    yalnız kendi yerel ``float(...)`` kopyasına bakıyordu, üretim bozulsa da
    geçerdi.
    """
    from app.einvoice import ubl as ubl_module

    # Önce sağlam hâl gerçekten yeşil.
    _assert_amounts_are_decimal(earsiv_payload())

    monkeypatch.setattr(ubl_module, "_amount", lambda value: float(money(value)))
    mutated = earsiv_payload()

    # Mutasyon gerçekten işledi mi — yoksa test boş bir yolu ölçer.
    assert isinstance(mutated["monetary_total"]["payable_amount"], float)

    with pytest.raises(AssertionError) as excinfo:
        _assert_amounts_are_decimal(mutated)
    assert "float" in str(excinfo.value) or "dize değil" in str(excinfo.value)


def test_mutation_writing_the_vat_inclusive_price_breaks_the_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Düzeltmeyi geri al → satır yine kendi içinde tutarsız olur."""
    payload = earsiv_payload()
    line = dict(payload["lines"][0])

    # Sağlam: hariç fiyat × miktar = matrah.
    assert money(Decimal(line["unit_price_excl_vat"]) * Decimal(line["quantity"])) == Decimal(
        line["line_extension_amount"]
    )

    # Mutasyon: eski davranış — ``PriceAmount`` alanına DAHİL fiyat.
    broken = money(Decimal(line["unit_price"]) * Decimal(line["quantity"]))
    assert broken != Decimal(line["line_extension_amount"])
    assert broken == Decimal("120.00")


# ==========================================================================
# 3) Oturum: yarış + tek seferlik yeniden giriş
# ==========================================================================
class CountingLoginTransport(ScriptTransport):
    """Login'i yavaşlatan taşıma — yarış penceresini gerçekten açar."""

    def __init__(self, delay: float = 0.05) -> None:
        super().__init__()
        self.delay = delay
        self.login_count = 0
        self._barrier = threading.Event()

    def request(self, method: str, url: str, *, body=None, headers=None, **kwargs) -> HttpResponse:
        text = (body or b"").decode("utf-8", errors="replace")
        self.calls.append(SimpleNamespace(url=url, body=text))
        if "LoginRequest" in text:
            self.login_count += 1
            # Pencereyi aç: kilit yoksa ikinci iş parçacığı buraya girer.
            self._barrier.wait(self.delay)
            return HttpResponse(200, LOGIN_OK.replace(b"{n}", str(self.login_count).encode()))
        return HttpResponse(200, STATUS_OK)


def test_two_concurrent_calls_open_exactly_one_login() -> None:
    """Kilit yoksa iki iş parçacığı iki oturum açar."""
    transport = CountingLoginTransport()
    adapter = make(transport)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            adapter.query_status("SNG1", channel="EARSIV", uuid=ETTN)
        except BaseException as exc:  # pragma: no cover - hata varsa test söyler
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert transport.login_count == 1, f"{transport.login_count} login açıldı, 1 olmalıydı"


def test_mutation_without_the_lock_two_logins_are_possible() -> None:
    """Kilidi sök → yarış gerçekten iki login üretiyor mu?

    Kilit yerine hiçbir şey yapmayan bir bağlam yöneticisi konuyor; kalan her
    şey aynı. İki login çıkıyorsa kilit yük taşıyor demektir.
    """

    class _NoLock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    transport = CountingLoginTransport()
    adapter = make(transport)
    adapter._lock_for = lambda key: _NoLock()  # type: ignore[assignment]

    def worker() -> None:
        adapter.query_status("SNG1", channel="EARSIV", uuid=ETTN)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert transport.login_count == 2, (
        "kilit sökülmesine rağmen tek login açıldı — yarış penceresi yakalanamadı"
    )


def test_an_auth_failure_triggers_exactly_one_relogin_and_then_stops() -> None:
    """İlk AUTH → yeniden giriş + tek tekrar. İkinci AUTH → dur."""
    transport = ScriptTransport(
        [
            LOGIN_OK.replace(b"{n}", b"1"),  # ilk giriş
            AUTH_FAULT,  # sorgu: oturum düştü
            LOGIN_OK.replace(b"{n}", b"2"),  # yeniden giriş
            AUTH_FAULT,  # tekrar: yine AUTH → DUR
        ]
    )

    result = make(transport).query_status("SNG1", channel="EARSIV", uuid=ETTN)

    assert result.status == UNRESOLVED
    assert transport.logins() == 2, "tam olarak iki giriş bekleniyordu"
    # Sonsuz döngü yok: üçüncü bir giriş denenmedi.
    assert len(transport.calls) == 4


def test_a_recovered_session_makes_the_second_attempt_succeed() -> None:
    """Yeniden giriş işe yararsa sorgu cevabını verir."""
    transport = ScriptTransport(
        [
            LOGIN_OK.replace(b"{n}", b"1"),
            AUTH_FAULT,
            LOGIN_OK.replace(b"{n}", b"2"),
            STATUS_OK,
        ]
    )

    result = make(transport).query_status("SNG1", channel="EARSIV", uuid=ETTN)

    assert result.status == PENDING  # 105
    assert transport.logins() == 2
    # İkinci sorgu TAZE jetonla gitti.
    assert "oturum-2" in transport.calls[-1].body


def test_submit_clears_the_session_on_auth_but_never_resends() -> None:
    """Gönderim tekrarlanmaz — çift belge riski (spec §7).

    Oturum önbelleği yine de temizlenir, böylece bir sonraki çağrı taze
    jetonla başlar.
    """
    transport = ScriptTransport(
        [LOGIN_OK.replace(b"{n}", b"1"), AUTH_FAULT, LOGIN_OK.replace(b"{n}", b"2")]
    )
    adapter = make(transport)

    result = adapter.submit(earsiv_payload())

    assert result.status == FAILED
    assert result.external_id is None
    # Bir gönderim isteği yapıldı, İKİNCİSİ YOK.
    submits = [c for c in transport.calls if "ArchiveInvoiceExtendedRequest" in c.body]
    assert len(submits) == 1
    # Ama önbellek temizlendi: yeniden giriş açıldı.
    assert transport.logins() == 2
    assert adapter._sessions[adapter._session_key()][0] == "oturum-2"


def test_the_session_ttl_keeps_a_margin_and_the_deadline_stays_an_integer() -> None:
    """Pay bırakılıyor VE saklanan son kullanma zamanı tamsayı.

    Tip iddiası ölçülüyor: ``1e9`` gibi bir float çarpan girseydi
    ``expires_at`` float olurdu ve ns büyüklüğünde 2^53 üstünde hassasiyet
    kaybederdi.
    """
    transport = ScriptTransport([LOGIN_OK.replace(b"{n}", b"1"), STATUS_OK])
    adapter = make(transport)

    ttl = adapter._token_ttl()
    assert ttl == wire.IZIBIZ_TOKEN_TTL_SECONDS - wire.IZIBIZ_TOKEN_REFRESH_MARGIN_SECONDS
    assert wire.IZIBIZ_TOKEN_REFRESH_MARGIN_SECONDS > 0
    assert isinstance(ttl, int) and not isinstance(ttl, bool)

    adapter.query_status("SNG1", channel="EARSIV", uuid=ETTN)
    _token, expires_at = adapter._sessions[adapter._session_key()]

    assert isinstance(expires_at, int), f"expires_at {type(expires_at).__name__}"
    assert not isinstance(expires_at, bool)
    assert expires_at > 0
    # Tamsayı nanosaniye: gelecekteki bir an, hassasiyet kaybı olmadan.
    assert expires_at - ttl * 1_000_000_000 <= time.monotonic_ns()


# ==========================================================================
# 4) e-Arşiv durum sorgusu: UUID yoksa ağ yok
# ==========================================================================
def test_an_earchive_status_without_an_ettn_never_opens_a_session() -> None:
    transport = ScriptTransport([LOGIN_OK.replace(b"{n}", b"1")])

    result = make(transport).query_status("SNG2026000000001", channel="EARSIV")

    assert result.status == UNRESOLVED
    assert result.external_id == "SNG2026000000001"
    assert wire.IZIBIZ_EARCHIVE_STATUS_NEEDS_ETTN in (result.error or "")
    assert transport.calls == [], "login açıldı — ön koşul ağdan sonra bakılmış"


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_every_empty_ettn_form_is_caught_before_the_network(empty) -> None:
    transport = ScriptTransport([LOGIN_OK.replace(b"{n}", b"1")])

    result = make(transport).query_status("SNG1", channel="EARSIV", uuid=empty)

    assert result.status == UNRESOLVED
    assert transport.calls == []


def test_an_efatura_status_without_an_ettn_still_works_by_document_id() -> None:
    """Ön koşul yalnız e-Arşiv'e özgü; e-Fatura kimlikle sorgulanabiliyor."""
    transport = ScriptTransport([LOGIN_OK.replace(b"{n}", b"1"), STATUS_OK])

    result = make(transport).query_status("TST2026000000001", channel="EFATURA")

    assert result.status == PENDING
    assert '<INVOICE ID="TST2026000000001"/>' in transport.calls[-1].body


def test_mutation_without_the_precondition_a_session_would_be_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ön koşulu geri al → boşuna login açılıyor mu?"""
    intact = ScriptTransport([LOGIN_OK.replace(b"{n}", b"1")])
    make(intact).query_status("SNG1", channel="EARSIV")
    assert intact.calls == []

    # Ön koşul İzibiz alt sınıfında; taban sınıfı yamalamak etkisiz kalır.
    assert (
        IzibizEInvoiceProvider._query_precondition
        is not _HttpEInvoiceProvider._query_precondition
    ), "ön koşul artık alt sınıfta değil — mutasyon yanlış yeri hedefliyor"
    monkeypatch.setattr(
        IzibizEInvoiceProvider,
        "_query_precondition",
        lambda self, external_id, *, channel, uuid: None,
    )
    mutated = ScriptTransport([LOGIN_OK.replace(b"{n}", b"1")])
    result = make(mutated).query_status("SNG1", channel="EARSIV")

    assert mutated.logins() == 1, "ön koşul sökülmesine rağmen login açılmadı"
    assert result.status == UNRESOLVED  # sonuç yine güvenli, ama oturum israfı var


# ==========================================================================
# Dokunulmaması gerekenler
# ==========================================================================
def test_the_untouchable_flags_are_untouched() -> None:
    assert wire.IZIBIZ_EFATURA_SUBMIT_VERIFIED is False
    assert wire.IZIBIZ_LIVE_HOST_DENYLIST  # canlı modda da aktif
    assert wire.izibiz_endpoint_violation("https://efaturaws.izibiz.com.tr", "live") is not None


def test_no_credential_reaches_an_error_or_the_audit_body() -> None:
    """Yeni yollar da sır sızdırmıyor."""
    transport = ScriptTransport([LOGIN_OK.replace(b"{n}", b"1"), AUTH_FAULT, LOGIN_OK, AUTH_FAULT])

    result = make(transport).query_status("SNG1", channel="EARSIV", uuid=ETTN)

    serialised = f"{result.error}{result.raw}"
    assert "parola" not in serialised
    assert "kullanici" not in serialised
    assert "oturum-1" not in serialised


# ==========================================================================
# 5) Oturum önbelleği kimlik başına anahtarlanır (çok kiracılı ERP)
# ==========================================================================
def _tenant_settings(username: str, base_url: str = BASE_URL) -> SimpleNamespace:
    return SimpleNamespace(
        einvoice_provider="izibiz",
        einvoice_username=username,
        einvoice_password="parola",
        einvoice_base_url=base_url,
        einvoice_sender_vkn="1111111111",
        einvoice_api_key=None,
        einvoice_endpoints_verified=False,
        izibiz_env="test",
    )


def test_the_session_key_uses_a_keyed_username_fingerprint() -> None:
    """Anahtar ``(kiracı, süreç-içi HMAC, taban URL)``; ham kimlik bilgisi yok.

    Anahtar sözlükte durur ve bir hata ayıklama dökümüne düşebilir; oraya bir
    sırrın kendisi de türevi de girmemeli.
    """
    settings = _tenant_settings("kiraci-a")
    adapter = IzibizEInvoiceProvider(settings, ScriptTransport(), company_id=7)

    key = adapter._session_key()

    assert key[0] == 7
    assert key[2] == BASE_URL
    assert len(key) == 3
    assert key == adapter._session_key()
    assert key != IzibizEInvoiceProvider(
        _tenant_settings("kiraci-b"), ScriptTransport(), company_id=7
    )._session_key()
    serialised = repr(key)
    assert settings.einvoice_username not in serialised
    assert settings.einvoice_password not in serialised
    # Parolanın yaygın türevleri de anahtarda değil.
    import hashlib

    secret = settings.einvoice_password.encode("utf-8")
    for digest in (hashlib.md5(secret), hashlib.sha1(secret), hashlib.sha256(secret)):
        assert digest.hexdigest() not in serialised
        assert digest.hexdigest()[:16] not in serialised
    username = settings.einvoice_username.encode("utf-8")
    for digest in (hashlib.md5(username), hashlib.sha1(username), hashlib.sha256(username)):
        assert digest.hexdigest() not in serialised
        assert digest.hexdigest()[:16] not in serialised


def test_username_fingerprint_key_is_random_per_process() -> None:
    script = """
from app.einvoice.provider import _session_username_key
first = _session_username_key('kiraci-a')
second = _session_username_key('kiraci-a')
assert first == second
print(first)
"""
    outputs = [
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for _ in range(2)
    ]
    assert outputs[0]
    assert outputs[0] != outputs[1]


def test_the_same_username_on_two_tenants_does_not_share_a_session() -> None:
    """Aynı kullanıcı adı + aynı URL, farklı kiracı → iki ayrı login.

    Kimlikler ``.env``'den çıkıp kiracı başına DB'ye taşındığında iki bayi
    yanlışlıkla aynı kullanıcı adıyla yapılandırılabilir. Kiracı anahtarda
    olmasaydı biri diğerinin oturumuyla belge işlerdi.
    """
    transport = ScriptTransport(
        [
            LOGIN_OK.replace(b"{n}", b"bayi1"),
            STATUS_OK,
            LOGIN_OK.replace(b"{n}", b"bayi2"),
            STATUS_OK,
        ]
    )
    settings = _tenant_settings("ayni-kullanici")
    first = IzibizEInvoiceProvider(settings, transport, company_id=1)
    second = IzibizEInvoiceProvider(settings, transport, company_id=2)

    first.query_status("SNG-1", channel="EARSIV", uuid=ETTN)
    second.query_status("SNG-2", channel="EARSIV", uuid=ETTN)

    assert transport.logins() == 2
    assert first._session_key() != second._session_key()
    assert first._session_key()[1:] == second._session_key()[1:]  # tek fark kiracı

    queries = [c for c in transport.calls if "GetEArchiveInvoiceStatusRequest" in c.body]
    assert "oturum-bayi1" in queries[0].body
    assert "oturum-bayi2" in queries[1].body


def test_one_adapter_serving_two_tenants_keeps_the_sessions_apart() -> None:
    """Tek adaptör örneği iki kiracıya hizmet ederse de oturumlar ayrı."""
    transport = ScriptTransport(
        [LOGIN_OK.replace(b"{n}", b"t1"), STATUS_OK, LOGIN_OK.replace(b"{n}", b"t2"), STATUS_OK]
    )
    adapter = IzibizEInvoiceProvider(_tenant_settings("ortak-kullanici"), transport, company_id=1)
    username_key = adapter._session_key()[1]

    adapter.query_status("SNG-1", channel="EARSIV", uuid=ETTN)
    adapter._company_id = 2
    adapter.query_status("SNG-2", channel="EARSIV", uuid=ETTN)

    assert transport.logins() == 2
    assert adapter._sessions[(1, username_key, BASE_URL)][0] == "oturum-t1"
    assert adapter._sessions[(2, username_key, BASE_URL)][0] == "oturum-t2"


def test_the_factory_threads_the_tenant_through() -> None:
    """Fabrika kiracıyı adaptöre geçiriyor; çağrı yeri vermezse ``None``."""
    from app.einvoice import get_einvoice_provider

    settings = _tenant_settings("kiraci-a")
    settings.einvoice_provider = "izibiz"

    assert get_einvoice_provider(settings, company_id=42)._session_key()[0] == 42
    assert get_einvoice_provider(settings)._session_key()[0] is None


def test_two_tenants_get_two_sessions_that_never_cross() -> None:
    """İki farklı kullanıcı adı → iki ayrı login, oturumlar karışmıyor.

    Tek global slotta ikinci kiracı birincinin jetonunu kullanırdı: A firmasının
    faturası B'nin oturumuyla gönderilirdi.
    """
    transport = ScriptTransport(
        [
            LOGIN_OK.replace(b"{n}", b"a"),
            STATUS_OK,
            LOGIN_OK.replace(b"{n}", b"b"),
            STATUS_OK,
            STATUS_OK,  # A tekrar sorguluyor: yeniden login OLMAMALI
        ]
    )
    settings = _tenant_settings("kiraci-a")
    adapter = IzibizEInvoiceProvider(settings, transport)

    key_a = adapter._session_key()
    adapter.query_status("SNG-A", channel="EARSIV", uuid=ETTN)
    settings.einvoice_username = "kiraci-b"
    key_b = adapter._session_key()
    adapter.query_status("SNG-B", channel="EARSIV", uuid=ETTN)
    settings.einvoice_username = "kiraci-a"
    adapter.query_status("SNG-A2", channel="EARSIV", uuid=ETTN)

    assert transport.logins() == 2, "kiracı başına bir login bekleniyordu"
    assert adapter._sessions[key_a][0] == "oturum-a"
    assert adapter._sessions[key_b][0] == "oturum-b"

    queries = [c for c in transport.calls if "GetEArchiveInvoiceStatusRequest" in c.body]
    assert len(queries) == 3
    assert "oturum-a" in queries[0].body
    assert "oturum-b" in queries[1].body
    # Üçüncü çağrı A'nın ÖNBELLEKTEKİ jetonunu kullandı; B'ninkini değil.
    assert "oturum-a" in queries[2].body
    assert "oturum-b" not in queries[2].body


def test_the_same_username_on_two_environments_does_not_share_a_session() -> None:
    """Aynı kullanıcı adı, farklı taban URL → ayrı oturum.

    Test ortamının jetonunu canlıda kullanmak (ya da tersi) kimlik doğrulama
    hatasından beter: yanlış ortama belge göndermek.
    """
    transport = ScriptTransport(
        [LOGIN_OK.replace(b"{n}", b"test"), STATUS_OK, LOGIN_OK.replace(b"{n}", b"diger"), STATUS_OK]
    )
    settings = _tenant_settings("ayni-kullanici")
    adapter = IzibizEInvoiceProvider(settings, transport)

    test_key = adapter._session_key()
    adapter.query_status("SNG1", channel="EARSIV", uuid=ETTN)
    settings.einvoice_base_url = f"{BASE_URL}/diger"
    other_key = adapter._session_key()
    adapter.query_status("SNG2", channel="EARSIV", uuid=ETTN)

    assert transport.logins() == 2
    assert set(adapter._sessions) == {test_key, other_key}


def test_one_username_two_concurrent_calls_still_open_a_single_login() -> None:
    """Anahtarlama, 4. maddedeki tek-login garantisini bozmadı."""
    transport = CountingLoginTransport()
    adapter = IzibizEInvoiceProvider(_tenant_settings("tek-kiraci"), transport)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            adapter.query_status("SNG1", channel="EARSIV", uuid=ETTN)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        # ``join(timeout)`` sessizce döner; kilitlenmede iş parçacığı hâlâ canlı
        # olurdu ve login_count == 1 iddiası yanlış yeşil verirdi. ``daemon=True``
        # ise asılı bir iş parçacığının süiti bloke etmesini önler.
        assert not thread.is_alive(), "iş parçacığı zaman aşımına uğradı — olası kilitlenme"

    assert errors == []
    assert transport.login_count == 1


def test_two_tenants_do_not_block_each_other() -> None:
    """Kilit anahtar başına: farklı kiracılar ayrı kilitler alır."""
    adapter = IzibizEInvoiceProvider(_tenant_settings("kiraci-a"), ScriptTransport())

    lock_a = adapter._lock_for((None, "kiraci-a", BASE_URL))
    lock_b = adapter._lock_for((None, "kiraci-b", BASE_URL))

    assert lock_a is not lock_b
    # Aynı anahtar her zaman aynı kilit — yoksa yarış kapanmazdı.
    assert adapter._lock_for((None, "kiraci-a", BASE_URL)) is lock_a
    # A kilitliyken B hâlâ alınabilir.
    with lock_a:
        assert lock_b.acquire(blocking=False)
        lock_b.release()


def test_an_auth_failure_only_clears_the_affected_tenant() -> None:
    """Bir kiracının oturumu düşerse diğerininki silinmemeli."""
    transport = ScriptTransport(
        [
            LOGIN_OK.replace(b"{n}", b"a"),
            STATUS_OK,
            LOGIN_OK.replace(b"{n}", b"b"),
            AUTH_FAULT,
            LOGIN_OK.replace(b"{n}", b"b2"),
            STATUS_OK,
        ]
    )
    settings = _tenant_settings("kiraci-a")
    adapter = IzibizEInvoiceProvider(settings, transport)

    key_a = adapter._session_key()
    adapter.query_status("SNG-A", channel="EARSIV", uuid=ETTN)
    settings.einvoice_username = "kiraci-b"
    key_b = adapter._session_key()
    adapter.query_status("SNG-B", channel="EARSIV", uuid=ETTN)

    # B yenilendi, A dokunulmadan duruyor.
    assert adapter._sessions[key_b][0] == "oturum-b2"
    assert adapter._sessions[key_a][0] == "oturum-a"


def test_mutation_a_single_global_slot_would_leak_one_tenant_session_to_another() -> None:
    """Anahtarlamayı geri al → ikinci kiracı birincinin jetonunu kullanıyor mu?

    Önbellek tek slota indirgeniyor (anahtar sabitleniyor). Sonuç: kiracı B
    hiç login açmadan A'nın oturumuyla sorgu yapıyor — çok kiracılı kurulumda
    A'nın adına belge işlemek demek.
    """
    def run(single_slot: bool) -> tuple[int, str]:
        transport = ScriptTransport(
            [LOGIN_OK.replace(b"{n}", b"a"), STATUS_OK, LOGIN_OK.replace(b"{n}", b"b"), STATUS_OK]
        )
        settings = _tenant_settings("kiraci-a")
        adapter = IzibizEInvoiceProvider(settings, transport)
        if single_slot:
            adapter._session_key = lambda: ("GLOBAL", "GLOBAL", "GLOBAL")  # type: ignore[assignment]
        adapter.query_status("SNG-A", channel="EARSIV", uuid=ETTN)
        settings.einvoice_username = "kiraci-b"
        adapter.query_status("SNG-B", channel="EARSIV", uuid=ETTN)
        queries = [c for c in transport.calls if "GetEArchiveInvoiceStatusRequest" in c.body]
        return transport.logins(), queries[-1].body

    keyed_logins, keyed_body = run(single_slot=False)
    global_logins, global_body = run(single_slot=True)

    # Sağlam: iki kiracı, iki login, B kendi jetonuyla.
    assert keyed_logins == 2
    assert "oturum-b" in keyed_body

    # Mutasyon: tek slot → B hiç login açmadı ve A'nın jetonunu kullandı.
    assert global_logins == 1
    assert "oturum-a" in global_body
    assert "oturum-b" not in global_body


# ==========================================================================
# 2b) Registry büyümesi sınırlı: süresi geçmiş kayıtlar temizleniyor
# ==========================================================================
def test_expired_sessions_and_their_locks_are_evicted_on_access() -> None:
    """50 anahtar aç, TTL'i geçir, bir erişimden sonra sözlükler küçülsün.

    Üst sınır yok; tek ölçüt süresi dolmuş olmak. Ölçüm gerçek: önce boyut
    sayılıyor, sonra saat ileri alınıyor, sonra tek bir erişim yapılıyor.
    """
    transport = ScriptTransport(default=LOGIN_OK.replace(b"{n}", b"x"))
    adapter = IzibizEInvoiceProvider(_tenant_settings("kiraci"), transport, company_id=1)

    for index in range(50):
        adapter._company_id = index
        adapter._session_token()

    assert len(adapter._sessions) == 50
    assert len(adapter._session_locks) == 50
    # Hiçbiri kullanımda değil: her çağrı kendi işaretini bıraktı.
    assert adapter._session_inflight == {}

    # TTL'i geçir: saklanan son kullanma anlarını geçmişe çek.
    adapter._sessions = {
        key: (token, deadline - adapter._token_ttl() * 1_000_000_000 - 1)
        for key, (token, deadline) in adapter._sessions.items()
    }

    # Tek bir erişim — 51. kiracı.
    adapter._company_id = 999
    active_key = adapter._session_key()
    adapter._session_token()

    assert len(adapter._sessions) == 1, adapter._sessions
    assert list(adapter._sessions) == [active_key]
    assert len(adapter._session_locks) == 1
    assert adapter._session_inflight == {}


def test_the_key_being_used_is_never_evicted_by_its_own_access() -> None:
    """Temizlik, o an istenen anahtarı korur — kendi altını oymaz."""
    transport = ScriptTransport(
        [LOGIN_OK.replace(b"{n}", b"1"), LOGIN_OK.replace(b"{n}", b"2")]
    )
    adapter = IzibizEInvoiceProvider(_tenant_settings("kiraci"), transport, company_id=1)

    first = adapter._session_token()
    key = adapter._session_key()
    # Süresini geçmiş göster.
    token, deadline = adapter._sessions[key]
    adapter._sessions[key] = (token, deadline - adapter._token_ttl() * 1_000_000_000 - 1)

    second = adapter._session_token()

    # Süresi dolduğu için yeniden giriş yapıldı ama anahtar yaşıyor.
    assert first == "oturum-1"
    assert second == "oturum-2"
    assert key in adapter._sessions
    assert key in adapter._session_locks


def _expire(adapter, key) -> None:
    """Saklanan oturumun son kullanma anını geçmişe çek."""
    token, deadline = adapter._sessions[key]
    adapter._sessions[key] = (token, deadline - adapter._token_ttl() * 1_000_000_000 - 1)


def test_a_lock_held_by_another_thread_survives_the_purge() -> None:
    """GERÇEK bir iş parçacığı kilidi TUTARKEN temizlik çalışsın.

    Önceki hâli sahteydi: hiç iş parçacığı başlatmıyor, kilidi hiç almıyordu —
    yani "kullanımdaki kilit korunur" iddiasını ölçmüyordu. Burada kilit
    gerçekten tutuluyor ve temizlikten sonra **aynı nesne** olduğu ``id()`` ile
    kanıtlanıyor. Nesne değişseydi iki iş parçacığı farklı kilitlerle aynı
    kritik bölgeye girerdi.
    """
    transport = ScriptTransport(default=LOGIN_OK.replace(b"{n}", b"x"))
    adapter = IzibizEInvoiceProvider(_tenant_settings("kiraci"), transport, company_id=1)

    adapter._session_token()
    stale_key = adapter._session_key()
    _expire(adapter, stale_key)

    lock = adapter._lock_for(stale_key)
    lock_id_before = id(lock)
    holding = threading.Event()
    release = threading.Event()
    acquired = []

    def worker() -> None:
        with lock:
            acquired.append(True)
            holding.set()
            release.wait(5)

    thread = threading.Thread(target=worker, name="kilit-tutan")
    thread.start()
    try:
        assert holding.wait(5), "iş parçacığı kilidi alamadı — test ölçmek istediğini kuramadı"
        assert acquired == [True]
        assert lock.locked(), "kilit gerçekten tutulmuyor"

        # İki koruma bağımsız ölçülsün. ``_lock_for`` kilidi verirken
        # ``_session_inflight``'i de artırmıştı; o işaret dururken purge zaten
        # satır 455'te kısa devre yapar ve ``acquire(blocking=False)`` koruması
        # hiç sınanmaz. İşareti bilerek düşür: geriye anahtarı ayakta tutacak
        # TEK şey kilidin tutuluyor olması kalsın.
        adapter._release_lock_use(stale_key)
        assert adapter._session_inflight.get(stale_key, 0) == 0, (
            "inflight işareti düşmedi — test hâlâ iki korumaya birden dayanıyor"
        )

        # Temizliği tetikle: başka bir kiracıya oturum aç.
        adapter._company_id = 2
        adapter._session_token()

        assert stale_key in adapter._session_locks, "kullanımdaki kilit silindi"
        assert id(adapter._session_locks[stale_key]) == lock_id_before, (
            "kilit nesnesi değişti — iki iş parçacığı farklı kilitlerle aynı bölgeye girebilir"
        )
    finally:
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive(), "iş parçacığı kapanmadı"

    # Bırakıldıktan sonra bir sonraki temizlikte gidebilir.
    adapter._company_id = 3
    adapter._session_token()
    assert stale_key not in adapter._session_locks
    assert stale_key not in adapter._sessions


def test_a_lock_handed_out_but_not_yet_acquired_survives_the_purge() -> None:
    """``_session_inflight``'in tek başına koruduğu pencere.

    ``_lock_for`` kilidi verdi ama çağıran henüz ``acquire`` etmedi: kilit
    BOŞTA görünür, yani ``acquire(blocking=False)`` kontrolü onu korumaz.
    Bu pencerede anahtarı ayakta tutan tek şey kullanım işareti. Kontrol
    değişkeni bir sonraki testte: işaret kaldırılınca kilit siliniyor.
    """
    transport = ScriptTransport(default=LOGIN_OK.replace(b"{n}", b"x"))
    adapter = IzibizEInvoiceProvider(_tenant_settings("kiraci"), transport, company_id=1)

    adapter._session_token()
    stale_key = adapter._session_key()
    _expire(adapter, stale_key)

    lock = adapter._lock_for(stale_key)
    assert not lock.locked(), "bu test kilidin BOŞTA olduğu pencereyi ölçer"
    # ``.get`` bilerek: kontrol değişkeni işareti kaldırdığında bu satır
    # KeyError ile patlayıp asıl iddianın ölçülmesini engellememeli. Kırmızı,
    # kurulumdan değil aşağıdaki eviction'dan gelmeli.
    assert adapter._session_inflight.get(stale_key, 0) in (0, 1)

    try:
        adapter._company_id = 2
        adapter._session_token()  # temizliği tetikler
        assert stale_key in adapter._session_locks
        assert id(adapter._session_locks[stale_key]) == id(lock)
    finally:
        adapter._release_lock_use(stale_key)


class _IgnoringInflight(dict):
    """Yazmaları yutan sözlük — ``_session_inflight`` artışını etkisizleştirir."""

    def __setitem__(self, key, value) -> None:  # pragma: no cover - mutasyon aracı
        pass


def test_mutation_without_the_inflight_marker_the_idle_lock_is_evicted() -> None:
    """Kontrol değişkeni: kullanım işareti kaldırılınca kilit siliniyor.

    Bir üstteki test bu olmadan yeşil kalırdı — çünkü kilit boşta olduğu için
    ``acquire`` kontrolü onu korumaz. Buradaki eviction, o testin gerçekten
    ``_session_inflight``'i ölçtüğünün kanıtı.
    """
    transport = ScriptTransport(default=LOGIN_OK.replace(b"{n}", b"x"))
    adapter = IzibizEInvoiceProvider(_tenant_settings("kiraci"), transport, company_id=1)
    adapter._session_inflight = _IgnoringInflight()

    adapter._session_token()
    stale_key = adapter._session_key()
    _expire(adapter, stale_key)

    lock = adapter._lock_for(stale_key)
    assert adapter._session_inflight == {}, "mutasyon işlemedi — işaret hâlâ yazılıyor"

    adapter._company_id = 2
    adapter._session_token()

    assert stale_key not in adapter._session_locks, (
        "işaret kaldırıldığı hâlde kilit korunuyor — üstteki test yük taşımıyor"
    )
    # Aynı anahtar tekrar istendiğinde BAŞKA bir kilit nesnesi doğar.
    adapter._company_id = 1
    assert id(adapter._lock_for(stale_key)) != id(lock)


def test_a_live_session_is_not_evicted() -> None:
    """Temizlik yalnız süresi GEÇMİŞ kayıtları alıyor; canlı oturum duruyor."""
    transport = ScriptTransport(
        [LOGIN_OK.replace(b"{n}", b"a"), LOGIN_OK.replace(b"{n}", b"b")]
    )
    adapter = IzibizEInvoiceProvider(_tenant_settings("kiraci"), transport, company_id=1)

    adapter._session_token()
    live_key = adapter._session_key()

    adapter._company_id = 2
    adapter._session_token()

    assert live_key in adapter._sessions
    assert len(adapter._sessions) == 2
    assert transport.logins() == 2


# CI yeniden tetikleme (no-op): GitHub bu PR icin synchronize/reopened olaylarini
# dusurdu; run yaratmak icin tek satirlik yorum. Test davranisi degismez.
