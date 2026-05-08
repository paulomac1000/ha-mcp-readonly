"""
Integration test fixtures - for tests against real Home Assistant.
Overrides problematic fixtures from root conftest.py.
"""

import asyncio
import inspect
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock

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
                print(f"✅ Loaded environment variables from {env_path}", file=sys.stderr)
                return
            except Exception as e:
                print(f"⚠️ Error loading {env_path}: {e}", file=sys.stderr)


load_dotenv()

# Environment variables
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
HA_CONFIG_PATH = os.getenv("HA_CONFIG_PATH", "/config")

# Status flags
ha_configured = bool(HA_URL and HA_TOKEN)


@pytest.fixture
def mock_mcp():
    """Create a mock MCP server with proper tool decorator."""
    mcp = Mock()
    mcp._tools = {}

    def tool_decorator(*args, **kwargs):
        """Decorator supporting name= parameter."""

        def wrapper(func):
            tool_name = kwargs.get("name", func.__name__)
            mcp._tools[tool_name] = func
            return func

        # Handle @mcp.tool (no parentheses)
        if len(args) == 1 and callable(args[0]) and not kwargs:
            func = args[0]
            mcp._tools[func.__name__] = func
            return func

        return wrapper

    mcp.tool = tool_decorator
    return mcp


class MCPWrapper:
    """
    Wrapper for FastMCP providing compatibility with different versions (0.3.x and ≥0.4.x).
    Handles both sync and async tools, always returning a ready result.

    CRITICAL: Uses ONE shared event loop for all async operations,
    to avoid "Future attached to a different loop" error.
    """

    def __init__(self, mcp_instance):
        self._mcp = mcp_instance
        self._tools_cache = None
        self._has_get_tool = False
        self._has_tm_get_tool = False
        self._loop = None  # Shared event loop

    def _discover_tools(self):
        """
        Discovers available tools using various strategies for compatibility.
        returns dict {name: callable}.
        """
        if self._tools_cache is not None:
            return self._tools_cache

        tools = {}

        self._has_get_tool = hasattr(self._mcp, "get_tool") and callable(self._mcp.get_tool)

        self._has_tm_get_tool = False
        if hasattr(self._mcp, "_tool_manager"):
            tm = self._mcp._tool_manager
            if hasattr(tm, "get_tool") and callable(tm.get_tool):
                self._has_tm_get_tool = True

        # Strategy 1: Direct _tools dict on FastMCP instatece
        if hasattr(self._mcp, "_tools") and isinstance(self._mcp._tools, dict):
            for name, tool in self._mcp._tools.items():
                unwrapped = self._unwrap_tool(tool)
                if unwrapped:
                    tools[name] = unwrapped

        # Strategy 2: _tool_manager with _tools dict
        if not tools and hasattr(self._mcp, "_tool_manager"):
            tm = self._mcp._tool_manager
            if hasattr(tm, "_tools") and isinstance(tm._tools, dict):
                for name, tool in tm._tools.items():
                    unwrapped = self._unwrap_tool(tool)
                    if unwrapped:
                        tools[name] = unwrapped

        # Strategy 3: tools property/attribute
        if not tools and hasattr(self._mcp, "tools"):
            tools_attr = self._mcp.tools
            if isinstance(tools_attr, dict):
                for name, tool in tools_attr.items():
                    unwrapped = self._unwrap_tool(tool)
                    if unwrapped:
                        tools[name] = unwrapped
            elif hasattr(tools_attr, "__iter__"):
                for tool in tools_attr:
                    name = self._get_tool_name(tool)
                    if name:
                        unwrapped = self._unwrap_tool(tool)
                        if unwrapped:
                            tools[name] = unwrapped

        self._tools_cache = tools
        return tools

    def _get_tool_name(self, tool):
        """Extract name from a tool object."""
        for attr in ("name", "__name__", "_name"):
            if hasattr(tool, attr):
                val = getattr(tool, attr)
                if val:
                    return val
        return None

    def _unwrap_tool(self, tool):
        """Unwrap tool object to get the actual callable function."""
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

    def _get_tool_function(self, name):
        """Get tool function by name using multiple strategies."""
        tools = self._discover_tools()
        if name in tools and tools[name] is not None:
            return tools[name]

        if self._has_get_tool:
            try:
                tool = self._mcp.get_tool(name)
                if tool:
                    unwrapped = self._unwrap_tool(tool)
                    if unwrapped:
                        if self._tools_cache is not None:
                            self._tools_cache[name] = unwrapped
                        return unwrapped
            except Exception:
                pass

        if self._has_tm_get_tool:
            try:
                tool = self._mcp._tool_manager.get_tool(name)
                if tool:
                    unwrapped = self._unwrap_tool(tool)
                    if unwrapped:
                        if self._tools_cache is not None:
                            self._tools_cache[name] = unwrapped
                        return unwrapped
            except Exception:
                pass

        return None

    def _list_available_tools(self):
        """List available tool names for debugging."""
        tools = self._discover_tools()
        names = list(tools.keys())

        if hasattr(self._mcp, "list_tools") and callable(self._mcp.list_tools):
            try:
                listed = self._mcp.list_tools()
                if isinstance(listed, (list, tuple)):
                    for item in listed:
                        if isinstance(item, str):
                            if item not in names:
                                names.append(item)
                        elif hasattr(item, "name"):
                            if item.name not in names:
                                names.append(item.name)
            except Exception:
                pass

        return names

    def call_tool(self, name, *args, **kwargs):
        """
        Execute a tool by name, handling async execution automatically.
        Always returns a ready result (not a coroutine).
        """
        func = self._get_tool_function(name)

        if not func:
            available = self._list_available_tools()
            preview = available[:10] if len(available) > 10 else available
            raise ValueError(
                f"Tool '{name}' not found. Available ({len(available)} total): {preview}"
            )

        if inspect.iscoroutinefunction(func):
            return self._run_async(func, *args, **kwargs)
        else:
            return func(*args, **kwargs)

    def _get_or_create_loop(self):
        """
        Fetches or creates shared event loop.
        KRYTYCZNE: Zawsze returns TEN SAM loop dla wszystkich operacji.
        """
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop

    def _run_async(self, func, *args, **kwargs):
        """
        Runs async function from sync context.

        CRITICAL: Does NOT use ThreadPoolExecutor or asyncio.run()!
        Instead it uses ONE shared event loop,
        which prevents the "Future attached to a different loop" error.
        """
        loop = self._get_or_create_loop()
        coro = func(*args, **kwargs)
        return loop.run_until_complete(coro)


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
        from tools.composite import register_composite_tools
        from tools.config import register_config_tools
        from tools.config_entries import register_config_entry_tools
        from tools.dev_tools import register_dev_tools
        from tools.devices import register_device_tools
        from tools.diagnostics import register_diagnostics_tools
        from tools.entity_context import register_entity_context_tools
        from tools.entity_dependencies import register_entity_dependency_tools
        from tools.filesystem_explorer import register_filesystem_tools
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
        register_entity_context_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
        register_history_tools(mcp, HA_URL, HA_TOKEN)
        register_area_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
        register_integration_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
        register_composite_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
        register_batch_operations_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
        register_filesystem_tools(mcp)

        print(f"✅ Registered Home Assistant tools (url: {HA_URL})", file=sys.stderr)

    return MCPWrapper(mcp)


@pytest.fixture(scope="module")
def sample_entities(real_mcp):
    """
    Get sample entities from real system for testing.
    """
    if not ha_configured:
        return {
            "all": [],
            "sensor": [],
            "binary_sensor": [],
            "light": [],
            "switch": [],
            "automation": [],
        }

    entities = {
        "all": [],
        "sensor": [],
        "binary_sensor": [],
        "light": [],
        "switch": [],
        "automation": [],
    }

    try:
        result = real_mcp.call_tool("get_domains_summary")
        data = json.loads(result)
    except Exception as e:
        print(f"⚠️ Failed to get domains summary: {e}", file=sys.stderr)
        entities["all"] = ["sun.sun", "sensor.time"]
        entities["sensor"] = ["sensor.time"]
        return entities

    if not data.get("success"):
        print("⚠️ get_domains_summary returned failure", file=sys.stderr)
        entities["all"] = ["sun.sun", "sensor.time"]
        entities["sensor"] = ["sensor.time"]
        return entities

    domains_to_check = ["sensor", "binary_sensor", "light", "switch", "automation"]

    for domain in domains_to_check:
        if domain in data.get("by_domain", {}):
            try:
                search_result = real_mcp.call_tool(
                    "search_entities", search_term="", domain=domain, max_results=5
                )
                search_data = json.loads(search_result)

                if search_data.get("success") and search_data.get("results"):
                    found = [s["entity_id"] for s in search_data["results"]]
                    entities[domain] = found
                    entities["all"].extend(found)
            except Exception as e:
                print(
                    f"⚠️ Failed to search entities for domain {domain}: {e}",
                    file=sys.stderr,
                )

    seen = set()
    unique_all = []
    for entity_id in entities["all"]:
        if entity_id not in seen:
            seen.add(entity_id)
            unique_all.append(entity_id)
    entities["all"] = unique_all

    if not entities["all"]:
        print("⚠️ No entities found, using fallback", file=sys.stderr)
        entities["all"] = ["sun.sun"]
    else:
        print(f"✅ Found {len(entities['all'])} sample entities", file=sys.stderr)

    return entities


@pytest.fixture(scope="module")
def ha_configured_flag():
    """Returns True if Home Assistant is configured."""
    return ha_configured
