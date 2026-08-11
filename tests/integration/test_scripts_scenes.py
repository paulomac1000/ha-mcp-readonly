"""
Integration tests for scripts and scenes tools.
Tests against REAL Home Assistant instance.

RUN:
    pytest tests/integration/test_scripts_scenes.py -v
"""

import json
import os
from typing import Any

import pytest

# Configuration
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")

# Skip if not configured
pytestmark = pytest.mark.skipif(
    not HA_URL or not HA_TOKEN, reason="HA_URL and HA_TOKEN must be set"
)


def _success_envelope(result: str, tool_name: str) -> dict[str, Any]:
    """Parse and validate the v2 JSON tool-response contract."""
    data = json.loads(result)
    assert isinstance(data, dict), f"{tool_name} response must be a JSON object"
    assert data.get("success") is True, f"{tool_name} failed: {data.get('error')}"
    assert isinstance(data.get("_meta"), dict), f"{tool_name} response must include _meta"
    return data


def _first_item_id(real_mcp: Any, list_tool: str, collection_key: str) -> str:
    """Discover one live object without depending on another test's execution order."""
    data = _success_envelope(real_mcp.call_tool(list_tool), list_tool)
    items = data.get(collection_key)
    assert isinstance(items, list), f"{list_tool}.{collection_key} must be a list"
    if not items:
        pytest.skip(f"No {collection_key} available on the live Home Assistant instance")

    first = items[0]
    assert isinstance(first, dict), f"{list_tool} entries must be objects"
    item_id = first.get("id")
    assert isinstance(item_id, str) and item_id, f"{list_tool} entry must contain a non-empty id"
    return item_id


class TestScriptsScenes:
    """Scripts and scenes tools tests."""

    def test_list_scripts(self, real_mcp: Any) -> None:
        """list_scripts returns scripts list and total count."""
        data = _success_envelope(real_mcp.call_tool("list_scripts"), "list_scripts")
        assert isinstance(data.get("scripts"), list)
        assert isinstance(data.get("total_scripts"), int)
        assert data["total_scripts"] >= 0

        print(f"\n[OK] list_scripts: {data['total_scripts']} scripts")

    def test_get_script_code(self, real_mcp: Any) -> None:
        """get_script_code returns YAML content inside the v2 JSON envelope."""
        script_id = _first_item_id(real_mcp, "list_scripts", "scripts")
        data = _success_envelope(
            real_mcp.call_tool("get_script_code", script_id=script_id),
            "get_script_code",
        )
        result = data.get("result")
        assert isinstance(result, str) and result.strip(), (
            "get_script_code result must be non-empty"
        )

        print(f"\n[OK] get_script_code for: {script_id}")

    def test_list_scenes(self, real_mcp: Any) -> None:
        """list_scenes returns scenes list and total count."""
        data = _success_envelope(real_mcp.call_tool("list_scenes"), "list_scenes")
        assert isinstance(data.get("scenes"), list)
        assert isinstance(data.get("total_scenes"), int)
        assert data["total_scenes"] >= 0

        print(f"\n[OK] list_scenes: {data['total_scenes']} scenes")

    def test_get_scene_code(self, real_mcp: Any) -> None:
        """get_scene_code returns YAML content inside the v2 JSON envelope."""
        scene_id = _first_item_id(real_mcp, "list_scenes", "scenes")
        data = _success_envelope(
            real_mcp.call_tool("get_scene_code", scene_id=scene_id),
            "get_scene_code",
        )
        result = data.get("result")
        assert isinstance(result, str) and result.strip(), (
            "get_scene_code result must be non-empty"
        )

        print(f"\n[OK] get_scene_code for: {scene_id}")
