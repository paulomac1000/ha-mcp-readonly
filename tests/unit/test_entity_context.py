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


class TestEntityContextWithAutomations:
    @pytest.mark.asyncio
    async def test_entity_referenced_by_automations(self, mock_mcp, config_path, ha_url, ha_token):
        import os

        storage_dir = config_path + "/.storage"
        os.makedirs(storage_dir, exist_ok=True)

        with open(storage_dir + "/core.entity_registry", "w") as f:
            f.write(
                json.dumps(
                    {
                        "data": {
                            "entities": [
                                {
                                    "entity_id": "light.kitchen",
                                    "platform": "hue",
                                    "device_id": "dev_kitchen",
                                },
                                {
                                    "entity_id": "automation.kitchen_auto",
                                    "platform": "automation",
                                    "device_id": None,
                                    "original_name": "Kitchen Lights Auto",
                                },
                                {
                                    "entity_id": "automation.motion_auto",
                                    "platform": "automation",
                                    "device_id": None,
                                    "original_name": "Motion Trigger",
                                },
                            ]
                        }
                    }
                )
            )

        with open(storage_dir + "/core.device_registry", "w") as f:
            f.write(
                json.dumps(
                    {
                        "data": {
                            "devices": [
                                {
                                    "id": "dev_kitchen",
                                    "name": "Kitchen Light",
                                    "manufacturer": "Philips",
                                    "area_id": "kitchen",
                                }
                            ]
                        }
                    }
                )
            )

        # Write automations.yaml that references the entity
        automations_path = os.path.join(config_path, "automations.yaml")
        with open(automations_path, "w") as f:
            f.write(
                json.dumps(
                    [
                        {
                            "id": "auto1",
                            "alias": "Kitchen Lights On",
                            "trigger": [
                                {"platform": "state", "entity_id": "light.kitchen", "to": "on"}
                            ],
                        }
                    ]
                )
            )

        tools = {}
        register_entity_context_tools(mock_mcp, config_path, ha_url, ha_token)
        tools = mock_mcp._tools

        state_response = {
            "success": True,
            "data": {
                "entity_id": "light.kitchen",
                "state": "on",
                "attributes": {"friendly_name": "Kitchen Light"},
                "last_changed": "2024-01-01T00:00:00Z",
                "last_updated": "2024-01-01T00:00:00Z",
            },
        }

        def mock_request(url, token, endpoint, **kwargs):
            if "states/light.kitchen" in endpoint:
                return state_response
            if "history" in endpoint:
                return {"success": True, "data": [[]]}
            if "logbook" in endpoint:
                return {"success": True, "data": []}
            return {"success": True, "data": []}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            with patch("tools.entity_context.load_registry", return_value={"data": {}}):
                result = await tools["entity_get_context_tree"]("light.kitchen")
                data = json.loads(result)

        assert data["success"] is True
        assert data["context_tree"]["entity_id"] == "light.kitchen"
        assert "affecting_automations" in data["context_tree"]
        assert len(data["context_tree"]["affecting_automations"]) > 0


class TestEntityContextSourceBreakdown:
    @pytest.mark.asyncio
    async def test_history_extraction(self, tools, config_path):
        import os

        storage_dir = os.path.join(config_path, ".storage")
        os.makedirs(storage_dir, exist_ok=True)

        with open(os.path.join(storage_dir, "core.entity_registry"), "w") as f:
            json.dump(
                {
                    "data": {
                        "entities": [
                            {
                                "entity_id": "light.test_entity",
                                "platform": "hue",
                                "device_id": "dev1",
                            }
                        ]
                    }
                },
                f,
            )

        with open(os.path.join(storage_dir, "core.device_registry"), "w") as f:
            json.dump({"data": {"devices": [{"id": "dev1", "name": "Test Device"}]}}, f)

        state_response = {
            "success": True,
            "data": {
                "entity_id": "light.test_entity",
                "state": "on",
                "attributes": {"friendly_name": "Test Entity"},
                "last_changed": "2024-01-01T10:00:00Z",
                "last_updated": "2024-01-01T10:00:00Z",
            },
        }

        history_data = [
            [
                {
                    "state": "off",
                    "last_changed": "2024-01-01T08:00:00Z",
                    "attributes": {"brightness": 0},
                },
                {
                    "state": "on",
                    "last_changed": "2024-01-01T09:00:00Z",
                    "attributes": {"brightness": 255},
                },
                {
                    "state": "off",
                    "last_changed": "2024-01-01T10:00:00Z",
                    "attributes": {"brightness": 0},
                },
            ]
        ]

        def mock_request(url, token, endpoint, **kwargs):
            if "/api/states/light.test_entity" in endpoint:
                return state_response
            if "history" in endpoint:
                return {"success": True, "data": history_data}
            if "logbook" in endpoint:
                return {"success": True, "data": []}
            return {"success": True, "data": []}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            with patch("tools.entity_context.load_registry", return_value={"data": {}}):
                result = await tools["entity_get_context_tree"]("light.test_entity")
                data = json.loads(result)

        assert data["success"] is True
        changes = data["context_tree"]["recent_changes"]
        assert changes["total_history_entries"] == 3

    @pytest.mark.asyncio
    async def test_source_breakdown_automation(self, tools, config_path):
        import os

        storage_dir = os.path.join(config_path, ".storage")
        os.makedirs(storage_dir, exist_ok=True)

        with open(os.path.join(storage_dir, "core.entity_registry"), "w") as f:
            json.dump(
                {
                    "data": {
                        "entities": [
                            {
                                "entity_id": "light.test_entity",
                                "platform": "hue",
                                "device_id": "dev1",
                            },
                            {
                                "entity_id": "automation.test_automation",
                                "platform": "automation",
                                "device_id": None,
                                "original_name": "Test Automation",
                            },
                        ]
                    }
                },
                f,
            )

        with open(os.path.join(storage_dir, "core.device_registry"), "w") as f:
            json.dump({"data": {"devices": [{"id": "dev1", "name": "Test Device"}]}}, f)

        state_response = {
            "success": True,
            "data": {
                "entity_id": "light.test_entity",
                "state": "on",
                "attributes": {"friendly_name": "Test Entity"},
                "last_changed": "2024-01-01T10:00:00Z",
                "last_updated": "2024-01-01T10:00:00Z",
            },
        }

        logbook_data = [
            {
                "entity_id": "light.test_entity",
                "when": "2024-01-01T09:00:00Z",
                "name": "Test Entity turned on",
                "message": "turned on by automation Test Automation",
                "domain": "light",
                "context": {"id": "ctx1"},
            },
            {
                "entity_id": "light.test_entity",
                "when": "2024-01-01T10:00:00Z",
                "name": "Test Entity turned off",
                "message": "turned off triggered by automation",
                "domain": "automation",
                "context": {"id": "ctx2"},
            },
        ]

        def mock_request(url, token, endpoint, **kwargs):
            if "/api/states/light.test_entity" in endpoint:
                return state_response
            if "history" in endpoint:
                return {"success": True, "data": [[]]}
            if "logbook" in endpoint:
                return {"success": True, "data": logbook_data}
            return {"success": True, "data": []}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            with patch("tools.entity_context.load_registry", return_value={"data": {}}):
                result = await tools["entity_get_context_tree"]("light.test_entity")
                data = json.loads(result)

        assert data["success"] is True
        sources = data["context_tree"]["sources_breakdown"]
        assert "automation" in sources
        assert sources["automation"]["count"] == 2

    @pytest.mark.asyncio
    async def test_source_breakdown_mixed_sources(self, tools, config_path):
        import os

        storage_dir = os.path.join(config_path, ".storage")
        os.makedirs(storage_dir, exist_ok=True)

        with open(os.path.join(storage_dir, "core.entity_registry"), "w") as f:
            json.dump(
                {
                    "data": {
                        "entities": [
                            {
                                "entity_id": "light.test_entity",
                                "platform": "hue",
                                "device_id": "dev1",
                            }
                        ]
                    }
                },
                f,
            )

        with open(os.path.join(storage_dir, "core.device_registry"), "w") as f:
            json.dump({"data": {"devices": [{"id": "dev1", "name": "Test Device"}]}}, f)

        state_response = {
            "success": True,
            "data": {
                "entity_id": "light.test_entity",
                "state": "on",
                "attributes": {"friendly_name": "Test Entity"},
                "last_changed": "2024-01-01T10:00:00Z",
                "last_updated": "2024-01-01T10:00:00Z",
            },
        }

        logbook_data = [
            {
                "entity_id": "light.test_entity",
                "when": "2024-01-01T08:00:00Z",
                "name": "Automation triggered",
                "message": "turned on by automation Test Automation",
                "domain": "light",
                "context": {"id": "ctx1"},
            },
            {
                "entity_id": "light.test_entity",
                "when": "2024-01-01T09:00:00Z",
                "name": "Script executed",
                "message": "executed script morning_routine",
                "domain": "script",
                "context": {"id": "ctx2"},
            },
            {
                "entity_id": "light.test_entity",
                "when": "2024-01-01T10:00:00Z",
                "name": "User action",
                "message": "turned off",
                "domain": "light",
                "context": {"id": "ctx3"},
            },
            {
                "entity_id": "light.test_entity",
                "when": "2024-01-01T11:00:00Z",
                "name": "Device update",
                "message": "state changed",
                "domain": "sensor",
                "context": {"id": "ctx4"},
            },
            {
                "entity_id": "light.test_entity",
                "when": "2024-01-01T12:00:00Z",
                "name": "Unknown event",
                "message": "battery low",
                "domain": "sensor",
                "context": {"id": "ctx5"},
            },
        ]

        def mock_request(url, token, endpoint, **kwargs):
            if "/api/states/light.test_entity" in endpoint:
                return state_response
            if "history" in endpoint:
                return {"success": True, "data": [[]]}
            if "logbook" in endpoint:
                return {"success": True, "data": logbook_data}
            return {"success": True, "data": []}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            with patch("tools.entity_context.load_registry", return_value={"data": {}}):
                result = await tools["entity_get_context_tree"]("light.test_entity")
                data = json.loads(result)

        assert data["success"] is True
        sources = data["context_tree"]["sources_breakdown"]
        assert sources["automation"]["count"] == 1
        assert sources["script"]["count"] == 1
        assert sources["user_action"]["count"] == 1
        assert sources["device_update"]["count"] == 1
        assert sources["unknown"]["count"] == 1
