"""İzibiz ortam kilidi — adaptör canlı bir uca çağrı yapamaz.

Tier 1'de bir boşluk vardı: ``_assert_test_endpoint`` yalnız duman betiğinde
duruyordu, ``app/einvoice`` tarafında karşılığı yoktu. Operatör
``EINVOICE_BASE_URL``'i canlı bir adrese ayarladığında adaptörü durduran hiçbir
şey yoktu. Bu dosya o kilidin sözleşmesini sabitliyor.

Sözleşme:

1. ``IZIBIZ_ENV`` yalnız ``test`` | ``live``. Ayarlanmamışsa ``test``; **boş
   dize ya da başka bir değer hatadır** — sessiz varsayılan yok.
2. ``test`` ortamı: ana makine adı ``test`` içermiyorsa çağrı yapılmadan ret.
   Bilinen canlı uçlar **her ortamda** ret.
3. ``live`` ortamı: ``test`` imzalı adrese de ret — ters yön de kapalı, canlı
   yapılandırmada kazara sandbox'a fatura kesilmesin.
4. Kontrol **her ağ çağrısının hemen öncesinde**; sınıf kurulumunda değil.
5. ``submit`` / ``query_status`` / ``fetch_pdf`` / ``check_taxpayer`` dördü de
   fail-closed: ``FAILED`` / ``UNRESOLVED`` / ``EInvoiceError``. Sessiz ``None``
   ya da "başarılı gibi" bir dönüş yok.

Bu dosyadaki **hiçbir test soket açmaz**: ``socket.socket`` etkisiz hâle
getirilmiş durumda (bkz. :func:`_no_sockets`) ve taşıma çifti çağrı sayar.
"""

from __future__ import annotations

import socket
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.einvoice import (
    FAILED,
    PENDING,
    UNRESOLVED,
    EInvoiceError,
    IzibizEInvoiceProvider,
    build_einvoice_payload,
)
from app.einvoice import endpoints as wire
from app.einvoice import transport as transport_module
from app.einvoice.endpoints import IzibizEnvironmentError
from app.einvoice.transport import HttpResponse


TEST_HOST = "https://efaturatest.izibiz.com.tr"
LIVE_HOST = "https://efaturaws.izibiz.com.tr"
NEUTRAL_LIVE_HOST = "https://efatura.baskasaglayici.com"


class SocketOpened(AssertionError):
    """Bir test gerçekten soket açmaya kalkıştı — bu dosyada olmamalı."""


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ağ kanıtı: soket açmak bu dosyada imkânsız.

    Taşıma çiftinin çağrı sayması "adaptör istemedi"yi gösterir; bu da
    "istese bile açamazdı"yı garanti eder. İkisi birlikte, testlerin gerçekten
    ağa çıkmadığının kanıtı.
    """

    def _explode(*args, **kwargs):
        raise SocketOpened("Bu test soket açmaya çalıştı")

    monkeypatch.setattr(socket, "socket", _explode)
    monkeypatch.setattr(socket, "create_connection", _explode)


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport_module, "_sleep", lambda seconds: None)


class CountingTransport:
    """Çağrı sayan taşıma. Sayaç sıfır kalmalı: kilit soketten ÖNCE devreye girer."""

    def __init__(self, response: HttpResponse | None = None) -> None:
        self.calls: list[str] = []
        self.response = response or HttpResponse(200, b"<Response/>")

    def request(self, method: str, url: str, *, body=None, headers=None, **kwargs) -> HttpResponse:
        self.calls.append(url)
        return self.response


def make(base_url: str | None, env: object = "test") -> tuple[IzibizEInvoiceProvider, CountingTransport]:
    settings = SimpleNamespace(
        einvoice_provider="izibiz",
        einvoice_username="kullanici",
        einvoice_password="parola",
        einvoice_base_url=base_url,
        einvoice_sender_vkn="1111111111",
        einvoice_api_key=None,
        einvoice_endpoints_verified=False,
        izibiz_env=env,
    )
    transport = CountingTransport()
    return IzibizEInvoiceProvider(settings, transport), transport


def payload(**overrides) -> dict:
    value = build_einvoice_payload(
        {
            "invoice_number": "SNG2026000000001",
            "currency": "TRY",
            "company_id": 7,
            "invoice_id": 42,
            "issued_at": "2026-07-29 14:04:04+03:00",
            "is_efatura_user": False,  # → EARSIV (İzibiz'de açık olan kanal)
            "company": {"id": 7, "name": "Sungur Tarım A.Ş.", "tax_number": "1111111111"},
            "customer": {"name": "Deneme Müşteri", "tax_number": "11111111111"},
            "totals": {"tax": Decimal("20.00"), "grand_total": Decimal("120.00")},
            "items": [
                {
                    "description": "Kalem",
                    "qty": Decimal("1"),
                    "unit_price": Decimal("100"),
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
# 1) IZIBIZ_ENV doğrulaması — sessiz varsayılan yok
# ==========================================================================
def test_unset_environment_defaults_to_test() -> None:
    """Hiç ayarlanmamış ⇒ en güvenli taraf."""
    assert wire.izibiz_environment(None) == wire.IZIBIZ_ENV_TEST
    assert wire.IZIBIZ_DEFAULT_ENV == "test"


@pytest.mark.parametrize("value", ["test", "TEST", " Test ", "live", "LIVE"])
def test_valid_environments_are_normalised(value: str) -> None:
    assert wire.izibiz_environment(value) in wire.IZIBIZ_ENVIRONMENTS


@pytest.mark.parametrize("value", ["", "   ", "prod", "production", "sandbox", "canli", "0", "yes"])
def test_an_unrecognised_environment_is_an_error_not_a_default(value: str) -> None:
    """Boş dize dâhil: ``IZIBIZ_ENV=`` yarım kalmış bir deploy'dur, "test" değil."""
    with pytest.raises(IzibizEnvironmentError) as excinfo:
        wire.izibiz_environment(value)
    assert "IZIBIZ_ENV" in str(excinfo.value)


def test_the_settings_default_is_test() -> None:
    """Yapılandırmanın kendisi de güvenli tarafta başlıyor."""
    from app.config import Settings

    assert Settings.model_fields["izibiz_env"].default == "test"


# ==========================================================================
# 2) Tablo testi — (IZIBIZ_ENV, host) kombinasyonları
# ==========================================================================
GUARD_TABLE = [
    # (env, url, izin_var_mı, açıklama)
    ("test", TEST_HOST, True, "test ortamı + sandbox ucu → tek izinli kombinasyon"),
    ("test", TEST_HOST.replace("https://", "http://"), False, "TLS'siz sandbox ucu → ret"),
    ("test", TEST_HOST.replace("https://", "https://user:pass@"), False, "URL userinfo → ret"),
    ("test", "https://[bad", False, "bozuk URL → ret"),
    ("test", f"{TEST_HOST}:bad", False, "bozuk port → ret"),
    ("test", f"{TEST_HOST}:444", False, "allowlist dışı port → ret"),
    ("test", "https://test.attacker.invalid", False, "adı test içeren saldırgan host → ret"),
    ("test", LIVE_HOST, False, "test ortamı + bilinen canlı uç → ret"),
    ("test", NEUTRAL_LIVE_HOST, False, "test ortamı + test imzası olmayan uç → ret"),
    ("test", "", False, "boş URL → ret (çözümlenemeyen host 'sorun yok' değildir)"),
    ("test", "https://", False, "host'suz URL → ret"),
    ("test", "not-a-url", False, "şemasız metin → ret"),
    ("live", TEST_HOST, False, "canlı ortam + sandbox ucu → ret (ters yön de kapalı)"),
    ("live", LIVE_HOST, False, "canlı ortam + denylist ucu → ret (denylist her ortamda)"),
    ("live", NEUTRAL_LIVE_HOST, False, "canlı ortam + allowlist dışı uç → ret"),
]


@pytest.mark.parametrize(("environment", "url", "allowed", "why"), GUARD_TABLE)
def test_guard_table(environment: str, url: str, allowed: bool, why: str) -> None:
    violation = wire.izibiz_endpoint_violation(url, environment)
    if allowed:
        assert violation is None, f"{why} — beklenmedik ret: {violation}"
    else:
        assert violation is not None, f"{why} — kilit AÇIK kaldı"
        assert violation.strip(), "ret gerekçesi boş olamaz"


@pytest.mark.parametrize("environment", ["test", "live"])
@pytest.mark.parametrize("blocked", wire.IZIBIZ_LIVE_HOST_DENYLIST)
def test_every_denylisted_host_is_blocked_in_every_environment(
    environment: str, blocked: str
) -> None:
    """Denylist'in tamamı, iki ortamda da. Yeni bir uç eklenirse bu test kapsar."""
    assert wire.izibiz_endpoint_violation(f"https://{blocked}/WS", environment) is not None
    # Alt alan adı da kapalı — 'x.efaturaws.izibiz.com.tr' bir kaçış yolu değil.
    assert wire.izibiz_endpoint_violation(f"https://x.{blocked}/WS", environment) is not None


def test_the_denylist_message_tells_a_live_operator_what_is_actually_going_on() -> None:
    """Aynı engel, iki ortamda iki mesaj.

    ``test`` ortamında "CANLI UÇ ENGELLENDİ" doğru ve yeterli. ``live``
    ortamında ise yanıltıcı: operatör zaten canlı moddadır ve mesajı bir
    yapılandırma hatası sanar. Orada söylenmesi gereken, engelin KASITLI olduğu
    ve kaldırma kararının nerede verileceğidir.
    """
    in_test = wire.izibiz_endpoint_violation(LIVE_HOST, "test")
    in_live = wire.izibiz_endpoint_violation(LIVE_HOST, "live")

    assert in_test is not None and in_live is not None
    assert in_test != in_live

    # test ortamı: kısa ve net.
    assert "CANLI e-Fatura ucu engellendi" in in_test
    assert "efaturaws.izibiz.com.tr" in in_test

    # live ortamı: durumu anlatan mesaj.
    assert "IZIBIZ_ENV=live" in in_live
    assert "efaturaws.izibiz.com.tr" in in_live
    assert "IZIBIZ_LIVE_HOST_DENYLIST" in in_live  # nereden kaldırılacağı
    assert "bilinçli bir karar" in in_live  # engelin kasıtlı olduğu
    assert "PR" in in_live  # gerekçelendirme beklentisi
    # "yapılandırma yanlış" izlenimi vermemeli.
    assert "CANLI e-Fatura ucu engellendi" not in in_live


def test_the_live_denylist_message_reaches_the_caller_intact() -> None:
    """Mesaj sabitte kalmıyor, gerçekten sonuca çıkıyor."""
    adapter, transport = make(LIVE_HOST, env="live")

    result = adapter.submit(payload())

    assert result.status == FAILED
    assert result.error == wire.IZIBIZ_LIVE_ENV_DENYLIST_ERROR.format(
        host="efaturaws.izibiz.com.tr"
    )
    assert transport.calls == []


def test_the_only_allowed_combination_really_is_allowed() -> None:
    """Kilit her şeyi reddetseydi testler yanlış nedenle yeşil olurdu."""
    assert wire.izibiz_endpoint_violation(TEST_HOST, "test") is None
    assert wire.izibiz_endpoint_violation(f"{TEST_HOST}/EInvoiceWS", "test") is None


# ==========================================================================
# 3) Dört giriş noktası da fail-closed
# ==========================================================================
def test_all_four_entry_points_refuse_a_live_endpoint_without_touching_the_network() -> None:
    adapter, transport = make(LIVE_HOST, env="test")

    submitted = adapter.submit(payload())
    assert submitted.status == FAILED
    assert submitted.external_id is None
    assert "engellendi" in (submitted.error or "")

    queried = adapter.query_status("SNG2026000000001", channel="EARSIV", uuid="A" * 36)
    assert queried.status == UNRESOLVED
    assert queried.status != FAILED  # ETTN'i inkâr etmiyor
    assert queried.external_id == "SNG2026000000001"
    assert queried.error

    with pytest.raises(EInvoiceError):
        adapter.fetch_pdf("SNG2026000000001", channel="EFATURA")
    with pytest.raises(EInvoiceError):
        adapter.check_taxpayer("1111111111")

    # Kanıt: hiçbiri taşımaya bile ulaşmadı.
    assert transport.calls == []


def test_an_invalid_environment_blocks_all_four_the_same_way() -> None:
    """Ortam okunamıyorsa "bilmiyorum ama göndereyim" yok."""
    adapter, transport = make(TEST_HOST, env="prod")

    assert adapter.submit(payload()).status == FAILED
    assert adapter.query_status("X", channel="EARSIV", uuid="A" * 36).status == UNRESOLVED
    with pytest.raises(EInvoiceError):
        adapter.fetch_pdf("X", channel="EFATURA")
    with pytest.raises(EInvoiceError):
        adapter.check_taxpayer("1111111111")
    assert transport.calls == []


def test_live_environment_refuses_the_sandbox_so_no_invoice_lands_in_the_wrong_world() -> None:
    adapter, transport = make(TEST_HOST, env="live")

    result = adapter.submit(payload())

    assert result.status == FAILED
    assert "sandbox" in (result.error or "")
    assert transport.calls == []


# ==========================================================================
# 4) Kontrol ÇAĞRI BAŞINA — kurulumda değil
# ==========================================================================
def test_the_guard_is_re_evaluated_on_every_call_not_cached_at_construction() -> None:
    """Aynı adaptör örneği, ortam değişince davranışını değiştirmeli.

    Kurulumda bir kez bakılsaydı, uzun ömürlü bir worker yeniden
    yapılandırıldıktan sonra eski karara göre çağrı yapmaya devam ederdi.
    """
    settings = SimpleNamespace(
        einvoice_provider="izibiz",
        einvoice_username="kullanici",
        einvoice_password="parola",
        einvoice_base_url=TEST_HOST,
        einvoice_sender_vkn="1111111111",
        einvoice_api_key=None,
        einvoice_endpoints_verified=False,
        izibiz_env="test",
    )
    transport = CountingTransport()
    adapter = IzibizEInvoiceProvider(settings, transport)

    # İzinli ortamda çağrı taşımaya ULAŞIYOR (login denemesi).
    adapter.submit(payload())
    assert len(transport.calls) >= 1
    reached_while_allowed = len(transport.calls)

    # Adaptör örneği aynı; yalnız ortam değişti.
    settings.izibiz_env = "live"
    blocked = adapter.submit(payload())

    assert blocked.status == FAILED
    assert "sandbox" in (blocked.error or "")
    assert len(transport.calls) == reached_while_allowed  # yeni çağrı YOK


# ==========================================================================
# 5) MUTASYON KANITI — kilit sökülünce canlıya çağrı geçiyor mu?
# ==========================================================================
def test_mutation_removing_the_guard_lets_a_call_reach_a_live_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kilit sökülürse canlı adrese giden çağrı taşımaya ULAŞIR.

    Sağlam hâlde: ``FAILED``, sıfır taşıma çağrısı.
    Sökük hâlde: taşıma ``efaturaws.izibiz.com.tr`` adresine çağrılıyor — yani
    kilit olmasa gerçekten canlı uca gidilirdi. Bu, yukarıdaki testlerin yük
    taşıdığının kanıtı.
    """
    # --- sağlam ---
    intact_adapter, intact_transport = make(LIVE_HOST, env="test")
    intact = intact_adapter.submit(payload())
    assert intact.status == FAILED
    assert intact_transport.calls == []

    # --- kilidi sök ---
    monkeypatch.setattr(IzibizEInvoiceProvider, "_guard", lambda self, url: None)
    mutated_adapter, mutated_transport = make(LIVE_HOST, env="test")
    mutated_adapter.submit(payload())

    assert mutated_transport.calls, "kilit sökülmesine rağmen çağrı yapılmadı — test yük taşımıyor"
    assert any("efaturaws.izibiz.com.tr" in url for url in mutated_transport.calls)
    # Yan yana: aynı yapılandırma, tek fark kilit.
    assert intact_transport.calls == [] and mutated_transport.calls != []


def test_mutation_removing_the_guard_would_even_produce_a_pending_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En kötü hâl: kilit yokken canlı uçtan "gönderildi" cevabı dönebilir.

    Taşıma, gerçek bir başarılı e-Arşiv yanıtı taklit ediyor. Kilit sökülünce
    adaptör ``PENDING`` + gerçek bir ``external_id`` üretiyor — canlı ortamda
    bu, kazara kesilmiş bir fatura demek.
    """
    success = HttpResponse(
        200,
        b'<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>'
        b'<ns3:LoginResponse xmlns:ns3="http://schemas.i2i.com/ei/wsdl">'
        b"<SESSION_ID>oturum</SESSION_ID></ns3:LoginResponse></S:Body></S:Envelope>",
    )
    written = HttpResponse(
        200,
        b'<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>'
        b'<ArchiveInvoiceExtendedResponse xmlns="http://schemas.i2i.com/ei/wsdl/archive">'
        b'<REQUEST_RETURN xmlns=""><RETURN_CODE>0</RETURN_CODE></REQUEST_RETURN>'
        b'<INVOICE_ID xmlns="">CANLI2026000000001</INVOICE_ID>'
        b"</ArchiveInvoiceExtendedResponse></S:Body></S:Envelope>",
    )

    class ScriptedTransport(CountingTransport):
        def request(self, method, url, *, body=None, headers=None, **kwargs):
            self.calls.append(url)
            return success if len(self.calls) == 1 else written

    def build():
        settings = SimpleNamespace(
            einvoice_provider="izibiz",
            einvoice_username="kullanici",
            einvoice_password="parola",
            einvoice_base_url=LIVE_HOST,
            einvoice_sender_vkn="1111111111",
            einvoice_api_key=None,
            einvoice_endpoints_verified=False,
            izibiz_env="test",
        )
        transport = ScriptedTransport()
        return IzibizEInvoiceProvider(settings, transport), transport

    intact_adapter, intact_transport = build()
    intact = intact_adapter.submit(payload())
    assert intact.status == FAILED
    assert intact.external_id is None
    assert intact_transport.calls == []

    monkeypatch.setattr(IzibizEInvoiceProvider, "_guard", lambda self, url: None)
    mutated_adapter, mutated_transport = build()
    mutated = mutated_adapter.submit(payload())

    assert mutated.status == PENDING  # ← canlı uçta kazara belge
    assert mutated.external_id == "CANLI2026000000001"
    assert any("efaturaws.izibiz.com.tr" in url for url in mutated_transport.calls)


# ==========================================================================
# 6) Ağ kanıtı ve dokunulmaması gereken bayrak
# ==========================================================================
def test_the_socket_sentinel_is_actually_armed() -> None:
    """Ağ kanıtının kendisi çalışıyor mu — nöbetçi gerçekten patlıyor mu?"""
    with pytest.raises(SocketOpened):
        socket.socket()
    with pytest.raises(SocketOpened):
        socket.create_connection(("example.invalid", 443))


def test_the_efatura_submit_gate_is_still_closed() -> None:
    """Ortam kilidi eklenirken e-Fatura gönderim kapısına dokunulmadı."""
    assert wire.IZIBIZ_EFATURA_SUBMIT_VERIFIED is False
