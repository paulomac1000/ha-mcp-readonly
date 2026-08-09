"""ASGI request-size enforcement shared by MCP HTTP and REST adapters."""

from __future__ import annotations

import json
from collections import deque

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestLimitsMiddleware:
    """Reject oversized aggregate headers and request bodies before parsing."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int, max_header_bytes: int) -> None:
        if max_body_bytes < 1 or max_header_bytes < 1:
            raise ValueError("HTTP request limits must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_header_bytes = max_header_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        header_bytes = sum(len(name) + len(value) for name, value in headers)
        if header_bytes > self.max_header_bytes:
            await self._reject(send, 431, "Request headers exceed configured limit")
            return

        content_length: int | None = None
        for name, value in headers:
            if name.lower() == b"content-length":
                try:
                    content_length = int(value.decode("ascii"))
                except (UnicodeDecodeError, ValueError):
                    await self._reject(send, 400, "Invalid Content-Length header")
                    return
                break
        if content_length is not None and content_length > self.max_body_bytes:
            await self._reject(send, 413, "Request body exceeds configured limit")
            return

        buffered: deque[Message] = deque()
        consumed = 0
        saw_disconnect = False
        while True:
            message = await receive()
            buffered.append(message)
            if message.get("type") == "http.disconnect":
                saw_disconnect = True
                break
            if message.get("type") != "http.request":
                continue
            body = message.get("body", b"")
            if isinstance(body, bytes):
                consumed += len(body)
            if consumed > self.max_body_bytes:
                await self._reject(send, 413, "Request body exceeds configured limit")
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if buffered:
                return buffered.popleft()
            if saw_disconnect:
                return {"type": "http.disconnect"}
            # Streamable HTTP implementations may keep reading after the request
            # body to observe an actual client disconnect while streaming the
            # response. Do not manufacture a disconnect after replaying the body.
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(send: Send, status: int, message: str) -> None:
        body = json.dumps(
            {"success": False, "error": {"code": "REQUEST_TOO_LARGE", "message": message}}
        ).encode("utf-8")
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
