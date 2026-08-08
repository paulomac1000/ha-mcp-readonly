import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from context_generator.config import GenerationConfig
from context_generator.core import GenerationError, generate_context_file, main


class TestGenerateContextFile:
    def test_explicit_output_path_takes_precedence_over_environment(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        requested = tmp_path / "requested.md"
        stale = tmp_path / "stale.md"
        monkeypatch.setenv("OUTPUT_FILE", str(stale))
        monkeypatch.setenv("OUTPUT_PATH", str(stale))

        with patch("context_generator.core.run_generation", return_value={"entities": 1}):
            result = generate_context_file(
                config_path=str(config_dir),
                output_path=str(requested),
                mode="offline",
            )

        assert result["output_file"] == str(requested.resolve())
        assert os.environ["OUTPUT_FILE"] == str(requested.resolve())
        assert os.environ["OUTPUT_PATH"] == str(requested.resolve())

    def test_explicit_network_values_are_synchronized_for_legacy_collectors(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        requested = tmp_path / "context.md"

        observed = {}

        def capture(config):
            observed["ha_url"] = os.environ.get("HA_URL")
            observed["ha_token"] = os.environ.get("HA_TOKEN")
            observed["mode"] = os.environ.get("CONTEXT_MODE")
            return {"entities": 0}

        with patch("context_generator.core.run_generation", side_effect=capture):
            generate_context_file(
                config_path=str(config_dir),
                output_path=str(requested),
                ha_url="http://example.invalid:8123",
                ha_token="one-run-token",
                mode="online",
            )

        assert observed == {
            "ha_url": "http://example.invalid:8123",
            "ha_token": "one-run-token",
            "mode": "online",
        }

    def test_environment_is_restored_after_generation(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        requested = tmp_path / "context.md"
        monkeypatch.setenv("HA_URL", "http://original.invalid")
        monkeypatch.setenv("HA_TOKEN", "original-token")
        monkeypatch.setenv("CONTEXT_MODE", "hybrid")

        with patch("context_generator.core.run_generation", return_value={"entities": 0}):
            generate_context_file(
                config_path=str(config_dir),
                output_path=str(requested),
                ha_url="http://temporary.invalid",
                ha_token="temporary-token",
                mode="offline",
            )

        assert os.environ["HA_URL"] == "http://original.invalid"
        assert os.environ["HA_TOKEN"] == "original-token"
        assert os.environ["CONTEXT_MODE"] == "hybrid"

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
        # Typed .storage sanitization removes the secret field entirely while
        # preserving a machine-readable indication that integration data was
        # intentionally reduced. The artifact must not depend on a literal
        # replacement marker to prove redaction.
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
