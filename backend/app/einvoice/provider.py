"""Pluggable e-Fatura provider seam + the real İzibiz / Nes adapters.

The DEFAULT is :class:`NoOpEInvoiceProvider`: it sends nothing, touches no
network, and reports status ``NONE`` — so with no configuration the internal
invoice flow behaves exactly as before.

:class:`IzibizEInvoiceProvider` (SOAP) and :class:`NesEInvoiceProvider` (REST)
are real adapters: request building, authentication, the spec §5 state machine,
the spec §6 error mapping and the spec §7 timeout/retry/idempotency policy are
implemented and tested. Every endpoint, operation name and response field name
lives in :mod:`app.einvoice.endpoints` — never here.

**The two halves differ, and this header used to say otherwise.** İzibiz is
verified: its operation names, body roots, namespaces and field names were read
from the WSDL/XSD downloaded live from ``efaturatest.izibiz.com.tr`` and
confirmed by real sandbox calls (evidence: ``docs/izibiz-sandbox-bulgular.md``,
``backend/tests/fixtures/izibiz/``, ``backend/test_izibiz_wire_contract.py``).
Nes is **not** verified — still ‹doğrulanacak› placeholders with an empty
``NES_BASE_URL`` and ``NES_ENDPOINTS_VERIFIED = False``.

Verified wire details are not a licence to send. Two gates must BOTH open before
a call leaves the building: a real configured URL *and* the verified-wire flag.
Configuring a base URL alone does not license anything. Separately, the LIVE
İzibiz hosts sit in ``IZIBIZ_LIVE_HOST_DENYLIST`` and the live allowlist is
empty, so the production channel is deliberately closed until someone removes
them in a PR that argues for it. A closed gate yields a loud failure; it never
fabricates a call, an ``external_id`` or a success.

Contracts this module must never break (spec §1, §5):

1. ``query_status()`` never returns ``ACCEPTED`` unconditionally. ``ACCEPTED``
   comes only from :func:`~app.einvoice.status.map_provider_status` reading the
   provider's own answer.
2. ``submit()`` takes the ``external_id``/ETTN from the provider response. If
   the response carries none the result is ``FAILED`` — never a made-up id, and
   never ``REJECTED`` either: a rejection without an ETTN is a failed send, not
   a GİB verdict on an existing envelope.
3. No method produces a silent fake success — **including the NoOp**. Failure is
   either a ``FAILED``/``UNRESOLVED`` result or an
   :class:`~app.einvoice.errors.EInvoiceError`. Methods whose return type cannot
   express failure (``fetch_pdf`` → ``bytes``, ``check_taxpayer`` → ``dict``)
   raise instead of returning an empty or default-looking value.
4. ``FAILED`` is submit-time only. A status query that cannot be answered
   returns :data:`~app.einvoice.status.UNRESOLVED` with the ETTN intact, so the
   result never asserts "there is no ETTN" while carrying one.
5. Credentials never reach ``error``, ``raw`` or the log — and the live session /
   bearer token counts as a credential.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote
from xml.etree import ElementTree
from xml.sax.saxutils import escape as xml_escape

from . import endpoints as wire
from . import ubl_xml
from .errors import (
    AUTH,
    NETWORK,
    UNKNOWN,
    VALIDATION,
    EInvoiceError,
    classify_body,
    classify_http,
    message_for,
    scrub,
)
from .status import (
    FAILED,
    NONE,
    PENDING,
    QUERYABLE,
    REJECTED,
    UNRESOLVED,
    advance_status,
    map_provider_status,
)
from .transport import (
    HttpResponse,
    HttpTransport,
    TransportError,
    call_once,
    response_summary,
    with_retry,
)
from .ubl import CHANNEL_EARSIV, CHANNEL_EFATURA, missing_required_fields


logger = logging.getLogger(__name__)

_NOT_CONFIGURED = "e-Fatura sağlayıcısı yapılandırılmamış"

#: Saniye → nanosaniye. Oturum son kullanma zamanı tamsayı nanosaniye tutulur.
_NANOSECONDS = 1_000_000_000

#: Oturum önbelleğinin anahtarı: (kiracı, süreç-içi HMAC, taban URL).
#: Ham kimlik bilgileri KASITLI olarak yok — anahtar sözlükte durur ve bir hata
#: ayıklama dökümüne düşebilir. Rastgele anahtar her süreç açılışında yenilenir.
_SessionKey = tuple[Any, str, str]
_SESSION_USERNAME_HMAC_KEY = secrets.token_bytes(32)


def _secret_text(value: Any) -> str:
    """Return a credential value at the provider boundary, supporting SecretStr."""
    getter = getattr(value, "get_secret_value", None)
    revealed = getter() if callable(getter) else value
    return str(revealed or "")


def _session_username_key(username: str) -> str:
    return hmac.new(
        _SESSION_USERNAME_HMAC_KEY,
        username.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@dataclass
class EInvoiceResult:
    """Outcome of a provider call. ``raw`` keeps the provider's untranslated body."""

    status: str
    channel: str | None = None
    uuid: str | None = None
    external_id: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class EInvoiceProvider(ABC):
    """Contract every e-Fatura backend implements."""

    @abstractmethod
    def submit(self, payload: dict[str, Any]) -> EInvoiceResult:
        ...

    @abstractmethod
    def query_status(
        self, external_id: str, *, channel: str | None = None, uuid: str | None = None
    ) -> EInvoiceResult:
        """Poll one document.

        ``channel``/``uuid`` are optional because not every provider needs them.
        İzibiz does: e-Fatura and e-Arşiv are **different services** with
        different status operations, and neither echoes the ETTN back at submit
        time — so ``external_id`` there is the provider's own document id and
        the ETTN has to be supplied separately. Providers that key everything
        off a single identifier (Nes) ignore both.
        """
        ...

    @abstractmethod
    def fetch_pdf(self, external_id: str, *, channel: str | None = None) -> bytes:
        ...

    @abstractmethod
    def check_taxpayer(self, vkn: str) -> dict[str, bool]:
        """Decide EFATURA vs EARSIV: ``{"is_efatura_user": bool}``."""
        ...

    # --- yapılandırma sinyali ---------------------------------------------
    # Soyut DEĞİL: bir sağlayıcının "çağrı yapabilir miyim" sorusuna cevabı
    # varsayılan olarak HAYIR'dır. Yeni bir adaptör bu metodu yazmayı unutursa
    # sonuç fail-closed olur; sessizce "yapılandırıldım" diyen bir taban
    # uygulama, bu sinyalin engellemek için var olduğu şeyin ta kendisidir.
    def is_configured(self) -> bool:
        """Bu sağlayıcı gerçekten bir çağrı yapabilir mi?"""
        return False

    def configuration_error(self) -> str | None:
        """``is_configured()`` False iken Türkçe gerekçe, aksi hâlde ``None``.

        Gerekçe operatöre/log'a yöneliktir ve **asla** kimlik bilgisi taşımaz:
        yalnız hangi kapının kapalı olduğunu söyler.
        """
        return _NOT_CONFIGURED


class NoOpEInvoiceProvider(EInvoiceProvider):
    """Default provider — inert, no network, keeps the internal invoice authoritative.

    "Inert" cuts both ways. ``submit``/``query_status`` return ``NONE``, which is
    a truthful answer: *this document has no external life*. But the other two
    methods answer **questions**, and a NoOp has no answer to give — so they
    raise rather than return a value that reads like one:

    * ``fetch_pdf`` returning ``b""`` would be indistinguishable from an empty
      PDF at the call site: a silent fake success, exactly what spec §1 forbids.
    * ``check_taxpayer`` returning a definite ``False`` is not "no answer", it is
      the answer "this buyer is not an e-Fatura taxpayer" — which would route a
      document to e-Arşiv on the strength of a provider that never asked anyone.
      Not defaulting to ``True`` is necessary but not sufficient; a NoOp must not
      decide the channel at all.
    """

    def submit(self, payload: dict[str, Any]) -> EInvoiceResult:
        return EInvoiceResult(status=NONE, channel=None, raw={})

    def query_status(
        self, external_id: str, *, channel: str | None = None, uuid: str | None = None
    ) -> EInvoiceResult:
        return EInvoiceResult(status=NONE)

    def fetch_pdf(self, external_id: str, *, channel: str | None = None) -> bytes:
        raise EInvoiceError(UNKNOWN, _NOT_CONFIGURED)

    def check_taxpayer(self, vkn: str) -> dict[str, bool]:
        raise EInvoiceError(UNKNOWN, _NOT_CONFIGURED)


# --------------------------------------------------------------------------
# Shared HTTP adapter behaviour
# --------------------------------------------------------------------------
class _HttpEInvoiceProvider(EInvoiceProvider):
    """Everything the two real adapters share: policy, mapping, safety.

    Subclasses only supply protocol mechanics (how to log in, how to shape a
    request, how to read a field out of a response). All behaviour that must not
    drift between providers — state machine, error classes, retry policy,
    credential scrubbing — lives here, governed by one implementation.
    """

    name = "provider"

    def __init__(
        self, settings: Any = None, transport: Any = None, *, company_id: Any = None
    ) -> None:
        self._settings = settings
        self._transport = transport if transport is not None else HttpTransport()
        # Kiracı kimliği. Bugün kimlik bilgileri ``.env``'den global geliyor, ama
        # oturum anahtarına şimdiden giriyor: kimlikler kiracı başına DB'ye
        # taşındığında iki bayinin yanlışlıkla aynı kullanıcı adıyla
        # yapılandırılması, bugünkü anahtarla aynı oturumu paylaşmaları demekti.
        # ``None`` = çağıran kiracıyı bildirmedi (bkz. get_einvoice_provider).
        self._company_id = company_id
        # Oturumlar KİMLİK BAŞINA saklanır, tek bir global slotta değil.
        # ERP çok kiracılı: her satıcı kendi VKN'siyle fatura keser ve kendi
        # İzibiz kimliğine sahiptir. Tek slot, bir kiracının oturumunu
        # diğerine verirdi — yani A firmasının faturası B'nin adına giderdi.
        # Değer: (jeton, monotonic son kullanma **nanosaniye**; TTL yoksa 0).
        # Nanosaniye tamsayı: bu depo ikili kayan noktalı sayı kullanmaz
        # (proje geneli kural, test_v2_9_decimal_contract). Süre ölçümü para
        # değil ama kuralı dolanmak yerine tamsayıya geçmek hem uyumlu hem
        # daha doğru — monotonic_ns() yuvarlama hatası taşımaz.
        self._sessions: dict[_SessionKey, tuple[str, int]] = {}
        # Kilit de anahtar başına: iki farklı kiracı birbirini bloklamamalı.
        # Reentrant DEĞİL — _session_token() kendini çağırmaz; düz bir Lock,
        # yanlışlıkla eklenen bir iç içe çağrıyı sessizce geçirmek yerine kilitler.
        self._session_locks: dict[_SessionKey, threading.Lock] = {}
        # Şu an kullanılmakta olan anahtarlar. Temizlik bunlara dokunmaz:
        # kilidi alınmak üzere olan (henüz ``with`` içine girmemiş) bir anahtarın
        # kilit nesnesini silmek, iki iş parçacığının farklı kilit nesneleriyle
        # aynı kritik bölgeye girmesi demek olurdu.
        self._session_inflight: dict[_SessionKey, int] = {}
        # Sözlüklerin kendisini korur; içinde ağ çağrısı yapılmaz, yalnız
        # sözlük araması olduğu için kısa süre tutulur.
        self._session_registry_lock = threading.Lock()
        # Every session/bearer token this adapter has ever held. Kept (not just
        # the current one) so a rotated token stays redacted in anything that was
        # captured while it was live.
        self._seen_tokens: set[str] = set()

    # --- configuration -----------------------------------------------------
    def _setting(self, key: str) -> Any:
        return getattr(self._settings, key, None)

    @property
    def _username(self) -> str:
        return _secret_text(self._setting("einvoice_username"))

    @property
    def _password(self) -> str:
        return _secret_text(self._setting("einvoice_password"))

    @property
    def _secrets(self) -> tuple[str, ...]:
        """Values that must never appear in ``error``, ``raw`` or the log.

        The **session/bearer token belongs here too**: it is a live credential
        for the rest of its TTL, and a provider that echoes it back in an
        operation response would otherwise put it straight into the audit
        ``raw``. Configured credentials alone are not the whole secret set.
        """
        values = (
            self._setting("einvoice_username"),
            self._setting("einvoice_password"),
            self._setting("einvoice_api_key"),
            *self._seen_tokens,
        )
        secrets = tuple(_secret_text(value) for value in values)
        return tuple(secret for secret in secrets if secret)

    def _endpoint(self) -> str:
        raise NotImplementedError

    def _token_ttl(self) -> int:
        return 0

    # --- protocol hooks (subclass) ----------------------------------------
    def _authenticate(self) -> str:
        """Return an opaque session token, or raise :class:`EInvoiceError`."""
        raise NotImplementedError

    def _call(self, operation: str, session: str, **kwargs: Any) -> HttpResponse:
        raise NotImplementedError

    def _extract(self, response: HttpResponse, keys: tuple[str, ...]) -> str | None:
        raise NotImplementedError

    def _extract_pdf(self, response: HttpResponse) -> bytes:
        raise NotImplementedError

    def _field(self, name: str) -> tuple[str, ...]:
        raise NotImplementedError

    # --- result helpers ----------------------------------------------------
    def _fail(
        self,
        operation: str,
        code: str,
        *,
        field_name: Any = None,
        provider_code: Any = None,
        raw: dict[str, Any] | None = None,
        external_id: str | None = None,
        uuid: str | None = None,
        channel: str | None = None,
        message: str | None = None,
    ) -> EInvoiceResult:
        text = message or message_for(code, field=field_name, provider_code=provider_code)
        # Only the error CLASS is logged — never the body, the URL or the credentials.
        logger.warning("e-Fatura %s/%s başarısız (sınıf=%s)", self.name, operation, code)
        return EInvoiceResult(
            status=FAILED,
            channel=channel,
            uuid=uuid,
            external_id=external_id,
            error=scrub(text, self._secrets),
            raw=scrub({"error_class": code, **(raw or {})}, self._secrets),
        )

    def _unresolved(
        self,
        operation: str,
        code: str,
        *,
        external_id: str,
        provider_code: Any = None,
        raw: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> EInvoiceResult:
        """A status query that could not be answered — the envelope is untouched.

        Deliberately NOT ``FAILED``: that status asserts "the submission never
        landed, there is no ETTN", which directly contradicts the ``external_id``
        this very call was made with. The envelope still exists at the provider;
        only our knowledge of it is missing. :func:`advance_status` treats
        ``UNRESOLVED`` as a non-transition, so the stored document state survives
        while the failure is still reported loudly through ``error``.
        """
        text = message or message_for(code, provider_code=provider_code)
        logger.warning("e-Fatura %s/%s çözümlenemedi (sınıf=%s)", self.name, operation, code)
        return EInvoiceResult(
            status=UNRESOLVED,
            external_id=external_id,
            error=scrub(text, self._secrets),
            raw=scrub({"error_class": code, **(raw or {})}, self._secrets),
        )

    def _raise(
        self,
        operation: str,
        code: str,
        *,
        provider_code: Any = None,
        raw: dict[str, Any] | None = None,
    ) -> EInvoiceError:
        text = message_for(code, provider_code=provider_code)
        logger.warning("e-Fatura %s/%s başarısız (sınıf=%s)", self.name, operation, code)
        return EInvoiceError(
            code,
            scrub(text, self._secrets),
            raw=scrub({"error_class": code, **(raw or {})}, self._secrets),
        )

    def _submit_gate(self, payload: dict[str, Any]) -> str | None:
        """Sağlayıcıya özgü ek gönderim kapısı; engel varsa mesajı döner.

        Uç yapılandırmasından ayrıdır: uç doğru olabilir ama o sağlayıcının
        belirli bir kanalı hâlâ doğrulanmamış olabilir.
        """
        return None

    def _query_precondition(
        self, external_id: str, *, channel: str | None, uuid: str | None
    ) -> str | None:
        """Bu durum sorgusu kurulabilir mi? Kurulamıyorsa gerekçesi.

        **Ağdan önce** sorulur. Sorgunun kurulamayacağını login açtıktan sonra
        fark etmek, sağlayıcıda boşuna bir oturum açmak ve boşuna bir kimlik
        doğrulaması yapmak demek.
        """
        return None

    # --- iş hatası (HTTP durumundan bağımsız) ------------------------------
    def _business_failure(self, response: HttpResponse) -> tuple[str, str] | None:
        """Sağlayıcının **2xx gövdesinde** taşıdığı iş hatası, ya da ``None``.

        Sandbox'ta ölçülen gerçek davranış: İzibiz belge reddini SOAP Fault ya da
        5xx ile değil, **HTTP 200 + ``<ERROR_TYPE>``** ile bildiriyor. ``ok``
        bayrağına güvenen bir adaptör bunu başarı sayar ve reddedilmiş bir belge
        için "gönderildi" der. Bu yüzden her yanıt, HTTP durumu ne olursa olsun
        iş sonucu açısından ayrıştırılır ve **fail-closed** yorumlanır:
        ayrıştırılamayan/eksik bir sonuç zarfı "sorun yok" değildir.

        ``(hata_sınıfı, mesaj)`` döner. Varsayılan: sağlayıcı bu kanalı
        kullanmıyor.
        """
        return None

    def _summary(self, response: HttpResponse) -> dict[str, Any]:
        return scrub(response_summary(response, wire.MAX_PARSED_RESPONSE_BYTES), self._secrets)

    def _body_text(self, response: HttpResponse) -> str:
        return response.text(wire.MAX_PARSED_RESPONSE_BYTES)

    #: Overridden per provider from the ‹doğrulanacak› flags in endpoints.py.
    _wire_verified = False

    #: Provider-specific status vocabulary, consulted before the shared table.
    _status_aliases: dict[str, str] | None = None

    def _verified(self) -> bool:
        """Are this provider's wire details known-correct (or explicitly vouched for)?"""
        return bool(self._wire_verified or self._setting("einvoice_endpoints_verified"))

    def _configured(self) -> bool:
        """A usable endpoint needs BOTH a real URL and verified wire details."""
        return wire.is_configured(self._endpoint()) and self._verified()

    def _unconfigured_message(self) -> str:
        """Say which of the two gates failed — they need different actions."""
        if not wire.is_configured(self._endpoint()):
            return wire.UNCONFIGURED_ENDPOINT_ERROR
        return wire.UNVERIFIED_ENDPOINT_ERROR

    def is_configured(self) -> bool:
        """Aynı kapı, çağrı anındakiyle bire bir: URL **ve** doğrulanmış tel.

        Kasıtlı olarak :meth:`_configured` üzerinden okunur. Sinyalin kendi
        kopya kuralını yazması, "buton açık ama gönderim FAILED" ayrışmasının
        doğduğu yer olurdu.
        """
        return self._configured()

    def configuration_error(self) -> str | None:
        return None if self._configured() else self._unconfigured_message()

    def _session_key(self) -> _SessionKey:
        """Oturum anahtarı: ``(kiracı, süreç-içi kullanıcı HMAC'i, taban URL)``.

        Ham kullanıcı adı/parola anahtarın parçası değildir. Süreç açılışında
        üretilen rastgele HMAC anahtarı, debug dökümündeki değerin başka bir
        süreçle ilişkilendirilmesini veya kullanıcı adına geri çevrilmesini
        engeller; aynı süreçte cache ayrımı için kararlı kalır.

        Kiracı kimliği, kullanıcı adı tek başına yetmediği için var: kimlikler
        ``.env``'den çıkıp kiracı başına DB'ye taşındığında iki bayi
        yanlışlıkla aynı kullanıcı adıyla yapılandırılabilir. Kiracı anahtarda
        olmasaydı ikisi aynı oturumu paylaşır, biri diğerinin adına belge
        işlerdi.

        Taban URL de anahtarda: aynı kullanıcı adının test ve canlı oturumları
        ayrıdır ve birini diğerinde kullanmak, kimlik doğrulama hatasından
        beter — yanlış ortama belge göndermek.
        """
        return (self._company_id, _session_username_key(self._username), self._endpoint())

    def _purge_expired_sessions(self, *, protect: _SessionKey) -> None:
        """Süresi geçmiş oturumları ve artık sahipsiz kilitleri at.

        ``_session_registry_lock`` altında çağrılır. Üst sınır yok; ölçüt tek:
        süresi dolmuş olmak. Bir kilit yalnız ŞU ÜÇ koşul birden sağlanırsa
        silinir — aksi hâlde iki iş parçacığı farklı kilit nesneleriyle aynı
        kritik bölgeye girebilirdi:

        1. anahtarın süresi dolmuş bir oturumu vardı (yani bilinen bir anahtar),
        2. şu an kullanımda değil (``_session_inflight``),
        3. kilit boşta (``acquire(blocking=False)`` başarılı).
        """
        now = time.monotonic_ns()
        for key, (_, deadline) in list(self._sessions.items()):
            if key != protect and deadline <= now:
                self._sessions.pop(key, None)
        # Kilitler ayrı taranır. Süresi dolan bir oturumun kilidi o an
        # kullanımdaysa bırakılır; ama sonra hiç ziyaret edilmezse yetim kalır
        # ve sözlük sınırsız büyür. Bu yüzden ölçüt "oturumu vardı" değil,
        # "artık sahibi yok": oturumu yok, kullanımda değil, kilidi boşta.
        for key in list(self._session_locks):
            if key == protect or key in self._sessions or self._session_inflight.get(key):
                continue
            lock = self._session_locks[key]
            if lock.acquire(blocking=False):
                lock.release()
                self._session_locks.pop(key, None)

    def _lock_for(self, key: _SessionKey) -> threading.Lock:
        """Anahtar başına kilit — iki kiracı birbirini beklemez.

        Kilidi vermeden önce süresi geçmiş kayıtlar temizlenir; istenen anahtar
        temizlikten korunur ve "kullanımda" olarak işaretlenir.
        """
        with self._session_registry_lock:
            self._purge_expired_sessions(protect=key)
            lock = self._session_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._session_locks[key] = lock
            self._session_inflight[key] = self._session_inflight.get(key, 0) + 1
            return lock

    def _release_lock_use(self, key: _SessionKey) -> None:
        """Anahtarın "kullanımda" işaretini bırak."""
        with self._session_registry_lock:
            remaining = self._session_inflight.get(key, 1) - 1
            if remaining > 0:
                self._session_inflight[key] = remaining
            else:
                self._session_inflight.pop(key, None)

    def _session_token(self, *, force_refresh: bool = False) -> str:
        """Cached session, honouring the TTL (0 ⇒ always re-login).

        **Kilit altında.** İki eşzamanlı istek aynı anda süresi dolmuş bir jetonu
        görürse, kilit olmadan ikisi de login açar: sağlayıcıda gereksiz oturum,
        ve yarışı kaybeden çağrı kendi jetonunu diğerinin üzerine yazdığı için
        ilk çağrı artık geçersiz bir jetonla devam edebilir. Kilit içinde ikinci
        kez bakmak (double-checked) bunu kapatır: bekleyen iş, kilidi kazananın
        tazelediği jetonu bulur ve yeniden login açmaz.

        ``force_refresh`` yalnız AUTH sonrası tek seferlik yeniden giriş içindir
        (:meth:`_call_with_session`).
        """
        key = self._session_key()
        lock = self._lock_for(key)
        try:
            return self._authenticate_under(lock, key, force_refresh=force_refresh)
        finally:
            self._release_lock_use(key)

    def _authenticate_under(
        self, lock: Any, key: _SessionKey, *, force_refresh: bool
    ) -> str:
        with lock:
            ttl = self._token_ttl()
            if force_refresh:
                # Sağlayıcı bu jetonu reddetti; önbellek yanıltıcı.
                self._sessions.pop(key, None)
            else:
                cached = self._sessions.get(key)
                if cached and ttl > 0 and time.monotonic_ns() < cached[1]:
                    return cached[0]
            token = self._authenticate()
            # Registered as sensitive the moment it exists, before it can appear
            # in any response we summarise.
            if token:
                self._seen_tokens.add(str(token))
            expires_at = time.monotonic_ns() + ttl * _NANOSECONDS if ttl > 0 else 0
            self._sessions[key] = (token, expires_at)
            return token

    def _is_auth_failure(self, response: HttpResponse) -> bool:
        """Sağlayıcı "oturumun geçersiz" mi diyor?

        İki yoldan gelebilir: HTTP 401/403, ya da — İzibiz'in yaptığı gibi —
        HTTP 200 gövdesinde AUTH sınıfına düşen bir iş hatası.
        """
        if response.status_code in (401, 403):
            return True
        business = self._business_failure(response)
        return business is not None and business[0] == AUTH

    #: Oturum düştüğünde çağrı tekrarlanabilir mi? ``submit`` için HAYIR:
    #: yeniden göndermek çift belge riski taşır (spec §7). Sadece idempotent
    #: yollar tekrarlanır; ``submit`` yalnız önbelleği temizler ve hatayı bildirir.
    _RELOGIN_REPEATABLE: frozenset[str] = frozenset({"query_status", "fetch_pdf", "check_taxpayer"})

    def _call_with_session(
        self, operation: str, *, retryable: bool, **kwargs: Any
    ) -> HttpResponse:
        """Oturum al → çağır. AUTH gelirse **bir kez** yeniden giriş, **bir kez** tekrar.

        Sonsuz döngü yok: ikinci AUTH yanıtı olduğu gibi döner ve çağıran onu
        normal hata yolundan işler. Sayaç değil, tek bir ``if`` — döngü olmadığı
        için kaçacak bir yol da yok.
        """
        runner = self._retryable if retryable else call_once
        session = self._session_token()
        response = runner(lambda: self._call(operation, session, **kwargs))
        if not self._is_auth_failure(response):
            return response
        # Oturum düşmüş: önbelleği temizle. Çağrıyı tekrarlamak yalnız
        # idempotent operasyonlarda güvenli.
        fresh = self._session_token(force_refresh=True)
        if operation not in self._RELOGIN_REPEATABLE:
            return response
        return runner(lambda: self._call(operation, fresh, **kwargs))

    def _retryable(self, operation: Callable[[], HttpResponse]) -> HttpResponse:
        """Idempotent calls only (spec §7): 3 attempts, 1s/2s/4s backoff."""
        return with_retry(operation)

    # --- EInvoiceProvider --------------------------------------------------
    def submit(self, payload: dict[str, Any]) -> EInvoiceResult:
        """Send one envelope. Never retried automatically (spec §7: double-send risk)."""
        payload = payload or {}
        client_ettn = payload.get("uuid")
        channel = payload.get("channel")

        missing = missing_required_fields(payload)
        if missing:
            return self._fail("submit", VALIDATION, field_name=", ".join(missing), uuid=client_ettn)
        if not self._configured():
            return self._fail(
                "submit",
                UNKNOWN,
                message=self._unconfigured_message(),
                uuid=client_ettn,
                channel=channel,
            )
        blocked = self._submit_gate(payload)
        if blocked is not None:
            # Sağlayıcıya özgü ek kapı (ör. bir kanalın gönderimi henüz
            # doğrulanmadı). Uç yapılandırması kadar temel değil, o yüzden
            # sonra bakılır; ama oturum açmadan önce, çünkü çağrı yapılmayacak.
            return self._fail(
                "submit", UNKNOWN, message=blocked, uuid=client_ettn, channel=channel
            )
        try:
            # ``retryable=False``: gönderim asla otomatik tekrarlanmaz (spec §7).
            # Oturum düşerse önbellek temizlenir ama çağrı YENİDEN GÖNDERİLMEZ —
            # çift belge riski, raporlanan bir hatadan kötüdür.
            response = self._call_with_session(
                "submit", retryable=False, payload=payload, channel=channel
            )
        except EInvoiceError as exc:
            # Giriş yapılamadı ya da gövde kurulamadı (ör. UBL üretilemedi).
            return self._fail(
                "submit", exc.code, message=exc.message, raw=exc.raw, uuid=client_ettn, channel=channel
            )
        except TransportError:
            # No automatic retry: a resend must be an explicit decision, and it
            # carries the same client ETTN so the provider rejects the duplicate.
            return self._fail("submit", NETWORK, uuid=client_ettn, channel=channel)

        raw = self._summary(response)
        if not response.ok:
            code = classify_http(response.status_code, self._body_text(response))
            return self._fail(
                "submit",
                code,
                field_name=self._extract(response, self._field("reason")),
                provider_code=response.status_code,
                raw=raw,
                uuid=client_ettn,
                channel=channel,
            )

        business = self._business_failure(response)
        if business is not None:
            # 2xx ama sağlayıcı belgeyi reddetmiş. ``ok`` bayrağına güvenip
            # devam etmek, reddedilen bir belge için "gönderildi" demektir.
            code, message = business
            return self._fail(
                "submit", code, message=message, raw=raw, uuid=client_ettn, channel=channel
            )

        reported = map_provider_status(
            self._extract(response, self._field("status")), self._status_aliases
        )
        external_id = (self._extract(response, self._field("ettn")) or "").strip() or None
        if reported == REJECTED and external_id is not None:
            # REJECTED is GİB's verdict on a document that HAS an ETTN; it is a
            # terminal state of a real envelope.
            return EInvoiceResult(
                status=REJECTED,
                channel=channel,
                uuid=client_ettn,
                external_id=external_id,
                error=self._rejection_reason(response),
                raw=raw,
            )
        if reported == REJECTED:
            # Rejected *at submission*, with no ETTN issued: nothing reached GİB,
            # so this is a failed send (spec §5), not a GİB rejection. Marking it
            # REJECTED would claim a terminal verdict on a document that does not
            # exist at the provider — and would block a legitimate resend.
            return self._fail(
                "submit",
                UNKNOWN,
                message=self._rejection_reason(response),
                raw=raw,
                uuid=client_ettn,
                channel=channel,
            )
        if external_id is None:
            # 2xx but no ETTN. Inventing one here is the exact failure mode this
            # adapter exists to prevent.
            return self._fail(
                "submit",
                UNKNOWN,
                provider_code="ETTN_YOK",
                raw=raw,
                uuid=client_ettn,
                channel=channel,
            )
        return EInvoiceResult(
            status=advance_status(NONE, PENDING),
            channel=channel,
            uuid=client_ettn,
            external_id=external_id,
            raw=raw,
        )

    def query_status(
        self, external_id: str, *, channel: str | None = None, uuid: str | None = None
    ) -> EInvoiceResult:
        """Poll one envelope. ``ACCEPTED`` only ever comes from the provider's answer."""
        ettn = str(external_id or "").strip()
        if not ettn:
            return self._fail("query_status", VALIDATION, field_name="ETTN")
        if not self._configured():
            return self._unresolved(
                "query_status", UNKNOWN, message=self._unconfigured_message(), external_id=ettn
            )
        blocked = self._query_precondition(ettn, channel=channel, uuid=uuid)
        if blocked is not None:
            # Sorgu KURULAMIYOR. Bunu login açtıktan sonra fark etmek, boşuna
            # bir oturum açmak demekti; kontrol ağdan önce.
            return self._unresolved(
                "query_status", VALIDATION, message=blocked, external_id=ettn
            )

        try:
            response = self._call_with_session(
                "query_status", retryable=True, ettn=ettn, channel=channel, uuid=uuid
            )
        except EInvoiceError as exc:
            return self._unresolved(
                "query_status", exc.code, message=exc.message, raw=exc.raw, external_id=ettn
            )
        except TransportError:
            return self._unresolved("query_status", NETWORK, external_id=ettn)

        raw = self._summary(response)
        if not response.ok:
            code = classify_http(response.status_code, self._body_text(response))
            return self._unresolved(
                "query_status", code, provider_code=response.status_code, raw=raw, external_id=ettn
            )

        business = self._business_failure(response)
        if business is not None:
            # 2xx ama sorgu iş düzeyinde başarısız. ``UNRESOLVED``: zarf hâlâ
            # sağlayıcıda, eksik olan yalnız bizim bilgimiz (spec §5).
            code, message = business
            return self._unresolved(
                "query_status", code, message=message, raw=raw, external_id=ettn
            )

        token = self._extract(response, self._field("status"))
        reported = map_provider_status(token, self._status_aliases)
        if reported not in QUERYABLE:
            # Two cases, one answer. Either the provider said nothing we
            # understand (empty/unmapped — explicitly NOT acceptance), or it
            # reported a state that cannot describe the envelope we just asked
            # about: ``FAILED`` ("the send never landed, there is no ETTN") and
            # ``NONE`` ("never sent") both deny the very ETTN carried by this
            # call. Passing either through would rebuild the exact
            # ``FAILED + external_id`` contradiction the error paths above were
            # changed to avoid — the provider's wording must not be able to
            # smuggle a submit-time terminal into a query result.
            return self._unresolved(
                "query_status",
                UNKNOWN,
                provider_code=token or "BOS_YANIT",
                raw=raw,
                external_id=ettn,
            )
        return EInvoiceResult(
            status=reported,
            external_id=ettn,
            error=self._rejection_reason(response) if reported == REJECTED else None,
            raw=raw,
        )

    def fetch_pdf(self, external_id: str, *, channel: str | None = None) -> bytes:
        """Return the provider PDF, or raise — empty ``bytes`` would read as success."""
        ettn = str(external_id or "").strip()
        if not ettn:
            raise self._raise("fetch_pdf", VALIDATION, provider_code="ETTN_YOK")
        if not self._configured():
            raise EInvoiceError(UNKNOWN, self._unconfigured_message())
        try:
            response = self._call_with_session(
                "fetch_pdf", retryable=True, ettn=ettn, channel=channel
            )
        except TransportError as exc:
            raise self._raise("fetch_pdf", NETWORK) from exc
        if not response.ok:
            code = classify_http(response.status_code, self._body_text(response))
            raise self._raise(
                "fetch_pdf", code, provider_code=response.status_code, raw=self._summary(response)
            )
        business = self._business_failure(response)
        if business is not None:
            code, message = business
            raise EInvoiceError(code, scrub(message, self._secrets), raw=self._summary(response))
        content = self._extract_pdf(response)
        if not content:
            raise self._raise("fetch_pdf", UNKNOWN, provider_code="PDF_YOK")
        return content

    def check_taxpayer(self, vkn: str) -> dict[str, bool]:
        """GİB registration lookup. Raises when unanswerable — never defaults to True."""
        number = str(vkn or "").strip()
        if not number:
            raise self._raise("check_taxpayer", VALIDATION, provider_code="VKN_YOK")
        if not self._configured():
            raise EInvoiceError(UNKNOWN, self._unconfigured_message())
        try:
            response = self._call_with_session("check_taxpayer", retryable=True, vkn=number)
        except TransportError as exc:
            raise self._raise("check_taxpayer", NETWORK) from exc
        if not response.ok:
            code = classify_http(response.status_code, self._body_text(response))
            raise self._raise(
                "check_taxpayer", code, provider_code=response.status_code, raw=self._summary(response)
            )
        business = self._business_failure(response)
        if business is not None:
            code, message = business
            raise EInvoiceError(code, scrub(message, self._secrets), raw=self._summary(response))
        answer = _as_bool(self._extract(response, self._field("taxpayer")))
        if answer is None:
            # Unreadable answer: raise instead of routing the invoice to EFATURA
            # (or EARSIV) on a guess.
            raise self._raise(
                "check_taxpayer",
                UNKNOWN,
                provider_code="MUKELLEF_YANITI_OKUNAMADI",
                raw=self._summary(response),
            )
        return {"is_efatura_user": answer}

    def _rejection_reason(self, response: HttpResponse) -> str:
        """GİB's own rejection text (spec §5: REJECTED carries the reason)."""
        reason = self._extract(response, self._field("reason"))
        text = str(reason).strip() if reason else ""
        return scrub(text[:500] or message_for(UNKNOWN, provider_code="RED"), self._secrets)


def _as_bool(value: Any) -> bool | None:
    """Strict truthiness for a provider flag; ``None`` when it cannot be read."""
    if value is None:
        return None
    token = str(value).strip().upper()
    if token in ("TRUE", "1", "YES", "Y", "EVET", "E", "REGISTERED", "KAYITLI"):
        return True
    if token in ("FALSE", "0", "NO", "N", "HAYIR", "H", "UNREGISTERED", "KAYITSIZ"):
        return False
    return None


def _decode_pdf(raw: bytes) -> bytes:
    """Accept either a raw PDF body or a base64 field; reject anything else."""
    if raw[:5] == b"%PDF-":
        return raw
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return b""
    return decoded if decoded[:5] == b"%PDF-" else b""


# --------------------------------------------------------------------------
# İzibiz — SOAP / WS (WSDL'den doğrulandı, sandbox'ta çalıştırıldı)
# --------------------------------------------------------------------------
class IzibizEInvoiceProvider(_HttpEInvoiceProvider):
    """İzibiz SOAP adaptörü — üç servis, tek oturum.

    Tel detayları :mod:`app.einvoice.endpoints` içinde ve artık tahmin değil:
    resmî WSDL'den okundu, ``backend/sandbox/izibiz_smoke.py`` ile test
    ortamında çalıştırıldı. Bu sınıfın bilmesi gereken, diğer sağlayıcılardan
    ayrıştığı dört nokta:

    1. **Üç ayrı servis, tek ``SESSION_ID``.** Kimlik (``AuthenticationWS``),
       e-Fatura (``EInvoiceWS``) ve e-Arşiv (``EIArchiveWS``) farklı adreslerde;
       hangisine gidileceğini operasyon belirler
       (:func:`~app.einvoice.endpoints.izibiz_service_url`).
    2. **İş hatası HTTP 200 gövdesinde gelir.** ``response.ok`` yeterli değil;
       bkz. :meth:`_business_failure`.
    3. **Gönderim yanıtı ETTN taşımaz.** ``INVOICE_ID`` döner, ``UUID``
       dönmez — ``external_id`` bu yüzden sağlayıcı belge kimliğidir.
    4. **Mükellefiyet boolean değil, liste uzunluğudur.** ``CheckUser`` etiket
       listesi döner; bkz. :meth:`check_taxpayer`.
    """

    name = "izibiz"
    _wire_verified = wire.IZIBIZ_ENDPOINTS_VERIFIED
    _status_aliases = wire.IZIBIZ_STATUS_ALIASES

    _FIELDS = {
        "session": wire.IZIBIZ_FIELD_SESSION,
        "ettn": wire.IZIBIZ_FIELD_ETTN,
        "status": wire.IZIBIZ_FIELD_STATUS,
        "reason": wire.IZIBIZ_FIELD_REASON,
        "error_code": wire.IZIBIZ_FIELD_ERROR_CODE,
        "pdf": wire.IZIBIZ_FIELD_PDF,
    }

    # --- adresleme ---------------------------------------------------------
    def _endpoint(self) -> str:
        """Taban adres. Servis yolları :meth:`_service_url` ile eklenir."""
        return wire.resolve_endpoint(self._setting("einvoice_base_url"), wire.IZIBIZ_BASE_URL)

    def _service_url(self, operation: str) -> str:
        return wire.izibiz_service_url(self._endpoint(), operation)

    def _token_ttl(self) -> int:
        """8 saatlik TTL, son dakikaları güvenlik payı olarak kesilerek.

        Payı burada düşmek, "jeton geçerli" kontrolünün uzun bir gönderimin
        ortasında yanlış çıkmasını engeller: adaptör oturumu sona ermeden
        yeniler, sağlayıcının 'oturum yok' demesini beklemez.
        """
        return max(0, wire.IZIBIZ_TOKEN_TTL_SECONDS - wire.IZIBIZ_TOKEN_REFRESH_MARGIN_SECONDS)

    def _field(self, name: str) -> tuple[str, ...]:
        return self._FIELDS[name]

    # --- taşıma ------------------------------------------------------------
    def _environment(self) -> str:
        """``IZIBIZ_ENV`` — her çağrıda yeniden okunur, önbelleğe alınmaz."""
        return wire.izibiz_environment(self._setting("izibiz_env"))

    def is_configured(self) -> bool:
        """URL/tel kapılarına ek olarak **ortam kilidi de** okunabilir olmalı.

        Geçersiz bir ``IZIBIZ_ENV`` ile hiçbir çağrı yapılamaz (:meth:`_guard`),
        dolayısıyla o yapılandırma "hazır" değildir. Sinyalin bunu görmemesi,
        frontend'in butonu açık bırakıp her denemede hata alması demekti.
        """
        try:
            environment = self._environment()
        except wire.IzibizEnvironmentError:
            return False
        return (
            super().is_configured()
            and wire.izibiz_endpoint_violation(self._endpoint(), environment) is None
        )

    def configuration_error(self) -> str | None:
        try:
            environment = self._environment()
        except wire.IzibizEnvironmentError as exc:
            return str(exc)
        violation = wire.izibiz_endpoint_violation(self._endpoint(), environment)
        if violation is not None:
            return violation
        return super().configuration_error()

    def _guard(self, url: str) -> None:
        """Ortam kilidi. **Her ağ çağrısının hemen öncesinde** çalışır.

        Sınıf kurulumunda değil, tam da burada: ``IZIBIZ_ENV`` bir adaptör
        örneği yaşarken değişebilir (yeniden yapılandırma, test, uzun ömürlü
        worker) ve tek seferlik bir kontrol o değişikliği kaçırır. Kontrolü
        soketin bir satır öncesine koymak, atlanabilecek bir yol bırakmaz.

        Geçersiz ``IZIBIZ_ENV`` de buradan patlar: ortam okunamıyorsa çağrı
        yapılmaz — "hangi ortamdayım bilmiyorum ama göndereyim" yok.
        """
        try:
            environment = self._environment()
        except wire.IzibizEnvironmentError as exc:
            # Ortam okunamadı ⇒ hiçbir yere gidilmez. ``EInvoiceError``'a
            # çevriliyor ki dört giriş noktasının dördü de kendi fail-closed
            # yolunu kullansın (FAILED / UNRESOLVED / raise) — çağıranın
            # yakalamadığı ham bir ``ValueError`` değil.
            raise EInvoiceError(UNKNOWN, str(exc)) from None
        violation = wire.izibiz_endpoint_violation(url, environment)
        if violation is not None:
            # Sınıf AUTH/NETWORK değil: bu bir politika ihlali, geçici bir arıza
            # değil. Tekrar denemek düzeltmez, o yüzden retry'ye de girmez.
            logger.warning("e-Fatura izibiz/ortam kilidi devrede (sınıf=%s)", UNKNOWN)
            raise EInvoiceError(UNKNOWN, scrub(violation, self._secrets))

    def _post(
        self, operation: str, body: bytes, *, max_bytes: int = wire.MAX_PARSED_RESPONSE_BYTES
    ) -> HttpResponse:
        url = self._service_url(operation)
        self._guard(url)
        return self._transport.request(
            "POST",
            url,
            body=body,
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                # WSDL binding'i soapAction="" diyor: başlık var, içeriği boş.
                "SOAPAction": f'"{wire.IZIBIZ_SOAPACTION}"',
                "Accept": "text/xml",
                "Content-Length": str(len(body)),
            },
            max_bytes=max_bytes,
        )

    def _authenticate(self) -> str:
        # Şema REQUEST_HEADER/SESSION_ID'yi zorunlu tutuyor ama login anında
        # henüz oturum yok; yer tutucu gönderilir.
        body = _soap_envelope(
            wire.IZIBIZ_OP_LOGIN,
            _request_header("-1")
            + f"<USER_NAME>{xml_escape(self._username)}</USER_NAME>"
            + f"<PASSWORD>{xml_escape(self._password)}</PASSWORD>",
        )
        try:
            response = self._post(wire.IZIBIZ_OP_LOGIN, body)
        except TransportError as exc:
            raise self._raise("login", NETWORK) from exc
        if not response.ok:
            raise self._raise(
                "login",
                classify_http(response.status_code, self._body_text(response)),
                provider_code=response.status_code,
            )
        business = self._business_failure(response)
        if business is not None:
            # Giriş reddi de 200 gövdesinde gelebilir.
            code, message = business
            raise EInvoiceError(code, scrub(message, self._secrets))
        session = self._extract(response, self._FIELDS["session"])
        if not session:
            raise self._raise("login", AUTH)
        return session

    # --- istek gövdeleri ---------------------------------------------------
    def _call(self, operation: str, session: str, **kwargs: Any) -> HttpResponse:
        channel = kwargs.get("channel")
        earsiv = channel == CHANNEL_EARSIV

        if operation == "submit":
            payload = kwargs["payload"]
            try:
                package = ubl_xml.package_invoice(payload)
            except ubl_xml.UblBuildError as exc:
                # Eksik/uyumsuz alan: ağa çıkmadan, gürültülü biçimde dur.
                raise self._raise("submit", VALIDATION, provider_code="UBL_URETILEMEDI") from exc
            content = base64.b64encode(package).decode("ascii")
            if earsiv:
                return self._post(
                    wire.IZIBIZ_OP_SUBMIT_EARCHIVE,
                    _soap_envelope(
                        wire.IZIBIZ_OP_SUBMIT_EARCHIVE,
                        _request_header(session) + _earchive_write_content(content),
                    ),
                )
            return self._post(
                wire.IZIBIZ_OP_SUBMIT,
                _soap_envelope(
                    wire.IZIBIZ_OP_SUBMIT,
                    _request_header(session)
                    + f'<INVOICE ID="{xml_escape(str(payload.get("invoice_number") or ""))}"'
                    f' UUID="{xml_escape(str(payload.get("uuid") or ""))}">'
                    f"<CONTENT>{content}</CONTENT></INVOICE>",
                ),
            )

        if operation == "query_status":
            if earsiv:
                ettn = str(kwargs.get("uuid") or "").strip()
                if not ettn:
                    # e-Arşiv durum sorgusu YALNIZ UUID kabul eder. Sağlayıcı
                    # belge kimliğini UUID yerine göndermek boş bir yanıt üretir
                    # ve bu "belge yok" gibi okunur — sessiz yanlış yerine
                    # gürültülü hata.
                    raise self._raise("query_status", VALIDATION, provider_code="ETTN_GEREKLI")
                return self._post(
                    wire.IZIBIZ_OP_STATUS_EARCHIVE,
                    _soap_envelope(
                        wire.IZIBIZ_OP_STATUS_EARCHIVE,
                        _request_header(session) + f"<UUID>{xml_escape(ettn)}</UUID>",
                    ),
                )
            return self._post(
                wire.IZIBIZ_OP_STATUS,
                _soap_envelope(
                    wire.IZIBIZ_OP_STATUS,
                    _request_header(session) + _invoice_key(kwargs.get("uuid"), kwargs["ettn"]),
                ),
            )

        if operation == "fetch_pdf":
            if earsiv:
                # ``GetEArchiveInvoice`` UUID değil WEB_VALIDATION_KEY ister.
                # Bu anahtar gönderim yanıtında (``WEB_KEY``) geliyor ama
                # bugün saklanmıyor → açık boşluk, sessizce yanlış çağrı yerine
                # gürültülü hata.
                raise self._raise("fetch_pdf", UNKNOWN, provider_code="EARSIV_WEB_KEY_YOK")
            search = (
                f"<UUID>{xml_escape(str(kwargs['ettn']))}</UUID>"
                if _looks_like_uuid(kwargs["ettn"])
                else f"<ID>{xml_escape(str(kwargs['ettn']))}</ID>"
            )
            return self._post(
                wire.IZIBIZ_OP_PDF,
                _soap_envelope(
                    wire.IZIBIZ_OP_PDF,
                    _request_header(session)
                    + f"<INVOICE_SEARCH_KEY><LIMIT>1</LIMIT>{search}<TYPE>PDF</TYPE>"
                    "</INVOICE_SEARCH_KEY><HEADER_ONLY>N</HEADER_ONLY>",
                ),
                max_bytes=wire.MAX_BINARY_RESPONSE_BYTES,
            )

        # check_taxpayer
        return self._post(
            wire.IZIBIZ_OP_TAXPAYER,
            _soap_envelope(
                wire.IZIBIZ_OP_TAXPAYER,
                _request_header(session)
                + f"<USER><IDENTIFIER>{xml_escape(str(kwargs['vkn']))}</IDENTIFIER></USER>"
                "<DOCUMENT_TYPE>INVOICE</DOCUMENT_TYPE>",
            ),
        )

    # --- yanıt okuma -------------------------------------------------------
    def _extract(self, response: HttpResponse, keys: tuple[str, ...]) -> str | None:
        return _first_xml_field(response.body[: wire.MAX_PARSED_RESPONSE_BYTES], keys)

    def _extract_pdf(self, response: HttpResponse) -> bytes:
        if response.body[:5] == b"%PDF-":
            return response.body
        encoded = _first_xml_field(response.body[: wire.MAX_BINARY_RESPONSE_BYTES], self._FIELDS["pdf"])
        return _decode_pdf(encoded.encode("utf-8")) if encoded else b""

    def _business_failure(self, response: HttpResponse) -> tuple[str, str] | None:
        """``ERROR_TYPE`` / sıfırdan farklı ``RETURN_CODE`` → iş hatası.

        Bu, sınıfın en önemli metodu. İzibiz belge reddini **HTTP 200** ile
        döner (sandbox fixture'ı:
        ``backend/tests/fixtures/izibiz/WriteToArchiveExtended-fault.200.xml``).
        Bu ayrıştırma kaldırılırsa ``ERROR_CODE=10007 "Zip bir dosya
        içermelidir."`` diyen bir yanıt başarı sayılır — reddedilmiş bir belge
        için "gönderildi" denir. Testler bunu mutasyonla kanıtlıyor.

        **Fail-closed, üç aşamada.** "Okuyamadım" cevabı "sorun yok" cevabı
        değildir; okunamayan her 2xx gövdesi hata sayılır:

        * gövde boş ⇒ hata (``BOS_YANIT``)
        * gövde XML olarak ayrıştırılamıyor ⇒ hata (``YANIT_AYRISTIRILAMADI``)
        * ayrıştırıldı ama İzibiz yanıt zarfı yok ⇒ hata
          (``BEKLENMEYEN_YANIT``) — bu gövde bir proxy hata sayfası, kesilmiş
          bir yanıt veya yanlış bir uç olabilir; içinde ``ERROR_TYPE``
          bulunmaması "işlem başarılı" demek değildir.

        Üçünün sınıfı da ``UNKNOWN``, kasıtlı olarak ``NETWORK`` değil: ``NETWORK``
        idempotent yollarda yeniden denemeyi tetikler, oysa okunamayan bir gövde
        tekrar sorulmakla okunur hâle gelmez.
        """
        body = response.body[: wire.MAX_PARSED_RESPONSE_BYTES]
        if not body.strip():
            return UNKNOWN, message_for(UNKNOWN, provider_code="BOS_YANIT")
        root = _parse_xml(body)
        if root is None:
            return UNKNOWN, message_for(UNKNOWN, provider_code="YANIT_AYRISTIRILAMADI")
        error = _first_xml_element(root, wire.IZIBIZ_FIELD_ERROR_ENVELOPE)
        if error is not None:
            code = _child_text(error, "ERROR_CODE") or ""
            short = _child_text(error, "ERROR_SHORT_DES") or ""
            long_description = _child_text(error, "ERROR_LONG_DES") or ""
            error_class = wire.IZIBIZ_ERROR_CODE_CLASSES.get(code.strip())
            if error_class is None:
                error_class = classify_body(f"{short} {long_description}") or UNKNOWN
            detail = (short or long_description).strip()
            message = message_for(error_class, field=detail, provider_code=code or "ISLEM_HATASI")
            return error_class, message
        if not _has_response_envelope(root):
            # ``ERROR_TYPE`` yok ama bu gövde bir İzibiz yanıtı da değil.
            # Sessizce "başarılı" saymak, yanlış uca gitmiş bir çağrıyı
            # gönderilmiş bir belge gibi göstermek olurdu.
            return UNKNOWN, message_for(UNKNOWN, provider_code="BEKLENMEYEN_YANIT")
        return_code = _first_xml_field(body, (wire.IZIBIZ_FIELD_RETURN_CODE,))
        if return_code is not None and return_code.strip() not in ("", "0"):
            return UNKNOWN, message_for(UNKNOWN, provider_code=return_code.strip())
        return None

    # --- kanal / mükellefiyet ---------------------------------------------
    def _submit_gate(self, payload: dict[str, Any]) -> str | None:
        """e-Arşiv açık, e-Fatura kapalı — :data:`IZIBIZ_EFATURA_SUBMIT_VERIFIED`.

        e-Fatura gönderimi sandbox'ta hiç denenmedi. İstek gövdesi WSDL'e uygun
        kurulur ama doğrulanmamış bir gönderim CANLI BİR BELGE üretebilir; okuma
        yollarının aksine burada hata geri alınamaz. Bu yüzden ayrı bir kapı.
        """
        if (payload or {}).get("channel") == CHANNEL_EFATURA and not wire.IZIBIZ_EFATURA_SUBMIT_VERIFIED:
            return wire.IZIBIZ_EFATURA_SUBMIT_ERROR
        return None

    def _query_precondition(
        self, external_id: str, *, channel: str | None, uuid: str | None
    ) -> str | None:
        """e-Arşiv durum sorgusu YALNIZ UUID kabul eder.

        ``GetEArchiveInvoiceStatus`` şemasında tek anahtar ``UUID``; sağlayıcı
        belge kimliğini oraya yazmak boş bir yanıt üretir ve bu "belge yok" gibi
        okunur. ETTN yoksa sorgu kurulamaz — ve bunu **login açmadan** söylemek
        gerekir, yoksa her başarısız sorgu sağlayıcıda bir oturum daha açar.
        """
        if channel == CHANNEL_EARSIV and not str(uuid or "").strip():
            return wire.IZIBIZ_EARCHIVE_STATUS_NEEDS_ETTN
        return None

    def check_taxpayer(self, vkn: str) -> dict[str, bool]:
        """Mükellefiyet = ``USER`` etiket SAYISI. Boolean bir alan yok.

        ``CheckUser`` kayıtlı alıcı için bir veya daha çok ``USER`` (GİB etiketi)
        döner, kayıtsız için hiç döndürmez. Bunu "alan bulunamadı → bilinmiyor"
        diye okumak, kayıtsız her alıcıyı çözümsüz bırakırdı; "0 etiket" gerçek
        ve kesin bir cevaptır: **bu alıcı e-Fatura mükellefi değil**.

        Ancak sıfır etiketi cevap saymak, ancak yanıtın GERÇEKTEN bir
        ``CheckUserResponse`` olduğu doğrulanırsa güvenlidir — boş/bozuk bir
        gövde de sıfır etiket "gösterir" ve o bir cevap değildir.
        """
        number = str(vkn or "").strip()
        if not number:
            raise self._raise("check_taxpayer", VALIDATION, provider_code="VKN_YOK")
        if not self._configured():
            raise EInvoiceError(UNKNOWN, self._unconfigured_message())
        try:
            response = self._call_with_session("check_taxpayer", retryable=True, vkn=number)
        except TransportError as exc:
            raise self._raise("check_taxpayer", NETWORK) from exc
        if not response.ok:
            code = classify_http(response.status_code, self._body_text(response))
            raise self._raise(
                "check_taxpayer", code, provider_code=response.status_code, raw=self._summary(response)
            )
        business = self._business_failure(response)
        if business is not None:
            code, message = business
            raise EInvoiceError(code, scrub(message, self._secrets), raw=self._summary(response))

        root = _parse_xml(response.body[: wire.MAX_PARSED_RESPONSE_BYTES])
        envelope = (
            _first_xml_element(root, wire.IZIBIZ_FIELD_TAXPAYER_RESPONSE) if root is not None else None
        )
        if envelope is None:
            # Beklenen yanıt elemanı yok: bu "0 etiket" değil, "cevap yok".
            raise self._raise(
                "check_taxpayer",
                UNKNOWN,
                provider_code="MUKELLEF_YANITI_OKUNAMADI",
                raw=self._summary(response),
            )
        tags = [
            element
            for element in envelope.iter()
            if _local_name(element.tag) == wire.IZIBIZ_FIELD_TAXPAYER_ELEMENT
        ]
        return {"is_efatura_user": bool(tags)}


def _request_header(session: str) -> str:
    """Her İzibiz isteğinin ilk elemanı. Oturum gövdede taşınır, başlıkta değil."""
    return (
        "<REQUEST_HEADER>"
        f"<SESSION_ID>{xml_escape(str(session))}</SESSION_ID>"
        "<APPLICATION_NAME>SungurTarimERP</APPLICATION_NAME>"
        "<CHANNEL_NAME>WS</CHANNEL_NAME>"
        "</REQUEST_HEADER>"
    )


def _invoice_key(uuid_value: Any, fallback_id: Any) -> str:
    """``GetInvoiceStatus`` anahtarı: ETTN varsa UUID, yoksa belge kimliği.

    İkisi de ÖZNİTELİK olarak taşınır (şemada ``INVOICE`` elemanının ``ID`` /
    ``UUID`` attribute'ları), alt eleman olarak değil.
    """
    ettn = str(uuid_value or "").strip()
    if ettn:
        return f'<INVOICE UUID="{xml_escape(ettn)}"/>'
    return f'<INVOICE ID="{xml_escape(str(fallback_id or ""))}"/>'


def _earchive_write_content(content_b64: str) -> str:
    """``WriteToArchiveExtended`` gövdesi — e-Arşiv özellikleri + ZIP içerik."""
    return (
        "<ArchiveInvoiceExtendedContent><INVOICE_PROPERTIES>"
        "<EARSIV_FLAG>Y</EARSIV_FLAG>"
        "<EARSIV_PROPERTIES>"
        f"<EARSIV_TYPE>{wire.IZIBIZ_EARSIV_TYPE_NORMAL}</EARSIV_TYPE>"
        "<EARSIV_EMAIL_FLAG>N</EARSIV_EMAIL_FLAG>"
        f"<SUB_STATUS>{wire.IZIBIZ_SUB_STATUS_NEW}</SUB_STATUS>"
        "<VALIDATION_FLAG>Y</VALIDATION_FLAG>"
        "</EARSIV_PROPERTIES>"
        "<PDF_PROPERTIES><EARSIV_PDF_FLAG>N</EARSIV_PDF_FLAG></PDF_PROPERTIES>"
        f"<INVOICE_CONTENT>{content_b64}</INVOICE_CONTENT>"
        "</INVOICE_PROPERTIES></ArchiveInvoiceExtendedContent>"
    )


def _soap_envelope(operation: str, inner: str) -> bytes:
    """SOAP 1.1 document/literal zarfı.

    İki ayrıntı WSDL'den geliyor ve ikisi de eski tahminden farklı: kök eleman
    operasyon adı değil ``<Ad>Request`` (tablo:
    :data:`~app.einvoice.endpoints.IZIBIZ_REQUEST_ELEMENT`), ve alt elemanlar
    **niteliksizdir** — şemada ``elementFormDefault`` yok. Bu yüzden kök
    öneklenir; varsayılan ad alanı verilseydi çocuklar da o ad alanına düşerdi
    ve sunucu isteği reddederdi.
    """
    element = wire.IZIBIZ_REQUEST_ELEMENT[operation]
    namespace = wire.izibiz_namespace(operation)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soapenv:Header/><soapenv:Body>"
        f'<izb:{element} xmlns:izb="{namespace}">{inner}</izb:{element}>'
        "</soapenv:Body></soapenv:Envelope>"
    ).encode("utf-8")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _looks_like_uuid(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) == 36 and text.count("-") == 4


def _parse_xml(body: bytes) -> ElementTree.Element | None:
    if not body:
        return None
    try:
        return ElementTree.fromstring(body.decode("utf-8", errors="replace"))
    except ElementTree.ParseError:
        return None


def _has_response_envelope(root: ElementTree.Element) -> bool:
    """Bu gövde gerçekten bir İzibiz yanıtı mı?

    Her operasyonun yanıtı ``…Response`` ile biten bir eleman taşır. Yoksa
    elimizdeki şey bir proxy hata sayfası, kesilmiş bir gövde ya da yanlış bir
    uçtan gelen bir cevaptır — ve ``ERROR_TYPE`` içermemesi onu başarılı yapmaz.
    """
    suffix = wire.IZIBIZ_RESPONSE_ELEMENT_SUFFIX
    for element in root.iter():
        if _local_name(element.tag).endswith(suffix):
            return True
    return False


def _first_xml_element(root: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for element in root.iter():
        if _local_name(element.tag) == name:
            return element
    return None


def _child_text(element: ElementTree.Element, name: str) -> str | None:
    for child in element.iter():
        if _local_name(child.tag) == name:
            text = (child.text or "").strip()
            if text:
                return text
    return None


def _first_xml_field(body: bytes, keys: tuple[str, ...]) -> str | None:
    """First non-empty value among ``keys``, matched on the XML local name."""
    root = _parse_xml(body)
    if root is None:
        return None
    found: dict[str, str] = {}
    for element in root.iter():
        local = _local_name(element.tag).lower()
        text = (element.text or "").strip()
        if text and local not in found:
            found[local] = text
    for key in keys:
        value = found.get(key.lower())
        if value:
            return value
    return None


# --------------------------------------------------------------------------
# Nes — REST / JSON
# --------------------------------------------------------------------------
class NesEInvoiceProvider(_HttpEInvoiceProvider):
    """REST adapter. Base URL and paths come from ‹doğrulanacak› constants.

    Not implemented in this increment: spec §2's "on 401, re-login once and retry
    the call once". Today a 401 maps to the AUTH message and stops. Adding it
    before the token TTL and the real 401 body shape are known would be guessing
    at when a session actually expires; it is deferred to the increment that
    verifies the endpoints.
    """

    name = "nes"
    _wire_verified = wire.NES_ENDPOINTS_VERIFIED

    _FIELDS = {
        "session": wire.NES_FIELD_TOKEN,
        "ettn": wire.NES_FIELD_ETTN,
        "status": wire.NES_FIELD_STATUS,
        "reason": wire.NES_FIELD_REASON,
        "error_code": wire.NES_FIELD_ERROR_CODE,
        "pdf": wire.NES_FIELD_PDF,
        "taxpayer": wire.NES_FIELD_TAXPAYER,
    }

    def _endpoint(self) -> str:
        return wire.resolve_endpoint(self._setting("einvoice_base_url"), wire.NES_BASE_URL)

    def _token_ttl(self) -> int:
        return wire.NES_TOKEN_TTL_SECONDS

    def _field(self, name: str) -> tuple[str, ...]:
        return self._FIELDS[name]

    def _headers(self, session: str | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json; charset=utf-8"}
        if session:
            headers["Authorization"] = f"Bearer {session}"
        api_key = self._setting("einvoice_api_key")
        if wire.NES_REQUIRES_API_KEY and api_key:
            headers[wire.NES_API_KEY_HEADER] = _secret_text(api_key)
        return headers

    def _authenticate(self) -> str:
        body = json.dumps({"username": self._username, "password": self._password}).encode("utf-8")
        try:
            response = self._transport.request(
                "POST",
                f"{self._endpoint()}{wire.NES_LOGIN_PATH}",
                body=body,
                headers=self._headers(),
            )
        except TransportError as exc:
            raise self._raise("login", NETWORK) from exc
        if not response.ok:
            raise self._raise(
                "login",
                classify_http(response.status_code, self._body_text(response)),
                provider_code=response.status_code,
            )
        token = self._extract(response, self._FIELDS["session"])
        if not token:
            raise self._raise("login", AUTH)
        return token

    def _call(self, operation: str, session: str, **kwargs: Any) -> HttpResponse:
        base = self._endpoint()
        headers = self._headers(session)
        if operation == "submit":
            payload = kwargs["payload"]
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            return self._transport.request(
                "POST", f"{base}{wire.NES_SUBMIT_PATH}", body=body, headers=headers
            )
        if operation == "query_status":
            path = wire.NES_STATUS_PATH.format(ettn=_quote(kwargs["ettn"]))
            return self._transport.request("GET", f"{base}{path}", headers=headers)
        if operation == "fetch_pdf":
            path = wire.NES_PDF_PATH.format(ettn=_quote(kwargs["ettn"]))
            return self._transport.request(
                "GET", f"{base}{path}", headers=headers, max_bytes=wire.MAX_BINARY_RESPONSE_BYTES
            )
        path = wire.NES_TAXPAYER_PATH.format(vkn=_quote(kwargs["vkn"]))
        return self._transport.request("GET", f"{base}{path}", headers=headers)

    def _extract(self, response: HttpResponse, keys: tuple[str, ...]) -> str | None:
        return _first_json_field(response.body[: wire.MAX_PARSED_RESPONSE_BYTES], keys)

    def _extract_pdf(self, response: HttpResponse) -> bytes:
        if response.body[:5] == b"%PDF-":
            return response.body
        encoded = _first_json_field(response.body[: wire.MAX_BINARY_RESPONSE_BYTES], self._FIELDS["pdf"])
        return _decode_pdf(encoded.encode("utf-8")) if encoded else b""


def _quote(value: Any) -> str:
    """Path-safe segment. Provider ids are opaque; nothing exotic may reach the URL."""
    return quote(str(value or ""), safe="")


def _first_json_field(body: bytes, keys: tuple[str, ...]) -> str | None:
    """First non-empty value among ``keys``, searched case-insensitively."""
    if not body:
        return None
    try:
        document = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError):
        return None
    flat: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    walk(value)
                elif value is not None and str(value).strip() and key.lower() not in flat:
                    flat[key.lower()] = str(value).strip()
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    for key in keys:
        value = flat.get(key.lower())
        if value:
            return value
    return None


_PROVIDERS: dict[str, type[EInvoiceProvider]] = {
    "izibiz": IzibizEInvoiceProvider,
    "nes": NesEInvoiceProvider,
}


def get_einvoice_provider(settings: Any, *, company_id: Any = None) -> EInvoiceProvider:
    """Resolve the configured provider.

    ``company_id`` oturum önbelleğinin anahtarına girer. Bugün kimlik bilgileri
    ``.env``'den global geliyor, yani iki kiracı zaten aynı kimliği kullanıyor;
    ama kimlikler kiracı başına DB'ye taşındığında anahtar hazır olmalı. İsteğe
    bağlı: vermeyen çağıran ``None`` alır ve tüm kiracıları tek kovada toplar —
    bugünkü global yapılandırmayla doğru, yarınki için değil.

    Falls back to the inert NoOp default unless a real provider is BOTH
    explicitly named AND credentialed. A half-configured provider (name set
    but ``einvoice_username`` / ``einvoice_password`` missing) resolves to
    NoOp rather than an adapter that would fail on first use, so a partial
    configuration can never break the internal invoice flow.

    This resolution contract is byte-for-byte the one hardened in #131; only
    what ``izibiz`` / ``nes`` resolve *to* changed (raising stub → real adapter).
    """
    name = (getattr(settings, "einvoice_provider", "noop") or "noop").strip().lower()
    if name == "noop":
        return NoOpEInvoiceProvider()
    factory = _PROVIDERS.get(name)
    if factory is None:
        return NoOpEInvoiceProvider()
    username = getattr(settings, "einvoice_username", None)
    password = getattr(settings, "einvoice_password", None)
    if not _secret_text(username) or not _secret_text(password):
        return NoOpEInvoiceProvider()
    return factory(settings, company_id=company_id)  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# Yapılandırma sinyali — "buton çizilsin mi?" sorusunun tek cevabı
# --------------------------------------------------------------------------
#: Yapılandırma eksikken operatöre söylenecek gerekçeler. Hiçbiri kimlik
#: bilgisi içermez; hepsi hangi DEĞİŞKENİN eksik olduğunu söyler.
EINVOICE_PROVIDER_UNSET = "EINVOICE_PROVIDER ayarlanmadı (varsayılan: noop)"
EINVOICE_PROVIDER_UNKNOWN = "EINVOICE_PROVIDER tanınmıyor: {name}"
EINVOICE_CREDENTIALS_MISSING = "EINVOICE_USERNAME ve EINVOICE_PASSWORD zorunlu"


@dataclass(frozen=True)
class EInvoiceConfiguration:
    """e-Fatura yapılandırmasının salt-okunur özeti.

    **Kimlik bilgisi taşımaz** ve taşımamalıdır: kullanıcı adı, parola, API
    anahtarı, taban URL — hiçbiri buraya girmez. Taşıdığı üç şey, bir arayüzün
    "fatura kes" butonunu çizip çizmeyeceğine karar vermesi için yeterlidir.

    ``reason`` yalnız ``configured`` False iken doludur ve son kullanıcıya
    değil operatöre/log'a yöneliktir.
    """

    configured: bool
    provider: str
    reason: str | None = None


def einvoice_configuration(settings: Any) -> EInvoiceConfiguration:
    """e-Fatura gönderimi bugün mümkün mü?

    Cevap **çözümlenmiş sağlayıcının kendisinden** okunur
    (:meth:`EInvoiceProvider.is_configured`), kuralların ikinci bir kopyasından
    değil: sinyalin "açık" deyip gönderimin ``FAILED`` dönmesi tam olarak bu
    ayrışmadan doğardı. ``reason`` sadece teşhistir ve kararı etkilemez.
    """
    name = (getattr(settings, "einvoice_provider", "noop") or "noop").strip().lower()
    provider = get_einvoice_provider(settings)
    if provider.is_configured():
        return EInvoiceConfiguration(configured=True, provider=name)
    return EInvoiceConfiguration(
        configured=False, provider=name, reason=_configuration_reason(settings, name, provider)
    )


def _configuration_reason(settings: Any, name: str, provider: EInvoiceProvider) -> str:
    """Hangi kapının kapalı olduğunu en spesifik biçimde söyle."""
    if name == "noop":
        return EINVOICE_PROVIDER_UNSET
    if name not in _PROVIDERS:
        return EINVOICE_PROVIDER_UNKNOWN.format(name=name)
    if not _secret_text(getattr(settings, "einvoice_username", None)) or not _secret_text(
        getattr(settings, "einvoice_password", None)
    ):
        return EINVOICE_CREDENTIALS_MISSING
    return provider.configuration_error() or _NOT_CONFIGURED
