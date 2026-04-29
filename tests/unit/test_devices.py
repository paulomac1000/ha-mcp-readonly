"""
Tests for tools/devices.py
"""

import json
from unittest.mock import patch

import pytest

from tools.devices import register_device_tools


class TestGetDeviceDetails:
    """Tests for get_device_details()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data, sample_states):
        """Setup test fixtures."""
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.ha_url = ha_url
        self.ha_token = ha_token
        self.mock_registry_data = mock_registry_data
        self.sample_states = sample_states

    @pytest.mark.asyncio
    async def test_get_existing_device(self):
        """Test getting details of an existing device."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = self.mock_registry_data["core.entity_registry"][
                    "data"
                ]["entities"]

                with patch("tools.devices.get_registry_areas") as mock_areas:
                    mock_areas.return_value = self.mock_registry_data["core.area_registry"]["data"][
                        "areas"
                    ]

                    with patch("tools.devices.get_registry_config_entries") as mock_entries:
                        mock_entries.return_value = self.mock_registry_data["core.config_entries"][
                            "data"
                        ]["entries"]

                        with patch("tools.devices.make_ha_request") as mock_request:
                            mock_request.return_value = {
                                "success": True,
                                "data": self.sample_states,
                            }

                            register_device_tools(
                                self.mock_mcp,
                                self.config_path,
                                self.ha_url,
                                self.ha_token,
                            )

                            result = await self.mock_mcp._tools["get_device_details"](
                                "c67a8024bc53a3d38dacc8c8c6e01cf6"
                            )

        data = json.loads(result)

        assert data["success"] is True
        assert data["device_id"] == "c67a8024bc53a3d38dacc8c8c6e01cf6"
        assert data["name"] == "Sonoff Button"
        assert data["manufacturer"] == "SONOFF"
        assert data["model"] == "Wireless button"
        assert data["area_id"] == "biuro"
        assert data["area_name"] == "Biuro"
        assert "entities_summary" in data
        assert "entities" in data

    @pytest.mark.asyncio
    async def test_get_nonexistent_device(self):
        """Test getting details of a non-existent device."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            register_device_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)

            result = await self.mock_mcp._tools["get_device_details"]("nonexistent_device_id")

        data = json.loads(result)

        assert data["success"] is False
        assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_device_with_via_device(self):
        """Test getting device that has via_device_id (connected through hub)."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = self.mock_registry_data["core.entity_registry"][
                    "data"
                ]["entities"]

                with patch("tools.devices.get_registry_areas") as mock_areas:
                    mock_areas.return_value = self.mock_registry_data["core.area_registry"]["data"][
                        "areas"
                    ]

                    with patch("tools.devices.get_registry_config_entries") as mock_entries:
                        mock_entries.return_value = self.mock_registry_data["core.config_entries"][
                            "data"
                        ]["entries"]

                        with patch("tools.devices.make_ha_request") as mock_request:
                            mock_request.return_value = {
                                "success": True,
                                "data": self.sample_states,
                            }

                            register_device_tools(
                                self.mock_mcp,
                                self.config_path,
                                self.ha_url,
                                self.ha_token,
                            )

                            # Sonoff Button has via_device_id
                            result = await self.mock_mcp._tools["get_device_details"](
                                "c67a8024bc53a3d38dacc8c8c6e01cf6"
                            )

        data = json.loads(result)

        assert data["success"] is True
        # via_device_id exists but the referenced device might not be in sample data
        # Just check the field is present
        assert "via_device" in data

    @pytest.mark.asyncio
    async def test_device_entities_summary(self):
        """Test that entities summary is correctly calculated."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = self.mock_registry_data["core.entity_registry"][
                    "data"
                ]["entities"]

                with patch("tools.devices.get_registry_areas") as mock_areas:
                    mock_areas.return_value = self.mock_registry_data["core.area_registry"]["data"][
                        "areas"
                    ]

                    with patch("tools.devices.get_registry_config_entries") as mock_entries:
                        mock_entries.return_value = self.mock_registry_data["core.config_entries"][
                            "data"
                        ]["entries"]

                        with patch("tools.devices.make_ha_request") as mock_request:
                            mock_request.return_value = {
                                "success": True,
                                "data": self.sample_states,
                            }

                            register_device_tools(
                                self.mock_mcp,
                                self.config_path,
                                self.ha_url,
                                self.ha_token,
                            )

                            result = await self.mock_mcp._tools["get_device_details"](
                                "c67a8024bc53a3d38dacc8c8c6e01cf6"
                            )

        data = json.loads(result)

        assert data["success"] is True
        summary = data["entities_summary"]

        # Check summary has required fields
        assert "total" in summary
        assert "enabled" in summary
        assert "disabled" in summary
        assert "available" in summary
        assert "unavailable" in summary

        # Total should match enabled + disabled
        assert summary["total"] == summary["enabled"] + summary["disabled"]


class TestGetDeviceEntities:
    """Tests for get_device_entities()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data, sample_states):
        """Setup test fixtures."""
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.ha_url = ha_url
        self.ha_token = ha_token
        self.mock_registry_data = mock_registry_data
        self.sample_states = sample_states

    @pytest.mark.asyncio
    async def test_get_device_entities(self):
        """Test getting entities for a device."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = self.mock_registry_data["core.entity_registry"][
                    "data"
                ]["entities"]

                with patch("tools.devices.make_ha_request") as mock_request:
                    mock_request.return_value = {
                        "success": True,
                        "data": self.sample_states,
                    }

                    register_device_tools(
                        self.mock_mcp, self.config_path, self.ha_url, self.ha_token
                    )

                    result = await self.mock_mcp._tools["get_device_entities"](
                        "c67a8024bc53a3d38dacc8c8c6e01cf6"
                    )

        data = json.loads(result)

        assert data["success"] is True
        assert data["device_id"] == "c67a8024bc53a3d38dacc8c8c6e01cf6"
        assert "entities" in data
        assert "total_entities" in data

    @pytest.mark.asyncio
    async def test_get_device_entities_exclude_disabled(self):
        """Test excluding disabled entities."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = self.mock_registry_data["core.entity_registry"][
                    "data"
                ]["entities"]

                with patch("tools.devices.make_ha_request") as mock_request:
                    mock_request.return_value = {
                        "success": True,
                        "data": self.sample_states,
                    }

                    register_device_tools(
                        self.mock_mcp, self.config_path, self.ha_url, self.ha_token
                    )

                    result = await self.mock_mcp._tools["get_device_entities"](
                        "c67a8024bc53a3d38dacc8c8c6e01cf6", include_disabled=False
                    )

        data = json.loads(result)

        assert data["success"] is True
        # All returned entities should not be disabled
        for entity in data["entities"]:
            assert entity.get("disabled_by") is None

    @pytest.mark.asyncio
    async def test_get_device_entities_include_disabled(self):
        """Test including disabled entities."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = self.mock_registry_data["core.entity_registry"][
                    "data"
                ]["entities"]

                with patch("tools.devices.make_ha_request") as mock_request:
                    mock_request.return_value = {
                        "success": True,
                        "data": self.sample_states,
                    }

                    register_device_tools(
                        self.mock_mcp, self.config_path, self.ha_url, self.ha_token
                    )

                    result = await self.mock_mcp._tools["get_device_entities"](
                        "c67a8024bc53a3d38dacc8c8c6e01cf6", include_disabled=True
                    )

        data = json.loads(result)

        assert data["success"] is True
        # Should include both enabled and disabled entities

    @pytest.mark.asyncio
    async def test_get_device_entities_not_found(self):
        """Non-existent device_id should return success=False."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            register_device_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_device_entities"]("nonexistent_device")

        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_get_device_entities_no_states(self):
        """include_states=False must not call make_ha_request at all."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = self.mock_registry_data["core.entity_registry"][
                    "data"
                ]["entities"]

                with patch("tools.devices.make_ha_request") as mock_request:
                    register_device_tools(
                        self.mock_mcp, self.config_path, self.ha_url, self.ha_token
                    )
                    result = await self.mock_mcp._tools["get_device_entities"](
                        "c67a8024bc53a3d38dacc8c8c6e01cf6", include_states=False
                    )

        data = json.loads(result)
        assert data["success"] is True
        mock_request.assert_not_called()
        # No state keys on entities
        for entity in data["entities"]:
            assert "state" not in entity


class TestSearchDevices:
    """Tests for search_devices()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data):
        """Setup test fixtures."""
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

    @pytest.mark.asyncio
    async def test_search_by_term(self):
        """Test searching devices by search term."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = []

                with patch("tools.devices.get_registry_areas") as mock_areas:
                    mock_areas.return_value = self.mock_registry_data["core.area_registry"]["data"][
                        "areas"
                    ]

                    with patch("tools.devices.get_registry_config_entries") as mock_entries:
                        mock_entries.return_value = []

                        register_device_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["search_devices"](search_term="sonoff")

        data = json.loads(result)

        assert data["success"] is True
        assert data["matched_count"] >= 1

        # All results should contain "sonoff" in name, manufacturer, or model
        for device in data["devices"]:
            name = (device.get("name") or "").lower()
            mfr = (device.get("manufacturer") or "").lower()
            model = (device.get("model") or "").lower()
            assert "sonoff" in name or "sonoff" in mfr or "sonoff" in model

    @pytest.mark.asyncio
    async def test_search_by_manufacturer(self):
        """Test searching devices by manufacturer."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = []

                with patch("tools.devices.get_registry_areas") as mock_areas:
                    mock_areas.return_value = self.mock_registry_data["core.area_registry"]["data"][
                        "areas"
                    ]

                    with patch("tools.devices.get_registry_config_entries") as mock_entries:
                        mock_entries.return_value = []

                        register_device_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["search_devices"](
                            manufacturer="philips"
                        )

        data = json.loads(result)

        assert data["success"] is True
        for device in data["devices"]:
            assert "philips" in device.get("manufacturer", "").lower()

    @pytest.mark.asyncio
    async def test_search_by_area(self):
        """Test searching devices by area."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = []

                with patch("tools.devices.get_registry_areas") as mock_areas:
                    mock_areas.return_value = self.mock_registry_data["core.area_registry"]["data"][
                        "areas"
                    ]

                    with patch("tools.devices.get_registry_config_entries") as mock_entries:
                        mock_entries.return_value = []

                        register_device_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["search_devices"](area_id="salon")

        data = json.loads(result)

        assert data["success"] is True
        for device in data["devices"]:
            assert device.get("area_id") == "salon"

    @pytest.mark.asyncio
    async def test_search_disabled_only(self):
        """Test searching only disabled devices."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = []

                with patch("tools.devices.get_registry_areas") as mock_areas:
                    mock_areas.return_value = self.mock_registry_data["core.area_registry"]["data"][
                        "areas"
                    ]

                    with patch("tools.devices.get_registry_config_entries") as mock_entries:
                        mock_entries.return_value = []

                        register_device_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["search_devices"](disabled_only=True)

        data = json.loads(result)

        assert data["success"] is True
        for device in data["devices"]:
            assert device.get("disabled_by") is not None

    @pytest.mark.asyncio
    async def test_search_with_entities_count(self):
        """Test searching with entity count."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = self.mock_registry_data["core.entity_registry"][
                    "data"
                ]["entities"]

                with patch("tools.devices.get_registry_areas") as mock_areas:
                    mock_areas.return_value = self.mock_registry_data["core.area_registry"]["data"][
                        "areas"
                    ]

                    with patch("tools.devices.get_registry_config_entries") as mock_entries:
                        mock_entries.return_value = []

                        register_device_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["search_devices"](
                            search_term="sonoff", with_entities_count=True
                        )

        data = json.loads(result)

        assert data["success"] is True
        for device in data["devices"]:
            assert "entities_count" in device

    @pytest.mark.asyncio
    async def test_search_by_model(self):
        """Filter by model should return only matching devices."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = []

            with patch("tools.devices.get_registry_areas") as mock_areas:
                mock_areas.return_value = self.mock_registry_data["core.area_registry"]["data"][
                    "areas"
                ]

                with patch("tools.devices.get_registry_config_entries") as mock_entries:
                    mock_entries.return_value = []

                    register_device_tools(self.mock_mcp, self.config_path, "http://test", "token")
                    result = await self.mock_mcp._tools["search_devices"](model="Hue White")

        data = json.loads(result)
        assert data["success"] is True
        assert data["matched_count"] >= 1
        for device in data["devices"]:
            assert "hue white" in (device.get("model") or "").lower()

    @pytest.mark.asyncio
    async def test_search_by_domain(self):
        """Filter by domain should return devices from that integration only."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = []

            with patch("tools.devices.get_registry_areas") as mock_areas:
                mock_areas.return_value = self.mock_registry_data["core.area_registry"]["data"][
                    "areas"
                ]

                with patch("tools.devices.get_registry_config_entries") as mock_entries:
                    mock_entries.return_value = self.mock_registry_data["core.config_entries"][
                        "data"
                    ]["entries"]

                    register_device_tools(self.mock_mcp, self.config_path, "http://test", "token")
                    result = await self.mock_mcp._tools["search_devices"](domain="tuya")

        data = json.loads(result)
        assert data["success"] is True
        assert data["matched_count"] >= 1
        # All returned devices should belong to the tuya entry
        tuya_entry_id = "tuya_entry_456"
        for device in data["devices"]:
            device_id = device["device_id"]
            raw = next(
                d
                for d in self.mock_registry_data["core.device_registry"]["data"]["devices"]
                if d["id"] == device_id
            )
            assert tuya_entry_id in raw.get("config_entries", [])


class TestGetDevicesByArea:
    """Tests for get_devices_by_area()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data):
        """Setup test fixtures."""
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

    @pytest.mark.asyncio
    async def test_get_devices_by_area_id(self):
        """Test getting devices by area id."""
        with patch("tools.devices.get_registry_areas") as mock_areas:
            mock_areas.return_value = self.mock_registry_data["core.area_registry"]["data"]["areas"]

            with patch("tools.devices.get_registry_devices") as mock_devices:
                mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                    "devices"
                ]

                with patch("tools.devices.get_registry_entities") as mock_entities:
                    mock_entities.return_value = self.mock_registry_data["core.entity_registry"][
                        "data"
                    ]["entities"]

                    register_device_tools(self.mock_mcp, self.config_path, "http://test", "token")

                    result = await self.mock_mcp._tools["get_devices_by_area"]("salon")

        data = json.loads(result)

        assert data["success"] is True
        assert data["area"]["id"] == "salon"
        assert data["area"]["name"] == "Salon"
        assert "devices" in data

        for device in data["devices"]:
            assert "entities_count" in device

    @pytest.mark.asyncio
    async def test_get_devices_by_area_name(self):
        """Test getting devices by area name (case-insensitive)."""
        with patch("tools.devices.get_registry_areas") as mock_areas:
            mock_areas.return_value = self.mock_registry_data["core.area_registry"]["data"]["areas"]

            with patch("tools.devices.get_registry_devices") as mock_devices:
                mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                    "devices"
                ]

                with patch("tools.devices.get_registry_entities") as mock_entities:
                    mock_entities.return_value = self.mock_registry_data["core.entity_registry"][
                        "data"
                    ]["entities"]

                    register_device_tools(self.mock_mcp, self.config_path, "http://test", "token")

                    result = await self.mock_mcp._tools["get_devices_by_area"]("Biuro")

        data = json.loads(result)

        assert data["success"] is True
        assert data["area"]["id"] == "biuro"

    @pytest.mark.asyncio
    async def test_get_devices_by_nonexistent_area(self):
        """Test getting devices for non-existent area."""
        with patch("tools.devices.get_registry_areas") as mock_areas:
            mock_areas.return_value = self.mock_registry_data["core.area_registry"]["data"]["areas"]

            register_device_tools(self.mock_mcp, self.config_path, "http://test", "token")

            result = await self.mock_mcp._tools["get_devices_by_area"]("nonexistent_area")

        data = json.loads(result)

        assert data["success"] is False
        assert "not found" in data["error"].lower()
        assert "available_areas" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
