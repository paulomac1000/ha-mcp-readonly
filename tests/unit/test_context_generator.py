"""Tests for context_generator/core.py and helper functions."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from context_generator.config import GenerationConfig
from context_generator.core import GenerationError, generate_context_file, main


class TestGenerateContextFile:
    """Explicit generation configuration must be isolated per call."""

    def test_builds_immutable_per_call_configuration(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HA_URL", "http://env-ha:8123")
        monkeypatch.setenv("HA_TOKEN", "env-token")
        monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "env.md"))
        captured = {}

        def fake_run(config: GenerationConfig):
            captured["config"] = config
            return {
                "output_file": str(config.output_path),
                "config_path": str(config.config_path),
                "mode": config.mode,
            }

        with patch("context_generator.core.run_generation", side_effect=fake_run):
            result = generate_context_file(
                config_path=str(tmp_path / "config"),
                output_path=str(tmp_path / "explicit.md"),
                ha_url="http://explicit-ha:8123",
                ha_token="explicit-token",
                mode="offline",
            )

        config = captured["config"]
        assert config.mode == "offline"
        assert config.ha_url == "http://explicit-ha:8123"
        assert config.ha_token == "explicit-token"
        assert config.output_path == tmp_path / "explicit.md"
        assert result["output_file"] == str(tmp_path / "explicit.md")
        # No environment or module-global mutation is used to pass per-run data.
        assert Path(os.environ["OUTPUT_PATH"]) == tmp_path / "env.md"

    def test_empty_credentials_clear_stale_environment(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HA_URL", "http://stale-ha:8123")
        monkeypatch.setenv("HA_TOKEN", "stale-token")
        captured = {}

        def fake_run(config: GenerationConfig):
            captured["config"] = config
            return {
                "output_file": str(config.output_path),
                "config_path": str(config.config_path),
                "mode": config.mode,
            }

        with patch("context_generator.core.run_generation", side_effect=fake_run):
            generate_context_file(
                config_path=str(tmp_path),
                output_path=str(tmp_path / "offline.md"),
                ha_url="",
                ha_token="",
                mode="offline",
            )

        assert captured["config"].ha_url == ""
        assert captured["config"].ha_token == ""
        assert captured["config"].network_enabled is False

    def test_offline_generation_never_touches_network_and_reports_sources(self, tmp_path):
        config_dir = tmp_path / "config"
        storage = config_dir / ".storage"
        storage.mkdir(parents=True)
        registries = {
            "core.entity_registry": {"data": {"entities": []}},
            "core.device_registry": {"data": {"devices": []}},
            "core.area_registry": {"data": {"areas": []}},
            "core.config_entries": {
                "data": {"entries": [{"entry_id": "x", "data": {"access_token": "hide-me"}}]}
            },
        }
        for name, payload in registries.items():
            (storage / name).write_text(json.dumps(payload), encoding="utf-8")
        (config_dir / "automations.yaml").write_text("[]", encoding="utf-8")
        (config_dir / "scripts.yaml").write_text("{}", encoding="utf-8")
        (config_dir / "scenes.yaml").write_text("[]", encoding="utf-8")
        (config_dir / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")
        (config_dir / "secrets.yaml").write_text("password: leak-me\n", encoding="utf-8")
        output = tmp_path / "nested" / "context.md"

        with (
            patch("requests.get", side_effect=AssertionError("offline network access")),
            patch("requests.post", side_effect=AssertionError("offline network access")),
        ):
            result = generate_context_file(
                config_path=str(config_dir),
                output_path=str(output),
                ha_url="http://stale-ha:8123",
                ha_token="stale-token",
                mode="offline",
            )

        text = output.read_text(encoding="utf-8")
        assert result["mode"] == "offline"
        assert result["completeness"] == "partial"
        assert "Source Provenance and Completeness" in text
        assert "Comprehensive Safe Data Snapshot" in text
        assert "hide-me" not in text
        assert "leak-me" not in text
        assert '"entry_id": "x"' in text
        assert '"data": {}' in text
        assert '"data_redacted": true' in text


class TestMain:
    def test_main_raises_controlled_error_on_required_source_failure(self, monkeypatch):
        config = GenerationConfig.from_env()
        with (
            patch("context_generator.core.GenerationConfig.from_env", return_value=config),
            patch("context_generator.core.run_generation", side_effect=GenerationError("failed")),
        ):
            with pytest.raises(GenerationError):
                main()

    def test_main_uses_environment_configuration(self, monkeypatch, tmp_path):
        config = GenerationConfig(
            config_path=tmp_path,
            output_path=tmp_path / "out.md",
            ha_url="",
            ha_token="",
            mode="offline",
        )
        with (
            patch("context_generator.core.GenerationConfig.from_env", return_value=config),
            patch("context_generator.core.run_generation", return_value={}) as run,
        ):
            main()
        run.assert_called_once_with(config)


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
        assert is_ignorable_entity("update.home_assistant") is False
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

    def test_make_ha_request(self, tmp_path):
        from context_generator.provenance import ProvenanceTracker
        from context_generator.runtime import GenerationRuntime, generation_scope
        from context_generator.utils import make_ha_request

        config = GenerationConfig(
            config_path=tmp_path,
            output_path=tmp_path / "out.md",
            ha_url="http://ha:8123",
            ha_token="token",
            mode="online",
        )
        runtime = GenerationRuntime(config=config, provenance=ProvenanceTracker())
        with patch("context_generator.utils.requests.get") as mock_get, generation_scope(runtime):
            mock_response = MagicMock()
            mock_response.json.return_value = {"data": "ok"}
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            result = make_ha_request("/api/states")
            assert result["success"] is True
            mock_get.assert_called_once()
            assert mock_get.call_args.args[0] == "http://ha:8123/api/states"
            assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer token"

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
        registry.updates_available = {"total": 0, "available": 0, "entities": []}

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
        import context_generator.constants as cg_constants

        original_constants_path = cg_constants.HA_CONFIG_PATH
        cg_constants.HA_CONFIG_PATH = tmpdir

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


class TestProvenanceAndComprehensiveSnapshot:
    def test_policy_exclusions_do_not_hide_missing_runtime_sources(self):
        from context_generator.provenance import ProvenanceTracker

        tracker = ProvenanceTracker()
        tracker.record(
            "storage:auth",
            method="storage",
            status="skipped",
            reason="policy: credential-bearing source blocked",
        )
        assert tracker.summary()["completeness"] == "complete"
        tracker.record(
            "states_api",
            method="rest",
            status="skipped",
            reason="offline mode",
        )
        assert tracker.summary()["completeness"] == "partial"

    def test_calendar_events_are_collected_for_every_calendar(self, tmp_path):
        from context_generator.provenance import ProvenanceTracker
        from context_generator.snapshot import ComprehensiveSnapshotCollector

        config = GenerationConfig(
            config_path=tmp_path,
            output_path=tmp_path / "out.md",
            ha_url="http://ha:8123",
            ha_token="token",
            mode="online",
            calendar_days=14,
        )
        provenance = ProvenanceTracker()

        def fake_request(endpoint: str, **kwargs):
            del kwargs
            if endpoint == "/api/calendars":
                return {
                    "success": True,
                    "data": [
                        {"entity_id": "calendar.family", "name": "Family"},
                        {"entity_id": "calendar.work", "name": "Work"},
                    ],
                }
            if endpoint.startswith("/api/calendars/calendar."):
                return {
                    "success": True,
                    "data": [{"summary": "Meeting", "description": "Bearer secret-token"}],
                }
            return {"success": True, "data": []}

        collector = ComprehensiveSnapshotCollector(config, provenance)
        with patch("context_generator.snapshot.make_ha_request", side_effect=fake_request):
            collector._collect_rest()

        events = collector.data["rest"]["calendar_events"]
        assert set(events) == {"calendar.family", "calendar.work"}
        assert events["calendar.family"][0]["description"] == "Bearer [REDACTED]"
        assert provenance.as_dict()["calendar_events:calendar.family"]["records"] == 1

    def test_todo_items_are_discovered_from_states(self, tmp_path):
        from context_generator.provenance import ProvenanceTracker
        from context_generator.snapshot import ComprehensiveSnapshotCollector

        class FakeWebSocket:
            def __init__(self):
                self.sent = []
                self.responses = []

            def send(self, value):
                request = json.loads(value)
                self.sent.append(request)
                self.responses.append(
                    json.dumps(
                        {
                            "id": request["id"],
                            "type": "result",
                            "success": True,
                            "result": {"items": [{"summary": "Buy milk", "uid": "1"}]},
                        }
                    )
                )

            def recv(self, timeout):
                del timeout
                return self.responses.pop(0)

        config = GenerationConfig(
            config_path=tmp_path,
            output_path=tmp_path / "out.md",
            ha_url="http://ha:8123",
            ha_token="token",
            mode="online",
        )
        provenance = ProvenanceTracker()
        collector = ComprehensiveSnapshotCollector(config, provenance)
        collector.data["rest"]["states_api"] = [
            {"entity_id": "todo.shopping", "state": "1"},
            {"entity_id": "sensor.temperature", "state": "20"},
        ]
        ws = FakeWebSocket()

        next_id = collector._collect_todo_items(ws, 9)

        assert next_id == 10
        assert ws.sent == [{"id": 9, "type": "todo/item/list", "entity_id": "todo.shopping"}]
        assert (
            collector.data["websocket"]["todo_items"]["todo.shopping"]["items"][0]["summary"]
            == "Buy milk"
        )
        assert provenance.as_dict()["todo_items:todo.shopping"]["records"] == 1
        assert provenance.as_dict()["todo_items"]["records"] == 1

    def test_weather_forecasts_collect_every_advertised_type(self, tmp_path):
        from context_generator.provenance import ProvenanceTracker
        from context_generator.snapshot import ComprehensiveSnapshotCollector

        class FakeWebSocket:
            def __init__(self):
                self.sent = []
                self.responses = []

            def send(self, value):
                request = json.loads(value)
                self.sent.append(request)
                if request["type"] == "unsubscribe_events":
                    self.responses.append(
                        json.dumps(
                            {
                                "id": request["id"],
                                "type": "result",
                                "success": True,
                                "result": None,
                            }
                        )
                    )
                    return
                self.responses.extend(
                    [
                        json.dumps(
                            {
                                "id": request["id"],
                                "type": "result",
                                "success": True,
                                "result": None,
                            }
                        ),
                        json.dumps(
                            {
                                "id": request["id"],
                                "type": "event",
                                "event": {
                                    "type": request["forecast_type"],
                                    "forecast": [
                                        {
                                            "datetime": "2026-08-07T12:00:00+00:00",
                                            "condition": "sunny",
                                        }
                                    ],
                                },
                            }
                        ),
                    ]
                )

            def recv(self, timeout):
                del timeout
                return self.responses.pop(0)

        config = GenerationConfig(
            config_path=tmp_path,
            output_path=tmp_path / "out.md",
            ha_url="http://ha:8123",
            ha_token="token",
            mode="online",
        )
        provenance = ProvenanceTracker()
        collector = ComprehensiveSnapshotCollector(config, provenance)
        collector.data["rest"]["states_api"] = [
            {
                "entity_id": "weather.home",
                "state": "sunny",
                "attributes": {"supported_features": 3},
            },
            {"entity_id": "sensor.temperature", "state": "20", "attributes": {}},
        ]
        ws = FakeWebSocket()

        next_id = collector._collect_weather_forecasts(ws, 20)

        assert next_id == 24
        subscribe_requests = [
            item for item in ws.sent if item["type"] == "weather/subscribe_forecast"
        ]
        unsubscribe_requests = [item for item in ws.sent if item["type"] == "unsubscribe_events"]
        assert [item["forecast_type"] for item in subscribe_requests] == ["daily", "hourly"]
        assert [item["id"] for item in unsubscribe_requests] == [21, 23]
        assert [item["subscription"] for item in unsubscribe_requests] == [20, 22]
        forecasts = collector.data["websocket"]["weather_forecasts"]["weather.home"]
        assert set(forecasts) == {"daily", "hourly"}
        assert forecasts["daily"][0]["condition"] == "sunny"
        assert provenance.as_dict()["weather_forecast:weather.home:daily"]["records"] == 1
        assert provenance.as_dict()["weather_forecasts"]["records"] == 2

    def test_websocket_collection_covers_static_and_dynamic_sources(self, tmp_path):
        from context_generator.provenance import ProvenanceTracker
        from context_generator.snapshot import ComprehensiveSnapshotCollector

        class FakeWebSocket:
            def __init__(self):
                self.responses = [json.dumps({"type": "auth_required"})]

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                del exc_type, exc, traceback
                return False

            def send(self, value):
                request = json.loads(value)
                if request.get("type") == "auth":
                    self.responses.append(json.dumps({"type": "auth_ok"}))
                    return
                if request["type"] == "todo/item/list":
                    result = {"items": [{"summary": "Review context", "uid": "1"}]}
                    self.responses.append(
                        json.dumps(
                            {
                                "id": request["id"],
                                "type": "result",
                                "success": True,
                                "result": result,
                            }
                        )
                    )
                    return
                if request["type"] == "weather/subscribe_forecast":
                    self.responses.extend(
                        [
                            json.dumps(
                                {
                                    "id": request["id"],
                                    "type": "result",
                                    "success": True,
                                    "result": None,
                                }
                            ),
                            json.dumps(
                                {
                                    "id": request["id"],
                                    "type": "event",
                                    "event": {
                                        "type": request["forecast_type"],
                                        "forecast": [{"condition": "sunny"}],
                                    },
                                }
                            ),
                        ]
                    )
                    return
                self.responses.append(
                    json.dumps(
                        {
                            "id": request["id"],
                            "type": "result",
                            "success": True,
                            "result": [{"source": request["type"]}],
                        }
                    )
                )

            def recv(self, timeout):
                del timeout
                return self.responses.pop(0)

        config = GenerationConfig(
            config_path=tmp_path,
            output_path=tmp_path / "out.md",
            ha_url="http://ha:8123",
            ha_token="token",
            mode="online",
        )
        provenance = ProvenanceTracker()
        collector = ComprehensiveSnapshotCollector(config, provenance)
        collector.data["rest"]["states_api"] = [
            {"entity_id": "todo.tasks", "state": "1", "attributes": {}},
            {
                "entity_id": "weather.home",
                "state": "sunny",
                "attributes": {"supported_features": 1},
            },
        ]

        with patch("websockets.sync.client.connect", return_value=FakeWebSocket()):
            collector._collect_websocket()

        websocket = collector.data["websocket"]
        assert websocket["areas_ws"][0]["source"] == "config/area_registry/list"
        assert websocket["todo_items"]["todo.tasks"]["items"][0]["uid"] == "1"
        assert websocket["weather_forecasts"]["weather.home"]["daily"][0]["condition"] == "sunny"
        assert provenance.as_dict()["system_health_ws"]["status"] == "complete"
