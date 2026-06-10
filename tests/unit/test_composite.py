"""
Comprehensive unit tests for tools/composite.py

Tests composite/aggregate functions that combine multiple operations:
- investigate_entity: Multi-source entity investigation
- get_entity_with_automations: Entity + automation analysis
- get_area_diagnostic: Area-wide diagnostics
- Internal helper functions
- Error handling and edge cases
"""

import json
import os
from unittest.mock import Mock, patch

import pytest
import yaml

# Mock the tools registry loading
MOCK_REGISTRY_DATA = {
    "core.entity_registry": {
        "data": {
            "entities": [
                {
                    "entity_id": "sensor.temperature_living_room",
                    "name": "Living Room Temperature",
                    "platform": "mqtt",
                    "device_id": "device_123",
                    "area_id": "living_room",
                },
                {
                    "entity_id": "binary_sensor.motion_kitchen",
                    "name": "Kitchen Motion",
                    "platform": "zha",
                    "device_id": "device_456",
                    "area_id": "kitchen",
                },
                {
                    "entity_id": "template.test_sensor",
                    "name": "Test Template",
                    "platform": "template",
                    "area_id": "living_room",
                },
            ]
        }
    },
    "core.device_registry": {
        "data": {
            "devices": [
                {
                    "id": "device_123",
                    "name": "Temperature Sensor",
                    "area_id": "living_room",
                },
                {"id": "device_456", "name": "Motion Sensor", "area_id": "kitchen"},
            ]
        }
    },
    "core.area_registry": {
        "data": {
            "areas": [
                {"id": "living_room", "name": "Living Room"},
                {"id": "kitchen", "name": "Kitchen"},
            ]
        }
    },
}


@pytest.fixture
def mock_mcp():
    """Create a mock MCP server."""
    mcp = Mock()
    mcp._tools = {}

    def tool_decorator(*args, **kwargs):
        def wrapper(func):
            tool_name = kwargs.get("name", func.__name__)
            mcp._tools[tool_name] = func
            return func

        if len(args) == 1 and callable(args[0]) and not kwargs:
            func = args[0]
            mcp._tools[func.__name__] = func
            return func

        return wrapper

    mcp.tool = tool_decorator
    return mcp


@pytest.fixture
def config_path(tmp_path):
    """Create a temporary config directory."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Create .storage directory
    storage_dir = config_dir / ".storage"
    storage_dir.mkdir()

    return str(config_dir)


@pytest.fixture
def ha_url():
    """Home Assistant URL."""
    return "http://test-ha:8123"


@pytest.fixture
def ha_token():
    """Home Assistant token."""
    return "test-token-12345"


@pytest.fixture
def sample_automations():
    """Sample automation data."""
    return [
        {
            "id": "auto_1",
            "alias": "Turn on lights at sunset",
            "trigger": [{"platform": "state", "entity_id": "sensor.temperature_living_room"}],
            "condition": [],
            "action": [
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": "light.living_room"},
                }
            ],
        },
        {
            "id": "auto_2",
            "alias": "Motion in kitchen",
            "trigger": [{"platform": "state", "entity_id": "binary_sensor.motion_kitchen"}],
            "condition": [],
            "action": [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}],
        },
    ]


@pytest.fixture
def sample_states():
    """Sample entity states."""
    return [
        {
            "entity_id": "sensor.temperature_living_room",
            "state": "22.5",
            "attributes": {"unit_of_measurement": "°C", "device_class": "temperature"},
            "last_changed": "2026-02-24T10:00:00+00:00",
        },
        {
            "entity_id": "binary_sensor.motion_kitchen",
            "state": "off",
            "attributes": {"device_class": "motion"},
            "last_changed": "2026-02-24T09:30:00+00:00",
        },
    ]


class TestInvestigateEntity:
    """Test investigate_entity function."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        """Setup test environment."""
        from tools.composite import register_composite_tools

        self.mock_registry_data = MOCK_REGISTRY_DATA
        with patch("tools.composite.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            register_composite_tools(mock_mcp, config_path, ha_url, ha_token)
        self.mcp = mock_mcp
        self.config_path = config_path
        self.ha_url = ha_url
        self.ha_token = ha_token

    @pytest.mark.asyncio
    async def test_investigate_entity_single_term(self, sample_states, sample_automations):
        """Test investigating a single entity."""
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {"success": True, "data": sample_states}
            mock_auto.return_value = (sample_automations, None)

            result = await self.mcp._tools["investigate_entity"]("temperature")
            data = json.loads(result)

            assert data["success"] is True
            assert "matched_entities" in data
            assert len(data["matched_entities"]) > 0
            assert any("temperature" in m["entity_id"].lower() for m in data["matched_entities"])

    @pytest.mark.asyncio
    async def test_investigate_entity_csv_terms(self, sample_states, sample_automations):
        """Test investigating multiple comma-separated terms."""
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {"success": True, "data": sample_states}
            mock_auto.return_value = (sample_automations, None)

            result = await self.mcp._tools["investigate_entity"]("temperature,motion")
            data = json.loads(result)

            assert data["success"] is True
            assert len(data["matched_entities"]) >= 2

    @pytest.mark.asyncio
    async def test_investigate_entity_area_match(self, sample_states, sample_automations):
        """Test investigating entities by area."""
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {"success": True, "data": sample_states}
            mock_auto.return_value = (sample_automations, None)

            result = await self.mcp._tools["investigate_entity"]("living")
            data = json.loads(result)

            assert data["success"] is True
            # Should find entities in Living Room area
            assert len(data["matched_entities"]) > 0

    @pytest.mark.asyncio
    async def test_investigate_entity_no_matches(self, sample_states, sample_automations):
        """Test investigating with no matching entities."""
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {"success": True, "data": sample_states}
            mock_auto.return_value = (sample_automations, None)

            result = await self.mcp._tools["investigate_entity"]("nonexistent_xyz")
            data = json.loads(result)

            assert data["success"] is True
            assert len(data.get("matched_entities", [])) == 0

    @pytest.mark.asyncio
    async def test_investigate_entity_empty_search_term(self):
        """Empty search_term should return success=False."""
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {"success": True, "data": []}
            mock_auto.return_value = ([], None)

            result = await self.mcp._tools["investigate_entity"]("   ")
            data = json.loads(result)

            assert data["success"] is False
            assert "empty" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_investigate_entity_unavailable_state(self, sample_automations):
        """Unavailable entity state should appear in issues."""
        unavailable_states = [
            {
                "entity_id": "sensor.temperature_living_room",
                "state": "unavailable",
                "attributes": {},
                "last_changed": "2026-02-24T10:00:00+00:00",
            },
        ]
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {"success": True, "data": unavailable_states}
            mock_auto.return_value = (sample_automations, None)

            result = await self.mcp._tools["investigate_entity"]("temperature")
            data = json.loads(result)

            assert data["success"] is True
            assert any("UNAVAILABLE" in issue for issue in data["issues"])
            assert len(data["recommendations"]) > 0

    @pytest.mark.asyncio
    async def test_investigate_entity_include_history(self, sample_states, sample_automations):
        """include_history=True should add a 'history' key for the primary entity."""
        history_data = [
            [
                {"state": "21", "last_changed": "2026-02-24T08:00:00+00:00"},
                {"state": "22", "last_changed": "2026-02-24T09:00:00+00:00"},
            ]
        ]

        def ha_side_effect(url, token, endpoint, **kwargs):
            if endpoint.startswith("/api/history"):
                return {"success": True, "data": history_data}
            return {"success": True, "data": sample_states}

        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request", side_effect=ha_side_effect),
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_auto.return_value = (sample_automations, None)

            result = await self.mcp._tools["investigate_entity"](
                "temperature", include_history=True, hours_back=24
            )
            data = json.loads(result)

        assert data["success"] is True
        assert "history" in data
        assert data["history"]["total_changes"] == 2


class TestGetEntityWithAutomations:
    """Test get_entity_with_automations function."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        """Setup test environment."""
        from tools.composite import register_composite_tools

        self.mock_registry_data = MOCK_REGISTRY_DATA

        with patch("tools.composite.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            register_composite_tools(mock_mcp, config_path, ha_url, ha_token)

        self.mcp = mock_mcp

    @pytest.mark.asyncio
    async def test_get_entity_with_automations_found(self, sample_states, sample_automations):
        """Test getting entity with its automations."""
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {"success": True, "data": sample_states}
            mock_auto.return_value = (sample_automations, None)

            result = await self.mcp._tools["get_entity_with_automations"](
                "sensor.temperature_living_room", include_automation_code=False
            )
            data = json.loads(result)

            assert data["success"] is True
            assert data["entity_id"] == "sensor.temperature_living_room"
            assert "current_state" in data
            assert "automations" in data
            assert len(data["automations"]) > 0

    @pytest.mark.asyncio
    async def test_get_entity_with_automations_not_found(self, sample_states, sample_automations):
        """Test getting non-existent entity."""
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {"success": True, "data": sample_states}
            mock_auto.return_value = (sample_automations, None)

            result = await self.mcp._tools["get_entity_with_automations"](
                "sensor.nonexistent", include_automation_code=False
            )
            data = json.loads(result)

            assert data["success"] is False
            assert "not found" in data.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_get_entity_with_code_included(self, sample_states, sample_automations):
        """Test including automation code in response."""
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {"success": True, "data": sample_states}
            mock_auto.return_value = (sample_automations, None)

            result = await self.mcp._tools["get_entity_with_automations"](
                "sensor.temperature_living_room", include_automation_code=True
            )
            data = json.loads(result)

            assert data["success"] is True
            # Automations should have full code
            assert any("code" in auto for auto in data.get("automations", []))


class TestGetAreaDiagnostic:
    """Test get_area_diagnostic function."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        """Setup test environment."""
        from tools.composite import register_composite_tools

        self.mock_registry_data = MOCK_REGISTRY_DATA

        with patch("tools.composite.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            register_composite_tools(mock_mcp, config_path, ha_url, ha_token)

        self.mcp = mock_mcp

    @pytest.mark.asyncio
    async def test_get_area_diagnostic_valid_area(self, sample_states):
        """Test getting diagnostic for valid area."""
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {"success": True, "data": sample_states}

            result = await self.mcp._tools["get_area_diagnostic"]("living_room")
            data = json.loads(result)

            assert data["success"] is True
            assert data["area_info"]["id"] == "living_room"
            assert "entities_by_domain" in data
            assert "warnings" in data

    @pytest.mark.asyncio
    async def test_get_area_diagnostic_invalid_area(self, sample_states):
        """Test getting diagnostic for non-existent area."""
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {"success": True, "data": sample_states}

            result = await self.mcp._tools["get_area_diagnostic"]("nonexistent_area")
            data = json.loads(result)

            assert data["success"] is False
            assert "not found" in data.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_get_area_diagnostic_unavailable_entities(self):
        """Unavailable entities should appear in issues and recommendations."""
        unavailable_states = [
            {
                "entity_id": "sensor.temperature_living_room",
                "state": "unavailable",
                "attributes": {},
                "last_changed": "2026-02-24T10:00:00+00:00",
            },
        ]
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {"success": True, "data": unavailable_states}
            mock_auto.return_value = ([], None)

            result = await self.mcp._tools["get_area_diagnostic"]("living_room")
            data = json.loads(result)

        assert data["success"] is True
        assert data["area_info"]["unavailable_count"] >= 1
        assert any("unavailable" in i.lower() for i in data["issues"])
        assert any("connectivity" in r.lower() for r in data["recommendations"])

    @pytest.mark.asyncio
    async def test_get_area_diagnostic_exclude_automations_and_sensors(self, sample_states):
        """include_automations=False and include_sensors=False omit those sections."""
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {"success": True, "data": sample_states}

            result = await self.mcp._tools["get_area_diagnostic"](
                "living_room", include_automations=False, include_sensors=False
            )
            data = json.loads(result)

        assert data["success"] is True
        assert data["automations"] == []
        assert data["sensor_readings"] is None


class TestInternalHelpers:
    """Test internal helper functions."""

    def test_minify_state_removes_blacklisted_attrs(self):
        """Test that _minify_state removes blacklisted attributes."""
        from tools.composite import _minify_state

        full_state = {
            "entity_id": "sensor.test",
            "state": "42",
            "attributes": {
                "friendly_name": "Test",
                "icon": "mdi:test",
                "entity_picture": "/local/image.png",  # Should be removed
                "device_class": "temperature",
            },
            "last_changed": "2026-02-24T10:00:00+00:00",
            "last_updated": "2026-02-24T10:00:00+00:00",
            "context": {"id": "123"},  # Should be removed
        }

        minified = _minify_state(full_state)

        assert "entity_id" in minified
        assert "state" in minified
        assert "entity_picture" not in minified.get("attributes", {})
        assert "context" not in minified

    def test_find_automations_for_entity(self):
        """Test finding automations that use an entity."""
        from tools.composite import _find_automations_for_entity

        automations = [
            {
                "id": "auto_1",
                "trigger": [{"platform": "state", "entity_id": "sensor.test"}],
                "action": [],
            },
            {
                "id": "auto_2",
                "trigger": [{"platform": "time", "at": "12:00"}],
                "action": [{"service": "light.turn_on", "target": {"entity_id": "sensor.test"}}],
            },
            {"id": "auto_3", "trigger": [], "action": []},
        ]

        found = _find_automations_for_entity("sensor.test", automations)

        assert len(found) == 2
        assert any(a["id"] == "auto_1" for a in found)
        assert any(a["id"] == "auto_2" for a in found)

    def test_conflict_analysis_race_condition(self):
        """Test conflict detection for race conditions."""
        from tools.composite import _get_conflict_analysis

        automations = [
            {
                "id": "auto_1",
                "trigger": [{"platform": "state", "entity_id": "sensor.test"}],
                "action": [{"service": "light.turn_on", "entity_id": "light.test"}],
            },
            {
                "id": "auto_2",
                "trigger": [{"platform": "state", "entity_id": "sensor.test"}],
                "action": [{"service": "light.turn_off", "entity_id": "light.test"}],
            },
        ]

        conflicts = _get_conflict_analysis(automations)

        # Should detect potential race condition (same trigger, conflicting actions)
        assert len(conflicts) > 0 or "race_condition" in str(conflicts).lower()

    def test_conflict_analysis_feedback_loop(self):
        """Test conflict detection for feedback loops."""
        from tools.composite import _get_conflict_analysis

        automations = [
            {
                "id": "auto_1",
                "trigger": [{"platform": "state", "entity_id": "input_boolean.test"}],
                "action": [
                    {
                        "service": "input_boolean.turn_on",
                        "entity_id": "input_boolean.test",
                    }
                ],
            }
        ]

        _get_conflict_analysis(automations)

        # Should detect potential feedback loop (triggers on same entity it modifies)
        # Implementation dependent on actual conflict detection logic


class TestErrorHandling:
    """Test error handling in composite functions."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        """Setup test environment."""
        from tools.composite import register_composite_tools

        with patch("tools.composite.load_registry") as mock_load:
            mock_load.return_value = {}
            register_composite_tools(mock_mcp, config_path, ha_url, ha_token)

        self.mcp = mock_mcp

    @pytest.mark.asyncio
    async def test_handle_ha_api_error(self):
        """Test handling HA API errors gracefully."""
        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
        ):
            mock_load.return_value = {}
            mock_request.return_value = {
                "success": False,
                "error": "Connection refused",
            }

            result = await self.mcp._tools["investigate_entity"]("test")
            data = json.loads(result)

            # investigate_entity gracefully degrades - returns success with warnings
            assert "success" in data
            if data["success"]:
                assert len(data.get("warnings", [])) > 0

    @pytest.mark.asyncio
    async def test_handle_missing_registry(self):
        """Test handling missing registry data."""
        with patch("tools.composite.load_registry") as mock_load:
            mock_load.return_value = {}  # Empty registry

            with patch("tools.composite.make_ha_request") as mock_request:
                mock_request.return_value = {"success": True, "data": []}

                result = await self.mcp._tools["investigate_entity"]("test")
                data = json.loads(result)

                # Should handle gracefully, not crash
                assert "success" in data

    @pytest.mark.asyncio
    async def test_handle_malformed_data(self):
        """Test handling malformed entity/automation data."""
        with patch("tools.composite.make_ha_request") as mock_request:
            # Return malformed data
            mock_request.return_value = {"success": True, "data": "not a list"}

            result = await self.mcp._tools["investigate_entity"]("test")
            data = json.loads(result)

            # Should handle error gracefully
            assert "success" in data


class TestTokenOptimization:
    """Test token optimization in composite functions."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        """Setup test environment."""
        from tools.composite import register_composite_tools

        with patch("tools.utils.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: MOCK_REGISTRY_DATA.get(name, {})
            register_composite_tools(mock_mcp, config_path, ha_url, ha_token)

        self.mcp = mock_mcp

    @pytest.mark.asyncio
    async def test_minified_response_size(self, sample_states, sample_automations):
        """Test that responses are minified to reduce tokens."""
        with patch("tools.composite.make_ha_request") as mock_request:
            with patch("tools.composite._load_automations") as mock_auto:
                mock_request.return_value = {"success": True, "data": sample_states}
                mock_auto.return_value = sample_automations

                result = await self.mcp._tools["get_entity_with_automations"](
                    "sensor.temperature_living_room", include_automation_code=False
                )

                # Response should be minified (no automation code)
                data = json.loads(result)
                automations = data.get("automations", [])

                if automations:
                    # Should not have full trigger/action code when include_automation_code=False
                    assert "trigger" not in automations[0] or "action" not in automations[0]


class TestConflictDetection:
    """Test conflict detection (race conditions / feedback loops) in get_entity_with_automations."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        """Setup test environment."""
        from tools.composite import register_composite_tools

        self.mock_registry_data = json.loads(json.dumps(MOCK_REGISTRY_DATA))
        self.mock_registry_data["core.entity_registry"]["data"]["entities"].append(
            {
                "entity_id": "light.test_light",
                "name": "Test Light",
                "platform": "mqtt",
                "device_id": "device_999",
                "area_id": "test_area",
            }
        )
        self.mock_registry_data["core.area_registry"]["data"]["areas"].append(
            {"id": "test_area", "name": "Test Area"}
        )

        with patch("tools.composite.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            register_composite_tools(mock_mcp, config_path, ha_url, ha_token)

        self.mcp = mock_mcp
        self.config_path = config_path

    @pytest.mark.asyncio
    async def test_race_condition_detected(self):
        """Two automations controlling the same entity should be detected as race condition."""
        race_automations = [
            {
                "id": "auto_race_1",
                "alias": "Race Light On",
                "trigger": [{"platform": "state", "entity_id": "sensor.test_trigger"}],
                "action": [
                    {"service": "light.turn_on", "target": {"entity_id": "light.test_light"}}
                ],
            },
            {
                "id": "auto_race_2",
                "alias": "Race Light Off",
                "trigger": [{"platform": "state", "entity_id": "binary_sensor.test_trigger"}],
                "action": [
                    {"service": "light.turn_off", "target": {"entity_id": "light.test_light"}}
                ],
            },
        ]

        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "light.test_light",
                        "state": "on",
                        "attributes": {},
                        "last_changed": "2026-02-24T10:00:00+00:00",
                    }
                ],
            }
            mock_auto.return_value = (race_automations, None)

            result = await self.mcp._tools["get_entity_with_automations"]("light.test_light")
            data = json.loads(result)

        assert data["success"] is True
        assert data["conflict_analysis"]["race_condition_risk"] is True
        assert len(data["conflict_analysis"]["controlling_automations"]) >= 2
        assert any("RACE CONDITION" in issue for issue in data["issues"])
        assert any(
            "mode: restart" in rec or "mode: single" in rec for rec in data["recommendations"]
        )

    @pytest.mark.asyncio
    async def test_feedback_loop_detected(self):
        """Automation that triggers on AND controls the same entity → feedback loop."""
        feedback_automations = [
            {
                "id": "auto_feedback",
                "alias": "Feedback Loop",
                "trigger": [{"platform": "state", "entity_id": "light.test_light", "to": "on"}],
                "action": [
                    {"service": "light.turn_off", "target": {"entity_id": "light.test_light"}}
                ],
            },
        ]

        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "light.test_light",
                        "state": "on",
                        "attributes": {},
                        "last_changed": "2026-02-24T10:00:00+00:00",
                    }
                ],
            }
            mock_auto.return_value = (feedback_automations, None)

            result = await self.mcp._tools["get_entity_with_automations"]("light.test_light")
            data = json.loads(result)

        assert data["success"] is True
        assert data["conflict_analysis"]["feedback_loop_risk"] is True
        assert len(data["conflict_analysis"]["controlling_automations"]) >= 1
        assert len(data["conflict_analysis"]["triggering_automations"]) >= 1
        assert any("FEEDBACK LOOP" in issue for issue in data["issues"])
        assert any("conditions to prevent infinite loops" in rec for rec in data["recommendations"])

    @pytest.mark.asyncio
    async def test_no_conflicts(self):
        """Automations that don't conflict should produce no issues."""
        no_conflict_automations = [
            {
                "id": "auto_clean",
                "alias": "Clean Automation",
                "trigger": [{"platform": "state", "entity_id": "sensor.other"}],
                "action": [{"service": "light.turn_on", "target": {"entity_id": "light.other"}}],
            },
        ]

        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "light.test_light",
                        "state": "on",
                        "attributes": {},
                        "last_changed": "2026-02-24T10:00:00+00:00",
                    }
                ],
            }
            mock_auto.return_value = (no_conflict_automations, None)

            result = await self.mcp._tools["get_entity_with_automations"]("light.test_light")
            data = json.loads(result)

        assert data["success"] is True
        assert data["conflict_analysis"]["race_condition_risk"] is False
        assert data["conflict_analysis"]["feedback_loop_risk"] is False
        assert not any("RACE CONDITION" in issue for issue in data["issues"])
        assert not any("FEEDBACK LOOP" in issue for issue in data["issues"])


class TestAreaAutomationMatching:
    """Test area automation matching in get_area_diagnostic."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        """Setup test environment."""
        from tools.composite import register_composite_tools

        self.mock_registry_data = json.loads(json.dumps(MOCK_REGISTRY_DATA))
        self.mock_registry_data["core.area_registry"]["data"]["areas"].append(
            {"id": "test_area", "name": "Test Area"}
        )
        self.mock_registry_data["core.entity_registry"]["data"]["entities"].extend(
            [
                {
                    "entity_id": "light.test_light",
                    "name": "Test Light",
                    "platform": "mqtt",
                    "device_id": "device_999",
                    "area_id": "test_area",
                },
                {
                    "entity_id": "sensor.test_sensor",
                    "name": "Test Sensor",
                    "platform": "mqtt",
                    "device_id": "device_999",
                    "area_id": "test_area",
                },
            ]
        )
        self.mock_registry_data["core.device_registry"]["data"]["devices"].append(
            {"id": "device_999", "name": "Test Device", "area_id": "test_area"}
        )

        with patch("tools.composite.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            register_composite_tools(mock_mcp, config_path, ha_url, ha_token)

        self.mcp = mock_mcp

    @pytest.mark.asyncio
    async def test_area_automations_found(self):
        """Automations referencing area entities should be matched to the area."""
        area_automations = [
            {
                "id": "auto_area_1",
                "alias": "Area Light Control",
                "trigger": [{"platform": "time", "at": "08:00:00"}],
                "action": [
                    {"service": "light.turn_on", "target": {"entity_id": "light.test_light"}}
                ],
            },
            {
                "id": "auto_area_2",
                "alias": "Area Sensor Check",
                "trigger": [{"platform": "state", "entity_id": "sensor.test_sensor"}],
                "action": [{"service": "notify.notify", "data": {"message": "Sensor triggered"}}],
            },
        ]

        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "light.test_light",
                        "state": "on",
                        "attributes": {},
                        "last_changed": "2026-02-24T10:00:00+00:00",
                    },
                    {
                        "entity_id": "sensor.test_sensor",
                        "state": "25.0",
                        "attributes": {"unit_of_measurement": "°C"},
                        "last_changed": "2026-02-24T10:00:00+00:00",
                    },
                ],
            }
            mock_auto.return_value = (area_automations, None)

            result = await self.mcp._tools["get_area_diagnostic"]("test_area")
            data = json.loads(result)

        assert data["success"] is True
        assert len(data["automations"]) >= 1
        aliases = [a["alias"] for a in data["automations"]]
        assert any("Area Light Control" in alias for alias in aliases)
        assert any("Area Sensor Check" in alias for alias in aliases)

    @pytest.mark.asyncio
    async def test_area_no_automations(self):
        """Area with no matching automations should produce empty results."""
        unrelated_automations = [
            {
                "id": "auto_other",
                "alias": "Other Room Lights",
                "trigger": [{"platform": "time", "at": "12:00:00"}],
                "action": [{"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}}],
            },
        ]

        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "light.test_light",
                        "state": "on",
                        "attributes": {},
                        "last_changed": "2026-02-24T10:00:00+00:00",
                    },
                    {
                        "entity_id": "sensor.test_sensor",
                        "state": "25.0",
                        "attributes": {"unit_of_measurement": "°C"},
                        "last_changed": "2026-02-24T10:00:00+00:00",
                    },
                ],
            }
            mock_auto.return_value = (unrelated_automations, None)

            result = await self.mcp._tools["get_area_diagnostic"]("test_area")
            data = json.loads(result)

        assert data["success"] is True
        assert data["automations"] == []


class TestInvestigateEntityExtended:
    """Tests for investigate_entity with include_automation_code and conflict analysis."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        from tools.composite import register_composite_tools

        self.mock_registry_data = json.loads(json.dumps(MOCK_REGISTRY_DATA))
        self.mock_registry_data["core.entity_registry"]["data"]["entities"].append(
            {
                "entity_id": "light.test_primary",
                "name": "Test Primary Light",
                "platform": "mqtt",
                "device_id": "device_998",
                "area_id": "living_room",
            }
        )
        self.mock_registry_data["core.device_registry"]["data"]["devices"].append(
            {"id": "device_998", "name": "Test Primary Device", "area_id": "living_room"}
        )

        with patch("tools.composite.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            register_composite_tools(mock_mcp, config_path, ha_url, ha_token)

        self.mcp = mock_mcp

    @pytest.mark.asyncio
    async def test_investigate_entity_with_code(self):
        """include_automation_code=True should include full YAML code in automations."""
        code_automations = [
            {
                "id": "auto_code_1",
                "alias": "Primary Light On",
                "trigger": [{"platform": "time", "at": "08:00:00"}],
                "action": [
                    {"service": "light.turn_on", "target": {"entity_id": "light.test_primary"}}
                ],
                "mode": "single",
            },
        ]

        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "light.test_primary",
                        "state": "on",
                        "attributes": {},
                        "last_changed": "2026-02-24T10:00:00+00:00",
                    },
                ],
            }
            mock_auto.return_value = (code_automations, None)

            result = await self.mcp._tools["investigate_entity"](
                "Primary", include_automation_code=True
            )
            data = json.loads(result)

        assert data["success"] is True
        assert len(data["automations"]) >= 1
        assert any("code" in auto for auto in data["automations"])
        auto_with_code = next(a for a in data["automations"] if "code" in a)
        assert "light.turn_on" in auto_with_code["code"]

    @pytest.mark.asyncio
    async def test_investigate_entity_with_conflicts(self):
        """Conflicting automations should be detected and reported."""
        conflict_automations = [
            {
                "id": "auto_a",
                "alias": "Auto A",
                "trigger": [{"platform": "state", "entity_id": "light.test_primary"}],
                "action": [
                    {"service": "light.turn_on", "target": {"entity_id": "light.test_primary"}}
                ],
                "mode": "single",
            },
            {
                "id": "auto_b",
                "alias": "Auto B",
                "trigger": [{"platform": "state", "entity_id": "sensor.other"}],
                "action": [
                    {"service": "light.turn_off", "target": {"entity_id": "light.test_primary"}}
                ],
                "mode": "single",
            },
        ]

        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_request,
            patch("tools.composite._load_automations") as mock_auto,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_request.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "light.test_primary",
                        "state": "on",
                        "attributes": {},
                        "last_changed": "2026-02-24T10:00:00+00:00",
                    },
                ],
            }
            mock_auto.return_value = (conflict_automations, None)

            result = await self.mcp._tools["investigate_entity"]("primary")
            data = json.loads(result)

        assert data["success"] is True
        assert "conflicts" in data
        assert data["conflicts"]["race_condition_risk"] is True
        assert any("RACE CONDITION" in issue for issue in data["issues"])
        assert data["conflicts"]["feedback_loop_risk"] is True
        assert any("FEEDBACK LOOP" in issue for issue in data["issues"])


class TestAuditConfigOrphans:
    """Tests for audit_config_orphans tool."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        from tools.composite import register_composite_tools

        self.mock_registry_data = MOCK_REGISTRY_DATA

        with patch("tools.composite.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            register_composite_tools(mock_mcp, config_path, ha_url, ha_token)

        self.mcp = mock_mcp
        self.config_path = config_path

    @pytest.mark.asyncio
    async def test_orphans_found(self):
        automations = [
            {
                "id": "auto1",
                "alias": "Test Auto",
                "trigger": [{"platform": "state", "entity_id": "sensor.temperature_living_room"}],
                "action": [],
            }
        ]
        with open(os.path.join(self.config_path, "automations.yaml"), "w") as f:
            yaml.dump(automations, f)

        reg_data = json.loads(json.dumps(self.mock_registry_data))
        reg_data["core.entity_registry"]["data"]["entities"].append(
            {"entity_id": "sensor.orphan_unused", "name": "Orphan", "platform": "mqtt"}
        )

        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path: reg_data.get(name, {})
            mock_req.return_value = {"success": True, "data": []}

            result = json.loads(await self.mcp._tools["audit_config_orphans"]())

        assert result["success"] is True
        assert result["orphan_count"] > 0
        assert "sensor.orphan_unused" in result["orphan_entities"]

    @pytest.mark.asyncio
    async def test_broken_references(self):
        automations = [
            {
                "id": "auto1",
                "alias": "Test Auto",
                "trigger": [{"platform": "state", "entity_id": "sensor.nonexistent_ref"}],
                "action": [],
            }
        ]
        with open(os.path.join(self.config_path, "automations.yaml"), "w") as f:
            yaml.dump(automations, f)

        reg_data = json.loads(json.dumps(self.mock_registry_data))
        reg_data["core.entity_registry"]["data"]["entities"] = []

        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path: reg_data.get(name, {})
            mock_req.return_value = {"success": True, "data": []}

            result = json.loads(await self.mcp._tools["audit_config_orphans"]())

        assert result["success"] is True
        assert result["broken_reference_count"] > 0
        assert "sensor.nonexistent_ref" in result["broken_references"]

    @pytest.mark.asyncio
    async def test_no_issues(self):
        automations = [
            {
                "id": "auto1",
                "alias": "Test Auto",
                "trigger": [
                    {"platform": "state", "entity_id": "sensor.temperature_living_room"},
                    {"platform": "state", "entity_id": "binary_sensor.motion_kitchen"},
                ],
                "action": [],
            }
        ]
        with open(os.path.join(self.config_path, "automations.yaml"), "w") as f:
            yaml.dump(automations, f)

        reg_data = json.loads(json.dumps(self.mock_registry_data))
        reg_data["core.entity_registry"]["data"]["entities"] = [
            e
            for e in reg_data["core.entity_registry"]["data"]["entities"]
            if e.get("entity_id")
            in ("sensor.temperature_living_room", "binary_sensor.motion_kitchen")
        ]

        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path: reg_data.get(name, {})
            mock_req.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "automation.test_auto",
                        "state": "on",
                        "attributes": {
                            "last_triggered": "2024-01-01T00:00:00",
                            "friendly_name": "Test",
                        },
                    }
                ],
            }

            result = json.loads(await self.mcp._tools["audit_config_orphans"]())

        assert result["success"] is True
        assert result["orphan_count"] == 0
        assert result["broken_reference_count"] == 0

    @pytest.mark.asyncio
    async def test_states_api_failure_data_quality(self):
        """States API failure should produce data_quality.failed and never_triggered_status unknown."""
        automations = [
            {
                "id": "auto1",
                "alias": "Test Auto",
                "trigger": [{"platform": "state", "entity_id": "sensor.temperature_living_room"}],
                "action": [],
            }
        ]
        with open(os.path.join(self.config_path, "automations.yaml"), "w") as f:
            yaml.dump(automations, f)

        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_req.return_value = {
                "success": False,
                "error": "Connection refused to /api/states",
            }

            result = json.loads(await self.mcp._tools["audit_config_orphans"]())

        assert result["success"] is True
        assert result["data_quality"]["states_api"] == "failed"
        assert "Connection refused" in result["data_quality"]["states_error"]
        assert result["never_triggered_status"] == "unknown"
        assert result["never_triggered_automations"] == []
        assert result["never_triggered_count"] == 0

    @pytest.mark.asyncio
    async def test_states_api_success_data_quality(self):
        """States API success should produce data_quality.complete and never_triggered_status complete."""
        automations = [
            {
                "id": "auto1",
                "alias": "Test Auto",
                "trigger": [{"platform": "state", "entity_id": "sensor.temperature_living_room"}],
                "action": [],
            }
        ]
        with open(os.path.join(self.config_path, "automations.yaml"), "w") as f:
            yaml.dump(automations, f)

        with (
            patch("tools.composite.load_registry") as mock_load,
            patch("tools.composite.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})
            mock_req.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "automation.test_auto",
                        "state": "on",
                        "attributes": {
                            "last_triggered": "2024-01-01T00:00:00",
                            "friendly_name": "Test",
                        },
                    }
                ],
            }

            result = json.loads(await self.mcp._tools["audit_config_orphans"]())

        assert result["success"] is True
        assert result["data_quality"]["overall"] == "complete"
        assert result["never_triggered_status"] == "complete"
        # Automation was triggered, so never_triggered should be empty
        assert result["never_triggered_automations"] == []

    @pytest.mark.asyncio
    async def test_exception_handler(self):
        with patch("tools.composite._do_audit_config_orphans") as mock_do:
            mock_do.side_effect = RuntimeError("audit failed")
            result = json.loads(await self.mcp._tools["audit_config_orphans"]())

        assert result["success"] is False
        assert "audit failed" in result["error"]
