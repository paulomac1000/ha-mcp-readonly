"""
Unit tests for tools/integrations.py

Tests integration management functions including:
- get_integration_entities: Get entities for a specific integration domain
- get_integration_summary: Get summary of integration health
- Internal helper functions
- Error handling
"""

import json
from unittest.mock import Mock, patch

import pytest

# =========================================================================
# Test data matching actual registry structure
# =========================================================================

MOCK_ENTITIES = [
    {
        "entity_id": "sensor.mqtt_temperature",
        "name": "MQTT Temperature",
        "platform": "mqtt",
        "device_id": "dev1",
        "disabled_by": None,
    },
    {
        "entity_id": "sensor.mqtt_humidity",
        "name": "MQTT Humidity",
        "platform": "mqtt",
        "device_id": "dev1",
        "disabled_by": None,
    },
    {
        "entity_id": "sensor.disabled_sensor",
        "name": "Disabled Sensor",
        "platform": "mqtt",
        "device_id": "dev1",
        "disabled_by": "user",
    },
    {
        "entity_id": "light.hue_lamp",
        "name": "Hue Lamp",
        "platform": "hue",
        "device_id": "dev2",
        "disabled_by": None,
    },
]

MOCK_DEVICES = [
    {
        "id": "dev1",
        "name": "MQTT Device",
        "model": "Sensor",
        "config_entries": ["mqtt_entry_1"],
    },
    {
        "id": "dev2",
        "name": "Hue Device",
        "model": "Lamp",
        "config_entries": ["hue_entry_1"],
    },
]

MOCK_CONFIG_ENTRIES = [
    {
        "entry_id": "mqtt_entry_1",
        "domain": "mqtt",
        "title": "MQTT Broker",
        "state": 1,
        "disabled_by": None,
    },
    {
        "entry_id": "hue_entry_1",
        "domain": "hue",
        "title": "Philips Hue",
        "state": 1,
        "disabled_by": None,
    },
    {
        "entry_id": "zha_entry_1",
        "domain": "zha",
        "title": "Zigbee",
        "state": 2,
        "disabled_by": None,
    },
]

MOCK_STATES = [
    {"entity_id": "sensor.mqtt_temperature", "state": "22.5", "attributes": {}},
    {"entity_id": "sensor.mqtt_humidity", "state": "unavailable", "attributes": {}},
    {"entity_id": "light.hue_lamp", "state": "on", "attributes": {"brightness": 255}},
]

# Patch target prefix — functions are imported into tools.integrations namespace
_P = "tools.integrations"


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
            mcp._tools[args[0].__name__] = args[0]
            return args[0]

        return wrapper

    mcp.tool = tool_decorator
    return mcp


@pytest.fixture
def tools(mock_mcp):
    """Register integration tools and return tool dict."""
    from tools.integrations import register_integration_tools

    register_integration_tools(mock_mcp, "/config", "http://ha:8123", "token")
    return mock_mcp._tools


# =========================================================================
# Internal helpers
# =========================================================================


class TestInternalHelpers:
    """Test internal helper functions."""

    def test_get_entries_by_domain(self):
        """Test filtering config entries by domain."""
        from tools.integrations import _get_entries_by_domain

        assert len(_get_entries_by_domain(MOCK_CONFIG_ENTRIES, "mqtt")) == 1
        assert len(_get_entries_by_domain(MOCK_CONFIG_ENTRIES, "hue")) == 1
        assert len(_get_entries_by_domain(MOCK_CONFIG_ENTRIES, "nonexistent")) == 0

    def test_get_entities_for_domain(self):
        """Test filtering entities by platform/domain."""
        from tools.integrations import _get_entities_for_domain

        mqtt = _get_entities_for_domain(MOCK_ENTITIES, "mqtt")
        assert len(mqtt) == 3
        assert all(e["platform"] == "mqtt" for e in mqtt)

        hue = _get_entities_for_domain(MOCK_ENTITIES, "hue")
        assert len(hue) == 1

        assert len(_get_entities_for_domain(MOCK_ENTITIES, "nonexistent")) == 0


# =========================================================================
# get_integration_entities
# =========================================================================


class TestGetIntegrationEntities:
    """Test get_integration_entities function."""

    @pytest.mark.asyncio
    async def test_entities_found(self, tools):
        """Test getting entities for existing integration."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(
                f"{_P}.make_ha_request",
                return_value={"success": True, "data": MOCK_STATES},
            ),
        ):
            result = await tools["get_integration_entities"]("mqtt")
            data = json.loads(result)

            assert data["success"] is True
            assert data["domain"] == "mqtt"
            assert data["total_entities"] == 3
            # Default include_disabled=False, so disabled entity is skipped from by_device
            assert data["returned_entities"] == 2
            assert data["disabled_count"] == 1

    @pytest.mark.asyncio
    async def test_entities_not_found(self, tools):
        """Test getting entities for non-existent integration."""
        with patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES):
            result = await tools["get_integration_entities"]("nonexistent")
            data = json.loads(result)

            assert data["success"] is False
            assert "No entities found" in data["error"]
            assert data["domain"] == "nonexistent"

    @pytest.mark.asyncio
    async def test_include_disabled(self, tools):
        """Test including disabled entities."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(
                f"{_P}.make_ha_request",
                return_value={"success": True, "data": MOCK_STATES},
            ),
        ):
            result = await tools["get_integration_entities"](
                "mqtt", include_disabled=True
            )
            data = json.loads(result)

            assert data["success"] is True
            assert data["returned_entities"] == 3
            assert data["disabled_count"] == 1

    @pytest.mark.asyncio
    async def test_unavailable_count(self, tools):
        """Test that unavailable entities are counted."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(
                f"{_P}.make_ha_request",
                return_value={"success": True, "data": MOCK_STATES},
            ),
        ):
            result = await tools["get_integration_entities"]("mqtt")
            data = json.loads(result)

            # mqtt_humidity is "unavailable" and not disabled
            assert data["unavailable_count"] == 1

    @pytest.mark.asyncio
    async def test_unknown_state_counted_as_unavailable(self, tools):
        """State 'unknown' (not just 'unavailable') must increment unavailable_count."""
        states_with_unknown = [
            {
                "entity_id": "sensor.mqtt_temperature",
                "state": "unknown",
                "attributes": {},
            },
            {"entity_id": "sensor.mqtt_humidity", "state": "55", "attributes": {}},
        ]
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(
                f"{_P}.make_ha_request",
                return_value={"success": True, "data": states_with_unknown},
            ),
        ):
            result = await tools["get_integration_entities"]("mqtt")
            data = json.loads(result)

        assert data["unavailable_count"] == 1

    @pytest.mark.asyncio
    async def test_orphaned_device_id_goes_to_no_device(self, tools):
        """Entity whose device_id is not in the devices registry → placed in no_device group."""
        entities_orphaned = [
            {
                "entity_id": "sensor.orphan",
                "name": "Orphan",
                "platform": "mqtt",
                "device_id": "device_not_in_registry",
                "disabled_by": None,
            }
        ]
        with (
            patch(f"{_P}.get_registry_entities", return_value=entities_orphaned),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(f"{_P}.make_ha_request", return_value={"success": True, "data": []}),
        ):
            result = await tools["get_integration_entities"]("mqtt")
            data = json.loads(result)

        assert data["success"] is True
        assert "no_device" in data["by_device"]
        assert any(
            e["entity_id"] == "sensor.orphan" for e in data["by_device"]["no_device"]["entities"]
        )

    @pytest.mark.asyncio
    async def test_grouped_by_device(self, tools):
        """Test entities are grouped by device in by_device."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(
                f"{_P}.make_ha_request",
                return_value={"success": True, "data": MOCK_STATES},
            ),
        ):
            result = await tools["get_integration_entities"]("mqtt")
            data = json.loads(result)

            assert "by_device" in data
            assert "dev1" in data["by_device"]
            assert data["by_device"]["dev1"]["device_name"] == "MQTT Device"

    @pytest.mark.asyncio
    async def test_entity_state_included(self, tools):
        """Test that entity states are included in the response."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(
                f"{_P}.make_ha_request",
                return_value={"success": True, "data": MOCK_STATES},
            ),
        ):
            result = await tools["get_integration_entities"]("mqtt")
            data = json.loads(result)

            entities = data["by_device"]["dev1"]["entities"]
            states = {e["entity_id"]: e["state"] for e in entities}
            assert states["sensor.mqtt_temperature"] == "22.5"

    @pytest.mark.asyncio
    async def test_entities_no_device_group_removed_when_empty(self, tools):
        """When all entities have devices, no_device group should be removed."""
        entities_with_devices = [
            {**e, "device_id": "dev1" if e["platform"] == "mqtt" else "dev2"} for e in MOCK_ENTITIES
        ]
        with (
            patch(f"{_P}.get_registry_entities", return_value=entities_with_devices),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(
                f"{_P}.make_ha_request",
                return_value={"success": True, "data": MOCK_STATES},
            ),
        ):
            result = await tools["get_integration_entities"]("mqtt")
            data = json.loads(result)

            assert "no_device" not in data["by_device"]

    @pytest.mark.asyncio
    async def test_no_ha_api(self, tools):
        """Test behaviour when HA API is unavailable (states default to unknown)."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(f"{_P}.make_ha_request", return_value={"success": False}),
        ):
            result = await tools["get_integration_entities"]("mqtt")
            data = json.loads(result)

            assert data["success"] is True
            entities = data["by_device"]["dev1"]["entities"]
            assert all(e["state"] == "unknown" for e in entities)


# =========================================================================
# get_integration_summary
# =========================================================================


class TestGetIntegrationSummary:
    """Test get_integration_summary function."""

    @pytest.mark.asyncio
    async def test_summary_healthy(self, tools):
        """Test summary for healthy integration (all entities available)."""
        healthy_states = [
            {"entity_id": "sensor.mqtt_temperature", "state": "22.5"},
            {"entity_id": "sensor.mqtt_humidity", "state": "55"},
        ]
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(f"{_P}.get_registry_config_entries", return_value=MOCK_CONFIG_ENTRIES),
            patch(
                f"{_P}.make_ha_request",
                return_value={"success": True, "data": healthy_states},
            ),
        ):
            result = await tools["get_integration_summary"]("mqtt")
            data = json.loads(result)

            assert data["success"] is True
            assert data["domain"] == "mqtt"
            assert data["health"] == "Healthy"

    @pytest.mark.asyncio
    async def test_summary_issues_detected(self, tools):
        """Test summary for integration with unavailable entities."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(f"{_P}.get_registry_config_entries", return_value=MOCK_CONFIG_ENTRIES),
            patch(
                f"{_P}.make_ha_request",
                return_value={"success": True, "data": MOCK_STATES},
            ),
        ):
            result = await tools["get_integration_summary"]("mqtt")
            data = json.loads(result)

            assert data["success"] is True
            assert data["health"] == "Issues Detected"
            assert data["entities_summary"]["unavailable"] >= 1

    @pytest.mark.asyncio
    async def test_summary_config_entries(self, tools):
        """Test that summary includes config entry information."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(f"{_P}.get_registry_config_entries", return_value=MOCK_CONFIG_ENTRIES),
            patch(f"{_P}.make_ha_request", return_value={"success": True, "data": []}),
        ):
            result = await tools["get_integration_summary"]("mqtt")
            data = json.loads(result)

            assert data["success"] is True
            ce = data["config_entries"]
            assert ce["total"] == 1
            assert ce["loaded"] == 1
            assert "MQTT Broker" in ce["titles"]

    @pytest.mark.asyncio
    async def test_summary_disabled_config_entry(self, tools):
        """Disabled config entry should increment entries_summary['disabled']."""
        entries_with_disabled = [
            {
                "entry_id": "mqtt_entry_1",
                "domain": "mqtt",
                "title": "MQTT Broker",
                "state": 1,
                "disabled_by": "user",
            },
        ]
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(f"{_P}.get_registry_config_entries", return_value=entries_with_disabled),
            patch(f"{_P}.make_ha_request", return_value={"success": True, "data": []}),
        ):
            result = await tools["get_integration_summary"]("mqtt")
            data = json.loads(result)

        assert data["success"] is True
        assert data["config_entries"]["disabled"] == 1
        assert data["config_entries"]["loaded"] == 0

    @pytest.mark.asyncio
    async def test_summary_not_found(self, tools):
        """Test summary for non-existent integration."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(f"{_P}.get_registry_config_entries", return_value=MOCK_CONFIG_ENTRIES),
            patch(f"{_P}.make_ha_request", return_value={"success": True, "data": []}),
        ):
            result = await tools["get_integration_summary"]("nonexistent")
            data = json.loads(result)

            assert data["success"] is False
            assert "not found" in data["error"]

    @pytest.mark.asyncio
    async def test_summary_devices_count(self, tools):
        """Test that device count is computed correctly."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(f"{_P}.get_registry_config_entries", return_value=MOCK_CONFIG_ENTRIES),
            patch(f"{_P}.make_ha_request", return_value={"success": True, "data": []}),
        ):
            result = await tools["get_integration_summary"]("mqtt")
            data = json.loads(result)

            # dev1 has config_entries=["mqtt_entry_1"]
            assert data["devices_count"] == 1

    @pytest.mark.asyncio
    async def test_summary_entity_platforms(self, tools):
        """Test that entity platforms are counted."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(f"{_P}.get_registry_config_entries", return_value=MOCK_CONFIG_ENTRIES),
            patch(f"{_P}.make_ha_request", return_value={"success": True, "data": []}),
        ):
            result = await tools["get_integration_summary"]("mqtt")
            data = json.loads(result)

            assert "sensor" in data["entity_platforms"]
            assert data["entity_platforms"]["sensor"] == 3

    @pytest.mark.asyncio
    async def test_summary_states_unknown_when_ha_unavailable(self, tools):
        """Unavailable HA API should count enabled entities as unavailable."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(f"{_P}.get_registry_config_entries", return_value=MOCK_CONFIG_ENTRIES),
            patch(
                f"{_P}.make_ha_request",
                return_value={"success": False, "error": "down"},
            ),
        ):
            result = await tools["get_integration_summary"]("mqtt")
            data = json.loads(result)

            enabled = data["entities_summary"]["enabled"]
            unavailable = data["entities_summary"]["unavailable"]
            assert enabled == 2  # one disabled entity
            assert unavailable == enabled


# =========================================================================
# Error handling
# =========================================================================


class TestErrorHandling:
    """Test error handling in integration functions."""

    @pytest.mark.asyncio
    async def test_empty_registry(self, tools):
        """Test handling empty registry data."""
        with patch(f"{_P}.get_registry_entities", return_value=[]):
            result = await tools["get_integration_entities"]("test")
            data = json.loads(result)

            assert data["success"] is False
            assert "No entities found" in data["error"]

    @pytest.mark.asyncio
    async def test_empty_domain_name(self, tools):
        """Test handling empty integration name."""
        with patch(f"{_P}.get_registry_entities", return_value=[]):
            result = await tools["get_integration_entities"]("")
            data = json.loads(result)
            assert data["success"] is False

    @pytest.mark.asyncio
    async def test_ha_api_unavailable_graceful(self, tools):
        """Test that HA API failure doesn't crash - states default to unknown."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(
                f"{_P}.make_ha_request",
                return_value={"success": False, "error": "Connection refused"},
            ),
        ):
            result = await tools["get_integration_entities"]("mqtt")
            data = json.loads(result)

            # Should succeed with partial data
            assert data["success"] is True
            assert data["total_entities"] == 3

    @pytest.mark.asyncio
    async def test_summary_no_config_entries_but_entities_exist(self, tools):
        """Test summary when config entries are absent but entities exist."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(f"{_P}.get_registry_config_entries", return_value=[]),
            patch(f"{_P}.make_ha_request", return_value={"success": True, "data": []}),
        ):
            result = await tools["get_integration_summary"]("mqtt")
            data = json.loads(result)

            # Entities exist even if no config entries - should still return data
            assert data["success"] is True
            assert data["config_entries"]["total"] == 0


# =========================================================================
# Performance
# =========================================================================


class TestPerformance:
    """Test performance optimizations."""

    @pytest.mark.asyncio
    async def test_single_ha_api_call(self, tools):
        """Test that get_integration_entities makes at most one HA API call."""
        with (
            patch(f"{_P}.get_registry_entities", return_value=MOCK_ENTITIES),
            patch(f"{_P}.get_registry_devices", return_value=MOCK_DEVICES),
            patch(
                f"{_P}.make_ha_request",
                return_value={"success": True, "data": MOCK_STATES},
            ) as mock_req,
        ):
            await tools["get_integration_entities"]("mqtt")
            assert mock_req.call_count == 1
