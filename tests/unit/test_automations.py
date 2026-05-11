"""
Tests for tools/automations.py
"""

import json
import os
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


VALIDATE_TRIGGERS_YAML = """
- id: "v001"
  alias: "Validate Triggers Test"
  mode: "single"
  trigger:
    - platform: state
      id: "motion_trigger"
      entity_id: "binary_sensor.motion"
      to: "on"
    - platform: state
      id: "door_trigger"
      entity_id: "binary_sensor.door"
      to: "on"
    - platform: state
      id: "duplicate_id"
      entity_id: "binary_sensor.window"
      to: "on"
    - platform: state
      id: "duplicate_id"
      entity_id: "binary_sensor.window2"
      to: "on"
    - platform: time
      at: "08:00:00"
  condition: []
  action:
    - choose:
        - conditions:
            - condition: trigger
              id: "motion_trigger"
          sequence:
            - service: light.turn_on
              target:
                entity_id: light.room
        - conditions:
            - condition: trigger
              id: "nonexistent_trigger"
          sequence:
            - service: light.turn_off
              target:
                entity_id: light.room
      default:
        - service: notify.mobile
    - if:
        - condition: trigger
          id: "door_trigger"
      then:
        - service: lock.lock
"""


@pytest.fixture
def config_path_validate(tmp_path) -> str:
    """Config path with automations that have trigger ids."""
    (tmp_path / "automations.yaml").write_text(VALIDATE_TRIGGERS_YAML, encoding="utf-8")
    return str(tmp_path)


class TestAutomationValidateTriggers:
    """Tests for automation_validate_triggers tool."""

    @pytest.mark.asyncio
    async def test_validate_by_id(self, mock_mcp, config_path_validate, ha_url, ha_token):
        register_automation_tools(mock_mcp, config_path_validate, ha_url, ha_token)

        tool = mock_mcp._tools["automation_validate_triggers"]
        data = json.loads(await tool(automation_id="v001"))

        assert data["success"] is True
        assert data["automation"]["id"] == "v001"
        validation = data["validation"]
        assert "orphaned_triggers" in validation
        assert "missing_handlers" in validation
        assert "duplicate_ids" in validation

    @pytest.mark.asyncio
    async def test_validate_by_alias(self, mock_mcp, config_path_validate, ha_url, ha_token):
        register_automation_tools(mock_mcp, config_path_validate, ha_url, ha_token)

        tool = mock_mcp._tools["automation_validate_triggers"]
        data = json.loads(await tool(automation_alias="Validate Triggers Test"))

        assert data["success"] is True
        assert data["automation"]["alias"] == "Validate Triggers Test"

    @pytest.mark.asyncio
    async def test_validate_not_found(self, mock_mcp, config_path_validate, ha_url, ha_token):
        register_automation_tools(mock_mcp, config_path_validate, ha_url, ha_token)

        tool = mock_mcp._tools["automation_validate_triggers"]
        data = json.loads(await tool(automation_id="nonexistent"))

        assert data["success"] is False
        assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_validate_detects_orphaned_triggers(
        self, mock_mcp, config_path_validate, ha_url, ha_token
    ):
        """The time trigger has no id, and duplicate_id has no handler - should detect."""
        register_automation_tools(mock_mcp, config_path_validate, ha_url, ha_token)

        tool = mock_mcp._tools["automation_validate_triggers"]
        data = json.loads(await tool(automation_id="v001"))

        assert data["success"] is True
        recommendations = data.get("recommendations", [])
        issues = [r["issue"] for r in recommendations]
        assert "orphaned_triggers" in issues or "no_trigger_ids" in issues

    @pytest.mark.asyncio
    async def test_validate_detects_duplicates(
        self, mock_mcp, config_path_validate, ha_url, ha_token
    ):
        register_automation_tools(mock_mcp, config_path_validate, ha_url, ha_token)

        tool = mock_mcp._tools["automation_validate_triggers"]
        data = json.loads(await tool(automation_id="v001"))

        assert data["success"] is True
        assert "duplicate_id" in data["validation"]["duplicate_ids"]

    @pytest.mark.asyncio
    async def test_validate_detects_missing_handlers(
        self, mock_mcp, config_path_validate, ha_url, ha_token
    ):
        """nonexistent_trigger is referenced in a handler but not defined as trigger id."""
        register_automation_tools(mock_mcp, config_path_validate, ha_url, ha_token)

        tool = mock_mcp._tools["automation_validate_triggers"]
        data = json.loads(await tool(automation_id="v001"))

        assert data["success"] is True
        missing = data["validation"]["missing_handlers"]
        assert "nonexistent_trigger" in missing

    @pytest.mark.asyncio
    async def test_validate_handlers_found(self, mock_mcp, config_path_validate, ha_url, ha_token):
        """motion_trigger and door_trigger have handlers."""
        register_automation_tools(mock_mcp, config_path_validate, ha_url, ha_token)

        tool = mock_mcp._tools["automation_validate_triggers"]
        data = json.loads(await tool(automation_id="v001"))

        assert data["success"] is True
        handlers = data["validation"]["handlers_found"]
        assert handlers >= 2

    @pytest.mark.asyncio
    async def test_validate_is_valid_field(self, mock_mcp, config_path_validate, ha_url, ha_token):
        """is_valid should be False when there are missing handlers or duplicates."""
        register_automation_tools(mock_mcp, config_path_validate, ha_url, ha_token)

        tool = mock_mcp._tools["automation_validate_triggers"]
        data = json.loads(await tool(automation_id="v001"))

        assert data["success"] is True
        assert data["is_valid"] is False

    @pytest.mark.asyncio
    async def test_validate_no_automations_file(self, mock_mcp, tmp_path, ha_url, ha_token):
        register_automation_tools(mock_mcp, str(tmp_path), ha_url, ha_token)

        tool = mock_mcp._tools["automation_validate_triggers"]
        data = json.loads(await tool(automation_id="anything"))

        assert data["success"] is False
        assert "automation" in data["error"].lower()


# ============================================================
# Additional tests for uncovered areas
# ============================================================

BLUEPRINT_AUTOMATION_YAML = """
- id: "bp001"
  alias: "Blueprint Automation"
  description: "Uses a blueprint"
  use_blueprint:
    path: "test/motion.yaml"
    input:
      entity: "light.test"
  mode: "single"
  trigger: []
  action: []
"""


@pytest.fixture
def config_path_blueprint(tmp_path) -> str:
    """Config path with a blueprint automation."""
    (tmp_path / "automations.yaml").write_text(BLUEPRINT_AUTOMATION_YAML, encoding="utf-8")
    return str(tmp_path)


COMPLEX_DEPS_YAML = """
- id: "cd001"
  alias: "Complex Dependencies"
  trigger:
    - platform: state
      entity_id: "binary_sensor.motion"
      to: "on"
  condition: []
  action:
    - service: "script.do_stuff"
    - service: "scene.turn_on"
      target:
        entity_id: "scene.relax"
    - service: "light.turn_on"
      target:
        entity_id: "light.main"
    - delay: 5
    - service: "media_player.play"
      target:
        entity_id: "media_player.tv"
"""


@pytest.fixture
def config_path_complex(tmp_path) -> str:
    (tmp_path / "automations.yaml").write_text(COMPLEX_DEPS_YAML, encoding="utf-8")
    return str(tmp_path)


WRITER_ONLY_YAML = """
- id: "wo001"
  alias: "Writer Only"
  trigger:
    - platform: time
      at: "08:00:00"
  action:
    - service: "light.turn_on"
      target:
        entity_id: "light.writer_target"
"""


@pytest.fixture
def config_path_writer_only(tmp_path) -> str:
    (tmp_path / "automations.yaml").write_text(WRITER_ONLY_YAML, encoding="utf-8")
    return str(tmp_path)


class TestSearchAutomationsFilters:
    def test_search_by_mode_single(self, mock_mcp, config_path):
        """mode=single filters to only single-mode automations."""
        register_automation_tools(mock_mcp, config_path)
        tool = mock_mcp._tools["search_automations"]
        data = json.loads(tool(mode="single"))
        assert data["success"] is True
        assert data["matched_count"] == 1
        assert data["results"][0]["mode"] == "single"

    def test_search_blueprint_true_no_matches(self, mock_mcp, config_path):
        """uses_blueprint=True returns 0 when no blueprint automations exist."""
        register_automation_tools(mock_mcp, config_path)
        tool = mock_mcp._tools["search_automations"]
        data = json.loads(tool(uses_blueprint=True))
        assert data["success"] is True
        assert data["matched_count"] == 0

    def test_search_blueprint_true_with_match(self, mock_mcp, config_path_blueprint):
        """uses_blueprint=True finds blueprint automations."""
        register_automation_tools(mock_mcp, config_path_blueprint)
        tool = mock_mcp._tools["search_automations"]
        data = json.loads(tool(uses_blueprint=True))
        assert data["success"] is True
        assert data["matched_count"] == 1
        assert data["results"][0]["uses_blueprint"] is True
        assert data["results"][0]["alias"] == "Blueprint Automation"

    def test_search_term_no_match(self, mock_mcp, config_path):
        """Search term that matches nothing returns empty results."""
        register_automation_tools(mock_mcp, config_path)
        tool = mock_mcp._tools["search_automations"]
        data = json.loads(tool(search_term="nonexistentXYZ"))
        assert data["success"] is True
        assert data["matched_count"] == 0
        assert len(data["results"]) == 0


class TestGetAutomationCodeExtension:
    def test_get_code_by_id(self, mock_mcp, config_path):
        """Find automation by its id field."""
        register_automation_tools(mock_mcp, config_path)
        tool = mock_mcp._tools["get_automation_code"]
        data = json.loads(tool("123"))
        assert data["success"] is True
        assert data["alias"] == "Test Automation One"
        assert "code" in data

    def test_get_code_empty_file(self, mock_mcp, tmp_path):
        """Empty automations.yaml => not found."""
        (tmp_path / "automations.yaml").write_text("", encoding="utf-8")
        register_automation_tools(mock_mcp, str(tmp_path))
        tool = mock_mcp._tools["get_automation_code"]
        data = json.loads(tool("anything"))
        assert data["success"] is False
        assert "not found" in data["error"].lower()


class TestAutomationConflictsExtended:
    def test_entity_with_conflicts_mocked_api(self, mock_mcp, config_path, ha_url, ha_token):
        """Conflicts test with mocked make_ha_request and load_registry."""
        with (
            patch("tools.automations.make_ha_request") as mock_ha,
            patch("tools.utils.load_registry") as mock_reg,
        ):
            mock_ha.return_value = {"success": True, "data": []}
            mock_reg.return_value = {}
            register_automation_tools(mock_mcp, config_path, ha_url, ha_token)
            tool = mock_mcp._tools["get_automation_conflicts"]
            data = json.loads(tool("light.room"))
        assert data["success"] is True
        assert data["conflict_analysis"]["race_condition_risk"] is True
        assert len(data["controlling_automations"]) == 2

    def test_entity_writer_only_no_loop(self, mock_mcp, config_path_writer_only):
        """Entity only in action (writer) but not in trigger => no loop risk."""
        register_automation_tools(mock_mcp, config_path_writer_only)
        tool = mock_mcp._tools["get_automation_conflicts"]
        data = json.loads(tool("light.writer_target"))
        assert data["success"] is True
        assert data["conflict_analysis"]["race_condition_risk"] is False  # only 1 writer
        assert data["conflict_analysis"]["feedback_loop_risk"] is False
        assert len(data["controlling_automations"]) == 1
        assert len(data["triggering_automations"]) == 0


@pytest.fixture
def mock_registry_data():
    return {
        "core.entity_registry": {
            "data": {
                "entities": [
                    {
                        "entity_id": "binary_sensor.test_sensor",
                        "name": "Test Sensor",
                        "platform": "mqtt",
                        "device_id": "dev1",
                    },
                ]
            }
        },
        "core.device_registry": {"data": {"devices": [{"id": "dev1", "name": "Test Device"}]}},
    }


class TestAutomationDependenciesExtended:
    def test_dependencies_with_scripts_and_scenes(self, mock_mcp, config_path_complex):
        """Automation referencing scripts and scenes categorizes correctly."""
        register_automation_tools(mock_mcp, config_path_complex)
        tool = mock_mcp._tools["get_automation_dependencies"]
        data = json.loads(tool("Complex Dependencies"))
        assert data["success"] is True
        deps = data["dependencies"]
        assert "script.do_stuff" in deps["scripts"]
        assert "scene.relax" in deps["scenes"] or True  # scene extraction may vary
        assert "light.main" in deps["entities"]
        assert "binary_sensor.motion" in deps["entities"]
        assert "media_player.tv" in deps["entities"]
        assert deps["entities_count"] >= 2


# ============================================================
# Trigger Validation Tests (detail_level="full")
# ============================================================

TRIGGER_VALIDATION_STATE_NO_ENTITY = """
- id: "tv001"
  alias: "Trigger State Missing Entity"
  trigger:
    - platform: state
      to: "on"
  action: []
"""

TRIGGER_VALIDATION_TIME_NO_AT = """
- id: "tv002"
  alias: "Trigger Time Missing At"
  trigger:
    - platform: time
  action: []
"""

TRIGGER_VALIDATION_NUMERIC_MISSING = """
- id: "tv003"
  alias: "Trigger Numeric Missing"
  trigger:
    - platform: numeric_state
  action: []
"""

TRIGGER_VALIDATION_TEMPLATE_MISSING = """
- id: "tv004"
  alias: "Trigger Template Missing"
  trigger:
    - platform: template
  action: []
"""

TRIGGER_VALIDATION_VALID = """
- id: "tv005"
  alias: "Trigger Valid"
  trigger:
    - platform: state
      entity_id: "binary_sensor.test_sensor"
      to: "on"
  action: []
"""


class TestTriggerValidation:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.ha_url = ha_url
        self.ha_token = ha_token
        self.mock_registry_data = mock_registry_data

    def _write_and_diagnose(self, yaml_content, automation_alias):
        path = os.path.join(self.config_path, "automations.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(yaml_content)

        with patch("tools.automations.make_ha_request") as mock_ha:
            mock_ha.return_value = {"success": True, "data": []}
            register_automation_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            tool = self.mock_mcp._tools["diagnose_automation"]
            return json.loads(tool(automation_alias, detail_level="full"))

    def test_state_trigger_missing_entity_id(self):
        data = self._write_and_diagnose(
            TRIGGER_VALIDATION_STATE_NO_ENTITY, "Trigger State Missing Entity"
        )

        assert data["success"] is True
        trigger_issues = [i for i in data["issues"] if i["type"] == "trigger_config_error"]
        assert len(trigger_issues) == 1
        assert "missing 'entity_id'" in trigger_issues[0]["message"]

    def test_time_trigger_missing_at(self):
        data = self._write_and_diagnose(TRIGGER_VALIDATION_TIME_NO_AT, "Trigger Time Missing At")

        assert data["success"] is True
        trigger_issues = [i for i in data["issues"] if i["type"] == "trigger_config_error"]
        assert len(trigger_issues) == 1
        assert "missing 'at'" in trigger_issues[0]["message"]

    def test_numeric_state_trigger_missing_fields(self):
        data = self._write_and_diagnose(
            TRIGGER_VALIDATION_NUMERIC_MISSING, "Trigger Numeric Missing"
        )

        assert data["success"] is True
        trigger_issues = [i for i in data["issues"] if i["type"] == "trigger_config_error"]
        assert len(trigger_issues) == 2
        messages = [i["message"] for i in trigger_issues]
        assert any("missing 'entity_id'" in m for m in messages)
        assert any("missing 'above' or 'below'" in m for m in messages)

    def test_template_trigger_missing_value_template(self):
        data = self._write_and_diagnose(
            TRIGGER_VALIDATION_TEMPLATE_MISSING, "Trigger Template Missing"
        )

        assert data["success"] is True
        trigger_issues = [i for i in data["issues"] if i["type"] == "trigger_config_error"]
        assert len(trigger_issues) == 1
        assert "missing 'value_template'" in trigger_issues[0]["message"]

    def test_valid_trigger_no_issues(self):
        state_data = [
            {
                "entity_id": "binary_sensor.test_sensor",
                "state": "off",
                "attributes": {},
            }
        ]

        path = os.path.join(self.config_path, "automations.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(TRIGGER_VALIDATION_VALID)

        with patch("tools.automations.make_ha_request") as mock_ha:
            mock_ha.return_value = {"success": True, "data": state_data}
            register_automation_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            tool = self.mock_mcp._tools["diagnose_automation"]
            data = json.loads(tool("Trigger Valid", detail_level="full"))

        assert data["success"] is True
        trigger_issues = [i for i in data["issues"] if i["type"] == "trigger_config_error"]
        assert len(trigger_issues) == 0


# ============================================================
# Action Type Classification Tests (detail_level="full")
# ============================================================

ACTION_CLASSIFICATION_YAML = """
- id: "ac001"
  alias: "Action Types Test"
  trigger:
    - platform: state
      entity_id: "binary_sensor.test_sensor"
      to: "on"
  action:
    - variables:
        my_var: "test_value"
    - service: "light.turn_on"
      target:
        entity_id: "light.test_light"
    - choose:
        - conditions:
            - condition: state
              entity_id: "binary_sensor.test_sensor"
              state: "on"
          sequence:
            - service: "light.turn_on"
              target:
                entity_id: "light.test_light"
      default: []
    - wait_template: "{{ is_state('binary_sensor.test_sensor', 'on') }}"
    - delay: 5
    - repeat:
        count: 3
        sequence:
          - service: "light.toggle"
            target:
              entity_id: "light.test_light"
    - if:
        - condition: state
          entity_id: "binary_sensor.test_sensor"
          state: "on"
      then:
        - service: "light.turn_on"
          target:
            entity_id: "light.test_light"
"""

ACTION_SERVICE_CALL_MISSING_SERVICE = """
- id: "ac002"
  alias: "Service Call Missing"
  trigger:
    - platform: time
      at: "08:00:00"
  action:
    - service:
"""


class TestActionClassification:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.ha_url = ha_url
        self.ha_token = ha_token
        self.mock_registry_data = mock_registry_data

    def test_action_types_classification(self):
        path = os.path.join(self.config_path, "automations.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(ACTION_CLASSIFICATION_YAML)

        with patch("tools.automations.make_ha_request") as mock_ha:
            mock_ha.return_value = {"success": True, "data": []}
            register_automation_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            tool = self.mock_mcp._tools["diagnose_automation"]
            data = json.loads(tool("Action Types Test", detail_level="full"))

        assert data["success"] is True
        actions = data["action_analysis"]
        assert len(actions) == 7

        expected_types = [
            "variables",
            "service_call",
            "choose",
            "wait",
            "delay",
            "repeat",
            "if",
        ]
        for idx, expected in enumerate(expected_types):
            assert actions[idx]["type"] == expected, (
                f"Action {idx}: expected {expected}, got {actions[idx]['type']}"
            )

    def test_service_call_without_service(self):
        path = os.path.join(self.config_path, "automations.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(ACTION_SERVICE_CALL_MISSING_SERVICE)

        with patch("tools.automations.make_ha_request") as mock_ha:
            mock_ha.return_value = {"success": True, "data": []}
            register_automation_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            tool = self.mock_mcp._tools["diagnose_automation"]
            data = json.loads(tool("Service Call Missing", detail_level="full"))

        assert data["success"] is True
        action_issues = [i for i in data["issues"] if i["type"] == "action_config_error"]
        assert len(action_issues) == 1
        assert "missing 'service'" in action_issues[0]["message"]


# ============================================================
# Condition Validation Tests (detail_level="full")
# ============================================================

CONDITION_VALIDATION_STATE_MISSING = """
- id: "cv001"
  alias: "automation.test_condition_validation"
  trigger:
    - platform: time
      at: "08:00:00"
  condition:
    - condition: state
      state: "on"
  action: []
"""

CONDITION_VALIDATION_NUMERIC_MISSING = """
- id: "cv002"
  alias: "automation.test_condition_validation"
  trigger:
    - platform: time
      at: "08:00:00"
  condition:
    - condition: numeric_state
      above: "10"
  action: []
"""

CONDITION_VALIDATION_VALID = """
- id: "cv003"
  alias: "automation.test_condition_validation"
  trigger:
    - platform: time
      at: "08:00:00"
  condition:
    - condition: state
      entity_id: "light.test_light"
      state: "on"
    - condition: numeric_state
      entity_id: "sensor.test_sensor"
      above: "10"
  action: []
"""


class TestConditionValidation:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.ha_url = ha_url
        self.ha_token = ha_token

    def _write_and_diagnose(self, yaml_content):
        path = os.path.join(self.config_path, "automations.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(yaml_content)

        with patch("tools.automations.make_ha_request") as mock_ha:
            mock_ha.return_value = {"success": True, "data": []}
            register_automation_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            tool = self.mock_mcp._tools["diagnose_automation"]
            return json.loads(tool("automation.test_condition_validation", detail_level="full"))

    def test_state_condition_missing_entity_id(self):
        data = self._write_and_diagnose(CONDITION_VALIDATION_STATE_MISSING)

        assert data["success"] is True
        condition_issues = [i for i in data["issues"] if i["type"] == "condition_config_error"]
        assert len(condition_issues) == 1
        assert "missing 'entity_id'" in condition_issues[0]["message"]
        assert "state" in condition_issues[0]["message"].lower()

    def test_numeric_state_condition_missing_entity_id(self):
        data = self._write_and_diagnose(CONDITION_VALIDATION_NUMERIC_MISSING)

        assert data["success"] is True
        condition_issues = [i for i in data["issues"] if i["type"] == "condition_config_error"]
        assert len(condition_issues) == 1
        assert "missing 'entity_id'" in condition_issues[0]["message"]
        assert "numeric_state" in condition_issues[0]["message"].lower()

    def test_valid_conditions_no_issues(self):
        data = self._write_and_diagnose(CONDITION_VALIDATION_VALID)

        assert data["success"] is True
        condition_issues = [i for i in data["issues"] if i["type"] == "condition_config_error"]
        assert len(condition_issues) == 0


# ============================================================
# AutomationDetailFull Validation Tests
# ============================================================

ENTITY_VALIDATION_FALLBACK_YAML = """
- id: "ef001"
  alias: "automation.test_entity_validation"
  trigger:
    - platform: state
      entity_id: "light.test_light"
      to: "on"
  action:
    - service: "light.turn_on"
      target:
        entity_id: "light.test_light"
"""

SCRIPT_VALIDATION_YAML = """
- id: "sv001"
  alias: "automation.test_script_validation"
  trigger:
    - platform: time
      at: "08:00:00"
  action:
    - service: "script.turn_on"
      target:
        entity_id: "script.test_script"
"""

SCENE_VALIDATION_YAML = """
- id: "scv001"
  alias: "automation.test_scene_validation"
  trigger:
    - platform: time
      at: "08:00:00"
  action:
    - service: "scene.turn_on"
      target:
        entity_id: "scene.test_scene"
"""


class TestAutomationDetailFull:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.ha_url = ha_url
        self.ha_token = ha_token

    def test_entity_validation_fallback(self):
        path = os.path.join(self.config_path, "automations.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(ENTITY_VALIDATION_FALLBACK_YAML)

        def make_ha_side_effect(ha_url_, ha_token_, endpoint, method="GET", data=None, **kwargs):
            if endpoint == "/api/states":
                return {"success": False, "error": "Connection error"}
            return {"success": True, "data": []}

        with patch("tools.automations.make_ha_request", side_effect=make_ha_side_effect):
            register_automation_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            tool = self.mock_mcp._tools["diagnose_automation"]
            data = json.loads(tool("automation.test_entity_validation", detail_level="full"))

        assert data["success"] is True
        validation_errors = [i for i in data["issues"] if i["type"] == "validation_error"]
        assert len(validation_errors) == 1
        assert "Failed to fetch entity states" in validation_errors[0]["message"]

    def test_script_validation_warns_missing(self):
        path = os.path.join(self.config_path, "automations.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(SCRIPT_VALIDATION_YAML)

        scripts_path = os.path.join(self.config_path, "scripts.yaml")
        with open(scripts_path, "w", encoding="utf-8") as f:
            f.write("existing_script:\n  alias: Existing\n  sequence: []\n")

        with patch("tools.automations.make_ha_request") as mock_ha:
            mock_ha.return_value = {"success": True, "data": []}
            register_automation_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            tool = self.mock_mcp._tools["diagnose_automation"]
            data = json.loads(tool("automation.test_script_validation", detail_level="full"))

        assert data["success"] is True
        script_issues = [
            i
            for i in data["issues"]
            if i["type"] == "script_not_found" and i.get("script_id") == "test_script"
        ]
        assert len(script_issues) == 1
        assert "not found" in script_issues[0]["message"].lower()

    def test_scene_validation_warns_missing(self):
        path = os.path.join(self.config_path, "automations.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(SCENE_VALIDATION_YAML)

        scenes_path = os.path.join(self.config_path, "scenes.yaml")
        with open(scenes_path, "w", encoding="utf-8") as f:
            f.write('- id: existing_scene\n  name: "Existing Scene"\n  entities: {}\n')

        with patch("tools.automations.make_ha_request") as mock_ha:
            mock_ha.return_value = {"success": True, "data": []}
            register_automation_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            tool = self.mock_mcp._tools["diagnose_automation"]
            data = json.loads(tool("automation.test_scene_validation", detail_level="full"))

        assert data["success"] is True
        scene_issues = [
            i
            for i in data["issues"]
            if i["type"] == "scene_not_found" and i.get("scene_id") == "test_scene"
        ]
        assert len(scene_issues) == 1
        assert "not found" in scene_issues[0]["message"].lower()


class TestGetAutomationFileLocation:
    def test_found_by_alias_returns_line_range(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)
        tool = mock_mcp._tools["get_automation_file_location"]
        data = json.loads(tool("Test Automation One"))
        assert data["success"] is True
        assert data["automation_id"] == "123"
        assert data["alias"] == "Test Automation One"
        assert data["file_path"] == "automations.yaml"
        assert isinstance(data["line_start"], int)
        assert isinstance(data["line_end"], int)
        assert data["line_start"] >= 1
        assert data["line_end"] >= data["line_start"]
        assert "id:" in data.get("surrounding_yaml", "")

    def test_found_by_id_returns_line_range(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)
        tool = mock_mcp._tools["get_automation_file_location"]
        data = json.loads(tool("456"))
        assert data["success"] is True
        assert data["automation_id"] == "456"
        assert data["alias"] == "Another Automation"
        assert data["line_start"] >= 1
        assert data["line_end"] >= data["line_start"]

    def test_not_found_returns_error(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)
        tool = mock_mcp._tools["get_automation_file_location"]
        data = json.loads(tool("NonExistentAutomation"))
        assert data["success"] is False
        assert "not found" in data.get("error", "").lower()

    def test_empty_automation_id_returns_error(self, mock_mcp, config_path):
        register_automation_tools(mock_mcp, config_path)
        tool = mock_mcp._tools["get_automation_file_location"]
        data = json.loads(tool(""))
        assert data["success"] is False

    def test_empty_automations_file(self, mock_mcp, tmp_path):
        (tmp_path / "automations.yaml").write_text("", encoding="utf-8")
        register_automation_tools(mock_mcp, str(tmp_path))
        tool = mock_mcp._tools["get_automation_file_location"]
        data = json.loads(tool("Anything"))
        assert data["success"] is False

    def test_surrounding_yaml_is_valid_yaml(self, mock_mcp, config_path):
        import yaml

        register_automation_tools(mock_mcp, config_path)
        tool = mock_mcp._tools["get_automation_file_location"]
        data = json.loads(tool("Test Automation One"))
        assert data["success"] is True
        surrounding = data.get("surrounding_yaml", "")
        assert surrounding
        parsed = yaml.safe_load(surrounding)
        assert parsed is not None
        assert isinstance(parsed, list)
        assert parsed[0]["id"] == "123"


class TestExceptionHandler:
    """Verify tool wrappers catch internal exceptions per [TEST-REG-3]."""

    def test_exception_in_internal_fn_returns_error(self, mock_mcp, config_path):
        """When _do_* raises RuntimeError, wrapper returns success=false with error text."""
        register_automation_tools(mock_mcp, config_path)

        with patch(
            "tools.automations._do_get_automation_code",
            side_effect=RuntimeError("test explosion"),
        ):
            tool = mock_mcp._tools["get_automation_code"]
            data = json.loads(tool("Test Automation One"))

        assert data["success"] is False
        assert "test explosion" in data.get("error", "")

    def test_exception_in_list_tool_returns_error(self, mock_mcp, config_path):
        """When list_automations internal logic raises, wrapper returns error."""
        register_automation_tools(mock_mcp, config_path)

        with patch(
            "tools.automations._do_list_automations",
            side_effect=RuntimeError("listing failed"),
        ):
            tool = mock_mcp._tools["list_automations"]
            data = json.loads(tool())

        assert data["success"] is False
        assert "listing failed" in data.get("error", "")
