"""Protocol-visible capability discovery identity and profile contract."""

import json
from fastmcp import Client

import server
from version import __version__


async def test_capability_discovery_reports_server_sdk_protocol_and_profiles() -> None:
    async with Client(server.create_mcp_server()) as client:
        result = await client.call_tool("describe_ha_capabilities", {})
    assert not result.is_error
    payload = json.loads(result.content[0].text)
    assert payload["server_version"] == __version__
    assert payload["tools_version"] == __version__
    assert payload["sdk"]["family"] == "fastmcp"
    assert payload["protocol_versions"]
    assert payload["supported_transports"] == ["stdio", "streamable-http"]
    assert payload["active_transports"]
    assert payload["supported_component_counts"]["tools"] == payload["supported_tool_count"]
    assert payload["active_component_counts"]["tools"] == payload["active_tool_count"]
