#!/usr/bin/env python3
"""
Home Assistant MCP Server
Model Context Protocol server for AI-assisted Home Assistant management.

Architecture:
- Port 9091: Health check (lightweight HTTP server)
- Port 9092: MCP SSE transport (for LibreChat) - /sse, /messages
- Port 9093: REST API (Starlette) - /api/*
"""

import inspect
import json
import os
import subprocess
import sys
import threading
import time
from fastmcp import FastMCP
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional

from __init__ import __version__
from tools.areas import register_area_tools  # noqa: E402
from tools.automations import register_automation_tools  # noqa: E402
from tools.batch_operations import register_batch_operations_tools  # noqa: E402
from tools.blueprints import register_blueprint_tools  # noqa: E402
from tools.composite import register_composite_tools  # noqa: E402
from tools.config import register_config_tools  # noqa: E402
from tools.config_entries import register_config_entry_tools  # noqa: E402
from tools.dev_tools import register_dev_tools  # noqa: E402
from tools.devices import register_device_tools  # noqa: E402
from tools.diagnostics import register_diagnostics_tools  # noqa: E402
from tools.entity_context import register_entity_context_tools  # noqa: E402
from tools.entity_dependencies import register_entity_dependency_tools  # noqa: E402
from tools.filesystem_explorer import register_filesystem_tools  # noqa: E402
from tools.health_reporter import register_health_reporter_tools  # noqa: E402
from tools.history import register_history_tools  # noqa: E402
from tools.integrations import register_integration_tools  # noqa: E402
from tools.logs import register_log_tools  # noqa: E402
from tools.scenes import register_scene_tools  # noqa: E402
from tools.scripts import register_script_tools  # noqa: E402

# Tool imports (placed at top; registration happens after config)
from tools.states import register_state_tools  # noqa: E402
from tools.storage import register_storage_tools  # noqa: E402

# =============================================================================
# HEALTH CHECK SERVER (port 9091)
# =============================================================================

HEALTH_STATE = {"status": "starting", "last_heartbeat": time.time()}


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(HEALTH_STATE).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_server(port=9091):
    """Start lightweight HTTP server for health checks."""
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True, name="HealthServer").start()
    print(f"[health] HTTP health endpoint started on port {port}")
    return server


# =============================================================================
# CONFIGURATION
# =============================================================================

HA_URL = os.getenv("HA_URL", "http://homeassistant:8123")
HA_TOKEN = os.getenv("HA_TOKEN")
HA_CONFIG_PATH = os.getenv("HA_CONFIG_PATH", "/config")
DEV_TOOLS_ENABLED = os.getenv("MCP_DEV_TOOLS_ENABLED", "1").lower() in (
    "1",
    "true",
    "yes",
)
RUN_TESTS_ON_STARTUP = os.getenv("RUN_TESTS_ON_STARTUP", "0").lower() in (
    "1",
    "true",
    "yes",
)

# PORTS
HEALTH_CHECK_PORT = int(os.getenv("HEALTH_CHECK_PORT", "9091"))
MCP_SSE_PORT = int(os.getenv("MCP_SSE_PORT", "9092"))
REST_API_PORT = int(os.getenv("REST_API_PORT", "9093"))

if not HA_TOKEN:
    print(
        "[server] WARNING: HA_TOKEN not set - some features will be disabled",
        file=sys.stderr,
    )

# =============================================================================
# INITIALIZE MCP SERVER
# =============================================================================

mcp = FastMCP("HA-Observer")

# =============================================================================
# REGISTER ALL TOOLS
# =============================================================================

register_state_tools(mcp, HA_URL, HA_TOKEN)

register_automation_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

register_script_tools(mcp, HA_CONFIG_PATH)

register_scene_tools(mcp, HA_CONFIG_PATH)

register_blueprint_tools(mcp, HA_CONFIG_PATH)

register_config_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

register_log_tools(mcp, HA_CONFIG_PATH)

register_storage_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

register_diagnostics_tools(mcp, HA_URL, HA_TOKEN, HA_CONFIG_PATH)

register_health_reporter_tools(mcp, HA_URL, HA_TOKEN, HA_CONFIG_PATH)

register_filesystem_tools(mcp)

register_composite_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

if DEV_TOOLS_ENABLED:
    register_dev_tools(mcp, HA_URL, HA_TOKEN, HA_CONFIG_PATH)
    print("[server] Dev tools: ENABLED")
else:
    print("[server] Dev tools: DISABLED")

register_config_entry_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

register_device_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

register_entity_dependency_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

register_entity_context_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

register_history_tools(mcp, HA_URL, HA_TOKEN)

register_area_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

register_integration_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

register_batch_operations_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)


# =============================================================================
# TOOL HELPERS
# =============================================================================


def get_all_tools() -> Dict[str, Any]:
    """Return a dictionary of all registered tools keyed by tool name."""
    try:
        raw = mcp._local_provider._components
        return {
            k.removeprefix("tool:").removesuffix("@"): v
            for k, v in raw.items()
        }
    except AttributeError:
        pass
    try:
        return mcp._tool_manager._tools
    except AttributeError:
        pass
    return {}


def get_tool(name: str) -> Optional[Any]:
    """Return tool by name if available."""
    return get_all_tools().get(name)


def get_tool_count() -> int:
    """Return the number of registered tools."""
    return len(get_all_tools())


tool_count = get_tool_count()


# =============================================================================
# CONTEXT GENERATOR INTEGRATION
# =============================================================================

OUTPUT_PATH = os.getenv("OUTPUT_PATH", "/app/output/ha-ai-context.md")

_generation_state = {
    "status": "idle",
    "started_at": None,
    "completed_at": None,
    "output_path": None,
    "error": None,
    "stats": {},
}


def _run_context_generation(config_path: str, output_path: str, mode: str):
    """Run context generation in background thread."""
    global _generation_state
    try:
        from context_generator import generate_context_file

        stats = generate_context_file(
            config_path=config_path,
            output_path=output_path,
            ha_url=HA_URL if mode in ("online", "hybrid") else None,
            ha_token=HA_TOKEN if mode in ("online", "hybrid") else None,
            mode=mode,
        )
        _generation_state["status"] = "completed"
        _generation_state["completed_at"] = time.time()
        _generation_state["stats"] = stats
        print(f"[generator] Context generation completed: {output_path}")
    except Exception as exc:
        _generation_state["status"] = "error"
        _generation_state["error"] = str(exc)
        _generation_state["completed_at"] = time.time()
        print(f"[generator] Context generation failed: {exc}", file=sys.stderr)


# =============================================================================
# STARTUP SELF-TEST
# =============================================================================


def run_startup_tests():
    """Run all tests on startup."""
    print("\n" + "=" * 60)
    print("RUNNING STARTUP SELF-TESTS")
    print("=" * 60)

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-v", "-p", "no:cacheprovider"],
            check=False,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        print("=" * 60)
        print(
            "ALL TESTS PASSED" if result.returncode == 0 else f"TESTS FAILED ({result.returncode})"
        )
        print("=" * 60 + "\n")
        return result.returncode == 0
    except Exception as e:
        print(f"Error running tests: {e}")
        return False


# =============================================================================
# REST API (Starlette on separate port 9093)
# =============================================================================


def create_rest_app():
    """REST API for tools (alternative access, not MCP)."""
    from pathlib import Path
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.responses import JSONResponse, PlainTextResponse
    from starlette.routing import Route

    async def health(request):
        return JSONResponse(
            {
                "status": "healthy",
                "server": "HA-Observer",
                "version": __version__,
                "tools_registered": get_tool_count(),
                "endpoints": {
                    "mcp_sse": f"http://0.0.0.0:{MCP_SSE_PORT}/sse",
                    "mcp_messages": f"http://0.0.0.0:{MCP_SSE_PORT}/messages",
                    "rest_api": f"http://0.0.0.0:{REST_API_PORT}/api/",
                },
            }
        )

    async def list_tools_endpoint(request):
        tools = get_all_tools()
        tool_list = []
        for name, tool in tools.items():
            desc = None
            if hasattr(tool, "description") and tool.description:
                desc = tool.description
            elif hasattr(tool, "fn") and hasattr(tool.fn, "__doc__") and tool.fn.__doc__:
                desc = tool.fn.__doc__.strip().split("\n")[0]
            tool_list.append({"name": name, "description": desc})
        return JSONResponse(
            {
                "success": True,
                "total": len(tool_list),
                "tools": sorted(tool_list, key=lambda x: x["name"]),
            }
        )

    async def call_tool_endpoint(request):
        tool_name = request.path_params.get("tool_name", "")

        try:
            body = await request.body()
            args = json.loads(body) if body else {}
        except json.JSONDecodeError:
            args = {}
        except Exception:
            args = {}

        tool = get_tool(tool_name)

        if tool is None:
            all_tool_names = list(get_all_tools().keys())
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Tool '{tool_name}' not found",
                    "available_tools": sorted(all_tool_names)[:30],
                    "total_tools": len(all_tool_names),
                },
                status_code=404,
            )

        try:
            if hasattr(tool, "fn") and callable(tool.fn):
                fn = tool.fn
            elif callable(tool):
                fn = tool
            else:
                return JSONResponse(
                    {"success": False, "error": f"Tool '{tool_name}' is not callable"},
                    status_code=500,
                )

            if inspect.iscoroutinefunction(fn):
                result = await fn(**args)
            else:
                result = fn(**args)

            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    pass

            return JSONResponse({"success": True, "tool": tool_name, "result": result})

        except TypeError as e:
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Invalid arguments: {e}",
                    "tool": tool_name,
                },
                status_code=400,
            )
        except Exception as e:
            return JSONResponse(
                {
                    "success": False,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "tool": tool_name,
                },
                status_code=500,
            )

    async def context_generate(request):
        """Generate HA context file."""
        global _generation_state
        if _generation_state["status"] == "running":
            return JSONResponse(
                {
                    "success": False,
                    "error": "Generation already in progress",
                    "started_at": _generation_state["started_at"],
                },
                status_code=409,
            )

        try:
            body = await request.body()
            params = json.loads(body) if body else {}
        except json.JSONDecodeError:
            params = {}
        except Exception:
            params = {}

        config_path = params.get("config_path", HA_CONFIG_PATH)
        output_path = params.get("output_path", OUTPUT_PATH)
        mode = params.get("mode", "hybrid")

        allowed_prefixes = [HA_CONFIG_PATH, os.path.dirname(OUTPUT_PATH) or "/app"]
        if not any(str(config_path).startswith(p) for p in allowed_prefixes):
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Invalid config_path: {config_path}",
                },
                status_code=403,
            )
        if not any(str(output_path).startswith(p) for p in allowed_prefixes):
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Invalid output_path: {output_path}",
                },
                status_code=403,
            )

        _generation_state["status"] = "running"
        _generation_state["started_at"] = time.time()
        _generation_state["output_path"] = output_path
        _generation_state["error"] = None
        _generation_state["stats"] = {}

        thread = threading.Thread(
            target=_run_context_generation,
            args=(config_path, output_path, mode),
            daemon=True,
        )
        thread.start()

        return JSONResponse(
            {
                "success": True,
                "message": "Context generation started",
                "mode": mode,
                "config_path": config_path,
                "output_path": output_path,
                "started_at": _generation_state["started_at"],
            }
        )

    async def context_status(request):
        """Get context generation status."""
        return JSONResponse(
            {
                "status": _generation_state["status"],
                "started_at": _generation_state["started_at"],
                "completed_at": _generation_state["completed_at"],
                "output_path": _generation_state["output_path"],
                "error": _generation_state["error"],
                "stats": _generation_state["stats"],
            }
        )

    async def context_download(request):
        """Download generated context file."""
        output_path = _generation_state["output_path"] or OUTPUT_PATH
        if not os.path.exists(output_path):
            return JSONResponse(
                {
                    "success": False,
                    "error": "Context file not found. Run generation first.",
                    "path": output_path,
                },
                status_code=404,
            )
        if _generation_state["status"] == "running":
            return JSONResponse(
                {
                    "success": False,
                    "error": "Generation still in progress",
                    "status": "running",
                },
                status_code=409,
            )

        format_param = request.query_params.get("format", "markdown")
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                content = f.read()
            if format_param == "json":
                return JSONResponse(
                    {
                        "success": True,
                        "content": content,
                        "path": output_path,
                        "size_bytes": len(content.encode("utf-8")),
                        "generated_at": _generation_state["completed_at"],
                    }
                )
            return PlainTextResponse(
                content,
                media_type="text/markdown",
                headers={"Content-Disposition": f'attachment; filename="{Path(output_path).name}"'},
            )
        except Exception as exc:
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Failed to read context file: {exc}",
                },
                status_code=500,
            )

    async def context_modes(request):
        """List available generation modes."""
        return JSONResponse(
            {
                "modes": [
                    {
                        "id": "offline",
                        "name": "Offline Mode",
                        "description": "Reads only from local filesystem",
                    },
                    {
                        "id": "online",
                        "name": "Online Mode",
                        "description": "Fetches data from HA API",
                    },
                    {
                        "id": "hybrid",
                        "name": "Hybrid Mode (default)",
                        "description": "Combines offline and online data",
                    },
                ]
            }
        )

    async def openapi_schema(request):
        tools = get_all_tools()
        paths = {
            "/api/tools": {
                "get": {
                    "summary": "List all available tools",
                    "operationId": "listTools",
                    "responses": {"200": {"description": "List of tools"}},
                }
            },
            "/api/health": {
                "get": {
                    "summary": "Health check",
                    "operationId": "healthCheck",
                    "responses": {"200": {"description": "Server is healthy"}},
                }
            },
        }

        for name, tool in tools.items():
            desc = "MCP Tool"
            if hasattr(tool, "description") and tool.description:
                desc = tool.description
            elif hasattr(tool, "fn") and hasattr(tool.fn, "__doc__") and tool.fn.__doc__:
                desc = tool.fn.__doc__.strip().split("\n")[0]

            paths[f"/api/tools/{name}"] = {
                "post": {
                    "summary": desc[:100] if len(desc) > 100 else desc,
                    "operationId": name,
                    "requestBody": {
                        "content": {"application/json": {"schema": {"type": "object"}}}
                    },
                    "responses": {"200": {"description": "Tool result"}},
                }
            }

        return JSONResponse(
            {
                "openapi": "3.0.0",
                "info": {
                    "title": "HA-Observer REST API",
                    "description": "REST bridge for Home Assistant MCP tools",
                    "version": "1.0.0",
                },
                "servers": [{"url": "/"}],
                "paths": paths,
            }
        )

    routes = [
        Route("/health", endpoint=health, methods=["GET"]),
        Route("/api/health", endpoint=health, methods=["GET"]),
        Route("/api/tools", endpoint=list_tools_endpoint, methods=["GET"]),
        Route("/api/tools/{tool_name}", endpoint=call_tool_endpoint, methods=["POST"]),
        Route("/api/openapi.json", endpoint=openapi_schema, methods=["GET"]),
        Route("/api/context/generate", endpoint=context_generate, methods=["POST"]),
        Route("/api/context/status", endpoint=context_status, methods=["GET"]),
        Route("/api/context/download", endpoint=context_download, methods=["GET"]),
        Route("/api/context/modes", endpoint=context_modes, methods=["GET"]),
    ]

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]

    return Starlette(routes=routes, middleware=middleware)


def run_rest_api():
    """Start REST API in a separate thread."""
    import uvicorn

    app = create_rest_app()
    print(f"[rest] REST API started on port {REST_API_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=REST_API_PORT, log_level="warning")


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    # 1. Start health check server
    start_health_server(port=HEALTH_CHECK_PORT)
    HEALTH_STATE["status"] = "healthy"
    HEALTH_STATE["last_heartbeat"] = time.time()

    # 2. Run startup tests (optional)
    if RUN_TESTS_ON_STARTUP:
        run_startup_tests()

    print("[server] " + "=" * 50)
    print("[server] HA-Observer MCP Server")
    print("[server] " + "=" * 50)
    print(f"[server] HA_URL: {HA_URL}")
    print(f"[server] HA_CONFIG_PATH: {HA_CONFIG_PATH}")
    print(f"[server] Registered tools: {tool_count}")
    print("[server] " + "-" * 50)

    # 3. Start REST API in a separate thread
    rest_thread = threading.Thread(target=run_rest_api, daemon=True, name="RestAPI")
    rest_thread.start()

    print("[server] Endpoints:")
    print(f"[server]   Health:      http://0.0.0.0:{HEALTH_CHECK_PORT}/health")
    print(f"[server]   MCP SSE:     http://0.0.0.0:{MCP_SSE_PORT}/sse      <- LibreChat")
    print(f"[server]   MCP MSG:     http://0.0.0.0:{MCP_SSE_PORT}/messages")
    print(f"[server]   REST API:    http://0.0.0.0:{REST_API_PORT}/api/")
    print("[server] " + "=" * 50)

    # 4. Start MCP SSE server - BLOCKING!
    print(f"[server] Starting MCP SSE transport on port {MCP_SSE_PORT}...")
    mcp.run(transport="sse", host="0.0.0.0", port=MCP_SSE_PORT)
