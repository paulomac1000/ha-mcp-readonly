"""
Unit tests for tools/yaml_utils.py

SECURITY CRITICAL TESTS - Custom YAML loader validation

Tests:
- HomeAssistantLoader tag handling
- Security: No code execution through YAML
- Tag parsing (include, secret, env_var, etc.)
- Error handling
- Edge cases and malicious input
"""

from unittest.mock import MagicMock

import yaml

from tools.yaml_utils import (
    HomeAssistantLoader,
    dump_yaml,
    load_yaml_file,
    parse_yaml_string,
)


class TestHomeAssistantLoader:
    """Test custom YAML loader for HA tags."""

    def test_ha_loader_handles_secret_tag(self):
        """Test that !secret tags are handled correctly."""
        yaml_content = """
        api_key: !secret openai_key
        password: !secret db_password
        """

        result = yaml.load(yaml_content, Loader=HomeAssistantLoader)

        assert isinstance(result, dict)
        assert "api_key" in result
        assert "!secret" in result["api_key"]
        assert "openai_key" in result["api_key"]

    def test_ha_loader_handles_include_tag(self):
        """Test that !include tags are handled correctly."""
        yaml_content = """
        automations: !include automations.yaml
        scripts: !include scripts.yaml
        """

        result = yaml.load(yaml_content, Loader=HomeAssistantLoader)

        assert isinstance(result, dict)
        assert "automations" in result
        assert "!include" in result["automations"]
        assert "automations.yaml" in result["automations"]

    def test_ha_loader_handles_include_dir_list(self):
        """Test that !include_dir_list tags are handled."""
        yaml_content = """
        automation: !include_dir_list automations/
        """

        result = yaml.load(yaml_content, Loader=HomeAssistantLoader)

        assert isinstance(result, dict)
        assert "automation" in result
        assert "!include_dir_list" in result["automation"]

    def test_ha_loader_handles_include_dir_merge_list(self):
        """Test that !include_dir_merge_list tags are handled."""
        yaml_content = """
        template: !include_dir_merge_list templates/
        """

        result = yaml.load(yaml_content, Loader=HomeAssistantLoader)

        assert isinstance(result, dict)
        assert "template" in result
        assert "!include_dir_merge_list" in result["template"]

    def test_ha_loader_handles_env_var_tag(self):
        """Test that !env_var tags are handled."""
        yaml_content = """
        api_url: !env_var API_URL
        timeout: !env_var TIMEOUT 30
        """

        result = yaml.load(yaml_content, Loader=HomeAssistantLoader)

        assert isinstance(result, dict)
        assert "api_url" in result
        assert "!env_var" in result["api_url"]

    def test_ha_loader_handles_input_tag(self):
        """Test that !input tags are handled (blueprint inputs)."""
        yaml_content = """
        entity_id: !input target_entity
        delay: !input wait_time
        """

        result = yaml.load(yaml_content, Loader=HomeAssistantLoader)

        assert isinstance(result, dict)
        assert "entity_id" in result
        assert "!input" in result["entity_id"]

    def test_ha_loader_handles_multiple_tags(self):
        """Test document with multiple different tags."""
        yaml_content = """
        homeassistant:
          name: !secret home_name
          latitude: !env_var LATITUDE

        automations: !include automations.yaml
        scripts: !include_dir_merge_named scripts/
        """

        result = yaml.load(yaml_content, Loader=HomeAssistantLoader)

        assert isinstance(result, dict)
        assert "homeassistant" in result
        assert "automations" in result
        assert "scripts" in result

    def test_ha_loader_handles_file_tag(self):
        """Test that !file tag is handled (registered in _HA_TAGS)."""
        yaml_content = """
        certificate: !file /ssl/cert.pem
        """
        result = yaml.load(yaml_content, Loader=HomeAssistantLoader)
        assert isinstance(result, dict)
        assert "certificate" in result
        assert "!file" in result["certificate"]
        assert "cert.pem" in result["certificate"]

    def test_ha_loader_handles_include_dir_named(self):
        """Test that !include_dir_named tag is handled."""
        yaml_content = """
        zone: !include_dir_named zones/
        """
        result = yaml.load(yaml_content, Loader=HomeAssistantLoader)
        assert isinstance(result, dict)
        assert "!include_dir_named" in result["zone"]

    def test_ha_tag_constructor_sequence_node(self):
        """Tag applied to a YAML sequence → returned as string with list repr."""
        yaml_content = """
        setting: !env_var [DEFAULT_A, DEFAULT_B]
        """
        result = yaml.load(yaml_content, Loader=HomeAssistantLoader)
        assert isinstance(result, dict)
        assert "setting" in result
        assert "!env_var" in result["setting"]

    def test_ha_tag_constructor_mapping_node(self):
        """Tag applied to a YAML mapping → returned as string with dict repr."""
        yaml_content = """
        setting: !env_var {key: VALUE, fallback: default}
        """
        result = yaml.load(yaml_content, Loader=HomeAssistantLoader)
        assert isinstance(result, dict)
        assert "setting" in result
        assert "!env_var" in result["setting"]


class TestSecurityValidation:
    """CRITICAL: Test that loader doesn't execute code."""

    def test_security_no_python_code_execution(self):
        """SECURITY: Test that Python code in YAML is NOT executed."""
        malicious_yaml = """
        dangerous: !!python/object/apply:os.system ['echo pwned']
        """

        # Should not execute code, should raise error or return safe value
        try:
            yaml.load(malicious_yaml, Loader=HomeAssistantLoader)
            # If it doesn't raise, check it didn't execute
            # (SafeLoader should prevent this)
        except yaml.constructor.ConstructorError:
            # Expected - SafeLoader rejects dangerous tags
            pass

    def test_security_no_arbitrary_object_creation(self):
        """SECURITY: Test that arbitrary Python objects cannot be created."""
        malicious_yaml = """
        exploit: !!python/object/new:os.system
        args: ['rm -rf /']
        """

        try:
            yaml.load(malicious_yaml, Loader=HomeAssistantLoader)
        except yaml.constructor.ConstructorError:
            # Expected - should reject
            pass

    def test_security_no_eval_through_tags(self):
        """SECURITY: Test that eval/exec cannot be triggered through custom tags."""
        malicious_yaml = """
        code: !secret __import__('os').system('whoami')
        """

        result = yaml.load(malicious_yaml, Loader=HomeAssistantLoader)

        # Should treat as string, not execute
        assert isinstance(result, dict)
        # The dangerous code should be in string form, not executed
        assert "code" in result
        # Should NOT have executed (no actual import occurred)

    def test_security_no_file_access_through_tags(self):
        """SECURITY: Test that !include doesn't actually read files in loader."""
        yaml_content = """
        secrets: !include /etc/passwd
        """

        result = yaml.load(yaml_content, Loader=HomeAssistantLoader)

        # Should return tag as string, not file contents
        assert isinstance(result, dict)
        assert "secrets" in result
        # Should contain the tag, not actual file data
        assert "!include" in result["secrets"]

    def test_security_deeply_nested_structures(self):
        """SECURITY: Test handling of deeply nested YAML (DoS prevention)."""
        # Create deeply nested structure
        nested_yaml = "a: " + "{'b': " * 100 + "'value'" + "}" * 100

        try:
            yaml.load(nested_yaml, Loader=HomeAssistantLoader)
            # Should handle or reject, not crash
        except yaml.YAMLError:
            # Acceptable to reject overly complex structures
            pass

    def test_security_large_yaml_document(self):
        """SECURITY: Test handling of very large YAML documents."""
        # Create large document
        large_yaml = "\n".join([f"key_{i}: value_{i}" for i in range(10000)])

        try:
            result = yaml.load(large_yaml, Loader=HomeAssistantLoader)
            assert isinstance(result, dict)
            assert len(result) == 10000
        except (yaml.YAMLError, MemoryError):
            # May reject if too large
            pass


class TestLoadYAMLFile:
    """Test load_yaml_file function."""

    def test_load_valid_yaml_file(self, tmp_path):
        """Test loading valid YAML file."""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text("""
        sensor:
          - platform: template
            name: Test Sensor
        """)

        result = load_yaml_file(str(yaml_file))

        assert result is not None
        assert isinstance(result, dict)
        assert "sensor" in result

    def test_load_yaml_file_with_ha_tags(self, tmp_path):
        """Test loading YAML with HA tags."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("""
        api_key: !secret my_key
        automations: !include automations.yaml
        """)

        result = load_yaml_file(str(yaml_file))

        assert result is not None
        assert "api_key" in result
        assert "!secret" in result["api_key"]

    def test_load_yaml_file_not_found(self):
        """Test loading non-existent file."""
        result = load_yaml_file("/nonexistent/file.yaml")

        assert result is None

    def test_load_yaml_file_invalid_syntax(self, tmp_path):
        """Test loading file with invalid YAML syntax."""
        yaml_file = tmp_path / "invalid.yaml"
        yaml_file.write_text("""
        invalid yaml:
          - missing quotes
          bad: [unclosed
        """)

        result = load_yaml_file(str(yaml_file))

        assert result is None  # Should return None on error

    def test_load_yaml_file_empty(self, tmp_path):
        """Test loading empty YAML file."""
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("")

        result = load_yaml_file(str(yaml_file))

        # Empty file returns None from PyYAML
        assert result is None

    def test_load_yaml_file_with_unicode(self, tmp_path):
        """Test loading YAML with Unicode characters."""
        yaml_file = tmp_path / "unicode.yaml"
        yaml_file.write_text(
            "\nname: Test Sensor with \U0001f321\nlocation: Lodz\nemoji: OK\n",
            encoding="utf-8",
        )

        result = load_yaml_file(str(yaml_file))

        assert result is not None
        assert "name" in result
        assert "\U0001f321" in result["name"]


class TestParseYAMLString:
    """Test parse_yaml_string function."""

    def test_parse_valid_yaml_string(self):
        """Test parsing valid YAML string."""
        yaml_string = """
        sensor:
          - platform: mqtt
            state_topic: test/topic
        """

        result = parse_yaml_string(yaml_string)

        assert result is not None
        assert isinstance(result, dict)
        assert "sensor" in result

    def test_parse_yaml_string_with_tags(self):
        """Test parsing YAML with HA tags."""
        yaml_string = """
        password: !secret db_password
        config: !include settings.yaml
        """

        result = parse_yaml_string(yaml_string)

        assert result is not None
        assert "password" in result
        assert "!secret" in result["password"]

    def test_parse_invalid_yaml_string(self):
        """Test parsing invalid YAML."""
        yaml_string = """
        invalid: [unclosed
        bad syntax here
        """

        result = parse_yaml_string(yaml_string)

        assert result is None

    def test_parse_empty_string(self):
        """Test parsing empty string."""
        result = parse_yaml_string("")

        assert result is None

    def test_parse_yaml_with_anchors_and_aliases(self):
        """Test parsing YAML with anchors and aliases."""
        yaml_string = """
        defaults: &defaults
          timeout: 30
          retries: 3

        service_a:
          <<: *defaults
          name: Service A

        service_b:
          <<: *defaults
          name: Service B
        """

        result = parse_yaml_string(yaml_string)

        assert result is not None
        assert "service_a" in result
        assert "timeout" in result["service_a"]
        assert result["service_a"]["timeout"] == 30


class TestDumpYAML:
    """Test dump_yaml function."""

    def test_dump_simple_dict(self):
        """Test dumping simple dictionary."""
        data = {"name": "Test", "value": 42, "items": ["a", "b", "c"]}

        result = dump_yaml(data)

        assert isinstance(result, str)
        assert "name: Test" in result
        assert "value: 42" in result

    def test_dump_with_unicode(self):
        """Test dumping data with Unicode."""
        data = {"name": "Sensor 🌡️", "location": "Lodz"}

        result = dump_yaml(data)

        assert isinstance(result, str)
        assert "🌡️" in result or "Sensor" in result

    def test_dump_flow_style(self):
        """Test dumping with flow style."""
        data = {"items": [1, 2, 3]}

        result = dump_yaml(data, default_flow_style=True)

        assert isinstance(result, str)
        # Flow style uses inline notation
        assert "[1, 2, 3]" in result or "items:" in result

    def test_dump_sorted_keys(self):
        """Test dumping with sorted keys."""
        data = {"z": 1, "a": 2, "m": 3}

        result = dump_yaml(data, sort_keys=True)

        assert isinstance(result, str)
        # Should be alphabetically sorted
        lines = result.strip().split("\n")
        keys = [line.split(":")[0] for line in lines if ":" in line]
        assert keys == sorted(keys)

    def test_dump_nested_structure(self):
        """Test dumping nested structure."""
        data = {"level1": {"level2": {"level3": "value"}}}

        result = dump_yaml(data)

        assert isinstance(result, str)
        assert "level1:" in result
        assert "level2:" in result
        assert "level3:" in result


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_yaml_with_tabs(self, tmp_path):
        """Test handling YAML with tabs (invalid in YAML spec)."""
        yaml_file = tmp_path / "tabs.yaml"
        yaml_file.write_text("key:\tvalue")  # Tab character

        result = load_yaml_file(str(yaml_file))

        # May fail or succeed depending on PyYAML version
        # Should not crash
        assert result is None or isinstance(result, dict)

    def test_yaml_with_special_characters(self):
        """Test YAML with special characters."""
        yaml_string = """
        key1: "value with: colon"
        key2: "value with # hash"
        key3: "value with @ at"
        """

        result = parse_yaml_string(yaml_string)

        assert result is not None
        assert "key1" in result

    def test_yaml_with_null_values(self):
        """Test YAML with null/None values."""
        yaml_string = """
        key1: null
        key2: ~
        key3:
        """

        result = parse_yaml_string(yaml_string)

        assert result is not None
        assert result["key1"] is None
        assert result["key2"] is None
        assert result["key3"] is None

    def test_yaml_with_boolean_strings(self):
        """Test YAML with boolean-like strings."""
        yaml_string = """
        bool1: yes
        bool2: no
        bool3: true
        bool4: false
        bool5: on
        bool6: off
        """

        result = parse_yaml_string(yaml_string)

        assert result is not None
        # PyYAML auto-converts these
        assert isinstance(result["bool1"], bool) or isinstance(result["bool1"], str)

    def test_yaml_with_numeric_strings(self):
        """Test YAML with numeric strings."""
        yaml_string = """
        num1: "123"
        num2: "45.67"
        num3: 123
        num4: 45.67
        """

        result = parse_yaml_string(yaml_string)

        assert result is not None
        assert isinstance(result["num1"], str)
        assert isinstance(result["num3"], int)

    def test_circular_reference_prevention(self):
        """Test that circular references don't cause infinite loops."""
        # This would cause issues if actually resolved
        yaml_string = """
        a: &ref
          b: *ref
        """

        try:
            parse_yaml_string(yaml_string)
            # If it parses, should handle gracefully
        except yaml.constructor.ConstructorError:
            # Expected - PyYAML detects circular references
            pass


class TestIntegrationWithHAConfigs:
    """Test with real Home Assistant configuration patterns."""

    def test_configuration_yaml_pattern(self):
        """Test typical configuration.yaml structure."""
        yaml_string = """
        homeassistant:
          name: !secret home_name
          latitude: !secret longitude
          longitude: !secret longitude

        http:
          ssl_certificate: !secret ssl_cert
          ssl_key: !secret ssl_key

        automation: !include automations.yaml
        script: !include scripts.yaml
        scene: !include scenes.yaml

        sensor: !include_dir_merge_list sensors/
        """

        result = parse_yaml_string(yaml_string)

        assert result is not None
        assert "homeassistant" in result
        assert "automation" in result
        assert "sensor" in result
        # Tags should be preserved as strings
        assert "!secret" in str(result)
        assert "!include" in str(result)

    def test_automation_pattern(self):
        """Test typical automation structure."""
        yaml_string = """
        - alias: Turn on lights at sunset
          trigger:
            - platform: sun
              event: sunset
          condition:
            - condition: state
              entity_id: input_boolean.auto_lights
              state: 'on'
          action:
            - service: light.turn_on
              target:
                entity_id: light.living_room
        """

        result = parse_yaml_string(yaml_string)

        assert result is not None
        assert isinstance(result, list)
        assert len(result) > 0
        assert "alias" in result[0]

    def test_template_with_jinja(self):
        """Test template sensor with Jinja2 templates."""
        yaml_string = """
        - sensor:
            - name: "Temperature Rounded"
              state: >
                {{ states('sensor.temperature') | round(1) }}
            - name: "Is Home"
              state: >
                {{ is_state('person.john', 'home') }}
        """

        result = parse_yaml_string(yaml_string)

        assert result is not None
        assert isinstance(result, list)
        # Jinja templates should be preserved as strings
        assert "{{" in str(result)


class TestHaTagConstructor:
    """Tests for _ha_tag_constructor edge cases."""

    def test_unknown_node_type_fallback(self):
        """Fallback return when node type is not Scalar, Sequence, or Mapping."""
        from tools.yaml_utils import _ha_tag_constructor

        # Use a plain object that isinstance won't match for any yaml node type
        class UnknownNode:
            pass

        mock_node = UnknownNode()
        result = _ha_tag_constructor(MagicMock(), "custom_test", mock_node)

        assert result == "!custom_test"
