"""
Tests for tools/config.py
"""

import json
from unittest.mock import patch

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


# ============================================================
# Additional tests for uncovered areas
# ============================================================

SERVICE_FILTER_YAML = """
- alias: "Service Test"
  trigger: []
  action:
    - service: "notify.mobile"
      data:
        message: "Hello"
"""


PLATFORM_FILTER_YAML = """
- alias: "Platform Test"
  trigger:
    - platform: "numeric_state"
      entity_id: "sensor.temp"
      above: 25
"""


@pytest.fixture
def config_path_service(tmp_path) -> str:
    (tmp_path / "automations.yaml").write_text(SERVICE_FILTER_YAML, encoding="utf-8")
    return str(tmp_path)


@pytest.fixture
def config_path_platform(tmp_path) -> str:
    (tmp_path / "automations.yaml").write_text(PLATFORM_FILTER_YAML, encoding="utf-8")
    return str(tmp_path)


class TestSearchConfigByParamsExtended:
    def test_search_by_service(self, mock_mcp, config_path_service):
        """Search by service field value."""
        register_config_tools(mock_mcp, config_path_service)
        data = json.loads(mock_mcp._tools["search_config_by_params"](service="notify.mobile"))
        assert data["success"] is True
        assert data["summary"]["files_with_matches"] >= 1

    def test_search_by_platform(self, mock_mcp, config_path_platform):
        """Search by platform field value."""
        register_config_tools(mock_mcp, config_path_platform)
        data = json.loads(mock_mcp._tools["search_config_by_params"](platform="numeric_state"))
        assert data["success"] is True
        assert data["summary"]["files_with_matches"] >= 1

    def test_search_by_device_class(self, mock_mcp, tmp_path):
        """Search by device_class field."""
        (tmp_path / "test.yaml").write_text(
            '- platform: "mqtt"\n  device_class: "temperature"\n  name: "Test"\n',
            encoding="utf-8",
        )
        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(mock_mcp._tools["search_config_by_params"](device_class="temperature"))
        assert data["success"] is True
        assert data["summary"]["files_with_matches"] >= 1


class TestReadConfigFileExtended:
    def test_read_with_offset(self, mock_mcp, tmp_path):
        """Read file starting from a non-default offset."""
        content = "line1\nline2\nline3\nline4\nline5\n"
        (tmp_path / "test.yaml").write_text(content, encoding="utf-8")
        register_config_tools(mock_mcp, str(tmp_path))
        result = mock_mcp._tools["read_config_file"]("test.yaml", offset=3)
        assert "line3" in result
        assert "line1" not in result

    def test_read_with_offset_and_limit(self, mock_mcp, tmp_path):
        """Read file with offset and max_lines limit."""
        content = "\n".join(f"line{i}" for i in range(1, 11)) + "\n"
        (tmp_path / "test.yaml").write_text(content, encoding="utf-8")
        register_config_tools(mock_mcp, str(tmp_path))
        result = mock_mcp._tools["read_config_file"]("test.yaml", offset=5, max_lines=2)
        assert "line5" in result
        assert "line6" in result
        assert "line7" not in result


class TestSearchInConfigExtended:
    def test_search_json_file_type(self, mock_mcp, tmp_path):
        """Search only in JSON files."""
        (tmp_path / "data.json").write_text('{"my_setting": "find_me"}', encoding="utf-8")
        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(mock_mcp._tools["search_in_config"]("find_me", file_types="json"))
        assert data["success"] is True
        assert data["summary"]["files_matching_criteria"] >= 1

    def test_search_all_file_types(self, mock_mcp, tmp_path):
        """Search across both YAML and JSON files."""
        (tmp_path / "sensor.yaml").write_text("needle: found_in_yaml", encoding="utf-8")
        (tmp_path / "data.json").write_text('{"needle": "found_in_json"}', encoding="utf-8")
        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(mock_mcp._tools["search_in_config"]("needle", file_types="all"))
        assert data["success"] is True
        matching = data["summary"]["files_matching_criteria"]
        assert matching >= 1

    def test_search_in_config_delegates_to_batch(self, mock_mcp, tmp_path):
        """search_in_config delegates to search_in_config_batch."""
        (tmp_path / "example.yaml").write_text("hello: world", encoding="utf-8")
        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(mock_mcp._tools["search_in_config"]("world"))
        assert data["success"] is True
        assert data["match_mode"] == "any"


class TestSearchInConfigBatchExtended:
    """Additional tests for search_in_config_batch()."""

    def test_batch_with_all_match_mode(self, mock_mcp, tmp_path):
        """match_mode='all' should only return files containing ALL terms."""
        (tmp_path / "file_a.yaml").write_text("alpha: one\nbeta: two\n", encoding="utf-8")
        (tmp_path / "file_b.yaml").write_text("alpha: one\n", encoding="utf-8")
        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(
            mock_mcp._tools["search_in_config_batch"](search_terms="alpha, beta", match_mode="all")
        )
        assert data["success"] is True
        # Only file_a.yaml should match (has both alpha and beta)
        matching = data["matching_files"]
        filenames = [m.get("file", "") for m in matching]
        assert any("file_a" in f for f in filenames)
        assert not any("file_b" in f for f in filenames)

    def test_batch_no_terms_returns_error(self, mock_mcp, tmp_path):
        """Empty search terms should return error."""
        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(mock_mcp._tools["search_in_config_batch"](search_terms=""))
        assert data["success"] is False

    def test_batch_json_file_type(self, mock_mcp, tmp_path):
        """Search only in JSON files."""
        (tmp_path / "data.json").write_text('{"key": "findme"}', encoding="utf-8")
        (tmp_path / "data.yaml").write_text("key: findme", encoding="utf-8")
        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(
            mock_mcp._tools["search_in_config_batch"](search_terms="findme", file_types="json")
        )
        assert data["success"] is True
        matching = data["matching_files"]
        filenames = [m.get("file", "") for m in matching]
        assert any("data.json" in f for f in filenames)
        assert not any("data.yaml" in f for f in filenames)


class TestGetConfigStructureExtended:
    """Additional tests for get_config_structure()."""

    def test_structure_with_subdirectories(self, mock_mcp, tmp_path):
        """Config directory with nested subdirectories."""
        (tmp_path / "configuration.yaml").write_text("homeassistant:", encoding="utf-8")
        sub = tmp_path / "packages"
        sub.mkdir()
        (sub / "sensors.yaml").write_text("sensor:", encoding="utf-8")
        nested = sub / "nested"
        nested.mkdir()
        (nested / "automations.yaml").write_text("automation:", encoding="utf-8")

        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(mock_mcp._tools["get_config_structure"]())
        assert data["success"] is True
        assert "structure" in data
        assert len(data["structure"]) >= 2  # root + packages dirs

    def test_structure_empty_dir(self, mock_mcp, tmp_path):
        """Empty config directory should still return structure."""
        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(mock_mcp._tools["get_config_structure"]())
        assert data["success"] is True
        assert "structure" in data


class TestValidateYamlExtended:
    """Additional tests for validate_yaml_syntax()."""

    def test_validate_with_entity_checking(self, mock_mcp, config_path):
        """validate_yaml_syntax with check_entities_services=True."""
        register_config_tools(mock_mcp, config_path, "http://test", "token")
        yaml_content = """
        - alias: "Test"
          trigger:
            - platform: "state"
              entity_id: "light.kitchen"
              to: "on"
          action:
            - service: "light.turn_on"
              data:
                entity_id: "light.kitchen"
        """

        with patch("tools.config.make_ha_request") as mock_req:
            mock_req.side_effect = [
                {
                    "success": True,
                    "data": [
                        {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
                    ],
                },
                {
                    "success": True,
                    "data": [
                        {
                            "domain": "light",
                            "services": {"turn_on": {}, "turn_off": {}},
                        }
                    ],
                },
            ]

            result = mock_mcp._tools["validate_yaml_syntax"](
                yaml_content=yaml_content,
                check_entities_services=True,
            )
            data = json.loads(result)

        assert data["success"] is True
        assert data["syntax_valid"] is True

    def test_validate_file_not_found(self, mock_mcp, config_path):
        """validate_yaml_syntax with nonexistent file path."""
        register_config_tools(mock_mcp, config_path)
        data = json.loads(
            mock_mcp._tools["validate_yaml_syntax"](file_path="nonexistent_file.yaml")
        )
        assert data["success"] is False
        assert "not found" in data["error"]


# ============================================================
# Tests for get_lovelace_entity_usage() (lines 699-775)
# ============================================================

MOCK_DASHBOARD_REGISTRY = {
    "data": {
        "items": [
            {"url_path": "lovelace", "title": "Home"},
        ]
    }
}

MOCK_DASHBOARD_CONFIG_BASE = {
    "data": {
        "config": {
            "views": [
                {
                    "title": "Main View",
                    "cards": [
                        {"type": "tile", "entity": "light.test_entity"},
                        {
                            "type": "entities",
                            "entities": [
                                "light.test_other",
                                {"entity": "light.test_entity"},
                            ],
                        },
                    ],
                }
            ]
        }
    }
}


class TestLovelaceEntityUsage:
    def test_entity_found_in_main_card(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)

        dashboard_config = json.loads(json.dumps(MOCK_DASHBOARD_CONFIG_BASE))

        with patch("tools.config.load_registry") as mock_load:
            mock_load.side_effect = [
                MOCK_DASHBOARD_REGISTRY,
                dashboard_config,
            ]

            result = mock_mcp._tools["get_lovelace_entity_usage"](entity_id="light.test_entity")
            data = json.loads(result)

        assert data["success"] is True
        assert data["entity_id"] == "light.test_entity"
        assert data["usage_count"] >= 1
        roles = [u["role"] for u in data["usage"]]
        assert "main_entity" in roles

    def test_entity_found_in_entities_list(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)

        dashboard_config = {
            "data": {
                "config": {
                    "views": [
                        {
                            "title": "List View",
                            "cards": [
                                {
                                    "type": "entities",
                                    "entities": [
                                        "light.test_entity",
                                        "sensor.other",
                                    ],
                                }
                            ],
                        }
                    ]
                }
            }
        }

        with patch("tools.config.load_registry") as mock_load:
            mock_load.side_effect = [
                MOCK_DASHBOARD_REGISTRY,
                dashboard_config,
            ]

            result = mock_mcp._tools["get_lovelace_entity_usage"](entity_id="light.test_entity")
            data = json.loads(result)

        assert data["success"] is True
        roles = [u["role"] for u in data["usage"]]
        assert "entities_list" in roles

    def test_entity_not_found(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)

        dashboard_config = {
            "data": {
                "config": {
                    "views": [
                        {
                            "title": "Other View",
                            "cards": [
                                {
                                    "type": "tile",
                                    "entity": "light.other_entity",
                                }
                            ],
                        }
                    ]
                }
            }
        }

        with patch("tools.config.load_registry") as mock_load:
            mock_load.side_effect = [
                MOCK_DASHBOARD_REGISTRY,
                dashboard_config,
            ]

            result = mock_mcp._tools["get_lovelace_entity_usage"](entity_id="light.nonexistent")
            data = json.loads(result)

        assert data["success"] is True
        assert data["usage_count"] == 0
        assert data["usage"] == []

    def test_entity_in_badge(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)

        dashboard_config = {
            "data": {
                "config": {
                    "views": [
                        {
                            "title": "Badge View",
                            "cards": [
                                {
                                    "type": "custom:badge-card",
                                    "entity": "light.test_entity",
                                    "badge": True,
                                }
                            ],
                        }
                    ]
                }
            }
        }

        with patch("tools.config.load_registry") as mock_load:
            mock_load.side_effect = [
                MOCK_DASHBOARD_REGISTRY,
                dashboard_config,
            ]

            result = mock_mcp._tools["get_lovelace_entity_usage"](entity_id="light.test_entity")
            data = json.loads(result)

        assert data["success"] is True
        assert data["usage_count"] >= 1
        assert data["usage"][0]["entity_id"] == "light.test_entity"

    def test_correct_registry_key_used(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)

        dashboard_config = json.loads(json.dumps(MOCK_DASHBOARD_CONFIG_BASE))

        with patch("tools.config.load_registry") as mock_load:
            mock_load.side_effect = [
                MOCK_DASHBOARD_REGISTRY,
                dashboard_config,
            ]

            result = mock_mcp._tools["get_lovelace_entity_usage"](entity_id="light.test_entity")
            data = json.loads(result)

        assert data["success"] is True
        call_args = [c.args[0] for c in mock_load.call_args_list]
        assert "lovelace_dashboards" in call_args

    def test_missing_registry_file_returns_error(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path)

        with patch("tools.config.load_registry") as mock_load:
            mock_load.return_value = {}

            result = mock_mcp._tools["get_lovelace_entity_usage"](entity_id="light.test_entity")
            data = json.loads(result)

        assert data["success"] is False
        assert "lovelace_dashboards" in data["error"]
        assert data["_meta"]["registry_path"] == ".storage/lovelace_dashboards"
        call_args = [c.args[0] for c in mock_load.call_args_list]
        assert "lovelace_dashboards" in call_args


# ============================================================
# Tests for template validation in validate_yaml_syntax()
# (lines 676-691)
# ============================================================


class TestValidateYamlTemplateValidation:
    def test_template_validation_active(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path, "http://test", "token")

        yaml_content = """- alias: Template Test
  trigger: []
  action:
    - service: notify.mobile
      data:
        message: "{{ states('sensor.temp') }}" """

        with patch("tools.config.make_ha_request") as mock_req:
            mock_req.side_effect = [
                {
                    "success": True,
                    "data": [
                        {"entity_id": "sensor.temp", "state": "22.5", "attributes": {}},
                    ],
                },
                {
                    "success": True,
                    "data": [
                        {
                            "domain": "notify",
                            "services": {"mobile": {}},
                        }
                    ],
                },
                {
                    "success": True,
                    "data": {"result": "22.5"},
                },
            ]

            result = mock_mcp._tools["validate_yaml_syntax"](
                yaml_content=yaml_content,
                check_entities_services=True,
                check_templates=True,
            )
            data = json.loads(result)

        assert data["success"] is True
        assert data["syntax_valid"] is True

    def test_template_validation_error(self, mock_mcp, config_path):
        register_config_tools(mock_mcp, config_path, "http://test", "token")

        yaml_content = """- alias: Bad Template Test
  trigger: []
  action:
    - service: notify.mobile
      data:
        message: "{{ states('bad.sensor' | invalid_filter()) }}" """

        with patch("tools.config.make_ha_request") as mock_req:
            mock_req.side_effect = [
                {
                    "success": True,
                    "data": [],
                },
                {
                    "success": True,
                    "data": [
                        {"domain": "notify", "services": {"mobile": {}}},
                    ],
                },
                {
                    "success": False,
                    "error": "TemplateSyntaxError: unexpected token",
                },
            ]

            result = mock_mcp._tools["validate_yaml_syntax"](
                yaml_content=yaml_content,
                check_entities_services=True,
                check_templates=True,
            )
            data = json.loads(result)

        assert data["success"] is True
        assert data["syntax_valid"] is True
        template_issues = [i for i in data["issues"] if i["type"] == "template_error"]
        assert len(template_issues) >= 1


# ============================================================
# Tests for _sanitize_config() list handling (line 58)
# ============================================================


class TestSanitizeConfigList:
    def test_sanitize_list_items(self, mock_mcp, tmp_path):
        config_content = """
- server: "main"
  password: "secret123"
- server: "backup"
  token: "abc-token"
        """
        (tmp_path / "configuration.yaml").write_text(
            "sections:" + config_content.replace("\n", "\n  "),
            encoding="utf-8",
        )
        register_config_tools(mock_mcp, str(tmp_path))
        result = mock_mcp._tools["get_main_configuration"]()

        assert "secret123" not in result
        assert "abc-token" not in result
        assert "REDACTED" in result

    def test_sanitize_nested_list_dicts(self, mock_mcp, tmp_path):
        config_content = """
section:
  - name: "test1"
    config:
      api_key: "key123"
  - name: "test2"
    config:
      password: "pw456"
        """
        (tmp_path / "configuration.yaml").write_text(config_content, encoding="utf-8")
        register_config_tools(mock_mcp, str(tmp_path))
        result = mock_mcp._tools["get_main_configuration"]()

        assert "key123" not in result
        assert "pw456" not in result
        assert "REDACTED" in result


class TestSearchConfigByParamsExtended2:
    def test_search_config_by_params_with_file_pattern(self, mock_mcp, tmp_path):
        """file_pattern should restrict search to only matching files."""
        (tmp_path / "sensors.yaml").write_text(
            "- platform: mqtt\n  entity_id: sensor.match_me\n", encoding="utf-8"
        )
        (tmp_path / "lights.yaml").write_text(
            "- platform: mqtt\n  entity_id: light.no_match\n", encoding="utf-8"
        )
        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(
            mock_mcp._tools["search_config_by_params"](
                entity_id="sensor.match_me", file_pattern="sensors*"
            )
        )
        assert data["success"] is True
        matching = data["results"]
        filenames = [r.get("file", "") for r in matching]
        assert any("sensors" in f for f in filenames)
        assert not any("lights" in f for f in filenames)

    def test_search_config_by_params_entity_id_match(self, mock_mcp, tmp_path):
        """Specific entity_id should be found in config files."""
        (tmp_path / "automations.yaml").write_text(
            "- alias: Test\n"
            "  trigger: []\n"
            "  action:\n"
            "    - service: light.turn_on\n"
            "      data:\n"
            "        entity_id: light.unique_k4x9\n",
            encoding="utf-8",
        )
        register_config_tools(mock_mcp, str(tmp_path))
        data = json.loads(mock_mcp._tools["search_config_by_params"](entity_id="light.unique_k4x9"))
        assert data["success"] is True
        results = data["results"]
        assert len(results) >= 1
        assert any("light.unique_k4x9" in str(r) for r in results)
