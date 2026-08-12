#!/usr/bin/env python3
"""Verify HA-MCP with the official modelcontextprotocol Python client SDK."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import timedelta
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

SUPPORTED_PROTOCOL_REVISION = "2025-11-25"
INVALID_PARAMS = -32602
SESSION_TIMEOUT_SECONDS = 20


def _is_error(result: Any) -> bool:
    return bool(getattr(result, "is_error", getattr(result, "isError", False)))


def _json_payload(result: Any) -> dict[str, Any]:
    content = getattr(result, "content", None)
    if not isinstance(content, list) or not content:
        raise AssertionError(f"tool result has no content: {result!r}")
    text = getattr(content[0], "text", None)
    if not isinstance(text, str):
        raise AssertionError(f"first tool result is not text: {content[0]!r}")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise AssertionError("tool payload is not an object")
    return payload


def _assert_validation_error_result(result: Any) -> None:
    if not _is_error(result):
        raise AssertionError("invalid tool input did not produce a protocol error result")
    content = getattr(result, "content", None)
    if not isinstance(content, list) or not content:
        raise AssertionError("validation failure has no protocol error content")
    if not any(
        isinstance(getattr(item, "text", None), str) and getattr(item, "text").strip()
        for item in content
    ):
        raise AssertionError("validation failure has no non-empty text error content")


async def _verify_session(session: ClientSession) -> None:
    initialized = await session.initialize()
    negotiated = getattr(initialized, "protocolVersion", None)
    if negotiated is None:
        negotiated = getattr(initialized, "protocol_version", None)
    if negotiated != SUPPORTED_PROTOCOL_REVISION:
        raise AssertionError(
            f"protocol mismatch: {negotiated!r} != {SUPPORTED_PROTOCOL_REVISION!r}"
        )

    listing = await session.list_tools()
    names = {tool.name for tool in listing.tools}
    if "describe_ha_capabilities" not in names:
        raise AssertionError("capability discovery tool is missing")
    result = await session.call_tool("describe_ha_capabilities", arguments={})
    if _is_error(result):
        raise AssertionError(f"capability discovery failed: {result!r}")
    payload = _json_payload(result)
    if payload.get("success") is not True:
        raise AssertionError(payload)
    expected_version = os.getenv("MCP_EXPECTED_VERSION")
    if expected_version and payload.get("server_version") != expected_version:
        raise AssertionError(
            f"server version mismatch: {payload.get('server_version')} != {expected_version}"
        )
    if payload.get("sdk", {}).get("family") != "fastmcp":
        raise AssertionError(payload.get("sdk"))
    if SUPPORTED_PROTOCOL_REVISION not in payload.get("protocol_versions", []):
        raise AssertionError(payload.get("protocol_versions"))

    try:
        invalid = await session.call_tool(
            "get_entity_state", arguments={"wrong_parameter": "value"}
        )
    except Exception as exc:
        error = getattr(exc, "error", None)
        code = getattr(error, "code", None)
        if code != INVALID_PARAMS:
            raise AssertionError(
                f"unexpected exception category for invalid input: {exc!r}"
            ) from exc
    else:
        _assert_validation_error_result(invalid)


async def _verify_session_bounded(session: ClientSession) -> None:
    try:
        await asyncio.wait_for(_verify_session(session), timeout=SESSION_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise AssertionError("official MCP client verification timed out") from exc


def _stdio_env(config_path: str) -> dict[str, str]:
    allowed = {
        "PATH": os.getenv("PATH", ""),
        "LANG": os.getenv("LANG", "C.UTF-8"),
        "LC_ALL": os.getenv("LC_ALL", "C.UTF-8"),
        "MCP_TRANSPORT": "stdio",
        "HEALTH_SERVER_ENABLED": "0",
        "MCP_DEV_TOOLS_ENABLED": "0",
        "HA_CONFIG_PATH": config_path,
        "PYTHONUNBUFFERED": "1",
    }
    return {key: value for key, value in allowed.items() if value}


async def verify_stdio(command: str, command_args: list[str], config_path: str) -> None:
    params = StdioServerParameters(
        command=command,
        args=command_args,
        env=_stdio_env(config_path),
    )
    async with stdio_client(params) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=SESSION_TIMEOUT_SECONDS)
        ) as session:
            await _verify_session_bounded(session)


async def verify_http(url: str) -> None:
    import httpx

    token = os.getenv("MCP_AUTH_TOKEN")
    if not token:
        raise ValueError("MCP_AUTH_TOKEN must be set for HTTP verification")
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=SESSION_TIMEOUT_SECONDS) as http_client:
        async with streamable_http_client(url, http_client=http_client) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=SESSION_TIMEOUT_SECONDS)
            ) as session:
                await _verify_session_bounded(session)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="transport", required=True)
    stdio = subparsers.add_parser("stdio")
    stdio.add_argument("--command", required=True)
    stdio.add_argument("--arg", action="append", default=[])
    stdio.add_argument("--config-path", default="/tmp")
    http = subparsers.add_parser("http")
    http.add_argument("--url", required=True)
    args = parser.parse_args()

    if args.transport == "stdio":
        asyncio.run(verify_stdio(args.command, list(args.arg), args.config_path))
    else:
        asyncio.run(verify_http(args.url))


if __name__ == "__main__":
    main()
