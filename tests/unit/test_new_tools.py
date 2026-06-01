"""
Tests for newly added tools: helpers_health, categories, automations (new),
diagnostics (new), devices (new), storage (new), dev_tools (new).
"""

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import mock_open, patch

import pytest

from tools.automations import register_automation_tools
from tools.categories import register_categories_tools
from tools.dev_tools import register_dev_tools
from tools.devices import register_device_tools
from tools.diagnostics import register_diagnostics_tools
from tools.storage import register_storage_tools


@pytest.fixture
def config_path(tmp_path) -> str:
    """Create a temporary config path with .storage directory."""
    storage_dir = tmp_path / ".storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return str(tmp_path)


@pytest.fixture
def ha_url():
    return "http://test-ha"


@pytest.fixture
def ha_token():
    return "test-token"


@pytest.fixture
def mock_mcp():
    """Mock MCP server instance."""

    class MockMCP:
        def __init__(self):
            self._tools = {}

        def tool(self):
            def decorator(func):
                self._tools[func.__name__] = func
                return func

            return decorator

    return MockMCP()


# ================================================================
# 2. list_automation_categories
# ================================================================


class TestListAutomationCategories:
    def _category_registry(self):
        return {
            "data": {
                "categories": [
                    {"category_id": "lighting", "name": "Lighting", "scope": "automation"},
                    {"category_id": "security", "name": "Security", "scope": "automation"},
                ]
            }
        }

    def test_success_path(self, mock_mcp, config_path):
        with patch("tools.categories.load_registry") as mock_load:
            mock_load.return_value = self._category_registry()
            register_categories_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["list_automation_categories"]
            data = json.loads(tool(include_entity_count=False))

        assert data["success"] is True
        assert data["total"] == 2
        names = [c["name"] for c in data["categories"]]
        assert "Lighting" in names
        assert "Security" in names

    def test_empty_categories(self, mock_mcp, config_path):
        with patch("tools.categories.load_registry") as mock_load:
            mock_load.return_value = {"data": {"categories": []}}
            register_categories_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["list_automation_categories"]
            data = json.loads(tool(include_entity_count=False))

        assert data["success"] is True
        assert data["total"] == 0
        assert data["categories"] == []

    def test_exception_handler(self, mock_mcp, config_path):
        with patch(
            "tools.categories._do_list_automation_categories",
            side_effect=RuntimeError("registry read failure"),
        ):
            register_categories_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["list_automation_categories"]
            data = json.loads(tool())

        assert data["success"] is False
        assert "registry read failure" in data.get("error", "")


# ================================================================
# 3. search_inside_automations
# ================================================================


AUTO_SEARCH_YAML = """
- id: "s001"
  alias: "Search Test Auto"
  trigger:
    - platform: state
      entity_id: "binary_sensor.motion"
      to: "on"
  action:
    - service: "light.turn_on"
      target:
        entity_id: "light.room"
- id: "s002"
  alias: "No Match Auto"
  trigger:
    - platform: time
      at: "08:00:00"
  action:
    - service: "script.do_nothing"
"""


@pytest.fixture
def config_path_auto_search(tmp_path) -> str:
    (tmp_path / "automations.yaml").write_text(AUTO_SEARCH_YAML, encoding="utf-8")
    return str(tmp_path)


class TestSearchInsideAutomations:
    def test_success_path(self, mock_mcp, config_path_auto_search):
        register_automation_tools(mock_mcp, config_path_auto_search)
        tool = mock_mcp._tools["search_inside_automations"]
        data = json.loads(tool(pattern="light.turn_on", search_in="all"))

        assert data["success"] is True
        assert data["match_count"] >= 1
        matches = data["matches"]
        assert any("light.turn_on" in m["matched_text"] for m in matches)

    def test_no_matches(self, mock_mcp, config_path_auto_search):
        register_automation_tools(mock_mcp, config_path_auto_search)
        tool = mock_mcp._tools["search_inside_automations"]
        data = json.loads(tool(pattern="nonexistent_pattern_xyz", search_in="all"))

        assert data["success"] is True
        assert data["match_count"] == 0
        assert data["matches"] == []

    def test_exception_handler(self, mock_mcp, config_path_auto_search):
        register_automation_tools(mock_mcp, config_path_auto_search)
        with patch(
            "tools.automations._do_search_inside_automations",
            side_effect=RuntimeError("search failure"),
        ):
            tool = mock_mcp._tools["search_inside_automations"]
            data = json.loads(tool(pattern="test"))

        assert data["success"] is False
        assert "search failure" in data.get("error", "")


# ================================================================
# 4. diagnose_uncategorized_automations
# ================================================================


class TestDiagnoseUncategorizedAutomations:
    def _entity_registry_with_categories(self):
        return {
            "data": {
                "entities": [
                    {
                        "entity_id": "automation.categorized_one",
                        "name": "Categorized Auto",
                        "categories": {"automation": "lighting"},
                        "area_id": "living_room",
                    },
                    {
                        "entity_id": "automation.uncategorized_one",
                        "name": "Uncategorized Auto",
                        "categories": {},
                        "area_id": "office",
                    },
                ]
            }
        }

    def test_success_path(self, mock_mcp, config_path, ha_url, ha_token):
        with patch("tools.automations.load_registry") as mock_load:
            mock_load.return_value = self._entity_registry_with_categories()
            register_automation_tools(mock_mcp, config_path, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_uncategorized_automations"]
            data = json.loads(tool(scope="automation", auto_suggest=False))

        assert data["success"] is True
        assert data["total_uncategorized"] == 1
        uncat = data["uncategorized"]
        assert uncat[0]["entity_id"] == "automation.uncategorized_one"

    def test_all_categorized(self, mock_mcp, config_path, ha_url, ha_token):
        registry = {
            "data": {
                "entities": [
                    {
                        "entity_id": "automation.cat_one",
                        "name": "Cat One",
                        "categories": {"automation": "lighting"},
                    },
                ]
            }
        }
        with patch("tools.automations.load_registry") as mock_load:
            mock_load.return_value = registry
            register_automation_tools(mock_mcp, config_path, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_uncategorized_automations"]
            data = json.loads(tool())

        assert data["success"] is True
        assert data["total_uncategorized"] == 0

    def test_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.automations._do_diagnose_uncategorized_automations",
            side_effect=RuntimeError("registry failure"),
        ):
            register_automation_tools(mock_mcp, config_path, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_uncategorized_automations"]
            data = json.loads(tool())

        assert data["success"] is False
        assert "registry failure" in data.get("error", "")


# ================================================================
# 5. validate_automation_names
# ================================================================


VIOLATION_AUTOS_YAML = """
- id: "v001"
  alias: "Heating - Living Room Thermostat"
  trigger: []
  action: []

- id: "v002"
  alias: "Broken Separator: Test"
  trigger: []
  action: []
"""

ALL_VALID_AUTOS_YAML = """
- id: "v003"
  alias: "Light - Hallway Motion"
  trigger: []
  action: []
"""


@pytest.fixture
def config_path_violation(tmp_path) -> str:
    (tmp_path / "automations.yaml").write_text(VIOLATION_AUTOS_YAML, encoding="utf-8")
    return str(tmp_path)


@pytest.fixture
def config_path_all_valid(tmp_path) -> str:
    (tmp_path / "automations.yaml").write_text(ALL_VALID_AUTOS_YAML, encoding="utf-8")
    return str(tmp_path)


class TestValidateAutomationNames:
    def test_success_path_with_violations(self, mock_mcp, config_path_violation, ha_url, ha_token):
        with patch("tools.automations.load_registry") as mock_load:
            mock_load.return_value = {"data": {"entities": [], "categories": []}}
            register_automation_tools(mock_mcp, config_path_violation, ha_url, ha_token)
            tool = mock_mcp._tools["validate_automation_names"]
            data = json.loads(tool())

        assert data["success"] is True
        assert data["total_violations"] >= 1
        violation_types = [v["violation_type"] for v in data["violations"]]
        assert "wrong_separator" in violation_types

    def test_all_valid(self, mock_mcp, config_path_all_valid, ha_url, ha_token):
        with patch("tools.automations.load_registry") as mock_load:
            mock_load.return_value = {"data": {"entities": [], "categories": []}}
            register_automation_tools(mock_mcp, config_path_all_valid, ha_url, ha_token)
            tool = mock_mcp._tools["validate_automation_names"]
            data = json.loads(tool())

        assert data["success"] is True
        assert data["total_violations"] == 0

    def test_exception_handler(self, mock_mcp, config_path_violation, ha_url, ha_token):
        with patch(
            "tools.automations._do_validate_automation_names",
            side_effect=RuntimeError("validation crash"),
        ):
            register_automation_tools(mock_mcp, config_path_violation, ha_url, ha_token)
            tool = mock_mcp._tools["validate_automation_names"]
            data = json.loads(tool())

        assert data["success"] is False
        assert "validation crash" in data.get("error", "")


# ================================================================
# 6. diagnose_category_alias_mismatch
# ================================================================


CAT_MISMATCH_AUTOS_YAML = """
- id: "cm001"
  alias: "Light - Kitchen Overhead"
  trigger: []
  action: []
- id: "cm002"
  alias: "Heating - Bedroom Thermostat"
  trigger: []
  action: []
"""


@pytest.fixture
def config_path_cat_mismatch(tmp_path) -> str:
    (tmp_path / "automations.yaml").write_text(CAT_MISMATCH_AUTOS_YAML, encoding="utf-8")
    return str(tmp_path)


class TestDiagnoseCategoryAliasMismatch:
    def _full_registry(self):
        return {
            "entity_registry": {
                "data": {
                    "entities": [
                        {
                            "entity_id": "automation.light_kitchen_overhead",
                            "unique_id": "cm001",
                            "categories": {"automation": "security"},
                        },
                        {
                            "entity_id": "automation.heating_bedroom_thermostat",
                            "unique_id": "cm002",
                            "categories": {"automation": "heating"},
                        },
                    ]
                }
            },
            "category_registry": {
                "data": {
                    "categories": [
                        {
                            "category_id": "security",
                            "name": "Security",
                            "scope": "automation",
                        },
                        {
                            "category_id": "heating",
                            "name": "Heating",
                            "scope": "automation",
                        },
                        {
                            "category_id": "lighting",
                            "name": "Lighting",
                            "scope": "automation",
                        },
                    ]
                }
            },
        }

    def test_success_path_mismatches(self, mock_mcp, config_path_cat_mismatch, ha_url, ha_token):
        reg = self._full_registry()

        def mock_load_registry(name, path):
            if "entity" in name:
                return reg["entity_registry"]
            if "category" in name:
                return reg["category_registry"]
            return {"data": {}}

        with patch("tools.automations.load_registry", side_effect=mock_load_registry):
            register_automation_tools(mock_mcp, config_path_cat_mismatch, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_category_alias_mismatch"]
            data = json.loads(tool())

        assert data["success"] is True
        mismatches = data["mismatches"]
        assert len(mismatches) >= 1

    def test_all_matching(self, mock_mcp, config_path_cat_mismatch, ha_url, ha_token):
        reg = {
            "entity_registry": {
                "data": {
                    "entities": [
                        {
                            "entity_id": "automation.light_kitchen_overhead",
                            "unique_id": "cm001",
                            "categories": {"automation": "lighting"},
                        },
                    ]
                }
            },
            "category_registry": {
                "data": {
                    "categories": [
                        {
                            "category_id": "lighting",
                            "name": "Light",
                            "scope": "automation",
                        },
                    ]
                }
            },
        }

        def mock_load_registry(name, path):
            if "entity" in name:
                return reg["entity_registry"]
            if "category" in name:
                return reg["category_registry"]
            return {"data": {}}

        with patch("tools.automations.load_registry", side_effect=mock_load_registry):
            register_automation_tools(mock_mcp, config_path_cat_mismatch, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_category_alias_mismatch"]
            data = json.loads(tool())

        assert data["success"] is True
        assert data["total_mismatches"] == 0

    def test_exception_handler(self, mock_mcp, config_path_cat_mismatch, ha_url, ha_token):
        with patch(
            "tools.automations._do_diagnose_category_alias_mismatch",
            side_effect=RuntimeError("mismatch analysis crash"),
        ):
            register_automation_tools(mock_mcp, config_path_cat_mismatch, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_category_alias_mismatch"]
            data = json.loads(tool())

        assert data["success"] is False
        assert "mismatch analysis crash" in data.get("error", "")


# ================================================================
# 7. diagnose_stale_entities
# ================================================================


class TestDiagnoseStaleEntities:
    def _stale_states(self):
        now = datetime.now(UTC)
        stale_ts = (now - timedelta(hours=2)).isoformat()
        fresh_ts = (now - timedelta(minutes=5)).isoformat()
        return [
            {
                "entity_id": "sensor.stale_temp",
                "state": "22.0",
                "last_updated": stale_ts,
                "last_changed": stale_ts,
                "attributes": {},
            },
            {
                "entity_id": "sensor.fresh_humidity",
                "state": "55.0",
                "last_updated": fresh_ts,
                "last_changed": fresh_ts,
                "attributes": {},
            },
            {
                "entity_id": "light.room_light",
                "state": "on",
                "last_updated": fresh_ts,
                "last_changed": fresh_ts,
                "attributes": {},
            },
        ]

    def test_success_path(self, mock_mcp, config_path, ha_url, ha_token):
        states = self._stale_states()
        entity_reg = {
            "data": {
                "entities": [
                    {"entity_id": "sensor.stale_temp", "platform": "mqtt"},
                    {"entity_id": "sensor.fresh_humidity", "platform": "mqtt"},
                ]
            }
        }
        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_load,
        ):
            mock_req.return_value = {"success": True, "data": states}
            mock_load.return_value = entity_reg
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_stale_entities"]
            data = json.loads(tool(stale_minutes=15))

        assert data["success"] is True
        assert data["total_stale"] >= 1
        stale_ids = [e["entity_id"] for e in data["stale_entities"]]
        assert "sensor.stale_temp" in stale_ids

    def test_no_stale_entities(self, mock_mcp, config_path, ha_url, ha_token):
        now = datetime.now(UTC)
        fresh_ts = (now - timedelta(minutes=5)).isoformat()
        states = [
            {
                "entity_id": "sensor.active_temp",
                "state": "20.0",
                "last_updated": fresh_ts,
                "last_changed": fresh_ts,
                "attributes": {},
            },
        ]
        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_load,
        ):
            mock_req.return_value = {"success": True, "data": states}
            mock_load.return_value = {"data": {"entities": []}}
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_stale_entities"]
            data = json.loads(tool(stale_minutes=15))

        assert data["success"] is True
        assert data["total_stale"] == 0

    def test_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.diagnostics._do_diagnose_stale_entities",
            side_effect=RuntimeError("stale check failure"),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_stale_entities"]
            data = json.loads(tool())

        assert data["success"] is False
        assert "stale check failure" in data.get("error", "")


# ================================================================
# 8. diagnose_orphan_references
# ================================================================


ORPHAN_AUTOS_YAML = """
- id: "orph001"
  alias: "Orphan Test Automation"
  trigger:
    - platform: state
      entity_id: "light.orphan_light"
      to: "on"
  action:
    - delay: 5
"""


@pytest.fixture
def config_path_orphan(tmp_path) -> str:
    (tmp_path / "automations.yaml").write_text(ORPHAN_AUTOS_YAML, encoding="utf-8")
    return str(tmp_path)


class TestDiagnoseOrphanReferences:
    def test_success_path(self, mock_mcp, config_path_orphan, ha_url, ha_token):
        existing_states = [
            {"entity_id": "sensor.temperature", "state": "22.0", "attributes": {}},
        ]
        with patch("tools.diagnostics.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": existing_states}
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path_orphan)
            tool = mock_mcp._tools["diagnose_orphan_references"]
            data = json.loads(tool(scope="automations"))

        assert data["success"] is True
        orphans = data["orphan_references"]
        assert len(orphans) >= 1
        orphan_ids = [o["entity_id"] for o in orphans]
        assert "light.orphan_light" in orphan_ids

    def test_no_orphans(self, mock_mcp, config_path_orphan, ha_url, ha_token):
        existing_states = [
            {"entity_id": "light.orphan_light", "state": "on", "attributes": {}},
        ]
        with patch("tools.diagnostics.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": existing_states}
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path_orphan)
            tool = mock_mcp._tools["diagnose_orphan_references"]
            data = json.loads(tool(scope="automations"))

        assert data["success"] is True
        assert data["orphan_count"] == 0

    def test_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.diagnostics._do_diagnose_orphan_references",
            side_effect=RuntimeError("orphan scan failure"),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_orphan_references"]
            data = json.loads(tool())

        assert data["success"] is False
        assert "orphan scan failure" in data.get("error", "")


# ================================================================
# 9. diagnose_entity_threshold_proximity
# ================================================================


class TestDiagnoseEntityThresholdProximity:
    def _threshold_states(self):
        return [
            {
                "entity_id": "sensor.living_room_illuminance",
                "state": "95.0",
                "attributes": {"unit_of_measurement": "lx"},
            },
            {
                "entity_id": "input_number.living_room_illuminance_lvl",
                "state": "100.0",
                "attributes": {},
            },
            {
                "entity_id": "sensor.bedroom_illuminance",
                "state": "10.0",
                "attributes": {"unit_of_measurement": "lx"},
            },
            {
                "entity_id": "input_number.bedroom_illuminance_lvl",
                "state": "100.0",
                "attributes": {},
            },
        ]

    def test_success_path_sensors_near_threshold(self, mock_mcp, config_path, ha_url, ha_token):
        states = self._threshold_states()
        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_load,
        ):
            mock_req.return_value = {"success": True, "data": states}
            mock_load.return_value = {"data": {"entities": []}}
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_entity_threshold_proximity"]
            data = json.loads(tool(proximity_percent=15))

        assert data["success"] is True
        alerts = data["threshold_alerts"]
        assert len(alerts) >= 1
        assert alerts[0]["sensor_entity"] == "sensor.living_room_illuminance"

    def test_all_far_from_thresholds(self, mock_mcp, config_path, ha_url, ha_token):
        states = [
            {
                "entity_id": "sensor.living_room_illuminance",
                "state": "10.0",
                "attributes": {"unit_of_measurement": "lx"},
            },
            {
                "entity_id": "input_number.living_room_illuminance_lvl",
                "state": "100.0",
                "attributes": {},
            },
        ]
        with (
            patch("tools.diagnostics.make_ha_request") as mock_req,
            patch("tools.diagnostics.load_registry") as mock_load,
        ):
            mock_req.return_value = {"success": True, "data": states}
            mock_load.return_value = {"data": {"entities": []}}
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_entity_threshold_proximity"]
            data = json.loads(tool(proximity_percent=15))

        assert data["success"] is True
        assert len(data["threshold_alerts"]) == 0

    def test_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.diagnostics._do_diagnose_entity_threshold_proximity",
            side_effect=RuntimeError("threshold diagnostic error"),
        ):
            register_diagnostics_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_entity_threshold_proximity"]
            data = json.loads(tool())

        assert data["success"] is False
        assert "threshold diagnostic error" in data.get("error", "")


# ================================================================
# 10. get_entity_registry_batch
# ================================================================


class TestGetEntityRegistryBatch:
    def _entity_registry(self):
        return {
            "data": {
                "entities": [
                    {
                        "entity_id": "light.living_room",
                        "platform": "hue",
                        "device_id": "dev1",
                        "area_id": "living_room",
                        "name": "Living Room Light",
                    },
                    {
                        "entity_id": "sensor.temperature",
                        "platform": "mqtt",
                        "device_id": "dev2",
                        "area_id": None,
                    },
                ]
            }
        }

    @pytest.mark.asyncio
    async def test_success_path_filtered(self, mock_mcp, config_path):
        with patch("tools.storage.load_registry") as mock_load:
            mock_load.return_value = self._entity_registry()
            register_storage_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["get_entity_registry_batch"]
            data = json.loads(await tool(entity_ids="light.living_room"))

        assert data["success"] is True
        assert data["total_entities"] == 1
        assert data["entities"][0]["entity_id"] == "light.living_room"

    @pytest.mark.asyncio
    async def test_exception_handler(self, mock_mcp, config_path):
        with patch(
            "tools.storage._do_get_entity_registry_batch",
            side_effect=RuntimeError("registry batch failure"),
        ):
            register_storage_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["get_entity_registry_batch"]
            data = json.loads(await tool())

        assert data["success"] is False
        assert "registry batch failure" in data.get("error", "")


# ================================================================
# 11. get_device_triggers
# ================================================================


class TestGetDeviceTriggers:
    def _device_dict(self):
        return {
            "id": "dev_trigger_test",
            "name": "Motion Sensor",
            "name_by_user": None,
            "manufacturer": "Acme",
            "model": "Z100",
            "config_entries": ["entry_mqtt"],
        }

    def _entity_list(self):
        return [
            {
                "entity_id": "binary_sensor.motion_test",
                "device_id": "dev_trigger_test",
                "platform": "mqtt",
                "original_name": "Motion Sensor",
            },
        ]

    def _config_entry_list(self):
        return [
            {"entry_id": "entry_mqtt", "domain": "mqtt", "title": "MQTT"},
        ]

    @pytest.mark.asyncio
    async def test_success_path_triggers_found(self, mock_mcp, config_path, ha_url, ha_token):
        with (
            patch("tools.devices.get_registry_entities", return_value=self._entity_list()),
            patch(
                "tools.devices.get_registry_config_entries",
                return_value=self._config_entry_list(),
            ),
            patch("tools.devices._get_device_by_id", return_value=self._device_dict()),
        ):
            register_device_tools(mock_mcp, config_path, ha_url, ha_token)
            tool = mock_mcp._tools["get_device_triggers"]
            data = json.loads(await tool(entity_id="binary_sensor.motion_test"))

        assert data["success"] is True
        assert "triggers" in data
        assert data["total_triggers"] >= 1

    @pytest.mark.asyncio
    async def test_no_triggers_empty_device(self, mock_mcp, config_path, ha_url, ha_token):
        empty_device = {
            "id": "dev_empty",
            "name": "Empty Device",
            "name_by_user": None,
            "config_entries": [],
            "manufacturer": "None",
            "model": "None",
        }
        with (
            patch("tools.devices.get_registry_entities", return_value=[]),
            patch("tools.devices.get_registry_config_entries", return_value=[]),
            patch("tools.devices._get_device_by_id", return_value=empty_device),
        ):
            register_device_tools(mock_mcp, config_path, ha_url, ha_token)
            tool = mock_mcp._tools["get_device_triggers"]
            data = json.loads(await tool(device_id="dev_empty"))

        assert data["success"] is True
        assert data["total_triggers"] == 0

    @pytest.mark.asyncio
    async def test_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        with patch(
            "tools.devices._do_get_device_triggers",
            side_effect=RuntimeError("trigger lookup error"),
        ):
            register_device_tools(mock_mcp, config_path, ha_url, ha_token)
            tool = mock_mcp._tools["get_device_triggers"]
            data = json.loads(await tool(device_id="dev_test"))

        assert data["success"] is False
        assert "trigger lookup error" in data.get("error", "")


# ================================================================
# 12. diagnose_template now() detection
# ================================================================


class TestDiagnoseTemplateNowDetection:
    def _mock_registry_files(self, entity_id, template_code, triggers):
        entity_registry = {
            "data": {
                "entities": [
                    {
                        "entity_id": entity_id,
                        "config_entry_id": "template_entry_001",
                        "platform": "template",
                    },
                ]
            }
        }
        entry_data = {
            "entry_id": "template_entry_001",
            "domain": "template",
            "title": "Test Template",
            "options": {
                "state": template_code,
                "template_type": "sensor",
            },
            "data": {
                "triggers": triggers,
            },
        }
        config_entries = {"data": {"entries": [entry_data]}}
        er_handle = mock_open(read_data=json.dumps(entity_registry)).return_value
        ce_handle = mock_open(read_data=json.dumps(config_entries)).return_value
        return entity_registry, config_entries, er_handle, ce_handle

    def _mock_make_ha_request(self, ha_url, ha_token, endpoint, method="GET", data=None, **kwargs):
        if endpoint == "/api/template":
            return {"success": True, "data": "42"}
        if "/api/states/" in endpoint:
            return {"success": True, "data": {"state": "on", "attributes": {}}}
        return {"success": True, "data": []}

    def test_template_with_now_and_no_triggers_warns(self, mock_mcp, config_path, ha_url, ha_token):
        _, _, er_handle, ce_handle = self._mock_registry_files(
            "sensor.my_template", "{{ now().hour }}", []
        )
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("builtins.open", side_effect=[er_handle, ce_handle]),
            patch("tools.dev_tools.make_ha_request", side_effect=self._mock_make_ha_request),
        ):
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_template"]
            data = json.loads(tool("sensor.my_template"))

        assert data["success"] is True
        issues = data["issues"]
        stale_issues = [i for i in issues if i.get("type") == "stale_timer_risk"]
        assert len(stale_issues) >= 1
        assert "now()" in stale_issues[0]["message"]

    def test_template_with_now_and_triggers_no_warn(self, mock_mcp, config_path, ha_url, ha_token):
        _, _, er_handle, ce_handle = self._mock_registry_files(
            "sensor.my_triggered_template",
            "{{ now().hour }}",
            [{"platform": "time_pattern", "minutes": "/5"}],
        )
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("builtins.open", side_effect=[er_handle, ce_handle]),
            patch("tools.dev_tools.make_ha_request", side_effect=self._mock_make_ha_request),
        ):
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_template"]
            data = json.loads(tool("sensor.my_triggered_template"))

        assert data["success"] is True
        issues = data["issues"]
        stale_issues = [i for i in issues if i.get("type") == "stale_timer_risk"]
        assert len(stale_issues) == 0

    def test_template_without_now_no_warn(self, mock_mcp, config_path, ha_url, ha_token):
        _, _, er_handle, ce_handle = self._mock_registry_files(
            "sensor.simple_template", "{{ states('sensor.temperature') }}", []
        )
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("builtins.open", side_effect=[er_handle, ce_handle]),
            patch("tools.dev_tools.make_ha_request", side_effect=self._mock_make_ha_request),
        ):
            register_dev_tools(mock_mcp, ha_url, ha_token, config_path)
            tool = mock_mcp._tools["diagnose_template"]
            data = json.loads(tool("sensor.simple_template"))

        assert data["success"] is True
        issues = data["issues"]
        stale_issues = [i for i in issues if i.get("type") == "stale_timer_risk"]
        assert len(stale_issues) == 0


# ================================================================
# 13. get_template_entities_batch
# ================================================================


class TestGetTemplateEntitiesBatch:
    def _template_config_entries(self):
        return {
            "data": {
                "entries": [
                    {
                        "entry_id": "tpl_entry_1",
                        "domain": "template",
                        "title": "sensor.my_tpl1",
                        "options": {
                            "state": "{{ states('sensor.temperature') }}",
                            "template_type": "sensor",
                        },
                        "data": {"triggers": []},
                    },
                    {
                        "entry_id": "tpl_entry_2",
                        "domain": "template",
                        "title": "sensor.my_tpl2",
                        "options": {
                            "state": "{{ now().hour }}",
                            "template_type": "sensor",
                        },
                        "data": {"triggers": []},
                    },
                ]
            }
        }

    def _entity_registry(self):
        return {
            "data": {
                "entities": [
                    {
                        "entity_id": "sensor.my_tpl1",
                        "config_entry_id": "tpl_entry_1",
                        "platform": "template",
                    },
                    {
                        "entity_id": "sensor.my_tpl2",
                        "config_entry_id": "tpl_entry_2",
                        "platform": "template",
                    },
                ]
            }
        }

    def _mock_load_registry(self, name, config_path):
        if "config_entries" in name:
            return self._template_config_entries()
        if "entity_registry" in name:
            return self._entity_registry()
        return {"data": {}}

    @pytest.mark.asyncio
    async def test_success_path(self, mock_mcp, config_path):
        with patch("tools.storage.load_registry", side_effect=self._mock_load_registry):
            register_storage_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["get_template_entities_batch"]
            data = json.loads(await tool(entity_ids="sensor.my_tpl1,sensor.my_tpl2"))

        assert data["success"] is True
        assert data["total"] == 2
        assert data["found"] == 2
        assert data["errors"] == 0
        assert "sensor.my_tpl1" in data["results"]

    @pytest.mark.asyncio
    async def test_exception_handler(self, mock_mcp, config_path):
        with patch(
            "tools.storage._do_get_template_entities_batch",
            side_effect=RuntimeError("batch template error"),
        ):
            register_storage_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["get_template_entities_batch"]
            data = json.loads(await tool(entity_ids="sensor.my_tpl1"))

        assert data["success"] is False
        assert "batch template error" in data.get("error", "")


# ================================================================
# Gap 11: Functional overlap detection in diagnose_automation_aliases
# ================================================================


class TestDiagnoseAutomationAliasesOverlap:
    def test_functional_overlap_detected(self, mock_mcp, config_path, ha_url, ha_token):
        from tools.automations import _do_diagnose_automation_aliases

        def _wrapper():
            try:
                result = _do_diagnose_automation_aliases(config_path, ha_url, ha_token)
                return json.dumps({"success": True, **result})
            except Exception as e:
                return json.dumps({"success": False, "error": str(e)})

        mock_mcp._tools["diagnose_automation_aliases"] = _wrapper

        autos = [
            {
                "id": "auto1",
                "alias": "Light Control",
                "trigger": [{"platform": "state", "entity_id": "binary_sensor.motion", "to": "on"}],
                "action": [{"service": "light.turn_on", "target": {"entity_id": "light.hallway"}}],
            },
            {
                "id": "auto2",
                "alias": "Light Control",
                "trigger": [{"platform": "state", "entity_id": "binary_sensor.motion", "to": "on"}],
                "action": [{"service": "light.turn_on", "target": {"entity_id": "light.hallway"}}],
            },
        ]

        states_response = {
            "success": True,
            "data": [
                {
                    "entity_id": "automation.light_control",
                    "state": "on",
                    "attributes": {"friendly_name": "Light Control"},
                },
                {
                    "entity_id": "automation.light_control_2",
                    "state": "unavailable",
                    "attributes": {"friendly_name": "Light Control"},
                },
            ],
        }

        with (
            patch("tools.automations._load_automations", return_value=autos),
            patch("tools.automations.make_ha_request", return_value=states_response),
        ):
            tool = mock_mcp._tools["diagnose_automation_aliases"]
            data = json.loads(tool())

        assert data["success"] is True
        assert data["total_duplicates"] > 0
        dup = data["duplicates"][0]
        assert dup["overlap_score"] > 0
        assert len(dup["trigger_overlap"]) > 0
        assert "binary_sensor.motion" in dup["trigger_overlap"]
        assert "light.hallway" in dup.get("action_target_overlap", [])


# ================================================================
# Gap 20: search_automations with category filter
# ================================================================


CATEGORY_AUTOS_YAML = """
- id: "cat001"
  alias: "Light Group Control"
  trigger:
    - platform: state
      entity_id: light.living_room
  action:
    - service: light.turn_on
      target:
        entity_id: light.living_room
- id: "cat002"
  alias: "Security Alarm"
  trigger:
    - platform: state
      entity_id: binary_sensor.door
  action:
    - service: notify.mobile
- id: "cat003"
  alias: "Another Light Automation"
  trigger:
    - platform: time
      at: "08:00:00"
  action:
    - service: light.turn_on
      target:
        entity_id: light.kitchen
"""


class TestSearchAutomationsByCategory:
    def _category_registry(self):
        return {
            "data": {
                "categories": [
                    {"category_id": "lighting", "name": "Lighting", "scope": "automation"},
                    {"category_id": "security", "name": "Security", "scope": "automation"},
                ]
            }
        }

    def _entity_registry(self):
        return {
            "data": {
                "entities": [
                    {
                        "entity_id": "automation.light_group_control",
                        "unique_id": "cat001",
                        "categories": {"automation": "lighting"},
                    },
                    {
                        "entity_id": "automation.security_alarm",
                        "unique_id": "cat002",
                        "categories": {"automation": "security"},
                    },
                    {
                        "entity_id": "automation.another_light_automation",
                        "unique_id": "cat003",
                        "categories": {"automation": "lighting"},
                    },
                ]
            }
        }

    def test_search_by_category_name(self, mock_mcp, config_path, ha_url, ha_token):
        import yaml

        from tools.yaml_utils import HomeAssistantLoader

        parsed_autos = yaml.load(CATEGORY_AUTOS_YAML, Loader=HomeAssistantLoader) or []
        register_automation_tools(mock_mcp, config_path, ha_url, ha_token)

        def mock_load_registry(name, path_):
            if "category" in name:
                return self._category_registry()
            if "entity" in name:
                return self._entity_registry()
            return {"data": {}}

        with (
            patch("tools.automations._load_automations", return_value=parsed_autos),
            patch("tools.automations.load_registry", side_effect=mock_load_registry),
        ):
            tool = mock_mcp._tools["search_automations"]
            data = json.loads(tool(category="Lighting"))

        assert data["success"] is True
        assert data["matched_count"] == 2
        aliases = [r["alias"] for r in data["results"]]
        assert "Light Group Control" in aliases
        assert "Another Light Automation" in aliases
        assert "Security Alarm" not in aliases

    def test_search_by_category_id(self, mock_mcp, config_path, ha_url, ha_token):
        import yaml

        from tools.yaml_utils import HomeAssistantLoader

        parsed_autos = yaml.load(CATEGORY_AUTOS_YAML, Loader=HomeAssistantLoader) or []
        register_automation_tools(mock_mcp, config_path, ha_url, ha_token)

        def mock_load_registry(name, path_):
            if "category" in name:
                return self._category_registry()
            if "entity" in name:
                return self._entity_registry()
            return {"data": {}}

        with (
            patch("tools.automations._load_automations", return_value=parsed_autos),
            patch("tools.automations.load_registry", side_effect=mock_load_registry),
        ):
            tool = mock_mcp._tools["search_automations"]
            data = json.loads(tool(category="security"))

        assert data["success"] is True
        assert data["matched_count"] == 1
        assert data["results"][0]["alias"] == "Security Alarm"

    def test_search_by_nonexistent_category(self, mock_mcp, config_path, ha_url, ha_token):
        import yaml

        from tools.yaml_utils import HomeAssistantLoader

        parsed_autos = yaml.load(CATEGORY_AUTOS_YAML, Loader=HomeAssistantLoader) or []
        register_automation_tools(mock_mcp, config_path, ha_url, ha_token)

        def mock_load_registry(name, path_):
            if "category" in name:
                return self._category_registry()
            if "entity" in name:
                return self._entity_registry()
            return {"data": {}}

        with (
            patch("tools.automations._load_automations", return_value=parsed_autos),
            patch("tools.automations.load_registry", side_effect=mock_load_registry),
        ):
            tool = mock_mcp._tools["search_automations"]
            data = json.loads(tool(category="Nonexistent"))

        assert data["success"] is True
        assert data["matched_count"] == 3


# ================================================================
# 14. YAML-based template scanning in get_template_entity_code
# ================================================================


class TestYamlTemplateScanning:
    """Tests for YAML-based template sensor detection in _do_get_template_entity_code."""

    TEMPLATE_YAML = """
template:
  - sensor:
      - unique_id: yaml_temp_sensor
        name: YAML Temperature
        state: "{{ states('sensor.temperature') | float }}"
        unit_of_measurement: "C"
        device_class: temperature
  - binary_sensor:
      - unique_id: yaml_motion_sensor
        name: YAML Motion
        state: "{{ states('binary_sensor.motion') }}"
"""

    TEMPLATE_YAML_NO_MATCH = """
template:
  - sensor:
      - unique_id: other_sensor
        name: Other Sensor
        state: "{{ 42 }}"
"""

    TEMPLATE_SUBDIR_YAML = """
template:
  - sensor:
      - unique_id: dir_sensor
        name: Directory Sensor
        state: "{{ states('sensor.humidity') }}"
"""

    @staticmethod
    def _yaml_opener(content):
        def _open(*args, **kwargs):
            return StringIO(content)
        return _open

    @pytest.mark.asyncio
    async def test_yaml_template_found_by_unique_id(self, mock_mcp, config_path):
        with (
            patch("tools.storage.load_registry", return_value={"data": {"entries": [], "entities": []}}),
            patch("os.path.isfile", return_value=True),
            patch("builtins.open", side_effect=self._yaml_opener(self.TEMPLATE_YAML)),
        ):
            register_storage_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["get_template_entity_code"]
            data = json.loads(await tool(entity_id="sensor.yaml_temp_sensor"))

        assert data["success"] is True
        assert data["entity_id"] == "sensor.yaml_temp_sensor"
        assert data["name"] == "YAML Temperature"
        assert data["template_type"] == "sensor"
        assert "states('sensor.temperature')" in data["state_template"]
        assert data["unit_of_measurement"] == "C"
        assert data["device_class"] == "temperature"
        assert data["source"] == "yaml"
        assert "configuration.yaml" in data["file_path"]

    @pytest.mark.asyncio
    async def test_yaml_template_found_by_name(self, mock_mcp, config_path):
        with (
            patch("tools.storage.load_registry", return_value={"data": {"entries": [], "entities": []}}),
            patch("os.path.isfile", return_value=True),
            patch("builtins.open", side_effect=self._yaml_opener(self.TEMPLATE_YAML)),
        ):
            register_storage_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["get_template_entity_code"]
            data = json.loads(await tool(entity_id="binary_sensor.yaml_motion"))

        assert data["success"] is True
        assert data["entity_id"] == "binary_sensor.yaml_motion"
        assert data["template_type"] == "binary_sensor"
        assert "states('binary_sensor.motion')" in data["state_template"]
        assert data["source"] == "yaml"

    @pytest.mark.asyncio
    async def test_yaml_template_not_found(self, mock_mcp, config_path):
        with (
            patch("tools.storage.load_registry", return_value={"data": {"entries": [], "entities": []}}),
            patch("os.path.isfile", return_value=True),
            patch("builtins.open", side_effect=self._yaml_opener(self.TEMPLATE_YAML_NO_MATCH)),
        ):
            register_storage_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["get_template_entity_code"]
            data = json.loads(await tool(entity_id="sensor.nonexistent"))

        assert data["success"] is False
        err = data.get("error", {})
        if isinstance(err, dict):
            assert err.get("code") == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_yaml_template_in_subdirectory(self, mock_mcp, config_path):
        def _mock_isfile(path):
            return path.endswith(".yaml") and "sensors" in path

        with (
            patch("tools.storage.load_registry", return_value={"data": {"entries": [], "entities": []}}),
            patch("os.path.isfile", side_effect=_mock_isfile),
            patch("os.path.isdir", return_value=True),
            patch("os.listdir", return_value=["sensors.yaml"]),
            patch("builtins.open", side_effect=self._yaml_opener(self.TEMPLATE_SUBDIR_YAML)),
        ):
            register_storage_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["get_template_entity_code"]
            data = json.loads(await tool(entity_id="sensor.dir_sensor"))

        assert data["success"] is True
        assert data["entity_id"] == "sensor.dir_sensor"
        assert data["source"] == "yaml"
        assert "sensors.yaml" in data["file_path"]

    @pytest.mark.asyncio
    async def test_yaml_parse_error_handled_gracefully(self, mock_mcp, config_path):
        with (
            patch("tools.storage.load_registry", return_value={"data": {"entries": [], "entities": []}}),
            patch("os.path.isfile", return_value=True),
            patch(
                "builtins.open",
                side_effect=self._yaml_opener("template: [invalid: yaml: {{{"),
            ),
        ):
            register_storage_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["get_template_entity_code"]
            data = json.loads(await tool(entity_id="sensor.anything"))

        assert data["success"] is False
        err = data.get("error", {})
        if isinstance(err, dict):
            assert err.get("code") == "NOT_FOUND"


# ================================================================
# Gap 3: YAML template line boundaries
# ================================================================


class TestYamlTemplateLineBoundaries:
    """Tests for _find_yaml_line_boundaries."""

    CT_YAML = """\
template:
  - sensor:
      - unique_id: smart_vacuum_upstairs_score
        state: "{{ states('sensor.vacuum_status') }}"
"""
    SUBDIR_YAML = """\
sensor:
  - unique_id: heating_mode
    state: "{{ states('input_select.heating_mode') }}"
"""

    @pytest.mark.asyncio
    async def test_line_boundaries_computed(self, mock_mcp, config_path):
        with (
            patch("tools.storage.load_registry", return_value={"data": {"entries": [], "entities": []}}),
            patch("os.path.isfile", return_value=True),
            patch("builtins.open", side_effect=lambda *a, **kw: StringIO(self.CT_YAML)),
        ):
            register_storage_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["get_template_entity_code"]
            data = json.loads(await tool(entity_id="sensor.smart_vacuum_upstairs_score"))

        assert data["success"] is True
        assert data.get("source") == "yaml"
        assert data.get("file_path") == "configuration.yaml"
        assert isinstance(data.get("line_start"), int)
        assert isinstance(data.get("line_end"), int)
        assert data["line_start"] > 0
        assert data["line_end"] >= data["line_start"]

    @pytest.mark.asyncio
    async def test_line_boundaries_in_subdir(self, mock_mcp, config_path):
        def _mock_isfile(path):
            return "heating" in path

        with (
            patch("tools.storage.load_registry", return_value={"data": {"entries": [], "entities": []}}),
            patch("os.path.isfile", side_effect=_mock_isfile),
            patch("os.path.isdir", return_value=True),
            patch("os.listdir", return_value=["heating.yaml"]),
            patch("builtins.open", side_effect=lambda *a, **kw: StringIO(self.SUBDIR_YAML)),
        ):
            register_storage_tools(mock_mcp, config_path)
            tool = mock_mcp._tools["get_template_entity_code"]
            data = json.loads(await tool(entity_id="sensor.heating_mode"))

        assert data["success"] is True
        assert "templates/heating.yaml" in data.get("file_path", "")
        assert isinstance(data.get("line_start"), int)
        assert data["line_start"] > 0


class TestOverlapScoreWithConditions:
    """Gap 11: conditions contribute 20% to overlap score."""

    OVERLAP_YAML = [
        {
            "id": "auto1",
            "alias": "Climate Control",
            "trigger": [
                {"platform": "numeric_state", "entity_id": "sensor.temperature", "above": 25},
            ],
            "condition": [
                {"condition": "state", "entity_id": "binary_sensor.window", "state": "off"},
            ],
            "action": [
                {"service": "climate.set_hvac_mode", "target": {"entity_id": "climate.living_room"}},
            ],
        },
        {
            "id": "auto2",
            "alias": "Climate Control",
            "trigger": [
                {"platform": "numeric_state", "entity_id": "sensor.temperature", "above": 25},
            ],
            "condition": [
                {"condition": "state", "entity_id": "binary_sensor.window", "state": "off"},
            ],
            "action": [
                {"service": "climate.set_hvac_mode", "target": {"entity_id": "climate.living_room"}},
            ],
        },
    ]

    def test_conditions_contribute_to_overlap(self, config_path):
        from tools.automations import _do_diagnose_automation_aliases

        with (
            patch("tools.automations._load_automations", return_value=self.OVERLAP_YAML),
            patch("tools.automations.make_ha_request") as mock_req,
        ):
            mock_req.return_value = {
                "success": True,
                "data": [
                    {
                        "entity_id": "automation.climate_control",
                        "state": "on",
                        "attributes": {"friendly_name": "Climate Control"},
                    }
                ],
            }
            result = _do_diagnose_automation_aliases(config_path)

        assert result["total_duplicates"] >= 1
        dup = result["duplicates"][0]
        assert dup.get("overlap_score", 0) >= 80


class TestHistoryGroupBy:
    """Gap 12: group_by parameter in history summary."""

    @pytest.mark.asyncio
    async def test_group_by_hour(self, mock_mcp):
        from tools.history import register_history_tools

        now = datetime.now(UTC)
        history_data = []
        for minute in range(0, 120, 5):
            ts = now - timedelta(minutes=minute)
            history_data.append(
                {
                    "entity_id": "sensor.temperature",
                    "state": str(round(20 + (minute % 30) / 5, 1)),
                    "last_changed": ts.isoformat(),
                }
            )

        with patch("tools.history.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": [history_data]}
            register_history_tools(mock_mcp, "http://ha", "token")
            tool = mock_mcp._tools["get_entity_state_history_summary"]
            data = json.loads(
                await tool(
                    entity_id="sensor.temperature",
                    hours_back=2,
                    group_by="hour",
                )
            )

        assert data["success"] is True
        assert data.get("grouped_by") == "hour"
        assert len(data.get("grouped", {})) > 0
        for bucket, stats in data["grouped"].items():
            assert "count" in stats and "min" in stats and "max" in stats and "avg" in stats

    @pytest.mark.asyncio
    async def test_group_by_none(self, mock_mcp):
        from tools.history import register_history_tools

        now = datetime.now(UTC)
        history_data = [
            {
                "entity_id": "sensor.temp",
                "state": "22.5",
                "last_changed": (now - timedelta(minutes=10)).isoformat(),
            }
        ]

        with patch("tools.history.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": [history_data]}
            register_history_tools(mock_mcp, "http://ha", "token")
            tool = mock_mcp._tools["get_entity_state_history_summary"]
            data = json.loads(await tool(entity_id="sensor.temp"))

        assert data["success"] is True
        assert "grouped_by" not in data
        assert "last_5_changes" in data
