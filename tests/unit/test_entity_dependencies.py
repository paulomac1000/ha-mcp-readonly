"""
Tests for tools/entity_dependencies.py
"""

import json
from unittest.mock import patch

import pytest

from tools.entity_dependencies import register_entity_dependency_tools


class TestGetEntityDependencies:
    """Tests for get_entity_dependencies()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data):
        """Setup test fixtures."""
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

        # Sample automation YAML content
        self.automations_yaml = """
- id: '1679916667559'
  alias: Switch - socket PC
  trigger:
  - platform: state
    entity_id: sensor.power_usage
  action:
  - service: switch.turn_on
    target:
      entity_id: switch.socket_pc
"""

        # Sample script YAML content
        self.scripts_yaml = """
turn_off_all:
  alias: Turn Off All
  sequence:
  - service: switch.turn_off
    target:
      entity_id: switch.socket_pc
"""

    @pytest.mark.asyncio
    async def test_get_dependencies_found(self):
        """Test finding dependencies in automation and scripts."""
        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:

                def yaml_side_effect(path):
                    if "automations.yaml" in path:
                        import yaml

                        return yaml.safe_load(self.automations_yaml)
                    if "scripts.yaml" in path:
                        import yaml

                        return yaml.safe_load(self.scripts_yaml)
                    return None

                mock_yaml.side_effect = yaml_side_effect

                # Mock file existence
                with patch("os.path.exists", return_value=True):
                    register_entity_dependency_tools(
                        self.mock_mcp, self.config_path, "http://test", "token"
                    )

                    result = await self.mock_mcp._tools["get_entity_dependencies"](
                        "switch.socket_pc"
                    )

        data = json.loads(result)

        assert data["success"] is True
        assert len(data["used_in"]["automations"]) == 1
        assert data["used_in"]["automations"][0]["alias"] == "Switch - socket PC"

        assert len(data["used_in"]["scripts"]) == 1
        assert data["used_in"]["scripts"][0]["alias"] == "Turn Off All"

    @pytest.mark.asyncio
    async def test_get_dependencies_not_found(self):
        """Test entity with no dependencies."""
        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file", return_value=[]):
                with patch("os.path.exists", return_value=True):
                    register_entity_dependency_tools(
                        self.mock_mcp, self.config_path, "http://test", "token"
                    )

                    result = await self.mock_mcp._tools["get_entity_dependencies"]("unused.entity")

        data = json.loads(result)

        assert data["success"] is True
        assert len(data["used_in"]["automations"]) == 0
        assert len(data["used_in"]["scripts"]) == 0

    @pytest.mark.asyncio
    async def test_get_dependencies_with_depends_on(self):
        """Test depends_on field resolution."""
        entity_id = "binary_sensor.sonoff_button_action"

        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file", return_value=[]):
                with patch("os.path.exists", return_value=True):
                    register_entity_dependency_tools(
                        self.mock_mcp, self.config_path, "http://test", "token"
                    )

                    result = await self.mock_mcp._tools["get_entity_dependencies"](entity_id)

        data = json.loads(result)

        assert data["success"] is True
        assert data["depends_on"]["config_entry_id"] == "e01182bae2f8b20605c8317f4623d1e9"
        assert data["depends_on"]["integration"] == "mqtt"
        assert data["depends_on"]["device_id"] == "c67a8024bc53a3d38dacc8c8c6e01cf6"
        assert data["depends_on"]["device_name"] == "Sonoff Button"

    @pytest.mark.asyncio
    async def test_get_dependencies_empty_entity_id(self):
        """Empty entity_id should return success=False immediately."""
        register_entity_dependency_tools(self.mock_mcp, self.config_path, "http://test", "token")
        result = await self.mock_mcp._tools["get_entity_dependencies"]("")
        data = json.loads(result)
        assert data["success"] is False
        assert "entity_id" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_get_dependencies_in_dashboard(self, tmp_path):
        """Entity referenced in a Lovelace dashboard should appear in used_in.dashboards."""
        import json as _json

        storage = tmp_path / ".storage"
        storage.mkdir()
        lovelace_data = {
            "data": {
                "config": {"views": [{"title": "Home", "cards": [{"entity": "light.living_room"}]}]}
            }
        }
        (storage / "lovelace").write_text(_json.dumps(lovelace_data))

        with patch("tools.entity_dependencies.load_registry") as mock_load:

            def load_side(name, path):
                if name == "lovelace":
                    return lovelace_data
                return self.mock_registry_data.get(name, {})

            mock_load.side_effect = load_side

            with patch("tools.entity_dependencies.load_yaml_file", return_value=[]):
                with patch("os.path.exists", return_value=True):
                    with patch("os.listdir", return_value=["lovelace"]):
                        register_entity_dependency_tools(
                            self.mock_mcp, str(tmp_path), "http://test", "token"
                        )
                        result = await self.mock_mcp._tools["get_entity_dependencies"](
                            "light.living_room"
                        )

        data = json.loads(result)
        assert data["success"] is True
        assert data["summary"]["dashboards_count"] >= 1

    @pytest.mark.asyncio
    async def test_get_dependencies_in_template_config_entry(self):
        """Entity in a template config entry should appear in used_in.templates."""
        registry_with_template = dict(self.mock_registry_data)
        registry_with_template["core.config_entries"] = {
            "data": {
                "entries": [
                    {
                        "entry_id": "tpl1",
                        "domain": "template",
                        "title": "My Template",
                        "options": {"state": "{{ states('sensor.temperature_living_room') }}"},
                    }
                ]
            }
        }

        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: registry_with_template.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file", return_value=[]):
                with patch("os.path.exists", return_value=False):
                    register_entity_dependency_tools(
                        self.mock_mcp, self.config_path, "http://test", "token"
                    )
                    result = await self.mock_mcp._tools["get_entity_dependencies"](
                        "sensor.temperature_living_room"
                    )

        data = json.loads(result)
        assert data["success"] is True
        assert data["summary"]["templates_count"] >= 1
        assert any(t["name"] == "My Template" for t in data["used_in"]["templates"])

    @pytest.mark.asyncio
    async def test_get_dependencies_in_configuration_yaml_template(self, tmp_path):
        """Entity in configuration.yaml template: section should appear in used_in.templates as typeee yaml."""
        config_content = (
            "template:\n"
            "  - sensor:\n"
            "      - name: avg_temp\n"
            "        state: \"{{ states('sensor.living_temp') }}\"\n"
        )
        (tmp_path / "configuration.yaml").write_text(config_content, encoding="utf-8")

        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file", return_value=[]):
                register_entity_dependency_tools(
                    self.mock_mcp, str(tmp_path), "http://test", "token"
                )
                result = await self.mock_mcp._tools["get_entity_dependencies"]("sensor.living_temp")

        data = json.loads(result)
        assert data["success"] is True
        yaml_templates = [t for t in data["used_in"]["templates"] if t["type"] == "yaml"]
        assert len(yaml_templates) >= 1
        assert yaml_templates[0]["name"] == "configuration.yaml"


class TestGetEntityConsumers:
    """Tests for get_entity_consumers()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

        self.automations_yaml = """
- id: '123'
  alias: Test Auto
  trigger: []
  action:
  - service: switch.turn_on
    entity_id: switch.test
"""
        # Empty script for certainty
        self.scripts_yaml = "{}"

    @pytest.mark.asyncio
    async def test_get_consumers(self):
        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.return_value = {}

            with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:
                import yaml

                # We use side_effect instead of return_value
                def yaml_side_effect(path):
                    if "automations.yaml" in path:
                        return yaml.safe_load(self.automations_yaml)
                    if "scripts.yaml" in path:
                        return yaml.safe_load(self.scripts_yaml)
                    return None

                mock_yaml.side_effect = yaml_side_effect

                with patch("os.path.exists", return_value=True):
                    register_entity_dependency_tools(
                        self.mock_mcp, self.config_path, "http://test", "token"
                    )

                    result = await self.mock_mcp._tools["get_entity_consumers"]("switch.test")

        data = json.loads(result)

        assert data["success"] is True
        assert data["consumers_count"] == 1
        assert data["consumers"][0]["type"] == "automation"
        assert data["consumers"][0]["name"] == "Test Auto"

    @pytest.mark.asyncio
    async def test_get_consumers_scripts_list_format(self):
        """scripts.yaml in list format should be parsed correctly."""
        scripts_list_yaml = """
- id: script_abc
  alias: List Script
  sequence:
  - service: switch.turn_off
    entity_id: switch.test
"""
        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.return_value = {}

            with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:
                import yaml

                def yaml_side_effect(path):
                    if "automations.yaml" in path:
                        return []
                    if "scripts.yaml" in path:
                        return yaml.safe_load(scripts_list_yaml)
                    return None

                mock_yaml.side_effect = yaml_side_effect

                with patch("os.path.exists", return_value=True):
                    register_entity_dependency_tools(
                        self.mock_mcp, self.config_path, "http://test", "token"
                    )
                    result = await self.mock_mcp._tools["get_entity_consumers"]("switch.test")

        data = json.loads(result)
        assert data["success"] is True
        assert data["consumers_count"] == 1
        assert data["consumers"][0]["type"] == "script"
        assert data["consumers"][0]["name"] == "List Script"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
