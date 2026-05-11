"""
Tests for tools/dev_tools.py
"""

import json
from unittest.mock import patch

import pytest

from tools.dev_tools import register_dev_tools


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
    """Sample states response from HA API."""
    return [
        {
            "entity_id": "sensor.temp",
            "state": "20.5",
            "attributes": {"unit_of_measurement": "°C"},
        },
        {"entity_id": "light.room", "state": "on", "attributes": {"brightness": 255}},
        {"entity_id": "switch.kitchen", "state": "off", "attributes": {}},
    ]


@pytest.fixture
def sample_services():
    """Sample services response from HA API."""
    return [
        {
            "domain": "light",
            "services": {
                "turn_on": {
                    "description": "Turn on light",
                    "fields": {
                        "brightness": {
                            "description": "Brightness value",
                            "example": 255,
                        }
                    },
                },
                "turn_off": {"description": "Turn off light", "fields": {}},
            },
        }
    ]


class TestTemplateTesting:
    """Tests for template testing tools."""

    def test_test_template(self, mock_mcp, ha_url, ha_token, config_path):
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": "20.5"}

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["test_template"]
            result = tool("{{ states('sensor.temp') }}")
            data = json.loads(result)

        assert data["success"] is True
        assert data["result"] == "20.5"
        assert "render_time" in data

    def test_test_template_error(self, mock_mcp, ha_url, ha_token, config_path):
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": False, "error": "TemplateSyntaxError"}

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["test_template"]
            result = tool("{{ invalid syntax }}")
            data = json.loads(result)

        assert data["success"] is False
        assert "TemplateSyntaxError" in data["error"]

    def test_test_templates_batch(self, mock_mcp, ha_url, ha_token, config_path):
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            # First call succeeds, second fails
            mock_req.side_effect = [
                {"success": True, "data": "OK"},
                {"success": False, "error": "Error"},
            ]

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["test_templates_batch"]
            result = tool('["{{ 1+1 }}", "{{ invalid }}"]')
            data = json.loads(result)

        assert data["success"] is True
        assert data["total_templates"] == 2
        assert data["successful"] == 1
        assert data["failed"] == 1
        assert len(data["results"]) == 2

    def test_test_templates_batch_dict_format(self, mock_mcp, ha_url, ha_token, config_path):
        """Dict input should use keys as template names."""
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": "42"}
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            data = json.loads(mock_mcp._tools["test_templates_batch"]('{"my_tpl": "{{ 21*2 }}"}'))

        assert data["success"] is True
        assert data["total_templates"] == 1
        assert data["results"][0]["name"] == "my_tpl"

    def test_test_templates_batch_invalid_json(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(mock_mcp._tools["test_templates_batch"]("not json"))
        assert data["success"] is False
        assert "Invalid JSON" in data["error"]

    def test_get_template_performance(self, mock_mcp, ha_url, ha_token, config_path):
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": "OK"}

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["get_template_performance"]
            result = tool("{{ states | list }}", iterations=2)
            data = json.loads(result)

        assert data["success"] is True
        assert data["benchmark"]["iterations"] == 2
        assert "avg_ms" in data["benchmark"]
        # Complexity score should be high due to 'states' usage
        assert data["analysis"]["complexity_score"] >= 10

    def test_get_template_performance_error(self, mock_mcp, ha_url, ha_token, config_path):
        """Error during benchmark stops and returns error."""
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": False, "error": "Render failed"}

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            data = json.loads(
                mock_mcp._tools["get_template_performance"]("{{ fail }}", iterations=5)
            )

        assert data["success"] is False
        assert "Render failed" in data["error"]

    def test_get_template_performance_complex_expand(self, mock_mcp, ha_url, ha_token, config_path):
        """'expand' keyword adds +5 complexity."""
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": "OK"}
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            data = json.loads(
                mock_mcp._tools["get_template_performance"](
                    "{{ expand('group.lights') }}", iterations=2
                )
            )

        assert data["success"] is True
        assert data["analysis"]["complexity_score"] >= 5

    def test_get_template_performance_complex_states_call(
        self, mock_mcp, ha_url, ha_token, config_path
    ):
        """states() call without states.attr adds +10 complexity."""
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": "OK"}
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            data = json.loads(
                mock_mcp._tools["get_template_performance"](
                    "{{ states('sensor.temp') | float }}", iterations=2
                )
            )

        assert data["success"] is True
        assert data["analysis"]["complexity_score"] >= 10

    def test_get_template_performance_long_template(self, mock_mcp, ha_url, ha_token, config_path):
        """Template > 500 chars adds +3 complexity."""
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": "OK"}
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            long_template = "A" * 501
            data = json.loads(
                mock_mcp._tools["get_template_performance"](long_template, iterations=2)
            )

        assert data["success"] is True
        assert data["analysis"]["complexity_score"] >= 3


class TestValidation:
    """Tests for validation tools."""

    def test_validate_automation_trigger_valid(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

        tool = mock_mcp._tools["validate_automation_trigger"]
        valid_yaml = """
        - platform: state
          entity_id: sensor.temp
          to: "25"
        """
        result = tool(valid_yaml)
        data = json.loads(result)

        assert data["success"] is True
        assert data["valid"] is True
        assert not data.get("issues")

    def test_validate_automation_trigger_invalid(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

        tool = mock_mcp._tools["validate_automation_trigger"]
        # Missing entity_id for state platform
        invalid_yaml = """
        - platform: state
          to: "25"
        """
        result = tool(invalid_yaml)
        data = json.loads(result)

        assert data["success"] is False
        assert data["valid"] is False
        assert any("requires 'entity_id'" in issue for issue in data["issues"])

    def test_validate_automation_trigger_yaml_error(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(mock_mcp._tools["validate_automation_trigger"]("invalid: [unclosed"))
        assert data["success"] is False
        assert "YAML" in data.get("error", "")

    def test_validate_time_trigger_valid(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("""
        - platform: time
          at: "07:00:00"
        """)
        )
        assert data["success"] is True
        assert data["valid"] is True

    def test_validate_time_trigger_missing_at(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(mock_mcp._tools["validate_automation_trigger"]("- platform: time"))
        assert data["success"] is False
        assert any("'at'" in i for i in data["issues"])

    def test_validate_numeric_state_trigger(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("""
        - platform: numeric_state
          entity_id: sensor.temp
          above: 25
        """)
        )
        assert data["success"] is True
        assert data["valid"] is True

    def test_validate_numeric_state_missing_fields(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("- platform: numeric_state")
        )
        assert data["success"] is False
        assert any("entity_id" in i for i in data["issues"])
        assert any("above" in i.lower() or "below" in i.lower() for i in data["issues"])

    def test_validate_template_trigger_valid(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("""
        - platform: template
          value_template: "{{ now().hour > 7 }}"
        """)
        )
        assert data["success"] is True
        assert data["valid"] is True

    def test_validate_template_trigger_no_jinja(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("""
        - platform: template
          value_template: "no jinja here"
        """)
        )
        assert data["success"] is True
        assert any("Jinja2" in w for w in (data.get("warnings") or []))

    def test_validate_event_trigger(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("""
        - platform: event
          event_type: my_event
        """)
        )
        assert data["success"] is True

    def test_validate_event_trigger_missing_type(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(mock_mcp._tools["validate_automation_trigger"]("- platform: event"))
        assert data["success"] is False
        assert any("event_type" in i for i in data["issues"])

    def test_validate_homeassistant_trigger(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("""
        - platform: homeassistant
          event: start
        """)
        )
        assert data["success"] is True
        assert data["valid"] is True

    def test_validate_homeassistant_bad_event(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("""
        - platform: homeassistant
          event: reboot
        """)
        )
        assert data["success"] is True
        assert any(
            "start" in w.lower() or "shutdown" in w.lower() for w in (data.get("warnings") or [])
        )

    def test_validate_time_pattern_trigger(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("""
        - platform: time_pattern
          minutes: "/5"
        """)
        )
        assert data["success"] is True
        assert data["valid"] is True

    def test_validate_time_pattern_missing(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("- platform: time_pattern")
        )
        assert data["success"] is False
        assert any(
            "hours" in i.lower() or "minutes" in i.lower() or "seconds" in i.lower()
            for i in data["issues"]
        )

    def test_validate_webhook_trigger_missing_id(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(mock_mcp._tools["validate_automation_trigger"]("- platform: webhook"))
        assert data["success"] is False
        assert any("webhook_id" in i for i in data["issues"])

    def test_validate_zone_trigger(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("""
        - platform: zone
          entity_id: person.test_person
          zone: zone.home
          event: enter
        """)
        )
        assert data["success"] is True
        assert data["valid"] is True

    def test_validate_zone_missing_event(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("""
        - platform: zone
          entity_id: person.test_person
          zone: zone.home
        """)
        )
        assert data["success"] is True
        assert any("event" in w.lower() for w in (data.get("warnings") or []))

    def test_validate_sun_trigger_bad_event(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("""
        - platform: sun
          event: midnight
        """)
        )
        assert data["success"] is True
        assert any(
            "sunrise" in w.lower() or "sunset" in w.lower() for w in (data.get("warnings") or [])
        )

    def test_validate_mqtt_trigger_missing_topic(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(mock_mcp._tools["validate_automation_trigger"]("- platform: mqtt"))
        assert data["success"] is False
        assert any("topic" in i for i in data["issues"])

    def test_validate_device_trigger_missing_id(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(mock_mcp._tools["validate_automation_trigger"]("- platform: device"))
        assert data["success"] is False
        assert any("device_id" in i for i in data["issues"])

    def test_validate_unknown_platform(self, mock_mcp, ha_url, ha_token, config_path):
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("""
        - platform: imaginary_thing
        """)
        )
        assert data["success"] is True
        assert data["valid"] is True
        assert any("unknown platform" in w.lower() for w in (data.get("warnings") or []))

    def test_validate_dict_not_list(self, mock_mcp, ha_url, ha_token, config_path):
        """Single dict trigger should be wrapped into list."""
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"]("""
        platform: state
        entity_id: sensor.temp
        to: "25"
        """)
        )
        assert data["success"] is True
        assert data["valid"] is True

    def test_validate_non_dict_triggers(self, mock_mcp, ha_url, ha_token, config_path):
        """Non-dict item in trigger list → issue."""
        register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
        data = json.loads(
            mock_mcp._tools["validate_automation_trigger"](
                "- just_a_string\n- 42\n- platform: time\n  at: '07:00'"
            )
        )
        assert data["success"] is False
        assert any("dictionary" in i.lower() for i in data["issues"])

    def test_test_condition(self, mock_mcp, ha_url, ha_token, config_path):
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": True}

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["test_condition"]
            result = tool("{{ 1 == 1 }}")
            data = json.loads(result)

        assert data["success"] is True
        assert data["result"] is True
        assert data["evaluates_to"] is True

    def test_test_condition_error(self, mock_mcp, ha_url, ha_token, config_path):
        """Template render error in test_condition."""
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": False, "error": "TemplateSyntaxError"}

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            data = json.loads(mock_mcp._tools["test_condition"]("{{ invalid }}"))

        assert data["success"] is False
        assert "TemplateSyntaxError" in data["error"]

    def test_test_condition_with_context(self, mock_mcp, ha_url, ha_token, config_path):
        """test_condition with context variable parameter."""
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": True}

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            data = json.loads(
                mock_mcp._tools["test_condition"](
                    "{{ trigger.to_state.state == 'on' }}",
                    "trigger.to_state.state = 'on'",
                )
            )

        assert data["success"] is True
        assert data["result"] is True
        assert data["context"] == "trigger.to_state.state = 'on'"

    def test_test_condition_string_true(self, mock_mcp, ha_url, ha_token, config_path):
        """String 'True' parsed as boolean True."""
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": "True"}

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            data = json.loads(mock_mcp._tools["test_condition"]("{{ 1 == 1 }}"))

        assert data["success"] is True
        assert data["evaluates_to"] is True

    def test_test_condition_string_off(self, mock_mcp, ha_url, ha_token, config_path):
        """String 'off' parsed as boolean False."""
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": "off"}

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            data = json.loads(mock_mcp._tools["test_condition"]("{{ 'off' }}"))

        assert data["success"] is True
        assert data["evaluates_to"] is False


class TestEntityChecking:
    """Tests for entity checking tools."""

    def test_check_entity_exists(self, mock_mcp, ha_url, ha_token, config_path, sample_states):
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states[0]}

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["check_entity_exists"]
            result = tool("sensor.temp")
            data = json.loads(result)

        assert data["success"] is True
        assert data["exists"] is True
        assert data["current_state"] == "20.5"

    def test_check_entity_not_found(self, mock_mcp, ha_url, ha_token, config_path):
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": False, "error": "Entity not found"}
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
            data = json.loads(mock_mcp._tools["check_entity_exists"]("sensor.ghost"))

        assert data["exists"] is False

    def test_check_entities_batch(self, mock_mcp, ha_url, ha_token, config_path, sample_states):
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_states}

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["check_entities_batch"]
            # sensor.temp exists, sensor.missing does not
            result = tool("sensor.temp, sensor.missing")
            data = json.loads(result)

        assert data["success"] is True
        assert data["summary"]["exists"] == "1/2"
        assert data["summary"]["missing"] == 1

        results = {r["entity_id"]: r["status"] for r in data["results"]}
        assert "OK" in results["sensor.temp"]
        assert "NOT FOUND" in results["sensor.missing"]


class TestServiceCall:
    """Tests for service call validation tool."""

    def test_test_service_call_valid(
        self, mock_mcp, ha_url, ha_token, config_path, sample_services, sample_states
    ):
        # Mock requests side effect
        def make_ha_request_side_effect(
            ha_url, ha_token, endpoint, method="GET", data=None, **kwargs
        ):
            if endpoint == "/api/services":
                return {"success": True, "data": sample_services}
            if endpoint.startswith("/api/states/"):
                return {"success": True, "data": sample_states[1]}  # light.room
            return {"success": False, "error": "Unknown"}

        with patch("tools.dev_tools.make_ha_request", side_effect=make_ha_request_side_effect):
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["test_service_call"]
            result = tool("light", "turn_on", "light.room", '{"brightness": 100}')
            data = json.loads(result)

        assert data["success"] is True
        assert data["valid"] is True
        assert "DRY RUN" in data["note"]

    def test_test_service_call_invalid_service(
        self, mock_mcp, ha_url, ha_token, config_path, sample_services
    ):
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_services}

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["test_service_call"]
            result = tool("light", "explode")
            data = json.loads(result)

        assert data["success"] is False
        assert "not found in domain" in data["error"]

    def test_test_service_call_unknown_fields(
        self, mock_mcp, ha_url, ha_token, config_path, sample_services, sample_states
    ):
        # Mock requests side effect
        def make_ha_request_side_effect(
            ha_url, ha_token, endpoint, method="GET", data=None, **kwargs
        ):
            if endpoint == "/api/services":
                return {"success": True, "data": sample_services}
            if endpoint.startswith("/api/states/"):
                return {"success": True, "data": sample_states[1]}
            return {"success": False}

        with patch("tools.dev_tools.make_ha_request", side_effect=make_ha_request_side_effect):
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            tool = mock_mcp._tools["test_service_call"]
            # 'color' field doesn't exist in our sample service definition
            result = tool("light", "turn_on", "light.room", '{"color": "red"}')
            data = json.loads(result)

        assert data["success"] is True
        # Should warn about unknown field
        assert "Unknown fields" in data["data_validation"]["warnings"]

    def test_test_service_call_unknown_domain(
        self, mock_mcp, ha_url, ha_token, config_path, sample_services
    ):
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": sample_services}

            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)

            data = json.loads(mock_mcp._tools["test_service_call"]("nope", "do_thing"))

        assert data["success"] is False
        assert "not found" in data["error"].lower()
        assert "available_domains" in data


class TestDiagnoseEntity:
    """Tests for diagnose_entity()."""

    def test_diagnose_entity_success(self, mock_mcp, ha_url, ha_token, tmp_path):
        """Basic successful entity diagnosis with registry data."""
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.entity_registry").write_text(
            json.dumps(
                {
                    "data": {
                        "entities": [
                            {
                                "entity_id": "sensor.temp",
                                "platform": "mqtt",
                                "device_id": "dev1",
                                "area_id": None,
                                "disabled_by": None,
                                "hidden_by": None,
                                "unique_id": "u1",
                                "config_entry_id": "ce1",
                            }
                        ]
                    }
                }
            )
        )
        (storage / "core.device_registry").write_text(
            json.dumps(
                {
                    "data": {
                        "devices": [
                            {
                                "id": "dev1",
                                "name": "TempDev",
                                "manufacturer": "Acme",
                                "model": "T1",
                                "sw_version": "1.0",
                                "hw_version": "2.0",
                                "disabled_by": None,
                            }
                        ]
                    }
                }
            )
        )
        (storage / "core.area_registry").write_text(json.dumps({"data": {"areas": []}}))

        state_obj = {
            "entity_id": "sensor.temp",
            "state": "22.5",
            "attributes": {
                "friendly_name": "Temp",
                "device_class": "temperature",
                "unit_of_measurement": "°C",
            },
            "last_changed": "2026-01-01T00:00:00+00:00",
            "last_updated": "2026-01-01T00:00:00+00:00",
        }

        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": state_obj}
            register_dev_tools(mock_mcp, ha_url, ha_token, str(tmp_path))
            data = json.loads(mock_mcp._tools["diagnose_entity"]("sensor.temp"))

        assert data["success"] is True
        assert data["current_state"]["state"] == "22.5"
        assert data["entity_info"]["platform"] == "mqtt"
        assert data["device_info"]["manufacturer"] == "Acme"

    def test_diagnose_entity_not_found(self, mock_mcp, ha_url, ha_token, config_path):
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": False, "error": "not found"}
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
            data = json.loads(mock_mcp._tools["diagnose_entity"]("sensor.missing"))

        assert data["success"] is False
        assert any("not found" in i["message"].lower() for i in data["issues"])

    def test_diagnose_entity_unavailable(self, mock_mcp, ha_url, ha_token, config_path):
        state_obj = {
            "entity_id": "sensor.dead",
            "state": "unavailable",
            "attributes": {},
            "last_changed": "2026-01-01T00:00:00+00:00",
            "last_updated": "2026-01-01T00:00:00+00:00",
        }
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": state_obj}
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
            data = json.loads(mock_mcp._tools["diagnose_entity"]("sensor.dead"))

        assert data["success"] is True
        assert any("UNAVAILABLE" in i["message"] for i in data["issues"])

    def test_diagnose_entity_state_unknown(self, mock_mcp, ha_url, ha_token, config_path):
        """Entity state 'unknown' gets warning."""
        state_obj = {
            "entity_id": "sensor.unknown",
            "state": "unknown",
            "attributes": {},
            "last_changed": "2026-01-01T00:00:00+00:00",
            "last_updated": "2026-01-01T00:00:00+00:00",
        }
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": state_obj}
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
            data = json.loads(mock_mcp._tools["diagnose_entity"]("sensor.unknown"))

        assert data["success"] is True
        assert any("UNKNOWN" in i["message"] for i in data["issues"])

    def test_diagnose_entity_disabled(self, mock_mcp, ha_url, ha_token, tmp_path):
        """Disabled entity → warning issue."""
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.entity_registry").write_text(
            json.dumps(
                {
                    "data": {
                        "entities": [
                            {
                                "entity_id": "sensor.disabled",
                                "platform": "mqtt",
                                "device_id": None,
                                "area_id": None,
                                "disabled_by": "user",
                                "hidden_by": None,
                                "unique_id": "u1",
                                "config_entry_id": None,
                            }
                        ]
                    }
                }
            )
        )
        (storage / "core.device_registry").write_text(json.dumps({"data": {"devices": []}}))
        (storage / "core.area_registry").write_text(json.dumps({"data": {"areas": []}}))

        state_obj = {
            "entity_id": "sensor.disabled",
            "state": "22",
            "attributes": {},
            "last_changed": "2026-01-01T00:00:00+00:00",
            "last_updated": "2026-01-01T00:00:00+00:00",
        }
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": state_obj}
            register_dev_tools(mock_mcp, ha_url, ha_token, str(tmp_path))
            data = json.loads(mock_mcp._tools["diagnose_entity"]("sensor.disabled"))

        assert data["success"] is True
        assert any("disabled" in i["message"].lower() for i in data["issues"])
        assert data["entity_info"]["disabled"] is True

    def test_diagnose_entity_hidden(self, mock_mcp, ha_url, ha_token, tmp_path):
        """Hidden entity reported in entity_info."""
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.entity_registry").write_text(
            json.dumps(
                {
                    "data": {
                        "entities": [
                            {
                                "entity_id": "sensor.hidden",
                                "platform": "mqtt",
                                "device_id": None,
                                "area_id": None,
                                "disabled_by": None,
                                "hidden_by": "user",
                                "unique_id": "u1",
                                "config_entry_id": None,
                            }
                        ]
                    }
                }
            )
        )
        (storage / "core.device_registry").write_text(json.dumps({"data": {"devices": []}}))
        (storage / "core.area_registry").write_text(json.dumps({"data": {"areas": []}}))

        state_obj = {
            "entity_id": "sensor.hidden",
            "state": "22",
            "attributes": {},
            "last_changed": "2026-01-01T00:00:00+00:00",
            "last_updated": "2026-01-01T00:00:00+00:00",
        }
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": state_obj}
            register_dev_tools(mock_mcp, ha_url, ha_token, str(tmp_path))
            data = json.loads(mock_mcp._tools["diagnose_entity"]("sensor.hidden"))

        assert data["success"] is True
        assert data["entity_info"]["hidden"] is True
        assert data["entity_info"]["hidden_by"] == "user"

    def test_diagnose_entity_stale(self, mock_mcp, ha_url, ha_token, config_path):
        """Entity not changed in >24h → stale warning."""
        state_obj = {
            "entity_id": "sensor.old",
            "state": "20",
            "attributes": {},
            "last_changed": "2023-01-01T00:00:00+00:00",
            "last_updated": "2023-01-01T00:00:00+00:00",
        }
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": state_obj}
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
            data = json.loads(mock_mcp._tools["diagnose_entity"]("sensor.old"))

        assert data["success"] is True
        assert any("hasn't changed" in i["message"].lower() for i in data["issues"])

    def test_diagnose_entity_with_area(self, mock_mcp, ha_url, ha_token, tmp_path):
        """Entity with area → area_info populated."""
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.entity_registry").write_text(
            json.dumps(
                {
                    "data": {
                        "entities": [
                            {
                                "entity_id": "sensor.kitchen_temp",
                                "platform": "mqtt",
                                "device_id": None,
                                "area_id": "area_kitchen",
                                "disabled_by": None,
                                "hidden_by": None,
                                "unique_id": "u1",
                                "config_entry_id": None,
                            }
                        ]
                    }
                }
            )
        )
        (storage / "core.device_registry").write_text(json.dumps({"data": {"devices": []}}))
        (storage / "core.area_registry").write_text(
            json.dumps(
                {
                    "data": {
                        "areas": [
                            {
                                "id": "area_kitchen",
                                "name": "Kitchen",
                                "aliases": ["cooking"],
                            }
                        ]
                    }
                }
            )
        )

        state_obj = {
            "entity_id": "sensor.kitchen_temp",
            "state": "24",
            "attributes": {},
            "last_changed": "2026-01-01T00:00:00+00:00",
            "last_updated": "2026-01-01T00:00:00+00:00",
        }
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": state_obj}
            register_dev_tools(mock_mcp, ha_url, ha_token, str(tmp_path))
            data = json.loads(mock_mcp._tools["diagnose_entity"]("sensor.kitchen_temp"))

        assert data["success"] is True
        assert data["area_info"]["name"] == "Kitchen"
        assert "cooking" in data["area_info"]["aliases"]


class TestDiagnoseTemplate:
    """Tests for diagnose_template()."""

    def test_diagnose_template_found_in_config_entries(self, mock_mcp, ha_url, ha_token, tmp_path):
        """Template code found in config_entries → full diagnosis."""
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.config_entries").write_text(
            json.dumps(
                {
                    "data": {
                        "entries": [
                            {
                                "domain": "template",
                                "title": "My Template",
                                "options": {
                                    "template_type": "sensor",
                                    "state": "{{ states('sensor.temp') }}",
                                    "device_class": "temperature",
                                    "unit_of_measurement": "°C",
                                },
                            }
                        ]
                    }
                }
            )
        )

        def ha_side_effect(url, token, endpoint, method="GET", data=None, **kwargs):
            if endpoint.startswith("/api/states/sensor.temp"):
                return {"success": True, "data": {"state": "22", "attributes": {}}}
            if endpoint == "/api/template":
                return {"success": True, "data": "22"}
            return {"success": False, "error": "not found"}

        with patch("tools.dev_tools.make_ha_request", side_effect=ha_side_effect):
            register_dev_tools(mock_mcp, ha_url, ha_token, str(tmp_path))
            data = json.loads(mock_mcp._tools["diagnose_template"]("sensor.my_template"))

        assert data["success"] is True
        assert data["syntax_validation"] == "ok"
        assert data["test_render"] == "22"
        assert "sensor.temp" in data["referenced_entities"]

    def test_diagnose_template_not_found(self, mock_mcp, ha_url, ha_token, config_path):
        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": False, "error": "not found"}
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
            data = json.loads(mock_mcp._tools["diagnose_template"]("sensor.ghost"))

        assert data["success"] is False
        assert any("not found" in i["message"].lower() for i in data["issues"])

    def test_diagnose_template_fuzzy_match(self, mock_mcp, ha_url, ha_token, tmp_path):
        """Template found via Strategy B: fuzzy name match."""
        storage = tmp_path / ".storage"
        storage.mkdir()
        # Entity NOT in registry → Strategy A fails → falls back to B
        (storage / "core.entity_registry").write_text(json.dumps({"data": {"entities": []}}))
        (storage / "core.config_entries").write_text(
            json.dumps(
                {
                    "data": {
                        "entries": [
                            {
                                "domain": "template",
                                "title": "My Template",
                                "options": {
                                    "template_type": "sensor",
                                    "state": "{{ states('sensor.temp') }}",
                                },
                            }
                        ]
                    }
                }
            )
        )

        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.side_effect = [
                {"success": True, "data": {"state": "22", "attributes": {}}},
                {"success": True, "data": "22"},
            ]
            register_dev_tools(mock_mcp, ha_url, ha_token, str(tmp_path))
            data = json.loads(mock_mcp._tools["diagnose_template"]("sensor.my_template"))

        assert data["success"] is True
        assert data["syntax_validation"] == "ok"
        assert data["test_render"] == "22"

    def test_diagnose_template_render_error(self, mock_mcp, ha_url, ha_token, tmp_path):
        """Template rendering fails → syntax_validation == 'error'."""
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.entity_registry").write_text(json.dumps({"data": {"entities": []}}))
        (storage / "core.config_entries").write_text(
            json.dumps(
                {
                    "data": {
                        "entries": [
                            {
                                "domain": "template",
                                "title": "Bad Template",
                                "options": {
                                    "template_type": "sensor",
                                    "state": "{{ bad syntax }}",
                                },
                            }
                        ]
                    }
                }
            )
        )

        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": False, "error": "TemplateSyntaxError"}
            register_dev_tools(mock_mcp, ha_url, ha_token, str(tmp_path))
            data = json.loads(mock_mcp._tools["diagnose_template"]("sensor.bad_template"))

        assert data["success"] is True
        assert data["syntax_validation"] == "error"
        assert any("syntax error" in i["message"].lower() for i in data["issues"])

    def test_diagnose_template_unavailable_reference(self, mock_mcp, ha_url, ha_token, tmp_path):
        """Referenced entity unavailable → warning."""
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.entity_registry").write_text(json.dumps({"data": {"entities": []}}))
        (storage / "core.config_entries").write_text(
            json.dumps(
                {
                    "data": {
                        "entries": [
                            {
                                "domain": "template",
                                "title": "Ref Template",
                                "options": {
                                    "template_type": "sensor",
                                    "state": "{{ states('sensor.dead') }}",
                                },
                            }
                        ]
                    }
                }
            )
        )

        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.side_effect = [
                {"success": True, "data": {"state": "unavailable", "attributes": {}}},
                {"success": True, "data": "unavailable"},
            ]
            register_dev_tools(mock_mcp, ha_url, ha_token, str(tmp_path))
            data = json.loads(mock_mcp._tools["diagnose_template"]("sensor.ref_template"))

        assert data["success"] is True
        assert any("unavailable" in i["message"].lower() for i in data["issues"])

    def test_diagnose_template_missing_reference(self, mock_mcp, ha_url, ha_token, tmp_path):
        """Referenced entity not found → error."""
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.entity_registry").write_text(json.dumps({"data": {"entities": []}}))
        (storage / "core.config_entries").write_text(
            json.dumps(
                {
                    "data": {
                        "entries": [
                            {
                                "domain": "template",
                                "title": "Ref Template",
                                "options": {
                                    "template_type": "sensor",
                                    "state": "{{ states('sensor.ghost') }}",
                                },
                            }
                        ]
                    }
                }
            )
        )

        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.side_effect = [
                {"success": False, "error": "not found"},
                {"success": True, "data": "unknown"},
            ]
            register_dev_tools(mock_mcp, ha_url, ha_token, str(tmp_path))
            data = json.loads(mock_mcp._tools["diagnose_template"]("sensor.ref_template"))

        assert data["success"] is True
        assert any("not found" in i["message"].lower() for i in data["issues"])
        assert data["entity_status"]["sensor.ghost"]["exists"] is False

    def test_diagnose_template_many_references(self, mock_mcp, ha_url, ha_token, tmp_path):
        """Template referencing >10 entities → simplification recommendation."""
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.entity_registry").write_text(json.dumps({"data": {"entities": []}}))

        entities = [f"sensor.e{i}" for i in range(15)]
        template_parts = [f"{{{{ states('{e}') }}}}" for e in entities]
        big_template = " ".join(template_parts)

        (storage / "core.config_entries").write_text(
            json.dumps(
                {
                    "data": {
                        "entries": [
                            {
                                "domain": "template",
                                "title": "Big Template",
                                "options": {
                                    "template_type": "sensor",
                                    "state": big_template,
                                },
                            }
                        ]
                    }
                }
            )
        )

        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.side_effect = [
                {"success": True, "data": {"state": "10", "attributes": {}}}
            ] * 16
            register_dev_tools(mock_mcp, ha_url, ha_token, str(tmp_path))
            data = json.loads(mock_mcp._tools["diagnose_template"]("sensor.big_template"))

        assert data["success"] is True
        assert len(data["referenced_entities"]) == 15
        assert any("simplif" in r.lower() for r in data["recommendations"])


class TestDiagnoseEnergySetup:
    """Tests for diagnose_energy_setup()."""

    def test_diagnose_energy_basic(self, mock_mcp, ha_url, ha_token, config_path):
        """Basic energy diagnosis with a few sensors."""
        states = [
            {
                "entity_id": "sensor.energy_total",
                "state": "123",
                "attributes": {"device_class": "energy", "unit_of_measurement": "kWh"},
            },
            {
                "entity_id": "sensor.power_now",
                "state": "350",
                "attributes": {"device_class": "power", "unit_of_measurement": "W"},
            },
        ]

        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
            data = json.loads(mock_mcp._tools["diagnose_energy_setup"]())

        assert data["success"] is True
        assert data["statistics"]["total_energy_sensors"] >= 1
        assert data["statistics"]["total_power_sensors"] >= 1
        assert len(data["recommendations"]) > 0

    def test_diagnose_energy_g12w_detected(self, mock_mcp, ha_url, ha_token, config_path):  # noqa: F841
        """Tariff entity should be detected."""
        states = [
            {
                "entity_id": "binary_sensor.g12w_peak",
                "state": "on",
                "attributes": {"friendly_name": "Peak Hour"},
            },
        ]

        with patch("tools.dev_tools.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
            data = json.loads(mock_mcp._tools["diagnose_energy_setup"]())

        assert data["tariff"] == "Dual-zone tariff detected"
