from pathlib import Path

HTTP_SECURITY = '''\
"""ASGI request-size enforcement shared by MCP HTTP and REST adapters."""

from __future__ import annotations

import json
from collections import deque
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestLimitExceeded(RuntimeError):
    """Internal marker raised when a streamed body exceeds its configured bound."""


class StreamingRequestLimitsMiddleware:
    """Bound MCP requests without pre-reading or replaying the ASGI receive stream."""

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
            if content_length > self.max_body_bytes:
                await _reject(send, 413, "Request body exceeds configured limit")
                return
            break

        consumed = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message.get("type") == "http.request":
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
        except RequestLimitExceeded:
            if response_started:
                raise
            await _reject(send, 413, "Request body exceeds configured limit")


class RequestLimitsMiddleware:
    """Buffer bounded REST requests before application-level JSON parsing."""

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
        if content_length is not None and content_length > self.max_body_bytes:
            await _reject(send, 413, "Request body exceeds configured limit")
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
                await _reject(send, 413, "Request body exceeds configured limit")
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if buffered:
                return buffered.popleft()
            if saw_disconnect:
                return {"type": "http.disconnect"}
            return await receive()

        await self.app(scope, replay_receive, send)


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
'''

Path("tools/http_security.py").write_text(HTTP_SECURITY, encoding="utf-8")

path = Path("server.py")
text = path.read_text(encoding="utf-8")
old_import = "from tools.http_security import RequestLimitsMiddleware\n"
new_import = (
    "from tools.http_security import RequestLimitsMiddleware, StreamingRequestLimitsMiddleware\n"
)
if old_import not in text:
    raise SystemExit("http security import marker missing")
text = text.replace(old_import, new_import, 1)

old_block = '''    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=MCP_ALLOWED_HOSTS),
        Middleware(
            RequestLimitsMiddleware,
            max_body_bytes=MCP_HTTP_MAX_BODY_BYTES,
            max_header_bytes=MCP_HTTP_MAX_HEADER_BYTES,
        ),
        Middleware(
            CORSMiddleware,
'''
new_block = '''    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=MCP_ALLOWED_HOSTS),
        Middleware(
            StreamingRequestLimitsMiddleware,
            max_body_bytes=MCP_HTTP_MAX_BODY_BYTES,
            max_header_bytes=MCP_HTTP_MAX_HEADER_BYTES,
        ),
        Middleware(
            CORSMiddleware,
'''
if old_block not in text:
    raise SystemExit("MCP middleware block marker missing")
text = text.replace(old_block, new_block, 1)

old_uvicorn = '''        log_level="warning",
        access_log=False,
        limit_concurrency=MCP_HTTP_CONNECTION_LIMIT,
'''
new_uvicorn = '''        log_level=LOG_LEVEL.lower(),
        access_log=LOG_LEVEL.upper() == "DEBUG",
        limit_concurrency=MCP_HTTP_CONNECTION_LIMIT,
'''
if old_uvicorn not in text:
    raise SystemExit("MCP uvicorn logging marker missing")
text = text.replace(old_uvicorn, new_uvicorn, 1)
path.write_text(text, encoding="utf-8")

test_path = Path("tests/unit/test_http_security.py")
test_text = test_path.read_text(encoding="utf-8")
test_text = test_text.replace(
    '''    middleware = StreamingRequestLimitsMiddleware(
        app, max_body_bytes=8, max_header_bytes=64
    )
''',
    '''    middleware = StreamingRequestLimitsMiddleware(app, max_body_bytes=8, max_header_bytes=64)
''',
)
test_path.write_text(test_text, encoding="utf-8")

Path(".github/patch_server_mcp_stream.py").unlink(missing_ok=True)
