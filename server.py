#!/usr/bin/env python3
"""Home Assistant read-only MCP server composition root."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import multiprocessing
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, cast

from tools.http_security import (
    ConnectionLimitMiddleware,
    RequestLimitsMiddleware,
    StreamingRequestLimitsMiddleware,
)
from tools.invocation import (
    InvocationError,
    Principal,
    current_principal,
    principal_scope,
    set_process_principal,
)
from tools.manifests import (
    get_all_manifests,
    get_inactive_reasons,
    get_manifest,
    is_tool_active,
    set_active_tools,
)
from tools.observability import RequestIdFilter
from tools.operations import Operation, OperationMCPAdapter, OperationRegistry
from tools.schema_utils import signature_to_json_schema
from tools.security import (
    PathPolicy,
    SecurityBoundaryError,
    bearer_token_is_valid,
    resolve_output_path,
)
from tools.settings import SETTINGS
from version import __version__

CONTEXT_OUTPUT_ROOT = SETTINGS.context_output_root
CORS_ALLOWED_ORIGINS = list(SETTINGS.cors_allowed_origins)
DEV_TOOLS_ENABLED = SETTINGS.dev_tools_enabled
HA_CONFIG_PATH = SETTINGS.ha_config_path
HA_TOKEN = SETTINGS.ha_token
HA_URL = SETTINGS.ha_url
HEALTH_CHECK_PORT = SETTINGS.health_check_port
HEALTH_SERVER_ENABLED = SETTINGS.health_server_enabled
LOG_LEVEL = SETTINGS.log_level
MCP_AUTH_TOKEN = SETTINGS.mcp_auth_token
MCP_BIND_HOST = SETTINGS.mcp_bind_host
MCP_ALLOWED_HOSTS = list(SETTINGS.mcp_allowed_hosts)
MCP_HTTP_MAX_BODY_BYTES = SETTINGS.mcp_http_max_body_bytes
MCP_HTTP_MAX_HEADER_BYTES = SETTINGS.mcp_http_max_header_bytes
MCP_HTTP_CONNECTION_LIMIT = SETTINGS.mcp_http_connection_limit
MCP_HTTP_KEEPALIVE_SECONDS = SETTINGS.mcp_http_keepalive_seconds
MCP_HTTP_STATELESS = SETTINGS.mcp_http_stateless
MCP_PORT = SETTINGS.mcp_port
MCP_TRANSPORT = SETTINGS.mcp_transport
OUTPUT_PATH = SETTINGS.output_path
REST_API_ENABLED = SETTINGS.rest_api_enabled
REST_API_PORT = SETTINGS.rest_api_port
REST_API_TOKEN = SETTINGS.rest_api_token
RUN_TESTS_ON_STARTUP = SETTINGS.run_tests_on_startup

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RequestIdFilter())
_logger = logging.getLogger("ha-mcp")

_MCP_SERVER: FastMCP | None = None
_TOOL_CATALOG: dict[str, Operation] = {}
_SERVER_LOCK = threading.Lock()
HEALTH_STATE: dict[str, Any] = {
    "status": "starting",
    "ready": False,
    "started_at": time.time(),
    "components": {
        "catalog": "starting",
        "transport": "starting",
        "filesystem": "unknown",
        "backend": "unknown",
        "rest": "disabled" if not REST_API_ENABLED else "starting",
    },
}
_HEALTH_LOCK = threading.Lock()


def _set_health_component(name: str, status: str, **details: Any) -> None:
    with _HEALTH_LOCK:
        components = HEALTH_STATE.setdefault("components", {})
        components[name] = status
        if details:
            HEALTH_STATE.setdefault("component_details", {})[name] = details
        required = ["catalog", "transport", "filesystem"]
        if SETTINGS.backend_required_for_ready:
            required.append("backend")
        if REST_API_ENABLED:
            required.append("rest")
        ready = all(components.get(item) == "ready" for item in required)
        HEALTH_STATE["ready"] = bool(ready)
        HEALTH_STATE["status"] = "ready" if HEALTH_STATE["ready"] else "not_ready"


def _probe_backend() -> dict[str, Any]:
    """Probe the Home Assistant API and return component detail entries."""
    try:
        import requests

        response = requests.get(
            f"{HA_URL.rstrip('/')}/api/",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            timeout=2,
        )
        response.raise_for_status()
    except Exception as exc:
        return {"configured": True, "reachable": False, "reason": type(exc).__name__}
    return {"configured": True, "reachable": True}


def _initialize_runtime_health() -> None:
    config_root = Path(HA_CONFIG_PATH)
    filesystem_status = "ready" if config_root.is_dir() else "failed"
    _set_health_component(
        "filesystem", filesystem_status, path=str(config_root), readable=config_root.is_dir()
    )

    if not HA_TOKEN:
        _set_health_component("backend", "degraded", configured=False, reachable=False)
        HEALTH_STATE["capability_degradation"] = {
            "ha.read": "Home Assistant token is not configured"
        }
        _refresh_active_catalog()
        return
    # One bounded probe establishes the initial capability profile without
    # delaying MCP/REST transport startup for an unreachable backend. The
    # background reconciler performs subsequent retries and promotes tools when
    # Home Assistant becomes reachable.
    detail = _probe_backend()
    if detail["reachable"]:
        _set_health_component("backend", "ready", **detail)
        HEALTH_STATE.pop("capability_degradation", None)
    else:
        _set_health_component("backend", "degraded", **detail)
        HEALTH_STATE["capability_degradation"] = {
            "ha.read": "Home Assistant backend is unavailable"
        }
    _refresh_active_catalog()


def _network_auth_provider() -> Any | None:
    if MCP_TRANSPORT != "http":
        return None
    if not MCP_AUTH_TOKEN:
        return None
    from tools.auth import ConfiguredBearerTokenVerifier

    return ConfiguredBearerTokenVerifier(MCP_AUTH_TOKEN)


class RequestPrincipalMiddleware(Middleware):
    """Bind kernel authorization to the access token of each MCP request."""

    async def __call__(self, context: Any, call_next: Any) -> Any:
        if getattr(context, "method", None) != "tools/call":
            return await call_next(context)
        from fastmcp.server.dependencies import get_access_token

        access_token = get_access_token()
        if access_token is None:
            principal = (
                Principal(
                    subject="local-mcp-client",
                    transport="stdio",
                    capabilities=frozenset({"ha.read", "filesystem.read", "artifact.read"}),
                    targets=frozenset({"runtime", "home_assistant", "home_assistant_config"}),
                )
                if MCP_TRANSPORT == "stdio"
                else Principal(
                    subject="unauthenticated-network-client",
                    transport=MCP_TRANSPORT,
                    capabilities=frozenset(),
                    targets=frozenset(),
                )
            )
        else:
            principal = Principal(
                subject=str(
                    access_token.subject or access_token.client_id or "authenticated-client"
                ),
                transport=MCP_TRANSPORT,
                capabilities=frozenset(access_token.scopes or []),
                targets=frozenset({"runtime", "home_assistant", "home_assistant_config"}),
                credential_id=str(
                    access_token.client_id or access_token.subject or "configured-token"
                ),
            )
        with principal_scope(principal):
            return await call_next(context)


def create_mcp_server() -> FastMCP:
    """Build a fully registered server without network or filesystem startup side effects."""
    server = FastMCP(
        "HA-Observer",
        version=__version__,
        auth=_network_auth_provider(),
        middleware=[RequestPrincipalMiddleware()],
        mask_error_details=True,
        strict_input_validation=True,
    )

    registry = OperationRegistry()
    operations = OperationMCPAdapter(server, registry)

    from tools.areas import register_area_tools
    from tools.automations import register_automation_tools
    from tools.batch_operations import register_batch_operations_tools
    from tools.blueprints import register_blueprint_tools
    from tools.capabilities import register_capability_tools
    from tools.categories import register_categories_tools
    from tools.composite import register_composite_tools
    from tools.config import register_config_tools
    from tools.config_entries import register_config_entry_tools
    from tools.dev_tools import register_dev_tools
    from tools.devices import register_device_tools
    from tools.diagnostics import register_diagnostics_tools
    from tools.entity_context import register_entity_context_tools
    from tools.entity_dependencies import register_entity_dependency_tools
    from tools.filesystem_explorer import register_filesystem_tools
    from tools.graph_tools import register_graph_tools
    from tools.health_reporter import register_health_reporter_tools
    from tools.helpers_health import register_helpers_health_tools
    from tools.history import register_history_tools
    from tools.integrations import register_integration_tools
    from tools.logs import register_log_tools
    from tools.scenes import register_scene_tools
    from tools.scripts import register_script_tools
    from tools.states import register_state_tools
    from tools.storage import register_storage_tools

    register_state_tools(operations, HA_URL, HA_TOKEN, HA_CONFIG_PATH)
    register_automation_tools(operations, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    register_script_tools(operations, HA_CONFIG_PATH)
    register_scene_tools(operations, HA_CONFIG_PATH)
    register_blueprint_tools(operations, HA_CONFIG_PATH)
    register_config_tools(operations, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    register_log_tools(operations, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    register_storage_tools(operations, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    register_diagnostics_tools(operations, HA_URL, HA_TOKEN, HA_CONFIG_PATH)
    register_health_reporter_tools(operations, HA_URL, HA_TOKEN, HA_CONFIG_PATH)
    register_filesystem_tools(operations, HA_CONFIG_PATH)
    register_graph_tools(operations, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    register_composite_tools(operations, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    if DEV_TOOLS_ENABLED:
        register_dev_tools(operations, HA_URL, HA_TOKEN, HA_CONFIG_PATH)
    register_config_entry_tools(operations, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    register_device_tools(operations, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    register_entity_dependency_tools(operations, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    register_entity_context_tools(operations, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    register_history_tools(operations, HA_URL, HA_TOKEN)
    register_area_tools(operations, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    register_integration_tools(operations, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    register_batch_operations_tools(operations, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    register_categories_tools(operations, HA_CONFIG_PATH)
    register_helpers_health_tools(operations, HA_URL, HA_TOKEN)
    register_capability_tools(operations)

    registered = registry.names()
    missing = sorted(name for name in registered if get_manifest(name) is None)
    if missing:
        raise RuntimeError("Missing explicit tool manifests: " + ", ".join(missing))
    setattr(server, "_ha_operation_registry", registry)
    return server


def _refresh_active_catalog() -> None:
    """Derive the active profile from registered operations and dependency state."""
    if not _TOOL_CATALOG:
        return
    components = HEALTH_STATE.get("components", {})
    backend = components.get("backend")
    filesystem = components.get("filesystem")
    active: set[str] = set()
    reasons: dict[str, str] = {}
    for name in _TOOL_CATALOG:
        manifest = get_manifest(name)
        if manifest is None:
            continue
        binding = manifest.get("extensions", {}).get("target_binding", {})
        target = binding.get("target") if isinstance(binding, dict) else None
        if target == "runtime":
            active.add(name)
        elif target == "home_assistant_config":
            if filesystem == "ready":
                active.add(name)
            else:
                reasons[name] = "Home Assistant configuration root is unavailable"
        elif target == "home_assistant":
            if backend == "ready":
                active.add(name)
            else:
                reasons[name] = "Home Assistant backend is unavailable"
        else:
            reasons[name] = "Unknown or unsupported target binding"
    for name in set(get_all_manifests()) - set(_TOOL_CATALOG):
        reasons.setdefault(name, "Capability is not registered in the active deployment profile")
    set_active_tools(active, reasons)
    HEALTH_STATE["active_tool_count"] = len(active)


def get_mcp_server() -> FastMCP:
    global _MCP_SERVER, _TOOL_CATALOG
    if _MCP_SERVER is None:
        with _SERVER_LOCK:
            if _MCP_SERVER is None:
                built = create_mcp_server()
                registry = getattr(built, "_ha_operation_registry")
                if not isinstance(registry, OperationRegistry):
                    raise RuntimeError("MCP server has no application-owned operation registry")
                _TOOL_CATALOG = registry.all()
                _MCP_SERVER = built
                HEALTH_STATE["tool_count"] = len(_TOOL_CATALOG)
                _set_health_component("catalog", "ready", tool_count=len(_TOOL_CATALOG))
                # Composition is intentionally dependency-agnostic. Runtime startup
                # applies dependency health and may reduce this supported catalog.
                set_active_tools(set(_TOOL_CATALOG))
                HEALTH_STATE["active_tool_count"] = len(_TOOL_CATALOG)
    return _MCP_SERVER


def get_all_tools() -> dict[str, Operation]:
    get_mcp_server()
    return dict(_TOOL_CATALOG)


def get_tool(name: str) -> Operation | None:
    return get_all_tools().get(name)


def get_tool_count() -> int:
    return len(get_all_tools())


def _extract_fn(tool: Any) -> Any | None:
    """Return the application-owned callable without inspecting FastMCP internals."""
    function = getattr(tool, "fn", None)
    if callable(function):
        return function
    return tool if callable(tool) else None


def _extract_desc(tool: Operation) -> str:
    return tool.description.split("\n", maxsplit=1)[0].rstrip(".")


def _signature_to_json_schema(function: Any) -> dict[str, Any]:
    return signature_to_json_schema(function)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/health", "/live", "/ready"}:
            self.send_response(404)
            self.end_headers()
            return
        ready = bool(HEALTH_STATE.get("ready"))
        payload: dict[str, Any]
        if self.path == "/live":
            payload = {"status": "live", "version": __version__}
            status = 200
        elif self.path == "/ready":
            payload = {"status": "ready" if ready else "not_ready", "version": __version__}
            status = 200 if ready else 503
        else:
            payload = {"status": "ready" if ready else "not_ready", "version": __version__}
            status = 200 if ready else 503
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def start_health_server(port: int = HEALTH_CHECK_PORT) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((MCP_BIND_HOST, port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True, name="health-server").start()
    return server


def _context_generation_worker(
    connection: Connection,
    config_path: str,
    output_path: str,
    ha_url: str,
    ha_token: str,
    mode: str,
) -> None:
    """Run generation in a killable child process and return a sanitized result."""
    try:
        from context_generator import generate_context_file

        result = generate_context_file(
            config_path=config_path,
            output_path=output_path,
            ha_url=ha_url,
            ha_token=ha_token,
            mode=cast(Any, mode),
        )
        connection.send(("ok", result))
    except BaseException as exc:
        connection.send(("error", type(exc).__name__))
    finally:
        connection.close()


@dataclass
class GenerationTask:
    task_id: str
    owner: str
    output_path: Path
    started_at: float
    future: Future[dict[str, Any]]
    mode: str


@dataclass
class ContextTaskManager:
    timeout_seconds: int = 300
    _executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=1, thread_name_prefix="context")
    )
    _task: GenerationTask | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _generate(self, config_path: Path, output_path: Path, mode: str) -> dict[str, Any]:
        output_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        temporary = output_path.with_name(
            f".{output_path.stem}.{uuid.uuid4().hex}{output_path.suffix}"
        )
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        process = context.Process(
            target=_context_generation_worker,
            args=(
                send,
                str(config_path),
                str(temporary),
                HA_URL if mode in {"online", "hybrid"} else "",
                HA_TOKEN if mode in {"online", "hybrid"} else "",
                mode,
            ),
            name="ha-context-generator",
        )
        try:
            process.start()
            send.close()
            process.join(self.timeout_seconds)
            if process.is_alive():
                process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)
                raise TimeoutError("Context generation exceeded its deadline")
            if not receive.poll(1):
                raise RuntimeError("Context generator exited without a result")
            status, payload = receive.recv()
            if status != "ok":
                raise RuntimeError(f"Context generation failed: {payload}")
            if not isinstance(payload, dict) or not temporary.is_file():
                raise RuntimeError("Context generator returned an invalid artifact")
            temporary.chmod(0o640)
            temporary.replace(output_path)
            payload["output_file"] = str(output_path)
            return cast(dict[str, Any], payload)
        finally:
            receive.close()
            if process.is_alive():
                process.kill()
                process.join(5)
            process.close()
            temporary.unlink(missing_ok=True)

    def start(self, config_path: str, output_path: str, mode: str, owner: str) -> GenerationTask:
        if mode not in {"offline", "online", "hybrid"}:
            raise ValueError("mode must be offline, online, or hybrid")
        config_policy = PathPolicy.from_paths(
            [Path(HA_CONFIG_PATH)], max_file_size=20 * 1024 * 1024, deny_storage=False
        )
        safe_config = config_policy.resolve(config_path, require_directory=True)
        safe_output = resolve_output_path(output_path, CONTEXT_OUTPUT_ROOT)
        with self._lock:
            if self._task is not None and not self._task.future.done():
                raise RuntimeError("Context generation is already running")
            future = self._executor.submit(self._generate, safe_config, safe_output, mode)
            self._task = GenerationTask(
                task_id=uuid.uuid4().hex,
                owner=owner,
                output_path=safe_output,
                started_at=time.monotonic(),
                future=future,
                mode=mode,
            )
            return self._task

    def status(self, owner: str) -> dict[str, Any]:
        with self._lock:
            task = self._task
        if task is None:
            return {"status": "idle"}
        if task.owner != owner:
            raise PermissionError("Task belongs to another principal")
        elapsed = time.monotonic() - task.started_at
        if not task.future.done() and elapsed > self.timeout_seconds:
            return {"status": "deadline_exceeded", "task_id": task.task_id}
        if not task.future.done():
            return {"status": "running", "task_id": task.task_id, "mode": task.mode}
        exception = task.future.exception()
        if exception is not None:
            return {"status": "error", "task_id": task.task_id, "error": "Generation failed"}
        return {
            "status": "completed",
            "task_id": task.task_id,
            "output_path": str(task.output_path),
            "stats": task.future.result(),
        }

    def output_for(self, owner: str) -> Path:
        with self._lock:
            task = self._task
        if task is None or task.owner != owner or not task.future.done() or task.future.exception():
            raise FileNotFoundError("No completed context artifact")
        return resolve_output_path(task.output_path, CONTEXT_OUTPUT_ROOT)


_CONTEXT_TASKS = ContextTaskManager()


def create_rest_app(auth_token: str | None = None) -> Any:
    """Create the optional authenticated REST compatibility adapter."""
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.requests import Request
    from starlette.responses import FileResponse, JSONResponse
    from starlette.routing import Route

    token = REST_API_TOKEN if auth_token is None else auth_token
    if not token:
        raise ValueError("REST_API_TOKEN or MCP_AUTH_TOKEN is required for the REST adapter")

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Any) -> Any:
            if request.method == "OPTIONS" or request.url.path in {"/health", "/api/health"}:
                return await call_next(request)
            if not bearer_token_is_valid(request.headers, token):
                return JSONResponse(
                    {
                        "success": False,
                        "error": {"code": "UNAUTHORIZED", "message": "Bearer token required"},
                    },
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            principal = Principal(
                subject="authenticated-rest-client",
                transport="rest",
                capabilities=frozenset({"ha.read", "filesystem.read", "artifact.read"}),
                targets=frozenset({"runtime", "home_assistant", "home_assistant_config"}),
                credential_id="configured-rest-token",
            )
            with principal_scope(principal):
                return await call_next(request)

    async def health(request: Request) -> JSONResponse:
        ready = bool(HEALTH_STATE.get("ready"))
        return JSONResponse(
            {"status": "ready" if ready else "not_ready", "version": __version__},
            # `/ready` is the readiness gate. Public `/health` is deliberately
            # minimal and liveness-like so it does not expose dependency detail.
            status_code=200,
        )

    async def health_details(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": HEALTH_STATE.get("status", "unknown"),
                "version": __version__,
                "ready": bool(HEALTH_STATE.get("ready")),
                "tool_count": get_tool_count(),
                "active_tool_count": HEALTH_STATE.get("active_tool_count", 0),
                "components": HEALTH_STATE.get("components", {}),
                "component_details": HEALTH_STATE.get("component_details", {}),
                "capability_degradation": HEALTH_STATE.get("capability_degradation", {}),
            }
        )

    async def list_tools_endpoint(request: Request) -> JSONResponse:
        full = request.query_params.get("detail") == "full"
        entries = []
        inactive_reasons = get_inactive_reasons()
        for name, tool in sorted(get_all_tools().items()):
            entry: dict[str, Any] = {
                "name": name,
                "description": _extract_desc(tool),
                "active": is_tool_active(name),
            }
            if not entry["active"]:
                entry["inactive_reason"] = inactive_reasons.get(name, "Capability is inactive")
            if full:
                function = _extract_fn(tool)
                entry["parameters"] = _signature_to_json_schema(function) if function else {}
                entry["manifest"] = get_manifest(name)
            entries.append(entry)
        return JSONResponse({"success": True, "total": len(entries), "tools": entries})

    async def call_tool_endpoint(request: Request) -> JSONResponse:
        tool_name = request.path_params["tool_name"]
        tool = get_tool(tool_name)
        if tool is None:
            return JSONResponse(
                {"success": False, "error": {"code": "NOT_FOUND", "message": "Tool not found"}},
                status_code=404,
            )
        chunks: list[bytes] = []
        body_size = 0
        async for chunk in request.stream():
            body_size += len(chunk)
            if body_size > 1024 * 1024:
                return JSONResponse(
                    {
                        "success": False,
                        "error": {
                            "code": "REQUEST_TOO_LARGE",
                            "message": "Request body exceeds 1 MiB",
                        },
                    },
                    status_code=413,
                )
            chunks.append(chunk)
        body = b"".join(chunks)
        try:
            arguments = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return JSONResponse(
                {
                    "success": False,
                    "error": {"code": "INVALID_JSON", "message": "JSON object required"},
                },
                status_code=400,
            )
        if not isinstance(arguments, dict):
            return JSONResponse(
                {
                    "success": False,
                    "error": {"code": "INVALID_ARGUMENTS", "message": "JSON object required"},
                },
                status_code=400,
            )
        function = _extract_fn(tool)
        if function is None:
            return JSONResponse(
                {
                    "success": False,
                    "error": {"code": "NOT_CALLABLE", "message": "Tool is unavailable"},
                },
                status_code=500,
            )
        try:
            inspect.signature(function).bind(**arguments)
        except TypeError:
            return JSONResponse(
                {
                    "success": False,
                    "error": {
                        "code": "INVALID_ARGUMENTS",
                        "message": "Arguments do not match schema",
                    },
                },
                status_code=400,
            )
        try:
            result = (
                await function(**arguments)
                if inspect.iscoroutinefunction(function)
                else await asyncio.to_thread(function, **arguments)
            )
        except InvocationError as exc:
            status = {
                "FORBIDDEN": 403,
                "BUSY": 429,
                "DEADLINE_EXCEEDED": 504,
                "UNAVAILABLE_DEPENDENCY": 503,
            }.get(exc.code, 502)
            return JSONResponse(
                {"success": False, "error": {"code": exc.code, "message": str(exc)}},
                status_code=status,
            )
        except Exception:
            _logger.exception("REST tool invocation failed: %s", tool_name)
            return JSONResponse(
                {
                    "success": False,
                    "error": {"code": "INTERNAL_ERROR", "message": "Tool invocation failed"},
                },
                status_code=500,
            )
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                pass
        return JSONResponse({"success": True, "tool": tool_name, "result": result})

    async def tool_manifest(request: Request) -> JSONResponse:
        name = request.path_params["tool_name"]
        manifest = get_manifest(name)
        if manifest is None:
            return JSONResponse(
                {"success": False, "error": {"code": "NOT_FOUND", "message": "Manifest not found"}},
                status_code=404,
            )
        runtime_manifest = dict(manifest)
        runtime_manifest["runtime_active"] = is_tool_active(name)
        if not runtime_manifest["runtime_active"]:
            runtime_manifest["inactive_reason"] = get_inactive_reasons().get(
                name, "Capability is inactive"
            )
        return JSONResponse({"success": True, "manifest": runtime_manifest})

    async def tool_schema(request: Request) -> JSONResponse:
        name = request.path_params["tool_name"]
        tool = get_tool(name)
        if tool is None:
            return JSONResponse(
                {"success": False, "error": {"code": "NOT_FOUND", "message": "Tool not found"}},
                status_code=404,
            )
        function = _extract_fn(tool)
        return JSONResponse(
            {
                "success": True,
                "name": name,
                "description": _extract_desc(tool),
                "parameters": _signature_to_json_schema(function) if function else {},
                "manifest": get_manifest(name),
            }
        )

    async def context_generate(request: Request) -> JSONResponse:
        try:
            params = await request.json()
        except (json.JSONDecodeError, TypeError):
            params = {}
        if not isinstance(params, dict):
            return JSONResponse(
                {
                    "success": False,
                    "error": {"code": "INVALID_ARGUMENTS", "message": "JSON object required"},
                },
                status_code=400,
            )
        try:
            task = _CONTEXT_TASKS.start(
                str(params.get("config_path", HA_CONFIG_PATH)),
                str(params.get("output_path", OUTPUT_PATH)),
                str(params.get("mode", "hybrid")),
                current_principal().subject,
            )
        except (ValueError, SecurityBoundaryError):
            return JSONResponse(
                {
                    "success": False,
                    "error": {
                        "code": "INVALID_PATH",
                        "message": "Invalid or disallowed context path",
                    },
                },
                status_code=400,
            )
        except RuntimeError as exc:
            return JSONResponse(
                {"success": False, "error": {"code": "CONFLICT", "message": str(exc)}},
                status_code=409,
            )
        return JSONResponse(
            {"success": True, "task_id": task.task_id, "status": "running"}, status_code=202
        )

    async def context_status(request: Request) -> JSONResponse:
        try:
            status = _CONTEXT_TASKS.status(current_principal().subject)
        except PermissionError:
            return JSONResponse(
                {
                    "success": False,
                    "error": {"code": "FORBIDDEN", "message": "Task is not accessible"},
                },
                status_code=403,
            )
        return JSONResponse({"success": True, **status})

    async def context_download(request: Request) -> Any:
        try:
            output = _CONTEXT_TASKS.output_for(current_principal().subject)
        except (FileNotFoundError, OSError, SecurityBoundaryError):
            return JSONResponse(
                {
                    "success": False,
                    "error": {"code": "NOT_FOUND", "message": "Context artifact not available"},
                },
                status_code=404,
            )
        if request.query_params.get("format") == "json":
            try:
                content = output.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return JSONResponse(
                    {
                        "success": False,
                        "error": {"code": "NOT_FOUND", "message": "Context artifact not available"},
                    },
                    status_code=404,
                )
            return JSONResponse(
                {"success": True, "content": content, "size_bytes": len(content.encode())}
            )
        return FileResponse(output, media_type="text/markdown", filename=output.name)

    async def context_modes(request: Request) -> JSONResponse:
        return JSONResponse({"modes": ["offline", "online", "hybrid"]})

    async def openapi_schema(request: Request) -> JSONResponse:
        paths: dict[str, Any] = {
            "/api/tools": {"get": {"summary": "List tools", "security": [{"bearerAuth": []}]}},
        }
        for name, operation in sorted(get_all_tools().items()):
            paths[f"/api/tools/{name}"] = {
                "post": {"summary": _extract_desc(operation), "security": [{"bearerAuth": []}]}
            }
        return JSONResponse(
            {
                "openapi": "3.0.3",
                "info": {"title": "HA-Observer REST compatibility API", "version": __version__},
                "components": {
                    "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}
                },
                "paths": paths,
            }
        )

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/api/health", health, methods=["GET"]),
        Route("/api/health/details", health_details, methods=["GET"]),
        Route("/api/tools", list_tools_endpoint, methods=["GET"]),
        Route("/api/tools/{tool_name}", call_tool_endpoint, methods=["POST"]),
        Route("/api/tools/{tool_name}/manifest", tool_manifest, methods=["GET"]),
        Route("/api/tools/{tool_name}/schema", tool_schema, methods=["GET"]),
        Route("/api/openapi.json", openapi_schema, methods=["GET"]),
        Route("/api/context/generate", context_generate, methods=["POST"]),
        Route("/api/context/status", context_status, methods=["GET"]),
        Route("/api/context/download", context_download, methods=["GET"]),
        Route("/api/context/modes", context_modes, methods=["GET"]),
    ]
    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=[*MCP_ALLOWED_HOSTS, "testserver"]),
        Middleware(
            RequestLimitsMiddleware,
            max_body_bytes=MCP_HTTP_MAX_BODY_BYTES,
            max_header_bytes=MCP_HTTP_MAX_HEADER_BYTES,
        ),
        Middleware(BearerAuthMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=CORS_ALLOWED_ORIGINS,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["authorization", "content-type"],
        ),
    ]

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        del app
        _set_health_component("rest", "ready")
        try:
            yield
        finally:
            if REST_API_ENABLED:
                _set_health_component("rest", "failed")

    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)


def run_mcp_http(server: FastMCP) -> None:
    """Run the authenticated Streamable HTTP app with explicit transport limits."""
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    import uvicorn

    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=MCP_ALLOWED_HOSTS),
        Middleware(ConnectionLimitMiddleware, limit=MCP_HTTP_CONNECTION_LIMIT),
        Middleware(
            StreamingRequestLimitsMiddleware,
            max_body_bytes=MCP_HTTP_MAX_BODY_BYTES,
            max_header_bytes=MCP_HTTP_MAX_HEADER_BYTES,
        ),
        Middleware(
            CORSMiddleware,
            allow_origins=CORS_ALLOWED_ORIGINS,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "authorization",
                "content-type",
                "mcp-protocol-version",
                "mcp-session-id",
            ],
            expose_headers=["mcp-session-id"],
        ),
    ]
    app = server.http_app(
        path="/mcp",
        middleware=middleware,
        stateless_http=MCP_HTTP_STATELESS,
    )
    uvicorn.run(
        app,
        host=MCP_BIND_HOST,
        port=MCP_PORT,
        log_level=LOG_LEVEL.lower(),
        access_log=LOG_LEVEL.upper() == "DEBUG",
        timeout_keep_alive=MCP_HTTP_KEEPALIVE_SECONDS,
        h11_max_incomplete_event_size=MCP_HTTP_MAX_HEADER_BYTES,
    )


def run_rest_api() -> None:
    import uvicorn

    uvicorn.run(
        create_rest_app(),
        host=MCP_BIND_HOST,
        port=REST_API_PORT,
        log_level="warning",
        access_log=False,
    )


def run_startup_tests() -> bool:
    tests_dir = Path(__file__).resolve().parent / "tests" / "unit"
    if not tests_dir.is_dir():
        _logger.warning("Startup tests requested but unit tests are not installed; skipping")
        return True
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(tests_dir), "-q", "-p", "no:cacheprovider"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        _logger.error("Startup tests exceeded the 300 second deadline")
        return False
    if result.returncode:
        _logger.error("Startup tests failed")
    return result.returncode == 0


def validate_runtime_config() -> None:
    network_transport = MCP_TRANSPORT == "http"
    if network_transport and not MCP_AUTH_TOKEN:
        raise RuntimeError("MCP_AUTH_TOKEN is required for Streamable HTTP transport")
    if REST_API_ENABLED and not REST_API_TOKEN:
        raise RuntimeError("REST_API_TOKEN or MCP_AUTH_TOKEN is required when REST_API_ENABLED=1")
    if MCP_BIND_HOST not in {"127.0.0.1", "localhost", "::1"} and not MCP_AUTH_TOKEN:
        raise RuntimeError("Non-loopback binding requires MCP_AUTH_TOKEN")


def _probe_network_transport() -> None:
    deadline = time.monotonic() + 20
    probe_host = (
        "127.0.0.1" if MCP_BIND_HOST in {"0.0.0.0", "::"} else MCP_BIND_HOST  # nosec B104 -- comparison only
    )
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((probe_host, MCP_PORT), timeout=1):
                _set_health_component("transport", "ready", port=MCP_PORT)
                return
        except OSError:
            time.sleep(0.1)
    _set_health_component("transport", "failed", port=MCP_PORT)


def _reconcile_backend_health() -> None:
    """Re-probe a degraded Home Assistant backend until it becomes reachable."""
    interval = 15
    while HEALTH_STATE.get("components", {}).get("backend") != "ready":
        time.sleep(interval)
        if not HA_TOKEN:
            return
        detail = _probe_backend()
        if detail["reachable"]:
            _set_health_component("backend", "ready", **detail)
            HEALTH_STATE.pop("capability_degradation", None)
            _refresh_active_catalog()
            return
        _set_health_component("backend", "degraded", **detail)


def main() -> None:
    validate_runtime_config()
    # Build the registered catalog first, then evaluate dependency health so
    # runtime activation can reduce the supported deployment profile.
    server = get_mcp_server()
    _initialize_runtime_health()
    if not HA_TOKEN:
        _logger.warning("HA_TOKEN is not set; Home Assistant API operations will fail closed")
    if HEALTH_STATE.get("components", {}).get("backend") == "degraded":
        threading.Thread(
            target=_reconcile_backend_health, daemon=True, name="backend-health-reconcile"
        ).start()
    if RUN_TESTS_ON_STARTUP and not run_startup_tests():
        raise SystemExit("Startup tests failed")
    if HEALTH_SERVER_ENABLED:
        start_health_server()
    if REST_API_ENABLED:
        threading.Thread(target=run_rest_api, daemon=True, name="rest-api").start()

    if MCP_TRANSPORT == "stdio":
        set_process_principal(
            Principal(
                subject="local-parent-process",
                transport="stdio",
                capabilities=frozenset({"ha.read", "filesystem.read", "artifact.read"}),
                targets=frozenset({"runtime", "home_assistant", "home_assistant_config"}),
            )
        )
        _set_health_component("transport", "ready", transport="stdio")
        server.run(transport="stdio", show_banner=False)
        return

    set_process_principal(
        Principal(
            subject="unauthenticated-network-default",
            transport=MCP_TRANSPORT,
            capabilities=frozenset(),
            targets=frozenset(),
        )
    )
    threading.Thread(
        target=_probe_network_transport, daemon=True, name="mcp-transport-probe"
    ).start()
    run_mcp_http(server)


if __name__ == "__main__":
    main()
