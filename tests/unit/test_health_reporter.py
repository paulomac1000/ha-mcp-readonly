"""
Tests for tools/health_reporter.py (read-only version)
"""

from unittest.mock import patch

import pytest

from tools.health_reporter import (
    calculate_health_score,
    register_health_reporter_tools,
    run_once,
)


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
def sample_api_states():
    return [
        {"entity_id": "sensor.temp", "state": "20"},
        {"entity_id": "light.room", "state": "unavailable"},
        {
            "entity_id": "automation.test",
            "state": "on",
            "attributes": {"last_triggered": "2025-01-01"},
        },
        {
            "entity_id": "automation.unused",
            "state": "on",
            "attributes": {"last_triggered": None},
        },
        {
            "entity_id": "automation.disabled",
            "state": "off",
            "attributes": {"last_triggered": "2025-01-01"},
        },
    ]


@pytest.fixture
def sample_api_config():
    return {
        "version": "2025.1.0",
        "location_name": "Home",
        "components": ["sensor", "light", "automation"],
    }


class TestRunOnce:
    def test_run_once_success(self, config_path, sample_api_states, sample_api_config):
        def make_ha_request_side_effect(
            ha_url, ha_token, endpoint, method="GET", data=None, **kwargs
        ):
            if endpoint == "/api/config":
                return {"success": True, "data": sample_api_config}
            if endpoint == "/api/states":
                return {"success": True, "data": sample_api_states}
            if "/api/hassio/" in endpoint:
                return {"success": False}
            return {"success": False, "error": "Unknown endpoint"}

        with patch(
            "tools.health_reporter.make_ha_request",
            side_effect=make_ha_request_side_effect,
        ):
            with patch(
                "tools.health_reporter.tail_log_file",
                return_value=["2099-01-01 12:00:00 ERROR: Test error"],
            ):
                report = run_once("http://ha", "token", config_path)

        assert report["health_score"]["score"] < 100
        assert report["system_metrics"]["core_version"] == "2025.1.0"
        assert report["entity_health"]["unavailable_count"] == 1
        assert report["automation_health"]["disabled_count"] == 1
        assert report["automation_health"]["never_triggered_count"] == 1


class TestHealthScore:
    def test_calculate_score(self):
        data_perfect = {
            "entity_health": {"unavailable_count": 0, "total_entities": 100},
            "log_summary": {"total_error_count": 0},
            "automation_health": {"disabled_count": 0},
        }
        score_perfect = calculate_health_score(data_perfect)
        assert score_perfect["score"] == 100
        assert score_perfect["status"] == "excellent"

        data_bad = {
            "entity_health": {"unavailable_count": 50, "total_entities": 100},
            "log_summary": {"total_error_count": 100, "unique_error_patterns": 10},
            "automation_health": {"disabled_count": 20},
        }
        score_bad = calculate_health_score(data_bad)
        assert score_bad["score"] == 40
        assert score_bad["status"] == "poor"

    def test_score_status_critical(self):
        data = {
            "entity_health": {"unavailable_count": 100, "total_entities": 100},
            "log_summary": {"total_error_count": 200, "unique_error_patterns": 50},
            "automation_health": {"disabled_count": 50},
        }
        result = calculate_health_score(data)
        assert result["score"] <= 25
        assert result["status"] in ("critical", "poor")

    def test_score_status_fair(self):
        data = {
            "entity_health": {"unavailable_count": 10, "total_entities": 100},
            "log_summary": {"total_error_count": 0, "unique_error_patterns": 0},
            "automation_health": {"disabled_count": 0},
        }
        result = calculate_health_score(data)
        assert result["status"] in ("fair", "good", "excellent")

    def test_score_unknown_entity_penalty(self):
        data_with_unknown = {
            "entity_health": {
                "unavailable_count": 0,
                "unknown_count": 50,
                "total_entities": 100,
            },
            "log_summary": {"total_error_count": 0, "unique_error_patterns": 0},
            "automation_health": {"disabled_count": 0},
        }
        data_clean = {
            "entity_health": {
                "unavailable_count": 0,
                "unknown_count": 0,
                "total_entities": 100,
            },
            "log_summary": {"total_error_count": 0, "unique_error_patterns": 0},
            "automation_health": {"disabled_count": 0},
        }
        score_unknown = calculate_health_score(data_with_unknown)["score"]
        score_clean = calculate_health_score(data_clean)["score"]
        assert score_unknown < score_clean

    def test_score_error_count_path_no_patterns(self):
        data = {
            "entity_health": {"unavailable_count": 0, "total_entities": 100},
            "log_summary": {"total_error_count": 30, "unique_error_patterns": 3},
            "automation_health": {"disabled_count": 0},
        }
        result = calculate_health_score(data)
        assert result["score"] < 100
        assert any("errors in logs" in issue for issue in result["issues"])


class TestCollectEntityHealth:
    def test_collect_entity_health_success(self):
        from tools.health_reporter import collect_entity_health

        states = [
            {"entity_id": "sensor.a", "state": "ok"},
            {"entity_id": "sensor.b", "state": "unavailable"},
            {"entity_id": "sensor.c", "state": "unknown"},
        ]
        with patch(
            "tools.health_reporter.make_ha_request",
            return_value={"success": True, "data": states},
        ):
            result = collect_entity_health("http://ha", "token")
        assert result["total_entities"] == 3
        assert result["unavailable_count"] == 1
        assert result["unknown_count"] == 1

    def test_collect_entity_health_api_error(self):
        from tools.health_reporter import collect_entity_health

        with patch(
            "tools.health_reporter.make_ha_request",
            return_value={"success": False, "error": "timeout"},
        ):
            result = collect_entity_health("http://ha", "token")
        assert "error" in result


class TestCollectAutomationHealth:
    def test_collect_automation_health_success(self):
        from tools.health_reporter import collect_automation_health

        states = [
            {
                "entity_id": "automation.on_one",
                "state": "on",
                "attributes": {"last_triggered": "2025-01-01"},
            },
            {
                "entity_id": "automation.off_one",
                "state": "off",
                "attributes": {"last_triggered": None},
            },
            {"entity_id": "sensor.other", "state": "on", "attributes": {}},
        ]
        with patch(
            "tools.health_reporter.make_ha_request",
            return_value={"success": True, "data": states},
        ):
            result = collect_automation_health("http://ha", "token")
        assert result["total_automations"] == 2
        assert result["disabled_count"] == 1
        assert result["never_triggered_count"] == 1

    def test_collect_automation_health_api_error(self):
        from tools.health_reporter import collect_automation_health

        with patch(
            "tools.health_reporter.make_ha_request",
            return_value={"success": False, "error": "conn refused"},
        ):
            result = collect_automation_health("http://ha", "token")
        assert "error" in result


class TestRunOnceFatalError:
    def test_run_once_fatal_error(self, config_path):
        """Fatal exception in run_once → error_report with score 0."""
        with patch(
            "tools.health_reporter.collect_system_metrics",
            side_effect=RuntimeError("fatal"),
        ):
            report = run_once("http://ha", "token", config_path)
        assert report["health_score"]["score"] == 0
        assert report["health_score"]["status"] == "error"
        assert "error" in report


class TestToolRegistration:
    def test_tools_registered(self, mock_mcp, config_path):
        register_health_reporter_tools(mock_mcp, "http://ha", "token", config_path)
        assert "trigger_health_report" in mock_mcp._tools
        assert "get_last_health_report" not in mock_mcp._tools


# ============================================================
# Additional tests for trigger_health_report
# ============================================================


class TestTriggerHealthReport:
    def test_trigger_health_report_success(
        self, mock_mcp, config_path, sample_api_states, sample_api_config
    ):
        """trigger_health_report returns a valid health report JSON string."""
        import json

        # Create log file so collect_log_summary_optimized doesn't return early
        log_path = config_path + "/home-assistant.log"
        with open(log_path, "w") as f:
            f.write("2025-01-01 12:00:00 INFO: Test\n")

        def make_ha_request_side_effect(
            ha_url, ha_token, endpoint, method="GET", data=None, **kwargs
        ):
            if endpoint == "/api/config":
                return {"success": True, "data": sample_api_config}
            if endpoint == "/api/states":
                return {"success": True, "data": sample_api_states}
            if "/api/hassio/" in endpoint:
                return {"success": False}
            return {"success": False, "error": "Unknown endpoint"}

        with patch(
            "tools.health_reporter.make_ha_request",
            side_effect=make_ha_request_side_effect,
        ):
            with patch("tools.health_reporter.tail_log_file", return_value=[]):
                register_health_reporter_tools(mock_mcp, "http://ha", "token", config_path)
                result = mock_mcp._tools["trigger_health_report"]()
                data = json.loads(result)

        assert "health_score" in data
        assert "system_metrics" in data
        assert "entity_health" in data
        assert "automation_health" in data
        assert "log_summary" in data
        assert data["health_score"]["score"] >= 0
        assert data["system_metrics"]["core_version"] == "2025.1.0"

    def test_trigger_health_report_api_unavailable(self, mock_mcp, config_path):
        """trigger_health_report still produces a report when HA API is down."""
        import json

        with patch(
            "tools.health_reporter.make_ha_request",
            return_value={"success": False, "error": "Connection refused"},
        ):
            with patch("tools.health_reporter.tail_log_file", return_value=[]):
                register_health_reporter_tools(mock_mcp, "http://ha", "token", config_path)
                result = mock_mcp._tools["trigger_health_report"]()
                data = json.loads(result)

        # Should still return a valid report structure, not crash
        assert "health_score" in data
        assert "entity_health" in data
        assert "automation_health" in data
        assert "system_metrics" in data


class TestCollectLogSummaryOptimized:
    def test_log_parsing_loop_detects_errors_and_warnings(self, config_path):
        from datetime import datetime, timedelta

        from tools.health_reporter import collect_log_summary_optimized

        now = datetime.now()
        log_lines = [
            (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
            + " ERROR (MainThread) [homeassistant.core] Something failed",
            (now - timedelta(minutes=9)).strftime("%Y-%m-%d %H:%M:%S")
            + " WARNING (MainThread) [homeassistant.components.mqtt] Retrying connection",
            (now - timedelta(minutes=8)).strftime("%Y-%m-%d %H:%M:%S")
            + " ERROR (MainThread) [homeassistant.components.automation] Template error",
            (now - timedelta(minutes=7)).strftime("%Y-%m-%d %H:%M:%S")
            + " INFO (MainThread) [homeassistant.core] All good",
            (now - timedelta(minutes=6)).strftime("%Y-%m-%d %H:%M:%S")
            + " WARNING (MainThread) [homeassistant.components.hue] Slow response",
        ]

        # Create log file so the path check passes
        log_path = config_path + "/home-assistant.log"
        with open(log_path, "w") as f:
            f.write("\n".join(log_lines))

        with patch(
            "tools.health_reporter.make_ha_request",
            return_value={"success": False},
        ):
            result = collect_log_summary_optimized("http://ha", "token", config_path, hours=1)

        assert result["total_error_count"] == 2
        assert result["total_warning_count"] == 2
        assert result["unique_error_patterns"] > 0

    def test_normalize_message_replaces_patterns(self, config_path):
        from datetime import datetime, timedelta

        from tools.health_reporter import collect_log_summary_optimized

        now = datetime.now()
        log_lines = [
            (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
            + ".123 ERROR (MainThread) [homeassistant.core] Error from 192.168.0.100 with id a1b2c3d4e5f6a7b8",
            (now - timedelta(minutes=4)).strftime("%Y-%m-%d %H:%M:%S")
            + ".456 ERROR (MainThread) [homeassistant.core] Error from 10.0.0.1 with id 1234567890abcdef",
        ]

        log_path = config_path + "/home-assistant.log"
        with open(log_path, "w") as f:
            f.write("\n".join(log_lines))

        with patch(
            "tools.health_reporter.make_ha_request",
            return_value={"success": False},
        ):
            result = collect_log_summary_optimized("http://ha", "token", config_path, hours=1)

        patterns = [g["pattern"] for g in result["top_error_groups"]]
        combined = " ".join(patterns)
        assert "TIMESTAMP" in combined
        assert "IP" in combined
        assert "ID" in combined
