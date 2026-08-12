from __future__ import annotations

from fastmcp import Client

import pytest

import server
from tools.manifests import get_all_manifests


@pytest.mark.asyncio
async def test_negotiated_protocol_revision_is_manifest_claim() -> None:
    async with Client(server.create_mcp_server()) as client:
        negotiated = client.initialize_result.protocolVersion
        tools = await client.list_tools()
    assert negotiated == "2025-11-25"
    assert len(tools) == server.get_tool_count()
    registered = {tool.name for tool in tools}
    manifests = get_all_manifests()
    for name in registered:
        assert negotiated in manifests[name]["protocol_revisions"]
