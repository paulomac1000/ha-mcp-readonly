"""ASGI request-size enforcement shared by MCP HTTP and REST adapters."""

from __future__ import annotations

import asyncio
import json
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestLimitExceeded(RuntimeError):
    """Internal marker raised when a streamed request exceeds a configured bound."""


class StreamingRequestLimitsMiddleware:
    """Bound MCP requests without pre-reading or replaying the ASGI receive stream."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        max_header_bytes: int,
        max_request_messages: int = 65_536,
    ) -> None:
        if max_body_bytes < 1 or max_header_bytes < 1 or max_request_messages < 1:
            raise ValueError("HTTP request limits must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_header_bytes = max_header_bytes
        self.max_request_messages = max_request_messages

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        header_bytes = sum(len(name) + len(value) for name, value in headers)
        if header_bytes > self.max_header_bytes:
            await _reject(send, 431, "Request headers exceed configured limit")
            return

        for name, value in headers:
            if name.lower() != b"content-length":
                continue
            try:
                content_length = int(value.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                await _reject(send, 400, "Invalid Content-Length header")
                return
            if content_length < 0:
                await _reject(send, 400, "Invalid Content-Length header")
                return
            if content_length > self.max_body_bytes:
                await _reject(send, 413, "Request body exceeds configured limit")
                return
            break

        consumed = 0
        request_messages = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal consumed, request_messages
            message = await receive()
            if message.get("type") == "http.request":
                request_messages += 1
                if request_messages > self.max_request_messages:
                    raise RequestLimitExceeded("Request message count exceeds configured limit")
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    consumed += len(body)
                if consumed > self.max_body_bytes:
                    raise RequestLimitExceeded("Request body exceeds configured limit")
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except RequestLimitExceeded as exc:
            if response_started:
                raise
            await _reject(send, 413, str(exc))


class ConnectionLimitMiddleware:
    """Bound concurrent HTTP request/stream lifetimes without relying on Uvicorn internals."""

    def __init__(self, app: ASGIApp, *, limit: int) -> None:
        if limit < 1:
            raise ValueError("HTTP connection limit must be positive")
        self.app = app
        self.limit = limit
        self._active = 0
        self._guard = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        rejected = False
        async with self._guard:
            if self._active >= self.limit:
                rejected = True
            else:
                self._active += 1
        if rejected:
            await _reject(send, 503, "HTTP connection limit reached", code="SERVER_BUSY")
            return
        try:
            await self.app(scope, receive, send)
        finally:
            async with self._guard:
                self._active -= 1


class RequestLimitsMiddleware:
    """Buffer a bounded REST body as bytes, not an unbounded list of ASGI messages."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        max_header_bytes: int,
        max_request_messages: int = 65_536,
    ) -> None:
        if max_body_bytes < 1 or max_header_bytes < 1 or max_request_messages < 1:
            raise ValueError("HTTP request limits must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_header_bytes = max_header_bytes
        self.max_request_messages = max_request_messages

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        header_bytes = sum(len(name) + len(value) for name, value in headers)
        if header_bytes > self.max_header_bytes:
            await _reject(send, 431, "Request headers exceed configured limit")
            return

        content_length: int | None = None
        for name, value in headers:
            if name.lower() == b"content-length":
                try:
                    content_length = int(value.decode("ascii"))
                except (UnicodeDecodeError, ValueError):
                    await _reject(send, 400, "Invalid Content-Length header")
                    return
                break
        if content_length is not None and content_length < 0:
            await _reject(send, 400, "Invalid Content-Length header")
            return
        if content_length is not None and content_length > self.max_body_bytes:
            await _reject(send, 413, "Request body exceeds configured limit")
            return

        body = bytearray()
        request_messages = 0
        saw_disconnect = False
        body_complete = False
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                saw_disconnect = True
                break
            if message.get("type") != "http.request":
                continue
            request_messages += 1
            if request_messages > self.max_request_messages:
                await _reject(send, 413, "Request message count exceeds configured limit")
                return
            chunk = message.get("body", b"")
            if isinstance(chunk, bytes):
                if len(body) + len(chunk) > self.max_body_bytes:
                    await _reject(send, 413, "Request body exceeds configured limit")
                    return
                body.extend(chunk)
            if not message.get("more_body", False):
                body_complete = True
                break

        replay_pending = True
        disconnect_pending = saw_disconnect

        async def replay_receive() -> Message:
            nonlocal replay_pending, disconnect_pending
            if replay_pending:
                replay_pending = False
                return {
                    "type": "http.request",
                    "body": bytes(body),
                    "more_body": bool(disconnect_pending and not body_complete),
                }
            if disconnect_pending:
                disconnect_pending = False
                return {"type": "http.disconnect"}
            return await receive()

        await self.app(scope, replay_receive, send)


async def _reject(
    send: Send, status: int, message: str, *, code: str = "REQUEST_TOO_LARGE"
) -> None:
    body = json.dumps({"success": False, "error": {"code": code, "message": message}}).encode(
        "utf-8"
    )
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
