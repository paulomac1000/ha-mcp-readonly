"""
Unit test fixtures — mocked dependencies, no real connections.
"""

import asyncio
import inspect
import json
from unittest.mock import MagicMock

import pytest

from tests.fixtures import (
    MOCK_AREA_REGISTRY,
    MOCK_CONFIG_ENTRIES,
    MOCK_DEVICE_REGISTRY,
    MOCK_ENTITY_REGISTRY,
    MOCK_SAMPLE_STATES,
)


@pytest.fixture
def config_path(tmp_path):
    """Create a temporary config path with mock registry files."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    storage_dir = config_dir / ".storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    (storage_dir / "core.area_registry").write_text(
        json.dumps(MOCK_AREA_REGISTRY), encoding="utf-8"
    )
    (storage_dir / "core.device_registry").write_text(
        json.dumps(MOCK_DEVICE_REGISTRY), encoding="utf-8"
    )
    (storage_dir / "core.entity_registry").write_text(
        json.dumps(MOCK_ENTITY_REGISTRY), encoding="utf-8"
    )
    (storage_dir / "core.config_entries").write_text(
        json.dumps(MOCK_CONFIG_ENTRIES), encoding="utf-8"
    )

    log_file = config_dir / "home-assistant.log"
    log_file.write_text(
        """2024-06-01 14:20:00.000 INFO (MainThread) [homeassistant.core] Starting Home Assistant
2024-06-01 14:20:01.000 INFO (MainThread) [homeassistant.components.mqtt] Setting up MQTT
2024-06-01 14:24:55.286 ERROR (MainThread) [homeassistant.components.gree] Connection failed for Bedroom AC
2024-06-01 14:24:56.000 WARNING (MainThread) [homeassistant.components.gree] Retrying connection...
""",
        encoding="utf-8",
    )

    return str(config_dir)


@pytest.fixture
def ha_url():
    """Mock Home Assistant URL for unit tests."""
    return "http://localhost:8123"


@pytest.fixture
def ha_token():
    """Mock Home Assistant token for unit tests."""
    return "mock_token_for_testing_1234567890"


@pytest.fixture
def mock_registry_data():
    """Return mock registry data as a dict for patching load_registry()."""
    return {
        "core.area_registry": MOCK_AREA_REGISTRY,
        "core.device_registry": MOCK_DEVICE_REGISTRY,
        "core.entity_registry": MOCK_ENTITY_REGISTRY,
        "core.config_entries": MOCK_CONFIG_ENTRIES,
    }


@pytest.fixture
def sample_states():
    """Return sample entity states for unit tests."""
    return MOCK_SAMPLE_STATES.copy()


@pytest.fixture
def mock_mcp():
    """Mock MCP for unit tests that properly handles tool registration."""
    mcp = MagicMock()
    mcp._tools = {}

    def tool_decorator(*args, **kwargs):
        def wrapper(func):
            tool_name = kwargs.get("name", func.__name__)
            mcp._tools[tool_name] = func
            return func

        if len(args) == 1 and callable(args[0]) and not kwargs:
            mcp._tools[args[0].__name__] = args[0]
            return args[0]

        return wrapper

    mcp.tool = tool_decorator

    def mock_get_tool(name):
        return mcp._tools.get(name)

    mcp.get_tool = mock_get_tool
    return mcp


class MCPWrapper:
    """FastMCP wrapper providing compatibility across versions."""

    def __init__(self, mcp):
        self.mcp = mcp
        self._tools_cache = None
        self._has_get_tool = False
        self._has_tm_get_tool = False

    def _discover_tools(self):
        if self._tools_cache is not None:
            return self._tools_cache

        tools = {}
        self._has_get_tool = hasattr(self.mcp, "get_tool") and callable(self.mcp.get_tool)
        self._has_tm_get_tool = False
        if hasattr(self.mcp, "_tool_manager"):
            tm = self.mcp._tool_manager
            if hasattr(tm, "get_tool") and callable(tm.get_tool):
                self._has_tm_get_tool = True

        if hasattr(self.mcp, "_tools") and isinstance(self.mcp._tools, dict):
            for name, tool in self.mcp._tools.items():
                unwrapped = self._unwrap_tool(tool)
                if unwrapped:
                    tools[name] = unwrapped

        if not tools and hasattr(self.mcp, "_tool_manager"):
            tm = self.mcp._tool_manager
            if hasattr(tm, "_tools") and isinstance(tm._tools, dict):
                for name, tool in tm._tools.items():
                    unwrapped = self._unwrap_tool(tool)
                    if unwrapped:
                        tools[name] = unwrapped

        if not tools and hasattr(self.mcp, "tools"):
            tools_attr = self.mcp.tools
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
        for attr in ("name", "__name__", "_name"):
            if hasattr(tool, attr):
                val = getattr(tool, attr)
                if val:
                    return val
        return None

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

    def _get_tool_function(self, name):
        tools = self._discover_tools()
        if name in tools and tools[name] is not None:
            return tools[name]
        if self._has_get_tool:
            try:
                tool = self.mcp.get_tool(name)
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
                tool = self.mcp._tool_manager.get_tool(name)
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
        tools = self._discover_tools()
        return list(tools.keys())

    def call_tool(self, name, *args, **kwargs):
        func = self._get_tool_function(name)
        if not func:
            available = self._list_available_tools()
            preview = available[:10] if len(available) > 10 else available
            raise ValueError(f"Tool '{name}' not found. Available ({len(available)}): {preview}")
        if inspect.iscoroutinefunction(func):
            return self._run_async(func, *args, **kwargs)
        else:
            return func(*args, **kwargs)

    def _run_async(self, func, *args, **kwargs):
        try:
            asyncio.get_running_loop()
            is_running = True
        except RuntimeError:
            is_running = False
        if is_running:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, func(*args, **kwargs))
                return future.result()
        else:
            return asyncio.run(func(*args, **kwargs))


@pytest.fixture
def mcp_client(real_mcp):
    """Provide an MCPWrapper instance for calling tools in tests."""
    return MCPWrapper(real_mcp)
