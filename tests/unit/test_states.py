"""
Tests for tools/states.py
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from tools.states import _clear_cache, register_state_tools


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
    """Mock MCP server instatece."""

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
    """Sample states for testing."""
    now = datetime.now(UTC)
    recent = (now - timedelta(minutes=30)).isoformat()
    old = (now - timedelta(hours=5)).isoformat()

    return [
        {
            "entity_id": "sensor.temperature_living_room",
            "state": "22.5",
            "last_changed": recent,
            "last_updated": recent,
            "attributes": {
                "friendly_name": "Living Room Temperature",
                "device_class": "temperature",
                "unit_of_measurement": "°C",
                "icon": "mdi:thermometer",  # Should be filtered
            },
        },
        {
            "entity_id": "sensor.humidity_living_room",
            "state": "45",
            "last_changed": recent,
            "last_updated": recent,
            "attributes": {
                "friendly_name": "Living Room Humidity",
                "device_class": "humidity",
                "unit_of_measurement": "%",
            },
        },
        {
            "entity_id": "light.living_room",
            "state": "on",
            "last_changed": recent,
            "last_updated": recent,
            "attributes": {"friendly_name": "Living Room Light", "brightness": 255},
        },
        {
            "entity_id": "switch.kitchen",
            "state": "off",
            "last_changed": old,
            "last_updated": old,
            "attributes": {"friendly_name": "Kitchen Switch"},
        },
        {
            "entity_id": "sensor.unavailable_sensor",
            "state": "unavailable",
            "last_changed": recent,
            "last_updated": recent,
            "attributes": {"friendly_name": "Broken Sensor"},
        },
        {
            "entity_id": "binary_sensor.motion_living_room",
            "state": "off",
            "last_changed": recent,
            "last_updated": recent,
            "attributes": {"friendly_name": "Living Room Motion", "device_class": "motion"},
        },
        {
            "entity_id": "automation.test_automation",
            "state": "on",
            "last_changed": recent,
            "last_updated": recent,
            "attributes": {
                "friendly_name": "Test Automation",
                "last_triggered": recent,
                "mode": "single",
            },
        },
        {
            "entity_id": "automation.disabled_automation",
            "state": "off",
            "last_changed": old,
            "last_updated": old,
            "attributes": {"friendly_name": "Disabled Automation", "mode": "single"},
        },
        # Ignorable entities
        {
            "entity_id": "sun.sun",
            "state": "above_horizon",
            "last_changed": recent,
            "last_updated": recent,
            "attributes": {"friendly_name": "Sun"},
        },
        {
            "entity_id": "weather.home",
            "state": "sunny",
            "last_changed": recent,
            "last_updated": recent,
            "attributes": {"friendly_name": "Weather"},
        },
    ]


@pytest.fixture
def sample_entity_registry():
    return {
        "data": {
            "entities": [
                {
                    "entity_id": "sensor.temperature_living_room",
                    "platform": "hue",
                    "device_id": "dev1",
                },
                {
                    "entity_id": "sensor.humidity_living_room",
                    "platform": "hue",
                    "device_id": "dev1",
                },
                {
                    "entity_id": "light.living_room",
                    "platform": "hue",
                    "device_id": "dev2",
                },
                {
                    "entity_id": "switch.kitchen",
                    "platform": "tuya",
                    "device_id": "dev3",
                },
                {
                    "entity_id": "sensor.unavailable_sensor",
                    "platform": "tuya",
                    "device_id": "dev4",
                },
            ]
        }
    }


@pytest.fixture
def sample_device_registry():
    return {
        "data": {
            "devices": [
                {"id": "dev1", "name": "Hue Sensor"},
                {"id": "dev2", "name": "Hue Light"},
                {"id": "dev3", "name": "Tuya Switch"},
                {"id": "dev4", "name": "Tuya Sensor"},
            ]
        }
    }


def run_async(coro):
    """Helper to run async functions in tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestGetAllStates:
    def test_get_all_states_basic(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_all_states"]
            result = run_async(tool(domain=None, include_attributes=False))
            data = json.loads(result)

        assert data["success"] is True
        assert data["count"] == len(sample_states)

        # Check that icon is filtered out
        for state in data["states"]:
            if state.get("attributes"):
                assert "icon" not in state["attributes"]

    def test_get_all_states_domain_filter(
        self, mock_mcp, config_path, ha_url, ha_token, sample_states
    ):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_all_states"]
            result = run_async(tool(domain="sensor", include_attributes=False))
            data = json.loads(result)

        assert data["success"] is True
        # Should only return sensor entities
        for state in data["states"]:
            assert state["entity_id"].startswith("sensor.")


class TestGetEntityState:
    def test_get_single_entity(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states[0]}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_entity_state"]
            result = run_async(tool(entity_id="sensor.temperature_living_room"))
            data = json.loads(result)

        assert data["success"] is True
        assert data["entity"]["entity_id"] == "sensor.temperature_living_room"
        assert "context" not in data["entity"]

    def test_entity_not_found(self, mock_mcp, config_path, ha_url, ha_token):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": False, "error": "404 Not Found"}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_entity_state"]
            result = run_async(tool(entity_id="sensor.nonexistent"))
            data = json.loads(result)

        assert data["success"] is False
        assert "not found" in data["error"].lower()


class TestGetEntityStateBatch:
    def test_batch_entities(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_entity_state_batch"]
            result = run_async(
                tool(
                    entity_ids="sensor.temperature_living_room,light.living_room,sensor.nonexistent"
                )
            )
            data = json.loads(result)

        assert data["success"] is True
        assert data["found_count"] == 2
        assert data["missing_count"] == 1
        assert "sensor.nonexistent" in data["missing_ids"]

    def test_batch_too_many(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            # Create 101 entity ids
            many_ids = ",".join([f"sensor.test_{i}" for i in range(101)])

            tool = mock_mcp._tools["get_entity_state_batch"]
            result = run_async(tool(entity_ids=many_ids))
            data = json.loads(result)

        assert data["success"] is False
        assert "Too many" in data["error"]


class TestGetStatesGrouped:
    def test_group_by_domain(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_states_grouped"]
            result = run_async(tool(group_by="domain"))
            data = json.loads(result)

        assert data["success"] is True
        assert "groups" in data
        assert "sensor" in data["groups"]
        assert "light" in data["groups"]

        # Check structure
        for group_name, group_data in data["groups"].items():
            assert "count" in group_data
            assert "state_distribution" in group_data
            assert "sample_entities" in group_data

    def test_group_with_state_filter(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_states_grouped"]
            result = run_async(tool(group_by="domain", state_filter="unavailable"))
            data = json.loads(result)

        assert data["success"] is True
        # Should only have groups with unavailable entities
        total = sum(g["count"] for g in data["groups"].values())
        assert total >= 1

    def test_group_counts_only(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_states_grouped"]
            result = run_async(tool(group_by="domain", include_counts_only=True))
            data = json.loads(result)

        assert data["success"] is True
        # Should not have sample_entities when include_counts_only=True
        for group_data in data["groups"].values():
            assert "sample_entities" not in group_data


class TestSearchEntities:
    def test_search_by_name(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["search_entities"]
            result = run_async(tool(search_term="living room"))
            data = json.loads(result)

        assert data["success"] is True
        assert data["count"] >= 2  # temperature, humidity, light

        for entity in data["results"]:
            assert (
                "living" in entity["entity_id"].lower()
                or "living" in entity["friendly_name"].lower()
            )

    def test_search_with_domain(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["search_entities"]
            result = run_async(tool(search_term="living", domain="sensor"))
            data = json.loads(result)

        assert data["success"] is True
        for entity in data["results"]:
            assert entity["entity_id"].startswith("sensor.")


class TestGetDomainsSummary:
    def test_domains_summary(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_domains_summary"]
            result = run_async(tool())
            data = json.loads(result)

        assert data["success"] is True
        assert data["total_entities"] == len(sample_states)
        assert "by_domain" in data

        # Check structure
        for domain, stats in data["by_domain"].items():
            assert "total" in stats
            assert "unavailable" in stats
            assert "unknown" in stats


class TestGetSystemOverview:
    def test_system_overview_basic(
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
            patch("tools.states.make_ha_request") as mock_req,
            patch("tools.states.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": sample_states}
            mock_reg.side_effect = lambda name, path: (
                sample_entity_registry
                if "entity" in name
                else sample_device_registry
                if "device" in name
                else {"data": {}}
            )

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_system_overview"]
            result = run_async(
                tool(
                    include_states=False,
                    include_unavailable=True,
                    include_problems=True,
                    group_unavailable_by="integration",
                )
            )
            data = json.loads(result)

        assert data["success"] is True
        assert "summary" in data
        assert data["summary"]["total_entities"] == len(sample_states)
        assert data["summary"]["unavailable_count"] >= 1

        # Check unavailable grouping
        assert "unavailable_by_group" in data

        # Check problems
        assert "problems_count" in data

    def test_system_overview_with_states(
        self, mock_mcp, config_path, ha_url, ha_token, sample_states
    ):
        with (
            patch("tools.states.make_ha_request") as mock_req,
            patch("tools.states.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": sample_states}
            mock_reg.return_value = {"data": {"entities": [], "devices": []}}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_system_overview"]
            result = run_async(tool(include_states=True))
            data = json.loads(result)

        assert data["success"] is True
        assert "states" in data
        assert len(data["states"]) == len(sample_states)


class TestGetStatesFiltered:
    def test_filter_by_domain(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_states_filtered"]
            result = run_async(tool(domains="sensor,binary_sensor"))
            data = json.loads(result)

        assert data["success"] is True
        for entity in data["entities"]:
            domain = entity["entity_id"].split(".")[0]
            assert domain in ["sensor", "binary_sensor"]

    def test_filter_by_state(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_states_filtered"]
            result = run_async(tool(state="unavailable"))
            data = json.loads(result)

        assert data["success"] is True
        for entity in data["entities"]:
            assert entity["state"] == "unavailable"

    def test_filter_by_device_class(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_states_filtered"]
            result = run_async(tool(device_class="temperature", include_attributes=True))
            data = json.loads(result)

        assert data["success"] is True
        assert data["count"] >= 1

    def test_filter_grouped(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_states_filtered"]
            result = run_async(tool(domains="sensor,light", group_results=True))
            data = json.loads(result)

        assert data["success"] is True
        assert "by_domain" in data


class TestGetEntityChanges:
    def test_recent_changes(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_entity_changes"]
            result = run_async(tool(hours_back=1))
            data = json.loads(result)

        assert data["success"] is True
        assert "total_changed" in data
        assert "by_domain" in data

        # Some entities should have changed in last hour
        assert data["total_changed"] >= 1

    def test_changes_with_domain_filter(
        self, mock_mcp, config_path, ha_url, ha_token, sample_states
    ):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_entity_changes"]
            result = run_async(tool(hours_back=1, domains="sensor"))
            data = json.loads(result)

        assert data["success"] is True
        # Should only have sensor domain in results
        for domain in data["by_domain"].keys():
            assert domain == "sensor"


class TestGetHistoryBatch:
    def test_history_batch(self, mock_mcp, config_path, ha_url, ha_token):
        history_data = [
            [
                {
                    "entity_id": "sensor.test",
                    "state": "22",
                    "last_changed": "2024-01-01T12:00:00Z",
                },
                {
                    "entity_id": "sensor.test",
                    "state": "23",
                    "last_changed": "2024-01-01T11:00:00Z",
                },
            ]
        ]

        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": history_data}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_history_batch"]
            result = run_async(tool(entity_ids="sensor.test", hours_back=24, limit=10))
            data = json.loads(result)

        assert data["success"] is True
        assert "history" in data
        assert "sensor.test" in data["history"]

    def test_history_too_many_entities(self, mock_mcp, config_path, ha_url, ha_token):
        register_state_tools(mock_mcp, ha_url, ha_token, config_path)

        many_ids = ",".join([f"sensor.test_{i}" for i in range(25)])

        tool = mock_mcp._tools["get_history_batch"]
        result = run_async(tool(entity_ids=many_ids))
        data = json.loads(result)

        assert data["success"] is False
        assert "Too many" in data["error"]


class TestVerifyRecentImplementation:
    def test_verify_recent(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["verify_recent_implementation"]
            result = run_async(tool(hours_back=1))
            data = json.loads(result)

        assert data["success"] is True
        assert "meta" in data
        assert "summary" in data
        assert "recent_entities" in data
        assert "automations" in data
        assert "issues" in data

    def test_verify_with_pattern(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["verify_recent_implementation"]
            result = run_async(tool(hours_back=1, entity_pattern="living"))
            data = json.loads(result)

        assert data["success"] is True
        # Should filter to only living room entities
        for entity in data["recent_entities"]:
            assert (
                "living" in entity["entity_id"].lower()
                or "living" in entity["friendly_name"].lower()
            )

    def test_verify_automations(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["verify_recent_implementation"]
            result = run_async(
                tool(
                    hours_back=1,
                    automation_ids="automation.test_automation,automation.disabled_automation",
                )
            )
            data = json.loads(result)

        assert data["success"] is True
        # Should have automation info
        assert len(data["automations"]) >= 1

        # Disabled automation should be in issues
        disabled_issues = [i for i in data["issues"] if i.get("type") == "automation_state"]
        assert len(disabled_issues) >= 1


class TestGetServices:
    def test_get_services_all(self, mock_mcp, config_path, ha_url, ha_token):
        services_data = [
            {"domain": "light", "services": {"turn_on": {}, "turn_off": {}}},
            {"domain": "switch", "services": {"turn_on": {}, "turn_off": {}}},
        ]
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": services_data}
            register_state_tools(mock_mcp, ha_url, ha_token, config_path)
            data = json.loads(run_async(mock_mcp._tools["get_services"]()))
        assert data["success"] is True
        assert len(data["services"]) == 2

    def test_get_services_domain_filter(self, mock_mcp, config_path, ha_url, ha_token):
        services_data = [
            {"domain": "light", "services": {"turn_on": {}}},
            {"domain": "switch", "services": {"turn_on": {}}},
        ]
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": services_data}
            register_state_tools(mock_mcp, ha_url, ha_token, config_path)
            data = json.loads(run_async(mock_mcp._tools["get_services"](domain="light")))
        assert data["success"] is True
        assert len(data["services"]) == 1
        assert data["services"][0]["domain"] == "light"


class TestGetAllStatesTooMany:
    def test_too_many_entities_returns_error(self, mock_mcp, config_path, ha_url, ha_token):
        """More than 500 entities without domain filter → success: False."""
        big_states = [
            {
                "entity_id": f"sensor.s_{i}",
                "state": "ok",
                "attributes": {},
                "last_changed": "",
                "last_updated": "",
            }
            for i in range(501)
        ]
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": big_states}
            register_state_tools(mock_mcp, ha_url, ha_token, config_path)
            data = json.loads(run_async(mock_mcp._tools["get_all_states"]()))
        assert data["success"] is False
        assert "Too many" in data["error"]


class TestGetStatesGroupedByIntegration:
    def test_group_by_integration(
        self,
        mock_mcp,
        config_path,
        ha_url,
        ha_token,
        sample_states,
        sample_entity_registry,
    ):
        with (
            patch("tools.states.make_ha_request") as mock_req,
            patch("tools.states.load_registry") as mock_reg,
        ):
            mock_req.return_value = {"success": True, "data": sample_states}
            mock_reg.return_value = sample_entity_registry
            register_state_tools(mock_mcp, ha_url, ha_token, config_path)
            data = json.loads(
                run_async(mock_mcp._tools["get_states_grouped"](group_by="integration"))
            )
        assert data["success"] is True
        assert data["group_by"] == "integration"
        # Entities with known platform should be grouped by platform
        assert any(g in data["groups"] for g in ("hue", "tuya"))


class TestGetHistoryBatchEmptyIds:
    def test_empty_entity_ids(self, mock_mcp, config_path, ha_url, ha_token):
        register_state_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(run_async(mock_mcp._tools["get_history_batch"](entity_ids="   ")))
        assert data["success"] is False
        assert "No entity_ids" in data["error"]


class TestGetStatesFilteredByArea:
    def test_filter_by_area(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        with patch("tools.states.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}
            register_state_tools(mock_mcp, ha_url, ha_token, config_path)
            data = json.loads(
                run_async(mock_mcp._tools["get_states_filtered"](areas="living_room"))
            )
        assert data["success"] is True
        # All returned entity_ids should contain 'living_room'
        for entity in data["entities"]:
            assert "living_room" in entity["entity_id"].lower()

    def test_cache_works(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        call_count = 0

        def mock_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"success": True, "data": sample_states}

        with patch("tools.states.make_ha_request", side_effect=mock_request):
            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_domains_summary"]

            # First call
            result1 = run_async(tool())

            # Second call (should be cached)
            result2 = run_async(tool())

            assert result1 == result2
            assert call_count == 1  # Only one API call

    def test_cache_cleared(self, mock_mcp, config_path, ha_url, ha_token, sample_states):
        call_count = 0

        def mock_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"success": True, "data": sample_states}

        with patch("tools.states.make_ha_request", side_effect=mock_request):
            register_state_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_domains_summary"]

            run_async(tool())

            # Clear cache
            _clear_cache()

            run_async(tool())

            assert call_count == 2  # Two API calls after cache clear
