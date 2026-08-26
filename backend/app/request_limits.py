from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    """Reject oversized bodies, malformed Content-Length and duplicate metadata headers early.

    An oversized aggregate header block, chunked or otherwise unbounded bodies
    counted while streaming, ambiguous framing (duplicate Content-Length or
    Transfer-Encoding headers, or both present at once), a Transfer-Encoding
    other than chunked, more than one Host, Content-Type, Cookie or
    Authorization header, or a Content-Length that is not a plain decimal count,
    are all rejected before routing so oversized, ambiguous or malformed request
    metadata never reaches the application.
    """

    DEFAULT_MAX_HEADER_BYTES = 32 * 1024

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
        path_overrides: dict[str, int] | None = None,
    ) -> None:
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes en az 1 olmalıdır")
        if max_header_bytes < 1:
            raise ValueError("max_header_bytes en az 1 olmalıdır")
        for prefix, limit in (path_overrides or {}).items():
            if not prefix or limit < 1:
                raise ValueError("path_overrides girdileri boş olamaz ve limit en az 1 olmalıdır")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_header_bytes = max_header_bytes
        # Route-aware body limits: approved path prefixes (e.g. the Excel import
        # endpoints) may carry a different cap than the global default. Longest
        # prefix wins so a more specific override shadows a broader one. Header
        # and framing checks always stay global.
        self.path_overrides: tuple[tuple[str, int], ...] = tuple(
            sorted((path_overrides or {}).items(), key=lambda item: len(item[0]), reverse=True)
        )

    def _body_limit_for(self, path: str) -> int:
        for prefix, limit in self.path_overrides:
            if path.startswith(prefix):
                return limit
        return self.max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_headers = scope.get("headers", [])
        if self._header_bytes(raw_headers) > self.max_header_bytes:
            await self._reject_headers_too_large(send)
            return
        content_lengths = [
            value.strip()
            for name, value in raw_headers
            if name.lower() == b"content-length"
        ]
        transfer_encoding_headers = [
            value
            for name, value in raw_headers
            if name.lower() == b"transfer-encoding"
        ]
        if len(content_lengths) > 1 or len(transfer_encoding_headers) > 1 or (
            content_lengths and transfer_encoding_headers
        ):
            await self._reject_ambiguous_framing(send)
            return
        if transfer_encoding_headers and not self._is_supported_transfer_encoding(
            transfer_encoding_headers[0]
        ):
            await self._reject_invalid_transfer_encoding(send)
            return

        host_headers = [
            value
            for name, value in raw_headers
            if name.lower() == b"host"
        ]
        if len(host_headers) > 1:
            await self._reject_ambiguous_host(send)
            return

        content_type_headers = [
            value
            for name, value in raw_headers
            if name.lower() == b"content-type"
        ]
        if len(content_type_headers) > 1:
            await self._reject_ambiguous_content_type(send)
            return

        cookie_headers = [
            value
            for name, value in raw_headers
            if name.lower() == b"cookie"
        ]
        if len(cookie_headers) > 1:
            await self._reject_ambiguous_cookie(send)
            return

        authorization_headers = [
            value
            for name, value in raw_headers
            if name.lower() == b"authorization"
        ]
        if len(authorization_headers) > 1:
            await self._reject_ambiguous_authorization(send)
            return

        # Match overrides on the same routing path Starlette uses: strip any
        # ASGI root_path so a sub-path deployment cannot silently detach the
        # import endpoints from their approved higher cap.
        request_path = scope.get("path", "")
        root_path = scope.get("root_path", "")
        if root_path and request_path.startswith(root_path):
            request_path = request_path[len(root_path):]
        body_limit = self._body_limit_for(request_path)
        if content_lengths:
            raw_length = content_lengths[0]
            if not self._is_valid_content_length(raw_length):
                await self._reject_invalid_content_length(send)
                return
            if self._declared_body_exceeds_limit(raw_length, body_limit):
                await self._reject(send)
                return

        received = 0
        rejected = False

        async def limited_receive() -> Message:
            nonlocal received, rejected
            message = await receive()
            if message["type"] != "http.request":
                return message

            received += len(message.get("body", b""))
            if received > body_limit:
                rejected = True
                return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not rejected:
                await send(message)

        await self.app(scope, limited_receive, guarded_send)
        if rejected:
            await self._reject(send)

    @staticmethod
    def _header_bytes(raw_headers: list[tuple[bytes, bytes]]) -> int:
        return sum(len(name) + len(value) for name, value in raw_headers)

    @staticmethod
    def _is_valid_content_length(value: bytes) -> bool:
        return bool(value) and all(48 <= byte <= 57 for byte in value)

    @staticmethod
    def _declared_body_exceeds_limit(value: bytes, body_limit: int) -> bool:
        normalized = value.lstrip(b"0") or b"0"
        limit = str(body_limit).encode("ascii")
        return len(normalized) > len(limit) or (
            len(normalized) == len(limit) and normalized > limit
        )

    @staticmethod
    def _is_supported_transfer_encoding(value: bytes) -> bool:
        return value.strip().lower() == b"chunked"

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b'{"detail":"Istek govdesi izin verilen boyutu asiyor","code":"REQUEST_TOO_LARGE"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _reject_headers_too_large(send: Send) -> None:
        body = (
            b'{"detail":"Istek basliklari izin verilen boyutu asiyor",'
            b'"code":"REQUEST_HEADERS_TOO_LARGE"}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 431,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _reject_invalid_content_length(send: Send) -> None:
        body = (
            b'{"detail":"Content-Length basligi gecersiz",'
            b'"code":"INVALID_CONTENT_LENGTH"}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _reject_ambiguous_framing(send: Send) -> None:
        body = (
            b'{"detail":"Istek govdesi cercevelemesi belirsiz",'
            b'"code":"AMBIGUOUS_REQUEST_FRAMING"}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _reject_invalid_transfer_encoding(send: Send) -> None:
        body = (
            b'{"detail":"Transfer-Encoding basligi desteklenmiyor",'
            b'"code":"INVALID_TRANSFER_ENCODING"}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _reject_ambiguous_host(send: Send) -> None:
        body = (
            b'{"detail":"Birden fazla Host basligi kabul edilmez",'
            b'"code":"AMBIGUOUS_HOST"}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _reject_ambiguous_content_type(send: Send) -> None:
        body = (
            b'{"detail":"Birden fazla Content-Type basligi kabul edilmez",'
            b'"code":"AMBIGUOUS_CONTENT_TYPE"}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _reject_ambiguous_cookie(send: Send) -> None:
        body = (
            b'{"detail":"Birden fazla Cookie basligi kabul edilmez",'
            b'"code":"AMBIGUOUS_COOKIE"}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _reject_ambiguous_authorization(send: Send) -> None:
        body = (
            b'{"detail":"Birden fazla Authorization basligi kabul edilmez",'
            b'"code":"AMBIGUOUS_AUTHORIZATION"}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
