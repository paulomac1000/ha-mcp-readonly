"""
Tests for tools/entity_context.py
"""

import json
from unittest.mock import patch

import pytest

from tools.entity_context import register_entity_context_tools


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
def ha_url():
    return "http://test-ha"


@pytest.fixture
def ha_token():
    return "test-token"


@pytest.fixture
def tools(mock_mcp, config_path, ha_url, ha_token):
    register_entity_context_tools(mock_mcp, config_path, ha_url, ha_token)
    return mock_mcp._tools


class TestEntityGetContextTree:
    @pytest.mark.asyncio
    async def test_invalid_entity_id(self, tools):
        result = await tools["entity_get_context_tree"]("invalid_no_dot")
        data = json.loads(result)
        assert data["success"] is False
        assert "Invalid entity_id" in data["error"]

    @pytest.mark.asyncio
    async def test_entity_not_found(self, tools):
        with patch(
            "tools.entity_context.make_ha_request",
            return_value={"success": False, "error": "404"},
        ):
            result = await tools["entity_get_context_tree"]("light.nonexistent")
            data = json.loads(result)
            assert data["success"] is False
            assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_successful_context_tree(self, tools, config_path):
        storage_dir = config_path + "/.storage"
        import os

        os.makedirs(storage_dir, exist_ok=True)
        with open(storage_dir + "/core.entity_registry", "w") as f:
            import json as _json

            f.write(
                _json.dumps(
                    {
                        "data": {
                            "entities": [
                                {
                                    "entity_id": "light.test",
                                    "platform": "hue",
                                    "device_id": "dev1",
                                }
                            ]
                        }
                    }
                )
            )
        with open(storage_dir + "/core.device_registry", "w") as f:
            f.write(
                _json.dumps(
                    {
                        "data": {
                            "devices": [
                                {
                                    "id": "dev1",
                                    "name": "Test Device",
                                    "manufacturer": "Philips",
                                }
                            ]
                        }
                    }
                )
            )

        state_response = {
            "success": True,
            "data": {
                "entity_id": "light.test",
                "state": "on",
                "attributes": {"friendly_name": "Test Light"},
                "last_changed": "2024-01-01T00:00:00Z",
                "last_updated": "2024-01-01T00:00:00Z",
            },
        }

        with patch(
            "tools.entity_context.make_ha_request",
            side_effect=lambda url, token, endpoint, **kwargs: (
                state_response
                if endpoint == "/api/states/light.test"
                else {"success": True, "data": []}
                if "history" in endpoint
                else {"success": True, "data": []}
                if "logbook" in endpoint
                else {"success": True, "data": {"automations": []}}
            ),
        ):
            with patch("tools.entity_context.load_registry", return_value={"data": {}}):
                result = await tools["entity_get_context_tree"]("light.test")
                data = json.loads(result)

        assert data["success"] is True
        assert data["context_tree"]["entity_id"] == "light.test"
        assert data["context_tree"]["current_state"] == "on"
        assert "sources_breakdown" in data["context_tree"]
        assert "affecting_automations" in data["context_tree"]

    @pytest.mark.asyncio
    async def test_empty_entity_id(self, tools):
        result = await tools["entity_get_context_tree"]("")
        data = json.loads(result)
        assert data["success"] is False
