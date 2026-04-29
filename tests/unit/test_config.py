"""
Tests for tools/config.py
"""

import json

import pytest

from tools.config import register_config_tools


@pytest.fixture
def config_path(tmp_path) -> str:
    # We create structure
    (tmp_path / "configuration.yaml").write_text("homeassistant:\n  name: Home\n", encoding="utf-8")
    (tmp_path / "automations.yaml").write_text(
        "- alias: 'Test Auto'\n  trigger: []\n", encoding="utf-8"
    )
    (tmp_path / "custom_components").mkdir()
    (tmp_path / "custom_components/hacs").mkdir()
    (tmp_path / "custom_components/hacs/manifest.json").write_text(
        '{"domain": "hacs", "version": "1.0"}', encoding="utf-8"
    )
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


class TestGetMainConfiguration:
    def test_get_config(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_main_configuration"]
        result = tool()

        # Result is YAML string
        assert "homeassistant:" in result
        assert "name: Home" in result

    def test_sensitive_data_redacted(self, mock_mcp, tmp_path):
        """Passwords and tokens must be redacted."""
        (tmp_path / "configuration.yaml").write_text(
            "http:\n  api_password: mysecret\n  token: abc123\n",
            encoding="utf-8",
        )
        register_config_tools(mock_mcp, str(tmp_path))
        result = mock_mcp._tools["get_main_configuration"]()
        assert "mysecret" not in result
        assert "abc123" not in result
        assert "REDACTED" in result

    def test_missing_config_file(self, mock_mcp, tmp_path):
        """Missing configuration.yaml should return a JSON error."""
        register_config_tools(mock_mcp, str(tmp_path))
        result = mock_mcp._tools["get_main_configuration"]()
        data = json.loads(result)
        assert data["success"] is False


class TestSearchInConfig:
    def test_search_batch(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["search_in_config_batch"]
        result = tool(search_terms="Home, Test Auto")
        data = json.loads(result)

        assert data["success"] is True
        assert data["summary"]["files_matching_criteria"] >= 1
        assert "configuration.yaml" in str(data["matching_files"]) or "automations.yaml" in str(
            data["matching_files"]
        )


class TestValidateYaml:
    def test_validate_valid_yaml(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["validate_yaml_syntax"]
        result = tool(yaml_content="test: true\nlist:\n  - item1")
        data = json.loads(result)

        assert data["success"] is True
        assert data["syntax_valid"] is True
        assert len(data["issues"]) == 0

    def test_validate_invalid_yaml(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["validate_yaml_syntax"]
        result = tool(yaml_content="test: [unclosed list")
        data = json.loads(result)

        assert data["success"] is False
        assert data["syntax_valid"] is False
        assert "YAML syntax error" in data["error"]

    def test_validate_via_file_path(self, mock_mcp, config_path):
        """Validation should work when a file_path is given instead of yaml_content."""
        register_config_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["validate_yaml_syntax"](file_path="configuration.yaml"))
        assert data["success"] is True
        assert data["syntax_valid"] is True

    def test_validate_no_params_returns_error(self, mock_mcp, config_path):
        """Calling without any argument should return an error."""
        register_config_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["validate_yaml_syntax"]())
        assert data["success"] is False


class TestListCustomComponents:
    def test_list_components(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["list_custom_components"]
        result = tool()
        data = json.loads(result)

        assert data["success"] is True
        assert data["total_custom_components"] == 1
        assert data["components"][0]["domain"] == "hacs"

    def test_no_custom_components_dir(self, mock_mcp, tmp_path):
        """Missing custom_components dir should return a JSON error."""
        (tmp_path / "configuration.yaml").write_text("", encoding="utf-8")
        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(mock_mcp._tools["list_custom_components"]())
        assert data["success"] is False


class TestListThemes:
    def test_list_themes(self, mock_mcp, tmp_path):
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()
        (themes_dir / "my_theme.yaml").write_text(
            "my_theme:\n  primary-color: '#fff'\n", encoding="utf-8"
        )
        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(mock_mcp._tools["list_themes"]())
        assert data["success"] is True
        assert data["total_theme_files"] == 1
        assert "my_theme" in data["themes"][0]["theme_names"]

    def test_no_themes_dir(self, mock_mcp, tmp_path):
        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(mock_mcp._tools["list_themes"]())
        assert data["success"] is False


class TestGetConfigStructure:
    def test_returns_structure(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["get_config_structure"]())
        assert data["success"] is True
        assert "structure" in data
        assert len(data["structure"]) > 0


class TestReadConfigFile:
    def test_read_existing_file(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)
        result = mock_mcp._tools["read_config_file"]("configuration.yaml")
        assert "homeassistant:" in result

    def test_path_traversal_blocked(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["read_config_file"]("../../../etc/passwd"))
        assert data["success"] is False
        assert "Access denied" in data["error"]

    def test_file_not_found(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["read_config_file"]("nonexistent.yaml"))
        assert data["success"] is False
        assert "not found" in data["error"]

    def test_large_file_truncated(self, mock_mcp, tmp_path):
        large = "# " + ("x" * 1024 + "\n") * 210  # >200 KB
        (tmp_path / "big.yaml").write_text(large, encoding="utf-8")
        register_config_tools(mock_mcp, str(tmp_path))
        result = mock_mcp._tools["read_config_file"]("big.yaml")
        assert "too large" in result.lower() or len(result) < len(large)


class TestSearchConfigByParams:
    def test_search_by_entity_id(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["search_config_by_params"](entity_id="light.room"))
        assert data["success"] is True
        assert "files_searched" in data["summary"]

    def test_no_params_returns_error(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["search_config_by_params"]())
        assert data["success"] is False
