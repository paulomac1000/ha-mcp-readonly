"""
Tests for tools/automations.py
"""

import json
from unittest.mock import patch

import pytest

from tools.automations import register_automation_tools

AUTOMATIONS_YAML = """
- id: "123"
  alias: "Test Automation One"
  description: "First test automation"
  mode: "single"
  trigger:
    - platform: state
      entity_id: "binary_sensor.door"
      to: "on"
  condition: []
  action:
    - service: "light.turn_on"
      target:
        entity_id: "light.room"

- id: "456"
  alias: "Another Automation"
  description: "Second automation"
  mode: "restart"
  trigger:
    - platform: state
      entity_id: "light.room"
      to: "off"
  condition: []
  action:
    - service: "light.turn_on"
      target:
        entity_id: "light.room"
"""


@pytest.fixture
def config_path(tmp_path) -> str:
    """Override global config_path for this test module."""
    # writesmy automations.yaml w directoryu tymczasowym
    (tmp_path / "automations.yaml").write_text(AUTOMATIONS_YAML, encoding="utf-8")
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


class TestListAutomations:
    def test_list_all_automations(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["list_automations"]
        data = json.loads(tool())

        assert data["success"] is True
        assert data["total_count"] == 2
        aliases = [a["alias"] for a in data["automations"]]
        assert "Test Automation One" in aliases
        assert "Another Automation" in aliases

    def test_list_automations_empty_file(self, mock_mcp, tmp_path):
        (tmp_path / "automations.yaml").write_text("", encoding="utf-8")
        register_automation_tools(mock_mcp, str(tmp_path))

        tool = mock_mcp._tools["list_automations"]
        data = json.loads(tool())

        assert data["success"] is True
        assert data["total_count"] == 0


class TestGetAutomationCode:
    def test_get_code_by_alias(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_automation_code"]
        data = json.loads(tool("Test Automation One"))

        assert data["success"] is True
        assert data["alias"] == "Test Automation One"
        assert "code" in data
        # id must be stripped from code (top-level key only, not entity_id etc.)
        assert not any(
            line.strip() == "id: '123'" or line.strip() == 'id: "123"'
            for line in data["code"].splitlines()
        )

    def test_get_code_not_found(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_automation_code"]
        data = json.loads(tool("NonExistent"))

        assert data["success"] is False
        assert "not found" in data["error"].lower()


class TestSearchAutomationsByEntity:
    def test_entity_found_in_trigger_and_action(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["search_automations_by_entity"]
        data = json.loads(tool("light.room"))

        assert data["success"] is True
        # light.room appears in both automations (trigger + action)
        assert data["found_in_count"] == 2
        aliases = [a["alias"] for a in data["automations"]]
        assert "Test Automation One" in aliases
        assert "Another Automation" in aliases

    def test_entity_in_trigger_only(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["search_automations_by_entity"]
        data = json.loads(tool("binary_sensor.door"))

        assert data["success"] is True
        assert data["found_in_count"] == 1
        assert "trigger" in data["automations"][0]["usage_type"]

    def test_entity_not_found(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["search_automations_by_entity"]
        data = json.loads(tool("sensor.nonexistent"))

        assert data["success"] is True
        assert data["found_in_count"] == 0


class TestSearchAutomations:
    def test_search_by_alias(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["search_automations"]
        result = tool(search_term="Test", include_code=True)
        data = json.loads(result)

        assert data["success"] is True
        assert data["matched_count"] == 1
        assert data["results"][0]["alias"] == "Test Automation One"
        assert "code" in data["results"][0]

    def test_search_mode_and_blueprint_filters(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["search_automations"]
        data = json.loads(tool(mode="restart"))
        assert data["matched_count"] == 1
        assert data["results"][0]["mode"] == "restart"

        # uses_blueprint False should include native automations
        data = json.loads(tool(uses_blueprint=False))
        assert data["matched_count"] == 2


class TestAutomationDependencies:
    def test_get_automation_dependencies(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_automation_dependencies"]
        result = tool("Test Automation One")
        data = json.loads(result)

        assert data["success"] is True
        deps = data["dependencies"]
        assert deps["entities_count"] >= 2
        assert "light.room" in deps["entities"]
        assert "binary_sensor.door" in deps["entities"]

    def test_dependencies_not_found(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)
        tool = mock_mcp._tools["get_automation_dependencies"]
        data = json.loads(tool("missing"))
        assert data["success"] is False


class TestAutomationConflicts:
    def test_get_automation_conflicts(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_automation_conflicts"]
        result = tool("light.room")
        data = json.loads(result)

        assert data["success"] is True
        analysis = data["conflict_analysis"]
        # Two automations control the same light
        assert analysis["race_condition_risk"] is True
        assert len(data["controlling_automations"]) == 2

    def test_get_automation_conflicts_no_results(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_automation_conflicts"]
        data = json.loads(tool("sensor.no_usage"))

        assert data["success"] is True
        assert data["conflict_analysis"]["race_condition_risk"] is False
        assert len(data["controlling_automations"]) == 0
        assert "No conflicts" in data["recommendations"][0]


class TestDiagnoseAutomation:
    def test_diagnose_automation_basic(self, mock_mcp, config_path, ha_url, ha_token):
        # sample states: binary_sensor.door OK, light.room unavailable
        sample_states = [
            {
                "entity_id": "binary_sensor.door",
                "state": "off",
                "attributes": {"friendly_name": "Door"},
            },
            {
                "entity_id": "light.room",
                "state": "unavailable",
                "attributes": {"friendly_name": "Room Light"},
            },
        ]

        def make_ha_request_side_effect(
            ha_url_, ha_token_, endpoint, method="GET", data=None, **kwargs
        ):
            if endpoint == "/api/states":
                return {"success": True, "data": sample_states}
            if endpoint == "/api/template":
                return {"success": True, "data": "OK"}
            return {"success": False, "error": "Unexpected endpoint"}

        with patch("tools.automations.make_ha_request", side_effect=make_ha_request_side_effect):
            register_automation_tools(mock_mcp, config_path, ha_url, ha_token)

            tool = mock_mcp._tools["diagnose_automation"]
            result = tool("Test Automation One", detail_level="summary")
            data = json.loads(result)

        assert data["success"] is True
        stats = data["statistics"]
        assert stats["total_entities"] >= 2
        # Jedna entity unavailable
        assert stats["unavailable_entities"] == 1
        # There should be recommendations
        assert len(data["recommendations"]) > 0

    def test_diagnose_automation_not_found(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.automations.make_ha_request",
            return_value={"success": True, "data": []},
        ):
            register_automation_tools(mock_mcp, config_path, ha_url, ha_token)

            tool = mock_mcp._tools["diagnose_automation"]
            data = json.loads(tool("missing"))

        assert data["success"] is False
        assert "not found" in data["error"]

    def test_diagnose_automation_full_detail(self, mock_mcp, config_path, ha_url, ha_token):
        sample_states = [
            {"entity_id": "binary_sensor.door", "state": "off", "attributes": {}},
            {"entity_id": "light.room", "state": "on", "attributes": {}},
        ]

        def make_ha_request_side_effect(
            ha_url_, ha_token_, endpoint, method="GET", data=None, **kwargs
        ):
            if endpoint == "/api/states":
                return {"success": True, "data": sample_states}
            if endpoint == "/api/template":
                return {"success": True, "data": "OK"}
            return {"success": False, "error": "Unexpected endpoint"}

        with patch("tools.automations.make_ha_request", side_effect=make_ha_request_side_effect):
            register_automation_tools(mock_mcp, config_path, ha_url, ha_token)

            tool = mock_mcp._tools["diagnose_automation"]
            data = json.loads(tool("Test Automation One", detail_level="full"))

        assert data["success"] is True
        assert "entity_validation" in data
        assert "binary_sensor.door" in data["entity_validation"]
        assert "light.room" in data["entity_validation"]
        assert "trigger_analysis" in data
        assert "action_analysis" in data


class TestAutomationUsageStats:
    def test_get_automation_usage_stats(self, mock_mcp, config_path, ha_url, ha_token):
        """
        Tests get_automation_usage_stats with a simple scenario:
        - automation.123 exists
        - history: off -> on -> off (1 run)
        """

        # Automation entity_id will be automation.123 (slug from id "123")
        automation_state = {
            "entity_id": "automation.123",
            "state": "on",
            "attributes": {
                "last_triggered": "2025-01-01T12:00:00+00:00",
                "friendly_name": "Test Automation One",
            },
        }

        history_series = [
            [
                {
                    "entity_id": "automation.123",
                    "state": "off",
                    "last_changed": "2025-01-01T10:00:00+00:00",
                },
                {
                    "entity_id": "automation.123",
                    "state": "on",
                    "last_changed": "2025-01-01T11:00:00+00:00",
                },
                {
                    "entity_id": "automation.123",
                    "state": "off",
                    "last_changed": "2025-01-01T12:00:00+00:00",
                },
            ]
        ]

        def make_ha_request_side_effect(
            ha_url_, ha_token_, endpoint, method="GET", data=None, **kwargs
        ):
            if endpoint == "/api/states":
                # All states
                return {"success": True, "data": [automation_state]}
            if endpoint.startswith("/api/states/automation."):
                return {"success": True, "data": automation_state}
            if endpoint.startswith("/api/history/period/"):
                return {"success": True, "data": history_series}
            return {"success": False, "error": "Unexpected endpoint"}

        with patch("tools.automations.make_ha_request", side_effect=make_ha_request_side_effect):
            register_automation_tools(mock_mcp, config_path, ha_url, ha_token)

            tool = mock_mcp._tools["get_automation_usage_stats"]
            result = tool("Test Automation One", hours_back=24)
            data = json.loads(result)

        assert data["success"] is True
        stats = data["stats"]
        assert stats["run_count"] == 1
        assert stats["is_enabled"] is True
        assert stats["is_working"] is True

    def test_get_automation_usage_stats_not_found(self, mock_mcp, config_path, ha_url, ha_token):
        def make_ha_request_side_effect(
            ha_url_, ha_token_, endpoint, method="GET", data=None, **kwargs
        ):
            return {"success": True, "data": []}

        with patch("tools.automations.make_ha_request", side_effect=make_ha_request_side_effect):
            register_automation_tools(mock_mcp, config_path, ha_url, ha_token)

            tool = mock_mcp._tools["get_automation_usage_stats"]
            data = json.loads(tool("Unknown Automation", hours_back=24))

        assert data["success"] is False
        assert "not found" in data.get("error", "").lower()

    def test_get_automation_usage_stats_no_ha_config(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)  # no ha_url / ha_token

        tool = mock_mcp._tools["get_automation_usage_stats"]
        data = json.loads(tool("Test Automation One", hours_back=24))

        assert data["success"] is False
        assert "HA API" in data["error"] or "ha_url" in data["error"]
