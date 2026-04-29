"""
Tests for tools/diagnostics.py
"""

import json
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
