#!/usr/bin/env python3
"""Verify the real liveness, REST, context, and Streamable HTTP MCP boundaries."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HEALTH_URL = os.getenv("RUNTIME_HEALTH_URL", "http://127.0.0.1:9091")
MCP_URL = os.getenv("RUNTIME_MCP_URL", "http://127.0.0.1:9092/mcp")
REST_URL = os.getenv("RUNTIME_REST_URL", "http://127.0.0.1:9093")
TOKEN = os.getenv("RUNTIME_AUTH_TOKEN", "test-only-ci-token")
CONFIG_PATH = os.getenv("RUNTIME_CONFIG_PATH", "/config")
FORBIDDEN = tuple(
    value for value in os.getenv("RUNTIME_FORBIDDEN_CONTEXT_VALUES", "").split(",") if value
)


def _request(
    url: str,
    *,
    token: str | None = None,
    method: str = "GET",
    payload: Any = None,
    timeout: float = 5,
) -> tuple[int, bytes, dict[str, str]]:
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())


def _json(url: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
    status, body, _ = _request(url, **kwargs)
    value = json.loads(body.decode("utf-8")) if body else {}
    if not isinstance(value, dict):
        raise AssertionError(f"Expected JSON object from {url}")
    return status, value


def wait_until_ready() -> None:
    deadline = time.monotonic() + 60
    last: object = None
    while time.monotonic() < deadline:
        try:
            status, payload = _json(f"{HEALTH_URL}/ready", timeout=2)
            last = (status, payload)
            if status == 200 and payload.get("status") == "ready":
                return
        except Exception as exc:  # pragma: no cover - runtime diagnostic path
            last = exc
        time.sleep(0.5)
    raise AssertionError(f"runtime did not become ready: {last!r}")


def verify_health() -> None:
    status, live = _json(f"{HEALTH_URL}/live")
    assert status == 200 and live.get("status") == "live", live
    status, ready = _json(f"{HEALTH_URL}/ready")
    assert status == 200 and ready.get("status") == "ready", ready
    status, detailed = _json(f"{HEALTH_URL}/health")
    assert status == 200, detailed
    assert "invocations" not in detailed, "public health must not expose usage profiling"
    components = detailed.get("components")
    assert isinstance(components, dict)
    for required in ("catalog", "transport", "filesystem", "backend", "rest"):
        assert required in components, (required, components)


def verify_rest(strict: bool) -> dict[str, Any]:
    status, unauthorized = _json(f"{REST_URL}/api/tools")
    assert status == 401, unauthorized

    status, health = _json(f"{REST_URL}/api/health")
    assert status == 200 and "components" in health, health

    status, catalog = _json(f"{REST_URL}/api/tools?detail=full", token=TOKEN)
    assert status == 200, catalog
    tools = catalog.get("tools")
    assert isinstance(tools, list) and len(tools) == 145, (
        len(tools) if isinstance(tools, list) else tools
    )
    assert catalog.get("total") == 145

    status, openapi = _json(f"{REST_URL}/api/openapi.json", token=TOKEN)
    assert status == 200
    paths = openapi.get("paths")
    assert isinstance(paths, dict)
    for tool in tools:
        name = tool["name"]
        assert f"/api/tools/{name}" in paths
        manifest = tool.get("manifest")
        assert isinstance(manifest, dict)
        assert manifest.get("active_state") == "active"
        assert manifest.get("operation_kind") == "read"
        assert manifest.get("retryable") is False
        concurrency = manifest.get("concurrency")
        assert isinstance(concurrency, dict) and int(concurrency.get("limit", 0)) >= 1
        if strict:
            m_status, m_payload = _json(f"{REST_URL}/api/tools/{name}/manifest", token=TOKEN)
            s_status, s_payload = _json(f"{REST_URL}/api/tools/{name}/schema", token=TOKEN)
            assert m_status == 200 and m_payload.get("manifest", {}).get("id") == name
            assert s_status == 200 and s_payload.get("name") == name
    return {"tool_count": len(tools), "openapi_paths": len(paths)}


def verify_context() -> int:
    status, start = _json(
        f"{REST_URL}/api/context/generate",
        token=TOKEN,
        method="POST",
        payload={"config_path": CONFIG_PATH, "mode": "offline"},
        timeout=10,
    )
    assert status == 202, start
    task_id = start.get("task_id")
    assert task_id
    deadline = time.monotonic() + 90
    final: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, final = _json(f"{REST_URL}/api/context/status", token=TOKEN, timeout=5)
        assert status == 200, final
        if final.get("status") == "completed":
            break
        if final.get("status") in {"error", "deadline_exceeded"}:
            raise AssertionError(final)
        time.sleep(0.25)
    else:
        raise AssertionError(f"context task did not complete: {final}")

    status, body, _ = _request(f"{REST_URL}/api/context/download", token=TOKEN, timeout=10)
    assert status == 200
    text = body.decode("utf-8")
    assert "Source Provenance and Completeness" in text
    assert "Comprehensive Safe Data Snapshot" in text
    assert "filesystem_snapshot" in text
    assert "offline mode" in text
    for forbidden in FORBIDDEN:
        assert forbidden not in text, f"secret leaked into context artifact: {forbidden!r}"
    return len(body)


async def verify_mcp() -> None:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    import httpx

    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.post(
            MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        )
        assert response.status_code == 401, response.text

    transport = StreamableHttpTransport(
        MCP_URL,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    async with Client(transport, timeout=20) as client:
        tools = await client.list_tools()
        assert len(tools) == 145
        result = await client.call_tool("describe_ha_capabilities", {})
        assert not result.is_error


def self_check() -> None:
    import server
    from tools.manifests import get_all_manifests

    manifests = get_all_manifests()
    assert len(manifests) >= 145
    assert len(server.get_all_tools()) == 145
    for name in server.get_all_tools():
        manifest = manifests[name]
        assert manifest["id"] == name
        assert manifest["active_state"] == "active"
        assert manifest["operation_kind"] == "read"
        assert manifest["retryable"] is False
        assert manifest["concurrency"]["limit"] >= 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict", action="store_true", help="Verify every REST manifest/schema path"
    )
    parser.add_argument(
        "--self-check", action="store_true", help="Validate runtime contracts without network"
    )
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    wait_until_ready()
    verify_health()
    stats = verify_rest(args.strict)
    context_bytes = verify_context()
    asyncio.run(verify_mcp())
    print(json.dumps({**stats, "context_bytes": context_bytes, "status": "ok"}, sort_keys=True))


if __name__ == "__main__":
    main()
