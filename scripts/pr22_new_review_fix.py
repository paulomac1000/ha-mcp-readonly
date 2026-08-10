#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    text = read(path)
    found = text.count(old)
    if found < count:
        raise AssertionError(
            f"{path}: expected at least {count} occurrences, found {found}: {old[:120]!r}"
        )
    write(path, text.replace(old, new, count))


def regex_replace(
    path: str, pattern: str, replacement: str, *, count: int = 1, flags: int = 0
) -> None:
    text = read(path)
    updated, replaced = re.subn(pattern, replacement, text, count=count, flags=flags)
    if replaced != count:
        raise AssertionError(
            f"{path}: expected {count} regex replacements, got {replaced}: {pattern!r}"
        )
    write(path, updated)


# ---------------------------------------------------------------------------
# Security/request boundaries
# ---------------------------------------------------------------------------
write(
    "tools/http_security.py",
    '''"""ASGI request-size enforcement shared by MCP HTTP and REST adapters."""

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
''',
)

# ---------------------------------------------------------------------------
# Key-aware redaction, JSON schema, output envelope
# ---------------------------------------------------------------------------
replace(
    "tools/redaction.py",
    '_NORMALIZE_KEY = re.compile(r"[^a-z0-9]+")\n',
    '_CAMEL_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")\n'
    '_CAMEL_WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")\n'
    '_NORMALIZE_KEY = re.compile(r"[^a-z0-9]+")\n',
)
replace(
    "tools/redaction.py",
    'def _normalized_key(value: Any) -> str:\n    return _NORMALIZE_KEY.sub("_", str(value).strip().casefold()).strip("_")\n',
    "def _normalized_key(value: Any) -> str:\n"
    "    raw = str(value).strip()\n"
    '    raw = _CAMEL_ACRONYM_BOUNDARY.sub("_", raw)\n'
    '    raw = _CAMEL_WORD_BOUNDARY.sub("_", raw)\n'
    '    return _NORMALIZE_KEY.sub("_", raw.casefold()).strip("_")\n',
)

write(
    "tools/schema_utils.py",
    '''"""Application-owned JSON-schema helpers for callable signatures."""

from __future__ import annotations

import inspect
from typing import Any, get_type_hints

from pydantic import TypeAdapter
from pydantic.errors import PydanticSchemaGenerationError


def _resolved_annotations(function: Any) -> dict[str, Any]:
    """Resolve annotations without discarding good parameters when one forward ref is bad."""
    raw = inspect.get_annotations(function, eval_str=False)
    try:
        return get_type_hints(function, include_extras=True)
    except (NameError, TypeError):
        globalns = getattr(function, "__globals__", {})
        resolved: dict[str, Any] = {}
        for name, annotation in raw.items():
            if not isinstance(annotation, str):
                resolved[name] = annotation
                continue
            try:
                resolved[name] = eval(annotation, globalns, {})
            except (NameError, TypeError, SyntaxError):
                resolved[name] = annotation
        return resolved


def _merge_defs(root: dict[str, Any], parameter_schema: dict[str, Any]) -> dict[str, Any]:
    """Lift TypeAdapter definitions so local #/$defs references resolve from the root schema."""
    schema = dict(parameter_schema)
    definitions = schema.pop("$defs", None)
    if not isinstance(definitions, dict):
        return schema
    root_definitions = root.setdefault("$defs", {})
    for name, definition in definitions.items():
        existing = root_definitions.get(name)
        if existing is not None and existing != definition:
            raise ValueError(f"Conflicting JSON-schema definition: {name}")
        root_definitions[name] = definition
    return schema


def signature_to_json_schema(function: Any) -> dict[str, Any]:
    """Build a JSON schema while preserving unions, generics, refs, and Annotated metadata."""
    signature = inspect.signature(function)
    annotations = _resolved_annotations(function)
    schema: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
    required: list[str] = []

    for name, parameter in signature.parameters.items():
        if name == "self":
            continue
        annotation = annotations.get(name, parameter.annotation)
        if annotation is inspect.Signature.empty or isinstance(annotation, str):
            parameter_schema: dict[str, Any] = {"type": "string"}
        else:
            try:
                parameter_schema = TypeAdapter(annotation).json_schema()
            except PydanticSchemaGenerationError:
                parameter_schema = {"type": "string"}
        property_schema = _merge_defs(schema, parameter_schema)
        if parameter.default is inspect.Signature.empty:
            required.append(name)
        else:
            property_schema["default"] = parameter.default
        schema["properties"][name] = property_schema

    if required:
        schema["required"] = required
    return schema
''',
)

old = '''def _augment_result(result: Any, tool_name: str, start: float) -> Any:
    """Attach invocation metadata and enforce the final serialized response limit."""
    manifest = get_manifest(tool_name) or {}
    max_bytes = int(manifest.get("max_response_bytes", 1024 * 1024))
    metadata = {
        "tool_version": str(manifest.get("extensions", {}).get("tool_version", TOOLS_VERSION)),
        "duration_ms": int((time.monotonic() - start) * 1000),
    }
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            sanitized_text = sanitize_response_data(result)
            KERNEL.enforce_final_result_size(sanitized_text, max_bytes)
            return sanitized_text
        if not isinstance(parsed, dict):
            sanitized_value = sanitize_response_data(parsed)
            KERNEL.enforce_final_result_size(sanitized_value, max_bytes)
            return sanitized_value
        parsed = sanitize_response_data(parsed)
        existing = parsed.get("_meta") if isinstance(parsed.get("_meta"), dict) else {}
        parsed["_meta"] = {**existing, **metadata}
        encoded = json.dumps(parsed, indent=2, ensure_ascii=False)
        KERNEL.enforce_final_result_size(encoded, max_bytes)
        return encoded
    if isinstance(result, dict):
        sanitized_result = sanitize_response_data(result)
        existing = (
            sanitized_result.get("_meta")
            if isinstance(sanitized_result.get("_meta"), dict)
            else {}
        )
        sanitized_result["_meta"] = {**existing, **metadata}
        KERNEL.enforce_final_result_size(sanitized_result, max_bytes)
        return sanitized_result
    sanitized_result = sanitize_response_data(result)
    KERNEL.enforce_final_result_size(sanitized_result, max_bytes)
    return sanitized_result
'''
new = '''def _augment_result(result: Any, tool_name: str, start: float) -> str:
    """Normalize every public operation result to one JSON-string envelope."""
    manifest = get_manifest(tool_name) or {}
    max_bytes = int(manifest.get("max_response_bytes", 1024 * 1024))
    metadata = {
        "tool_version": str(manifest.get("extensions", {}).get("tool_version", TOOLS_VERSION)),
        "duration_ms": int((time.monotonic() - start) * 1000),
    }

    parsed: Any = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            parsed = result
    sanitized = sanitize_response_data(parsed)
    if isinstance(sanitized, dict):
        payload = dict(sanitized)
        payload.setdefault("success", "error" not in payload)
    else:
        payload = {"success": True, "result": sanitized}
    existing = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    payload["_meta"] = {**existing, **metadata}
    encoded = json.dumps(payload, indent=2, ensure_ascii=False)
    KERNEL.enforce_final_result_size(encoded, max_bytes)
    return encoded
'''
replace("tools/operations.py", old, new)

# ---------------------------------------------------------------------------
# Context snapshot/provenance and filesystem display paths
# ---------------------------------------------------------------------------
replace(
    "context_generator/snapshot.py",
    """                        try:
                            response = _ws_command(
                                websocket,
                                request_id,
                                {
                                    "type": "unsubscribe_events",
                                    "subscription": subscription_id,
                                },
                            )
                            if not response.get("success"):
                                raise WebSocketProtocolError(
                                    "weather forecast unsubscribe was rejected"
                                )
                            subscribed = False
                        except Exception:
                            raise
""",
    """                        unsubscribe_id = request_id
                        request_id += 1
                        websocket.send(
                            json.dumps(
                                {
                                    "id": unsubscribe_id,
                                    "type": "unsubscribe_events",
                                    "subscription": subscription_id,
                                }
                            )
                        )
                        for _ in range(64):
                            response = _ws_recv_json(websocket)
                            response_id = response.get("id")
                            response_type = response.get("type")
                            if response_id == subscription_id and response_type == "event":
                                continue
                            if response_id == unsubscribe_id and response_type == "result":
                                if not response.get("success"):
                                    raise WebSocketProtocolError(
                                        "weather forecast unsubscribe was rejected"
                                    )
                                subscribed = False
                                break
                            raise WebSocketProtocolError(
                                "unexpected websocket response while unsubscribing forecast"
                            )
                        else:
                            raise WebSocketProtocolError(
                                "weather forecast unsubscribe response limit exceeded"
                            )
""",
)
replace(
    "context_generator/snapshot.py",
    """                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if (
                    size > self.config.max_source_bytes
                    or total_bytes + size > self.config.max_source_bytes
                ):
                    self.provenance.record(
                        f"file:{relative.as_posix()}",
                        method="filesystem",
                        status="partial",
                        size_bytes=size,
                        reason="aggregate source-size limit reached",
                    )
                    continue
                try:
                    raw = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
""",
    """                try:
                    size = path.stat().st_size
                except OSError as exc:
                    self.provenance.record(
                        f"file:{relative.as_posix()}",
                        method="filesystem",
                        status="partial",
                        reason=f"stat failed: {type(exc).__name__}",
                    )
                    continue
                if size > self.config.max_source_bytes:
                    self.provenance.record(
                        f"file:{relative.as_posix()}",
                        method="filesystem",
                        status="partial",
                        size_bytes=size,
                        reason="per-file source-size limit reached",
                    )
                    continue
                if total_bytes + size > self.config.max_source_bytes:
                    self.provenance.record(
                        f"file:{relative.as_posix()}",
                        method="filesystem",
                        status="partial",
                        size_bytes=size,
                        reason="aggregate source-size limit reached",
                    )
                    continue
                try:
                    raw = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    self.provenance.record(
                        f"file:{relative.as_posix()}",
                        method="filesystem",
                        status="partial",
                        size_bytes=size,
                        reason=f"read failed: {type(exc).__name__}",
                    )
                    continue
""",
)
replace(
    "context_generator/utils.py",
    """        else:
            return {"success": False, "error": f"Unsupported method: {method}"}
""",
    """        else:
            reason = f"Unsupported method: {method}"
            _record_source(
                source,
                method="rest",
                status="unavailable",
                reason=reason,
                requested=endpoint,
            )
            return {"success": False, "error": reason}
""",
)

replace(
    "tools/filesystem_explorer.py",
    """            filepath = Path(root) / filename
            try:
                filepath = context.validate_text_file(filepath)
""",
    """            candidate = Path(root) / filename
            try:
                relative_path = candidate.relative_to(target)
            except ValueError:
                continue
            try:
                filepath = context.validate_text_file(candidate)
""",
)
replace(
    "tools/filesystem_explorer.py",
    """                        "path": str(filepath.relative_to(target)),
                        "absolute_path": str(filepath),
                        "matches_count": len(matches),
""",
    """                        "path": relative_path.as_posix(),
                        "absolute_path": str(filepath),
                        "matches_count": len(list(re.finditer(re.escape(pattern), content, re.IGNORECASE))),
""",
)

# Fail closed on method types before normalization.
replace(
    "tools/utils.py",
    """    if retries < 1:
        raise ValueError("retries must be at least 1")
    normalized_method = method.upper()
""",
    """    if retries < 1:
        raise ValueError("retries must be at least 1")
    if not isinstance(method, str):
        raise ValueError("HTTP method must be a string")
    normalized_method = method.upper()
""",
)

# Observe background future exceptions after caller timeout/cancellation.
replace(
    "tools/invocation.py",
    """            wrapped = asyncio.wrap_future(future)
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(wrapped), timeout=_remaining(deadline)
                )
""",
    """            wrapped = asyncio.wrap_future(future)

            def observe_background_result(completed: asyncio.Future[Any]) -> None:
                if completed.cancelled():
                    return
                try:
                    completed.exception()
                except asyncio.CancelledError:
                    return

            wrapped.add_done_callback(observe_background_result)
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(wrapped), timeout=_remaining(deadline)
                )
""",
)

# Preserve manifest active_state as declaration; runtime_active is the dynamic state.
replace(
    "tools/capabilities.py",
    """        runtime_active = name in active_names if initialized else None
        item["runtime_active"] = runtime_active
        if runtime_active is False:
            item["active_state"] = "inactive"
""",
    """        runtime_active = name in active_names if initialized else None
        item["runtime_active"] = runtime_active
""",
)

# ---------------------------------------------------------------------------
# CLI/evidence helpers
# ---------------------------------------------------------------------------
write(
    "scripts/migration_assessment_brief.py",
    '''#!/usr/bin/env python3
"""Print immutable inputs required for a canonical provider-backed assessment."""

from __future__ import annotations

import argparse
import json
import re

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _revision(value: str) -> str:
    normalized = value.strip().lower()
    if not _SHA_RE.fullmatch(normalized):
        raise argparse.ArgumentTypeError("--revision must be a full 40-character commit SHA")
    return normalized


def _positive_pr(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--pr must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("--pr must be a positive integer")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", required=True, type=_revision)
    parser.add_argument("--pr", type=_positive_pr, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "repository": "paulomac1000/ha-mcp-readonly",
                "revision": args.revision,
                "pull_request": args.pr,
                "required_external_evidence": [
                    "successful exact-head CI jobs and artifact digests",
                    "official mcp==1.29.0 stdio smoke for negotiated 2025-11-25 on the exact wheel",
                    "official mcp==1.29.0 Streamable HTTP smoke for negotiated 2025-11-25 on the exact container",
                    "real Home Assistant smoke, E2E, and integration suites",
                    "independent GitHub APPROVED review bound to the same revision",
                ],
                "unsupported_claims": [
                    "Do not claim MCP 2026-07-28 compatibility for the FastMCP 3.x lane without separate exact-artifact evidence."
                ],
                "ai_skills_revision": "b54fc6b27ea80b36a70d5de73445970e17f55789",
                "decision_before_external_evidence": "request-changes",
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
''',
)

write(
    "scripts/verify_official_mcp_client.py",
    '''#!/usr/bin/env python3
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
    if not any(isinstance(getattr(item, "text", None), str) and getattr(item, "text").strip() for item in content):
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
            raise AssertionError(f"unexpected exception category for invalid input: {exc!r}") from exc
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
''',
)

# ---------------------------------------------------------------------------
# Documentation, verification commands and operator examples
# ---------------------------------------------------------------------------
replace(
    "docker-compose.build.yml",
    """      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen(http://127.0.0.1:9091/ready, timeout=3)"]
""",
    """      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9091/ready', timeout=3)"]
""",
)

write(
    "Makefile",
    """.PHONY: test test-integration test-all typecheck lint format docs-check docker-build docker-build-source docker-run help clean

AFDS_VALIDATOR := scripts/vendor/afds_validate_b54fc6b2.py

help:
	@echo "Available targets:"
	@echo "  test              - Run unit tests"
	@echo "  test-integration  - Run integration tests (requires HA_URL + HA_TOKEN)"
	@echo "  test-all          - Run all test suites (unit, smoke, e2e, integration)"
	@echo "  typecheck         - Run strict mypy on server.py and tools/"
	@echo "  lint              - Run ruff linter"
	@echo "  format            - Format code with ruff"
	@echo "  docs-check        - Validate all governed Markdown with pinned AFDS validator"
	@echo "  docker-build      - Build Docker image"
	@echo "  docker-build-run  - Build from source and run"
	@echo "  clean             - Remove cache files"

test:
	pytest tests/unit/ -v --tb=short --cov=. --cov-report=term

test-integration:
	pytest tests/integration/ -v

test-all:
	pytest tests/unit/ tests/smoke/ tests/e2e/ tests/integration/ -q

typecheck:
	mypy server.py tools/ --strict

lint:
	ruff check .

format:
	ruff format .

docker-build:
	docker build -t ha-mcp-readonly:latest .

docker-build-run:
	docker compose -f docker-compose.build.yml up -d

docs-check:
	@set -eu; \
		docs="$$(find docs -type f -name '*.md' | sort)"; \
		python3 $(AFDS_VALIDATOR) AGENTS.md CHANGELOG.md CONTRIBUTING.md README.md SECURITY.md $$docs

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -f .coverage coverage.xml
""",
)

# Keep local hooks aligned with the canonical gates instead of the stale vendored validator.
replace(
    ".pre-commit-config.yaml",
    "entry: mypy tools/ --strict",
    "entry: mypy server.py tools/ --strict",
)
replace(
    ".pre-commit-config.yaml",
    "entry: bandit -r tools/ -ll",
    "entry: bandit -r server.py tools/ context_generator/ ha_graph/ -ll",
)
replace(
    ".pre-commit-config.yaml",
    """      - id: afds-docs
        name: AFDS documentation validation (vendored pinned validator)
        entry: python3 scripts/vendor/afds_validate_c6dc6b13.py AGENTS.md CONTRIBUTING.md SECURITY.md docs/documentation.md docs/testing-guidelines.md docs/ai-skills-adoption.md
""",
    """      - id: afds-docs
        name: AFDS documentation validation (vendored pinned validator)
        entry: make docs-check
""",
)

replace(
    "AGENTS.md",
    """3. Check the official Home Assistant REST or WebSocket documentation to decide whether the endpoint is public and therefore usable with a normal long-lived access token.
4. Verify the exact endpoint manually with a normal long-lived access token, for example:
""",
    """3. Check the official Home Assistant REST or WebSocket documentation to establish the public API shape. Documentation alone does **not** prove that a normal long-lived access token has the required authorization.
4. Verify the exact endpoint manually with the same class of normal long-lived access token intended for production, for example:
""",
)
replace(
    "AGENTS.md",
    """python scripts/vendor/afds_validate_c6dc6b13.py AGENTS.md CONTRIBUTING.md SECURITY.md docs/documentation.md docs/testing-guidelines.md docs/ai-skills-adoption.md
""",
    """make docs-check
""",
)
replace(
    "AGENTS.md",
    """- `ruff check .`, `ruff format --check .`, strict `mypy`, Bandit, Semgrep, registry-name validation, URL-encoding detection, version consistency, and the configured AFDS validation all pass.
""",
    """- `ruff check .`, `ruff format --check .`, strict `mypy`, Bandit, Semgrep, registry-name validation, URL-encoding detection, version consistency, `make docs-check`, and the full configured `pre-commit run --all-files` gate all pass.
- Every newly supported Home Assistant REST/WebSocket API surface is backed by official API-shape documentation, a successful manual request using the intended long-lived-token class, a sanitized recorded/VCR upstream-contract fixture, focused protocol tests, and smoke coverage that keeps catalog counts and capability discovery consistent.
""",
)

# Normalize changelog structure and remove stale evidence claims.
text = read("CHANGELOG.md")
text = text.replace(
    "# Changelog\n\n## [2.0.0] - 2026-08-09\n",
    "# Changelog\n\nAll notable changes to this project will be documented in this file.\n\nThe format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).\n\n## [Unreleased]\n",
    1,
)
text = text.replace(
    "`paulomac1000/ai-skills` revision `c5ba4091` with per-skill content digests.\n- Re-pinned the CI documentation validator and the adoption target to the current\n  `fix/unified-contract-release-hardening` revision; the capability-manifest schema\n  and AFDS validator are unchanged between the old and new pins.",
    "`paulomac1000/ai-skills` revision `b54fc6b27ea80b36a70d5de73445970e17f55789` with per-skill content digests.\n- Re-pinned the CI documentation validator and adoption target to the immutable\n  `b54fc6b27ea80b36a70d5de73445970e17f55789` revision from the\n  `fix/unified-contract-release-hardening` line; the consumer lock, validator, and\n  migration evidence now identify the same immutable revision.",
)
text = re.sub(
    r"### Tests\n\n- Unit and protocol: 1195 passing, including the recorded-cassette contract test\.\n- Smoke: 86 passing against the local container with live Home Assistant\.\n- E2E: 174 passing against the local container with live Home Assistant\.\n- Integration: 272 passing, 6 skipped \(environment-dependent\) against live\n  Home Assistant through the composition root\.\n\nAll notable changes to this project will be documented in this file\.\n\nThe format is based on \[Keep a Changelog\]\(https://keepachangelog\.com/en/1\.1\.0/\),\n\n## \[1\.6\.0\] - 2026-06-11\nand this project adheres to \[Semantic Versioning\]\(https://semver\.org/spec/v2\.0\.0\.html\)\.\n\n## \[1\.6\.0\] - 2026-06-11",
    "### Tests\n\n- Unit and protocol: 1,220 passed on exact-head revision `bf88ad03168f2095d59107aa7641ee3310037efd` before this follow-up review-fix round; the final count must be refreshed from the new exact-head CI before release.\n- Public CI does not claim live Home Assistant smoke, E2E, or integration results for this revision; those suites require separate provider-backed evidence before certification.\n\n## [1.6.0] - 2026-06-11",
    text,
    count=1,
)
write("CHANGELOG.md", text)

replace(
    "docs/ai-skills-adoption.md",
    """## Upstream normative vocabulary conflict

The pinned capability-manifest JSON schema and the narrative capability-manifest
reference currently use partially different field vocabularies. This consumer treats
the canonical JSON schema as the executable serialization contract and records the
narrative-only concepts through reviewed extension fields where possible. The
discrepancy is an upstream residual risk and must be resolved by ai-skills before a
final certification claims that the two sources are literally identical.
""",
    """## Upstream normative vocabulary conflict

The pinned capability-manifest JSON schema is the executable serialization contract.
Its `operation_kind` is this consumer's side-effect projection; `risk`, `impact`,
`determinism`, `latency`, retry/idempotency booleans, `authorization_scopes`,
`concurrency`, and `max_response_bytes` are validated at load time. Narrative-only
axes are represented explicitly where the schema has no top-level field:
`extensions.data_classification` carries confidentiality, `extensions.cost` carries
cost, `extensions.target_binding` carries stable target identity/revalidation,
`extensions.retry_conditions` and `extensions.idempotency_mechanism` carry retry and
idempotency evidence, and `extensions.outcome_semantics` carries ambiguous/unknown
outcome handling. The invocation kernel enforces authorization scopes, target binding,
deadlines, concurrency, and response bounds; descriptive confidentiality/cost fields
are not independent authorization grants.

The narrative operational-impact vocabulary (`transient`, `persistent`, `outage`,
`safety-critical`, `financial`) and explicit abuse-potential axis do not have literal
schema equivalents in the pinned contract. They are therefore an upstream residual
risk rather than silently being equated with the schema's `impact` enum. Final
certification must not claim literal schema/narrative identity until ai-skills resolves
that mismatch or the consumer records reviewed extensions for every missing axis.
""",
)

# ---------------------------------------------------------------------------
# Test isolation and focused regressions
# ---------------------------------------------------------------------------
replace(
    "tests/unit/conftest.py",
    "import copy\n",
    "import copy\nfrom collections.abc import Iterator\n",
)
replace(
    "tests/unit/conftest.py",
    "def _restore_manifest_state():\n",
    "def _restore_manifest_state() -> Iterator[None]:\n",
)

# Rewrite the HTTP security tests with typed ASGI signatures and coverage for both middleware modes.
write(
    "tests/unit/test_http_security.py",
    '''"""ASGI HTTP request limit regressions."""

import asyncio

from starlette.types import ASGIApp, Message, Receive, Scope, Send
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
''',
)

write(
    "tests/unit/test_schema_utils.py",
    '''"""JSON-schema generation must preserve modern Python type semantics."""

from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from tools.schema_utils import signature_to_json_schema


class _Record(BaseModel):
    value: int


def test_optional_and_generic_types_are_not_flattened_to_strings() -> None:
    def operation(name: str | None = None, values: list[int] | None = None) -> None:
        return None

    schema = signature_to_json_schema(operation)
    name_schema = schema["properties"]["name"]
    values_schema = schema["properties"]["values"]
    assert {item.get("type") for item in name_schema["anyOf"]} == {"string", "null"}
    arrays = [item for item in values_schema["anyOf"] if item.get("type") == "array"]
    assert arrays and arrays[0]["items"]["type"] == "integer"
    assert name_schema["default"] is None
    assert values_schema["default"] is None
    assert schema["additionalProperties"] is False
    assert "required" not in schema


def test_nested_model_defs_are_lifted_to_root() -> None:
    def operation(records: list[_Record]) -> None:
        return None

    schema = signature_to_json_schema(operation)
    assert "_Record" in schema["$defs"]
    item = schema["properties"]["records"]["items"]
    assert item["$ref"] == "#/$defs/_Record"


def test_annotated_constraints_are_preserved() -> None:
    def operation(count: Annotated[int, Field(gt=0, description="positive count")]) -> None:
        return None

    property_schema = signature_to_json_schema(operation)["properties"]["count"]
    assert property_schema["exclusiveMinimum"] == 0
    assert property_schema["description"] == "positive count"


def test_one_unresolved_forward_ref_does_not_flatten_other_parameters() -> None:
    def operation(record: _Record, missing: "MissingModel") -> None:  # type: ignore[name-defined]
        return None

    schema = signature_to_json_schema(operation)
    assert schema["properties"]["record"]["$ref"] == "#/$defs/_Record"
    assert schema["properties"]["missing"]["type"] == "string"


def test_unrelated_schema_generator_errors_are_not_silenced(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import schema_utils

    def explode(self):
        raise RuntimeError("schema implementation bug")

    monkeypatch.setattr(schema_utils.TypeAdapter, "json_schema", explode)

    def operation(value: int) -> None:
        return None

    with pytest.raises(RuntimeError, match="schema implementation bug"):
        signature_to_json_schema(operation)
''',
)

replace(
    "tests/unit/test_response_redaction_boundary.py",
    """    assert payload["nested"]["safe_name"] == "living room"
""",
    """    assert payload["nested"]["safe_name"] == "living room"


def test_key_aware_redaction_handles_camel_and_pascal_case_credentials() -> None:
    payload = sanitize_response_data(
        {
            "accessToken": "a",
            "refreshToken": "b",
            "clientSecret": "c",
            "privateKey": "d",
            "apiKey": "e",
            "APIKey": "f",
            "safeName": "visible",
        }
    )
    for key in ("accessToken", "refreshToken", "clientSecret", "privateKey", "apiKey", "APIKey"):
        assert payload[key] == REDACTED
    assert payload["safeName"] == "visible"
""",
)

replace(
    "tests/unit/test_operation_async_hardening.py",
    "import time\n",
    "import threading\nimport time\n",
)
regex_replace(
    "tests/unit/test_operation_async_hardening.py",
    r"""@pytest\.mark\.asyncio\nasync def test_storage_coroutine_blocking_io_does_not_block_server_event_loop\(\) -> None:\n(?:    .*\n)+?    assert result\["success"\] is True\n""",
    """@pytest.mark.asyncio
async def test_storage_coroutine_blocking_io_does_not_block_server_event_loop() -> None:
    name = "test_blocking_storage_coroutine"
    _register(name)
    order: list[str] = []
    worker_started = threading.Event()

    async def blocking_tool() -> str:
        worker_started.set()
        time.sleep(0.2)
        order.append("blocking-finished")
        return json.dumps({"success": True})

    blocking_tool.__module__ = "tools.storage"
    registry = OperationRegistry()
    operation = registry.register(name, blocking_tool)
    invocation = asyncio.create_task(operation.fn())
    assert await asyncio.wait_for(asyncio.to_thread(worker_started.wait, 1), timeout=1)
    await asyncio.sleep(0)
    order.append("probe-ran")
    assert order == ["probe-ran"]
    result = json.loads(await invocation)
    assert order == ["probe-ran", "blocking-finished"]
    assert result["success"] is True
""",
    flags=re.MULTILINE,
)

replace(
    "tests/unit/test_review_regressions.py",
    """@pytest.mark.parametrize("name", ["HEALTH_CHECK_PORT", "MCP_PORT", "REST_API_PORT"])
def test_runtime_ports_reject_out_of_range(monkeypatch, name: str) -> None:
    monkeypatch.setenv(name, "70000")
    with pytest.raises(ValueError, match=name):
        RuntimeSettings.from_env()
""",
    """@pytest.mark.parametrize("name", ["HEALTH_CHECK_PORT", "MCP_PORT", "REST_API_PORT"])
@pytest.mark.parametrize("value", ["0", "70000"])
def test_runtime_ports_reject_out_of_range(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        RuntimeSettings.from_env()
""",
)

# Pin transport in the wildcard-CORS test before from_env() validates unrelated settings.
text = read("tests/unit/test_settings.py")
needle = "def test_wildcard_cors_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:\n"
if (
    needle in text
    and 'def test_wildcard_cors_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:\n    monkeypatch.setenv("MCP_TRANSPORT", "stdio")\n'
    not in text
):
    text = text.replace(needle, needle + '    monkeypatch.setenv("MCP_TRANSPORT", "stdio")\n', 1)
write("tests/unit/test_settings.py", text)

# Make the cancellation-admission test synchronize on the second acquire rather than a sleep.
replace(
    "tests/unit/test_invocation_kernel.py",
    """    waiting = asyncio.create_task(kernel.invoke_async(name, _async_ok))
    await asyncio.sleep(0.03)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    release.set()
    assert await asyncio.wait_for(first, timeout=1) == "held"
    await asyncio.sleep(0.05)
    assert await asyncio.wait_for(kernel.invoke_async(name, _async_ok), timeout=1) == "ok"
""",
    """    original_acquire = kernel._acquire_async_semaphore
    second_admission_started = asyncio.Event()
    acquire_calls = 0

    async def tracked_acquire(semaphore, timeout):
        nonlocal acquire_calls
        acquire_calls += 1
        if acquire_calls == 2:
            second_admission_started.set()
        return await original_acquire(semaphore, timeout)

    kernel._acquire_async_semaphore = tracked_acquire  # type: ignore[method-assign]
    waiting = asyncio.create_task(kernel.invoke_async(name, _async_ok))
    await asyncio.wait_for(second_admission_started.wait(), timeout=1)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    try:
        release.set()
        assert await asyncio.wait_for(first, timeout=1) == "held"
        deadline = time.monotonic() + 1
        while True:
            try:
                assert await asyncio.wait_for(kernel.invoke_async(name, _async_ok), timeout=1) == "ok"
                break
            except InvocationError as exc:
                if exc.code != "BUSY" or time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.01)
    finally:
        release.set()
        if not first.done():
            await asyncio.wait_for(first, timeout=1)
""",
)

# Cache the real cassette once at module load instead of re-reading for each request/socket.
replace(
    "tests/protocol/test_home_assistant_upstream_contract.py",
    """from context_generator.snapshot import ComprehensiveSnapshotCollector


class _RecordedHAHandler(BaseHTTPRequestHandler):
""",
    """from context_generator.snapshot import ComprehensiveSnapshotCollector

_CASSETTE = json.loads(
    Path(__file__).with_name("cassettes").joinpath("recorded_ha_upstream.json").read_text()
)


class _RecordedHAHandler(BaseHTTPRequestHandler):
""",
)
replace(
    "tests/protocol/test_home_assistant_upstream_contract.py",
    """        cassette = json.loads(
            Path(__file__).with_name("cassettes").joinpath("recorded_ha_upstream.json").read_text()
        )
        payloads = cassette["rest"]
""",
    """        payloads = _CASSETTE["rest"]
""",
)
replace(
    "tests/protocol/test_home_assistant_upstream_contract.py",
    """        cassette = json.loads(
            Path(__file__).with_name("cassettes").joinpath("recorded_ha_upstream.json").read_text()
        )
        self._cassette = cassette["websocket"]
""",
    """        self._cassette = _CASSETTE["websocket"]
""",
)
# Explicitly reject cassette drift instead of silently substituting an empty payload.
replace(
    "tests/protocol/test_home_assistant_upstream_contract.py",
    """        result = self._cassette.get(command, [])
        self.queue.append(
""",
    """        if command not in self._cassette:
            raise AssertionError(f"recorded cassette has no response for command: {command}")
        result = self._cassette[command]
        self.queue.append(
""",
)
# Exercise queued forecast events before unsubscribe acknowledgements.
replace(
    "tests/protocol/test_home_assistant_upstream_contract.py",
    """    def __init__(self) -> None:
        self.queue: deque[str] = deque([json.dumps({"type": "auth_required"})])
""",
    """    def __init__(self) -> None:
        self.queue: deque[str] = deque([json.dumps({"type": "auth_required"})])
        self._forecast_subscription: int | None = None
""",
)
replace(
    "tests/protocol/test_home_assistant_upstream_contract.py",
    """        if command == "weather/subscribe_forecast":
            self.queue.append(
""",
    """        if command == "weather/subscribe_forecast":
            self._forecast_subscription = request_id
            self.queue.append(
""",
    count=1,
)
replace(
    "tests/protocol/test_home_assistant_upstream_contract.py",
    """        if command == "unsubscribe_events":
            self.queue.append(
                json.dumps({"id": request_id, "type": "result", "success": True, "result": None})
            )
            return
""",
    """        if command == "unsubscribe_events":
            if self._forecast_subscription is not None:
                self.queue.append(
                    json.dumps(
                        {
                            "id": self._forecast_subscription,
                            "type": "event",
                            "event": {"forecast": []},
                        }
                    )
                )
            self.queue.append(
                json.dumps({"id": request_id, "type": "result", "success": True, "result": None})
            )
            self._forecast_subscription = None
            return
""",
    count=1,
)
# The cassette subclass uses the inherited tracking state and exercises the same drain path.
replace(
    "tests/protocol/test_home_assistant_upstream_contract.py",
    """        if command == "weather/subscribe_forecast":
            self.queue.append(
""",
    """        if command == "weather/subscribe_forecast":
            self._forecast_subscription = request_id
            self.queue.append(
""",
    count=1,
)
replace(
    "tests/protocol/test_home_assistant_upstream_contract.py",
    """        if command == "unsubscribe_events":
            self.queue.append(
                json.dumps({"id": request_id, "type": "result", "success": True, "result": None})
            )
            return
""",
    """        if command == "unsubscribe_events":
            if self._forecast_subscription is not None:
                self.queue.append(
                    json.dumps(
                        {
                            "id": self._forecast_subscription,
                            "type": "event",
                            "event": {"forecast": []},
                        }
                    )
                )
            self.queue.append(
                json.dumps({"id": request_id, "type": "result", "success": True, "result": None})
            )
            self._forecast_subscription = None
            return
""",
    count=1,
)

# Integration fixture teardown method has an explicit return type.
replace("tests/integration/conftest.py", "    def close(self):\n", "    def close(self) -> None:\n")

# E2E context tests mutate process globals today. Move their state into monkeypatch-managed env.
text = read("tests/e2e/test_context_generator.py")
for name in (
    "test_generate_online_mode",
    "test_generate_offline_no_api",
    "test_generate_output_overwrites",
    "test_empty_config_path_does_not_crash",
):
    text = text.replace(
        f"def {name}(self, tmp_output_path):", f"def {name}(self, tmp_output_path, monkeypatch):"
    )
text = text.replace(
    '        os.environ["HA_URL"] = _HA_URL\n', '        monkeypatch.setenv("HA_URL", _HA_URL)\n'
)
text = text.replace(
    '        os.environ["HA_TOKEN"] = _HA_TOKEN\n',
    '        monkeypatch.setenv("HA_TOKEN", _HA_TOKEN)\n',
)
text = text.replace(
    '        os.environ["OUTPUT_PATH"] = tmp_output_path\n',
    '        monkeypatch.setenv("OUTPUT_PATH", tmp_output_path)\n',
)
text = text.replace(
    '        c.HA_URL = "http://nonexistent:8123"\n        c.HA_TOKEN = ""\n        c.HA_CONFIG_PATH = _HA_CONFIG_PATH\n        c.OUTPUT_FILE = tmp_output_path\n        monkeypatch.setenv("OUTPUT_PATH", tmp_output_path)\n',
    '        monkeypatch.setenv("HA_URL", "http://nonexistent:8123")\n        monkeypatch.setenv("HA_TOKEN", "")\n        monkeypatch.setenv("HA_CONFIG_PATH", _HA_CONFIG_PATH)\n        monkeypatch.setenv("OUTPUT_PATH", tmp_output_path)\n        monkeypatch.setenv("HA_CONTEXT_MODE", "offline")\n',
)
text = text.replace(
    '        c.HA_URL = "http://nonexistent:8123"\n        c.HA_TOKEN = ""\n        c.HA_CONFIG_PATH = "/nonexistent/path"\n        c.OUTPUT_FILE = tmp_output_path\n        monkeypatch.setenv("OUTPUT_PATH", tmp_output_path)\n',
    '        monkeypatch.setenv("HA_URL", "http://nonexistent:8123")\n        monkeypatch.setenv("HA_TOKEN", "")\n        monkeypatch.setenv("HA_CONFIG_PATH", "/nonexistent/path")\n        monkeypatch.setenv("OUTPUT_PATH", tmp_output_path)\n        monkeypatch.setenv("HA_CONTEXT_MODE", "offline")\n',
)
# Replace remaining direct constants assignments in online/overwrite tests with environment setup.
text = text.replace(
    "        import context_generator.constants as c\n\n        c.HA_URL = _HA_URL\n        c.HA_TOKEN = _HA_TOKEN\n        c.HA_CONFIG_PATH = _HA_CONFIG_PATH\n        c.OUTPUT_FILE = tmp_output_path\n",
    '        monkeypatch.setenv("HA_CONFIG_PATH", _HA_CONFIG_PATH)\n',
)
text = text.replace("        import context_generator.constants as c\n\n", "")
write("tests/e2e/test_context_generator.py", text)

# Add focused CLI validation tests without subprocess or filesystem I/O.
write(
    "tests/unit/test_migration_assessment_brief.py",
    '''"""Migration-assessment CLI inputs must identify an immutable provider revision."""

import argparse

import pytest

from scripts.migration_assessment_brief import _positive_pr, _revision


def test_revision_requires_full_sha() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="40-character"):
        _revision("bf88ad0")
    assert _revision("A" * 40) == "a" * 40


def test_pr_requires_positive_integer() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="positive"):
        _positive_pr("0")
    assert _positive_pr("22") == 22
''',
)

print("Applied latest PR review source/test/documentation fixes")
