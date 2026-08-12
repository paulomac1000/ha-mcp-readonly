"""ASGI HTTP request limit regressions."""

import asyncio
from starlette.types import Message, Receive, Scope, Send

from tools.http_security import (
    ConnectionLimitMiddleware,
    RequestLimitsMiddleware,
    StreamingRequestLimitsMiddleware,
)


def _run(
    body_chunks: list[bytes],
    headers: list[tuple[bytes, bytes]],
    *,
    body_limit: int = 8,
    header_limit: int = 64,
) -> list[Message]:
    messages: list[Message] = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(body_chunks) - 1,
        }
        for index, chunk in enumerate(body_chunks)
    ]
    sent: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive() -> Message:
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = StreamingRequestLimitsMiddleware(
        app, max_body_bytes=body_limit, max_header_bytes=header_limit
    )
    asyncio.run(
        middleware(
            {"type": "http", "method": "POST", "path": "/", "headers": headers},
            receive,
            send,
        )
    )
    return sent


def test_streamed_body_limit_returns_413() -> None:
    sent = _run([b"12345", b"6789"], [(b"host", b"localhost")])
    assert sent[0]["status"] == 413


def test_aggregate_header_limit_returns_431() -> None:
    sent = _run([b"{}"], [(b"x-long", b"a" * 80)])
    assert sent[0]["status"] == 431


def test_buffering_middleware_bounds_empty_request_messages() -> None:
    async def scenario() -> list[Message]:
        messages: list[Message] = [
            {"type": "http.request", "body": b"", "more_body": True} for _ in range(5)
        ]
        sent: list[Message] = []
        called = False

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            nonlocal called
            called = True

        async def receive() -> Message:
            return messages.pop(0)

        async def send(message: Message) -> None:
            sent.append(message)

        middleware = RequestLimitsMiddleware(
            app,
            max_body_bytes=8,
            max_header_bytes=64,
            max_request_messages=4,
        )
        await middleware(
            {"type": "http", "method": "POST", "path": "/", "headers": []},
            receive,
            send,
        )
        assert called is False
        return sent

    sent = asyncio.run(scenario())
    assert sent[0]["status"] == 413


def test_streaming_middleware_bounds_empty_request_messages() -> None:
    async def scenario() -> list[Message]:
        sent: list[Message] = []
        calls = 0

        async def receive() -> Message:
            nonlocal calls
            calls += 1
            return {"type": "http.request", "body": b"", "more_body": True}

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            for _ in range(5):
                await receive()

        async def send(message: Message) -> None:
            sent.append(message)

        middleware = StreamingRequestLimitsMiddleware(
            app,
            max_body_bytes=8,
            max_header_bytes=64,
            max_request_messages=4,
        )
        await middleware(
            {"type": "http", "method": "POST", "path": "/", "headers": []},
            receive,
            send,
        )
        return sent

    sent = asyncio.run(scenario())
    assert sent[0]["status"] == 413


def test_streaming_middleware_preserves_receive_lifecycle() -> None:
    messages: list[Message] = [
        {"type": "http.request", "body": b"{}", "more_body": False},
        {"type": "http.disconnect"},
    ]
    observed: list[str] = []
    sent: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        first = await receive()
        observed.append(first["type"])
        second = await receive()
        observed.append(second["type"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive() -> Message:
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = StreamingRequestLimitsMiddleware(app, max_body_bytes=8, max_header_bytes=64)
    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": [(b"content-length", b"2")],
            },
            receive,
            send,
        )
    )
    assert observed == ["http.request", "http.disconnect"]
    assert sent[0]["status"] == 200


def test_streaming_middleware_rejects_content_length_before_app() -> None:
    called = False
    sent: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = StreamingRequestLimitsMiddleware(app, max_body_bytes=8, max_header_bytes=64)
    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": [(b"content-length", b"9")],
            },
            receive,
            send,
        )
    )
    assert called is False
    assert sent[0]["status"] == 413


def test_connection_limit_does_not_hold_guard_while_rejecting() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        release_first = asyncio.Event()
        reject_send_entered = asyncio.Event()
        release_reject_send = asyncio.Event()
        first_sent: list[Message] = []
        second_sent: list[Message] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            entered.set()
            await release_first.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def first_send(message: Message) -> None:
            first_sent.append(message)

        async def second_send(message: Message) -> None:
            reject_send_entered.set()
            await release_reject_send.wait()
            second_sent.append(message)

        middleware = ConnectionLimitMiddleware(app, limit=1)
        scope: Scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
        first = asyncio.create_task(middleware(scope, receive, first_send))
        await asyncio.wait_for(entered.wait(), timeout=1)
        second = asyncio.create_task(middleware(scope, receive, second_send))
        await asyncio.wait_for(reject_send_entered.wait(), timeout=1)
        release_first.set()
        await asyncio.wait_for(first, timeout=1)
        release_reject_send.set()
        await asyncio.wait_for(second, timeout=1)
        assert first_sent[0]["status"] == 200
        assert second_sent[0]["status"] == 503

    asyncio.run(scenario())
