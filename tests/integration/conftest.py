"""
Integration test fixtures — real HA, MCPWrapper, sample entities.
"""

import asyncio
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
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
                print(f"[OK] Loaded environment variables from {env_path}", file=sys.stderr)
                return
            except Exception as e:
                print(f"[WARN] Error loading {env_path}: {e}", file=sys.stderr)


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
    """Synchronous facade over the supported in-memory FastMCP client.

    The tools are reached through ``fastmcp.Client`` (the supported client
    API) rather than private SDK registries, which is what the project
    contract requires for protocol evidence.
    """

    def __init__(self, mcp_instance):
        self._mcp = mcp_instance
        self._loop = None

    def close(self) -> None:
        """Close the shared event loop owned by this wrapper."""
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
        self._loop = None

    def _get_or_create_loop(self):
        """Fetch or create the single shared event loop."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop

    def _run_async(self, coro_factory):
        """Run a coroutine-returning callable on the shared loop."""
        loop = self._get_or_create_loop()
        return loop.run_until_complete(coro_factory())

    def call_tool(self, name, *args, **kwargs):
        """Execute a tool through the supported FastMCP client."""
        if args:
            raise TypeError("MCPWrapper.call_tool accepts keyword tool arguments only")
        from fastmcp import Client

        async def _call():
            async with Client(self._mcp) as client:
                result = await client.call_tool(name, kwargs or {})
                if result.content:
                    return result.content[0].text
                return ""

        return self._run_async(_call)


@pytest.fixture(scope="session")
def real_mcp():
    """
    Create MCP server for integration tests through the production composition root.

    Using ``server.create_mcp_server()`` exercises the same operation registry,
    fail-closed manifests, and invocation kernel that the deployed server uses,
    instead of hand-registering tools on a bare FastMCP instance.
    """
    if not ha_configured:
        pytest.skip("Integration tests require HA_URL + HA_TOKEN")

    import server

    mcp = server.create_mcp_server()

    print(
        f"[OK] Registered Home Assistant tools via composition root (url: {HA_URL})",
        file=sys.stderr,
    )

    wrapper = MCPWrapper(mcp)
    try:
        yield wrapper
    finally:
        wrapper.close()


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
        print(f"[WARN] Failed to get domains summary: {e}", file=sys.stderr)
        entities["all"] = ["sun.sun", "sensor.time"]
        entities["sensor"] = ["sensor.time"]
        return entities

    if not data.get("success"):
        print("[WARN] get_domains_summary returned failure", file=sys.stderr)
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
                    f"[WARN] Failed to search entities for domain {domain}: {e}",
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
        print("[WARN] No entities found, using fallback", file=sys.stderr)
        entities["all"] = ["sun.sun"]
    else:
        print(f"[OK] Found {len(entities['all'])} sample entities", file=sys.stderr)

    return entities


@pytest.fixture(scope="module")
def ha_configured_flag():
    """Returns True if Home Assistant is configured."""
    return ha_configured
