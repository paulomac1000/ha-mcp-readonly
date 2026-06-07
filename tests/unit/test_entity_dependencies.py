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


class TestIncludeFileScanning:
    """Tests for !include file scanning in entity dependencies."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

    @pytest.mark.asyncio
    async def test_finds_entity_in_include_files(self):
        """Entity referenced in YAML !include files should be found."""
        from pathlib import Path

        config_dir = Path(self.config_path)
        include_dir = config_dir / "packages"
        include_dir.mkdir(parents=True, exist_ok=True)
        include_file = include_dir / "test_includes.yaml"
        include_file.write_text("entity_id: light.test_entity\n")

        config_yaml = f"homeassistant: !include {include_file}"
        main_config = config_dir / "configuration.yaml"
        main_config.write_text(config_yaml)

        with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:

            def yaml_side_effect(path):
                if "automations.yaml" in path:
                    return []
                if "scripts.yaml" in path:
                    return {}
                if "configuration.yaml" in path:
                    return {"homeassistant": "!include " + str(include_file)}
                return None

            mock_yaml.side_effect = yaml_side_effect

            with patch("tools.entity_dependencies.load_registry") as mock_reg:
                mock_reg.side_effect = lambda name, path: self.mock_registry_data.get(
                    name, {"data": {"entries": []}}
                )

                with patch("os.path.exists", return_value=True):
                    register_entity_dependency_tools(
                        self.mock_mcp, self.config_path, "http://test", "token"
                    )

                    result = await self.mock_mcp._tools["get_entity_dependencies"](
                        "light.test_entity"
                    )

        data = json.loads(result)
        assert data["success"] is True
        assert data["summary"]["templates_count"] >= 1
        includes = [t for t in data["used_in"]["templates"] if t.get("type") == "include"]
        assert len(includes) >= 1

    @pytest.mark.asyncio
    async def test_include_files_not_found(self):
        """!include files that don't exist should be safely skipped."""
        from pathlib import Path

        config_dir = Path(self.config_path)
        main_config = config_dir / "configuration.yaml"
        main_config.write_text("homeassistant: !include /nonexistent/file.yaml\n")

        with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:

            def yaml_side_effect(path):
                if "automations.yaml" in path:
                    return []
                if "scripts.yaml" in path:
                    return {}
                if "configuration.yaml" in path:
                    return {"homeassistant": "!include /nonexistent/file.yaml"}
                return None

            mock_yaml.side_effect = yaml_side_effect

            with patch("tools.entity_dependencies.load_registry") as mock_reg:
                mock_reg.side_effect = lambda name, path: self.mock_registry_data.get(
                    name, {"data": {"entries": []}}
                )

                register_entity_dependency_tools(
                    self.mock_mcp, self.config_path, "http://test", "token"
                )

                result = await self.mock_mcp._tools["get_entity_dependencies"]("light.test_entity")

        data = json.loads(result)
        assert data["success"] is True


class TestDetailLevelFull:
    """Tests for detail_level='full' enrichment."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

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

    @pytest.mark.asyncio
    async def test_detail_full_adds_file_path_and_context(self):
        """detail_level='full' should add file_path, line, context_lines, object_path."""
        # Write actual automations.yaml so _find_entity_lines_in_file works
        from pathlib import Path

        auto_path = Path(self.config_path) / "automations.yaml"
        auto_path.write_text(self.automations_yaml, encoding="utf-8")
        (Path(self.config_path) / "scripts.yaml").write_text("{}", encoding="utf-8")

        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:
                import yaml

                def yaml_side_effect(path):
                    if "automations.yaml" in str(path):
                        return yaml.safe_load(self.automations_yaml)
                    if "scripts.yaml" in str(path):
                        return {}
                    return None

                mock_yaml.side_effect = yaml_side_effect

                with patch("os.path.exists", return_value=True):
                    # Mock make_ha_request for entity_exists
                    with patch("tools.entity_dependencies.make_ha_request") as mock_req:
                        mock_req.return_value = {"success": True, "data": {}}

                        register_entity_dependency_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["get_entity_dependencies"](
                            "switch.socket_pc",
                            "full",
                            True,
                        )

        data = json.loads(result)
        assert data["success"] is True
        assert data["entity_exists"] is True
        assert data["total_references"] >= 1
        autos = data["used_in"]["automations"]
        assert len(autos) >= 1
        assert "file_path" in autos[0]
        assert autos[0]["file_path"] == "automations.yaml"
        assert "context" in autos[0]  # object_path alias
        assert "line" in autos[0]

    @pytest.mark.asyncio
    async def test_detail_summary_backward_compat(self):
        """Default detail_level='summary' should produce identical structure (no file_path/line)."""
        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:
                import yaml

                def yaml_side_effect(path):
                    if "automations.yaml" in path:
                        return yaml.safe_load(self.automations_yaml)
                    if "scripts.yaml" in path:
                        return {}
                    return None

                mock_yaml.side_effect = yaml_side_effect

                with patch("os.path.exists", return_value=True):
                    with patch("tools.entity_dependencies.make_ha_request") as mock_req:
                        mock_req.return_value = {"success": False, "error": "not found"}

                        register_entity_dependency_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["get_entity_dependencies"](
                            "switch.socket_pc"
                        )

        data = json.loads(result)
        assert data["success"] is True
        assert "entity_exists" in data
        assert "total_references" in data
        autos = data["used_in"]["automations"]
        assert len(autos) >= 1
        # summary mode should NOT have file_path
        assert "file_path" not in autos[0]
        assert "line" not in autos[0]


class TestEntityExists:
    """Tests for entity_exists field."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

    @pytest.mark.asyncio
    async def test_entity_exists_true(self):
        """entity_exists should be True when API returns success."""
        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file", return_value=[]):
                with patch("os.path.exists", return_value=False):
                    with patch("tools.entity_dependencies.make_ha_request") as mock_req:
                        mock_req.return_value = {"success": True, "data": {"state": "on"}}

                        register_entity_dependency_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )
                        result = await self.mock_mcp._tools["get_entity_dependencies"](
                            "sensor.test_sensor"
                        )

        data = json.loads(result)
        assert data["success"] is True
        assert data["entity_exists"] is True

    @pytest.mark.asyncio
    async def test_entity_exists_false(self):
        """entity_exists should be False when API returns error/404."""
        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file", return_value=[]):
                with patch("os.path.exists", return_value=False):
                    with patch("tools.entity_dependencies.make_ha_request") as mock_req:
                        mock_req.return_value = {
                            "success": False,
                            "error": "HTTP 404: Not Found",
                        }

                        register_entity_dependency_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )
                        result = await self.mock_mcp._tools["get_entity_dependencies"](
                            "sensor.nonexistent"
                        )

        data = json.loads(result)
        assert data["success"] is True
        assert data["entity_exists"] is False

    @pytest.mark.asyncio
    async def test_entity_exists_no_ha_config(self):
        """entity_exists should be False when ha_url/ha_token are missing."""
        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file", return_value=[]):
                with patch("os.path.exists", return_value=False):
                    register_entity_dependency_tools(
                        self.mock_mcp,
                        self.config_path,
                        None,
                        None,  # type: ignore[arg-type]
                    )
                    result = await self.mock_mcp._tools["get_entity_dependencies"](
                        "sensor.test_sensor"
                    )

        data = json.loads(result)
        assert data["success"] is True
        assert data["entity_exists"] is False


class TestEntityInPackagesFull:
    """Tests for entity found in !include packages with detail_level='full'."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

    @pytest.mark.asyncio
    async def test_include_file_with_full_detail(self):
        """Entity in !include file should have file_path in full mode."""
        from pathlib import Path

        config_dir = Path(self.config_path)
        include_dir = config_dir / "packages"
        include_dir.mkdir(parents=True, exist_ok=True)
        include_file = include_dir / "heating.yaml"
        include_file.write_text("entity_id: light.test_entity\n")

        config_yaml = f"homeassistant: !include {include_file}"
        main_config = config_dir / "configuration.yaml"
        main_config.write_text(config_yaml)

        with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:

            def yaml_side_effect(path):
                if "automations.yaml" in path:
                    return []
                if "scripts.yaml" in path:
                    return {}
                if "configuration.yaml" in path:
                    return {"homeassistant": "!include " + str(include_file)}
                return None

            mock_yaml.side_effect = yaml_side_effect

            with patch("tools.entity_dependencies.load_registry") as mock_reg:
                mock_reg.side_effect = lambda name, path: self.mock_registry_data.get(
                    name, {"data": {"entries": []}}
                )

                with patch("os.path.exists", return_value=True):
                    with patch("tools.entity_dependencies.make_ha_request") as mock_req:
                        mock_req.return_value = {"success": True, "data": {}}

                        register_entity_dependency_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["get_entity_dependencies"](
                            "light.test_entity",
                            "full",
                            True,
                        )

        data = json.loads(result)
        assert data["success"] is True
        includes = [t for t in data["used_in"]["templates"] if t.get("type") == "include"]
        assert len(includes) >= 1
        assert "file_path" in includes[0]


class TestExceptionHandler:
    """Template 14: Every except Exception block must be tested."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.ha_url = ha_url
        self.ha_token = ha_token

    @pytest.mark.asyncio
    async def test_get_entity_dependencies_exception_handler(self):
        """_do_get_entity_dependencies raising RuntimeError should return success=False."""
        with patch(
            "tools.entity_dependencies._do_get_entity_dependencies",
            side_effect=RuntimeError("Simulated failure"),
        ):
            register_entity_dependency_tools(
                self.mock_mcp, self.config_path, self.ha_url, self.ha_token
            )
            result = await self.mock_mcp._tools["get_entity_dependencies"]("sensor.test")

        data = json.loads(result)
        assert data["success"] is False
        assert "Simulated failure" in data["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
