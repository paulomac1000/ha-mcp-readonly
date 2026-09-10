"""Unit tests for budget-aware generation options, config validation, and wiring."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from context_generator.budget import SECTION_ORDER
from context_generator.config import GenerationConfig
from context_generator.formatters import ReportGenerator
from context_generator.provenance import ProvenanceTracker
from context_generator.snapshot import ComprehensiveSnapshotCollector
from server import _normalize_context_options


def _make_config(tmp_path: Path, **overrides: object) -> GenerationConfig:
    values: dict[str, object] = {
        "config_path": tmp_path,
        "output_path": tmp_path / "out.md",
        "ha_url": "http://test-ha",
        "ha_token": "test-token",
        "mode": "offline",
    }
    values.update(overrides)
    return GenerationConfig(**values)  # type: ignore[arg-type]


class TestGenerationConfigBudgetValidation:
    """GenerationConfig validates budget options eagerly."""

    def test_defaults_preserve_current_behavior(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)

        assert config.profile == "full"
        assert config.include_sections is None
        assert config.include_repository_files is True
        assert config.include_storage_records is True
        assert config.on_budget_exceeded == "auto"

    @pytest.mark.parametrize("profile", ("agent", "compact"))
    def test_valid_profiles_accepted(self, tmp_path: Path, profile: str) -> None:
        assert _make_config(tmp_path, profile=profile).profile == profile

    def test_unknown_profile_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="unknown profile"):
            _make_config(tmp_path, profile="tiny")

    @pytest.mark.parametrize("policy", ("auto", "fail", "truncate"))
    def test_valid_policies_accepted(self, tmp_path: Path, policy: str) -> None:
        assert _make_config(tmp_path, on_budget_exceeded=policy).on_budget_exceeded == policy

    def test_unknown_policy_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="unknown budget overflow policy"):
            _make_config(tmp_path, on_budget_exceeded="explode")

    def test_unknown_section_rejected_eagerly(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="unknown section"):
            _make_config(tmp_path, include_sections=("topology", "nope"))

    def test_non_boolean_repository_flag_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="include_repository_files must be a boolean"):
            _make_config(tmp_path, include_repository_files="yes")

    def test_non_boolean_storage_flag_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="include_storage_records must be a boolean"):
            _make_config(tmp_path, include_storage_records=1)


class TestProfileEnvNormalization:
    """Environment aliases resolve before the canonical config is built."""

    def test_detail_compact_resolves_profile(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "out.md"))
        monkeypatch.setenv("HA_CONTEXT_DETAIL", "compact")

        config = GenerationConfig.from_env()

        assert config.profile == "compact"
        assert "profile" not in os.environ

    def test_explicit_profile_with_conflicting_detail_rejected(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "out.md"))
        monkeypatch.setenv("HA_CONTEXT_PROFILE", "agent")
        monkeypatch.setenv("HA_CONTEXT_DETAIL", "compact")

        with pytest.raises(ValueError, match="conflict"):
            GenerationConfig.from_env()

    def test_matching_profile_and_detail_accepted(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "out.md"))
        monkeypatch.setenv("HA_CONTEXT_PROFILE", "compact")
        monkeypatch.setenv("HA_CONTEXT_DETAIL", "compact")

        assert GenerationConfig.from_env().profile == "compact"

    def test_invalid_detail_rejected(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "out.md"))
        monkeypatch.setenv("HA_CONTEXT_DETAIL", "mini")

        with pytest.raises(ValueError, match="HA_CONTEXT_DETAIL must be full or compact"):
            GenerationConfig.from_env()

    def test_sections_env_parses_comma_list(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "out.md"))
        monkeypatch.setenv("HA_CONTEXT_SECTIONS", "health, provenance, topology")

        config = GenerationConfig.from_env()

        assert config.include_sections == ("health", "provenance", "topology")

    @pytest.mark.parametrize(
        ("env_name", "raw", "expected"),
        (
            ("HA_CONTEXT_INCLUDE_FILES", "0", False),
            ("HA_CONTEXT_INCLUDE_FILES", "true", True),
            ("HA_CONTEXT_INCLUDE_STORAGE", "off", False),
            ("HA_CONTEXT_INCLUDE_STORAGE", "1", True),
        ),
    )
    def test_boolean_flags(
        self, tmp_path: Path, monkeypatch, env_name: str, raw: str, expected: bool
    ) -> None:
        monkeypatch.setenv("HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "out.md"))
        monkeypatch.setenv(env_name, raw)

        config = GenerationConfig.from_env()

        actual = (
            config.include_repository_files
            if env_name == "HA_CONTEXT_INCLUDE_FILES"
            else config.include_storage_records
        )
        assert actual is expected

    def test_invalid_boolean_flag_rejected(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "out.md"))
        monkeypatch.setenv("HA_CONTEXT_INCLUDE_FILES", "maybe")

        with pytest.raises(ValueError, match="must be a boolean value"):
            GenerationConfig.from_env()


class TestContextOptionNormalization:
    """REST option aliases normalize deterministically before dispatch."""

    def test_defaults_when_no_options(self) -> None:
        assert _normalize_context_options({}) == {
            "profile": "full",
            "max_output_bytes": None,
            "include_sections": None,
            "include_repository_files": True,
            "include_storage_records": True,
            "on_budget_exceeded": "auto",
        }

    def test_camel_case_aliases_normalize(self) -> None:
        options = _normalize_context_options(
            {
                "maxBytes": 4096,
                "sections": ["health"],
                "repositoryFiles": False,
                "storageRecords": False,
                "onBudgetExceeded": "truncate",
            }
        )

        assert options == {
            "profile": "full",
            "max_output_bytes": 4096,
            "include_sections": ("health",),
            "include_repository_files": False,
            "include_storage_records": False,
            "on_budget_exceeded": "truncate",
        }

    def test_detail_compact_selects_compact_profile(self) -> None:
        assert _normalize_context_options({"detail": "compact"})["profile"] == "compact"

    def test_conflicting_aliases_rejected(self) -> None:
        with pytest.raises(ValueError, match="conflicting values"):
            _normalize_context_options({"maxBytes": 4096, "max_bytes": 8192})

    def test_profile_detail_conflict_rejected(self) -> None:
        with pytest.raises(ValueError, match="conflict"):
            _normalize_context_options({"profile": "agent", "detail": "compact"})

    def test_unknown_profile_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown profile"):
            _normalize_context_options({"profile": "nano"})

    def test_unknown_section_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown section"):
            _normalize_context_options({"sections": ["everything"]})

    def test_non_integer_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an integer"):
            _normalize_context_options({"maxBytes": "4096"})

    def test_tiny_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match="too small"):
            _normalize_context_options({"maxBytes": 10})

    def test_non_boolean_repository_alias_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be a boolean"):
            _normalize_context_options({"include_files": "no"})


def _minimal_generator() -> ReportGenerator:
    registry = MagicMock()
    registry.states = [
        {"entity_id": "light.living_room", "state": "on", "attributes": {}},
    ]
    registry.entities = []
    registry.devices = []
    registry.areas = []
    registry.config_entries = []
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

    return ReportGenerator(registry, automation, dashboard, logs, templates, history)


class TestBudgetedReportRendering:
    """ReportGenerator renders sections under the configured byte budget."""

    def test_full_profile_renders_every_section(self, tmp_path: Path) -> None:
        generator = _minimal_generator()
        output = tmp_path / "full.md"

        manifest = generator.generate(str(output))

        content = output.read_text(encoding="utf-8")
        assert manifest.rendered == SECTION_ORDER
        assert manifest.truncated is False
        for key in SECTION_ORDER:
            if key == "snapshot":
                assert "Comprehensive Safe Data Snapshot" in content
        assert "Home Assistant Context for AI" in content

    def test_agent_profile_excludes_heavy_sections(self, tmp_path: Path) -> None:
        generator = _minimal_generator()
        generator.generation_config = _make_config(tmp_path, profile="agent")
        output = tmp_path / "agent.md"

        manifest = generator.generate(str(output))

        assert "snapshot" not in manifest.rendered
        assert "logs" not in manifest.rendered
        assert "recent_changes" not in manifest.rendered
        assert "topology" in manifest.rendered

    def test_truncate_policy_omits_sections_and_fits_budget(self, tmp_path: Path) -> None:
        generator = _minimal_generator()
        budget = 2048
        generator.generation_config = _make_config(
            tmp_path,
            on_budget_exceeded="truncate",
            max_output_bytes=budget,
        )
        output = tmp_path / "truncated.md"

        manifest = generator.generate(str(output))

        assert manifest.truncated is True
        assert manifest.omitted
        assert manifest.omitted_bytes > 0
        assert output.stat().st_size <= budget

    def test_fail_policy_raises_and_leaves_no_artifact_or_temp_files(self, tmp_path: Path) -> None:
        generator = _minimal_generator()
        generator.generation_config = _make_config(
            tmp_path,
            on_budget_exceeded="fail",
            max_output_bytes=2048,
        )
        output = tmp_path / "failed.md"
        output.write_text("previous artifact", encoding="utf-8")

        with pytest.raises(ValueError, match="exceeds configured output limit"):
            generator.generate(str(output))

        assert output.read_text(encoding="utf-8") == "previous artifact"
        assert [p.name for p in tmp_path.iterdir()] == ["failed.md"]

    def test_default_full_profile_still_fails_closed(self, tmp_path: Path) -> None:
        generator = _minimal_generator()
        generator.generation_config = _make_config(tmp_path, max_output_bytes=2048)
        output = tmp_path / "default.md"

        with pytest.raises(ValueError, match="exceeds configured output limit"):
            generator.generate(str(output))

        assert not output.exists()

    def test_mandatory_floor_raises_when_budget_too_small(self, tmp_path: Path) -> None:
        from context_generator.provenance import ProvenanceTracker as Tracker

        generator = _minimal_generator()
        tracker = Tracker()
        for index in range(50):
            tracker.record(
                f"source:{index}",
                method="rest",
                status="complete",
                records=1,
                size_bytes=128,
            )
        generator.provenance = tracker
        generator.generation_config = _make_config(
            tmp_path,
            on_budget_exceeded="truncate",
            max_output_bytes=1024,
        )
        output = tmp_path / "floor.md"

        with pytest.raises(ValueError, match="mandatory section"):
            generator.generate(str(output))

    def test_explicit_selection_renders_only_selected_sections(self, tmp_path: Path) -> None:
        generator = _minimal_generator()
        generator.generation_config = _make_config(
            tmp_path,
            include_sections=("provenance", "topology"),
        )
        output = tmp_path / "selected.md"

        manifest = generator.generate(str(output))

        assert manifest.rendered == ("source_provenance", "topology")


class TestSnapshotSourceCategorySplit:
    """Repository bodies and .storage records are independently suppressible."""

    def test_disabled_repository_files_records_policy_skip(self, tmp_path: Path) -> None:
        self._assert_repository_skip(tmp_path)

    def test_disabled_storage_records_records_policy_skip(self, tmp_path: Path) -> None:
        self._assert_storage_skip(tmp_path)

    def _assert_repository_skip(self, tmp_path: Path) -> None:
        (tmp_path / "configuration.yaml").write_text("default_config:\n", encoding="utf-8")
        config = _make_config(
            tmp_path,
            include_repository_files=False,
            include_storage_records=True,
        )
        provenance = ProvenanceTracker()
        collector = ComprehensiveSnapshotCollector(config, provenance)

        collector._collect_files()

        records = provenance.as_dict()
        assert records["repository_files"]["status"] == "skipped"
        assert "repository file bodies disabled" in records["repository_files"]["reason"]
        assert collector.data["files"]["config_tree"] == {}

    def _assert_storage_skip(self, tmp_path: Path) -> None:
        storage_dir = tmp_path / ".storage"
        storage_dir.mkdir()
        (storage_dir / "core.config").write_text('{"data": {}}\n', encoding="utf-8")
        config = _make_config(
            tmp_path,
            include_repository_files=True,
            include_storage_records=False,
        )
        provenance = ProvenanceTracker()
        collector = ComprehensiveSnapshotCollector(config, provenance)

        collector._collect_files()

        records = provenance.as_dict()
        assert records["storage_records"]["status"] == "skipped"
        assert "storage records disabled" in records["storage_records"]["reason"]
        assert collector.data["files"]["config_tree"] == {}

    def test_both_categories_disabled_skips_walk(self, tmp_path: Path) -> None:
        (tmp_path / "configuration.yaml").write_text("default_config:\n", encoding="utf-8")
        config = _make_config(
            tmp_path,
            include_repository_files=False,
            include_storage_records=False,
        )
        provenance = ProvenanceTracker()
        collector = ComprehensiveSnapshotCollector(config, provenance)

        collector._collect_files()

        records = provenance.as_dict()
        assert records["filesystem_snapshot"]["status"] == "skipped"
        assert "disabled by request" in records["filesystem_snapshot"]["reason"]
        assert collector.data["files"]["config_tree"] == {}
