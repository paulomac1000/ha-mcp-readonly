"""
Tests for tools/entity_dependencies.py
"""

import json
from unittest.mock import patch

import pytest

from tests.fixtures import (
    ENTITY_ID_LIGHT,
    ENTITY_ID_SWITCH_KITCHEN,
    ENTITY_ID_SWITCH_LIVING_ROOM,
    ENTITY_ID_TEMPERATURE_SENSOR,
)
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


class TestEmptyConfig:
    """Tests for get_entity_dependencies when no config files exist."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

    @pytest.mark.asyncio
    async def test_get_entity_dependencies_empty_config(self):
        """No automations/scripts/config files → all used_in lists empty, entity_exists=False."""
        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            # Simulate no config files at all
            with patch("os.path.exists", return_value=False):
                with patch("tools.entity_dependencies.make_ha_request") as mock_req:
                    mock_req.return_value = {"success": False, "error": "not found"}

                    register_entity_dependency_tools(
                        self.mock_mcp, self.config_path, "http://test", "token"
                    )

                    result = await self.mock_mcp._tools["get_entity_dependencies"](
                        "sensor.nonexistent"
                    )

        data = json.loads(result)
        assert data["success"] is True
        assert data["used_in"]["automations"] == []
        assert data["used_in"]["scripts"] == []
        assert data["used_in"]["templates"] == []
        assert data["used_in"]["dashboards"] == []
        assert data["summary"]["total_usages"] == 0
        assert data["total_references"] == 0
        assert data["entity_exists"] is False

    @pytest.mark.asyncio
    async def test_get_entity_dependencies_include_context(self):
        """include_context=True should not crash (parameter is accepted by function signature)."""
        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file", return_value=[]):
                with patch("os.path.exists", return_value=False):
                    with patch("tools.entity_dependencies.make_ha_request") as mock_req:
                        mock_req.return_value = {"success": False, "error": "not found"}

                        register_entity_dependency_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["get_entity_dependencies"](
                            "sensor.test",
                            "summary",
                            True,  # include_context=True
                        )

        data = json.loads(result)
        assert data["success"] is True
        assert "entity_exists" in data
        assert "total_references" in data


class TestPackagesWithFullDetail:
    """Tests for entity found in !include packages with detail_level='full' and context."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

    @pytest.mark.asyncio
    async def test_get_entity_dependencies_full_detail_packages(self):
        """Entity in !include packages with detail_level=full should include file_path and line."""
        from pathlib import Path

        config_dir = Path(self.config_path)
        packages_dir = config_dir / "packages"
        packages_dir.mkdir(parents=True, exist_ok=True)
        package_file = packages_dir / "sensors.yaml"
        package_file.write_text(f"entity_id: {ENTITY_ID_TEMPERATURE_SENSOR}\n")

        main_config = config_dir / "configuration.yaml"
        main_config.write_text(f"homeassistant: !include {package_file}\n")

        with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:

            def yaml_side_effect(path):
                if "automations.yaml" in str(path):
                    return []
                if "scripts.yaml" in str(path):
                    return {}
                if "configuration.yaml" in str(path):
                    return {"homeassistant": "!include " + str(package_file)}
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
                            ENTITY_ID_TEMPERATURE_SENSOR,
                            "full",
                            True,
                        )

        data = json.loads(result)
        assert data["success"] is True
        assert data["entity_exists"] is True

        # Package include entries should have type='include'
        includes = [t for t in data["used_in"]["templates"] if t.get("type") == "include"]
        assert len(includes) >= 1

        # In full mode, include entries should carry file_path and line
        for entry in includes:
            assert "file_path" in entry
            assert "line" in entry
            assert "context_lines" in entry

    @pytest.mark.asyncio
    async def test_get_entity_dependencies_entity_not_exists_in_api(self):
        """Entity not found in HA API → entity_exists=False, but local files still scanned."""
        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file", return_value=[]):
                with patch("os.path.exists", return_value=False):
                    with patch("tools.entity_dependencies.make_ha_request") as mock_req:
                        mock_req.return_value = {
                            "success": False,
                            "error": "HTTP 404: Entity not found",
                        }

                        register_entity_dependency_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["get_entity_dependencies"](
                            "sensor.ghost_entity"
                        )

        data = json.loads(result)
        assert data["success"] is True
        assert data["entity_exists"] is False
        assert data["total_references"] == 0
        assert data["summary"]["total_usages"] == 0


class TestNonDictAutomationEntries:
    """Tests for malformed automation entries."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

    @pytest.mark.asyncio
    async def test_automation_not_a_dict_skipped(self):
        """Automations.yaml containing non-dict entries (e.g. bare strings) are skipped."""
        non_dict_yaml = f"""
- id: 'valid_one'
  alias: Valid Auto
  trigger: []
  action:
  - service: light.turn_on
    entity_id: {ENTITY_ID_LIGHT}
- just_a_string_not_a_dict
"""
        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:
                import yaml

                def yaml_side_effect(path):
                    if "automations.yaml" in str(path):
                        return yaml.safe_load(non_dict_yaml)
                    return {}

                mock_yaml.side_effect = yaml_side_effect

                with patch("os.path.exists", return_value=True):
                    with patch("tools.entity_dependencies.make_ha_request") as mock_req:
                        mock_req.return_value = {"success": False, "error": "not found"}

                        register_entity_dependency_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["get_entity_dependencies"](
                            ENTITY_ID_LIGHT
                        )

        data = json.loads(result)
        assert data["success"] is True
        assert data["summary"]["automations_count"] == 1


class TestScriptsFullDetail:
    """Tests for scripts.yaml full detail mode."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

    @pytest.mark.asyncio
    async def test_scripts_dict_format_full_detail(self):
        """Scripts.yaml in dict format with detail_level=full adds file_path and line."""
        from pathlib import Path

        scripts_yaml = f"""
turn_off_all:
  alias: Turn Off All
  sequence:
  - service: switch.turn_off
    entity_id: {ENTITY_ID_SWITCH_KITCHEN}
"""
        scripts_path = Path(self.config_path) / "scripts.yaml"
        scripts_path.write_text(scripts_yaml, encoding="utf-8")

        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:
                import yaml

                def yaml_side_effect(path):
                    if "automations.yaml" in str(path):
                        return []
                    if "scripts.yaml" in str(path):
                        return yaml.safe_load(scripts_yaml)
                    return None

                mock_yaml.side_effect = yaml_side_effect

                with patch("os.path.exists", return_value=True):
                    with patch("tools.entity_dependencies.make_ha_request") as mock_req:
                        mock_req.return_value = {"success": False, "error": "not found"}

                        register_entity_dependency_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["get_entity_dependencies"](
                            ENTITY_ID_SWITCH_KITCHEN, "full"
                        )

        data = json.loads(result)
        assert data["success"] is True
        scripts = data["used_in"]["scripts"]
        assert len(scripts) >= 1
        assert "file_path" in scripts[0]
        assert scripts[0]["file_path"] == "scripts.yaml"

    @pytest.mark.asyncio
    async def test_scripts_list_format_full_detail(self):
        """Scripts.yaml in list format with detail_level=full adds file_path and line."""
        from pathlib import Path

        scripts_list_yaml = f"""
- id: script_001
  alias: List Script
  sequence:
  - service: switch.turn_off
    entity_id: {ENTITY_ID_SWITCH_LIVING_ROOM}
"""
        scripts_path = Path(self.config_path) / "scripts.yaml"
        scripts_path.write_text(scripts_list_yaml, encoding="utf-8")

        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:
                import yaml

                def yaml_side_effect(path):
                    if "automations.yaml" in str(path):
                        return []
                    if "scripts.yaml" in str(path):
                        return yaml.safe_load(scripts_list_yaml)
                    return None

                mock_yaml.side_effect = yaml_side_effect

                with patch("os.path.exists", return_value=True):
                    with patch("tools.entity_dependencies.make_ha_request") as mock_req:
                        mock_req.return_value = {"success": False, "error": "not found"}

                        register_entity_dependency_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["get_entity_dependencies"](
                            ENTITY_ID_SWITCH_LIVING_ROOM, "full"
                        )

        data = json.loads(result)
        assert data["success"] is True
        scripts = data["used_in"]["scripts"]
        assert len(scripts) >= 1
        assert "file_path" in scripts[0]
        assert scripts[0]["file_path"] == "scripts.yaml"

    @pytest.mark.asyncio
    async def test_entity_not_in_any_automation(self):
        """Automations exist but entity is NOT referenced → automations_count=0."""
        auto_yaml = """
- id: 'other'
  alias: Other Auto
  trigger:
  - platform: state
    entity_id: sensor.other
  action:
  - service: light.turn_on
    entity_id: light.other
"""
        from pathlib import Path

        auto_path = Path(self.config_path) / "automations.yaml"
        auto_path.write_text(auto_yaml, encoding="utf-8")

        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:
                import yaml

                def yaml_side_effect(path):
                    if "automations.yaml" in str(path):
                        return yaml.safe_load(auto_yaml)
                    if "scripts.yaml" in str(path):
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
                            "sensor.unrelated"
                        )

        data = json.loads(result)
        assert data["success"] is True
        assert data["summary"]["automations_count"] == 0
        assert data["total_references"] == 0


class TestTemplateFullDetail:
    """Tests for template entities with detail_level=full."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, mock_registry_data):
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

    @pytest.mark.asyncio
    async def test_template_config_entry_full_detail(self):
        """Template config entry with detail_level=full adds file_path."""
        from pathlib import Path

        config_dir = Path(self.config_path)
        main_config = config_dir / "configuration.yaml"
        main_config.write_text("", encoding="utf-8")

        registry_with_template = dict(self.mock_registry_data)
        registry_with_template["core.config_entries"] = {
            "data": {
                "entries": [
                    {
                        "entry_id": "tpl_full",
                        "domain": "template",
                        "title": "Full Template",
                        "options": {"state": "{{ states('sensor.temp') }}"},
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
                        "sensor.temp", "full"
                    )

        data = json.loads(result)
        assert data["success"] is True
        templates = data["used_in"]["templates"]
        assert len(templates) >= 1
        full_entry = templates[0]
        assert "file_path" in full_entry
        assert full_entry["file_path"] == ".storage/core.config_entries"

    @pytest.mark.asyncio
    async def test_yaml_template_full_detail(self):
        """YAML template with detail_level=full adds file_path, line, context_lines."""
        from pathlib import Path

        config_dir = Path(self.config_path)
        config_content = (
            "template:\n"
            "  - sensor:\n"
            "      - name: avg_temp\n"
            "        state: \"{{ states('sensor.living_temp') }}\"\n"
        )
        (config_dir / "configuration.yaml").write_text(config_content, encoding="utf-8")

        with patch("tools.entity_dependencies.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            with patch("tools.entity_dependencies.load_yaml_file") as mock_yaml:

                def yaml_side_effect(path):
                    if "automations.yaml" in str(path):
                        return []
                    if "scripts.yaml" in str(path):
                        return {}
                    if "configuration.yaml" in str(path):
                        return {
                            "template": [
                                {
                                    "sensor": [
                                        {
                                            "name": "avg_temp",
                                            "state": "{{ states('sensor.living_temp') }}",
                                        }
                                    ]
                                }
                            ]
                        }
                    return None

                mock_yaml.side_effect = yaml_side_effect

                with patch("os.path.exists", return_value=True):
                    with patch("tools.entity_dependencies.make_ha_request") as mock_req:
                        mock_req.return_value = {"success": False, "error": "not found"}

                        register_entity_dependency_tools(
                            self.mock_mcp, self.config_path, "http://test", "token"
                        )

                        result = await self.mock_mcp._tools["get_entity_dependencies"](
                            "sensor.living_temp", "full"
                        )

        data = json.loads(result)
        assert data["success"] is True
        yaml_templates = [t for t in data["used_in"]["templates"] if t.get("type") == "yaml"]
        assert len(yaml_templates) >= 1
        yt = yaml_templates[0]
        assert "file_path" in yt
        assert yt["file_path"] == "configuration.yaml"
        assert "line" in yt
        assert "context_lines" in yt


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

    @pytest.mark.asyncio
    async def test_get_entity_consumers_exception_handler(self):
        """_do_get_entity_consumers raising RuntimeError should return success=False."""
        with patch(
            "tools.entity_dependencies._do_get_entity_consumers",
            side_effect=RuntimeError("Consumers failure"),
        ):
            register_entity_dependency_tools(
                self.mock_mcp, self.config_path, self.ha_url, self.ha_token
            )
            result = await self.mock_mcp._tools["get_entity_consumers"]("sensor.test")

        data = json.loads(result)
        assert data["success"] is False
        assert "Consumers failure" in data["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
