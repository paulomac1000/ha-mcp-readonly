"""
Tests for tools/storage.py
"""

import json
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
                        "area_id": "salon",
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
                    {"id": "salon", "name": "Salon"},
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

            if not data["success"]:
                print(f"Error: {data.get('error')}")

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

                if not data["success"]:
                    print(f"Error: {data.get('error')}")

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
        assert "available_areas" in data


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
