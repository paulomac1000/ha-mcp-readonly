"""Unit tests for the capability introspection tool.

Reference: mcp-server-standards.md rule 2b (L3+ capability introspection).
[RULE: TEST-REG-2] registration test, [RULE: TEST-REG-3] exception handler.
"""

import json
from unittest.mock import patch

from tools.capabilities import (
    CAPABILITIES_SCHEMA_VERSION,
    _do_describe_ha_capabilities,
    register_capability_tools,
)


class TestDoDescribeCapabilities:
    """Tests for the zero-I/O internal function."""

    def test_returns_catalog_structure(self):
        result = _do_describe_ha_capabilities()
        assert result["schema_version"] == CAPABILITIES_SCHEMA_VERSION
        assert result["server"] == "HA-Observer"
        assert "tools_version" in result
        assert result["transports"] == ["sse", "rest"]
        assert isinstance(result["tools"], list)
        assert result["tool_count"] == len(result["tools"])

    def test_is_zero_io_and_stable(self):
        # Pure logic: no HTTP, no filesystem; repeated calls are stable.
        a = _do_describe_ha_capabilities()
        b = _do_describe_ha_capabilities()
        assert a["tool_count"] == b["tool_count"]


class TestRegisterCapabilityTools:
    """Tests for register_capability_tools and the tool wrapper."""

    async def test_registration_and_success(self, mock_mcp):
        register_capability_tools(mock_mcp)
        tool = mock_mcp.get_tool("describe_ha_capabilities")
        assert tool is not None
        data = json.loads(await tool())
        assert data["success"] is True
        assert data["schema_version"] == CAPABILITIES_SCHEMA_VERSION

    async def test_exception_handler(self, mock_mcp):
        # [RULE: TEST-REG-3] — patch internal fn, assert controlled error.
        register_capability_tools(mock_mcp)
        tool = mock_mcp.get_tool("describe_ha_capabilities")
        with patch(
            "tools.capabilities._do_describe_ha_capabilities",
            side_effect=RuntimeError("boom"),
        ):
            data = json.loads(await tool())
        assert data["success"] is False
        assert "boom" in data["error"]

    def test_manifest_registered_as_instant_read(self, mock_mcp):
        from tools.manifests import get_manifest

        register_capability_tools(mock_mcp)
        manifest = get_manifest("describe_ha_capabilities")
        assert manifest is not None
        assert manifest["risk"] == "READ"
        assert manifest["latency"] == "instant"
