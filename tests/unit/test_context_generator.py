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
