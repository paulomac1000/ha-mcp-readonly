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

    def test_budget_above_artifact_limit_rejected(self) -> None:
        from context_generator.config import MAX_CONTEXT_ARTIFACT_BYTES

        with pytest.raises(ValueError, match="exceeds the supported artifact size"):
            _normalize_context_options({"maxBytes": MAX_CONTEXT_ARTIFACT_BYTES + 1})

    def test_budget_at_artifact_limit_accepted(self) -> None:
        from context_generator.config import MAX_CONTEXT_ARTIFACT_BYTES

        assert (
            _normalize_context_options({"maxBytes": MAX_CONTEXT_ARTIFACT_BYTES})["max_output_bytes"]
            == MAX_CONTEXT_ARTIFACT_BYTES
        )

    def test_non_string_detail_rejected(self) -> None:
        with pytest.raises(ValueError, match="detail must be a string"):
            _normalize_context_options({"detail": 3})

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
        # The artifact itself must disclose budget omissions (#33 visibility):
        # an in-artifact Generation Notes notice lists the omitted sections.
        content = output.read_text(encoding="utf-8")
        assert "## Generation Notes" in content
        for omitted in manifest.omitted:
            assert omitted.section in content
        assert "Artifact completeness:" not in content

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

    def test_explicit_selection_keeps_mandatory_floor(self, tmp_path: Path) -> None:
        generator = _minimal_generator()
        generator.generation_config = _make_config(
            tmp_path,
            include_sections=("topology",),
        )
        output = tmp_path / "selected.md"

        manifest = generator.generate(str(output))

        assert manifest.rendered == (
            "executive_summary",
            "source_provenance",
            "topology",
        )


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
        assert records["repository_files"]["status"] == "skipped"
        assert records["storage_records"]["status"] == "skipped"
        assert collector.data["files"]["config_tree"] == {}


class TestOfflineRunResultContract:
    """The public run result reflects the effective selection and timestamp contract."""

    def test_offline_run_reports_effective_selection_and_iso_timestamp(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from datetime import datetime
        from unittest.mock import patch

        from context_generator.core import generate_context_file

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")
        (config_dir / "automations.yaml").write_text("[]", encoding="utf-8")
        (config_dir / "scripts.yaml").write_text("{}", encoding="utf-8")
        (config_dir / "scenes.yaml").write_text("[]", encoding="utf-8")
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
                profile="agent",
                include_sections=("topology",),
            )

        assert result["requested_sections"] == ["topology"]
        assert result["selected_sections"] == [
            "executive_summary",
            "source_provenance",
            "topology",
        ]
        assert result["rendered_sections"] == result["selected_sections"]
        assert result["truncated"] is False
        # Zero-record instances are an empty success, not a failure.
        assert result["entities"] == 0
        assert result["automations"] == 0
        assert result["uncompressed_bytes"] == result["output_bytes"]
        assert result["max_bytes"] >= result["output_bytes"]
        assert result["profile_revision"]
        assert datetime.fromisoformat(result["generated_at"]).tzinfo is not None
        assert len(result["output_sha256"]) == 64

        content = output.read_text(encoding="utf-8")
        generated_lines = [
            line for line in content.splitlines() if line.startswith("> **Generated:**")
        ]
        assert generated_lines, "artifact header must carry a Generated timestamp"
        timestamp = generated_lines[0].split(":** ", 1)[1].strip()
        parsed = datetime.fromisoformat(timestamp)
        assert parsed.tzinfo is not None and parsed.utcoffset() is not None


class TestIssueAcceptanceRegressions:
    """Acceptance regressions tied to the GitHub issues this PR closes."""

    @pytest.mark.parametrize("profile", ("full", "agent", "compact"))
    def test_sensitive_canaries_absent_under_every_profile(
        self, tmp_path: Path, monkeypatch, profile: str
    ) -> None:
        from unittest.mock import patch

        from context_generator.core import generate_context_file

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        storage = config_dir / ".storage"
        storage.mkdir()
        (storage / "supervisor_entry").write_text(
            '{"data": {"access_token": "hide-me"}}', encoding="utf-8"
        )
        (config_dir / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")
        (config_dir / "automations.yaml").write_text("[]", encoding="utf-8")
        (config_dir / "scripts.yaml").write_text("{}", encoding="utf-8")
        (config_dir / "scenes.yaml").write_text("[]", encoding="utf-8")
        (config_dir / "secrets.yaml").write_text("password: leak-me\n", encoding="utf-8")
        output = tmp_path / "context.md"

        with (
            patch("requests.get", side_effect=AssertionError("offline network access")),
            patch("requests.post", side_effect=AssertionError("offline network access")),
        ):
            generate_context_file(
                config_path=str(config_dir),
                output_path=str(output),
                ha_url="http://stale-ha:8123",
                ha_token="stale-token",
                mode="offline",
                profile=profile,
            )

        content = output.read_text(encoding="utf-8")
        assert "hide-me" not in content
        assert "leak-me" not in content

    def test_large_source_would_exceed_old_guard_yields_bounded_artifact(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A source tree whose legacy artifact exceeded the old 50 MB guard
        produces a bounded artifact with explicit omissions (issue #34)."""
        from unittest.mock import patch

        from context_generator.core import generate_context_file

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        bulky = config_dir / "bulky.yaml"
        line = "x" * 120 + "\n"
        with bulky.open("w", encoding="utf-8") as handle:
            for _ in range(52 * 1024 * 1024 // len(line)):
                handle.write(line)
        (config_dir / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")
        output = tmp_path / "context.md"

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
                profile="full",
                on_budget_exceeded="truncate",
                max_output_bytes=2 * 1024 * 1024,
            )

        assert result["output_bytes"] <= 2 * 1024 * 1024
        assert result["output_bytes"] < 50 * 1024 * 1024
        assert result["truncated"] is True
        omitted = {item["section"] for item in result["omitted_sections"]}
        assert "snapshot" in omitted

        # Issue #33 operational view: the agent profile bounds the same large
        # source tree naturally (heavy sections never selected, no stripping).
        bounded = tmp_path / "bounded" / "context.md"
        with (
            patch("requests.get", side_effect=AssertionError("offline network access")),
            patch("requests.post", side_effect=AssertionError("offline network access")),
        ):
            agent_result = generate_context_file(
                config_path=str(config_dir),
                output_path=str(bounded),
                ha_url="http://stale-ha:8123",
                ha_token="stale-token",
                mode="offline",
                profile="agent",
                on_budget_exceeded="auto",
                max_output_bytes=2 * 1024 * 1024,
            )

        assert agent_result["profile"] == "agent"
        assert agent_result["output_bytes"] <= 2 * 1024 * 1024
        assert agent_result["truncated"] is False
        heavy = {"snapshot", "logs", "recent_changes"}
        assert heavy.isdisjoint(agent_result["rendered_sections"])
        assert heavy.isdisjoint(agent_result["selected_sections"])

    def test_explicit_max_bytes_bypasses_host_env_entirely(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """An explicit byte budget must not parse or validate the env fallback."""
        from unittest.mock import patch

        from context_generator.core import generate_context_file

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")
        (config_dir / "automations.yaml").write_text("[]", encoding="utf-8")

        monkeypatch.setenv("HA_CONTEXT_MAX_OUTPUT_BYTES", "not-an-integer")
        output_one = tmp_path / "one.md"
        with (
            patch("requests.get", side_effect=AssertionError("offline network access")),
            patch("requests.post", side_effect=AssertionError("offline network access")),
        ):
            result = generate_context_file(
                config_path=str(config_dir),
                output_path=str(output_one),
                ha_url="http://stale-ha:8123",
                ha_token="stale-token",
                mode="offline",
                profile="compact",
                max_output_bytes=2 * 1024 * 1024,
            )
        assert result["max_bytes"] == 2 * 1024 * 1024

        monkeypatch.setenv("HA_CONTEXT_MAX_OUTPUT_BYTES", str(128 * 1024 * 1024 + 1))
        output_two = tmp_path / "two.md"
        with (
            patch("requests.get", side_effect=AssertionError("offline network access")),
            patch("requests.post", side_effect=AssertionError("offline network access")),
        ):
            result = generate_context_file(
                config_path=str(config_dir),
                output_path=str(output_two),
                ha_url="http://stale-ha:8123",
                ha_token="stale-token",
                mode="offline",
                profile="compact",
                max_output_bytes=1024 * 1024,
            )
        assert result["max_bytes"] == 1024 * 1024

    def test_omitted_max_bytes_falls_back_to_environment(self, tmp_path: Path, monkeypatch) -> None:
        """Omitting maxBytes inherits HA_CONTEXT_MAX_OUTPUT_BYTES when parseable."""
        from unittest.mock import patch

        from context_generator.core import generate_context_file

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")
        (config_dir / "automations.yaml").write_text("[]", encoding="utf-8")
        output = tmp_path / "context.md"
        monkeypatch.setenv("HA_CONTEXT_MAX_OUTPUT_BYTES", str(4 * 1024 * 1024))

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
                profile="compact",
            )

        assert result["max_bytes"] == 4 * 1024 * 1024

    def test_worker_path_is_isolated_from_host_budget_environment(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Explicit REST options must not be invalidated by host-side env state.

        The worker calls generate_context_file, which must not parse budget
        environment variables: a valid pre-validated request may not fail
        asynchronously because the server process carries unrelated or
        malformed budget environment values (issue review finding).
        """
        from unittest.mock import patch

        from context_generator.core import generate_context_file

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")
        (config_dir / "automations.yaml").write_text("[]", encoding="utf-8")
        output = tmp_path / "context.md"
        monkeypatch.setenv("HA_CONTEXT_PROFILE", "not-a-profile")
        monkeypatch.setenv("HA_CONTEXT_DETAIL", "bogus")
        monkeypatch.setenv("HA_CONTEXT_SECTIONS", "nope")
        monkeypatch.setenv("HA_CONTEXT_INCLUDE_FILES", "maybe")
        monkeypatch.setenv("HA_CONTEXT_INCLUDE_STORAGE", "perhaps")
        monkeypatch.setenv("HA_CONTEXT_ON_BUDGET_EXCEEDED", "explode")

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
                profile="agent",
                include_repository_files=False,
                include_storage_records=False,
                on_budget_exceeded="truncate",
                max_output_bytes=1024 * 1024,
            )

        assert result["profile"] == "agent"
        assert result["truncated"] is False
        heavy = {"snapshot", "logs", "recent_changes"}
        assert heavy.isdisjoint(result["selected_sections"])
        assert heavy.isdisjoint(result["rendered_sections"])

    def test_default_invocation_uses_env_paths_as_paths(self, tmp_path: Path, monkeypatch) -> None:
        """generate_context_file() without path args inherits Path-typed env paths."""
        from unittest.mock import patch

        from context_generator.core import generate_context_file

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")
        output_root = tmp_path / "out"
        output_root.mkdir()
        monkeypatch.setenv("HA_CONFIG_PATH", str(config_dir))
        monkeypatch.setenv("OUTPUT_PATH", str(output_root / "context.md"))

        with (
            patch("requests.get", side_effect=AssertionError("offline network access")),
            patch("requests.post", side_effect=AssertionError("offline network access")),
        ):
            result = generate_context_file(
                ha_url="http://stale-ha:8123",
                ha_token="stale-token",
                mode="offline",
                profile="compact",
            )

        assert Path(result["output_file"]).read_text(encoding="utf-8")

    def test_notice_that_cannot_fit_fails_as_explicit_budget_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A truncate run without room for the omission notice fails explicitly."""
        from unittest.mock import patch

        from context_generator.budget import BudgetExceededError
        from context_generator.core import generate_context_file

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")
        (config_dir / "automations.yaml").write_text("[]", encoding="utf-8")
        output = tmp_path / "context.md"

        with (
            patch("requests.get", side_effect=AssertionError("offline network access")),
            patch("requests.post", side_effect=AssertionError("offline network access")),
        ):
            measured = generate_context_file(
                config_path=str(config_dir),
                output_path=str(tmp_path / "measure.md"),
                ha_url="http://stale-ha:8123",
                ha_token="stale-token",
                mode="offline",
                include_sections=("source_provenance",),
            )
        mandatory_size = measured["output_bytes"]
        with pytest.raises(BudgetExceededError, match="generation notes do not fit"):
            with (
                patch("requests.get", side_effect=AssertionError("offline network access")),
                patch("requests.post", side_effect=AssertionError("offline network access")),
            ):
                generate_context_file(
                    config_path=str(config_dir),
                    output_path=str(output),
                    ha_url="http://stale-ha:8123",
                    ha_token="stale-token",
                    mode="offline",
                    on_budget_exceeded="truncate",
                    max_output_bytes=mandatory_size + 100,
                )
        assert not output.exists()
