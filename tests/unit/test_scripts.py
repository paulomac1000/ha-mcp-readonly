"""
Tests for tools/scripts.py
"""

import json
import os

import pytest
import yaml

from tools.scripts import register_script_tools


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
    register_script_tools(mock_mcp, config_path)
    return mock_mcp._tools


class TestListScripts:
    def test_no_scripts_file(self, tools, config_path):
        result = tools["list_scripts"]()
        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"].lower()

    def test_list_scripts_success(self, tools, config_path):
        scripts_data = {
            "notify_energy_price": {
                "alias": "Notify Energy Price",
                "description": "Send energy price notification",
                "mode": "single",
                "fields": {"price": {"description": "Current price"}},
            },
            "turn_off_all_lights": {"alias": "Turn Off All Lights", "mode": "parallel"},
        }
        script_file = os.path.join(config_path, "scripts.yaml")
        with open(script_file, "w", encoding="utf-8") as f:
            yaml.dump(scripts_data, f)

        result = tools["list_scripts"]()
        data = json.loads(result)
        assert data["total_scripts"] == 2
        assert data["scripts"][0]["alias"] == "Notify Energy Price"
        assert "price" in data["scripts"][0]["fields"]

    def test_list_scripts_empty(self, tools, config_path):
        script_file = os.path.join(config_path, "scripts.yaml")
        with open(script_file, "w", encoding="utf-8") as f:
            f.write("{}")

        result = tools["list_scripts"]()
        data = json.loads(result)
        assert data["total_scripts"] == 0


class TestGetScriptCode:
    def test_script_not_found(self, tools, config_path):
        script_file = os.path.join(config_path, "scripts.yaml")
        with open(script_file, "w", encoding="utf-8") as f:
            yaml.dump({}, f)

        result = tools["get_script_code"]("nonexistent")
        assert "not found" in result.lower()

    def test_get_script_code_success(self, tools, config_path):
        scripts_data = {
            "notify_energy_price": {
                "alias": "Notify Energy Price",
                "sequence": [
                    {
                        "service": "notify.mobile_app",
                        "data": {"message": "Price: {{ price }}"},
                    }
                ],
            }
        }
        script_file = os.path.join(config_path, "scripts.yaml")
        with open(script_file, "w", encoding="utf-8") as f:
            yaml.dump(scripts_data, f)

        result = tools["get_script_code"]("notify_energy_price")
        assert "Notify Energy Price" in result
        assert "notify.mobile_app" in result
