"""ASGI HTTP request limit regressions."""

import asyncio

from tools.http_security import RequestLimitsMiddleware


def _run(
    body_chunks: list[bytes],
    headers: list[tuple[bytes, bytes]],
    *,
    body_limit: int = 8,
    header_limit: int = 64,
) -> list[dict]:
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(body_chunks) - 1,
        }
        for index, chunk in enumerate(body_chunks)
    ]
    sent: list[dict] = []

    async def app(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    middleware = RequestLimitsMiddleware(
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
