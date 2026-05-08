"""
Tests for context_generator/core.py
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from context_generator import constants
from context_generator.core import generate_context_file, main


class TestGenerateContextFile:
    """Tests for generate_context_file()."""

    def test_overrides_paths_and_credentials(self, tmp_path):
        """Test that generate_context_file overrides constants."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        output_file = tmp_path / "output" / "context.md"
        output_file.parent.mkdir()

        # Create minimal mock registry files
        storage_dir = config_dir / ".storage"
        storage_dir.mkdir()
        (storage_dir / "core.entity_registry").write_text(
            json.dumps({"data": {"entities": []}}), encoding="utf-8"
        )
        (storage_dir / "core.device_registry").write_text(
            json.dumps({"data": {"devices": []}}), encoding="utf-8"
        )
        (storage_dir / "core.area_registry").write_text(
            json.dumps({"data": {"areas": []}}), encoding="utf-8"
        )
        (storage_dir / "core.config_entries").write_text(
            json.dumps({"data": {"entries": []}}), encoding="utf-8"
        )

        # Create empty automations.yaml so the analyzer doesn't crash
        (config_dir / "automations.yaml").write_text("[]", encoding="utf-8")
        (config_dir / "scripts.yaml").write_text("{}", encoding="utf-8")
        (config_dir / "scenes.yaml").write_text("[]", encoding="utf-8")
        (config_dir / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")

        with patch("context_generator.core.RegistryCollector") as MockReg:
            reg = MagicMock()
            reg.states = []
            reg.entities = []
            reg.devices = []
            reg.areas = []
            reg.config_entries = []
            MockReg.return_value = reg

            with patch("context_generator.core.AutomationAnalyzer") as MockAuto:
                auto = MagicMock()
                auto.automation_analysis = []
                auto.script_analysis = []
                auto.scene_analysis = []
                auto.ghost_entities = []
                auto.conflicting_entities = []
                MockAuto.return_value = auto

                with patch("context_generator.core.DashboardAnalyzer") as MockDash:
                    dash = MagicMock()
                    dash.entity_in_dashboards = []
                    MockDash.return_value = dash

                    with patch("context_generator.core.LogAnalyzer") as MockLog:
                        log = MagicMock()
                        log.errors = []
                        MockLog.return_value = log

                        with patch("context_generator.core.TemplateEntityCollector") as MockTpl:
                            tpl = MagicMock()
                            tpl.template_entities = []
                            MockTpl.return_value = tpl

                            with patch("context_generator.core.HistoryAnalyzer") as MockHist:
                                hist = MagicMock()
                                MockHist.return_value = hist

                                with patch("context_generator.core.ReportGenerator") as MockReport:
                                    MockReport.return_value = MagicMock()

                                    # Mock make_ha_request for states
                                    with patch(
                                        "context_generator.analyzers.make_ha_request",
                                        return_value={"success": True, "data": []},
                                    ):
                                        with patch(
                                            "context_generator.utils.make_ha_request",
                                            return_value={"success": True, "data": []},
                                        ):
                                            result = generate_context_file(
                                                config_path=str(config_dir),
                                                output_path=str(output_file),
                                                ha_url="http://test-ha:8123",
                                                ha_token="test-token",
                                                mode="offline",
                                            )

        assert result["output_file"] == str(output_file)
        assert result["config_path"] == str(config_dir)
        assert result["mode"] == "offline"
        assert constants.HA_CONFIG_PATH == str(config_dir)
        assert constants.OUTPUT_FILE == str(output_file)
        assert constants.HA_URL == "http://test-ha:8123"
        assert constants.HA_TOKEN == "test-token"

    def test_creates_output_directory(self, tmp_path):
        """Test that generate_context_file creates output directory if missing."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        output_file = tmp_path / "deep" / "nested" / "context.md"

        storage_dir = config_dir / ".storage"
        storage_dir.mkdir()
        for fname in (
            "core.entity_registry",
            "core.device_registry",
            "core.area_registry",
            "core.config_entries",
        ):
            (storage_dir / fname).write_text(json.dumps({"data": {}}), encoding="utf-8")
        (config_dir / "automations.yaml").write_text("[]", encoding="utf-8")
        (config_dir / "scripts.yaml").write_text("{}", encoding="utf-8")
        (config_dir / "scenes.yaml").write_text("[]", encoding="utf-8")
        (config_dir / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")

        with patch("context_generator.core.RegistryCollector") as MockReg:
            reg = MagicMock()
            reg.states = []
            MockReg.return_value = reg

            with patch("context_generator.core.AutomationAnalyzer") as MockAuto:
                auto = MagicMock()
                auto.automation_analysis = []
                auto.script_analysis = []
                auto.scene_analysis = []
                auto.ghost_entities = []
                auto.conflicting_entities = []
                MockAuto.return_value = auto

                with patch("context_generator.core.DashboardAnalyzer") as MockDash:
                    dash = MagicMock()
                    dash.entity_in_dashboards = []
                    MockDash.return_value = dash

                    with patch("context_generator.core.LogAnalyzer") as MockLog:
                        log = MagicMock()
                        log.errors = []
                        MockLog.return_value = log

                        with patch("context_generator.core.TemplateEntityCollector") as MockTpl:
                            tpl = MagicMock()
                            tpl.template_entities = []
                            MockTpl.return_value = tpl

                            with patch("context_generator.core.HistoryAnalyzer") as MockHist:
                                hist = MagicMock()
                                MockHist.return_value = hist

                                with patch("context_generator.core.ReportGenerator") as MockReport:
                                    MockReport.return_value = MagicMock()

                                    with patch(
                                        "context_generator.analyzers.make_ha_request",
                                        return_value={"success": True, "data": []},
                                    ):
                                        with patch(
                                            "context_generator.utils.make_ha_request",
                                            return_value={"success": True, "data": []},
                                        ):
                                            generate_context_file(
                                                config_path=str(config_dir),
                                                output_path=str(output_file),
                                            )

        assert output_file.parent.exists()


class TestMain:
    """Tests for main()."""

    def test_main_exits_on_registry_failure(self, tmp_path, monkeypatch):
        """main() should exit(1) when registry collection fails."""
        monkeypatch.setattr(constants, "HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setattr(constants, "OUTPUT_FILE", str(tmp_path / "out.md"))

        with patch("context_generator.core.RegistryCollector") as MockReg:
            reg = MagicMock()
            reg.collect.return_value = False
            MockReg.return_value = reg

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_main_success_flow(self, tmp_path, monkeypatch):
        """main() should succeed when all collectors work."""
        monkeypatch.setattr(constants, "HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setattr(constants, "OUTPUT_FILE", str(tmp_path / "out.md"))

        with patch("context_generator.core.RegistryCollector") as MockReg:
            reg = MagicMock()
            reg.collect.return_value = True
            reg.states = []
            MockReg.return_value = reg

            with patch("context_generator.core.AutomationAnalyzer") as MockAuto:
                auto = MagicMock()
                auto.automation_analysis = []
                auto.script_analysis = []
                auto.scene_analysis = []
                auto.ghost_entities = []
                auto.conflicting_entities = []
                MockAuto.return_value = auto

                with patch("context_generator.core.DashboardAnalyzer") as MockDash:
                    dash = MagicMock()
                    dash.entity_in_dashboards = []
                    MockDash.return_value = dash

                    with patch("context_generator.core.LogAnalyzer") as MockLog:
                        log = MagicMock()
                        log.errors = []
                        MockLog.return_value = log

                        with patch("context_generator.core.TemplateEntityCollector") as MockTpl:
                            tpl = MagicMock()
                            tpl.template_entities = []
                            MockTpl.return_value = tpl

                            with patch("context_generator.core.HistoryAnalyzer") as MockHist:
                                hist = MagicMock()
                                MockHist.return_value = hist

                                with patch("context_generator.core.ReportGenerator") as MockReport:
                                    MockReport.return_value = MagicMock()

                                    # Should not raise
                                    main()

                                    MockReport.assert_called_once()


class TestContextGeneratorUtils:
    """Tests for context_generator/utils.py helper functions."""

    def test_extract_entities_from_template(self):
        """Extract entity IDs from Jinja2 templates."""
        from context_generator.utils import extract_entities_from_template

        template = (
            "{{ states('sensor.temperature') }} and {{ is_state('light.living_room', 'on') }}"
        )
        entities = extract_entities_from_template(template)
        assert "sensor.temperature" in entities
        assert "light.living_room" in entities

    def test_extract_entities_from_template_states_dot(self):
        """Extract entities from states.xxx.yyy pattern."""
        from context_generator.utils import extract_entities_from_template

        template = "{{ states.sensor.temperature }} {{ states.light.living_room }}"
        entities = extract_entities_from_template(template)
        assert len(entities) >= 1

    def test_extract_entities_from_template_empty(self):
        from context_generator.utils import extract_entities_from_template

        assert extract_entities_from_template("") == set()
        assert extract_entities_from_template("no entities here") == set()

    def test_extract_services(self):
        """Extract service calls from automation actions."""
        from context_generator.utils import extract_services

        actions = [
            {"service": "light.turn_on", "target": {"entity_id": "light.living_room"}},
            {"service": "notify.mobile", "data": {"message": "test"}},
        ]
        services = extract_services(actions)
        assert "light.turn_on" in services
        assert "notify.mobile" in services

    def test_extract_services_empty(self):
        from context_generator.utils import extract_services

        assert extract_services([]) == set()

    def test_extract_trigger_info(self):
        """Extract info from automation trigger. Returns (entities_set, platforms_list)."""
        from context_generator.utils import extract_trigger_info

        trigger = {"platform": "state", "entity_id": "binary_sensor.motion", "to": "on"}
        entities, platforms = extract_trigger_info(trigger)
        assert isinstance(entities, set)
        assert isinstance(platforms, list)
        assert "state" in platforms

    def test_extract_entities_from_data(self):
        """Extract entity references from service call data."""
        from context_generator.utils import extract_entities_from_data

        result = extract_entities_from_data({"entity_id": "light.living_room"})
        assert "light.living_room" in result

    def test_is_ignorable_entity(self):
        """Check if entity is in ignorable domains/patterns."""
        from context_generator.utils import is_ignorable_entity

        assert is_ignorable_entity("sun.sun") is True
        assert is_ignorable_entity("update.home_assistant") is True
        assert is_ignorable_entity("sensor.temperature") is False

    def test_get_best_name_entity(self):
        from context_generator.utils import get_best_name

        assert get_best_name({"name": "Custom", "entity_id": "sensor.x"}, "entity") == "Custom"
        assert get_best_name({"entity_id": "sensor.x"}, "entity") == "sensor.x"

    def test_get_best_name_device(self):
        from context_generator.utils import get_best_name

        assert get_best_name({"name_by_user": "User", "name": "Default"}, "device") == "User"
        assert get_best_name({"name": "Default"}, "device") == "Default"

    def test_resolve_area_id(self):
        from context_generator.utils import resolve_area_id

        entity = {"area_id": "kitchen"}
        assert resolve_area_id(entity, {}) == "kitchen"

    def test_load_yaml_file(self, tmp_path):
        from context_generator.utils import load_yaml_file

        f = tmp_path / "test.yaml"
        f.write_text("key: value\n")
        result = load_yaml_file(str(f))
        assert result == {"key": "value"}

    def test_validate_yaml_syntax_valid(self):
        from context_generator.utils import validate_yaml_syntax

        result = validate_yaml_syntax("key: value\n")
        assert result["syntax_valid"] is True

    def test_validate_yaml_syntax_invalid(self):
        from context_generator.utils import validate_yaml_syntax

        result = validate_yaml_syntax("key: value: bad\n\n")
        assert result["syntax_valid"] is False

    def test_make_ha_request(self):
        from context_generator.utils import make_ha_request

        with patch("context_generator.utils.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {"data": "ok"}
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            result = make_ha_request("/api/states")
            assert result["success"] is True

    def test_slugify(self):
        from context_generator.utils import slugify

        assert slugify("Hello World") == "hello_world"
        assert slugify("Test-123") == "test_123"


class TestContextGeneratorAnalyzers:
    """Tests for context_generator/analyzers.py core classes."""

    def test_registry_collector_basic(self, tmp_path, monkeypatch):
        """Test RegistryCollector.collect() with minimal data."""
        from context_generator import constants
        from context_generator.analyzers import RegistryCollector

        monkeypatch.setattr(constants, "HA_CONFIG_PATH", str(tmp_path))
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.entity_registry").write_text(
            json.dumps({"data": {"entities": []}}), encoding="utf-8"
        )
        (storage / "core.device_registry").write_text(
            json.dumps({"data": {"devices": []}}), encoding="utf-8"
        )
        (storage / "core.area_registry").write_text(
            json.dumps({"data": {"areas": []}}), encoding="utf-8"
        )
        (storage / "core.config_entries").write_text(
            json.dumps({"data": {"entries": []}}), encoding="utf-8"
        )

        with patch("context_generator.analyzers.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": []}
            rc = RegistryCollector()
            result = rc.collect()
            assert result is True
            assert rc.states == []

    def test_automation_analyzer_basic(self, tmp_path, monkeypatch):
        """Test AutomationAnalyzer with empty automations."""
        from context_generator import constants
        from context_generator.analyzers import AutomationAnalyzer, RegistryCollector

        monkeypatch.setattr(constants, "HA_CONFIG_PATH", str(tmp_path))
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.entity_registry").write_text(
            json.dumps({"data": {"entities": []}}), encoding="utf-8"
        )
        (storage / "core.device_registry").write_text(
            json.dumps({"data": {"devices": []}}), encoding="utf-8"
        )
        (storage / "core.area_registry").write_text(
            json.dumps({"data": {"areas": []}}), encoding="utf-8"
        )
        (storage / "core.config_entries").write_text(
            json.dumps({"data": {"entries": []}}), encoding="utf-8"
        )

        (tmp_path / "automations.yaml").write_text("[]", encoding="utf-8")
        (tmp_path / "scripts.yaml").write_text("{}", encoding="utf-8")
        (tmp_path / "scenes.yaml").write_text("[]", encoding="utf-8")

        with patch("context_generator.analyzers.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": []}
            rc = RegistryCollector()
            rc.collect()

            aa = AutomationAnalyzer(rc)
            aa.collect()
            aa.analyze()
            assert isinstance(aa.automation_analysis, list)

    def test_log_analyzer_basic(self, tmp_path, monkeypatch):
        """Test LogAnalyzer with no log file."""
        from context_generator import constants
        from context_generator.analyzers import LogAnalyzer

        monkeypatch.setattr(constants, "HA_CONFIG_PATH", str(tmp_path))
        la = LogAnalyzer()
        la.analyze(hours=1)
        assert isinstance(la.errors, list)

    def test_dashboard_analyzer_basic(self, tmp_path, monkeypatch):
        """Test DashboardAnalyzer with no dashboards."""
        from context_generator import constants
        from context_generator.analyzers import DashboardAnalyzer, RegistryCollector

        monkeypatch.setattr(constants, "HA_CONFIG_PATH", str(tmp_path))
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.entity_registry").write_text(
            json.dumps({"data": {"entities": []}}), encoding="utf-8"
        )
        (storage / "core.device_registry").write_text(
            json.dumps({"data": {"devices": []}}), encoding="utf-8"
        )
        (storage / "core.area_registry").write_text(
            json.dumps({"data": {"areas": []}}), encoding="utf-8"
        )
        (storage / "core.config_entries").write_text(
            json.dumps({"data": {"entries": []}}), encoding="utf-8"
        )

        with patch("context_generator.analyzers.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": []}
            rc = RegistryCollector()
            rc.collect()

            da = DashboardAnalyzer(rc)
            da.analyze()
            assert isinstance(da.entity_in_dashboards, dict)

    def test_template_entity_collector(self, tmp_path, monkeypatch):
        """Test TemplateEntityCollector with no templates."""
        from context_generator import constants
        from context_generator.analyzers import RegistryCollector, TemplateEntityCollector

        monkeypatch.setattr(constants, "HA_CONFIG_PATH", str(tmp_path))
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.entity_registry").write_text(
            json.dumps({"data": {"entities": []}}), encoding="utf-8"
        )
        (storage / "core.device_registry").write_text(
            json.dumps({"data": {"devices": []}}), encoding="utf-8"
        )
        (storage / "core.area_registry").write_text(
            json.dumps({"data": {"areas": []}}), encoding="utf-8"
        )
        (storage / "core.config_entries").write_text(
            json.dumps({"data": {"entries": []}}), encoding="utf-8"
        )

        with patch("context_generator.analyzers.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": []}
            rc = RegistryCollector()
            rc.collect()

            tec = TemplateEntityCollector(rc)
            tec.collect()
            assert isinstance(tec.template_entities, list)

    def test_history_analyzer_basic(self, tmp_path, monkeypatch):
        """Test HistoryAnalyzer with no entity history."""
        from context_generator import constants
        from context_generator.analyzers import HistoryAnalyzer, RegistryCollector

        monkeypatch.setattr(constants, "HA_CONFIG_PATH", str(tmp_path))
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.entity_registry").write_text(
            json.dumps({"data": {"entities": []}}), encoding="utf-8"
        )
        (storage / "core.device_registry").write_text(
            json.dumps({"data": {"devices": []}}), encoding="utf-8"
        )
        (storage / "core.area_registry").write_text(
            json.dumps({"data": {"areas": []}}), encoding="utf-8"
        )
        (storage / "core.config_entries").write_text(
            json.dumps({"data": {"entries": []}}), encoding="utf-8"
        )

        with patch("context_generator.analyzers.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": []}
            rc = RegistryCollector()
            rc.collect()

            ha = HistoryAnalyzer(rc)
            ha.analyze(hours=1)
            assert isinstance(ha.recent_changes, list)


class TestContextGeneratorFormatter:
    """Tests for context_generator/formatters.py ReportGenerator."""

    def test_report_generator_basic(self, tmp_path, monkeypatch):
        """Test that ReportGenerator produces an output file with minimal data."""
        from context_generator import constants as cg_constants
        from context_generator.formatters import ReportGenerator

        registry = MagicMock()
        registry.states = [
            {
                "entity_id": "light.living_room",
                "state": "on",
                "attributes": {"friendly_name": "Salon Light"},
            },
            {
                "entity_id": "sensor.temp",
                "state": "22.5",
                "attributes": {"unit_of_measurement": "°C"},
            },
        ]
        registry.entities = []
        registry.devices = []
        registry.areas = []
        registry.config_entries = []
        registry.entities_map = {}
        registry.devices_map = {}
        registry.areas_map = {}
        registry.states_map = {
            "light.living_room": registry.states[0],
            "sensor.temp": registry.states[1],
        }
        registry.config_entries_map = {}
        registry.entity_to_platform = {}
        registry.entity_to_device = {}
        registry.entity_to_config_entry = {}
        registry.device_to_config_entry = {}
        registry.config_entry_health = {}

        automation = MagicMock()
        automation.automation_analysis = []
        automation.script_analysis = []
        automation.scene_analysis = []
        automation.ghost_entities = []
        automation.conflicting_entities = {}
        automation.blueprints = []

        dashboard = MagicMock()
        dashboard.entity_in_dashboards = {}
        dashboard.dashboards_found = []
        dashboard.missing_entities = {}

        logs = MagicMock()
        logs.errors = []
        logs.startup_errors = []

        templates = MagicMock()
        templates.template_entities = []
        templates.validation_errors = []

        history = MagicMock()
        history.recent_changes = []

        monkeypatch.setattr(cg_constants, "HA_URL", "http://test-ha")
        monkeypatch.setattr(cg_constants, "HA_CONFIG_PATH", str(tmp_path))

        output = tmp_path / "out.md"
        gen = ReportGenerator(registry, automation, dashboard, logs, templates, history)
        gen.generate(str(output))

        assert output.exists()
        content = output.read_text()
        assert "Home Assistant Context for AI" in content
        assert "light.living_room" in content


@pytest.fixture
def config_path(tmp_path) -> str:
    return str(tmp_path)


@pytest.fixture
def ha_url():
    return "http://test-ha"


@pytest.fixture
def ha_token():
    return "test-token"


class TestContextGeneratorV10:
    """Tests for Context Generator v1.0 new analyzers."""

    @pytest.fixture(autouse=True)
    def setup(self, config_path, ha_url, ha_token):
        import os

        os.environ["HA_CONFIG_PATH"] = config_path
        self.config_path = config_path

    def test_person_analyzer_collects_persons(self):
        """PersonAnalyzer should collect person entities, states, trackers."""
        from unittest.mock import patch

        from context_generator.analyzers import PersonAnalyzer, RegistryCollector

        mock_result = {
            "success": True,
            "data": [
                {
                    "entity_id": "person.test_user",
                    "state": "home",
                    "attributes": {
                        "friendly_name": "Test User",
                        "latitude": 52.4,
                        "longitude": 16.9,
                        "source": "gps",
                        "device_trackers": ["device_tracker.test_phone"],
                    },
                    "last_changed": "2024-01-01T10:00:00+00:00",
                    "last_updated": "2024-01-01T10:00:00+00:00",
                },
                {
                    "entity_id": "device_tracker.test_phone",
                    "state": "home",
                    "attributes": {"battery": 85, "source_type": "gps"},
                    "last_changed": "2024-01-01T10:00:00+00:00",
                    "last_updated": "2024-01-01T10:00:00+00:00",
                },
            ],
        }

        with (
            patch("context_generator.analyzers.load_registry", return_value={}),
            patch("context_generator.analyzers.make_ha_request", return_value=mock_result),
        ):
            rc = RegistryCollector()
            assert rc.collect() is True

            pa = PersonAnalyzer(rc)
            pa.collect()

            assert len(pa.persons) == 1
            assert pa.persons[0]["entity_id"] == "person.test_user"
            assert pa.persons[0]["state"] == "home"
            assert pa.persons[0]["latitude"] == 52.4
            assert "device_tracker.test_phone" in pa.tracker_states
            assert pa.tracker_states["device_tracker.test_phone"]["state"] == "home"
            assert pa.tracker_states["device_tracker.test_phone"]["battery"] == 85

    def test_zone_analyzer_collects_zones(self):
        """ZoneAnalyzer should collect zones from config entries and API states."""
        from unittest.mock import patch

        from context_generator.analyzers import RegistryCollector, ZoneAnalyzer

        mock_states = {
            "success": True,
            "data": [
                {
                    "entity_id": "zone.home",
                    "state": "zoning",
                    "attributes": {
                        "friendly_name": "Home",
                        "latitude": 52.4,
                        "longitude": 16.9,
                        "radius": 100,
                    },
                },
                {
                    "entity_id": "zone.work",
                    "state": "zoning",
                    "attributes": {
                        "friendly_name": "Work",
                        "latitude": 52.3,
                        "longitude": 16.8,
                        "radius": 200,
                    },
                },
                {
                    "entity_id": "person.test_user",
                    "state": "zone.home",
                    "attributes": {"friendly_name": "Test User"},
                    "last_changed": "2024-01-01T10:00:00+00:00",
                    "last_updated": "2024-01-01T10:00:00+00:00",
                },
            ],
        }

        mock_entries = {
            "data": {
                "entries": [
                    {
                        "domain": "zone",
                        "title": "Home",
                        "data": {"latitude": 52.4, "longitude": 16.9, "radius": 100},
                    },
                    {
                        "domain": "zone",
                        "title": "Work",
                        "data": {"latitude": 52.3, "longitude": 16.8, "radius": 200},
                    },
                ]
            }
        }

        with (
            patch("context_generator.analyzers.load_registry", return_value=mock_entries),
            patch("context_generator.analyzers.make_ha_request", return_value=mock_states),
        ):
            rc = RegistryCollector()
            assert rc.collect() is True

            za = ZoneAnalyzer(rc)
            za.collect()

            assert len(za.zones) >= 1
            zone_ids = [z["entity_id"] for z in za.zones]
            assert "zone.home" in zone_ids
            assert len(za.persons_in_zones["zone.home"]) == 1

    def test_energy_analyzer_collects_sensors(self):
        """EnergyAnalyzer should collect energy/power sensors."""
        from unittest.mock import patch

        from context_generator.analyzers import EnergyAnalyzer, RegistryCollector

        mock_states = {
            "success": True,
            "data": [
                {
                    "entity_id": "sensor.total_energy",
                    "state": "1234.5",
                    "attributes": {
                        "device_class": "energy",
                        "unit_of_measurement": "kWh",
                        "friendly_name": "Total Energy",
                    },
                    "last_changed": "2024-01-01T10:00:00+00:00",
                    "last_updated": "2024-01-01T10:00:00+00:00",
                },
                {
                    "entity_id": "sensor.fridge_power",
                    "state": "42",
                    "attributes": {
                        "device_class": "power",
                        "unit_of_measurement": "W",
                        "friendly_name": "Fridge Power",
                    },
                    "last_changed": "2024-01-01T10:00:00+00:00",
                    "last_updated": "2024-01-01T10:00:00+00:00",
                },
            ],
        }

        mock_energy = {"success": True, "data": {"info": "mock"}}

        with (
            patch("context_generator.analyzers.load_registry", return_value={}),
            patch("context_generator.analyzers.make_ha_request") as mock_req,
        ):
            mock_req.side_effect = [mock_states, mock_energy]
            rc = RegistryCollector()
            assert rc.collect() is True

            ea = EnergyAnalyzer(rc)
            ea.collect()

            assert len(ea.energy_sensors) >= 1
            sensor_ids = [s["entity_id"] for s in ea.energy_sensors]
            assert "sensor.total_energy" in sensor_ids

    def test_helper_analyzer_collects_all_types(self):
        """HelperAnalyzer should collect timers, counters, input helpers."""
        from unittest.mock import patch

        from context_generator.analyzers import HelperAnalyzer, RegistryCollector

        mock_states = {
            "success": True,
            "data": [
                {
                    "entity_id": "timer.test_timer",
                    "state": "idle",
                    "attributes": {
                        "friendly_name": "Test Timer",
                        "duration": "0:05:00",
                        "remaining": "0:00:00",
                    },
                    "last_changed": "2024-01-01T10:00:00+00:00",
                    "last_updated": "2024-01-01T10:00:00+00:00",
                },
                {
                    "entity_id": "counter.test_counter",
                    "state": "5",
                    "attributes": {
                        "friendly_name": "Test Counter",
                        "min": 0,
                        "max": 100,
                        "step": 1,
                    },
                    "last_changed": "2024-01-01T10:00:00+00:00",
                    "last_updated": "2024-01-01T10:00:00+00:00",
                },
                {
                    "entity_id": "input_boolean.test_bool",
                    "state": "on",
                    "attributes": {"friendly_name": "Test Bool"},
                    "last_changed": "2024-01-01T10:00:00+00:00",
                    "last_updated": "2024-01-01T10:00:00+00:00",
                },
                {
                    "entity_id": "input_number.test_number",
                    "state": "42.0",
                    "attributes": {
                        "friendly_name": "Test Number",
                        "min": 0,
                        "max": 100,
                        "step": 0.5,
                        "unit_of_measurement": "%",
                    },
                    "last_changed": "2024-01-01T10:00:00+00:00",
                    "last_updated": "2024-01-01T10:00:00+00:00",
                },
            ],
        }

        with (
            patch("context_generator.analyzers.load_registry", return_value={}),
            patch("context_generator.analyzers.make_ha_request", return_value=mock_states),
        ):
            rc = RegistryCollector()
            assert rc.collect() is True

            ha = HelperAnalyzer(rc)
            ha.collect()

            assert len(ha.timers) == 1
            assert len(ha.counters) == 1
            assert len(ha.input_booleans) == 1
            assert len(ha.input_numbers) == 1
            assert ha.timers[0]["entity_id"] == "timer.test_timer"
            assert ha.counters[0]["state"] == "5"
            assert ha.input_booleans[0]["state"] == "on"

    def test_service_catalog_analyzer(self):
        """ServiceCatalogAnalyzer should list available services."""
        from unittest.mock import patch

        from context_generator.analyzers import RegistryCollector, ServiceCatalogAnalyzer

        mock_services = {
            "success": True,
            "data": [
                {
                    "domain": "light",
                    "services": {
                        "turn_on": {"description": "Turn on light"},
                        "turn_off": {"description": "Turn off light"},
                    },
                },
                {"domain": "switch", "services": {"toggle": {"description": "Toggle switch"}}},
            ],
        }
        mock_states = {"success": True, "data": []}

        with (
            patch("context_generator.analyzers.load_registry", return_value={}),
            patch("context_generator.analyzers.make_ha_request") as mock_req,
        ):
            mock_req.side_effect = [mock_states, mock_services]
            rc = RegistryCollector()
            assert rc.collect() is True

            sca = ServiceCatalogAnalyzer(rc)
            sca.collect()

            assert sca.total_services == 3
            assert "light" in sca.services
            assert len(sca.services["light"]) == 2

    def test_hacs_analyzer_collects(self):
        """HacsAnalyzer should collect HACS repos and custom components."""
        import json
        import os
        import tempfile
        from unittest.mock import patch

        from context_generator.analyzers import HacsAnalyzer, RegistryCollector

        mock_hacs = {
            "data": {
                "repositories": [
                    {
                        "name": "Test Card",
                        "category": "lovelace",
                        "installed_version": "1.0.0",
                        "available_version": "1.1.0",
                        "status": "pending-update",
                    },
                ]
            }
        }
        mock_states = {"success": True, "data": []}

        # Create temporary custom_components directory
        tmpdir = tempfile.mkdtemp()
        import context_generator.analyzers as cg_analyzers
        import context_generator.constants as cg_constants

        original_constants_path = cg_constants.HA_CONFIG_PATH
        original_analyzers_path = cg_analyzers.HA_CONFIG_PATH
        cg_constants.HA_CONFIG_PATH = tmpdir
        cg_analyzers.HA_CONFIG_PATH = tmpdir

        cc_dir = os.path.join(tmpdir, "custom_components", "test_component")
        os.makedirs(cc_dir, exist_ok=True)
        with open(os.path.join(cc_dir, "manifest.json"), "w") as f:
            json.dump({"domain": "test_component", "version": "2.0.0", "dependencies": ["mqtt"]}, f)

        try:
            with (
                patch("context_generator.analyzers.load_registry") as mock_load,
                patch("context_generator.analyzers.make_ha_request") as mock_req,
            ):
                mock_load.side_effect = [{}, {}, {}, {}, mock_hacs]
                mock_req.return_value = mock_states
                rc = RegistryCollector()
                assert rc.collect() is True

                ha = HacsAnalyzer(rc)
                ha.collect()

                assert len(ha.hacs_repos) == 1
                assert ha.hacs_repos[0]["name"] == "Test Card"
                assert len(ha.custom_components) == 1
                assert ha.custom_components[0]["domain"] == "test_component"
        finally:
            cg_constants.HA_CONFIG_PATH = original_constants_path
            cg_analyzers.HA_CONFIG_PATH = original_analyzers_path
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_registry_domain_summary(self):
        """RegistryCollector should compute domain counts and state distribution."""
        from unittest.mock import patch

        from context_generator.analyzers import RegistryCollector

        mock_states = {
            "success": True,
            "data": [
                {
                    "entity_id": "light.test1",
                    "state": "on",
                    "attributes": {},
                    "last_changed": "2024-01-01T10:00:00+00:00",
                    "last_updated": "2024-01-01T10:00:00+00:00",
                },
                {
                    "entity_id": "light.test2",
                    "state": "off",
                    "attributes": {},
                    "last_changed": "2024-01-01T10:00:00+00:00",
                    "last_updated": "2024-01-01T10:00:00+00:00",
                },
                {
                    "entity_id": "sensor.temp",
                    "state": "22",
                    "attributes": {},
                    "last_changed": "2024-01-01T10:00:00+00:00",
                    "last_updated": "2024-01-01T10:00:00+00:00",
                },
                {
                    "entity_id": "sensor.humidity",
                    "state": "unavailable",
                    "attributes": {},
                    "last_changed": "2024-01-01T10:00:00+00:00",
                    "last_updated": "2024-01-01T10:00:00+00:00",
                },
            ],
        }

        with (
            patch("context_generator.analyzers.load_registry", return_value={}),
            patch("context_generator.analyzers.make_ha_request", return_value=mock_states),
        ):
            rc = RegistryCollector()
            assert rc.collect() is True

            assert rc.domain_counts["light"] == 2
            assert rc.domain_counts["sensor"] == 2
            assert rc.state_distribution["on"] == 1
            assert rc.state_distribution["off"] == 1
            assert rc.state_distribution["unavailable"] == 1
