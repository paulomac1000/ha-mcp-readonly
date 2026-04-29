"""
Integration test configuration.
Integration tests require real Home Assistant access.
"""

import os
import sys
from pathlib import Path

import pytest


def load_dotenv():
    """Load environment variables from .env file."""
    env_paths = [
        Path("/app/.env"),
        Path(".env"),
    ]

    for env_path in env_paths:
        if env_path.exists():
            try:
                with open(env_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
                print(f"Loaded environment variables from {env_path}", file=sys.stderr)
                return
            except Exception as e:
                print(f"Warning: Error loading {env_path}: {e}", file=sys.stderr)


load_dotenv()

# Environment variables
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
HA_CONFIG_PATH = os.getenv("HA_CONFIG_PATH", "/config")

# Status flags
ha_configured = bool(HA_URL and HA_TOKEN)


@pytest.fixture(scope="session")
def ha_configured_flag():
    """Returns True if Home Assistant is configured."""
    return ha_configured


@pytest.fixture(scope="session")
def real_mcp():
    """
    Create MCP server for integration tests with tools registered based on configuration.
    """
    if not ha_configured:
        pytest.skip("Integration tests require HA_URL + HA_TOKEN")

    from fastmcp import FastMCP

    mcp = FastMCP("HA-Observer-Integration-Test")

    # Register HA tools
    if ha_configured:
        from tools.areas import register_area_tools
        from tools.automations import register_automation_tools
        from tools.batch_operations import register_batch_operations_tools
        from tools.blueprints import register_blueprint_tools
        from tools.config import register_config_tools
        from tools.config_entries import register_config_entry_tools
        from tools.dev_tools import register_dev_tools
        from tools.devices import register_device_tools
        from tools.diagnostics import register_diagnostics_tools
        from tools.entity_dependencies import register_entity_dependency_tools
        from tools.health_reporter import register_health_reporter_tools
        from tools.history import register_history_tools
        from tools.integrations import register_integration_tools
        from tools.logs import register_log_tools
        from tools.scenes import register_scene_tools
        from tools.scripts import register_script_tools
        from tools.states import register_state_tools
        from tools.storage import register_storage_tools

        register_state_tools(mcp, HA_URL, HA_TOKEN, HA_CONFIG_PATH)
        register_automation_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
        register_script_tools(mcp, HA_CONFIG_PATH)
        register_scene_tools(mcp, HA_CONFIG_PATH)
        register_diagnostics_tools(mcp, HA_URL, HA_TOKEN, HA_CONFIG_PATH)
        register_health_reporter_tools(mcp, HA_URL, HA_TOKEN, HA_CONFIG_PATH)
        register_dev_tools(mcp, HA_URL, HA_TOKEN, HA_CONFIG_PATH)
        register_config_entry_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
        register_storage_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
        register_log_tools(mcp, HA_CONFIG_PATH)
        register_blueprint_tools(mcp, HA_CONFIG_PATH)
        register_config_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
        register_device_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
        register_entity_dependency_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
        register_history_tools(mcp, HA_URL, HA_TOKEN)
        register_area_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
        register_integration_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
        register_batch_operations_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

    return mcp


class MCPWrapper:
    """FastMCP wrapper providing compatibility across versions."""

    def __init__(self, mcp):
        self._mcp = mcp
        self._tools_cache = None

    def _discover_tools(self):
        if self._tools_cache is not None:
            return self._tools_cache

        tools = {}

        if hasattr(self._mcp, "_tools") and isinstance(self._mcp._tools, dict):
            for name, tool in self._mcp._tools.items():
                unwrapped = self._unwrap_tool(tool)
                if unwrapped:
                    tools[name] = unwrapped

        if not tools and hasattr(self._mcp, "_tool_manager"):
            tm = self._mcp._tool_manager
            if hasattr(tm, "_tools") and isinstance(tm._tools, dict):
                for name, tool in tm._tools.items():
                    unwrapped = self._unwrap_tool(tool)
                    if unwrapped:
                        tools[name] = unwrapped

        self._tools_cache = tools
        return tools

    def _unwrap_tool(self, tool):
        if tool is None:
            return None
        for attr in ("fn", "func", "_func", "function", "_function", "callback"):
            if hasattr(tool, attr):
                unwrapped = getattr(tool, attr)
                if callable(unwrapped):
                    return unwrapped
        if callable(tool):
            return tool
        return None

    def call_tool(self, name, *args, **kwargs):
        import asyncio
        import concurrent.futures
        import inspect

        tools = self._discover_tools()
        func = tools.get(name)

        if not func:
            raise ValueError(f"Tool '{name}' not found")

        if inspect.iscoroutinefunction(func):
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, func(*args, **kwargs))
                return future.result()
        else:
            return func(*args, **kwargs)


@pytest.fixture
def mcp_client(real_mcp):
    """Provide MCPWrapper instance for calling tools in tests."""
    return MCPWrapper(real_mcp)
