from pathlib import Path

http_path = Path("tools/http_security.py")
http_text = http_path.read_text(encoding="utf-8")
http_text = http_text.replace(
    "import json\nfrom collections import deque\n",
    "import asyncio\nimport json\nfrom collections import deque\n",
    1,
)
marker = "\n\nclass RequestLimitsMiddleware:\n"
connection_class = '''

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

        async with self._guard:
            if self._active >= self.limit:
                await _reject(send, 503, "HTTP connection limit reached", code="SERVER_BUSY")
                return
            self._active += 1
        try:
            await self.app(scope, receive, send)
        finally:
            async with self._guard:
                self._active -= 1
'''
if marker not in http_text:
    raise SystemExit("RequestLimitsMiddleware marker missing")
http_text = http_text.replace(marker, connection_class + marker, 1)
http_text = http_text.replace(
    "async def _reject(send: Send, status: int, message: str) -> None:\n",
    "async def _reject(\n    send: Send, status: int, message: str, *, code: str = \"REQUEST_TOO_LARGE\"\n) -> None:\n",
    1,
)
http_text = http_text.replace(
    '{"success": False, "error": {"code": "REQUEST_TOO_LARGE", "message": message}}',
    '{"success": False, "error": {"code": code, "message": message}}',
    1,
)
http_path.write_text(http_text, encoding="utf-8")

server_path = Path("server.py")
server = server_path.read_text(encoding="utf-8")
old_import = "from tools.http_security import RequestLimitsMiddleware, StreamingRequestLimitsMiddleware\n"
new_import = '''from tools.http_security import (
    ConnectionLimitMiddleware,
    RequestLimitsMiddleware,
    StreamingRequestLimitsMiddleware,
)
'''
if old_import not in server:
    raise SystemExit("server HTTP security import marker missing")
server = server.replace(old_import, new_import, 1)
old_stack = '''    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=MCP_ALLOWED_HOSTS),
        Middleware(
            StreamingRequestLimitsMiddleware,
'''
new_stack = '''    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=MCP_ALLOWED_HOSTS),
        Middleware(ConnectionLimitMiddleware, limit=MCP_HTTP_CONNECTION_LIMIT),
        Middleware(
            StreamingRequestLimitsMiddleware,
'''
if old_stack not in server:
    raise SystemExit("MCP middleware stack marker missing")
server = server.replace(old_stack, new_stack, 1)
line = "        limit_concurrency=MCP_HTTP_CONNECTION_LIMIT,\n"
if line not in server:
    raise SystemExit("uvicorn limit_concurrency marker missing")
server = server.replace(line, "", 1)
server_path.write_text(server, encoding="utf-8")

test_path = Path("tests/unit/test_http_security.py")
tests = test_path.read_text(encoding="utf-8")
tests = tests.replace(
    "from tools.http_security import RequestLimitsMiddleware, StreamingRequestLimitsMiddleware\n",
    '''from tools.http_security import (
    ConnectionLimitMiddleware,
    RequestLimitsMiddleware,
    StreamingRequestLimitsMiddleware,
)
''',
    1,
)
if "test_connection_limit_rejects_overlapping_request" not in tests:
    tests += '''


def test_connection_limit_rejects_overlapping_request() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        first_sent: list[dict] = []
        second_sent: list[dict] = []

        async def app(scope, receive, send):
            entered.set()
            await release.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def first_send(message):
            first_sent.append(message)

        async def second_send(message):
            second_sent.append(message)

        middleware = ConnectionLimitMiddleware(app, limit=1)
        scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
        first = asyncio.create_task(middleware(scope, receive, first_send))
        await asyncio.wait_for(entered.wait(), timeout=1)
        await middleware(scope, receive, second_send)
        assert second_sent[0]["status"] == 503
        release.set()
        await asyncio.wait_for(first, timeout=1)
        assert first_sent[0]["status"] == 200

    asyncio.run(scenario())
'''
test_path.write_text(tests, encoding="utf-8")

Path(".github/patch_http_admission.py").unlink(missing_ok=True)
