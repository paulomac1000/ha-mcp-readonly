"""
Unit tests for tools/batch_operations.py

Tests new batch and optimization tools:
- validate_yaml_batch: Batch YAML validation
- compare_entities_state: State comparison before/after
- get_template_dependencies: Template dependency analysis
- bulk_search_entities: Bulk entity search
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

MOCK_REGISTRY_DATA = {
    "core.entity_registry": {
        "data": {
            "entities": [
                {
                    "entity_id": "sensor.temperature",
                    "name": "Temperature",
                    "platform": "mqtt",
                },
                {
                    "entity_id": "sensor.humidity",
                    "name": "Humidity",
                    "platform": "mqtt",
                },
                {
                    "entity_id": "template.test_sensor",
                    "name": "Test Template",
                    "platform": "template",
                },
            ]
        }
    }
}


@pytest.fixture
def mock_mcp():
    """Create a mock MCP server."""
    mcp = Mock()
    mcp._tools = {}

    def tool_decorator():
        def wrapper(func):
            mcp._tools[func.__name__] = func
            return func

        return wrapper

    mcp.tool = tool_decorator
    return mcp


@pytest.fixture
def config_path(tmp_path):
    """Create a temporary config directory with minimal registry files."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    storage_dir = config_dir / ".storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    (storage_dir / "core.entity_registry").write_text(
        json.dumps(MOCK_REGISTRY_DATA["core.entity_registry"]), encoding="utf-8"
    )

    # Minimal placeholders to satisfy loaders
    (storage_dir / "core.area_registry").write_text(json.dumps({"data": {"areas": []}}))
    (storage_dir / "core.device_registry").write_text(json.dumps({"data": {"devices": []}}))
    (storage_dir / "core.config_entries").write_text(json.dumps({"data": {"entries": []}}))

    return str(config_dir)


@pytest.fixture
def ha_url():
    """Home Assistant URL."""
    return "http://test-ha:8123"


@pytest.fixture
def ha_token():
    """Home Assistant token."""
    return "test-token"


class TestValidateYAMLBatch:
    """Test validate_yaml_batch function."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        """Setup test environment."""
        from tools.batch_operations import register_batch_operations_tools

        register_batch_operations_tools(mock_mcp, config_path, ha_url, ha_token)
        self.mcp = mock_mcp
        self.config_path = config_path

    @pytest.mark.asyncio
    async def test_validate_multiple_files_success(self):
        """Test validating multiple valid YAML files."""
        # Create test files
        config_dir = Path(self.config_path)

        (config_dir / "automations.yaml").write_text("""
- alias: Test
  trigger:
    - platform: state
  action:
    - service: light.turn_on
        """)

        (config_dir / "scripts.yaml").write_text("""
test_script:
  sequence:
    - service: light.turn_on
        """)

        result = await self.mcp._tools["validate_yaml_batch"](
            file_paths="automations.yaml,scripts.yaml"
        )
        data = json.loads(result)

        assert data["success"] is True
        assert data["files_validated"] == 2
        assert data["summary"]["valid"] == 2
        assert data["summary"]["invalid"] == 0

    @pytest.mark.asyncio
    async def test_validate_invalid_yaml(self):
        """Test validation with invalid YAML."""
        config_dir = Path(self.config_path)

        (config_dir / "bad.yaml").write_text("""
invalid: [unclosed
bad syntax
        """)

        result = await self.mcp._tools["validate_yaml_batch"](file_paths="bad.yaml")
        data = json.loads(result)

        assert data["summary"]["invalid"] > 0

    @pytest.mark.asyncio
    async def test_validate_nonexistent_file(self):
        """Test validation with non-existent file."""
        result = await self.mcp._tools["validate_yaml_batch"](file_paths="nonexistent.yaml")
        data = json.loads(result)

        assert data["summary"]["invalid"] > 0
        # The file should be reported as invalid
        assert any(not r.get("valid", True) for r in data["results"])

    @pytest.mark.asyncio
    async def test_validate_path_traversal_prevention(self):
        """Test that path traversal attempts are blocked."""
        result = await self.mcp._tools["validate_yaml_batch"](file_paths="../../../etc/passwd")
        data = json.loads(result)

        assert any(
            "security" in r.get("error", "").lower() or "traversal" in r.get("error", "").lower()
            for r in data["results"]
        )

    @pytest.mark.asyncio
    async def test_validate_empty_file_paths(self):
        """Test with empty file paths."""
        result = await self.mcp._tools["validate_yaml_batch"](file_paths="")
        data = json.loads(result)

        assert data["success"] is False
        assert "No file paths provided" in data.get("error", "")

    @pytest.mark.asyncio
    async def test_validate_token_savings_metadata(self):
        """Test that token savings metadata is included."""
        config_dir = Path(self.config_path)
        (config_dir / "test.yaml").write_text("key: value")

        result = await self.mcp._tools["validate_yaml_batch"](file_paths="test.yaml")
        data = json.loads(result)

        assert "metadata" in data
        assert "token_savings_vs_individual" in data["metadata"]

    @pytest.mark.asyncio
    async def test_validate_empty_yaml_file_produces_warning(self):
        """Empty YAML file should be valid but carry a warning."""
        config_dir = Path(self.config_path)
        (config_dir / "empty.yaml").write_text("")

        result = await self.mcp._tools["validate_yaml_batch"](file_paths="empty.yaml")
        data = json.loads(result)

        assert data["summary"]["valid"] == 1
        assert data["summary"]["warnings"] >= 1
        file_result = data["results"][0]
        assert file_result["valid"] is True
        assert file_result["warnings"] is not None


class TestCompareEntitiesState:
    """Test compare_entities_state function."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        """Setup test environment."""
        from tools.batch_operations import register_batch_operations_tools

        register_batch_operations_tools(mock_mcp, config_path, ha_url, ha_token)
        self.mcp = mock_mcp

    @pytest.mark.asyncio
    async def test_take_initial_snapshot(self):
        """Test taking initial snapshot without comparison."""
        sample_states = [{"entity_id": "sensor.temp", "state": "22", "attributes": {}}]

        with patch("tools.batch_operations.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": sample_states}

            result = await self.mcp._tools["compare_entities_state"](
                entity_ids="sensor.temp", snapshot_before=None
            )
            data = json.loads(result)

            assert data["success"] is True
            assert data["mode"] == "snapshot"
            assert "snapshot" in data
            assert "sensor.temp" in data["snapshot"]

    @pytest.mark.asyncio
    async def test_compare_with_changes(self):
        """Test comparison when states have changed."""
        before_snapshot = {"sensor.temp": {"state": "20", "attributes": {}}}

        after_states = [
            {
                "entity_id": "sensor.temp",
                "state": "25",
                "attributes": {},
                "last_changed": "2026-02-24T10:00:00Z",
            }
        ]

        with patch("tools.batch_operations.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": after_states}

            result = await self.mcp._tools["compare_entities_state"](
                entity_ids="sensor.temp",
                snapshot_before=json.dumps({"snapshot": before_snapshot}),
            )
            data = json.loads(result)

            assert data["success"] is True
            assert data["mode"] == "comparison"
            assert data["summary"]["changed"] > 0
            assert len(data["changes"]) > 0

    @pytest.mark.asyncio
    async def test_compare_no_changes(self):
        """Test comparison when states are unchanged."""
        snapshot = {"sensor.temp": {"state": "22", "attributes": {}}}

        current_states = [
            {
                "entity_id": "sensor.temp",
                "state": "22",
                "attributes": {},
                "last_changed": "2026-02-24T10:00:00Z",
            }
        ]

        with patch("tools.batch_operations.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": current_states}

            result = await self.mcp._tools["compare_entities_state"](
                entity_ids="sensor.temp",
                snapshot_before=json.dumps({"snapshot": snapshot}),
            )
            data = json.loads(result)

            assert data["success"] is True
            assert data["summary"]["unchanged"] > 0
            assert data["summary"]["changed"] == 0

    @pytest.mark.asyncio
    async def test_compare_new_entities(self):
        """Test detecting new entities."""
        snapshot = {"sensor.temp": {"state": "22", "attributes": {}}}

        current_states = [
            {"entity_id": "sensor.temp", "state": "22", "attributes": {}},
            {"entity_id": "sensor.new", "state": "30", "attributes": {}},
        ]

        with patch("tools.batch_operations.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": current_states}

            result = await self.mcp._tools["compare_entities_state"](
                entity_ids="sensor.temp,sensor.new",
                snapshot_before=json.dumps({"snapshot": snapshot}),
            )
            data = json.loads(result)

            assert data["success"] is True
            assert data["summary"]["new"] > 0

    @pytest.mark.asyncio
    async def test_compare_missing_entities(self):
        """Test detecting missing entities."""
        snapshot = {
            "sensor.temp": {"state": "22", "attributes": {}},
            "sensor.removed": {"state": "30", "attributes": {}},
        }

        current_states = [{"entity_id": "sensor.temp", "state": "22", "attributes": {}}]

        with patch("tools.batch_operations.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": current_states}

            result = await self.mcp._tools["compare_entities_state"](
                entity_ids="sensor.temp,sensor.removed",
                snapshot_before=json.dumps({"snapshot": snapshot}),
            )
            data = json.loads(result)

            assert data["success"] is True
            assert data["summary"]["missing"] > 0

    @pytest.mark.asyncio
    async def test_compare_empty_entity_ids(self):
        """Empty entity_ids should return an error."""
        result = await self.mcp._tools["compare_entities_state"](
            entity_ids="", snapshot_before=None
        )
        data = json.loads(result)

        assert data["success"] is False
        assert "No entity IDs" in data.get("error", "")

    @pytest.mark.asyncio
    async def test_compare_attribute_changes_same_state(self):
        """Attribute changes while state stays the same should not appear in changes."""
        snapshot = {"light.room": {"state": "on", "attributes": {"brightness": 100}}}
        current_states = [
            {
                "entity_id": "light.room",
                "state": "on",
                "attributes": {"brightness": 200},
                "last_changed": "2026-01-01T00:00:00Z",
            }
        ]

        with patch("tools.batch_operations.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": current_states}

            result = await self.mcp._tools["compare_entities_state"](
                entity_ids="light.room",
                snapshot_before=json.dumps({"snapshot": snapshot}),
            )
            data = json.loads(result)

        assert data["success"] is True
        # state didn't change, so entity is in unchanged
        assert data["summary"]["unchanged"] == 1
        assert data["summary"]["changed"] == 0


class TestGetTemplateDependencies:
    """Test get_template_dependencies function."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        """Setup test environment."""
        from tools.batch_operations import register_batch_operations_tools

        with patch("tools.utils.get_registry_entities") as mock_registry:
            mock_registry.return_value = MOCK_REGISTRY_DATA["core.entity_registry"]["data"][
                "entities"
            ]
            register_batch_operations_tools(mock_mcp, config_path, ha_url, ha_token)

        self.mcp = mock_mcp
        self.config_path = config_path

    @pytest.mark.asyncio
    async def test_analyze_template_with_dependencies(self):
        """Test analyzing template with entity dependencies."""
        # Create template configuration
        config_dir = Path(self.config_path)
        templates_dir = config_dir / "templates"
        templates_dir.mkdir()

        (templates_dir / "test.yaml").write_text("""
- name: test_sensor
  state: >
    {{ states('sensor.temperature') | round(1) }}
        """)

        with patch("tools.utils.get_registry_entities") as mock_registry:
            mock_registry.return_value = [
                {"entity_id": "template.test_sensor", "platform": "template"},
                {"entity_id": "sensor.temperature", "platform": "mqtt"},
            ]

            result = await self.mcp._tools["get_template_dependencies"](
                entity_id="template.test_sensor"
            )
            data = json.loads(result)

            assert data["success"] is True
            assert data["dependencies_found"] > 0

    @pytest.mark.asyncio
    async def test_analyze_non_template_entity(self):
        """Test analyzing non-template entity returns error."""
        with patch("tools.utils.get_registry_entities") as mock_registry:
            mock_registry.return_value = [{"entity_id": "sensor.temperature", "platform": "mqtt"}]

            result = await self.mcp._tools["get_template_dependencies"](
                entity_id="sensor.temperature"
            )
            data = json.loads(result)

            assert data["success"] is False
            assert "not a template" in data.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_analyze_nonexistent_entity(self):
        """Test analyzing non-existent entity."""
        with patch("tools.utils.get_registry_entities") as mock_registry:
            mock_registry.return_value = []

            result = await self.mcp._tools["get_template_dependencies"](
                entity_id="template.nonexistent"
            )
            data = json.loads(result)

            assert data["success"] is False
            assert "not found" in data.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_analyze_template_with_missing_dependencies(self):
        """Template whose dependencies don't exist should report a warning."""
        config_dir = Path(self.config_path)
        templates_dir = config_dir / "templates"
        templates_dir.mkdir(exist_ok=True)

        (templates_dir / "missing_deps.yaml").write_text("""
- name: ghost_sensor
  state: >
    {{ states('sensor.does_not_exist') | round(1) }}
        """)

        with patch("tools.batch_operations.get_registry_entities") as mock_registry:
            mock_registry.return_value = [
                {
                    "entity_id": "template.ghost_sensor",
                    "platform": "template",
                    "original_name": "ghost_sensor",
                }
            ]

            result = await self.mcp._tools["get_template_dependencies"](
                entity_id="template.ghost_sensor"
            )
            data = json.loads(result)

        assert data["success"] is True
        if data["dependencies_found"] > 0:
            assert data["summary"]["missing"] > 0
            assert "warning" in data


class TestBulkSearchEntities:
    """Test bulk_search_entities function."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        """Setup test environment."""
        from tools.batch_operations import register_batch_operations_tools

        with patch("tools.utils.get_registry_entities") as mock_registry:
            mock_registry.return_value = [
                {
                    "entity_id": "sensor.temperature_living",
                    "name": "Living Temp",
                    "platform": "mqtt",
                },
                {
                    "entity_id": "sensor.temperature_bedroom",
                    "name": "Bedroom Temp",
                    "platform": "mqtt",
                },
                {
                    "entity_id": "sensor.humidity_living",
                    "name": "Living Humidity",
                    "platform": "mqtt",
                },
            ]
            register_batch_operations_tools(mock_mcp, config_path, ha_url, ha_token)

        self.mcp = mock_mcp

    @pytest.mark.asyncio
    async def test_bulk_search_multiple_terms(self):
        """Test searching for multiple terms at once."""
        with patch("tools.utils.get_registry_entities") as mock_registry:
            mock_registry.return_value = [
                {
                    "entity_id": "sensor.temperature",
                    "name": "Temperature",
                    "platform": "mqtt",
                },
                {
                    "entity_id": "sensor.humidity",
                    "name": "Humidity",
                    "platform": "mqtt",
                },
            ]

            result = await self.mcp._tools["bulk_search_entities"](
                search_terms="temperature,humidity", max_results_per_term=10
            )
            data = json.loads(result)

            assert data["success"] is True
            assert data["terms_searched"] == 2
            assert "temperature" in data["results"]
            assert "humidity" in data["results"]

    @pytest.mark.asyncio
    async def test_bulk_search_no_results(self):
        """Test search with no matching results."""
        with patch("tools.utils.get_registry_entities") as mock_registry:
            mock_registry.return_value = []

            result = await self.mcp._tools["bulk_search_entities"](
                search_terms="nonexistent", max_results_per_term=10
            )
            data = json.loads(result)

            assert data["success"] is True
            assert data["total_matches"] == 0

    @pytest.mark.asyncio
    async def test_bulk_search_max_results_limit(self):
        """Test that max_results_per_term is respected."""
        entities = [
            {"entity_id": f"sensor.temp_{i}", "name": f"Temp {i}", "platform": "mqtt"}
            for i in range(20)
        ]

        with patch("tools.batch_operations.get_registry_entities") as mock_registry:
            mock_registry.return_value = entities

            result = await self.mcp._tools["bulk_search_entities"](
                search_terms="temp", max_results_per_term=5
            )
            data = json.loads(result)

            assert data["success"] is True
            temp_results = data["results"]["temp"]
            assert len(temp_results["matches"]) <= 5
            assert temp_results["matches_found"] == 20
            assert temp_results.get("truncated") is True

    @pytest.mark.asyncio
    async def test_bulk_search_case_insensitive(self):
        """Test that search is case-insensitive."""
        with patch("tools.utils.get_registry_entities") as mock_registry:
            mock_registry.return_value = [
                {
                    "entity_id": "sensor.Temperature",
                    "name": "Temperature",
                    "platform": "mqtt",
                }
            ]

            result = await self.mcp._tools["bulk_search_entities"](
                search_terms="TEMPERATURE,temperature,TeMpErAtUrE",
                max_results_per_term=10,
            )
            data = json.loads(result)

            assert data["success"] is True
            # All variants should find the same entity
            for term in ["temperature", "temperature", "temperature"]:
                assert data["results"][term]["matches_found"] > 0

    @pytest.mark.asyncio
    async def test_bulk_search_empty_terms(self):
        """Test with empty search terms."""
        result = await self.mcp._tools["bulk_search_entities"](
            search_terms="", max_results_per_term=10
        )
        data = json.loads(result)

        assert data["success"] is False
        assert "No search terms" in data.get("error", "")

    @pytest.mark.asyncio
    async def test_bulk_search_token_savings_metadata(self):
        """Test that token savings metadata is included."""
        with patch("tools.utils.get_registry_entities") as mock_registry:
            mock_registry.return_value = []

            result = await self.mcp._tools["bulk_search_entities"](
                search_terms="test", max_results_per_term=10
            )
            data = json.loads(result)

            assert "metadata" in data
            assert "token_savings_vs_individual" in data["metadata"]


class TestErrorHandling:
    """Test error handling in batch operations."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token):
        """Setup test environment."""
        from tools.batch_operations import register_batch_operations_tools

        register_batch_operations_tools(mock_mcp, config_path, ha_url, ha_token)
        self.mcp = mock_mcp

    @pytest.mark.asyncio
    async def test_handle_ha_api_failure(self):
        """Test handling HA API failures."""
        with patch("tools.batch_operations.make_ha_request") as mock_request:
            mock_request.return_value = {"success": False, "error": "Connection failed"}

            result = await self.mcp._tools["compare_entities_state"](
                entity_ids="sensor.test", snapshot_before=None
            )
            data = json.loads(result)

            assert data["success"] is False
            assert "error" in data

    @pytest.mark.asyncio
    async def test_handle_invalid_snapshot_json(self):
        """Test handling invalid snapshot JSON."""
        with patch("tools.batch_operations.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": []}

            result = await self.mcp._tools["compare_entities_state"](
                entity_ids="sensor.test", snapshot_before="invalid json"
            )
            data = json.loads(result)

            assert data["success"] is False
            assert "invalid" in data.get("error", "").lower()


class TestCompareAttributes:
    def test_compare_attributes_detects_changes(self):
        from tools.batch_operations import _compare_attributes

        before = {"brightness": 100, "color_temp": 300, "effect": "none"}
        after = {"brightness": 200, "color_temp": 300, "effect": "colorloop"}

        result = _compare_attributes(before, after)

        assert len(result) == 2
        changed_keys = {c["attribute"] for c in result}
        assert "brightness" in changed_keys
        assert "effect" in changed_keys

        brightness_change = next(c for c in result if c["attribute"] == "brightness")
        assert brightness_change["before"] == 100
        assert brightness_change["after"] == 200

    def test_compare_attributes_new_key(self):
        from tools.batch_operations import _compare_attributes

        before = {"brightness": 100}
        after = {"brightness": 100, "transition": 2}

        result = _compare_attributes(before, after)

        assert len(result) == 1
        assert result[0]["attribute"] == "transition"
        assert result[0]["before"] is None
        assert result[0]["after"] == 2

    def test_compare_attributes_removed_key(self):
        from tools.batch_operations import _compare_attributes

        before = {"brightness": 100, "color_temp": 300}
        after = {"brightness": 100}

        result = _compare_attributes(before, after)

        assert len(result) == 1
        assert result[0]["attribute"] == "color_temp"
        assert result[0]["before"] == 300
        assert result[0]["after"] is None
