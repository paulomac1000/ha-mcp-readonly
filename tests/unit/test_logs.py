"""
Tests for tools/logs.py
"""

import json
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


class TestBasicLogReading:
    def test_get_recent_logs(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)

        with patch("tools.logs.tail_log_file", return_value=sample_log_lines):
            tool = mock_mcp._tools["get_recent_logs"]
            result = tool(lines=5, level="error")

        assert "ERROR" in result or "ValueError" in result

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

        assert "ERROR" in result or "WARNING" in result

    def test_get_previous_logs_not_found(self, mock_mcp, config_path):
        register_log_tools(mock_mcp, config_path)
        result = mock_mcp._tools["get_previous_logs"](lines=10)

        assert "not found" in result.lower()

    def test_get_previous_logs_level_filter(self, mock_mcp, config_path, sample_log_lines):
        log_path = Path(config_path) / "home-assistant.log.1"
        log_path.write_text("".join(sample_log_lines), encoding="utf-8")

        register_log_tools(mock_mcp, config_path)
        result = mock_mcp._tools["get_previous_logs"](lines=50, level="error")

        for line in result.splitlines():
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
