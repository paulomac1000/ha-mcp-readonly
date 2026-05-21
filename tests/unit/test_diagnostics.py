"""
Tests for tools/diagnostics.py
"""

import json
from datetime import UTC
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.diagnostics import _clear_cache, register_diagnostics_tools


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before each test."""
    _clear_cache()
    yield
    _clear_cache()


@pytest.fixture
def config_path(tmp_path) -> str:
    return str(tmp_path)


@pytest.fixture
def ha_url():
    return "http://test-ha"


@pytest.fixture
def ha_token():
    return "test-token"


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
def sample_states():
    return [
        {"entity_id": "sensor.temp", "state": "20.5", "attributes": {}},
        {"entity_id": "light.living_room", "state": "unavailable", "attributes": {}},
        {"entity_id": "switch.kitchen", "state": "on", "attributes": {}},
        {
            "entity_id": "persistent_notification.config_error",
            "state": "notifying",
            "attributes": {"title": "Config Error"},
        },
        {
            "entity_id": "sensor.energy_total",
            "state": "123.5",
            "attributes": {"device_class": "energy", "unit_of_measurement": "kWh"},
        },
        {
            "entity_id": "sensor.energy_daily",
            "state": "12.5",
            "attributes": {"device_class": "energy", "unit_of_measurement": "kWh"},
        },
        {"entity_id": "sensor.tuya_device", "state": "unavailable", "attributes": {}},
        {"entity_id": "climate.tuya_ac", "state": "unavailable", "attributes": {}},
    ]


@pytest.fixture
def sample_entity_registry():
    return {
        "data": {
            "entities": [
                {
                    "entity_id": "sensor.tuya_device",
                    "platform": "tuya",
                    "device_id": "dev1",
                },
                {
                    "entity_id": "climate.tuya_ac",
                    "platform": "tuya",
                    "device_id": "dev2",
                },
                {
                    "entity_id": "light.living_room",
                    "platform": "hue",
                    "device_id": "dev3",
                },
            ]
        }
    }


@pytest.fixture
def sample_device_registry():
    return {
        "data": {
            "devices": [
                {"id": "dev1", "name": "Tuya Sensor"},
                {"id": "dev2", "name": "Tuya AC"},
                {"id": "dev3", "name": "Living Room Light"},
            ]
        }
    }


class TestDiagnoseSystemHealth:
    def test_health_check_complete(
        self,
        mock_mcp,
        config_path,
        ha_url,
        ha_token,
        sample_states,
        sample_entity_registry,
        sample_device_registry,
    ):
        # Mock logs with patterns
        log_path = Path(config_path) / "home-assistant.log"
        log_content = """2099-01-01 12:00:00.000 ERROR (MainThread) [homeassistatet.components.tuya] Connection timeout for sensor.tuya_device
2099-01-01 12:00:01.000 ERROR (MainThread) [homeassistatet.components.tuya] Connection timeout for climate.tuya_ac
2099-01-01 12:00:02.000 WARNING (MainThread) [homeassistatet.components.hue] Device not responding
2099-01-01 12:00:03.000 ERROR (MainThread) [homeassistatet.helpers.template] Template error in sensor.test
2099-01-01 12:00:04.000 ERROR (MainThread) [custom_components.pstryk] 429 Rate Limit exceeded
"""
        log_path.write_text(log_content * 3)  # Multiple occurrences

        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": sample_states}
            mock_reg.side_effect = lambda name, path: (
                sample_entity_registry
                if "entity" in name
                else sample_device_registry
                if "device" in name
                else {"data": {"areas": []}}
            )

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["diagnose_system_health"]
            result = tool(
                include_log_analysis=True,
                include_unavailable_breakdown=True,
                include_performance=True,
                hours_back=1,
            )
            data = json.loads(result)

        assert data["success"] is True

        # Check summary
        summary = data["summary"]
        assert "health_score" in summary
        assert summary["unavailable_count"] >= 1
        assert summary["errors_last_hours"] >= 1

        # Check unavailable breakdown
        assert "unavailable_by_integration" in data
        breakdown = data["unavailable_by_integration"]
        if "tuya" in breakdown:
            assert breakdown["tuya"]["count"] >= 1

        # Check error patterns
        assert "top_error_patterns" in data

        # Check API errors detection
        assert "api_errors" in data

        # Check recommendations
        assert "recommendations" in data
        assert len(data["recommendations"]) > 0

    def test_health_check_minimal(
        self,
        mock_mcp,
        config_path,
        ha_url,
        ha_token,
        sample_states,
        sample_entity_registry,
        sample_device_registry,
    ):
        """Test with minimal options."""
        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": sample_states}
            mock_reg.return_value = {"data": {"entities": [], "devices": [], "areas": []}}

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["diagnose_system_health"]
            result = tool(
                include_log_analysis=False,
                include_unavailable_breakdown=False,
                include_performance=False,
            )
            data = json.loads(result)

        assert data["success"] is True
        assert "health_score" in data["summary"]


class TestGetUnavailableEntitiesGrouped:
    def test_group_by_integration(
        self,
        mock_mcp,
        config_path,
        ha_url,
        ha_token,
        sample_states,
        sample_entity_registry,
        sample_device_registry,
    ):
        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": sample_states}
            mock_reg.side_effect = lambda name, path: (
                sample_entity_registry
                if "entity" in name
                else sample_device_registry
                if "device" in name
                else {"data": {}}
            )

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_unavailable_entities_grouped"]
            result = tool(group_by="integration", include_device_names=True)
            data = json.loads(result)

        assert data["success"] is True
        assert "total_unavailable" in data
        assert "by_integration" in data

        # Should have tuya in the breakdown
        if data["total_unavailable"] > 0:
            assert "by_domain" in data or "by_integration" in data


class TestIntegrationHealth:
    def test_integration_health_with_issues(
        self, mock_mcp, config_path, ha_url, ha_token, sample_entity_registry
    ):
        domain_states = [
            {"entity_id": "tuya.sensor1", "state": "10"},
            {"entity_id": "tuya.sensor2", "state": "unavailable"},
            {"entity_id": "sensor.tuya_temp", "state": "unavailable"},
        ]

        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("2099-01-01 12:00:00 ERROR [tuya] Connection failed\n" * 15)

        with (
            patch("tools.diagnostics.load_registry") as mock_load,
            patch("tools.diagnostics.make_ha_request") as mock_req,
        ):
            mock_req.return_value = {"success": True, "data": domain_states}
            mock_load.return_value = sample_entity_registry

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_integration_health"]
            result = tool("tuya")
            data = json.loads(result)

        assert data["success"] is True
        assert data["domain"] == "tuya"
        assert data["stats"]["unavailable"] >= 1
        assert data["recent_log_issues_count"] >= 1


class TestEnergyDashboard:
    def test_energy_data(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.diagnostics.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_energy_dashboard_data"]
            result = tool()
            data = json.loads(result)

        assert data["success"] is True
        assert data["sensors_found"]["energy_sensors"] >= 1
        assert "tariff_status" in data
        assert "consumption" in data
        assert "recommendations" in data


class TestDiagnosePersonTracking:
    """Tests for diagnose_person_tracking tool (async)."""

    @pytest.fixture
    def person_states(self):
        return {
            "entity_id": "person.test_person",
            "state": "home",
            "attributes": {
                "latitude": 50.061,
                "longitude": 19.937,
                "gps_accuracy": 10,
                "source": "gps",
                "device_trackers": [
                    "device_tracker.phone_gps",
                    "device_tracker.router_wifi",
                ],
            },
        }

    @pytest.fixture
    def tracker_states(self):
        return [
            {
                "entity_id": "device_tracker.phone_gps",
                "state": "home",
                "attributes": {"latitude": 50.061, "longitude": 19.937, "source_type": "gps"},
                "last_changed": "2025-05-07T10:00:00+00:00",
            },
            {
                "entity_id": "device_tracker.router_wifi",
                "state": "home",
                "attributes": {"source_type": "router"},
                "last_changed": "2025-05-07T10:30:00+00:00",
            },
        ]

    @pytest.fixture
    def zone_states(self):
        return [
            {
                "entity_id": "zone.home",
                "state": "0",
                "attributes": {"latitude": 50.06, "longitude": 19.94, "radius": 100},
            },
            {
                "entity_id": "zone.office",
                "state": "0",
                "attributes": {"latitude": 50.07, "longitude": 19.93, "radius": 50},
            },
        ]

    def _setup_mocks(
        self, ha_url, ha_token, config_path, person_states, tracker_states, zone_states
    ):
        """Setup common mocks for person tracking tests."""

        def mock_load_registry(name, path):
            if "config_entry" in name:
                return {"data": {"entries": []}}
            if "entity" in name:
                return {
                    "data": {
                        "entities": [
                            {"entity_id": "device_tracker.phone_gps", "platform": "mobile_app"},
                            {"entity_id": "device_tracker.router_wifi", "platform": "unifi"},
                            {"entity_id": "person.test_person", "platform": "person"},
                        ]
                    }
                }
            return {"data": {}}

        def mock_make_request(url, token, endpoint, **kwargs):
            if "person" in endpoint:
                return {"success": True, "data": person_states}
            if "device_tracker" in endpoint:
                for ts in tracker_states:
                    if ts["entity_id"] in endpoint:
                        return {"success": True, "data": ts}
                return {"success": False}
            if "/api/states" in endpoint and "zone" in endpoint:
                return {"success": True, "data": zone_states}
            if endpoint == "/api/states":
                return {"success": True, "data": tracker_states + zone_states}
            if "zone" in endpoint:
                return {"success": True, "data": []}
            return {"success": False}

        return mock_load_registry, mock_make_request

    @pytest.mark.asyncio
    async def test_person_tracking_home(
        self, mock_mcp, config_path, ha_url, ha_token, person_states, tracker_states, zone_states
    ):
        mock_load, mock_req = self._setup_mocks(
            ha_url, ha_token, config_path, person_states, tracker_states, zone_states
        )
        with (
            patch("tools.diagnostics.load_registry", side_effect=mock_load),
            patch("tools.diagnostics.make_ha_request", side_effect=mock_req),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_person_tracking"]
            result = json.loads(await tool("person.test_person"))

        assert result["success"] is True
        assert "person" in result
        assert result["person"]["state"] == "home"

    @pytest.mark.asyncio
    async def test_person_tracking_not_found(self, mock_mcp, config_path, ha_url, ha_token):
        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry", return_value={"data": {}}),
        ):
            mock_req.return_value = {"success": False, "error": "not found"}
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_person_tracking"]
            result = json.loads(await tool("person.nonexistent"))

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_person_tracking_default(
        self, mock_mcp, config_path, ha_url, ha_token, person_states, tracker_states, zone_states
    ):
        mock_load, mock_req = self._setup_mocks(
            ha_url, ha_token, config_path, person_states, tracker_states, zone_states
        )
        with (
            patch("tools.diagnostics.load_registry", side_effect=mock_load),
            patch("tools.diagnostics.make_ha_request", side_effect=mock_req),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_person_tracking"]
            result = json.loads(await tool())

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_person_tracking_auto_prefix(
        self, mock_mcp, config_path, ha_url, ha_token, person_states, tracker_states, zone_states
    ):
        """Test auto-adding person. prefix when missing."""
        mock_load, mock_req = self._setup_mocks(
            ha_url, ha_token, config_path, person_states, tracker_states, zone_states
        )
        with (
            patch("tools.diagnostics.load_registry", side_effect=mock_load),
            patch("tools.diagnostics.make_ha_request", side_effect=mock_req),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_person_tracking"]
            result = json.loads(await tool("test_person"))

        assert result["success"] is True


class TestGetNotificationHistory:
    """Tests for get_notification_history tool."""

    def test_notification_history(self, mock_mcp, config_path, ha_url, ha_token):
        notif_states = [
            {
                "entity_id": "persistent_notification.low_battery",
                "state": "notifying",
                "attributes": {
                    "title": "Low Battery",
                    "message": "Sensor battery low",
                    "notification_id": "nb1",
                },
                "last_changed": "2025-05-07T09:00:00+00:00",
            },
            {"entity_id": "sensor.temp", "state": "20", "attributes": {}},
        ]
        with patch("tools.diagnostics.make_ha_request") as mock_req:
            mock_req.side_effect = [
                {"success": True, "data": notif_states},
                {"success": True, "data": []},
            ]
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["get_notification_history"]
            result = json.loads(tool())

        assert result["success"] is True
        assert result["active_count"] >= 1


class TestGetAreaAutomationSummary:
    """Tests for get_area_automation_summary tool."""

    def test_area_automation_summary(self, mock_mcp, config_path, ha_url, ha_token):
        import yaml

        area_reg = {
            "data": {
                "areas": [
                    {"id": "kitchen", "name": "Kitchen", "aliases": []},
                ]
            }
        }
        device_reg = {
            "data": {
                "devices": [
                    {"id": "dev1", "area_id": "kitchen"},
                    {"id": "dev2", "area_id": "kitchen", "disabled_by": "user"},
                ]
            }
        }
        entity_reg = {
            "data": {
                "entities": [
                    {
                        "entity_id": "light.kitchen_main",
                        "device_id": "dev1",
                        "area_id": None,
                        "platform": "hue",
                    },
                    {
                        "entity_id": "sensor.kitchen_temp",
                        "device_id": "dev1",
                        "area_id": None,
                        "platform": "mqtt",
                    },
                    {
                        "entity_id": "switch.kitchen_fan",
                        "device_id": "dev2",
                        "area_id": None,
                        "platform": "mqtt",
                    },
                ]
            }
        }
        area_states = [
            {"entity_id": "light.kitchen_main", "state": "on", "attributes": {"brightness": 200}},
            {
                "entity_id": "sensor.kitchen_temp",
                "state": "23.5",
                "attributes": {"unit_of_measurement": "°C"},
            },
            {"entity_id": "switch.kitchen_fan", "state": "unavailable", "attributes": {}},
        ]

        automations_data = [
            {
                "id": "auto1",
                "alias": "Kitchen Lights",
                "trigger": [{"platform": "state", "entity_id": "binary_sensor.kitchen_motion"}],
            },
            {
                "id": "auto2",
                "alias": "Kitchen Timer",
                "trigger": [{"platform": "time", "at": "18:00"}],
            },
        ]

        # Write automations.yaml to config path
        auto_path = Path(config_path) / "automations.yaml"
        auto_path.parent.mkdir(parents=True, exist_ok=True)
        with open(auto_path, "w", encoding="utf-8") as f:
            yaml.dump(automations_data, f)

        with (
            patch("tools.diagnostics.load_registry") as mock_load,
            patch("tools.diagnostics.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path: (
                area_reg if "area" in name else device_reg if "device" in name else entity_reg
            )
            mock_req.return_value = {"success": True, "data": area_states}

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["get_area_automation_summary"]
            result = json.loads(tool("kitchen"))

        assert result["success"] is True
        assert "area" in result
        assert "intelligence" in result
        assert "entity_breakdown" in result

    def test_area_automation_summary_not_found(self, mock_mcp, config_path, ha_url, ha_token):
        area_reg = {"data": {"areas": [{"id": "bedroom", "name": "Bedroom"}]}}
        with patch("tools.diagnostics.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: area_reg if "area" in name else {"data": {}}
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["get_area_automation_summary"]
            result = json.loads(tool("nonexistent"))

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_area_automation_summary_by_name(self, mock_mcp, config_path, ha_url, ha_token):
        area_reg = {
            "data": {"areas": [{"id": "living_room", "name": "Living Room", "aliases": []}]}
        }
        device_reg = {"data": {"devices": [{"id": "dev_lr", "area_id": "living_room"}]}}
        entity_reg = {
            "data": {
                "entities": [
                    {
                        "entity_id": "light.lr_main",
                        "device_id": "dev_lr",
                        "area_id": None,
                        "platform": "hue",
                    },
                ]
            }
        }

        with (
            patch("tools.diagnostics.load_registry") as mock_load,
            patch("tools.diagnostics.make_ha_request") as mock_req,
        ):
            mock_load.side_effect = lambda name, path: (
                area_reg if "area" in name else device_reg if "device" in name else entity_reg
            )
            mock_req.return_value = {
                "success": True,
                "data": [{"entity_id": "light.lr_main", "state": "off", "attributes": {}}],
            }

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["get_area_automation_summary"]
            result = json.loads(tool("Living Room"))

        assert result["success"] is True
        assert result["area"]["id"] == "living_room"


class TestGetUnavailableGroupedByDomain:
    """Tests for get_unavailable_entities_grouped with group_by='domain'."""

    def test_group_by_domain(
        self,
        mock_mcp,
        config_path,
        ha_url,
        ha_token,
        sample_states,
        sample_entity_registry,
        sample_device_registry,
    ):
        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": sample_states}
            mock_reg.side_effect = lambda name, path: (
                sample_entity_registry
                if "entity" in name
                else sample_device_registry
                if "device" in name
                else {"data": {}}
            )

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_unavailable_entities_grouped"]
            result = tool(group_by="domain", include_device_names=True)
            data = json.loads(result)

        assert data["success"] is True
        assert "total_unavailable" in data
        assert "by_domain" in data
        # domain grouping should show individual domains
        by_domain = data["by_domain"]
        assert isinstance(by_domain, dict)
        # At least one domain with unavailable entities
        if data["total_unavailable"] > 0:
            assert len(by_domain) > 0


class TestIntegrationHealthEdgeCases:
    """Edge case tests for get_integration_health()."""

    def test_integration_health_empty_domain(self, mock_mcp, config_path, ha_url, ha_token):
        """Domain with no matching entities should return error."""
        states_data = [
            {"entity_id": "sensor.temp", "state": "20.5", "attributes": {}},
            {"entity_id": "light.living", "state": "on", "attributes": {}},
        ]

        entity_registry_data = {
            "data": {
                "entities": [
                    {"entity_id": "sensor.temp", "platform": "mqtt"},
                    {"entity_id": "light.living", "platform": "hue"},
                ]
            }
        }

        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_load,
        ):
            mock_req.return_value = {"success": True, "data": states_data}
            mock_load.return_value = entity_registry_data

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_integration_health"]
            result = tool("nonexistent_integration")
            data = json.loads(result)

        assert data["success"] is False
        assert (
            "not found" in data["status"].lower() or "no entities" in data.get("error", "").lower()
        )
        assert data["domain"] == "nonexistent_integration"


class TestDiagnoseSystemHealthApiFailure:
    """Tests for diagnose_system_health with API failures."""

    def test_api_failure_returns_error(
        self,
        mock_mcp,
        config_path,
        ha_url,
        ha_token,
    ):
        with patch("tools.diagnostics.make_ha_request") as mock_req:
            mock_req.return_value = {"success": False, "error": "API connection refused"}

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["diagnose_system_health"]
            result = tool()
            data = json.loads(result)

        assert data["success"] is False
        assert "error" in data
        assert "Cannot fetch" in data["error"]


class TestEnergyDashboardEdgeCases:
    """Edge case tests for get_energy_dashboard_data()."""

    def test_no_energy_sensors(self, mock_mcp, config_path, ha_url, ha_token):
        """No energy sensors should still return valid response with zeros."""
        states_data = [
            {"entity_id": "sensor.temp", "state": "20.5", "attributes": {}},
            {"entity_id": "light.living", "state": "on", "attributes": {}},
            {"entity_id": "sun.sun", "state": "above_horizon", "attributes": {}},
        ]

        with patch("tools.diagnostics.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states_data}

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_energy_dashboard_data"]
            result = tool()
            data = json.loads(result)

        assert data["success"] is True
        assert data["sensors_found"]["energy_sensors"] == 0
        assert data["consumption"]["current_power_w"] == 0.0
        assert data["consumption"]["today_energy_kwh"] == 0.0
        assert "tariff_status" in data
        assert "recommendations" in data


class TestSlowEntityDetection:
    """Tests for slow entity detection in system health."""

    def test_slow_entity_detected(
        self,
        mock_mcp,
        config_path,
        ha_url,
        ha_token,
        sample_entity_registry,
        sample_device_registry,
    ):
        log_path = Path(config_path) / "home-assistant.log"
        log_content = (
            "2099-01-01 14:30:00.000 WARNING (MainThread) "
            "[homeassistant.components.sensor] "
            "Updating sensor.temperature took 3.2 seconds\n"
        )
        log_path.write_text(log_content * 10)

        states = [
            {"entity_id": "sensor.temperature", "state": "22.0", "attributes": {}},
            {"entity_id": "light.living_room", "state": "on", "attributes": {}},
        ]

        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": states}
            mock_reg.side_effect = lambda name, path: (
                sample_entity_registry
                if "entity" in name
                else sample_device_registry
                if "device" in name
                else {"data": {"areas": []}}
            )

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_system_health"]
            result = tool(
                include_log_analysis=True,
                include_performance=True,
            )
            data = json.loads(result)

        assert data["success"] is True
        slow_entities = data.get("slow_entities", [])
        assert len(slow_entities) >= 1
        slow = slow_entities[0]
        assert slow["entity_id"] == "sensor.temperature"
        assert slow["update_time_seconds"] == 3.2
        assert slow["max_time"] == 3.2
        assert slow["occurrences"] >= 1

    def test_no_slow_entities(
        self,
        mock_mcp,
        config_path,
        ha_url,
        ha_token,
        sample_entity_registry,
        sample_device_registry,
    ):
        log_path = Path(config_path) / "home-assistant.log"
        log_content = (
            "2099-01-01 14:30:00.000 WARNING (MainThread) "
            "[homeassistant.components.sensor] "
            "Update of sensor.temperature completed\n"
        )
        log_path.write_text(log_content)

        states = [
            {"entity_id": "sensor.temperature", "state": "22.0", "attributes": {}},
        ]

        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": states}
            mock_reg.side_effect = lambda name, path: (
                sample_entity_registry
                if "entity" in name
                else sample_device_registry
                if "device" in name
                else {"data": {"areas": []}}
            )

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_system_health"]
            result = tool(
                include_log_analysis=True,
                include_performance=True,
            )
            data = json.loads(result)

        assert data["success"] is True
        assert data["slow_entities"] == []


class TestPersonLocationAnalysis:
    """Tests for person location analysis: tracker staleness and nearby zones."""

    @staticmethod
    def _build_load_registry_mock(
        config_entries=None, entity_reg=None, device_reg=None, zone_reg=None
    ):
        def mock_load(name, path):
            if name == "core.config_entries":
                return config_entries or {"data": {"entries": []}}
            if name == "core.entity_registry":
                return entity_reg or {"data": {"entities": []}}
            if name == "core.device_registry":
                return device_reg or {"data": {"devices": []}}
            if name == "zone":
                return zone_reg or {"data": {"items": []}}
            return {"data": {}}

        return mock_load

    @pytest.mark.asyncio
    async def test_tracker_freshness_fresh(self, mock_mcp, config_path, ha_url, ha_token):
        from datetime import datetime, timedelta

        now = datetime.now(UTC)
        fresh_ts = (now - timedelta(minutes=2)).isoformat()

        person_data = {
            "entity_id": "person.test_person",
            "state": "home",
            "attributes": {
                "latitude": 52.0,
                "longitude": 21.0,
                "source": "gps",
                "device_trackers": ["device_tracker.phone_gps"],
            },
        }
        all_states = [
            {
                "entity_id": "device_tracker.phone_gps",
                "state": "home",
                "attributes": {"source_type": "gps"},
                "last_updated": fresh_ts,
            },
        ]
        entity_reg = {
            "data": {
                "entities": [
                    {"entity_id": "device_tracker.phone_gps", "platform": "mobile_app"},
                ]
            }
        }

        mock_load = self._build_load_registry_mock(entity_reg=entity_reg)

        def mock_request(url, token, endpoint, **kwargs):
            if "person" in endpoint and "states" in endpoint:
                return {"success": True, "data": person_data}
            if endpoint == "/api/states":
                return {"success": True, "data": all_states}
            return {"success": False}

        with (
            patch("tools.diagnostics.load_registry", side_effect=mock_load),
            patch("tools.diagnostics.make_ha_request", side_effect=mock_request),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_person_tracking"]
            result = json.loads(await tool("person.test_person"))

        assert result["success"] is True
        tracker = result["trackers"]["details"][0]
        assert tracker["staleness"] == "fresh"

    @pytest.mark.asyncio
    async def test_tracker_freshness_stale_hours(self, mock_mcp, config_path, ha_url, ha_token):
        from datetime import datetime, timedelta

        now = datetime.now(UTC)
        stale_ts = (now - timedelta(hours=2)).isoformat()

        person_data = {
            "entity_id": "person.test_person",
            "state": "home",
            "attributes": {
                "latitude": 52.0,
                "longitude": 21.0,
                "source": "gps",
                "device_trackers": ["device_tracker.phone_gps"],
            },
        }
        all_states = [
            {
                "entity_id": "device_tracker.phone_gps",
                "state": "home",
                "attributes": {"source_type": "gps"},
                "last_updated": stale_ts,
            },
        ]
        entity_reg = {
            "data": {
                "entities": [
                    {"entity_id": "device_tracker.phone_gps", "platform": "mobile_app"},
                ]
            }
        }

        mock_load = self._build_load_registry_mock(entity_reg=entity_reg)

        def mock_request(url, token, endpoint, **kwargs):
            if "person" in endpoint and "states" in endpoint:
                return {"success": True, "data": person_data}
            if endpoint == "/api/states":
                return {"success": True, "data": all_states}
            return {"success": False}

        with (
            patch("tools.diagnostics.load_registry", side_effect=mock_load),
            patch("tools.diagnostics.make_ha_request", side_effect=mock_request),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_person_tracking"]
            result = json.loads(await tool("person.test_person"))

        assert result["success"] is True
        tracker = result["trackers"]["details"][0]
        assert tracker["staleness"] == "stale_hours"
        assert result["trackers"]["stale"] >= 1

        stale_issue = next((i for i in result["issues"] if i.get("type") != "healthy"), None)
        assert stale_issue is not None

    @pytest.mark.asyncio
    async def test_tracker_freshness_aging(self, mock_mcp, config_path, ha_url, ha_token):
        from datetime import datetime, timedelta

        now = datetime.now(UTC)
        aging_ts = (now - timedelta(minutes=10)).isoformat()

        person_data = {
            "entity_id": "person.test_person",
            "state": "home",
            "attributes": {
                "latitude": 52.0,
                "longitude": 21.0,
                "source": "gps",
                "device_trackers": ["device_tracker.phone_gps"],
            },
        }
        all_states = [
            {
                "entity_id": "device_tracker.phone_gps",
                "state": "home",
                "attributes": {"source_type": "gps"},
                "last_updated": aging_ts,
            },
        ]
        entity_reg = {
            "data": {
                "entities": [
                    {"entity_id": "device_tracker.phone_gps", "platform": "mobile_app"},
                ]
            }
        }

        mock_load = self._build_load_registry_mock(entity_reg=entity_reg)

        def mock_request(url, token, endpoint, **kwargs):
            if "person" in endpoint and "states" in endpoint:
                return {"success": True, "data": person_data}
            if endpoint == "/api/states":
                return {"success": True, "data": all_states}
            return {"success": False}

        with (
            patch("tools.diagnostics.load_registry", side_effect=mock_load),
            patch("tools.diagnostics.make_ha_request", side_effect=mock_request),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_person_tracking"]
            result = json.loads(await tool("person.test_person"))

        assert result["success"] is True
        tracker = result["trackers"]["details"][0]
        assert tracker["staleness"] == "aging"

    @pytest.mark.asyncio
    async def test_nearby_zones_calculation(self, mock_mcp, config_path, ha_url, ha_token):
        person_data = {
            "entity_id": "person.test_person",
            "state": "home",
            "attributes": {
                "latitude": 52.0,
                "longitude": 21.0,
                "source": "gps",
                "device_trackers": ["device_tracker.phone_gps"],
            },
        }
        all_states = [
            {
                "entity_id": "device_tracker.phone_gps",
                "state": "home",
                "attributes": {"source_type": "gps"},
                "last_updated": "2099-01-01T12:00:00+00:00",
            },
        ]
        entity_reg = {
            "data": {
                "entities": [
                    {"entity_id": "device_tracker.phone_gps", "platform": "mobile_app"},
                ]
            }
        }

        config_entries = {
            "data": {
                "entries": [
                    {
                        "entry_id": "zone_home_001",
                        "domain": "zone",
                        "title": "Home",
                        "data": {"latitude": 52.0, "longitude": 21.0, "radius": 50},
                    },
                    {
                        "entry_id": "zone_office_001",
                        "domain": "zone",
                        "title": "Office",
                        "data": {"latitude": 52.002, "longitude": 21.001, "radius": 50},
                    },
                ]
            }
        }

        mock_load = self._build_load_registry_mock(
            config_entries=config_entries,
            entity_reg=entity_reg,
        )

        def mock_request(url, token, endpoint, **kwargs):
            if "person" in endpoint and "states" in endpoint:
                return {"success": True, "data": person_data}
            if endpoint == "/api/states":
                return {"success": True, "data": all_states}
            return {"success": False}

        with (
            patch("tools.diagnostics.load_registry", side_effect=mock_load),
            patch("tools.diagnostics.make_ha_request", side_effect=mock_request),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_person_tracking"]
            result = json.loads(await tool("person.test_person"))

        assert result["success"] is True
        nearby = result["zones"]["nearby"]
        assert len(nearby) >= 2

        home_zone = next(z for z in nearby if z["name"] == "Home")
        assert home_zone["distance_m"] < 1.0
        assert home_zone["in_zone"] is True

        office_zone = next(z for z in nearby if z["name"] == "Office")
        assert office_zone["distance_m"] > 50
        assert office_zone["distance_m"] < 500
        assert office_zone["in_zone"] is False


class TestDiagnoseSystemHealthRecommendations:
    """Tests for health score thresholds and integration recommendations."""

    def test_warning_status(self, mock_mcp, config_path, ha_url, ha_token):
        from datetime import datetime, timedelta

        states = [
            {"entity_id": "sensor.unav1", "state": "unavailable", "attributes": {}},
            {"entity_id": "sensor.unav2", "state": "unavailable", "attributes": {}},
            {"entity_id": "sensor.unav3", "state": "unavailable", "attributes": {}},
            {"entity_id": "sensor.unav4", "state": "unavailable", "attributes": {}},
            {"entity_id": "sensor.unav5", "state": "unavailable", "attributes": {}},
            {"entity_id": "light.ok", "state": "on", "attributes": {}},
        ]

        now = datetime.now()
        error_lines = []
        for i in range(12):
            ts = (now - timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M:%S")
            error_lines.append(f"{ts}.000 ERROR (MainThread) [test.component] Error number {i}")
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("\n".join(error_lines))

        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": states}
            mock_reg.return_value = {"data": {"entities": [], "devices": [], "areas": []}}

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_system_health"]
            result = tool(
                include_log_analysis=True,
                include_unavailable_breakdown=False,
                hours_back=1,
            )
            data = json.loads(result)

        assert data["success"] is True
        score = data["summary"]["health_score"]
        assert 50 <= score < 80
        assert data["summary"]["status"] == "Warning"

    def test_integration_recommendation(self, mock_mcp, config_path, ha_url, ha_token):
        states = [
            {"entity_id": f"test.dev{i}", "state": "unavailable", "attributes": {}}
            for i in range(7)
        ] + [
            {"entity_id": "sensor.ok", "state": "20", "attributes": {}},
        ]

        entity_reg = {
            "data": {
                "entities": [
                    {"entity_id": f"test.dev{i}", "platform": "broken_platform"} for i in range(7)
                ]
                + [
                    {"entity_id": "sensor.ok", "platform": "mqtt"},
                ]
            }
        }
        device_reg = {"data": {"devices": []}}

        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": states}
            mock_reg.side_effect = lambda name, path: (
                entity_reg
                if "entity" in name
                else device_reg
                if "device" in name
                else {"data": {"areas": []}}
            )

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_system_health"]
            result = tool(
                include_log_analysis=False,
                include_unavailable_breakdown=True,
                include_performance=False,
            )
            data = json.loads(result)

        assert data["success"] is True
        recs = data["recommendations"]
        integration_rec = next(
            (
                r
                for r in recs
                if "broken_platform" in r.get("message", "")
                and "unavailable" in r.get("message", "")
            ),
            None,
        )
        assert integration_rec is not None
        assert integration_rec["priority"] == "high"


class TestIntegrationHealthBranches:
    """Tests for integration health status branches."""

    def test_integration_critical_all_unavailable(self, mock_mcp, config_path, ha_url, ha_token):
        domain = "testdomain"
        states = [
            {"entity_id": f"{domain}.s1", "state": "unavailable", "attributes": {}},
            {"entity_id": f"{domain}.s2", "state": "unavailable", "attributes": {}},
            {"entity_id": f"{domain}.s3", "state": "unavailable", "attributes": {}},
        ]
        entity_reg = {
            "data": {
                "entities": [
                    {"entity_id": f"{domain}.s{i}", "platform": domain} for i in range(1, 4)
                ]
            }
        }

        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": states}
            mock_reg.return_value = entity_reg

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["get_integration_health"]
            result = tool(domain)
            data = json.loads(result)

        assert data["success"] is True
        assert data["domain"] == domain
        assert data["status"] == "Critical (All Unavailable)"
        assert data["stats"]["unavailable"] == 3
        assert data["stats"]["availability_pct"] == "0.0%"

    def test_integration_warning_majority_unavailable(
        self, mock_mcp, config_path, ha_url, ha_token
    ):
        domain = "testdomain"
        states = [
            {"entity_id": f"{domain}.s1", "state": "unavailable", "attributes": {}},
            {"entity_id": f"{domain}.s2", "state": "unavailable", "attributes": {}},
            {"entity_id": f"{domain}.s3", "state": "unavailable", "attributes": {}},
            {"entity_id": f"{domain}.s4", "state": "on", "attributes": {}},
            {"entity_id": f"{domain}.s5", "state": "10", "attributes": {}},
        ]
        entity_reg = {
            "data": {
                "entities": [
                    {"entity_id": f"{domain}.s{i}", "platform": domain} for i in range(1, 6)
                ]
            }
        }

        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": states}
            mock_reg.return_value = entity_reg

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["get_integration_health"]
            result = tool(domain)
            data = json.loads(result)

        assert data["success"] is True
        assert data["status"] == "Warning (Majority Unavailable)"
        assert data["stats"]["unavailable"] == 3
        assert data["stats"]["available"] == 2


class TestEnergyRecommendations:
    """Tests for energy dashboard recommendations."""

    def test_off_peak_cheap_tariff(self, mock_mcp, config_path, ha_url, ha_token):
        states = [
            {
                "entity_id": "binary_sensor.workday_sensor",
                "state": "off",
                "attributes": {},
            },
            {
                "entity_id": "sensor.energy_daily",
                "state": "5.0",
                "attributes": {
                    "device_class": "energy",
                    "unit_of_measurement": "kWh",
                },
            },
        ]

        with patch("tools.diagnostics.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["get_energy_dashboard_data"]
            result = tool()
            data = json.loads(result)

        assert data["success"] is True
        assert data["tariff_status"]["is_peak"] is False
        recs = data["recommendations"]
        cheap_rec = next(
            (
                r
                for r in recs
                if "cheap tariff" in r.get("message", "").lower()
                or "good time" in r.get("message", "").lower()
            ),
            None,
        )
        assert cheap_rec is not None

    def test_high_consumption_warning(self, mock_mcp, config_path, ha_url, ha_token):
        states = [
            {
                "entity_id": "sensor.power_total",
                "state": "4200.0",
                "attributes": {
                    "device_class": "power",
                    "unit_of_measurement": "W",
                },
            },
        ]

        with patch("tools.diagnostics.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["get_energy_dashboard_data"]
            result = tool()
            data = json.loads(result)

        assert data["success"] is True
        assert data["consumption"]["current_power_w"] > 3000
        recs = data["recommendations"]
        high_rec = next(
            (
                r
                for r in recs
                if "high consumption" in r.get("message", "").lower()
                or "4200" in r.get("message", "")
            ),
            None,
        )
        assert high_rec is not None
        assert high_rec["priority"] == "warning"


class TestPersonTrackingExtras:
    """Tests for related automations and zone fallback in person tracking."""

    @pytest.mark.asyncio
    async def test_person_with_related_automations(self, mock_mcp, config_path, ha_url, ha_token):
        import yaml

        person_data = {
            "entity_id": "person.test_person",
            "state": "home",
            "attributes": {
                "latitude": 52.0,
                "longitude": 21.0,
                "source": "gps",
                "device_trackers": ["device_tracker.phone"],
            },
        }
        tracker_state = {
            "entity_id": "device_tracker.phone",
            "state": "home",
            "attributes": {"source_type": "gps"},
            "last_updated": "2099-01-01T12:00:00+00:00",
        }

        automations_data = [
            {
                "id": "auto1",
                "alias": "Person Arrived",
                "trigger": [
                    {
                        "platform": "state",
                        "entity_id": "person.test_person",
                        "to": "home",
                    }
                ],
                "action": [
                    {
                        "service": "light.turn_on",
                        "target": {"entity_id": "light.entry"},
                    }
                ],
            },
        ]
        auto_path = Path(config_path) / "automations.yaml"
        auto_path.parent.mkdir(parents=True, exist_ok=True)
        with open(auto_path, "w", encoding="utf-8") as f:
            yaml.dump(automations_data, f)

        entity_reg = {
            "data": {
                "entities": [
                    {
                        "entity_id": "device_tracker.phone",
                        "platform": "mobile_app",
                    },
                    {
                        "entity_id": "person.test_person",
                        "platform": "person",
                    },
                ]
            }
        }
        config_entries = {"data": {"entries": []}}
        zone_reg = {"data": {"items": []}}

        def mock_load(name, path):
            if name == "core.config_entries":
                return config_entries
            if name == "core.entity_registry":
                return entity_reg
            if name == "zone":
                return zone_reg
            return {"data": {}}

        def mock_request(url, token, endpoint, **kwargs):
            if "person" in endpoint and "states" in endpoint:
                return {"success": True, "data": person_data}
            if endpoint == "/api/states":
                return {"success": True, "data": [tracker_state]}
            return {"success": False}

        with (
            patch("tools.diagnostics.load_registry", side_effect=mock_load),
            patch("tools.diagnostics.make_ha_request", side_effect=mock_request),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_person_tracking"]
            result = json.loads(await tool("person.test_person"))

        assert result["success"] is True
        assert result["automations"]["using_this_person"] >= 1
        auto_details = result["automations"]["details"]
        assert len(auto_details) >= 1
        found = next((a for a in auto_details if a["alias"] == "Person Arrived"), None)
        assert found is not None
        assert "trigger" in found["usage"]

    @pytest.mark.asyncio
    async def test_person_with_zone_fallback(self, mock_mcp, config_path, ha_url, ha_token):
        person_data = {
            "entity_id": "person.test_person",
            "state": "home",
            "attributes": {
                "latitude": 52.0,
                "longitude": 21.0,
                "source": "gps",
                "device_trackers": ["device_tracker.phone"],
            },
        }
        tracker_state = {
            "entity_id": "device_tracker.phone",
            "state": "home",
            "attributes": {"source_type": "gps"},
            "last_updated": "2099-01-01T12:00:00+00:00",
        }

        entity_reg = {
            "data": {
                "entities": [
                    {
                        "entity_id": "device_tracker.phone",
                        "platform": "mobile_app",
                    },
                    {
                        "entity_id": "person.test_person",
                        "platform": "person",
                    },
                ]
            }
        }
        # No zone entries in config_entries → triggers legacy fallback
        config_entries = {
            "data": {
                "entries": [
                    {"entry_id": "mqtt_1", "domain": "mqtt"},
                ]
            }
        }
        zone_reg = {
            "data": {
                "items": [
                    {
                        "id": "home_zone",
                        "name": "Home Zone",
                        "latitude": 52.0,
                        "longitude": 21.0,
                        "radius": 100,
                        "passive": False,
                    },
                    {
                        "id": "work_zone",
                        "name": "Work Zone",
                        "latitude": 52.001,
                        "longitude": 21.001,
                        "radius": 50,
                        "passive": False,
                    },
                ]
            }
        }

        def mock_load(name, path):
            if name == "core.config_entries":
                return config_entries
            if name == "core.entity_registry":
                return entity_reg
            if name == "zone":
                return zone_reg
            return {"data": {}}

        def mock_request(url, token, endpoint, **kwargs):
            if "person" in endpoint and "states" in endpoint:
                return {"success": True, "data": person_data}
            if endpoint == "/api/states":
                return {"success": True, "data": [tracker_state]}
            return {"success": False}

        with (
            patch("tools.diagnostics.load_registry", side_effect=mock_load),
            patch("tools.diagnostics.make_ha_request", side_effect=mock_request),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_person_tracking"]
            result = json.loads(await tool("person.test_person"))

        assert result["success"] is True
        assert result["zones"]["total_configured"] >= 2
        nearby = result["zones"]["nearby"]
        assert len(nearby) >= 2
        home_zone = next((z for z in nearby if z["name"] == "Home Zone"), None)
        assert home_zone is not None
        assert home_zone["in_zone"] is True


class TestDiagnoseConnectivity:
    """Tests for diagnose_connectivity tool."""

    def test_healthy(self, mock_mcp, config_path, ha_url, ha_token):
        with patch("tools.diagnostics.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": {}}

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_connectivity"]
            result = json.loads(tool())

        assert result["success"] is True
        assert result["overall_status"] == "healthy"
        assert result["connectivity_issues"] == []

    def test_with_issues(self, mock_mcp, config_path, ha_url, ha_token):
        health_data = {
            "mqtt": {"connected": "error: Connection refused"},
            "hue": {"reachable": False},
        }
        with patch("tools.diagnostics.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": health_data}

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_connectivity"]
            result = json.loads(tool())

        assert result["success"] is True
        assert result["overall_status"] == "degraded"
        assert len(result["connectivity_issues"]) >= 1

    def test_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.diagnostics._do_diagnose_connectivity",
            side_effect=RuntimeError("connectivity check failed"),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_connectivity"]
            result = json.loads(tool())

        assert result["success"] is False


class TestDiagnosePerformance:
    """Tests for diagnose_performance tool."""

    def test_with_data(self, mock_mcp, config_path, ha_url, ha_token):
        states = [
            {
                "entity_id": "sensor.big",
                "state": "42",
                "attributes": {str(i): i for i in range(20)},
            },
            {"entity_id": "sensor.small", "state": "10", "attributes": {"a": 1}},
            {
                "entity_id": "automation.morning_routine",
                "state": "on",
                "attributes": {"last_triggered": "2024-01-01T08:00:00+00:00"},
            },
            {
                "entity_id": "automation.evening_routine",
                "state": "on",
                "attributes": {"last_triggered": "2024-01-10T20:00:00+00:00"},
            },
        ]
        logbook_data = [
            {"entity_id": "automation.morning_routine", "when": "2024-01-01T08:00:00+00:00"},
            {"entity_id": "automation.morning_routine", "when": "2024-01-02T08:00:00+00:00"},
            {"entity_id": "automation.morning_routine", "when": "2024-01-03T08:00:00+00:00"},
            {"entity_id": "automation.evening_routine", "when": "2024-01-10T20:00:00+00:00"},
            {"entity_id": "sensor.big", "when": "2024-01-01T12:00:00+00:00"},
        ]
        auto_path = Path(config_path) / "automations.yaml"
        auto_path.write_text('- id: "auto1"\n  alias: "Test"\n  trigger: []\n  action: []\n')

        with patch("tools.diagnostics.make_ha_request") as mock_req:

            def _side_effect(url, token, path):
                if path == "/api/states":
                    return {"success": True, "data": states}
                return {"success": True, "data": logbook_data}

            mock_req.side_effect = _side_effect

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_performance"]
            result = json.loads(tool())

        assert result["success"] is True
        assert "largest_entities" in result
        assert len(result["largest_entities"]) >= 1
        assert result["largest_entities"][0]["entity_id"] == "sensor.big"
        assert "slowest_automations" in result
        assert len(result["slowest_automations"]) >= 1
        assert "most_triggered" in result
        assert result["most_triggered"][0]["entity_id"] == "automation.morning_routine"
        assert result["most_triggered"][0]["trigger_count"] == 3

    def test_empty_response(self, mock_mcp, config_path, ha_url, ha_token):
        with patch("tools.diagnostics.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": []}

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_performance"]
            result = json.loads(tool())

        assert result["success"] is True

    def test_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.diagnostics._do_diagnose_performance",
            side_effect=RuntimeError("perf fail"),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_performance"]
            result = json.loads(tool())

        assert result["success"] is False


class TestDiagnoseStartupProgress:
    """Tests for diagnose_startup_progress tool."""

    def test_ready(self, mock_mcp, config_path, ha_url, ha_token):
        states = [
            {"entity_id": "sensor.temp", "state": "20.5", "attributes": {}},
        ]
        config_res = {"components": ["sensor", "light"], "version": "2025.1"}
        entries = {
            "data": {
                "entries": [
                    {"entry_id": "e1", "domain": "sensor", "state": "loaded"},
                    {"entry_id": "e2", "domain": "mqtt", "state": "loaded"},
                ]
            }
        }
        auto_path = Path(config_path) / "automations.yaml"
        auto_path.write_text('- id: "auto1"\n  alias: "Test"\n  trigger: []\n  action: []\n')

        req_results = [
            {"success": True, "data": states},
            {"success": True, "data": config_res},
        ]

        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.side_effect = req_results
            mock_reg.return_value = entries

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_startup_progress"]
            result = json.loads(tool())

        assert result["success"] is True
        assert "status" in result
        assert isinstance(result["progress_pct"], (int, float))

    def test_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.diagnostics._do_diagnose_startup_progress",
            side_effect=RuntimeError("startup fail"),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_startup_progress"]
            result = json.loads(tool())

        assert result["success"] is False


class TestEntityHealthSnapshot:
    """Tests for take_entity_health_snapshot and compare_entity_health_snapshot tools."""

    def test_take_snapshot(self, mock_mcp, config_path, ha_url, ha_token, sample_entity_registry):
        states = [
            {"entity_id": "sensor.ok", "state": "20", "attributes": {}},
            {"entity_id": "sensor.bad", "state": "unavailable", "attributes": {}},
        ]
        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": states}
            mock_reg.return_value = sample_entity_registry

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["take_entity_health_snapshot"]
            result = json.loads(tool())

        assert result["success"] is True
        assert "snapshot_id" in result
        assert result["unavailable_count"] >= 1

    def test_compare_snapshot_found(
        self, mock_mcp, config_path, ha_url, ha_token, sample_entity_registry
    ):
        from tools.diagnostics import _SNAPSHOTS

        _SNAPSHOTS["snap_test123"] = {
            "timestamp": 1700000000.0,
            "total_entities": 3,
            "unavailable_count": 2,
            "unavailable_by_integration": {"tuya": 2},
            "unavailable_entity_ids": ["sensor.still_bad", "sensor.old_bad"],
        }
        current_states = [
            {"entity_id": "sensor.ok", "state": "20", "attributes": {}},
            {"entity_id": "sensor.still_bad", "state": "unavailable", "attributes": {}},
        ]
        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": current_states}
            mock_reg.return_value = sample_entity_registry

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["compare_entity_health_snapshot"]
            result = json.loads(tool("snap_test123"))

        assert result["success"] is True
        assert result["resolved_count"] >= 1

    def test_compare_snapshot_not_found(self, mock_mcp, config_path, ha_url, ha_token):
        register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
        tool = mock_mcp._tools["compare_entity_health_snapshot"]
        result = json.loads(tool("snap_nonexistent"))

        assert result["success"] is False

    def test_exception_handler_take(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.diagnostics._do_take_entity_health_snapshot",
            side_effect=RuntimeError("snapshot fail"),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["take_entity_health_snapshot"]
            result = json.loads(tool())

        assert result["success"] is False

    def test_exception_handler_compare(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.diagnostics._do_compare_entity_health_snapshot",
            side_effect=RuntimeError("compare fail"),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["compare_entity_health_snapshot"]
            result = json.loads(tool("snap_test"))

        assert result["success"] is False


class TestDiagnoseVoice:
    """Tests for diagnose_voice tool."""

    def test_with_data(self, mock_mcp, config_path, ha_url, ha_token):
        states = [
            {"entity_id": "stt.whisper", "state": "idle", "attributes": {}},
            {"entity_id": "tts.google", "state": "idle", "attributes": {}},
            {"entity_id": "conversation.homeassistant", "state": "ready", "attributes": {}},
        ]
        voice_data = {"data": {"pipelines": [{"id": "pipe1", "name": "Home"}]}}
        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_reg,
        ):
            mock_req.side_effect = [
                {"success": True, "data": states},
                {"success": True, "data": {"exposed_entities": {"light.living_room": True}}},
            ]
            mock_reg.return_value = voice_data

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_voice"]
            result = json.loads(tool())

        assert result["success"] is True
        assert "assistants_available" in result
        assert result["exposed_entities_count"] >= 1
        assert len(result["pipelines"]) >= 1

    def test_empty(self, mock_mcp, config_path, ha_url, ha_token):
        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry", return_value={"data": {}}),
        ):
            mock_req.return_value = {"success": True, "data": []}

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_voice"]
            result = json.loads(tool())

        assert result["success"] is True
        assert result["exposed_entities_count"] == 0

    def test_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.diagnostics._do_diagnose_voice",
            side_effect=RuntimeError("voice fail"),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_voice"]
            result = json.loads(tool())

        assert result["success"] is False


class TestDiagnoseInstallationType:
    """Tests for diagnose_installation_type tool."""

    def test_supervised(self, mock_mcp, config_path, ha_url, ha_token):
        with patch("tools.diagnostics.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": {"type": "os"}}

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_installation_type"]
            result = json.loads(tool())

        assert result["success"] is True
        assert result["type"] in ("supervised", "os")

    def test_container(self, mock_mcp, config_path, ha_url, ha_token):
        with patch("tools.diagnostics.make_ha_request") as mock_req:
            mock_req.side_effect = [
                {"success": False, "error": "not found"},
                {"success": True, "data": {"version": "2025.1"}},
            ]

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_installation_type"]
            result = json.loads(tool())

        assert result["success"] is True
        assert result["type"] in ("container", "core")

    def test_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.diagnostics._do_diagnose_installation_type",
            side_effect=RuntimeError("install type fail"),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_installation_type"]
            result = json.loads(tool())

        assert result["success"] is False


class TestDiagnosePostUpdateIntegrations:
    """Tests for diagnose_post_update_integrations tool."""

    def test_with_custom_components(self, mock_mcp, config_path, ha_url, ha_token):
        entries = {
            "data": {
                "entries": [
                    {"entry_id": "e1", "domain": "sensor", "title": "Builtin", "state": "loaded"},
                    {
                        "entry_id": "e2",
                        "domain": "tuya_local",
                        "title": "Tuya Local",
                        "state": "loaded",
                    },
                    {"entry_id": "e3", "domain": "gree", "title": "Gree", "state": "failed"},
                ]
            }
        }
        with patch("tools.diagnostics.load_registry") as mock_reg:
            mock_reg.return_value = entries

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_post_update_integrations"]
            result = json.loads(tool())

        assert result["success"] is True
        assert result["custom_components_total"] > 0

    def test_no_custom_components(self, mock_mcp, config_path, ha_url, ha_token):
        entries = {
            "data": {
                "entries": [
                    {"entry_id": "e1", "domain": "sensor", "title": "Builtin", "state": "loaded"},
                    {"entry_id": "e2", "domain": "light", "title": "Light", "state": "loaded"},
                ]
            }
        }
        with patch("tools.diagnostics.load_registry") as mock_reg:
            mock_reg.return_value = entries

            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_post_update_integrations"]
            result = json.loads(tool())

        assert result["success"] is True
        assert result["custom_components_total"] == 0

    def test_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.diagnostics._do_diagnose_post_update_integrations",
            side_effect=RuntimeError("post-update fail"),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_post_update_integrations"]
            result = json.loads(tool())

        assert result["success"] is False
