"""
Tests for tools/logs.py
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

import tools.logs as logs_module
from tools.logs import register_log_tools


@pytest.fixture
def config_path(tmp_path) -> str:
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


@pytest.fixture
def sample_log_lines():
    """Sample log lines with various patterns."""
    return [
        "2099-01-01 12:00:00.000 INFO (MainThread) [homeassistant.bootstrap] Starting Home Assistant\n",
        "2099-01-01 12:01:00.000 WARNING (MainThread) [homeassistant.components.mqtt] Something strange with sensor.mqtt_temp\n",
        "2099-01-01 12:02:00.000 ERROR (MainThread) [homeassistant.components.mqtt] Connection failed for sensor.mqtt_temp\n",
        "2099-01-01 12:02:01.000 ERROR (MainThread) [homeassistant.components.mqtt] Connection failed for sensor.mqtt_humidity\n",
        "2099-01-01 12:03:00.000 ERROR (MainThread) [homeassistant.components.http] Another error occurred\n",
        "2099-01-01 12:04:00.000 ERROR (MainThread) [homeassistant.helpers.template] Template error in automation.bedroom_lights\n",
        "2099-01-01 12:05:00.000 ERROR (MainThread) [custom_components.pstryk] 429 Rate Limit exceeded\n",
        "Traceback (most recent call last):\n",
        '  File "/usr/src/homeassistant/core.py", line 1, in <module>\n',
        "ValueError: test error\n",
    ]


@pytest.fixture(autouse=True)
def clear_log_cache():
    """Clear cache before each test."""
    logs_module._LOG_CACHE.clear()
    yield
    logs_module._LOG_CACHE.clear()


class TestGetLogInsights:
    def test_insights_with_affected_entities(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_log_insights"]
        result = tool(
            hours=1,
            severity="warning",
            group_similar=True,
            include_affected_entities=True,
            max_patterns=10,
        )
        data = json.loads(result)

        assert data["success"] is True

        summary = data["summary"]
        assert summary["total_errors"] >= 1
        assert summary["total_warnings"] >= 1

        # Check affected entities are extracted
        if data.get("affected_entities"):
            assert any("sensor" in e for e in data["affected_entities"])

        # Check affected automations
        if data.get("affected_automations"):
            assert any("automation" in a for a in data["affected_automations"])

        # Check grouped errors have affected_entities
        if data.get("grouped_errors"):
            for pattern, details in data["grouped_errors"].items():
                assert "affected_entities" in details
                assert "affected_automations" in details
                assert "count" in details

        # Check recommendations
        assert "recommendations" in data
        assert len(data["recommendations"]) > 0

    def test_insights_with_categories(self, mock_mcp, config_path):
        log_path = Path(config_path) / "home-assistant.log"
        log_content = """2099-01-01 12:00:00.000 ERROR (MainThread) [component1] Connection timeout occurred
2099-01-01 12:00:01.000 ERROR (MainThread) [component2] Template error in sensor.test
2099-01-01 12:00:02.000 ERROR (MainThread) [component3] API error 429 rate limit
2099-01-01 12:00:03.000 ERROR (MainThread) [component4] Entity unavailable sensor.broken
2099-01-01 12:00:04.000 WARNING (MainThread) [component5] Permission denied access
"""
        log_path.write_text(log_content)

        register_log_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_log_insights"]
        result = tool(hours=1, severity="warning")
        data = json.loads(result)

        assert data["success"] is True

        # Check error categories are detected
        categories = data.get("error_categories", {})
        assert len(categories) > 0

    def test_insights_missing_log_file(self, mock_mcp, config_path):
        """Missing log file → success: False with error message."""
        register_log_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["get_log_insights"](hours=1))
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_insights_cache_does_not_cross_ha_source_roots(self, mock_mcp, tmp_path):
        first_root = tmp_path / "first"
        second_root = tmp_path / "second"
        first_root.mkdir()
        second_root.mkdir()
        (first_root / "home-assistant.log").write_text(
            "2099-01-01 12:00:00 ERROR [component] only-first\n",
            encoding="utf-8",
        )
        (second_root / "home-assistant.log").write_text(
            "2099-01-01 12:00:00 INFO [component] only-second\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, str(first_root))
        first = json.loads(mock_mcp._tools["get_log_insights"]())
        register_log_tools(mock_mcp, str(second_root))
        second = json.loads(mock_mcp._tools["get_log_insights"]())
        assert first["summary"]["total_errors"] == 1
        assert second["summary"]["total_errors"] == 0
        assert first["_meta"]["excerpt_sha256"] != second["_meta"]["excerpt_sha256"]
        assert second["_meta"]["boot_generation"] == "UNKNOWN"


class TestAnalyzeLogErrors:
    def test_analyze_with_tracebacks(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["analyze_log_errors"]
        result = tool(log_source="current", max_results=10)
        data = json.loads(result)

        assert data["success"] is True
        assert data["total_errors"] >= 1
        assert data["total_warnings"] >= 1
        assert data["total_tracebacks"] >= 1
        assert len(data["components_with_errors"]) >= 1

    def test_analyze_previous_log_not_found(self, mock_mcp, config_path):
        """log_source='previous' with no log.1 file → success: False."""
        register_log_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["analyze_log_errors"](log_source="previous"))
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_window_tracks_source_time_without_claiming_utc_or_full_boot(
        self, mock_mcp, config_path
    ):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-01-01 12:00:00.000 ERROR [integration.a] old error\n"
            "2099-01-01 12:02:00.000 WARNING [integration.b] new warning\n"
            "2099-01-01 12:03:00.000 INFO [integration.b] Running automation actions\n"
            "2099-01-01 12:04:00.000 ERROR [integration.c] new error\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(
            mock_mcp._tools["analyze_log_errors"](
                since_source_time="2099-01-01 12:02:00",
                until_source_time="2099-01-01 12:04:00",
            )
        )
        assert result["success"] is True
        assert result["raw_level_counts"] == {"ERROR": 1, "WARNING": 1}
        assert result["total_errors"] == 1
        assert result["requested_window"]["time_basis"] == "SOURCE_LOG_LOCAL_UNVERIFIED"
        assert result["source_metadata"]["boot_generation"] == "UNKNOWN"
        assert result["source_metadata"]["excerpt_sha256"]
        assert result["count_semantics"]["version"] == 1
        assert result["count_semantics"]["raw"] == "SEVERITY_PREFIX"
        assert result["requested_utc_window"]["status"] == "UNVERIFIED"
        assert result["covered_utc_window"]["status"] == "UNVERIFIED"
        assert result["analysis_outcome"] == "ERRORS_OBSERVED"
        assert result["coverage_status"] == "PARTIAL"
        assert result["ha_start_generation"]["status"] == "START_MARKER_MISSING"
        assert result["observed_source_window"]["excluded_lines"] == 1

    def test_invalid_window_and_source_fail_before_log_io(self, mock_mcp, config_path):
        register_log_tools(mock_mcp, config_path)
        tool = mock_mcp._tools["analyze_log_errors"]
        with patch("tools.logs._acquire_log_lines") as acquire:
            for kwargs in (
                {"log_source": "other"},
                {"since_source_time": "2099-02-31 12:00:00"},
                {"since_source_time": "2099-01-01T12:00:00Z"},
                {
                    "since_source_time": "2099-01-02 12:00:00",
                    "until_source_time": "2099-01-01 12:00:00",
                },
                {"max_results": 0},
            ):
                result = json.loads(tool(**kwargs))
                assert result["success"] is False
            acquire.assert_not_called()

    def test_source_content_change_invalidates_cached_summary(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        register_log_tools(mock_mcp, config_path)
        tool = mock_mcp._tools["analyze_log_errors"]

        log.write_text("2099-01-01 12:00:00 ERROR [component] first\n")
        first = json.loads(tool())
        assert first["total_errors"] == 1

        log.write_text("2099-01-01 12:00:00 INFO [component] now fine\n")
        changed = json.loads(tool())
        assert changed["total_errors"] == 0
        assert changed["analysis_outcome"] == "NO_ERRORS_OBSERVED_ONLY"
        assert (
            changed["source_metadata"]["excerpt_sha256"]
            != first["source_metadata"]["excerpt_sha256"]
        )

    def test_backend_change_invalidates_cached_source_evidence(self, mock_mcp, config_path):
        register_log_tools(mock_mcp, config_path)
        tool = mock_mcp._tools["analyze_log_errors"]
        lines = ["2099-01-01 12:00:00 INFO [component] normal\n"]
        with patch(
            "tools.logs._acquire_log_lines",
            side_effect=[
                (lines, {"source": "log_file", "truncated": False, "max_lines": 10}),
                (lines, {"source": "supervisor_proxy", "truncated": True, "max_lines": 10}),
            ],
        ):
            assert json.loads(tool())["source_metadata"]["backend"] == "log_file"
            current = json.loads(tool())

        assert current["source_metadata"]["backend"] == "supervisor_proxy"
        assert current["coverage_status"] == "PARTIAL"

    def test_legacy_max_results_above_fifty_is_accepted_with_bounded_output(
        self, mock_mcp, config_path
    ):
        (Path(config_path) / "home-assistant.log").write_text(
            "2099-01-01 12:00:00 ERROR [component] failure\n", encoding="utf-8"
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(mock_mcp._tools["analyze_log_errors"](max_results=100))
        assert result["success"] is True
        assert result["raw_level_counts"] == {"ERROR": 1, "WARNING": 0}
        assert len(result["grouped_signatures"]) <= 20

    def test_log_tail_truncation_remains_partial(self, mock_mcp, config_path):
        register_log_tools(mock_mcp, config_path)
        lines = ["2099-01-01 12:00:00 INFO [component] normal\n"]
        with patch(
            "tools.logs._acquire_log_lines",
            return_value=(lines, {"source": "log_file", "truncated": True, "max_lines": 1}),
        ):
            data = json.loads(mock_mcp._tools["analyze_log_errors"]())
        assert data["coverage_status"] == "PARTIAL"
        assert data["source_metadata"]["truncated"] is True
        assert data["analysis_outcome"] == "NO_ERRORS_OBSERVED_ONLY"

    def test_info_flood_does_not_inflate_warning_or_group_counts(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        records = [
            f"2099-01-01 12:00:00 INFO [automation.test] Running automation actions {n}\n"
            for n in range(700)
        ]
        records += [
            f"2099-01-01 12:01:00 WARNING [integration.warning] Reconnect attempt {n}\n"
            for n in range(80)
        ]
        records += [
            f"2099-01-01 12:02:00 ERROR [integration.error] Error number {n}\n" for n in range(21)
        ]
        log.write_text("".join(records), encoding="utf-8")
        register_log_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["analyze_log_errors"]())
        assert data["raw_level_counts"] == {"ERROR": 21, "WARNING": 80}
        assert data["total_errors"] == 21
        assert data["total_warnings"] == 80
        assert len(data["grouped_signatures"]) <= 20
        assert not any(x["severity"] == "INFO" for x in data["grouped_signatures"])
        assert any(x["count"] == 80 for x in data["grouped_signatures"])


class TestLogTimelineEvidence:
    def test_timeline_does_not_claim_naive_source_time_is_utc(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        now = datetime.now().replace(microsecond=0)
        log.write_text(f"{now:%Y-%m-%d %H:%M:%S} ERROR [component] failure\n", encoding="utf-8")
        register_log_tools(mock_mcp, config_path)
        result = json.loads(mock_mcp._tools["get_log_timeline"](hours="1"))

        assert result["time_basis"] == "SOURCE_LOG_LOCAL_UNVERIFIED"
        assert result["requested_utc_window"]["status"] == "UNVERIFIED"
        assert result["observed_source_window"]["latest"] == f"{now:%Y-%m-%d %H:%M:%S}"
        assert result["total_events_found"] == 1


class TestBasicLogReading:
    def test_get_recent_logs(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)

        with patch("tools.logs.tail_log_file", return_value=sample_log_lines):
            tool = mock_mcp._tools["get_recent_logs"]
            result = tool(lines=5, level="error")

        data = json.loads(result)
        assert data["success"] is True
        assert "ERROR" in data["logs"] or "ValueError" in data["logs"]
        assert data["level_filter"] == "error"

    def test_search_logs_with_context(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["search_logs"]
        result = tool(
            search_term="Connection failed",
            log_source="current",
            max_results=5,
            context_lines=1,
        )
        data = json.loads(result)

        assert data["success"] is True
        assert data["total_found"] >= 1
        assert "Connection failed" in data["results"][0]["content"]

    def test_search_logs_no_results(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["search_logs"]("xyzzy_not_in_any_log_line_9999"))

        assert data["success"] is True
        assert data["total_found"] == 0


class TestStartupErrors:
    def test_get_startup_errors(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_startup_errors"]
        result = tool()
        data = json.loads(result)

        assert data["success"] is True
        assert data["total_errors"] >= 1

    def test_get_startup_errors_no_marker(self, mock_mcp, config_path):
        """Log without 'Starting Home Assistant' marker → 'Could not find startup marker'."""
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text(
            "2099-01-01 12:00:00.000 ERROR (MainThread) [comp] Some error\n",
            encoding="utf-8",
        )

        register_log_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["get_startup_errors"]())

        assert data["success"] is False
        assert "startup marker" in data["error"].lower()


class TestGetPreviousLogs:
    def test_get_previous_logs_found(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log.1"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)
        result = mock_mcp._tools["get_previous_logs"](lines=5, level="all")

        data = json.loads(result)
        assert data["success"] is True
        assert "ERROR" in data["logs"] or "WARNING" in data["logs"]

    def test_get_previous_logs_not_found(self, mock_mcp, config_path):
        register_log_tools(mock_mcp, config_path)
        result = mock_mcp._tools["get_previous_logs"](lines=10)

        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()
        assert "/api/hassio/core/logs/boots/-1" in data["error"]

    def test_get_previous_logs_level_filter(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log.1"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)
        result = mock_mcp._tools["get_previous_logs"](lines=50, level="error")

        data = json.loads(result)
        assert data["success"] is True
        for line in data["logs"].splitlines():
            if line.strip():
                assert "ERROR" in line


class TestGetComponentLogs:
    def test_get_component_logs_found(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["get_component_logs"]("homeassistant.components.mqtt"))

        assert data["success"] is True
        assert data["total_found"] >= 1
        for entry in data["logs"]:
            assert "mqtt" in entry["component"].lower()

    def test_get_component_logs_not_found(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["get_component_logs"]("nonexistent.component.xyz"))

        assert data["success"] is True
        assert data["total_found"] == 0


class TestLogTimeline:
    def test_get_log_timeline(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_log_timeline"]
        result = tool(hours="1", log_source="current")
        data = json.loads(result)

        assert data["success"] is True
        assert data["total_events_found"] >= 1

        for ev in data["timeline"]:
            assert ev["level"] in ["ERROR", "WARNING"]


class TestCaching:
    def test_cache_works(self, mock_mcp, config_path, sample_log_lines):
        """Test that caching works and returns same result."""
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)

        tool = mock_mcp._tools["get_log_insights"]

        # First call
        result1 = tool(hours=1, severity="warning")

        # Second call (should be cached)
        result2 = tool(hours=1, severity="warning")

        assert result1 == result2


class TestReadLogFileTruncation:
    def test_truncation_when_max_lines_is_none(self, config_path):
        """_read_log_file defaults to 10000 lines and sets truncated when exceeded."""
        log_path = Path(config_path) / "home-assistant.log"
        # Create 15000 lines
        log_path.write_text(
            "\n".join(
                f"2099-01-01 12:00:{i:02d}.000 INFO (MainThread) [comp] line_{i}"
                for i in range(15000)
            )
        )

        lines, meta = logs_module._read_log_file("home-assistant.log", config_path)
        assert lines is not None
        assert len(lines) <= 10000
        assert meta["truncated"] is True
        assert meta["max_lines"] == 10000
        assert meta["source"] == "log_file"

    def test_no_truncation_when_under_limit(self, config_path):
        """_read_log_file does not truncate when under 10000 lines."""
        log_path = Path(config_path) / "home-assistant.log"
        # Create 5 lines only
        log_path.write_text(
            "\n".join(
                f"2099-01-01 12:00:{i:02d}.000 INFO (MainThread) [comp] line_{i}" for i in range(5)
            )
        )

        lines, meta = logs_module._read_log_file("home-assistant.log", config_path)
        assert lines is not None
        assert len(lines) <= 5
        assert meta["truncated"] is False
        assert meta["max_lines"] == 10000
        assert meta["source"] == "log_file"

    def test_custom_max_lines_respected(self, config_path):
        """_read_log_file respects explicit max_lines parameter."""
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text(
            "\n".join(
                f"2099-01-01 12:00:{i:02d}.000 INFO (MainThread) [comp] line_{i}"
                for i in range(500)
            )
        )

        lines, meta = logs_module._read_log_file("home-assistant.log", config_path, max_lines=100)
        assert lines is not None
        assert len(lines) <= 100
        assert meta["truncated"] is True
        assert meta["max_lines"] == 100

    def test_file_not_found_returns_none_with_meta(self, config_path):
        """_read_log_file returns (None, meta) when file not found."""
        lines, meta = logs_module._read_log_file("nonexistent.log", config_path)
        assert lines is None
        assert meta["source"] == "log_file"
        assert meta["truncated"] is False


class TestGetLogInsightsMeta:
    def test_meta_source_is_log_file(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["get_log_insights"](hours=1))
        assert data["success"] is True
        assert data["_meta"]["source"] == "log_file"


class TestGetLogInsightsApiFallback:
    def test_api_fallback_when_file_not_found(self, mock_mcp, config_path):
        """When log file not found, try HA API /api/error_log."""
        register_log_tools(mock_mcp, config_path, ha_url="http://ha:8123", ha_token="test")

        with patch("tools.utils.make_ha_request") as mock_request:
            # First call (Supervisor proxy) fails, second (error_log) succeeds
            mock_request.side_effect = [
                {"success": False, "error": "404: Not Found"},
                {
                    "success": True,
                    "data": (
                        "2099-01-01 12:01:00.000 ERROR (MainThread) [comp] API fallback error\n"
                        "2099-01-01 12:02:00.000 WARNING (MainThread) [comp2] Test warning\n"
                    ),
                },
            ]
            data = json.loads(mock_mcp._tools["get_log_insights"](hours=1))
            assert data["success"] is True
            assert data["_meta"]["source"] == "api_fallback"
            assert data["summary"]["total_errors"] >= 1

    def test_fallback_to_logbook_when_error_log_fails(self, mock_mcp, config_path):
        """When /api/error_log fails, try /api/logbook."""
        register_log_tools(mock_mcp, config_path, ha_url="http://ha:8123", ha_token="test")

        with patch("tools.utils.make_ha_request") as mock_request:
            # First call (Supervisor proxy, issue #31) fails, second
            # (error_log) fails, third (logbook) succeeds
            mock_request.side_effect = [
                {"success": False, "error": "404: Not Found"},
                {"success": False, "error": "not found"},
                {
                    "success": True,
                    "data": [
                        {"when": "2099-01-01T12:01:00", "message": "Logbook entry from fallback"},
                    ],
                },
            ]
            data = json.loads(mock_mcp._tools["get_log_insights"](hours=1))
            assert data["success"] is True
            assert data["_meta"]["source"] == "api_fallback"

    def test_error_when_both_sources_fail(self, mock_mcp, config_path):
        """When file not found and all API fallbacks fail, return error."""
        register_log_tools(mock_mcp, config_path, ha_url="http://ha:8123", ha_token="test")

        with patch("tools.utils.make_ha_request") as mock_request:
            mock_request.return_value = {"success": False, "error": "not found"}
            data = json.loads(mock_mcp._tools["get_log_insights"](hours=1))
            assert data["success"] is False
            assert "not found" in data["error"].lower()

    def test_no_api_fallback_without_credentials(self, mock_mcp, config_path):
        """Without ha_url/ha_token, file-not-found returns error (no API attempt)."""
        register_log_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["get_log_insights"](hours=1))
        assert data["success"] is False
        assert "not found" in data["error"].lower()


# ========================================
# SUPERVISOR LOG FALLBACK (issue #31)
# ========================================


class TestSupervisorLogFallback:
    """HA OS/Supervised installs have no home-assistant.log (issue #31).

    Log tools must fall back to the Supervisor proxy before failing, and
    Core/Container installs (404) must keep the explicit file-path error.
    """

    def test_recent_logs_fall_back_to_supervisor_and_strip_ansi(
        self, mock_mcp, tmp_path, ha_url, ha_token
    ):
        """File missing + Supervisor proxy answering 200 serves ANSI-stripped logs."""
        register_log_tools(mock_mcp, str(tmp_path), ha_url=ha_url, ha_token=ha_token)

        ansi_payload = (
            "\x1b[32m2026-09-01 12:00:00.000 WARNING (MainThread) "
            "[generic.component] Generic client connect called\x1b[0m\n"
            "2026-09-01 12:00:01.000 ERROR (MainThread) [generic.component] Connection failed\n"
        )
        with patch("tools.utils.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": ansi_payload}
            data = json.loads(mock_mcp._tools["get_recent_logs"](lines=100))

        assert data["success"] is True
        assert data["source"] == "supervisor_proxy"
        assert "\x1b" not in data["logs"]
        assert "Generic client connect" in data["logs"]

    def test_recent_logs_request_lines_parameter(self, mock_mcp, tmp_path, ha_url, ha_token):
        """The current-boot proxy request carries the requested line budget."""
        register_log_tools(mock_mcp, str(tmp_path), ha_url=ha_url, ha_token=ha_token)

        with patch("tools.utils.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": "line\n"}
            json.loads(mock_mcp._tools["get_recent_logs"](lines=42))

        endpoint = mock_request.call_args[0][2]
        assert endpoint == "/api/hassio/core/logs?lines=84"

    def test_previous_logs_use_boots_endpoint(self, mock_mcp, tmp_path, ha_url, ha_token):
        """get_previous_logs maps home-assistant.log.1 to the previous boot (-1)."""
        register_log_tools(mock_mcp, str(tmp_path), ha_url=ha_url, ha_token=ha_token)

        with patch("tools.utils.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": "old boot line\n"}
            data = json.loads(mock_mcp._tools["get_previous_logs"](lines=10))

        endpoint = mock_request.call_args[0][2]
        assert endpoint == "/api/hassio/core/logs/boots/-1"
        assert data["source"] == "supervisor_proxy"

    def test_supervisor_404_keeps_explicit_error(self, mock_mcp, tmp_path, ha_url, ha_token):
        """Core/Container installs answer 404: tools fail with the explicit message."""
        register_log_tools(mock_mcp, str(tmp_path), ha_url=ha_url, ha_token=ha_token)

        with patch("tools.utils.make_ha_request") as mock_request:
            mock_request.return_value = {"success": False, "error": "404: Not Found"}
            data = json.loads(mock_mcp._tools["get_recent_logs"](lines=10))

        assert data["success"] is False
        assert "Supervisor log proxy" in data["error"]

    def test_file_still_preferred_over_supervisor(self, mock_mcp, tmp_path, ha_url, ha_token):
        """Installs that still write the log file keep the file path (no API call)."""
        (Path(tmp_path) / "home-assistant.log").write_text(
            "2024-06-01 14:20:00.000 INFO (MainThread) [homeassistant.core] boot\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, str(tmp_path), ha_url=ha_url, ha_token=ha_token)

        with patch("tools.utils.make_ha_request") as mock_request:
            data = json.loads(mock_mcp._tools["get_recent_logs"](lines=10))

        mock_request.assert_not_called()
        assert data["success"] is True
        assert data["source"] == "log_file"

    def test_analyze_log_errors_falls_back_to_supervisor(
        self, mock_mcp, tmp_path, ha_url, ha_token
    ):
        """analyze_log_errors works through the proxy on log-less installs."""
        register_log_tools(mock_mcp, str(tmp_path), ha_url=ha_url, ha_token=ha_token)

        payload = (
            "2026-09-01 12:00:00.000 ERROR (MainThread) [generic.component] Connection failed\n"
            "2026-09-01 12:00:01.000 WARNING (MainThread) [generic.component] Retrying\n"
        )
        with patch("tools.utils.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": payload}
            data = json.loads(mock_mcp._tools["analyze_log_errors"](log_source="current"))

        assert data["success"] is True
        assert data["total_errors"] == 1

    def test_search_logs_scans_supervisor_content(self, mock_mcp, tmp_path, ha_url, ha_token):
        """search_logs finds matches inside proxy-served journal lines."""
        register_log_tools(mock_mcp, str(tmp_path), ha_url=ha_url, ha_token=ha_token)

        with patch("tools.utils.make_ha_request") as mock_request:
            mock_request.return_value = {
                "success": True,
                "data": "2026-09-01 12:00:00 ERROR (MainThread) [generic.component] generic search marker\n",
            }
            data = json.loads(mock_mcp._tools["search_logs"](search_term="generic search marker"))

        assert data["total_found"] == 1
        assert "generic search marker" in data["results"][0]["content"]

    def test_no_credentials_keeps_file_path_error(self, mock_mcp, config_path):
        """Without credentials there is no proxy attempt and the error stays explicit."""
        register_log_tools(mock_mcp, config_path)
        data = json.loads(mock_mcp._tools["get_recent_logs"](lines=10))

        assert data["success"] is False
        assert "Supervisor log proxy" in data["error"]


# ========================================
# LOG EVIDENCE CONTRACT (issue #49)
# ========================================


class TestTrustedTimeBasis:
    """Caller-supplied trusted timezone unlocks exact UTC window receipts."""

    def test_trusted_iana_timezone_yields_exact_utc_windows(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-01-01 12:01:00.000 ERROR [integration.a] first error\n"
            "2099-01-01 12:03:00.000 WARNING [integration.b] mid warning\n"
            "2099-01-01 12:04:00.000 ERROR [integration.c] last error\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(
            mock_mcp._tools["analyze_log_errors"](
                since_source_time="2099-01-01 12:02:00",
                until_source_time="2099-01-01 12:04:00",
                source_timezone="Europe/Warsaw",
            )
        )

        assert result["success"] is True
        assert result["requested_utc_window"]["status"] == "VERIFIED"
        assert result["requested_utc_window"]["since_utc"] == "2099-01-01T11:02:00+00:00"
        assert result["requested_utc_window"]["until_utc"] == "2099-01-01T11:04:00+00:00"
        assert result["covered_utc_window"]["status"] == "VERIFIED"
        assert result["covered_utc_window"]["first_seen_utc"] == "2099-01-01T11:03:00+00:00"
        assert result["covered_utc_window"]["last_seen_utc"] == "2099-01-01T11:04:00+00:00"
        assert result["time_basis"]["clock_confidence"] == "CALLER_TRUSTED_OFFSET"
        assert result["time_basis"]["timezone_source"] == "CALLER"
        assert result["time_basis"]["local_offset"] == "+01:00"

    def test_dst_summer_zone_normalizes_with_summer_offset(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-07-01 12:00:00.000 ERROR [integration.a] summer error\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(mock_mcp._tools["analyze_log_errors"](source_timezone="Europe/Warsaw"))

        assert result["covered_utc_window"]["status"] == "VERIFIED"
        assert result["covered_utc_window"]["first_seen_utc"] == "2099-07-01T10:00:00+00:00"
        assert result["time_basis"]["local_offset"] == "+02:00"

    def test_fixed_offset_timezone_normalizes_exactly(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-01-01 12:00:00.000 ERROR [integration.a] offset error\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(mock_mcp._tools["analyze_log_errors"](source_timezone="-05:30"))

        assert result["covered_utc_window"]["first_seen_utc"] == "2099-01-01T17:30:00+00:00"
        assert result["time_basis"]["local_offset"] == "-05:30"

    def test_invalid_timezone_rejected_before_log_io(self, mock_mcp, config_path):
        register_log_tools(mock_mcp, config_path)
        with patch("tools.logs._acquire_log_lines") as acquire:
            result = json.loads(mock_mcp._tools["analyze_log_errors"](source_timezone="Not/AZone"))
        assert result["success"] is False
        assert "source_timezone" in result["error"]
        acquire.assert_not_called()

    def test_untrusted_clock_keeps_explicit_provenance(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-01-01 12:01:00.000 ERROR [integration.a] first error\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(mock_mcp._tools["analyze_log_errors"]())

        assert result["time_basis"]["clock_confidence"] == "TIME_BASIS_UNKNOWN"
        assert result["time_basis"]["timezone_source"] == "UNKNOWN"
        assert result["time_basis"]["source_timezone"] is None
        assert result["time_basis"]["local_offset"] is None
        assert result["covered_utc_window"]["status"] == "UNVERIFIED"
        assert result["first_sighting"]["utc"] is None
        assert result["first_sighting"]["source_time"] == "2099-01-01 12:01:00"


class TestGenerationAndRotation:
    """Boot/rotation identity must be explicit, never silently mixed."""

    def test_single_startup_marker_derives_boot_generation(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-01-01 12:00:00.000 INFO (MainThread) [homeassistant.bootstrap] Starting Home Assistant\n"
            "2099-01-01 12:01:00.000 ERROR [integration.a] boot error\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(mock_mcp._tools["analyze_log_errors"]())

        generation = result["ha_start_generation"]
        assert generation["status"] == "DERIVED"
        assert generation["generation"].startswith("sha256:")
        assert generation["startup_marker_count"] == 1
        assert generation["startup_marker_source_time"] == "2099-01-01 12:00:00"
        assert result["rotation_coverage"] == "SINGLE_GENERATION"
        assert result["cutoff_reason"] == "NONE"
        assert result["source_metadata"]["boot_generation"] == generation["generation"]

    def test_missing_startup_marker_forces_partial(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-01-01 12:01:00.000 INFO [integration.a] no marker anywhere\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(mock_mcp._tools["analyze_log_errors"]())

        assert result["ha_start_generation"]["status"] == "START_MARKER_MISSING"
        assert result["ha_start_generation"]["generation"] is None
        assert result["rotation_coverage"] == "UNKNOWN"
        assert result["cutoff_reason"] == "UNKNOWN"
        assert result["coverage_status"] == "PARTIAL"

    def test_previous_log_reports_rotation(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log.1"
        log.write_text(
            "2099-01-01 12:01:00.000 ERROR [integration.a] rotated boot error\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(mock_mcp._tools["analyze_log_errors"](log_source="previous"))

        assert result["rotation_coverage"] == "LOG_ROTATED"
        assert result["cutoff_reason"] == "ROTATION"
        assert result["coverage_status"] == "PARTIAL"

    def test_mixed_generations_not_presented_as_single_recurrence(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-01-01 10:00:00.000 INFO (MainThread) [homeassistant.bootstrap] Starting Home Assistant\n"
            "2099-01-01 10:05:00.000 ERROR [integration.old] old boot error\n"
            "2099-01-01 12:00:00.000 INFO (MainThread) [homeassistant.bootstrap] Starting Home Assistant\n"
            "2099-01-01 12:05:00.000 ERROR [integration.new] new boot error\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(mock_mcp._tools["analyze_log_errors"]())

        assert result["ha_start_generation"]["status"] == "MIXED_GENERATIONS"
        assert result["rotation_coverage"] == "MIXED_GENERATIONS"
        assert result["coverage_status"] == "PARTIAL"
        assert result["first_sighting"]["generation_scope"] == "MIXED"
        assert result["first_sighting"]["source_time"] == "2099-01-01 10:05:00"
        assert result["last_sighting"]["source_time"] == "2099-01-01 12:05:00"

    def test_source_file_span_is_reported_independently_of_window(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-01-01 09:00:00.000 INFO [integration.a] earliest line\n"
            "2099-01-01 12:01:00.000 ERROR [integration.b] windowed error\n"
            "2099-01-01 15:00:00.000 INFO [integration.c] latest line\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(
            mock_mcp._tools["analyze_log_errors"](
                since_source_time="2099-01-01 12:00:00",
                until_source_time="2099-01-01 13:00:00",
            )
        )

        assert result["source_file_span"]["earliest"] == "2099-01-01 09:00:00"
        assert result["source_file_span"]["latest"] == "2099-01-01 15:00:00"
        assert result["observed_source_window"]["earliest"] == "2099-01-01 12:01:00"
        assert result["observed_source_window"]["latest"] == "2099-01-01 12:01:00"


class TestOwnerScopedReceipt:
    """Owner-scoped post-deploy receipt keeps cross-domain critical errors visible."""

    def test_clean_owner_window_still_reports_global_critical(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-01-01 12:00:00.000 INFO (MainThread) [homeassistant.bootstrap] Starting Home Assistant\n"
            "2099-01-01 12:00:05.000 INFO (MainThread) [homeassistant.components.climate] Climate ready\n"
            "2099-01-01 12:00:10.000 ERROR (MainThread) [automation.garden_pump] "
            "Garden Pump failsafe disabled: missing nested choose.conditions\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(mock_mcp._tools["analyze_log_errors"](owner_component="climate"))

        receipt = result["owner_receipt"]
        assert receipt["owner_scope"] == "climate"
        assert receipt["owner_outcome"] == "OWNER_WINDOW_NO_ERRORS"
        assert receipt["owner_raw_level_counts"] == {"ERROR": 0, "WARNING": 0}
        assert receipt["global_critical_startup_errors_present"] is True
        assert receipt["global_critical_error_count"] == 1
        assert receipt["reason"] is None
        assert result["analysis_outcome"] == "ERRORS_OBSERVED"

    def test_owner_errors_counted_within_requested_window(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-01-01 12:00:00.000 INFO (MainThread) [homeassistant.bootstrap] Starting Home Assistant\n"
            "2099-01-01 12:01:00.000 ERROR (MainThread) [homeassistant.components.climate] owner boom\n"
            "2099-01-01 12:02:00.000 ERROR (MainThread) [automation.garden_pump] cross boom\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(
            mock_mcp._tools["analyze_log_errors"](
                owner_component="climate",
                since_source_time="2099-01-01 12:01:00",
            )
        )

        receipt = result["owner_receipt"]
        assert receipt["owner_outcome"] == "OWNER_ERRORS_OBSERVED"
        assert receipt["owner_raw_level_counts"] == {"ERROR": 1, "WARNING": 0}
        assert receipt["global_critical_startup_errors_present"] is True
        assert receipt["global_critical_error_count"] == 2

    def test_missing_marker_blocks_critical_false_claim(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-01-01 12:01:00.000 ERROR [integration.a] error without boot boundary\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(mock_mcp._tools["analyze_log_errors"](owner_component="climate"))

        receipt = result["owner_receipt"]
        assert receipt["owner_outcome"] == "OWNER_WINDOW_NO_ERRORS"
        assert receipt["global_critical_startup_errors_present"] is None
        assert receipt["reason"] == "START_MARKER_MISSING"

    def test_truncated_derived_generation_stays_unknown_not_false(self, mock_mcp, config_path):
        register_log_tools(mock_mcp, config_path)
        lines = [
            "2099-01-01 12:00:00.000 INFO (MainThread) [homeassistant.bootstrap] Starting Home Assistant\n",
            "2099-01-01 12:01:00.000 INFO [integration.a] quiet tail\n",
        ]
        with patch(
            "tools.logs._acquire_log_lines",
            return_value=(lines, {"source": "log_file", "truncated": True, "max_lines": 2}),
        ):
            result = json.loads(mock_mcp._tools["analyze_log_errors"](owner_component="climate"))

        receipt = result["owner_receipt"]
        assert receipt["global_critical_startup_errors_present"] is None
        assert receipt["reason"] == "COVERAGE_PARTIAL"
        assert result["cutoff_reason"] == "TRUNCATED_MAX_LINES"

    def test_sightings_carry_utc_when_clock_trusted(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-01-01 12:00:00.000 INFO (MainThread) [homeassistant.bootstrap] Starting Home Assistant\n"
            "2099-01-01 12:01:00.000 ERROR [integration.a] first\n"
            "2099-01-01 12:03:00.000 WARNING [integration.b] last\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        result = json.loads(mock_mcp._tools["analyze_log_errors"](source_timezone="+01:00"))

        assert result["first_sighting"]["source_time"] == "2099-01-01 12:01:00"
        assert result["first_sighting"]["utc"] == "2099-01-01T11:01:00+00:00"
        assert result["first_sighting"]["generation_scope"] == "SINGLE"
        assert result["last_sighting"]["source_time"] == "2099-01-01 12:03:00"
        assert result["last_sighting"]["utc"] == "2099-01-01T11:03:00+00:00"


class TestEvidenceReplayAndCrossTool:
    """Stable digest, semantic UNCHANGED replay and comparable raw counters."""

    def test_unchanged_source_replays_stable_receipt(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text(
            "2099-01-01 12:00:00.000 INFO (MainThread) [homeassistant.bootstrap] Starting Home Assistant\n"
            "2099-01-01 12:01:00.000 ERROR [integration.a] stable error\n",
            encoding="utf-8",
        )
        register_log_tools(mock_mcp, config_path)
        first = json.loads(mock_mcp._tools["analyze_log_errors"]())
        second = json.loads(mock_mcp._tools["analyze_log_errors"]())

        assert first["replay_status"] == "OBSERVED"
        assert second["replay_status"] == "UNCHANGED"
        assert second["evidence_digest"] == first["evidence_digest"]
        assert second["source_observed_at_utc"] == first["source_observed_at_utc"]

    def test_changed_source_produces_new_observed_receipt(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        log.write_text("2099-01-01 12:00:00 ERROR [component] first\n", encoding="utf-8")
        register_log_tools(mock_mcp, config_path)
        first = json.loads(mock_mcp._tools["analyze_log_errors"]())

        log.write_text("2099-01-01 12:00:00 INFO [component] fine now\n", encoding="utf-8")
        second = json.loads(mock_mcp._tools["analyze_log_errors"]())

        assert first["replay_status"] == "OBSERVED"
        assert second["replay_status"] == "OBSERVED"
        assert second["evidence_digest"] != first["evidence_digest"]

    def test_cross_tool_comparable_raw_counts_on_frozen_fixture(self, mock_mcp, config_path):
        log = Path(config_path) / "home-assistant.log"
        records = [
            "2099-01-01 12:00:00.000 INFO (MainThread) [homeassistant.bootstrap] Starting Home Assistant\n"
        ]
        records += [
            f"2099-01-01 12:00:10.000 ERROR [integration.a] error number {n}\n" for n in range(2)
        ]
        records += [
            f"2099-01-01 12:00:20.000 WARNING [integration.b] warning number {n}\n"
            for n in range(3)
        ]
        records += [
            f"2099-01-01 12:00:30.000 INFO [automation.noise] Running automation actions {n}\n"
            for n in range(50)
        ]
        log.write_text("".join(records), encoding="utf-8")
        register_log_tools(mock_mcp, config_path)

        analyzed = json.loads(mock_mcp._tools["analyze_log_errors"]())
        timeline = json.loads(mock_mcp._tools["get_log_timeline"](hours="24"))
        insights = json.loads(mock_mcp._tools["get_log_insights"](hours=24, severity="warning"))

        expected = {"ERROR": 2, "WARNING": 3}
        assert analyzed["raw_level_counts"] == expected
        assert timeline["raw_level_counts"] == expected
        assert insights["raw_level_counts"] == expected
        assert analyzed["cutoff_reason"] == "NONE"
        assert timeline["cutoff_reason"] == "NONE"
        assert insights["_meta"]["cutoff_reason"] == "NONE"
