"""Protocol-level tests through the supported FastMCP client API."""

import asyncio
import json
from fastmcp import Client
from fastmcp.exceptions import ToolError

import pytest

import server


def test_handshake_list_and_call() -> None:
    async def verify() -> None:
        expected = server.get_tool_count()
        async with Client(server.get_mcp_server()) as client:
            tools = await client.list_tools()
            assert len(tools) == expected
            names = {tool.name for tool in tools}
            assert "describe_ha_capabilities" in names
            result = await client.call_tool("describe_ha_capabilities", {})
            assert result.is_error is False
            payload = json.loads(result.content[0].text)
            assert payload["success"] is True
            assert payload["supported_tool_count"] == expected
            assert len(payload["tools"]) == expected
            assert {item["name"] for item in payload["tools"]} == {tool.name for tool in tools}
            assert payload["tool_count"] == payload["active_tool_count"]
            assert 0 < payload["active_tool_count"] <= expected
            assert {"stdio", "streamable-http"}.issubset(payload["transports"])

    asyncio.run(verify())


def test_protocol_native_input_error() -> None:
    async def verify() -> None:
        async with Client(server.get_mcp_server()) as client:
            with pytest.raises(ToolError):
                await client.call_tool("get_entity_state", {"wrong_parameter": "x"})

    asyncio.run(verify())
