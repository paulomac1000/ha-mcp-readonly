"""Smoke tests: critical tools reported by agents as potentially broken."""

import pytest
import requests

from .conftest import HA_TOKEN, REST_API_URL, _server_running

pytestmark = pytest.mark.skipif(
    not _server_running() or not HA_TOKEN or HA_TOKEN in ("", "your_long_lived_access_token_here"),
    reason="MCP server not running or HA_TOKEN not configured",
)


def _call_tool(tool_name, **params):
    """Call a tool via the REST API and return parsed JSON."""
    resp = requests.post(
        f"{REST_API_URL}/api/tools/{tool_name}",
        json=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _call_tool_safe(tool_name, **params):
    """Call a tool via the REST API, catching exceptions gracefully.

    Returns (data, error) tuple. When the call succeeds, data is the parsed
    JSON dict and error is None. When it fails, data is None and error is a
    string describing what went wrong.
    """
    try:
        resp = requests.post(
            f"{REST_API_URL}/api/tools/{tool_name}",
            json=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json(), None
    except requests.ConnectionError as e:
        return None, f"Connection error: {e}"
    except requests.Timeout as e:
        return None, f"Timeout after 30s: {e}"
    except requests.HTTPError as e:
        return None, f"HTTP {e.response.status_code}: {e}"
    except requests.RequestException as e:
        return None, f"Request error: {e}"
    except ValueError as e:
        return None, f"JSON parse error: {e}"


class TestCriticalEntityTools:
    """Verify the 6 critical tools return success."""

    def test_get_entity_state(self):
        """get_entity_state should return entity state for sun.sun."""
        data = _call_tool("get_entity_state", entity_id="sun.sun")
        assert data["success"] is True
        entity = data.get("result", {}).get("entity", {})
        assert entity.get("state") in ("above_horizon", "below_horizon")

    def test_get_entity_state_batch(self):
        """get_entity_state_batch should return states for multiple entities."""
        data = _call_tool("get_entity_state_batch", entity_ids="sun.sun")
        assert data["success"] is True
        result = data.get("result", {})
        assert "entities" in result

    def test_get_entity_context(self):
        """get_entity_context should return context for an entity."""
        data = _call_tool("get_entity_context", entity_id="sun.sun")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        # Entity context includes state data, registry info, and related entities
        assert "state" in result or "entity" in result or "entity_id" in result, (
            "should have state/entity info"
        )

    def test_search_registries_batch(self):
        """search_registries_batch should find entities by search term."""
        data = _call_tool("search_registries_batch", search_term="sun")
        assert data["success"] is True
        result = data.get("result", {})
        assert "matched_entities" in result

    def test_get_domains_summary(self):
        """get_domains_summary should return domain counts."""
        data = _call_tool("get_domains_summary")
        assert data["success"] is True
        result = data.get("result", {})
        domains = result.get("by_domain", {})
        assert len(domains) > 0 or result.get("total_domains", 0) > 0

    def test_search_entities(self):
        """search_entities should find entities by name."""
        data = _call_tool("search_entities", search_term="sun")
        assert data["success"] is True
        result = data.get("result", {})
        results = result.get("results", [])
        found = [e for e in results if "sun" in str(e.get("entity_id", "")).lower()]
        assert len(found) >= 1


class TestCriticalSmokeHealth:
    """Additional smoke-level tool checks."""

    def test_get_system_overview(self):
        """System overview should return aggregate data."""
        data = _call_tool("get_system_overview")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        # System overview should have basic structure info
        assert any(key in result for key in ("summary", "domains", "entities", "overview")), (
            "should have recognizable top-level keys"
        )

    def test_list_automations(self):
        """Automation listing should return results."""
        data = _call_tool("list_automations")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        automations = result.get("automations", result.get("results", []))
        assert isinstance(automations, list), "automations should be a list"

    def test_get_entity_registry(self):
        """Entity registry should be accessible."""
        data = _call_tool("get_entity_registry")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert "entities" in result, "should have entities key"
        assert isinstance(result["entities"], list), "entities should be a list"
        assert "total_entities" in result, "should have total_entities key"
        assert isinstance(result["total_entities"], int), "total_entities should be int"
        assert result["total_entities"] >= 0, "total_entities should be non-negative"

    def test_diagnose_system_health(self):
        """System health diagnostics should work."""
        data = _call_tool(
            "diagnose_system_health",
            include_log_analysis=False,
            include_unavailable_breakdown=False,
            include_performance=False,
        )
        assert data["success"] is True
        summary = data.get("result", {}).get("summary", {})
        assert "health_score" in summary


class TestCriticalAutomationTools:
    """Smoke tests for critical automation and diagnostic tools."""

    def test_get_unavailable_entities_grouped(self):
        """Unavailable entities grouping should return."""
        data = _call_tool("get_unavailable_entities_grouped", group_by="domain")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        groups = result.get("groups", result.get("grouped", {}))
        assert isinstance(groups, (dict, list)), "groups should be dict or list"

    def test_search_automations(self):
        """Search automations should find results."""
        data = _call_tool("search_automations")
        assert data["success"] is True
        result = data.get("result", {})
        assert "results" in result

    def test_get_automation_code(self):
        """Get automation code should work for existing automation."""
        list_data = _call_tool("list_automations")
        assert list_data["success"] is True
        automations = list_data.get("result", {}).get("automations", [])
        if automations:
            first = automations[0]
            auto_id = first.get("alias") or first.get("id") or first.get("entity_id")
            code_data = _call_tool("get_automation_code", automation_id=auto_id)
            assert code_data["success"] is True

    def test_get_automation_file_location(self):
        """get_automation_file_location should return line range for an automation."""
        list_data = _call_tool("list_automations")
        assert list_data["success"] is True
        automations = list_data.get("result", {}).get("automations", [])
        if automations:
            first = automations[0]
            auto_id = first.get("alias") or first.get("id") or first.get("entity_id")
            data = _call_tool("get_automation_file_location", automation_id=auto_id)
            assert data["success"] is True
            result = data.get("result", {})
            assert "line_start" in result
            assert "line_end" in result
            assert "file_path" in result

    def test_get_automation_codes_batch(self):
        """get_automation_codes_batch should return codes for multiple automations."""
        list_data = _call_tool("list_automations")
        assert list_data["success"] is True
        automations = list_data.get("result", {}).get("automations", [])
        if len(automations) >= 2:
            id1 = (
                automations[0].get("alias")
                or automations[0].get("id")
                or automations[0].get("entity_id")
            )
            id2 = (
                automations[1].get("alias")
                or automations[1].get("id")
                or automations[1].get("entity_id")
            )
            data = _call_tool("get_automation_codes_batch", automation_ids=f"{id1},{id2}")
            assert data["success"] is True
            result = data.get("result", {})
            assert "results" in result
            assert "total_requested" in result
        else:
            import pytest

            pytest.skip("Need at least 2 automations for batch test")


class TestScriptSceneSmoke:
    """Smoke tests for script and scene tools."""

    def test_list_scripts(self):
        data = _call_tool("list_scripts")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        scripts = result.get("scripts", result.get("results", []))
        assert isinstance(scripts, (list, dict)), "scripts should be list or dict"

    def test_list_scenes(self):
        data = _call_tool("list_scenes")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        scenes = result.get("scenes", result.get("results", []))
        assert isinstance(scenes, (list, dict)), "scenes should be list or dict"


class TestBlueprintSmoke:
    """Smoke tests for blueprint tools."""

    def test_list_blueprints(self):
        data = _call_tool("list_blueprints")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        blueprints = result.get("blueprints", result.get("results", []))
        assert isinstance(blueprints, (list, dict)), "blueprints should be list or dict"

    def test_get_blueprint_usage_summary(self):
        data = _call_tool("get_blueprint_usage_summary")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert any(key in result for key in ("summary", "used", "unused", "total", "usage")), (
            "should have blueprint usage keys"
        )


class TestConfigSmoke:
    """Smoke tests for config file tools."""

    def test_get_main_configuration(self):
        data = _call_tool("get_main_configuration")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert any(key in result for key in ("data", "config", "content", "configuration")), (
            "should have config content key"
        )

    def test_get_config_structure(self):
        data = _call_tool("get_config_structure")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert any(key in result for key in ("structure", "directories", "files", "tree")), (
            "should have structure key"
        )

    def test_read_config_file(self):
        data = _call_tool("read_config_file", file_path="configuration.yaml", max_lines=10)
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert any(key in result for key in ("content", "lines", "data")), (
            "should have content/lines key"
        )


class TestDeviceSmoke:
    """Smoke tests for device tools."""

    def test_search_devices(self):
        data = _call_tool("search_devices")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        devices = result.get("devices", result.get("results", []))
        assert isinstance(devices, (list, dict)), "devices should be list or dict"

    def test_get_device_details(self):
        data = _call_tool("search_devices", search_term="sun")
        assert data["success"] is True
        result = data.get("result", {})
        devices = result.get("devices", [])
        if devices:
            dev_id = devices[0].get("device_id")
            detail = _call_tool("get_device_details", device_id=dev_id)
            assert detail["success"] is True


class TestDiagnosticsExtraSmoke:
    """Smoke tests for additional diagnostic tools."""

    def test_get_log_insights(self):
        data = _call_tool("get_log_insights", hours=1, severity="error")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert "summary" in result, "should have summary key"
        assert isinstance(result["summary"], dict), "summary should be a dict"
        assert "total_errors" in result["summary"], "summary should have total_errors"
        assert isinstance(result["summary"]["total_errors"], int), "total_errors should be int"
        # Should have at least one of: grouped_errors, error_categories, recommendations
        assert any(
            key in result
            for key in ("grouped_errors", "error_categories", "recent_errors", "recommendations")
        ), "should have error-analysis keys"

    def test_get_integration_health(self):
        data = _call_tool("get_integration_health", domain="sun")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"

    def test_get_startup_errors(self):
        data = _call_tool("get_startup_errors")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert any(
            key in result
            for key in ("startup_errors", "errors", "total_errors", "startup_warnings")
        ), "should have startup error/warning keys"


class TestHistorySmoke:
    """Smoke tests for history tools."""

    def test_get_entity_state_history_summary(self):
        data = _call_tool(
            "get_entity_state_history_summary",
            entity_id="sun.sun",
            hours_back=1,
        )
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert any(
            key in result for key in ("summary", "changes", "history", "period_hours", "entity_id")
        ), "should have history-related keys"

    def test_get_recent_state_changes(self):
        data = _call_tool("get_recent_state_changes", minutes=5)
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        changes = result.get("changes", result.get("state_changes", []))
        assert isinstance(changes, (list, dict)), "changes should be list or dict"


class TestStorageHelpersSmoke:
    """Smoke tests for helper entities and storage tools."""

    def test_get_input_helpers(self):
        data = _call_tool("get_input_helpers")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        helpers = result.get("input_helpers", result.get("helpers", result.get("results", [])))
        assert isinstance(helpers, (list, dict)), "helpers should be list or dict"

    def test_get_timers(self):
        data = _call_tool("get_timers")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        timers = result.get("timers", result.get("results", []))
        assert isinstance(timers, (list, dict)), "timers should be list or dict"

    def test_get_counters(self):
        data = _call_tool("get_counters")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        counters = result.get("counters", result.get("results", []))
        assert isinstance(counters, (list, dict)), "counters should be list or dict"

    def test_get_exposed_entities(self):
        data = _call_tool("get_exposed_entities")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        exposed = result.get(
            "exposed_entities",
            result.get("exposed", result.get("entities", None)),
        )
        assert exposed is not None or "count" in result or "total" in result, (
            "should have exposed entities data"
        )

    def test_get_lovelace_entity_usage(self):
        data = _call_tool("get_lovelace_entity_usage", entity_id="sun.sun")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert "usage_count" in result, "should have usage_count key"
        assert isinstance(result["usage_count"], int), "usage_count should be int"
        assert result["usage_count"] >= 0, "usage_count should be non-negative"


class TestScriptSceneCodeSmoke:
    """Smoke tests for script and scene code retrieval."""

    def test_get_script_code(self):
        list_data = _call_tool("list_scripts")
        result = list_data.get("result", {})
        scripts = result.get("scripts", [])
        if scripts:
            sid = scripts[0].get("id") or scripts[0].get("script_id")
            data = _call_tool("get_script_code", script_id=sid)
            assert data["success"] is True

    def test_get_scene_code(self):
        list_data = _call_tool("list_scenes")
        result = list_data.get("result", {})
        scenes = result.get("scenes", [])
        if scenes:
            sid = scenes[0].get("id") or scenes[0].get("name")
            data = _call_tool("get_scene_code", scene_id=sid)
            assert data["success"] is True


class TestConfigSearchSmoke:
    """Smoke tests for config file search."""

    def test_search_in_config(self):
        data = _call_tool("search_in_config", search_term="homeassistant")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        results = result.get("results", result.get("matches", []))
        assert isinstance(results, (list, dict)), "search results should be list or dict"

    def test_search_in_config_batch(self):
        data = _call_tool("search_in_config_batch", search_terms="homeassistant,automation")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"


class TestDiagnosticsExtraSmoke2:
    """Smoke tests for more diagnostic tools."""

    def test_get_notification_history(self):
        data = _call_tool("get_notification_history")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        notifications = result.get(
            "notifications",
            result.get("results", result.get("messages", [])),
        )
        assert isinstance(notifications, (list, dict)), "notifications should be list or dict"

    def test_get_area_automation_summary(self):
        registry_data = _call_tool("get_area_registry")
        result = registry_data.get("result", {})
        areas = result.get("areas", [])
        if areas:
            area_id = areas[0].get("id") or areas[0].get("name")
            data = _call_tool("get_area_automation_summary", area_id=area_id)
            assert data["success"] is True


class TestDeviceExtraSmoke:
    """Smoke tests for extra device tools."""

    def test_get_device_entities(self):
        search = _call_tool("search_devices")
        result = search.get("result", {})
        devices = result.get("devices", [])
        if devices:
            dev_id = devices[0].get("device_id")
            data = _call_tool("get_device_entities", device_id=dev_id)
            assert data["success"] is True


class TestDevToolsExtraSmoke:
    """Smoke tests for development tools."""

    def test_test_condition(self):
        data = _call_tool("test_condition", condition_template="{{ 1 == 1 }}")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"

    def test_validate_automation_trigger(self):
        trigger = '- platform: state\n  entity_id: sun.sun\n  to: "below_horizon"'
        data = _call_tool("validate_automation_trigger", trigger_config=trigger)
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"


class TestFilesystemSmoke:
    """Smoke tests for filesystem explorer."""

    def test_list_directory(self):
        data = _call_tool("list_directory", path="/config")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        entries = result.get("entries", result.get("files", result.get("items", [])))
        assert isinstance(entries, (list, dict)), "directory entries should be list or dict"

    def test_read_file(self):
        data = _call_tool("read_file", file_path="/config/configuration.yaml", max_lines=5)
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert any(key in result for key in ("content", "lines", "data")), (
            "should have file content key"
        )

    def test_search_files(self):
        data = _call_tool(
            "search_files", pattern="homeassistant", search_path="/config", max_results=5
        )
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"


class TestHealthReporterSmoke:
    """Smoke test for health reporter tool."""

    def test_trigger_health_report(self):
        data = _call_tool("trigger_health_report")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert any(key in result for key in ("metrics", "health_score", "summary", "report")), (
            "should have health report keys"
        )


class TestChangesAndCompareSmoke:
    """Smoke tests for entity changes and state comparison."""

    def test_get_entity_changes(self):
        data = _call_tool("get_entity_changes", hours_back=1)
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        changes = result.get("changes", result.get("entities", result.get("results", [])))
        assert isinstance(changes, (list, dict)), "changes should be list or dict"

    def test_verify_recent_implementation(self):
        data = _call_tool("verify_recent_implementation", hours_back=1)
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"

    def test_compare_entities_state(self):
        data = _call_tool("compare_entities_state", entity_ids="sun.sun")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"


class TestDiagnoseLovelaceSmoke:
    """Smoke test for Lovelace diagnostics."""

    def test_diagnose_lovelace_setup(self):
        data = _call_tool("diagnose_lovelace_setup")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert any(
            key in result
            for key in ("dashboards", "issues", "summary", "resources", "recommendations")
        ), "should have lovelace diagnostic keys"


class TestZeroParamSmoke:
    """Tools that work with zero parameters or sun.sun defaults."""

    def test_diagnose_person_tracking_default(self):
        data = _call_tool("diagnose_person_tracking")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"

    def test_get_lovelace_config_default(self):
        data = _call_tool("get_lovelace_config")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"

    def test_get_previous_logs(self):
        data = _call_tool("get_previous_logs", lines=10)
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"

    def test_get_services(self):
        data = _call_tool("get_services")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        services = result.get("services", result.get("results", {}))
        assert isinstance(services, (list, dict)), "services should be dict or list"

    def test_get_template_performance(self):
        data = _call_tool("get_template_performance", template="{{ states('sun.sun') }}")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"

    def test_list_config_entry_domains(self):
        data = _call_tool("list_config_entry_domains")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        domains = result.get("domains", result.get("results", {}))
        assert isinstance(domains, (list, dict)), "domains should be dict or list"

    def test_list_custom_components(self):
        data = _call_tool("list_custom_components")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"

    def test_list_themes(self):
        data = _call_tool("list_themes")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"

    def test_search_config_by_params(self):
        import requests

        try:
            data = _call_tool("search_config_by_params", entity_id="sun.sun")
            assert data["success"] is True
        except requests.Timeout:
            pytest.skip("search_config_by_params timed out (config directory too large)")

    def test_search_entity_by_name(self):
        data = _call_tool("search_entity_by_name", search_term="sun")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        results = result.get("results", result.get("entities", []))
        assert isinstance(results, (list, dict)), "search results should be list or dict"

    def test_test_service_call(self):
        data = _call_tool(
            "test_service_call", domain="light", service="turn_on", entity_id="sun.sun"
        )
        assert data["success"] is True

    def test_validate_yaml_batch(self):
        data = _call_tool("validate_yaml_batch", file_paths="configuration.yaml")
        assert data["success"] in (True, False)

    def test_validate_yaml_syntax(self):
        data = _call_tool("validate_yaml_syntax", yaml_content="entity_id: sun.sun")
        assert data["success"] is True

    def test_get_component_logs(self):
        data = _call_tool("get_component_logs", component_name="homeassistant.core")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert any(key in result for key in ("logs", "results", "total_found", "component")), (
            "should have log result keys"
        )

    def test_get_integration_entities(self):
        data = _call_tool("get_integration_entities", domain="sun")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"

    def test_get_integration_summary(self):
        data = _call_tool("get_integration_summary", domain="sun")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"

    def test_get_entity_details(self):
        data = _call_tool("get_entity_details", entity_id="sun.sun")
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"

    def test_get_entity_state_history_summary(self):
        data = _call_tool("get_entity_state_history_summary", entity_id="sun.sun", hours_back=1)
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"

    def test_get_history_batch(self):
        data = _call_tool("get_history_batch", entity_ids="sun.sun", hours_back=1, limit=3)
        assert data["success"] is True
        result = data.get("result", {})
        assert isinstance(result, dict), "result should be a dict"
        assert "entities_found" in result, "should have entities_found key"
        assert isinstance(result["entities_found"], int), "entities_found should be int"
        assert result["entities_found"] >= 0, "entities_found should be non-negative"
        assert "period_hours" in result, "should have period_hours key"
        assert isinstance(result["period_hours"], int), "period_hours should be int"


class TestTemplateEntityCodeSmoke:
    """Smoke test for get_template_entity_code."""

    def test_get_template_entity_code(self):
        list_data = _call_tool("get_template_entities", entity_id=None)
        result = list_data.get("result", {})
        templates = result.get("templates", [])
        if templates:
            eid = templates[0].get("entity_id")
            data = _call_tool("get_template_entity_code", entity_id=eid)
            assert data["success"] is True
