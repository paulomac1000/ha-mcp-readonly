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


class TestGetContextChain:
    """Tests for get_context_chain tool — recursive context parent_id chain tracing."""

    def _make_logbook_entry(self, ctx_id, parent_id, entity_id, when=None):
        entry = {
            "context_id": ctx_id,
            "context_parent_id": parent_id,
            "entity_id": entity_id,
            "name": f"Event {ctx_id}",
            "message": "state changed",
            "domain": entity_id.split(".")[0] if "." in entity_id else "unknown",
        }
        if when:
            entry["when"] = when
        return entry

    @pytest.mark.asyncio
    async def test_invalid_entity_id(self, tools):
        result = await tools["get_context_chain"]("invalid_no_dot")
        data = json.loads(result)
        assert data["success"] is False
        assert "Invalid entity_id" in data["error"]

    @pytest.mark.asyncio
    async def test_empty_entity_id(self, tools):
        result = await tools["get_context_chain"]("")
        data = json.loads(result)
        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_logbook_api_failure(self, tools):
        with patch(
            "tools.entity_context.make_ha_request",
            return_value={"success": False, "error": "API error"},
        ):
            result = await tools["get_context_chain"]("light.test")
            data = json.loads(result)
        assert data["success"] is False
        assert "Failed to fetch" in data["error"]

    @pytest.mark.asyncio
    async def test_no_entity_entries(self, tools):
        """Entity has no logbook entries → empty chain."""
        full_entries = [
            self._make_logbook_entry("ctx_001", None, "automation.morning", "2025-01-01T08:00:00Z"),
        ]

        def mock_request(url, token, endpoint, **kwargs):
            if "?entity=" in endpoint:
                return {"success": True, "data": []}
            return {"success": True, "data": full_entries}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            result = await tools["get_context_chain"]("light.test")
            data = json.loads(result)

        assert data["success"] is True
        assert data["chain"] == []
        assert data["chain_length"] == 0

    @pytest.mark.asyncio
    async def test_successful_chain_two_levels(self, tools):
        """Chain: automation triggers → light changes."""
        when = "2025-01-01T08:00:00Z"
        entity_entries = [
            self._make_logbook_entry("ctx_002", "ctx_001", "light.living_room", when),
        ]
        full_entries = [
            self._make_logbook_entry("ctx_001", None, "automation.morning", when),
            self._make_logbook_entry("ctx_002", "ctx_001", "light.living_room", when),
        ]

        def mock_request(url, token, endpoint, **kwargs):
            if "?entity=" in endpoint:
                return {"success": True, "data": entity_entries}
            return {"success": True, "data": full_entries}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            result = await tools["get_context_chain"]("light.living_room")
            data = json.loads(result)

        assert data["success"] is True
        assert data["chain_length"] == 2

        chain = data["chain"]
        assert chain[0]["context_id"] == "ctx_002"
        assert chain[0]["entity_id"] == "light.living_room"
        assert chain[0]["depth"] == 0
        assert chain[0]["parent_id"] == "ctx_001"
        assert chain[0]["timestamp"] == when

        assert chain[1]["context_id"] == "ctx_001"
        assert chain[1]["entity_id"] == "automation.morning"
        assert chain[1]["depth"] == 1
        assert chain[1]["parent_id"] is None

    @pytest.mark.asyncio
    async def test_successful_chain_three_levels(self, tools):
        """Chain: automation → script → light."""
        when = "2025-06-01T12:00:00Z"
        entity_entries = [
            self._make_logbook_entry("ctx_003", "ctx_002", "light.living_room", when),
        ]
        full_entries = [
            self._make_logbook_entry("ctx_001", None, "automation.morning", when),
            self._make_logbook_entry("ctx_002", "ctx_001", "script.blink", when),
            self._make_logbook_entry("ctx_003", "ctx_002", "light.living_room", when),
        ]

        def mock_request(url, token, endpoint, **kwargs):
            if "?entity=" in endpoint:
                return {"success": True, "data": entity_entries}
            return {"success": True, "data": full_entries}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            result = await tools["get_context_chain"]("light.living_room", depth=5)
            data = json.loads(result)

        assert data["success"] is True
        assert data["chain_length"] == 3
        depths = [e["depth"] for e in data["chain"]]
        assert depths == [0, 1, 2]
        entities = [e["entity_id"] for e in data["chain"]]
        assert entities == ["light.living_room", "script.blink", "automation.morning"]

    @pytest.mark.asyncio
    async def test_depth_capping(self, tools):
        """depth=1 should stop after entity's own context level."""
        when = "2025-01-01T08:00:00Z"
        entity_entries = [
            self._make_logbook_entry("ctx_003", "ctx_002", "light.test", when),
        ]
        full_entries = [
            self._make_logbook_entry("ctx_001", None, "automation.root", when),
            self._make_logbook_entry("ctx_002", "ctx_001", "script.mid", when),
            self._make_logbook_entry("ctx_003", "ctx_002", "light.test", when),
        ]

        def mock_request(url, token, endpoint, **kwargs):
            if "?entity=" in endpoint:
                return {"success": True, "data": entity_entries}
            return {"success": True, "data": full_entries}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            result = await tools["get_context_chain"]("light.test", depth=1)
            data = json.loads(result)

        assert data["success"] is True
        assert data["chain_length"] == 2
        depths = [e["depth"] for e in data["chain"]]
        assert depths == [0, 1]

    @pytest.mark.asyncio
    async def test_depth_zero(self, tools):
        """depth=0 returns only the entity's own logbook entries, no recursion."""
        when = "2025-01-01T08:00:00Z"
        entity_entries = [
            self._make_logbook_entry("ctx_002", "ctx_001", "light.test", when),
        ]
        full_entries = [
            self._make_logbook_entry("ctx_001", None, "automation.root", when),
            self._make_logbook_entry("ctx_002", "ctx_001", "light.test", when),
        ]

        def mock_request(url, token, endpoint, **kwargs):
            if "?entity=" in endpoint:
                return {"success": True, "data": entity_entries}
            return {"success": True, "data": full_entries}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            result = await tools["get_context_chain"]("light.test", depth=0)
            data = json.loads(result)

        assert data["success"] is True
        assert data["chain_length"] == 1
        assert data["chain"][0]["depth"] == 0
        assert data["chain"][0]["context_id"] == "ctx_002"

    @pytest.mark.asyncio
    async def test_depth_exceeds_cap(self, tools):
        """Passing depth=10 should be silently capped at 5."""
        when = "2025-01-01T08:00:00Z"
        entity_entries = [
            self._make_logbook_entry("ctx_003", "ctx_002", "light.test", when),
        ]
        full_entries = [
            self._make_logbook_entry("ctx_001", None, "automation.root", when),
            self._make_logbook_entry("ctx_002", "ctx_001", "script.mid", when),
            self._make_logbook_entry("ctx_003", "ctx_002", "light.test", when),
        ]

        def mock_request(url, token, endpoint, **kwargs):
            if "?entity=" in endpoint:
                return {"success": True, "data": entity_entries}
            return {"success": True, "data": full_entries}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            result = await tools["get_context_chain"]("light.test", depth=10)
            data = json.loads(result)

        assert data["success"] is True
        max_depth = max(e["depth"] for e in data["chain"])
        assert max_depth <= 5

    @pytest.mark.asyncio
    async def test_missing_parent_context(self, tools):
        """Parent context not in logbook → terminal node with note."""
        when = "2025-01-01T08:00:00Z"
        entity_entries = [
            self._make_logbook_entry("ctx_002", "ctx_missing", "light.test", when),
        ]
        full_entries = [
            self._make_logbook_entry("ctx_002", "ctx_missing", "light.test", when),
        ]

        def mock_request(url, token, endpoint, **kwargs):
            if "?entity=" in endpoint:
                return {"success": True, "data": entity_entries}
            return {"success": True, "data": full_entries}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            result = await tools["get_context_chain"]("light.test")
            data = json.loads(result)

        assert data["success"] is True
        assert data["chain_length"] == 2
        terminal = data["chain"][1]
        assert terminal["context_id"] == "ctx_missing"
        assert terminal["parent_id"] is None
        assert terminal["entity_id"] is None
        assert "Parent context not found" in terminal["note"]

    @pytest.mark.asyncio
    async def test_no_timestamps(self, tools):
        """include_timestamps=False omits timestamp field."""
        entity_entries = [
            self._make_logbook_entry("ctx_002", None, "light.test", "2025-01-01T08:00:00Z"),
        ]
        full_entries = [
            self._make_logbook_entry("ctx_002", None, "light.test", "2025-01-01T08:00:00Z"),
        ]

        def mock_request(url, token, endpoint, **kwargs):
            if "?entity=" in endpoint:
                return {"success": True, "data": entity_entries}
            return {"success": True, "data": full_entries}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            result = await tools["get_context_chain"]("light.test", include_timestamps=False)
            data = json.loads(result)

        assert data["success"] is True
        assert data["chain_length"] == 1
        assert "timestamp" not in data["chain"][0]
        assert "context_id" in data["chain"][0]
        assert "entity_id" in data["chain"][0]
        assert "depth" in data["chain"][0]

    @pytest.mark.asyncio
    async def test_unfiltered_logbook_failure_graceful(self, tools):
        """Unfiltered logbook fails but entity-filtered succeeds → partial chain."""
        entity_entries = [
            self._make_logbook_entry("ctx_002", "ctx_001", "light.test"),
        ]

        def mock_request(url, token, endpoint, **kwargs):
            if "?entity=" in endpoint:
                return {"success": True, "data": entity_entries}
            return {"success": False, "data": None}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            result = await tools["get_context_chain"]("light.test")
            data = json.loads(result)

        assert data["success"] is True
        assert data["chain_length"] == 2
        assert data["chain"][0]["context_id"] == "ctx_002"
        assert data["chain"][0]["depth"] == 0
        assert data["chain"][1]["context_id"] == "ctx_001"
        assert "Parent context not found" in data["chain"][1]["note"]

    @pytest.mark.asyncio
    async def test_multiple_entity_entries(self, tools):
        """Entity has multiple logbook entries — chain should start from each."""
        when = "2025-01-01T08:00:00Z"
        entity_entries = [
            self._make_logbook_entry("ctx_002", "ctx_001", "light.test", when),
            self._make_logbook_entry("ctx_004", "ctx_003", "light.test", when),
        ]
        full_entries = [
            self._make_logbook_entry("ctx_001", None, "automation.a", when),
            self._make_logbook_entry("ctx_002", "ctx_001", "light.test", when),
            self._make_logbook_entry("ctx_003", None, "automation.b", when),
            self._make_logbook_entry("ctx_004", "ctx_003", "light.test", when),
        ]

        def mock_request(url, token, endpoint, **kwargs):
            if "?entity=" in endpoint:
                return {"success": True, "data": entity_entries}
            return {"success": True, "data": full_entries}

        with patch("tools.entity_context.make_ha_request", side_effect=mock_request):
            result = await tools["get_context_chain"]("light.test", depth=3)
            data = json.loads(result)

        assert data["success"] is True
        assert data["chain_length"] == 4
        ctx_ids = [e["context_id"] for e in data["chain"]]
        assert "ctx_001" in ctx_ids
        assert "ctx_002" in ctx_ids
        assert "ctx_003" in ctx_ids
        assert "ctx_004" in ctx_ids

    @pytest.mark.asyncio
    async def test_exception_handler(self, tools):
        with patch(
            "tools.entity_context._do_get_context_chain",
            side_effect=RuntimeError("Unexpected failure"),
        ):
            result = await tools["get_context_chain"]("light.test")
            data = json.loads(result)
        assert data["success"] is False
        assert "Unexpected failure" in data["error"]
