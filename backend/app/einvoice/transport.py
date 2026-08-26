"""HTTP transport seam for the e-Fatura adapters (spec §7).

The adapters never touch a socket directly: every provider call goes through
:class:`HttpTransport`. Tests substitute a recording double for it, so the whole
control flow — status machine, error mapping, retry/no-retry policy — is covered
without knowing (or inventing) a single real provider endpoint.

Timeout policy: connect 5 s, read 30 s. ``http.client`` is used rather than
``urllib.request`` precisely because it exposes the two separately: the
connection is built with the connect timeout, then the live socket is switched
to the read timeout before the request is written.

Retry policy: only idempotent operations retry (``query_status``, ``fetch_pdf``,
``check_taxpayer``), 3 attempts with 1s/2s/4s backoff. ``submit`` never retries —
a duplicated envelope is worse than a reported failure.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit


CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 30

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS: tuple[int, ...] = (1, 2, 4)

#: Transient HTTP answers that justify another attempt for an idempotent call.
RETRYABLE_STATUS: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})


class TransportError(RuntimeError):
    """Network-level failure: DNS, connect, TLS, timeout, truncated response."""


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def text(self, limit: int) -> str:
        """Decode at most ``limit`` bytes; provider bodies are never trusted to be small."""
        return self.body[:limit].decode("utf-8", errors="replace")


class HttpTransport:
    """Thin stdlib HTTP client. The only place in the package that opens a socket."""

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        connect_timeout: int = CONNECT_TIMEOUT_SECONDS,
        read_timeout: int = READ_TIMEOUT_SECONDS,
        max_bytes: int = 2 * 1024 * 1024,
    ) -> HttpResponse:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise TransportError("Geçersiz e-Fatura ucu")
        target = parts.path or "/"
        if parts.query:
            target = f"{target}?{parts.query}"

        connection: http.client.HTTPConnection
        if parts.scheme == "https":
            connection = http.client.HTTPSConnection(
                parts.hostname, parts.port, timeout=connect_timeout, context=ssl.create_default_context()
            )
        else:
            connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=connect_timeout)
        try:
            connection.connect()
            if connection.sock is not None:
                # Connect finished within the connect budget; reads get their own.
                connection.sock.settimeout(read_timeout)
            connection.request(method, target, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = response.read(max_bytes)
            return HttpResponse(
                status_code=response.status,
                body=payload,
                headers={key.lower(): value for key, value in response.getheaders()},
            )
        except (OSError, socket.timeout, http.client.HTTPException, ssl.SSLError) as exc:
            # The message is deliberately generic: it is mapped to the fixed NETWORK
            # sentence upstream and must not leak the host, path or credentials.
            raise TransportError("e-Fatura servisine bağlanılamadı") from exc
        finally:
            connection.close()


def _sleep(seconds: int) -> None:
    """Indirection so tests can neutralise the backoff without touching ``time``."""
    time.sleep(seconds)


def with_retry(
    operation: Callable[[], HttpResponse],
    *,
    attempts: int = RETRY_ATTEMPTS,
    backoff: tuple[int, ...] = RETRY_BACKOFF_SECONDS,
) -> HttpResponse:
    """Run an **idempotent** call with exponential backoff on transient failure.

    Returns the last response even when it is still transient — the caller maps
    it to a NETWORK error. Non-transient answers (4xx) return immediately: they
    will not get better by asking again.
    """
    last_error: TransportError | None = None
    last_response: HttpResponse | None = None
    for attempt in range(max(1, attempts)):
        try:
            response = operation()
        except TransportError as exc:
            last_error = exc
            last_response = None
        else:
            if response.status_code not in RETRYABLE_STATUS:
                return response
            last_error = None
            last_response = response
        if attempt + 1 < max(1, attempts):
            _sleep(backoff[min(attempt, len(backoff) - 1)])
    if last_response is not None:
        return last_response
    raise last_error if last_error is not None else TransportError("e-Fatura çağrısı başarısız")


def call_once(operation: Callable[[], HttpResponse]) -> HttpResponse:
    """Single attempt, no retry — the ``submit()`` path (spec §7)."""
    return operation()


def response_summary(response: HttpResponse, limit: int) -> dict[str, Any]:
    """Auditable, bounded view of a provider answer for ``EInvoiceResult.raw``.

    Contains the response only. The request (which carries the credentials) is
    never summarised, and the URL is never included.
    """
    return {"http_status": response.status_code, "body": response.text(limit)}
