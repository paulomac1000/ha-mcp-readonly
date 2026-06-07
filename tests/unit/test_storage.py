"""
Tests for tools/storage.py
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.storage import register_storage_tools


@pytest.fixture
def config_path(tmp_path) -> str:
    return str(tmp_path)


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
def mock_registry_data():
    return {
        "core.entity_registry": {
            "data": {
                "entities": [
                    {
                        "entity_id": "sensor.temp",
                        "name": "Temp",
                        "platform": "mqtt",
                        "device_id": "dev1",
                    },
                    {
                        "entity_id": "light.room",
                        "name": "Room Light",
                        "platform": "hue",
                        "area_id": "living_room",
                    },
                ]
            }
        },
        "core.device_registry": {
            "data": {"devices": [{"id": "dev1", "name": "Sensor Device", "area_id": "kitchen"}]}
        },
        "core.area_registry": {
            "data": {
                "areas": [
                    {"id": "kitchen", "name": "Kitchen"},
                    {"id": "living_room", "name": "Living Room"},
                ]
            }
        },
        "core.config_entries": {
            "data": {"entries": [{"entry_id": "123", "domain": "mqtt", "title": "MQTT"}]}
        },
    }


class TestSearchRegistries:
    @pytest.mark.asyncio
    async def test_search_batch(self, mock_mcp, config_path, mock_registry_data):
        # IMPORTANT: patch load_registry in tools.storage because it is imported there
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )

            register_storage_tools(mock_mcp, config_path)

            tool = mock_mcp._tools["search_registries_batch"]

            # Test search by name
            result = await tool(search_term="Temp")
            data = json.loads(result)

            assert data["success"] is True
            assert len(data["matched_entities"]) == 1
            assert data["matched_entities"][0]["entity_id"] == "sensor.temp"

            # Test search by area
            # sensor.temp -> dev1 -> kitchen
            result = await tool(area_id="kitchen")
            data = json.loads(result)

            assert data["success"] is True
            assert len(data["matched_entities"]) == 1
            assert data["matched_entities"][0]["entity_id"] == "sensor.temp"

    @pytest.mark.asyncio
    async def test_search_batch_with_states(self, mock_mcp, config_path, mock_registry_data):
        """include_states=True should attach live state data to matched entities."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "sensor.temp",
                        "state": "21",
                        "last_changed": "2025-01-01T00:00:00Z",
                        "last_updated": "2025-01-01T00:00:00Z",
                    }
                ],
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(
                await mock_mcp._tools["search_registries_batch"](
                    search_term="Temp", include_states=True
                )
            )

        assert data["success"] is True
        entity = data["matched_entities"][0]
        assert "state" in entity
        assert entity["state"]["state"] == "21"


class TestEntityContext:
    @pytest.mark.asyncio
    async def test_get_context(self, mock_mcp, config_path, mock_registry_data):
        # IMPORTANT: patch load_registry in tools.storage
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )

            with patch("tools.storage.make_ha_request") as mock_req:
                mock_req.return_value = {
                    "success": True,
                    "data": {"state": "20", "attributes": {}},
                }

                register_storage_tools(mock_mcp, config_path, "http://ha", "token")

                tool = mock_mcp._tools["get_entity_context"]
                result = await tool("sensor.temp")
                data = json.loads(result)

                assert data["success"] is True
                assert data["entity_info"]["name"] == "Temp"
                assert data["device_info"]["name"] == "Sensor Device"
                assert data["area_info"]["name"] == "Kitchen"

    @pytest.mark.asyncio
    async def test_get_context_entity_not_found(self, mock_mcp, config_path, mock_registry_data):
        """Entity missing from registry → success: False."""
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_entity_context"]("sensor.does_not_exist"))
        assert data["success"] is False
        assert "not found" in data["error"].lower()


class TestAreaOverview:
    @pytest.mark.asyncio
    async def test_area_found(self, mock_mcp, config_path, mock_registry_data):
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": [{"entity_id": "sensor.temp", "state": "20", "attributes": {}}],
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_area_overview"]("kitchen"))

        assert "area_info" in data
        assert data["area_info"]["name"] == "Kitchen"
        assert "entities_by_domain" in data

    @pytest.mark.asyncio
    async def test_area_not_found(self, mock_mcp, config_path, mock_registry_data):
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_area_overview"]("nonexistent_area"))
        assert data["success"] is False
        assert "not found" in data["error"].lower()


class TestRegistryDumpTools:
    @pytest.mark.asyncio
    async def test_get_entity_registry(self, mock_mcp, config_path, mock_registry_data):
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_entity_registry"]())
        assert "total_entities" in data
        assert data["total_entities"] == 2

    @pytest.mark.asyncio
    async def test_get_device_registry(self, mock_mcp, config_path, mock_registry_data):
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_device_registry"]())
        assert "total_devices" in data
        assert data["total_devices"] == 1

    @pytest.mark.asyncio
    async def test_get_area_registry(self, mock_mcp, config_path, mock_registry_data):
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_area_registry"]())
        assert "total_areas" in data
        assert data["total_areas"] == 2

    @pytest.mark.asyncio
    async def test_get_config_entries(self, mock_mcp, config_path, mock_registry_data):
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_config_entries"]())
        assert "total_entries" in data or "entries" in data


class TestHistoryStats:
    @pytest.mark.asyncio
    async def test_numeric_analysis(self, mock_mcp, config_path):
        history = [
            [
                {"state": "21.5", "last_changed": "2025-01-01T00:00:00Z"},
                {"state": "22.0", "last_changed": "2025-01-01T01:00:00Z"},
                {"state": "20.0", "last_changed": "2025-01-01T02:00:00Z"},
            ]
        ]
        with patch(
            "tools.storage.make_ha_request",
            return_value={"success": True, "data": history},
        ):
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(
                await mock_mcp._tools["get_history_stats"]("sensor.temp", hours_back=24)
            )
        assert data["analysis"]["type"] == "numeric"
        assert data["analysis"]["min"] == 20.0
        assert data["analysis"]["max"] == 22.0

    @pytest.mark.asyncio
    async def test_categorical_analysis(self, mock_mcp, config_path):
        history = [
            [
                {"state": "on", "last_changed": "2025-01-01T00:00:00Z"},
                {"state": "off", "last_changed": "2025-01-01T01:00:00Z"},
                {"state": "on", "last_changed": "2025-01-01T02:00:00Z"},
            ]
        ]
        with patch(
            "tools.storage.make_ha_request",
            return_value={"success": True, "data": history},
        ):
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(
                await mock_mcp._tools["get_history_stats"]("light.room", hours_back=24)
            )
        assert data["analysis"]["type"] == "categorical"
        assert "on" in data["analysis"]["distribution"]

    @pytest.mark.asyncio
    async def test_no_ha_configured(self, mock_mcp, config_path):
        register_storage_tools(mock_mcp, config_path)  # no ha_url / ha_token
        data = json.loads(await mock_mcp._tools["get_history_stats"]("sensor.temp"))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_empty_history_data(self, mock_mcp, config_path):
        """HA returns success but empty data — covers line 608."""
        with patch(
            "tools.storage.make_ha_request",
            return_value={"success": True, "data": []},
        ):
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(
                await mock_mcp._tools["get_history_stats"]("sensor.temp", hours_back=24)
            )
        assert "error" in data
        assert "No history data found" in data["error"]

    @pytest.mark.asyncio
    async def test_no_valid_states(self, mock_mcp, config_path):
        """All states are unavailable/unknown — covers line 614."""
        history = [
            [
                {"state": "unavailable", "last_changed": "2025-01-01T00:00:00Z"},
                {"state": "unknown", "last_changed": "2025-01-01T01:00:00Z"},
            ]
        ]
        with patch(
            "tools.storage.make_ha_request",
            return_value={"success": True, "data": history},
        ):
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(
                await mock_mcp._tools["get_history_stats"]("sensor.temp", hours_back=24)
            )
        assert "info" in data
        assert "No valid states" in data["info"]


class TestSearchRegistriesEdgeCases:
    @pytest.mark.asyncio
    async def test_filter_by_platform(self, mock_mcp, config_path, mock_registry_data):
        """Filter by platform — covers line 101."""
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["search_registries_batch"](platform="mqtt"))
        assert data["success"] is True
        assert data["summary"]["matched_entities"] == 1
        assert data["matched_entities"][0]["entity_id"] == "sensor.temp"

    @pytest.mark.asyncio
    async def test_filter_by_platform_no_match(self, mock_mcp, config_path, mock_registry_data):
        """Platform filter returns no matches — covers line 101."""
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["search_registries_batch"](platform="zwave"))
        assert data["success"] is True
        assert data["summary"]["matched_entities"] == 0

    @pytest.mark.asyncio
    async def test_filter_by_device_id(self, mock_mcp, config_path, mock_registry_data):
        """Filter by device_id — covers line 110."""
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["search_registries_batch"](device_id="dev1"))
        assert data["success"] is True
        assert data["summary"]["matched_entities"] == 1
        assert data["matched_entities"][0]["entity_id"] == "sensor.temp"

    @pytest.mark.asyncio
    async def test_filter_by_entity_ids(self, mock_mcp, config_path, mock_registry_data):
        """Filter by specific entity_ids — covers line 98."""
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(
                await mock_mcp._tools["search_registries_batch"](
                    entity_ids="sensor.temp,light.room"
                )
            )
        assert data["success"] is True
        assert data["summary"]["matched_entities"] == 2

    @pytest.mark.asyncio
    async def test_search_device_by_manufacturer(self, mock_mcp, config_path):
        """Matches device by manufacturer name — covers line 175."""
        registry_data = {
            "core.entity_registry": {
                "data": {
                    "entities": [
                        {
                            "entity_id": "sensor.test",
                            "name": "Test Sensor",
                            "platform": "mqtt",
                            "device_id": "dev1",
                        }
                    ]
                }
            },
            "core.device_registry": {
                "data": {
                    "devices": [
                        {
                            "id": "dev1",
                            "name": "Test Device",
                            "manufacturer": "Acme Corp",
                            "model": "X100",
                        }
                    ]
                }
            },
            "core.area_registry": {"data": {"areas": []}},
        }
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: registry_data.get(name, {})
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["search_registries_batch"](search_term="Acme"))
        assert data["success"] is True
        assert data["summary"]["matched_devices"] == 1
        assert data["matched_devices"][0]["manufacturer"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_search_area_by_name(self, mock_mcp, config_path, mock_registry_data):
        """Matches area by name — covers line 191."""
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(
                await mock_mcp._tools["search_registries_batch"](search_term="Kitchen")
            )
        assert data["success"] is True
        assert data["summary"]["matched_areas"] == 1


class TestEntityContextEdgeCases:
    @pytest.fixture
    def registry_data_with_disabled(self):
        return {
            "core.entity_registry": {
                "data": {
                    "entities": [
                        {
                            "entity_id": "sensor.disabled",
                            "name": "Disabled Sensor",
                            "platform": "mqtt",
                            "device_id": "dev1",
                            "disabled_by": "user",
                            "hidden_by": "user",
                            "config_entry_id": "entry1",
                        },
                        {
                            "entity_id": "sensor.sibling",
                            "name": "Sibling Sensor",
                            "platform": "mqtt",
                            "device_id": "dev1",
                        },
                    ]
                }
            },
            "core.device_registry": {
                "data": {"devices": [{"id": "dev1", "name": "Device One", "area_id": "kitchen"}]}
            },
            "core.area_registry": {"data": {"areas": [{"id": "kitchen", "name": "Kitchen"}]}},
            "core.config_entries": {
                "data": {
                    "entries": [
                        {
                            "entry_id": "entry1",
                            "domain": "mqtt",
                            "title": "MQTT",
                            "source": "user",
                        }
                    ]
                }
            },
        }

    @pytest.mark.asyncio
    async def test_entity_disabled_and_hidden(
        self, mock_mcp, config_path, registry_data_with_disabled
    ):
        """Disabled/hidden entity with config entry — covers lines 402-407, 417-418, 421."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: (
                registry_data_with_disabled.get(name, {})
            )
            mock_req.return_value = {
                "success": True,
                "data": {"state": "20", "attributes": {}},
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_entity_context"]("sensor.disabled"))
        assert data["success"] is True
        assert data["entity_info"]["disabled_by"] == "user"
        assert data["entity_info"]["hidden_by"] == "user"
        assert "integration_info" in data
        assert data["integration_info"]["domain"] == "mqtt"
        issues = [i.lower() for i in data["issues"]]
        assert any("disabled" in i for i in issues)
        assert any("hidden" in i for i in issues)

    @pytest.mark.asyncio
    async def test_entity_unavailable_state(self, mock_mcp, config_path, mock_registry_data):
        """Entity state is unavailable — covers lines 332-333."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": {"state": "unavailable", "attributes": {}},
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_entity_context"]("sensor.temp"))
        assert data["success"] is True
        assert any("unavailable" in i.lower() for i in data["issues"])

    @pytest.mark.asyncio
    async def test_entity_unknown_state(self, mock_mcp, config_path, mock_registry_data):
        """Entity state is unknown — covers lines 337-340."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": {"state": "unknown", "attributes": {}},
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_entity_context"]("sensor.temp"))
        assert data["success"] is True
        assert any("unknown" in i.lower() for i in data["issues"])

    @pytest.mark.asyncio
    async def test_state_fetch_error(self, mock_mcp, config_path, mock_registry_data):
        """HA request fails — covers line 340."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": False,
                "error": "Connection refused",
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_entity_context"]("sensor.temp"))
        assert data["success"] is True
        assert any("could not fetch" in i.lower() for i in data["issues"])

    @pytest.mark.asyncio
    async def test_related_entities_same_device(
        self, mock_mcp, config_path, registry_data_with_disabled
    ):
        """Entity on same device as another — covers line 360."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: (
                registry_data_with_disabled.get(name, {})
            )
            mock_req.return_value = {
                "success": True,
                "data": {"state": "20", "attributes": {}},
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_entity_context"]("sensor.disabled"))
        assert data["success"] is True
        related = data["related_entities"]
        assert len(related) == 1
        assert related[0]["entity_id"] == "sensor.sibling"


class TestAreaOverviewEdgeCases:
    @pytest.mark.asyncio
    async def test_entity_not_in_states_map(self, mock_mcp, config_path, mock_registry_data):
        """Entity in area missing from states response — covers line 529."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "light.room",
                        "state": "on",
                        "attributes": {},
                    }
                ],
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_area_overview"]("kitchen"))
        assert "area_info" in data
        summary_entities = [s for s in data["entities_summary"] if "entity_id" in s]
        all_eids = {s["entity_id"] for s in summary_entities}
        assert "sensor.temp" not in all_eids

    @pytest.mark.asyncio
    async def test_entity_unavailable_in_area(self, mock_mcp, config_path, mock_registry_data):
        """Unavailable entity in area — covers lines 545-546."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "sensor.temp",
                        "state": "unavailable",
                        "attributes": {},
                    }
                ],
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_area_overview"]("kitchen"))
        assert "sensor.temp" in data["unavailable_entities"]
        assert any("unavailable" in i.lower() for i in data["issues"])

    @pytest.mark.asyncio
    async def test_entity_unknown_in_area(self, mock_mcp, config_path, mock_registry_data):
        """Unknown state entity in area — covers line 548."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "sensor.temp",
                        "state": "unknown",
                        "attributes": {},
                    }
                ],
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_area_overview"]("kitchen"))
        assert any("unknown" in i.lower() for i in data["issues"])

    @pytest.mark.asyncio
    async def test_sensor_readings_with_unit(self, mock_mcp, config_path, mock_registry_data):
        """Sensor reading with unit of measurement — covers line 560."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "sensor.temp",
                        "state": "21.5",
                        "attributes": {"unit_of_measurement": "°C"},
                    }
                ],
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_area_overview"]("kitchen"))
        assert "sensor (°C)" in data["sensor_readings"]
        assert "Temp: 21.5" in data["sensor_readings"]["sensor (°C)"]

    @pytest.mark.asyncio
    async def test_sensor_non_numeric_value(self, mock_mcp, config_path, mock_registry_data):
        """Non-numeric sensor value → ValueError — covers lines 565-566."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "sensor.temp",
                        "state": "cloudy",
                        "attributes": {},
                    }
                ],
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_area_overview"]("kitchen"))
        assert "sensor" not in data["sensor_readings"]


class TestRegistryEmpty:
    """Tests for registry dump tools when registries are empty/missing."""

    @pytest.mark.asyncio
    async def test_get_lovelace_dashboards_empty(self, mock_mcp, config_path):
        """Empty lovelace dashboards — covers lines 751-752."""
        with patch("tools.storage.load_registry", return_value={}):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_dashboards"]())
        assert data["total_dashboards"] == 0
        assert data["dashboards"] == []

    @pytest.mark.asyncio
    async def test_get_lovelace_config_not_found(self, mock_mcp, config_path):
        """Dashboard not found — covers lines 766-770."""
        with patch("tools.storage.load_registry", return_value={}):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_config"]("nonexistent"))
        assert "error" in data
        assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_get_exposed_entities_empty(self, mock_mcp, config_path):
        """No exposed entities — covers lines 777-780."""
        with patch("tools.storage.load_registry", return_value={}):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_exposed_entities"]())
        assert data["total_exposed"] == 0

    @pytest.mark.asyncio
    async def test_get_persons_empty(self, mock_mcp, config_path):
        """No persons — covers lines 789-790."""
        with patch("tools.storage.load_registry", return_value={}):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_persons"]())
        assert data["total_persons"] == 0

    @pytest.mark.asyncio
    async def test_get_zones_empty(self, mock_mcp, config_path):
        """No zones — covers lines 799-800."""
        with patch("tools.storage.load_registry", return_value={}):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_zones"]())
        assert data["total_zones"] == 0

    @pytest.mark.asyncio
    async def test_get_input_helpers_empty(self, mock_mcp, config_path):
        """No input helpers — covers lines 807-822."""
        with patch("tools.storage.load_registry", return_value={}):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_input_helpers"]())
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_get_input_helpers_some_present(self, mock_mcp, config_path):
        """Some helper types have data — covers lines 817-822."""

        def registry_mock(name, path, use_cache=True):
            if name == "input_boolean":
                return {"data": {"items": [{"id": "b1", "name": "Test"}]}}
            return {}

        with patch("tools.storage.load_registry", side_effect=registry_mock):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_input_helpers"]())
        assert "input_boolean" in data
        assert data["input_boolean"]["count"] == 1

    @pytest.mark.asyncio
    async def test_get_hacs_data_empty(self, mock_mcp, config_path):
        """HACS not installed — covers lines 830-833."""
        with patch("tools.storage.load_registry", return_value={}):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_hacs_data"]())
        assert "info" in data

    @pytest.mark.asyncio
    async def test_get_timers_empty(self, mock_mcp, config_path):
        """No timers — covers lines 840-841."""
        with patch("tools.storage.load_registry", return_value={}):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_timers"]())
        assert data["total_timers"] == 0

    @pytest.mark.asyncio
    async def test_get_counters_empty(self, mock_mcp, config_path):
        """No counters — covers lines 848-849."""
        with patch("tools.storage.load_registry", return_value={}):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_counters"]())
        assert data["total_counters"] == 0

    @pytest.mark.asyncio
    async def test_get_template_entities(self, mock_mcp, config_path):
        """Template entities extracted from config entries — covers lines 864-903."""
        entries_data = {
            "data": {
                "entries": [
                    {
                        "entry_id": "tmpl1",
                        "domain": "template",
                        "title": "My Sensor",
                        "created_at": "2025-01-01",
                        "modified_at": "2025-01-02",
                        "options": {
                            "state": "{{ states('sensor.x') }}",
                            "template_type": "sensor",
                            "device_class": "temperature",
                            "unit_of_measurement": "°C",
                        },
                    },
                    {
                        "entry_id": "not_tmpl",
                        "domain": "mqtt",
                        "title": "MQTT Device",
                        "options": {},
                    },
                ]
            }
        }
        with patch("tools.storage.load_registry", return_value=entries_data):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_template_entities"]())
        assert data["total_templates"] == 1
        template = data["templates"][0]
        assert template["name"] == "My Sensor"
        assert template["entity_id"] == "sensor.my_sensor"
        assert template["state_template"] == "{{ states('sensor.x') }}"
        assert template["device_class"] == "temperature"

    @pytest.mark.asyncio
    async def test_get_template_entities_no_templates(self, mock_mcp, config_path):
        """Config entries exist but none are templates — covers lines 874-876."""
        entries_data = {
            "data": {
                "entries": [
                    {
                        "entry_id": "not_tmpl",
                        "domain": "mqtt",
                        "title": "MQTT Device",
                        "options": {},
                    },
                ]
            }
        }
        with patch("tools.storage.load_registry", return_value=entries_data):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_template_entities"]())
        assert data["total_templates"] == 0
        assert data["templates"] == []

    @pytest.mark.asyncio
    async def test_get_template_entities_title_fallback(self, mock_mcp, config_path):
        """Template with no title but name in options — covers line 879 fallback."""
        entries_data = {
            "data": {
                "entries": [
                    {
                        "entry_id": "tmpl1",
                        "domain": "template",
                        "title": None,
                        "options": {
                            "name": "Falling Back",
                            "state": "{{ 42 }}",
                            "template_type": "sensor",
                        },
                    },
                ]
            }
        }
        with patch("tools.storage.load_registry", return_value=entries_data):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_template_entities"]())
        assert data["total_templates"] == 1
        assert data["templates"][0]["name"] == "Falling Back"
        assert data["templates"][0]["entity_id"] == "sensor.falling_back"

    @pytest.mark.asyncio
    async def test_get_template_entities_disabled(self, mock_mcp, config_path):
        """Template with disabled_by set — covers line 896."""
        entries_data = {
            "data": {
                "entries": [
                    {
                        "entry_id": "tmpl1",
                        "domain": "template",
                        "title": "Disabled Tpl",
                        "disabled_by": "user",
                        "options": {
                            "state": "{{ 1 }}",
                            "template_type": "binary_sensor",
                        },
                    },
                ]
            }
        }
        with patch("tools.storage.load_registry", return_value=entries_data):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_template_entities"]())
        assert data["total_templates"] == 1
        assert data["templates"][0]["disabled"] is True
        assert data["templates"][0]["entry_id"] == "tmpl1"


class TestCompatibilityWrappers:
    """Tests for wrapper functions that delegate to the batch tools."""

    @pytest.mark.asyncio
    async def test_search_entity_by_name(self, mock_mcp, config_path, mock_registry_data):
        """search_entity_by_name delegates to search_registries_batch — covers line 923."""
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["search_entity_by_name"]("Temp"))
        assert data["success"] is True
        assert data["summary"]["matched_entities"] == 1
        assert data["matched_entities"][0]["entity_id"] == "sensor.temp"

    @pytest.mark.asyncio
    async def test_get_entity_details(self, mock_mcp, config_path, mock_registry_data):
        """get_entity_details delegates to get_entity_context — covers line 936."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": {"state": "20", "attributes": {}},
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_entity_details"]("sensor.temp"))
        assert data["success"] is True
        assert data["entity_id"] == "sensor.temp"
        assert "device_info" in data
        assert "area_info" in data


class TestTemplateEntityTools:
    """Tests for get_template_entities and get_template_entity_code."""

    MOCK_TEMPLATE_ENTRIES = {
        "data": {
            "entries": [
                {
                    "entry_id": "tmpl_entry_001",
                    "domain": "template",
                    "title": "Test Template Sensor",
                    "disabled_by": None,
                    "created_at": "2024-01-15T10:00:00+00:00",
                    "modified_at": "2024-06-01T14:00:00+00:00",
                    "options": {
                        "name": "Test Template Sensor",
                        "template_type": "sensor",
                        "state": "{{ states('sun.sun') }}",
                        "unit_of_measurement": "°C",
                        "device_class": "temperature",
                        "availability": "{{ states('sun.sun') != 'unknown' }}",
                        "attributes": {"custom_attr": "{{ states('sun.sun') }}"},
                    },
                },
                {
                    "entry_id": "tmpl_entry_002",
                    "domain": "template",
                    "title": "Second Template",
                    "disabled_by": None,
                    "created_at": "2024-02-01T10:00:00+00:00",
                    "modified_at": "2024-06-01T14:00:00+00:00",
                    "options": {
                        "template_type": "binary_sensor",
                        "state": "{{ is_state('sun.sun', 'above_horizon') }}",
                    },
                },
                {
                    "entry_id": "not_template_001",
                    "domain": "sun",
                    "title": "Sun",
                    "options": {},
                },
            ]
        }
    }

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = dict(mock_registry_data)
        self.mock_registry_data["core.config_entries"] = self.MOCK_TEMPLATE_ENTRIES

    @pytest.mark.asyncio
    async def test_get_template_entities_returns_all(self):
        """Without filter, should return all template entries."""
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: self.mock_registry_data.get(
                name, {}
            )
            register_storage_tools(self.mock_mcp, self.config_path, "http://test", "token")
            result = await self.mock_mcp._tools["get_template_entities"]()
            data = json.loads(result)
            assert data["success"] is True
            assert data["total_templates"] == 2
            assert len(data["templates"]) == 2

    @pytest.mark.asyncio
    async def test_get_template_entities_filter_by_entity_id(self):
        """With entity_id filter, should return only matching template."""
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: self.mock_registry_data.get(
                name, {}
            )
            register_storage_tools(self.mock_mcp, self.config_path, "http://test", "token")
            result = await self.mock_mcp._tools["get_template_entities"](
                entity_id="sensor.test_template_sensor"
            )
            data = json.loads(result)
            assert data["success"] is True
            assert data["total_templates"] == 1
            assert len(data["templates"]) == 1
            assert data["templates"][0]["entity_id"] == "sensor.test_template_sensor"

    @pytest.mark.asyncio
    async def test_get_template_entity_code_found(self):
        """Should return full code for existing template entity."""
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: self.mock_registry_data.get(
                name, {}
            )
            register_storage_tools(self.mock_mcp, self.config_path, "http://test", "token")
            result = await self.mock_mcp._tools["get_template_entity_code"](
                "sensor.test_template_sensor"
            )
            data = json.loads(result)
            assert data["success"] is True
            assert data["entity_id"] == "sensor.test_template_sensor"
            assert data["template_type"] == "sensor"
            assert "{{ states('sun.sun') }}" in data["state_template"]
            assert data["unit_of_measurement"] == "°C"
            assert data["entry_id"] == "tmpl_entry_001"
            assert "custom_attr" in data["attribute_templates"]

    @pytest.mark.asyncio
    async def test_get_template_entity_code_not_found(self):
        """Should return error for non-existent template entity."""
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: self.mock_registry_data.get(
                name, {}
            )
            register_storage_tools(self.mock_mcp, self.config_path, "http://test", "token")
            result = await self.mock_mcp._tools["get_template_entity_code"](
                "sensor.nonexistent_template"
            )
            data = json.loads(result)
            assert data["success"] is False
            error = data["error"]
            if isinstance(error, dict):
                assert "not found" in error.get("message", "").lower()
            else:
                assert "not found" in str(error).lower()

    @pytest.mark.asyncio
    async def test_get_template_entity_code_empty_entity_id(self):
        """Empty entity_id should return error."""
        register_storage_tools(self.mock_mcp, self.config_path, "http://test", "token")
        result = await self.mock_mcp._tools["get_template_entity_code"]("")
        data = json.loads(result)
        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_get_template_entity_code_unicode_name(self):
        """Template with diacritic characters in name should match via entity registry."""
        unicode_entries = {
            "data": {
                "entities": [
                    {
                        "entity_id": "sensor.tracker_test_user_extended",
                        "config_entry_id": "tmpl_unicode_001",
                        "platform": "template",
                    }
                ],
                "entries": [
                    {
                        "entry_id": "tmpl_unicode_001",
                        "domain": "template",
                        "title": "Tracker Test Us\u00f1er Extended",
                        "options": {
                            "template_type": "sensor",
                            "state": "{{ states('sun.sun') }}",
                        },
                    },
                ],
            }
        }

        mock_data = dict(self.mock_registry_data)
        mock_data["core.config_entries"] = unicode_entries
        mock_data["core.entity_registry"] = unicode_entries

        with patch("tools.storage.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: mock_data.get(name, {})
            register_storage_tools(self.mock_mcp, self.config_path, "http://test", "token")

            # Should find via entity registry lookup even with diacritic name
            result = await self.mock_mcp._tools["get_template_entity_code"](
                "sensor.tracker_test_user_extended"
            )
            data = json.loads(result)
            assert data["success"] is True
            assert data["entity_id"] == "sensor.tracker_test_user_extended"


class TestHacsGetUpdateCount:
    @pytest.mark.asyncio
    async def test_hacs_not_installed(self, mock_mcp, config_path):
        with patch("tools.storage.load_registry") as mock_reg:
            mock_reg.return_value = {}
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["hacs_get_update_count"]())
        assert data["success"] is False
        assert "HACS not installed" in data["error"]

    @pytest.mark.asyncio
    async def test_with_updates(self, mock_mcp, config_path):
        hacs_data = {
            "data": {
                "repositories": [
                    {
                        "installed": True,
                        "full_name": "user/repo-up-to-date",
                        "installed_version": "v1.0.0",
                        "available_version": "v1.0.0",
                        "available_updates": 0,
                        "critical": False,
                    },
                    {
                        "installed": True,
                        "full_name": "user/repo-minor",
                        "installed_version": "v1.0.0",
                        "available_version": "v1.1.0",
                        "available_updates": 1,
                        "critical": False,
                    },
                    {
                        "installed": True,
                        "full_name": "user/repo-major",
                        "installed_version": "v1.0.0",
                        "available_version": "v2.0.0",
                        "available_updates": 1,
                        "critical": False,
                    },
                ]
            }
        }
        with patch("tools.storage.load_registry") as mock_reg:
            mock_reg.return_value = hacs_data
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["hacs_get_update_count"]())
        assert data["success"] is True
        assert data["updates_available"] == 2
        assert len(data["major_version_bumps"]) == 1
        assert data["major_version_bumps"][0]["name"] == "user/repo-major"

    @pytest.mark.asyncio
    async def test_no_updates(self, mock_mcp, config_path):
        hacs_data = {
            "data": {
                "repositories": [
                    {
                        "installed": True,
                        "full_name": "user/repo-one",
                        "installed_version": "v2.0.0",
                        "available_version": "v2.0.0",
                        "available_updates": 0,
                        "critical": False,
                    },
                    {
                        "installed": True,
                        "full_name": "user/repo-two",
                        "installed_version": "v1.5.0",
                        "available_version": "v1.5.0",
                        "available_updates": 0,
                        "critical": False,
                    },
                ]
            }
        }
        with patch("tools.storage.load_registry") as mock_reg:
            mock_reg.return_value = hacs_data
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["hacs_get_update_count"]())
        assert data["success"] is True
        assert data["updates_available"] == 0

    @pytest.mark.asyncio
    async def test_exception_handler(self, mock_mcp, config_path):
        register_storage_tools(mock_mcp, config_path)
        with patch(
            "tools.storage._do_hacs_get_update_count",
            side_effect=RuntimeError("hacs fail"),
        ):
            data = json.loads(await mock_mcp._tools["hacs_get_update_count"]())
        assert data["success"] is False
        assert "hacs fail" in data["error"]


class TestGetNfcTags:
    TAG_DATA = {
        "data": {
            "tags": [
                {"id": "tag_abc123", "name": "Front Door", "last_scanned": "2024-01-01T00:00:00"},
            ]
        }
    }

    @pytest.mark.asyncio
    async def test_from_registry(self, mock_mcp, config_path):
        with (
            patch("tools.storage.Path", Path, create=True),
            patch("tools.storage.load_registry") as mock_reg,
        ):
            mock_reg.return_value = self.TAG_DATA
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_nfc_tags"]())
        assert data["success"] is True
        assert data["total"] == 1
        assert data["tags"][0]["tag_id"] == "tag_abc123"
        assert data["tags"][0]["name"] == "Front Door"

    @pytest.mark.asyncio
    async def test_from_api_fallback(self, mock_mcp, config_path):
        with (
            patch("tools.storage.Path", Path, create=True),
            patch("tools.storage.load_registry") as mock_reg,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_reg.return_value = {}
            mock_req.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "tag.tag_def456",
                        "attributes": {
                            "friendly_name": "Back Door",
                            "last_scanned": "2024-02-01T00:00:00",
                        },
                    }
                ],
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_nfc_tags"]())
        assert data["success"] is True
        assert data["total"] == 1
        assert data["tags"][0]["tag_id"] == "tag.tag_def456"

    @pytest.mark.asyncio
    async def test_no_tags(self, mock_mcp, config_path):
        with patch("tools.storage.load_registry") as mock_reg:
            mock_reg.return_value = {}
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_nfc_tags"]())
        assert data["success"] is True
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_exception_handler(self, mock_mcp, config_path):
        register_storage_tools(mock_mcp, config_path)
        with patch(
            "tools.storage._do_get_nfc_tags",
            side_effect=RuntimeError("nfc fail"),
        ):
            data = json.loads(await mock_mcp._tools["get_nfc_tags"]())
        assert data["success"] is False
        assert "nfc fail" in data["error"]


class TestBatchExceptionHandlers:
    """Exception handler tests for batch wrapper tools (TEST-REG-3)."""

    @pytest.mark.asyncio
    async def test_get_template_entities_batch_exception(self, mock_mcp, config_path):
        register_storage_tools(mock_mcp, config_path)
        with patch(
            "tools.storage._do_get_template_entities_batch",
            side_effect=RuntimeError("boom"),
        ):
            data = json.loads(
                await mock_mcp._tools["get_template_entities_batch"](entity_ids="sensor.test")
            )
        assert data["success"] is False
        assert "boom" in data["error"]

    @pytest.mark.asyncio
    async def test_get_entity_registry_batch_exception(self, mock_mcp, config_path):
        register_storage_tools(mock_mcp, config_path)
        with patch(
            "tools.storage._do_get_entity_registry_batch",
            side_effect=RuntimeError("boom"),
        ):
            data = json.loads(
                await mock_mcp._tools["get_entity_registry_batch"](entity_ids="sensor.test")
            )
        assert data["success"] is False
        assert "boom" in data["error"]


class TestGetEntityDetailsBatch:
    """Batch support for get_entity_details via comma-separated string."""

    @pytest.mark.asyncio
    async def test_batch_of_two(self, mock_mcp, config_path, mock_registry_data):
        """Batch of 2 entities returns results dict with both."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": {"state": "20", "attributes": {}},
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_entity_details"]("sensor.temp,light.room"))
        assert data["success"] is True
        assert "results" in data
        assert "sensor.temp" in data["results"]
        assert "light.room" in data["results"]
        assert data["not_found"] == []

    @pytest.mark.asyncio
    async def test_batch_one_missing(self, mock_mcp, config_path, mock_registry_data):
        """Batch with one missing entity adds it to not_found."""
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": {"state": "20", "attributes": {}},
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(
                await mock_mcp._tools["get_entity_details"]("sensor.temp,sensor.missing")
            )
        assert data["success"] is True
        assert "results" in data
        assert "sensor.temp" in data["results"]
        assert "sensor.missing" in data["not_found"]

    @pytest.mark.asyncio
    async def test_empty_string(self, mock_mcp, config_path):
        """Empty entity_id string returns error."""
        register_storage_tools(mock_mcp, config_path)
        data = json.loads(await mock_mcp._tools["get_entity_details"](""))
        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_too_many_entities(self, mock_mcp, config_path):
        """101+ entities returns error with max limit message."""
        many_ids = ",".join([f"sensor.test_{i}" for i in range(101)])
        register_storage_tools(mock_mcp, config_path)
        data = json.loads(await mock_mcp._tools["get_entity_details"](many_ids))
        assert data["success"] is False
        assert "100" in data.get("error", "") or "limit" in data.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_exception_handler(self, mock_mcp, config_path):
        """Internal exception returns success: False with error message."""
        register_storage_tools(mock_mcp, config_path)
        with patch(
            "tools.storage._do_get_entity_details",
            side_effect=RuntimeError("entity details fail"),
        ):
            data = json.loads(await mock_mcp._tools["get_entity_details"]("sensor.temp"))
        assert data["success"] is False
        assert "entity details fail" in data["error"]


class TestGetEntityDetailsCompact:
    @pytest.mark.asyncio
    async def test_compact_mode_single(self, mock_mcp, config_path, mock_registry_data):
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": {"state": "20", "attributes": {}},
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(
                await mock_mcp._tools["get_entity_details"]("sensor.temp", compact=True)
            )
        assert data["success"] is True
        assert data["entity_id"] == "sensor.temp"
        assert data["name"] == "Temp"
        assert data["platform"] == "mqtt"
        assert data["device_id"] == "dev1"
        assert "device_info" not in data
        assert "area_info" not in data
        assert "current_state" not in data
        assert "entity_info" not in data
        assert "related_entities" not in data

    @pytest.mark.asyncio
    async def test_compact_mode_batch(self, mock_mcp, config_path, mock_registry_data):
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": {"state": "20", "attributes": {}},
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(
                await mock_mcp._tools["get_entity_details"]("sensor.temp,light.room", compact=True)
            )
        assert data["success"] is True
        assert "results" in data
        temp = data["results"]["sensor.temp"]
        assert temp["entity_id"] == "sensor.temp"
        assert temp["name"] == "Temp"
        assert temp["platform"] == "mqtt"
        assert "device_info" not in temp
        assert "area_info" not in temp
        room = data["results"]["light.room"]
        assert room["entity_id"] == "light.room"
        assert room["name"] == "Room Light"
        assert "device_info" not in room

    @pytest.mark.asyncio
    async def test_compact_null_values_no_crash(self, mock_mcp, config_path):
        registry_data_minimal = {
            "core.entity_registry": {
                "data": {
                    "entities": [
                        {
                            "entity_id": "sensor.bare",
                            "name": None,
                            "platform": None,
                            "device_id": None,
                        }
                    ]
                }
            },
            "core.device_registry": {"data": {"devices": []}},
            "core.area_registry": {"data": {"areas": []}},
            "core.config_entries": {"data": {"entries": []}},
        }
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: registry_data_minimal.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": {"state": "unknown", "attributes": {}},
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(
                await mock_mcp._tools["get_entity_details"]("sensor.bare", compact=True)
            )
        assert data["success"] is True
        assert data["entity_id"] == "sensor.bare"
        assert "name" in data
        assert "platform" in data

    @pytest.mark.asyncio
    async def test_compact_false_backward_compat(self, mock_mcp, config_path, mock_registry_data):
        with (
            patch("tools.storage.load_registry") as mock_load,
            patch("tools.storage.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path, use_cache=True: mock_registry_data.get(
                name, {}
            )
            mock_req.return_value = {
                "success": True,
                "data": {"state": "20", "attributes": {}},
            }
            register_storage_tools(mock_mcp, config_path, "http://ha", "token")
            data = json.loads(await mock_mcp._tools["get_entity_details"]("sensor.temp"))
        assert data["success"] is True
        assert data["entity_id"] == "sensor.temp"
        assert "entity_info" in data
        assert "device_info" in data
        assert "area_info" in data
        assert "current_state" in data
        assert data["entity_info"]["name"] == "Temp"

    @pytest.mark.asyncio
    async def test_exception_handler(self, mock_mcp, config_path):
        register_storage_tools(mock_mcp, config_path)
        with patch(
            "tools.storage._do_get_entity_details",
            side_effect=RuntimeError("compact fail"),
        ):
            data = json.loads(
                await mock_mcp._tools["get_entity_details"]("sensor.temp", compact=True)
            )
        assert data["success"] is False
        assert "compact fail" in data["error"]
