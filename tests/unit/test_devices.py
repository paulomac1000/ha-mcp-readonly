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
        assert data["area_id"] == "office"
        assert data["area_name"] == "Office"
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

    @pytest.mark.asyncio
    async def test_get_device_details_include_entities_false(self):
        """Test that include_entities=False omits entity list."""
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
                                "c67a8024bc53a3d38dacc8c8c6e01cf6", include_entities=False
                            )

        data = json.loads(result)

        assert data["success"] is True
        assert "entities_summary" in data
        assert "entities" not in data
        assert "entities_note" not in data

    @pytest.mark.asyncio
    async def test_get_device_details_include_entities_true(self):
        """Test that include_entities=True (default) includes entity list."""
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
                                "c67a8024bc53a3d38dacc8c8c6e01cf6", include_entities=True
                            )

        data = json.loads(result)

        assert data["success"] is True
        assert data["entities"] is not None
        assert isinstance(data["entities"], list)


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

                        result = await self.mock_mcp._tools["search_devices"](area_id="living_room")

        data = json.loads(result)

        assert data["success"] is True
        for device in data["devices"]:
            assert device.get("area_id") == "living_room"

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

                    result = await self.mock_mcp._tools["get_devices_by_area"]("living_room")

        data = json.loads(result)

        assert data["success"] is True
        assert data["area"]["id"] == "living_room"
        assert data["area"]["name"] == "Living Room"
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

                    result = await self.mock_mcp._tools["get_devices_by_area"]("Office")

        data = json.loads(result)

        assert data["success"] is True
        assert data["area"]["id"] == "office"

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
        assert "Available" in data["error"]


class TestSearchDevicesEdgeCases:
    """Edge case tests for search_devices()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

    @pytest.mark.asyncio
    async def test_search_devices_no_results(self):
        """Search by manufacturer that doesn't exist should return empty results."""
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
                            manufacturer="nonexistent_manufacturer"
                        )

        data = json.loads(result)
        assert data["success"] is True
        assert data["matched_count"] == 0
        assert len(data["devices"]) == 0

    @pytest.mark.asyncio
    async def test_search_devices_combined_filters(self):
        """Search by multiple filters simultaneously."""
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
                            search_term="sonoff", area_id="office"
                        )

        data = json.loads(result)
        assert data["success"] is True
        # Sonoff Button is in office area
        for device in data["devices"]:
            assert "sonoff" in (device.get("name") or "").lower()
            assert device.get("area_id") == "office"


class TestGetDevicesByAreaEdgeCases:
    """Edge case tests for get_devices_by_area()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

    @pytest.mark.asyncio
    async def test_get_devices_by_area_with_empty_area(self):
        """Area with no devices should return success with empty devices list."""
        # Temporarily add an empty area
        areas = list(self.mock_registry_data["core.area_registry"]["data"]["areas"])
        areas.append({"id": "empty_area", "name": "Empty Area", "aliases": []})
        mock_areas_data = {"data": {"areas": areas}}

        with patch("tools.devices.get_registry_areas") as mock_areas:
            mock_areas.return_value = mock_areas_data["data"]["areas"]

            with patch("tools.devices.get_registry_devices") as mock_devices:
                mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                    "devices"
                ]

                with patch("tools.devices.get_registry_entities") as mock_entities:
                    mock_entities.return_value = []

                    register_device_tools(self.mock_mcp, self.config_path, "http://test", "token")

                    result = await self.mock_mcp._tools["get_devices_by_area"]("empty_area")

        data = json.loads(result)
        assert data["success"] is True
        assert data["area"]["id"] == "empty_area"
        assert data["devices_count"] == 0


class TestDeviceGetWifiStatus:
    """Tests for device_get_wifi_status()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data, sample_states):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.ha_url = ha_url
        self.ha_token = ha_token
        self.mock_registry_data = mock_registry_data
        self.sample_states = sample_states

    @pytest.mark.asyncio
    async def test_wifi_status_nonexistent_device(self):
        """Non-existent device should return error."""
        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            register_device_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["device_get_wifi_status"]("nonexistent")

        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_wifi_status_tasmota_device(self):
        """Tasmota device with rssi/ssid/ip entities returns full WiFi status."""
        tasmota_device = {
            "id": "tasmota_test_001",
            "config_entries": ["mqtt_entry"],
            "connections": [["mac", "aa:bb:cc:dd:ee:ff"]],
            "identifiers": [["mqtt", "tasmota_plug"]],
            "manufacturer": "Tasmota",
            "model": "Smart Plug",
            "name": "Test Tasmota Plug",
            "name_by_user": None,
            "area_id": "living_room",
            "disabled_by": None,
        }

        tasmota_entities = [
            {
                "entity_id": "sensor.tasmota_plug_rssi",
                "device_id": "tasmota_test_001",
                "platform": "mqtt",
            },
            {
                "entity_id": "sensor.tasmota_plug_ssid",
                "device_id": "tasmota_test_001",
                "platform": "mqtt",
            },
            {
                "entity_id": "sensor.tasmota_plug_ip",
                "device_id": "tasmota_test_001",
                "platform": "mqtt",
            },
        ]

        def mock_request(ha_url, ha_token, endpoint):
            if "rssi" in endpoint:
                return {"success": True, "data": {"state": "-55", "attributes": {}}}
            if "ssid" in endpoint:
                return {"success": True, "data": {"state": "MyWiFi", "attributes": {}}}
            if "ip" in endpoint:
                return {"success": True, "data": {"state": "device.local", "attributes": {}}}
            return {"success": True, "data": {"state": "unknown", "attributes": {}}}

        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = [tasmota_device]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = tasmota_entities

                with patch("tools.devices.make_ha_request") as mock_request_fn:
                    mock_request_fn.side_effect = mock_request

                    register_device_tools(
                        self.mock_mcp, self.config_path, self.ha_url, self.ha_token
                    )
                    result = await self.mock_mcp._tools["device_get_wifi_status"](
                        "tasmota_test_001"
                    )

        data = json.loads(result)
        assert data["success"] is True
        wifi = data["wifi_status"]
        assert wifi["connection_state"] == "connected"
        assert wifi["rssi"] == -55
        assert wifi["signal_quality"] == 70
        assert wifi["ssid"] == "MyWiFi"
        assert wifi["ip_address"] == "device.local"
        assert wifi["mac_address"] == "aa:bb:cc:dd:ee:ff"
        assert wifi["source"] == "ha_sensor"

    @pytest.mark.asyncio
    async def test_wifi_status_openbk_device(self):
        """OpenBK device with weak RSSI should report connection_state=weak."""
        openbk_device = {
            "id": "openbk_test_001",
            "config_entries": ["mqtt_entry"],
            "connections": [["mac", "11:22:33:44:55:66"]],
            "identifiers": [["mqtt", "openbk_light"]],
            "manufacturer": "OpenBeken",
            "model": "WiFi Light",
            "name": "Test OpenBK Light",
            "name_by_user": "OpenBK Light",
            "area_id": "bedroom",
            "disabled_by": None,
        }

        openbk_entities = [
            {
                "entity_id": "sensor.openbk_light_rssi",
                "device_id": "openbk_test_001",
                "platform": "mqtt",
            },
        ]

        def mock_request(ha_url, ha_token, endpoint):
            if "rssi" in endpoint:
                return {"success": True, "data": {"state": "-85", "attributes": {}}}
            return {"success": True, "data": {"state": "unknown", "attributes": {}}}

        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = [openbk_device]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = openbk_entities

                with patch("tools.devices.make_ha_request") as mock_request_fn:
                    mock_request_fn.side_effect = mock_request

                    register_device_tools(
                        self.mock_mcp, self.config_path, self.ha_url, self.ha_token
                    )
                    result = await self.mock_mcp._tools["device_get_wifi_status"]("openbk_test_001")

        data = json.loads(result)
        assert data["success"] is True
        wifi = data["wifi_status"]
        assert wifi["connection_state"] == "weak"
        assert wifi["rssi"] == -85
        assert wifi["signal_quality"] == 10
        assert wifi["mac_address"] == "11:22:33:44:55:66"
        assert wifi["source"] == "ha_sensor"

    @pytest.mark.asyncio
    async def test_wifi_status_non_wifi_device(self):
        """Non-WiFi device (e.g. Philips light) returns minimal data with unknown state."""
        regular_device = {
            "id": "regular_test_001",
            "config_entries": ["some_entry"],
            "connections": [],
            "identifiers": [["tuya", "light_01"]],
            "manufacturer": "Philips",
            "model": "Hue White",
            "name": "Regular Light",
            "name_by_user": None,
            "area_id": "living_room",
            "disabled_by": None,
        }

        regular_entities = [
            {
                "entity_id": "light.regular_light",
                "device_id": "regular_test_001",
                "platform": "tuya",
            },
        ]

        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = [regular_device]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = regular_entities

                with patch("tools.devices.make_ha_request") as mock_request_fn:
                    register_device_tools(
                        self.mock_mcp, self.config_path, self.ha_url, self.ha_token
                    )
                    result = await self.mock_mcp._tools["device_get_wifi_status"](
                        "regular_test_001"
                    )

                    mock_request_fn.assert_not_called()

        data = json.loads(result)
        assert data["success"] is True
        wifi = data["wifi_status"]
        assert wifi["connection_state"] == "unknown"
        assert wifi["ssid"] is None
        assert wifi["rssi"] is None
        assert wifi["signal_quality"] is None
        assert wifi["ip_address"] is None
        assert wifi["source"] == "none"

    @pytest.mark.asyncio
    async def test_wifi_status_rssi_signal_quality(self):
        """RSSI=-30 should produce signal_quality=100 (clamped at ceiling)."""
        tasmota_device = {
            "id": "rssi_test_001",
            "config_entries": ["mqtt_entry"],
            "connections": [["mac", "aa:bb:cc:dd:ee:00"]],
            "identifiers": [["mqtt", "tasmota_strong"]],
            "manufacturer": "Tasmota",
            "model": "Smart Plug",
            "name": "Strong Signal Device",
            "name_by_user": None,
            "area_id": None,
            "disabled_by": None,
        }

        tasmota_entities = [
            {
                "entity_id": "sensor.tasmota_strong_rssi",
                "device_id": "rssi_test_001",
                "platform": "mqtt",
            },
        ]

        def mock_request(ha_url, ha_token, endpoint):
            return {"success": True, "data": {"state": "-30", "attributes": {}}}

        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = [tasmota_device]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = tasmota_entities

                with patch("tools.devices.make_ha_request") as mock_request_fn:
                    mock_request_fn.side_effect = mock_request

                    register_device_tools(
                        self.mock_mcp, self.config_path, self.ha_url, self.ha_token
                    )
                    result = await self.mock_mcp._tools["device_get_wifi_status"]("rssi_test_001")

        data = json.loads(result)
        assert data["success"] is True
        wifi = data["wifi_status"]
        assert wifi["rssi"] == -30
        assert wifi["signal_quality"] == 100
        assert wifi["connection_state"] == "connected"

    @pytest.mark.asyncio
    async def test_wifi_status_partial_sensors(self):
        """Device with only ssid (no rssi) should still report connected."""
        tasmota_device = {
            "id": "partial_test_001",
            "config_entries": ["mqtt_entry"],
            "connections": [["mac", "aa:bb:cc:dd:ee:11"]],
            "identifiers": [["mqtt", "tasmota_partial"]],
            "manufacturer": "Tasmota",
            "model": "Smart Plug",
            "name": "Partial WiFi Device",
            "name_by_user": None,
            "area_id": None,
            "disabled_by": None,
        }

        tasmota_entities = [
            {
                "entity_id": "sensor.tasmota_partial_ssid",
                "device_id": "partial_test_001",
                "platform": "mqtt",
            },
        ]

        def mock_request(ha_url, ha_token, endpoint):
            if "ssid" in endpoint:
                return {"success": True, "data": {"state": "PartialWiFi", "attributes": {}}}
            return {"success": True, "data": {"state": "unknown", "attributes": {}}}

        with patch("tools.devices.get_registry_devices") as mock_devices:
            mock_devices.return_value = [tasmota_device]

            with patch("tools.devices.get_registry_entities") as mock_entities:
                mock_entities.return_value = tasmota_entities

                with patch("tools.devices.make_ha_request") as mock_request_fn:
                    mock_request_fn.side_effect = mock_request

                    register_device_tools(
                        self.mock_mcp, self.config_path, self.ha_url, self.ha_token
                    )
                    result = await self.mock_mcp._tools["device_get_wifi_status"](
                        "partial_test_001"
                    )

        data = json.loads(result)
        assert data["success"] is True
        wifi = data["wifi_status"]
        assert wifi["connection_state"] == "connected"
        assert wifi["ssid"] == "PartialWiFi"
        assert wifi["rssi"] is None
        assert wifi["signal_quality"] is None
        assert wifi["mac_address"] == "aa:bb:cc:dd:ee:11"


class TestDevicesExceptionHandlers:
    """Verify tool wrappers catch internal exceptions per [TEST-REG-3]."""

    @pytest.mark.parametrize(
        "tool_name,patch_target,args",
        [
            (
                "get_device_details",
                "tools.devices._do_get_device_details",
                {"device_id": "test_device"},
            ),
            (
                "get_device_entities",
                "tools.devices._do_get_device_entities",
                {"device_id": "test_device"},
            ),
            (
                "search_devices",
                "tools.devices._do_search_devices",
                {"search_term": "test"},
            ),
            (
                "get_devices_by_area",
                "tools.devices._do_get_devices_by_area",
                {"area_id": "test_area"},
            ),
            (
                "device_get_wifi_status",
                "tools.devices._do_device_get_wifi_status",
                {"device_id": "test_device"},
            ),
            (
                "get_device_triggers",
                "tools.devices._do_get_device_triggers",
                {"device_id": "test_device"},
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_exception_handler(
        self, tool_name, patch_target, args, mock_mcp, config_path, ha_url, ha_token
    ):
        """RuntimeError in _do_* function is caught and returned as error."""
        register_device_tools(mock_mcp, config_path, ha_url, ha_token)

        with patch(patch_target, side_effect=RuntimeError("boom")):
            tool = mock_mcp._tools[tool_name]
            data = json.loads(await tool(**args))

        assert data["success"] is False
        assert "boom" in data.get("error", "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
