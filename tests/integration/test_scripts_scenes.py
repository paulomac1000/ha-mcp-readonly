"""
Integration tests for scripts and scenes tools.
Tests against REAL Home Assistant instance.

RUN:
    pytest tests/integration/test_scripts_scenes.py -v
"""

import json
import os

import pytest

# Configuration
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")

# Skip if not configured
pytestmark = pytest.mark.skipif(
    not HA_URL or not HA_TOKEN, reason="HA_URL and HA_TOKEN must be set"
)


class TestScriptsScenes:
    """Scripts and scenes tools tests."""

    sample_script_id = None  # Populated by test_list_scripts
    sample_scene_id = None  # Populated by test_list_scenes

    def test_list_scripts(self, real_mcp):
        """list_scripts returns scripts list and total count."""
        result = real_mcp.call_tool("list_scripts")
        data = json.loads(result)
        assert data["success"]
        assert isinstance(data.get("scripts"), list)
        assert isinstance(data.get("total_scripts"), int)
        assert data["total_scripts"] >= 0

        # Store first script for get_script_code test
        scripts = data.get("scripts", [])
        if scripts:
            TestScriptsScenes.sample_script_id = scripts[0].get("id")

        print(f"\n[OK] list_scripts: {data['total_scripts']} scripts")

    def test_get_script_code(self, real_mcp):
        """get_script_code returns YAML code for a script."""
        if not TestScriptsScenes.sample_script_id:
            pytest.skip("No script from test_list_scripts")

        result = real_mcp.call_tool(
            "get_script_code", script_id=TestScriptsScenes.sample_script_id
        )

        # get_script_code returns raw YAML string on success,
        # or JSON {"success": false, ...} on failure
        try:
            data = json.loads(result)
            assert data.get("success"), f"get_script_code failed: {data.get('error')}"
            assert "code" in data
        except (json.JSONDecodeError, ValueError):
            # YAML string = success
            assert result.strip()

        print(f"\n[OK] get_script_code for: {TestScriptsScenes.sample_script_id}")

    def test_list_scenes(self, real_mcp):
        """list_scenes returns scenes list and total count."""
        result = real_mcp.call_tool("list_scenes")
        data = json.loads(result)
        assert data["success"]
        assert isinstance(data.get("scenes"), list)
        assert isinstance(data.get("total_scenes"), int)
        assert data["total_scenes"] >= 0

        # Store first scene for get_scene_code test
        scenes = data.get("scenes", [])
        if scenes:
            TestScriptsScenes.sample_scene_id = scenes[0].get("id")

        print(f"\n[OK] list_scenes: {data['total_scenes']} scenes")

    def test_get_scene_code(self, real_mcp):
        """get_scene_code returns YAML code for a scene."""
        if not TestScriptsScenes.sample_scene_id:
            pytest.skip("No scene from test_list_scenes")

        result = real_mcp.call_tool(
            "get_scene_code", scene_id=TestScriptsScenes.sample_scene_id
        )

        # get_scene_code returns raw YAML string on success,
        # or JSON {"success": false, ...} on failure
        try:
            data = json.loads(result)
            assert data.get("success"), f"get_scene_code failed: {data.get('error')}"
            assert "code" in data
        except (json.JSONDecodeError, ValueError):
            # YAML string = success
            assert result.strip()

        print(f"\n[OK] get_scene_code for: {TestScriptsScenes.sample_scene_id}")
