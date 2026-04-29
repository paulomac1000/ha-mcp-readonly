"""
Tests for tools/areas.py
"""

import json
from unittest.mock import patch

import pytest

from tools.areas import register_area_tools


class TestGetAreaDevicesSummary:
    """Tests for get_area_devices_summary()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data, sample_states):
        """Setup test fixtures."""
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.ha_url = ha_url
        self.ha_token = ha_token
        self.mock_registry_data = mock_registry_data
        self.sample_states = sample_states

        # IMPORTANT: We mock load_registry in tools.utils, because it is used by get_registry_*
        with patch("tools.utils.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: self.mock_registry_data.get(
                name, {}
            )

            register_area_tools(mock_mcp, config_path, ha_url, ha_token)

        self.tools = mock_mcp._tools

    @pytest.mark.asyncio
    async def test_get_existing_area(self):
        """Test getting summary for an existing area."""
        # We mock again for the tool call
        with patch("tools.utils.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: self.mock_registry_data.get(
                name, {}
            )

            with patch("tools.areas.make_ha_request") as mock_request:
                mock_request.return_value = {
                    "success": True,
                    "data": self.sample_states,
                }

                # Re-register to capture mocks if needed, though setup did it
                register_area_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)

                result = await self.mock_mcp._tools["get_area_devices_summary"]("salon")

        data = json.loads(result)

        # Debugging output if fails
        if not data["success"]:
            print(f"Error: {data.get('error')}")
            print(f"Available areas: {data.get('available_areas')}")

        assert data["success"] is True
        assert data["area_id"] == "salon"
        assert data["area_name"] == "Salon"
        assert len(data["devices"]) > 0
        assert "integrations_used" in data
        assert "total_entities" in data

    @pytest.mark.asyncio
    async def test_get_area_by_name(self):
        """Test getting area by name (case-insensitive)."""
        with patch("tools.utils.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: self.mock_registry_data.get(
                name, {}
            )

            with patch("tools.areas.make_ha_request") as mock_request:
                mock_request.return_value = {"success": True, "data": []}

                register_area_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)

                result = await self.mock_mcp._tools["get_area_devices_summary"]("Biuro")

        data = json.loads(result)

        assert data["success"] is True
        assert data["area_id"] == "biuro"

    @pytest.mark.asyncio
    async def test_get_nonexistent_area(self):
        """Test getting summary for a non-existent area."""
        with patch("tools.utils.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: self.mock_registry_data.get(
                name, {}
            )

            register_area_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)

            result = await self.mock_mcp._tools["get_area_devices_summary"]("nonexistent")

        data = json.loads(result)

        assert data["success"] is False
        assert "not found" in data["error"]
        assert "available_areas" in data

    @pytest.mark.asyncio
    async def test_orphan_entities(self):
        """Test handling of entities assigned directly to area (no device)."""
        # Create an orphan entity in the mock data
        orphan_entity = {
            "entity_id": "sensor.orphan",
            "area_id": "salon",
            "device_id": None,
            "platform": "template",
        }

        # Add to mock registry
        modified_data = self.mock_registry_data.copy()
        modified_data["core.entity_registry"]["data"]["entities"] = self.mock_registry_data[
            "core.entity_registry"
        ]["data"]["entities"] + [orphan_entity]

        with patch("tools.utils.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: modified_data.get(name, {})

            with patch("tools.areas.make_ha_request") as mock_request:
                mock_request.return_value = {"success": True, "data": []}

                register_area_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)

                result = await self.mock_mcp._tools["get_area_devices_summary"]("salon")

        data = json.loads(result)

        if not data["success"]:
            print(f"Error: {data.get('error')}")

        assert data["success"] is True

        # Check for orphan device entry
        orphan_device = next((d for d in data["devices"] if d["device_id"] is None), None)
        assert orphan_device is not None
        assert orphan_device["name"] == "Orphan Entities (No Device)"
        assert orphan_device["entities_count"] >= 1

    @pytest.mark.asyncio
    async def test_disabled_device_and_integration_detection(self):
        """Disabled device should report issues and integration from config entry."""
        with patch("tools.utils.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: self.mock_registry_data.get(
                name, {}
            )

            with patch("tools.areas.make_ha_request") as mock_request:
                mock_request.return_value = {
                    "success": True,
                    "data": self.sample_states,
                }

                register_area_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
                result = await self.mock_mcp._tools["get_area_devices_summary"]("sypialnia")

        data = json.loads(result)

        assert data["success"] is True
        device = next(d for d in data["devices"] if d["device_id"] == "disabled_device_001")
        assert "gree" in data["integrations_used"]
        assert any("Device disabled" in issue for issue in device["issues"])
        assert data["unavailable_entities"] >= 1

    @pytest.mark.asyncio
    async def test_states_api_unavailable_marks_entities_unknown(self):
        """When states API fails, entities should be treated as unavailable/unknown."""
        with patch("tools.utils.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path, use_cache=True: self.mock_registry_data.get(
                name, {}
            )

            with patch("tools.areas.make_ha_request") as mock_request:
                mock_request.return_value = {"success": False, "error": "down"}

                register_area_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
                result = await self.mock_mcp._tools["get_area_devices_summary"]("salon")

        data = json.loads(result)

        assert data["success"] is True
        assert data["unavailable_entities"] == data["total_entities"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
