"""
Tests for tools/scenes.py
"""

import json
import os

import pytest
import yaml

from tools.scenes import register_scene_tools


@pytest.fixture
def mock_mcp():
    class MockMCP:
        def __init__(self):
            self._tools = {}

        def tool(self):
            def decorator(func):
                self._tools[func.__name__] = func
                return func

            return decorator

    return MockMCP()


@pytest.fixture
def config_path(tmp_path):
    return str(tmp_path)


@pytest.fixture
def tools(mock_mcp, config_path):
    register_scene_tools(mock_mcp, config_path)
    return mock_mcp._tools


class TestListScenes:
    def test_no_scenes_file(self, tools, config_path):
        result = tools["list_scenes"]()
        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"].lower()

    def test_list_scenes_success(self, tools, config_path):
        scenes_data = [
            {
                "id": "evening_lights",
                "name": "Evening Lights",
                "icon": "mdi:weather-night",
                "entities": {
                    "light.living_room": {"state": "on", "brightness": 100},
                    "light.bedroom": {"state": "off"},
                },
            },
            {"id": "morning_routine", "name": "Morning Routine", "entities": {}},
        ]
        scene_file = os.path.join(config_path, "scenes.yaml")
        with open(scene_file, "w", encoding="utf-8") as f:
            yaml.dump(scenes_data, f)

        result = tools["list_scenes"]()
        data = json.loads(result)
        assert data["total_scenes"] == 2
        assert data["scenes"][0]["name"] == "Evening Lights"
        assert data["scenes"][0]["entity_count"] == 2
        assert "light.living_room" in data["scenes"][0]["entities"]

    def test_list_scenes_empty(self, tools, config_path):
        scene_file = os.path.join(config_path, "scenes.yaml")
        with open(scene_file, "w", encoding="utf-8") as f:
            f.write("[]")

        result = tools["list_scenes"]()
        data = json.loads(result)
        assert data["total_scenes"] == 0


class TestGetSceneCode:
    def test_scene_not_found(self, tools, config_path):
        scene_file = os.path.join(config_path, "scenes.yaml")
        with open(scene_file, "w", encoding="utf-8") as f:
            yaml.dump([], f)

        result = tools["get_scene_code"]("nonexistent")
        assert "not found" in result.lower()

    def test_get_scene_code_success(self, tools, config_path):
        scenes_data = [
            {
                "id": "evening_lights",
                "name": "Evening Lights",
                "entities": {"light.living_room": {"state": "on"}},
            }
        ]
        scene_file = os.path.join(config_path, "scenes.yaml")
        with open(scene_file, "w", encoding="utf-8") as f:
            yaml.dump(scenes_data, f)

        result = tools["get_scene_code"]("evening_lights")
        assert "Evening Lights" in result
        assert "light.living_room" in result

    def test_get_scene_code_by_name(self, tools, config_path):
        scenes_data = [{"id": "scene1", "name": "My Scene", "entities": {}}]
        scene_file = os.path.join(config_path, "scenes.yaml")
        with open(scene_file, "w", encoding="utf-8") as f:
            yaml.dump(scenes_data, f)

        result = tools["get_scene_code"]("My Scene")
        assert "My Scene" in result

    def test_get_scene_code_no_file(self, tools, config_path):
        """Test scenario where scenes.yaml does not exist at all."""
        result = tools["get_scene_code"]("anything")
        assert "not found" in result.lower()

    def test_list_scenes_corrupt_file(self, tools, config_path):
        """Test exception handler when YAML is corrupt/invalid."""
        scene_file = os.path.join(config_path, "scenes.yaml")
        scene_file_path = scene_file
        os.makedirs(os.path.dirname(scene_file_path), exist_ok=True)
        # Write invalid YAML that will cause safe_load to fail
        with open(scene_file, "wb") as f:
            f.write(b"\xff\xfe\x00\x00")

        result = json.loads(tools["list_scenes"]())
        assert result["success"] is False

    def test_get_scene_code_corrupt_file(self, tools, config_path):
        """Test exception handler for get_scene_code with corrupt file."""
        scene_file = os.path.join(config_path, "scenes.yaml")
        with open(scene_file, "wb") as f:
            f.write(b"\xff\xfe\x00\x00")

        result = json.loads(tools["get_scene_code"]("anything"))
        assert result["success"] is False
