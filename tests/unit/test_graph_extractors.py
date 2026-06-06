"""
Tests for ha_graph/extractors.py — entity extraction from templates,
structured data, triggers, services, and controlled entities.
"""

from ha_graph.extractors import (
    ENTITY_PATTERN,
    STATES_DOT_PATTERN,
    TEMPLATE_ENTITY_PATTERN,
    extract_controlled_entities,
    extract_entities_from_data,
    extract_entities_from_template,
    extract_services,
    extract_trigger_info,
    has_dynamic_template_refs,
)


class TestPatterns:
    """Test compiled regex patterns match expected inputs."""

    def test_entity_pattern_matches_standard(self):
        """ENTITY_PATTERN matches standard entity IDs."""
        text = "The light.living_room and sensor.temperature are referenced."
        matches = ENTITY_PATTERN.findall(text)
        assert "light.living_room" in matches
        assert "sensor.temperature" in matches

    def test_entity_pattern_requires_domain_prefix(self):
        """ENTITY_PATTERN does not match bare words."""
        text = "living_room temperature sensor"
        matches = ENTITY_PATTERN.findall(text)
        assert matches == []

    def test_entity_pattern_multidot(self):
        """ENTITY_PATTERN matches entity IDs with underscores and hyphens."""
        text = "binary_sensor.motion_sensor_kitchen and sensor.temp-outside"
        matches = ENTITY_PATTERN.findall(text)
        assert "binary_sensor.motion_sensor_kitchen" in matches
        assert "sensor.temp-outside" in matches

    def test_template_entity_pattern(self):
        """TEMPLATE_ENTITY_PATTERN extracts from states() calls."""
        text = "{{ states('sensor.temperature') }}"
        matches = TEMPLATE_ENTITY_PATTERN.findall(text)
        assert "sensor.temperature" in matches

    def test_template_entity_pattern_is_state(self):
        """TEMPLATE_ENTITY_PATTERN handles is_state() calls."""
        text = "{{ is_state('light.kitchen', 'on') }}"
        matches = TEMPLATE_ENTITY_PATTERN.findall(text)
        assert "light.kitchen" in matches

    def test_template_entity_pattern_expand(self):
        """TEMPLATE_ENTITY_PATTERN handles expand() calls."""
        text = "{{ expand('group.lights') }}"
        matches = TEMPLATE_ENTITY_PATTERN.findall(text)
        assert "group.lights" in matches

    def test_states_dot_pattern(self):
        """STATES_DOT_PATTERN extracts from states.x.y syntax."""
        text = "{{ states.sensor.temperature }}"
        matches = STATES_DOT_PATTERN.findall(text)
        assert "sensor.temperature" in matches

    def test_states_dot_pattern_with_filter(self):
        """STATES_DOT_PATTERN works with filter pipelines."""
        text = "{{ states.light.living_room | float }}"
        matches = STATES_DOT_PATTERN.findall(text)
        assert "light.living_room" in matches


class TestExtractEntitiesFromTemplate:
    """Test extract_entities_from_template()."""

    def test_extract_from_function_calls(self):
        """Extract from states(), is_state(), state_attr() calls."""
        result = extract_entities_from_template(
            "{{ states('sensor.temp') }} and {{ is_state('light.kitchen', 'on') }}"
        )
        ids = [eid for eid, _ in result]
        assert "sensor.temp" in ids
        assert "light.kitchen" in ids

    def test_extract_from_expand(self):
        """Extract from expand() calls."""
        result = extract_entities_from_template("{{ expand('group.living_room_lights') }}")
        ids = [eid for eid, _ in result]
        assert "group.living_room_lights" in ids

    def test_extract_from_states_dot(self):
        """Extract from states.x.y syntax."""
        result = extract_entities_from_template("{{ states.sensor.outdoor_temp }}")
        ids = [eid for eid, _ in result]
        assert "sensor.outdoor_temp" in ids

    def test_extract_from_area_entities(self):
        """Extract from area_entities() calls with entity IDs."""
        result = extract_entities_from_template(
            "{{ area_entities('sensor.living_temp') }}"
        )
        ids = [eid for eid, _ in result]
        assert "sensor.living_temp" in ids

    def test_dynamic_references_excluded(self):
        """Dynamic references using ~ concatenation are excluded."""
        result = extract_entities_from_template("{{ states('sensor.' ~ variable) }}")
        # Should not contain any entity since it's dynamic
        assert len(result) == 0

    def test_empty_input(self):
        """Empty or non-string input returns empty list."""
        assert extract_entities_from_template("") == []
        assert extract_entities_from_template(None) == []  # type: ignore[arg-type]

    def test_confidence_is_inferred(self):
        """Static references get confidence='inferred'."""
        result = extract_entities_from_template("{{ states('sensor.x') }}")
        assert len(result) >= 1
        for _eid, conf in result:
            assert conf == "inferred"


class TestHasDynamicTemplateRefs:
    """Test has_dynamic_template_refs()."""

    def test_dynamic_detected(self):
        """Templates with ~ are flagged as dynamic."""
        assert has_dynamic_template_refs("{{ states('sensor.' ~ var) }}") is True

    def test_static_not_detected(self):
        """Templates without ~ are not flagged as dynamic."""
        assert has_dynamic_template_refs("{{ states('sensor.temp') }}") is False

    def test_empty_input(self):
        """Empty input returns False."""
        assert has_dynamic_template_refs("") is False
        assert has_dynamic_template_refs(None) is False  # type: ignore[arg-type]


class TestExtractEntitiesFromData:
    """Test extract_entities_from_data()."""

    def test_entity_id_key(self):
        """Extract from entity_id key in dict."""
        data = {"entity_id": "light.living_room"}
        result = extract_entities_from_data(data)
        assert "light.living_room" in result

    def test_entity_id_list(self):
        """Extract from entity_id list value."""
        data = {"entity_id": ["light.a", "light.b"]}
        result = extract_entities_from_data(data)
        assert "light.a" in result
        assert "light.b" in result

    def test_nested_target_dict(self):
        """Extract from nested target.entity_id."""
        data = {"target": {"entity_id": "switch.socket"}}
        result = extract_entities_from_data(data)
        assert "switch.socket" in result

    def test_entity_key_and_scene_key(self):
        """Extract from 'entity' and 'scene' keys."""
        data = {"entity": "sensor.temp", "scene": "scene.movie_night"}
        result = extract_entities_from_data(data)
        assert "sensor.temp" in result
        assert "scene.movie_night" in result

    def test_template_in_string(self):
        """Extract from Jinja2 templates in string values."""
        data = {"message": "Temp is {{ states('sensor.temp') }}"}
        result = extract_entities_from_data(data)
        assert "sensor.temp" in result

    def test_extract_from_list(self):
        """Extract entity IDs from a list of strings."""
        data = ["light.a", "sensor.b"]
        result = extract_entities_from_data(data)
        assert "light.a" in result
        assert "sensor.b" in result

    def test_plain_string(self):
        """Extract from a plain string."""
        result = extract_entities_from_data("light.kitchen and sensor.temp")
        assert "light.kitchen" in result
        assert "sensor.temp" in result

    def test_no_entity_keys(self):
        """Dict without entity keys yields no results."""
        result = extract_entities_from_data({"name": "Kitchen", "value": 42})
        assert result == set()

    def test_empty_data(self):
        """Empty data yields empty set."""
        assert extract_entities_from_data({}) == set()
        assert extract_entities_from_data([]) == set()
        assert extract_entities_from_data("") == set()


class TestExtractTriggerInfo:
    """Test extract_trigger_info()."""

    def test_state_trigger(self):
        """Extract from state trigger with single entity_id."""
        triggers = [{"platform": "state", "entity_id": "binary_sensor.motion"}]
        result = extract_trigger_info(triggers)
        assert ("binary_sensor.motion", "state") in result

    def test_multiple_entity_ids(self):
        """Extract from trigger with multiple entity_ids."""
        triggers = [{"platform": "state", "entity_id": ["light.a", "light.b"]}]
        result = extract_trigger_info(triggers)
        assert ("light.a", "state") in result
        assert ("light.b", "state") in result

    def test_numeric_state_trigger(self):
        """Extract from numeric_state trigger."""
        triggers = [{"platform": "numeric_state", "entity_id": "sensor.temp", "above": "25"}]
        result = extract_trigger_info(triggers)
        assert ("sensor.temp", "numeric_state") in result

    def test_template_trigger(self):
        """Extract entity from template value_template."""
        triggers = [{"platform": "template", "value_template": "{{ states('sensor.temp') | float > 20 }}"}
        ]
        result = extract_trigger_info(triggers)
        assert ("sensor.temp", "template") in result

    def test_event_trigger(self):
        """Extract from event trigger with event_data."""
        triggers = [{"platform": "event", "event_type": "my_event", "event_data": {"entity_id": "light.kitchen"}}]
        result = extract_trigger_info(triggers)
        assert ("light.kitchen", "event") in result

    def test_zone_trigger(self):
        """Extract from zone trigger."""
        triggers = [{"platform": "zone", "entity_id": "person.test", "zone": "zone.home"}]
        result = extract_trigger_info(triggers)
        assert ("zone.home", "zone") in result

    def test_device_trigger(self):
        """Extract device_id from device trigger."""
        triggers = [{"platform": "device", "device_id": "device_abc123"}]
        result = extract_trigger_info(triggers)
        assert ("device_abc123", "device") in result

    def test_invalid_input(self):
        """Non-list input returns empty list."""
        assert extract_trigger_info(None) == []  # type: ignore[arg-type]
        assert extract_trigger_info("not a list") == []  # type: ignore[arg-type]
        assert extract_trigger_info({}) == []  # type: ignore[arg-type]

    def test_empty_trigger_list(self):
        """Empty trigger list returns empty list."""
        assert extract_trigger_info([]) == []


class TestExtractServices:
    """Test extract_services()."""

    def test_simple_service_call(self):
        """Extract from simple service call actions."""
        actions = [{"service": "light.turn_on", "data": {"entity_id": "light.kitchen"}}]
        result = extract_services(actions)
        assert "light.turn_on" in result

    def test_multiple_services(self):
        """Extract multiple services from action sequence."""
        actions = [
            {"service": "light.turn_on"},
            {"service": "lock.lock"},
            {"service": "climate.set_temperature"},
        ]
        result = extract_services(actions)
        assert "light.turn_on" in result
        assert "lock.lock" in result
        assert "climate.set_temperature" in result

    def test_nested_choose(self):
        """Extract services from choose branches."""
        actions = [
            {
                "choose": [
                    {"sequence": [{"service": "light.turn_on"}]},
                    {"sequence": [{"service": "light.turn_off"}]},
                ]
            }
        ]
        result = extract_services(actions)
        assert "light.turn_on" in result
        assert "light.turn_off" in result

    def test_nested_parallel(self):
        """Extract services from parallel actions."""
        actions = [{"parallel": [{"service": "switch.turn_on"}, {"service": "fan.turn_on"}]}]
        result = extract_services(actions)
        assert "switch.turn_on" in result
        assert "fan.turn_on" in result

    def test_empty_actions(self):
        """Empty action list returns empty set."""
        assert extract_services([]) == set()
        assert extract_services(None) == set()  # type: ignore[arg-type]


class TestExtractControlledEntities:
    """Test extract_controlled_entities()."""

    def test_target_entity_id(self):
        """Extract from target.entity_id."""
        actions = [{"service": "light.turn_on", "target": {"entity_id": "light.living_room"}}]
        result = extract_controlled_entities(actions)
        assert "light.living_room" in result

    def test_target_entity_id_list(self):
        """Extract from target.entity_id list."""
        actions = [{"service": "light.turn_on", "target": {"entity_id": ["light.a", "light.b"]}}]
        result = extract_controlled_entities(actions)
        assert "light.a" in result
        assert "light.b" in result

    def test_data_entity_id(self):
        """Extract from data.entity_id (scene.create pattern)."""
        actions = [{"service": "scene.create", "data": {"entity_id": "scene.movie"}}]
        result = extract_controlled_entities(actions)
        assert "scene.movie" in result

    def test_scene_activation(self):
        """Extract from scene activation."""
        actions = [{"scene": "scene.movie_night"}]
        result = extract_controlled_entities(actions)
        assert "scene.movie_night" in result

    def test_empty_actions(self):
        """Empty action list returns empty set."""
        assert extract_controlled_entities([]) == set()
        assert extract_controlled_entities(None) == set()  # type: ignore[arg-type]
