"""
Tests for tools/categories.py — list_automation_categories tool.
"""

import json
from unittest.mock import patch

import pytest

from tools.categories import register_categories_tools


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
def config_path(tmp_path) -> str:
    return str(tmp_path)


class TestListAutomationCategories:
    def _category_registry(self):
        return {
            "data": {
                "categories": [
                    {"category_id": "lighting", "name": "Lighting", "scope": "automation"},
                    {"category_id": "security", "name": "Security", "scope": "automation"},
                    {"category_id": "climate", "name": "Climate", "scope": "automation"},
                ]
            }
        }

    def _entity_registry_with_assignments(self):
        return {
            "data": {
                "entities": [
                    {
                        "entity_id": "automation.morning_lights",
                        "unique_id": "auto_001",
                        "platform": "automation",
                        "categories": {"automation": "lighting"},
                    },
                    {
                        "entity_id": "automation.evening_lights",
                        "unique_id": "auto_002",
                        "platform": "automation",
                        "categories": {"automation": "lighting"},
                    },
                    {
                        "entity_id": "automation.no_category",
                        "unique_id": "auto_003",
                        "platform": "automation",
                        "categories": {},
                    },
                    {
                        "entity_id": "automation.no_categories_key",
                        "unique_id": "auto_004",
                        "platform": "automation",
                    },
                ]
            }
        }

    def test_success_path_with_counts(self, mock_mcp, config_path):
        category_reg = self._category_registry()
        entity_reg = self._entity_registry_with_assignments()

        def mock_load_registry(name, path, use_cache=True):
            if "category" in name:
                return category_reg
            if "entity" in name:
                return entity_reg
            return {"data": {}}

        with patch("tools.categories.load_registry", side_effect=mock_load_registry):
            register_categories_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["list_automation_categories"]
            data = json.loads(tool(include_entity_count=True))

        assert data["success"] is True
        assert data["total"] == 3

        names = [c["name"] for c in data["categories"]]
        assert "Lighting" in names
        assert "Security" in names
        assert "Climate" in names

        lighting = [c for c in data["categories"] if c["category_id"] == "lighting"][0]
        assert lighting["entity_count"] == 2

        security = [c for c in data["categories"] if c["category_id"] == "security"][0]
        assert security["entity_count"] == 0

        climate = [c for c in data["categories"] if c["category_id"] == "climate"][0]
        assert climate["entity_count"] == 0

        assert set(data["empty_categories"]) == {"security", "climate"}

    def test_empty_categories(self, mock_mcp, config_path):
        with patch("tools.categories.load_registry") as mock_load:
            mock_load.return_value = {"data": {"categories": []}}
            register_categories_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["list_automation_categories"]
            data = json.loads(tool(include_entity_count=False))

        assert data["success"] is True
        assert data["total"] == 0
        assert data["categories"] == []

    def test_include_entity_count_false(self, mock_mcp, config_path):
        category_reg = self._category_registry()

        def mock_load_registry(name, path, use_cache=True):
            if "category" in name:
                return category_reg
            return {"data": {}}

        with patch("tools.categories.load_registry", side_effect=mock_load_registry):
            register_categories_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["list_automation_categories"]
            data = json.loads(tool(include_entity_count=False))

        assert data["success"] is True
        assert data["total"] == 3
        assert data["empty_categories"] == []
        for cat in data["categories"]:
            assert "entity_count" not in cat

    def test_exception_handler(self, mock_mcp, config_path):
        with patch(
            "tools.categories._do_list_automation_categories",
            side_effect=RuntimeError("boom"),
        ):
            register_categories_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["list_automation_categories"]
            data = json.loads(tool())

        assert data["success"] is False
        assert "boom" in data.get("error", "")
