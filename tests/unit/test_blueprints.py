"""
Tests for tools/blueprints.py
"""

import json

import pytest

from tools.blueprints import register_blueprint_tools


@pytest.fixture
def config_path(tmp_path) -> str:
    # Setup directory structure
    bp_dir = tmp_path / "blueprints"
    (bp_dir / "automation").mkdir(parents=True)
    (bp_dir / "script").mkdir(parents=True)

    # Create dummy blueprint
    bp_content = """
blueprint:
  name: Motion Light
  description: Turn on light on motion
  domain: automation
  input:
    motion_sensor:
    light_target:
"""
    (bp_dir / "automation/motion_light.yaml").write_text(bp_content, encoding="utf-8")

    # Create automation using blueprint
    auto_content = """
- id: '123'
  alias: Living Room Motion
  use_blueprint:
    path: automation/motion_light.yaml
    input:
      motion_sensor: binary_sensor.motion
      light_target: light.living_room
"""
    (tmp_path / "automations.yaml").write_text(auto_content, encoding="utf-8")

    return str(tmp_path)


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


class TestListBlueprints:
    def test_list_blueprints(self, mock_mcp, config_path):
        register_blueprint_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["list_blueprints"]
        result = tool()
        data = json.loads(result)

        assert data["success"] is True
        assert data["total_blueprints"] == 1
        assert data["blueprints"][0]["name"] == "Motion Light"
        assert data["blueprints"][0]["path"] == "automation/motion_light.yaml"

    def test_list_blueprints_no_directory(self, mock_mcp, tmp_path):
        """No blueprints dir should return a clear error."""
        register_blueprint_tools(mock_mcp, str(tmp_path))

        tool = mock_mcp._tools["list_blueprints"]
        data = json.loads(tool())

        assert data["success"] is False
        assert "not found" in data["error"]

    def test_list_blueprints_invalid_yaml(self, mock_mcp, tmp_path):
        """Blueprint file with broken YAML should still be listed with an error entry."""
        bp_dir = tmp_path / "blueprints" / "automation"
        bp_dir.mkdir(parents=True)
        (bp_dir / "broken.yaml").write_text("invalid: [unclosed", encoding="utf-8")
        (tmp_path / "automations.yaml").write_text("[]", encoding="utf-8")

        register_blueprint_tools(mock_mcp, str(tmp_path))
        tool = mock_mcp._tools["list_blueprints"]
        data = json.loads(tool())

        assert data["success"] is True
        broken = next(b for b in data["blueprints"] if "broken" in b["path"])
        assert "error" in broken

    def test_list_blueprints_script_type(self, mock_mcp, tmp_path):
        """Script-domain blueprints should be included and typeeed correctly."""
        bp_dir = tmp_path / "blueprints" / "script"
        bp_dir.mkdir(parents=True)
        (bp_dir / "my_script.yaml").write_text(
            "blueprint:\n  name: My Script\n  domain: script\n  input: {}\n",
            encoding="utf-8",
        )
        (tmp_path / "automations.yaml").write_text("[]", encoding="utf-8")

        register_blueprint_tools(mock_mcp, str(tmp_path))
        tool = mock_mcp._tools["list_blueprints"]
        data = json.loads(tool())

        assert data["success"] is True
        assert data["by_type"]["script"] == 1
        script_bp = next(b for b in data["blueprints"] if b["type"] == "script")
        assert script_bp["name"] == "My Script"


class TestGetBlueprintCode:
    def test_get_code(self, mock_mcp, config_path):
        register_blueprint_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_blueprint_code"]
        result = tool("automation/motion_light.yaml")
        data = json.loads(result)

        assert data["success"] is True
        assert "Motion Light" in data["code"]
        assert data["truncated"] is False

    def test_path_traversal(self, mock_mcp, config_path):
        register_blueprint_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_blueprint_code"]
        result = tool("../../../etc/passwd")
        data = json.loads(result)

        assert data["success"] is False
        assert "Access denied" in data["error"]

    def test_get_code_not_found(self, mock_mcp, config_path):
        """Requesting a non-existent blueprint should return an error."""
        register_blueprint_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_blueprint_code"]
        data = json.loads(tool("automation/nonexistent.yaml"))

        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_get_code_large_file_truncated(self, mock_mcp, tmp_path):
        """Files larger than 200 KB should be returned truncated."""
        bp_dir = tmp_path / "blueprints" / "automation"
        bp_dir.mkdir(parents=True)
        large_content = "# " + ("x" * 1024 + "\n") * 210  # >200 KB
        (bp_dir / "large.yaml").write_text(large_content, encoding="utf-8")

        register_blueprint_tools(mock_mcp, str(tmp_path))
        tool = mock_mcp._tools["get_blueprint_code"]
        data = json.loads(tool("automation/large.yaml"))

        assert data["success"] is True
        assert data["truncated"] is True
        assert "warning" in data


class TestGetBlueprintInstances:
    def test_get_instances(self, mock_mcp, config_path):
        register_blueprint_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_blueprint_instances"]
        result = tool("automation/motion_light.yaml")
        data = json.loads(result)

        assert data["success"] is True
        assert data["usage_count"] == 1
        assert data["instances"][0]["alias"] == "Living Room Motion"
        assert data["instances"][0]["inputs"]["motion_sensor"] == "binary_sensor.motion"

    def test_get_instances_zero(self, mock_mcp, config_path):
        """Blueprint not referenced anywhere should return usage_count 0."""
        register_blueprint_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_blueprint_instances"]
        data = json.loads(tool("automation/unused.yaml"))

        assert data["success"] is True
        assert data["usage_count"] == 0
        assert data["instances"] == []

    def test_get_instances_from_scripts(self, mock_mcp, tmp_path):
        """Blueprint used in scripts.yaml should be counted as script instatece."""
        bp_dir = tmp_path / "blueprints" / "script"
        bp_dir.mkdir(parents=True)
        (bp_dir / "helper.yaml").write_text(
            "blueprint:\n  name: Helper\n  domain: script\n  input: {}\n",
            encoding="utf-8",
        )
        (tmp_path / "automations.yaml").write_text("[]", encoding="utf-8")
        (tmp_path / "scripts.yaml").write_text(
            "my_script:\n  alias: My Script\n  use_blueprint:\n    path: script/helper.yaml\n    input: {}\n",
            encoding="utf-8",
        )

        register_blueprint_tools(mock_mcp, str(tmp_path))
        tool = mock_mcp._tools["get_blueprint_instances"]
        data = json.loads(tool("script/helper.yaml"))

        assert data["success"] is True
        assert data["usage_count"] == 1
        assert data["instances"][0]["type"] == "script"
        assert data["summary"]["scripts"] == 1


class TestGetBlueprintUsageSummary:
    def test_usage_summary(self, mock_mcp, config_path):
        register_blueprint_tools(mock_mcp, config_path)

        # This integration test relies on previous methods working correctly.
        tool = mock_mcp._tools["get_blueprint_usage_summary"]
        result = tool()
        data = json.loads(result)

        assert data["success"] is True
        assert data["total_blueprints"] == 1
        assert data["total_instances"] == 1
        assert data["most_used"][0]["name"] == "Motion Light"
        assert data["most_used"][0]["usage_count"] == 1

    def test_usage_summary_unused_listed(self, mock_mcp, tmp_path):
        """Blueprints with no instateces should appear in the 'unused' list."""
        bp_dir = tmp_path / "blueprints" / "automation"
        bp_dir.mkdir(parents=True)
        (bp_dir / "orphan.yaml").write_text(
            "blueprint:\n  name: Orphan\n  domain: automation\n  input: {}\n",
            encoding="utf-8",
        )
        (tmp_path / "automations.yaml").write_text("[]", encoding="utf-8")

        register_blueprint_tools(mock_mcp, str(tmp_path))
        tool = mock_mcp._tools["get_blueprint_usage_summary"]
        data = json.loads(tool())

        assert data["success"] is True
        assert data["total_instances"] == 0
        assert any(u["name"] == "Orphan" for u in data["unused"])


class TestGetBlueprintInstancesExtended:
    def test_get_instances_list_format_scripts(self, mock_mcp, tmp_path):
        """Scripts in list format (not dict) should still find blueprint instances."""
        bp_dir = tmp_path / "blueprints" / "script"
        bp_dir.mkdir(parents=True)
        (bp_dir / "helper.yaml").write_text(
            "blueprint:\n  name: Helper\n  domain: script\n  input: {}\n",
            encoding="utf-8",
        )
        (tmp_path / "automations.yaml").write_text("[]", encoding="utf-8")
        (tmp_path / "scripts.yaml").write_text(
            "- alias: List Script\n  use_blueprint:\n    path: script/helper.yaml\n    input: {}\n",
            encoding="utf-8",
        )

        register_blueprint_tools(mock_mcp, str(tmp_path))
        tool = mock_mcp._tools["get_blueprint_instances"]
        data = json.loads(tool("script/helper.yaml"))

        assert data["success"] is True
        assert data["usage_count"] == 1
        assert data["instances"][0]["type"] == "script"
        assert data["instances"][0]["alias"] == "List Script"

    def test_get_instances_dict_format_automations(self, mock_mcp, tmp_path):
        """Automations.yaml in dict format should be converted to list (non-crashing)."""
        bp_dir = tmp_path / "blueprints" / "automation"
        bp_dir.mkdir(parents=True)
        (bp_dir / "motion.yaml").write_text(
            "blueprint:\n  name: Motion\n  domain: automation\n  input: {}\n",
            encoding="utf-8",
        )
        (tmp_path / "automations.yaml").write_text(
            "my_auto:\n"
            "  alias: Dict Auto\n"
            "  use_blueprint:\n"
            "    path: automation/motion.yaml\n"
            "    input: {}\n",
            encoding="utf-8",
        )

        register_blueprint_tools(mock_mcp, str(tmp_path))
        tool = mock_mcp._tools["get_blueprint_instances"]
        data = json.loads(tool("automation/motion.yaml"))

        assert data["success"] is True

    def test_get_instances_non_dict_skip(self, mock_mcp, tmp_path):
        """Non-dict items in automations list should be safely skipped."""
        bp_dir = tmp_path / "blueprints" / "automation"
        bp_dir.mkdir(parents=True)
        (bp_dir / "motion.yaml").write_text(
            "blueprint:\n  name: Motion\n  domain: automation\n  input: {}\n",
            encoding="utf-8",
        )
        (tmp_path / "automations.yaml").write_text(
            "- just_a_string_not_a_dict\n"
            "- alias: Real Auto\n"
            "  use_blueprint:\n"
            "    path: automation/motion.yaml\n"
            "    input: {}\n",
            encoding="utf-8",
        )

        register_blueprint_tools(mock_mcp, str(tmp_path))
        tool = mock_mcp._tools["get_blueprint_instances"]
        data = json.loads(tool("automation/motion.yaml"))

        assert data["success"] is True
        assert data["usage_count"] == 1
        assert data["instances"][0]["alias"] == "Real Auto"
