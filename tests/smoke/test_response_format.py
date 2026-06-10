"""Smoke test: verify ALL tools return standard JSON with success field."""

import pytest
import requests

from .conftest import HA_TOKEN, REST_API_URL, _server_running

pytestmark = pytest.mark.skipif(
    not _server_running() or not HA_TOKEN or HA_TOKEN in ("", "your_long_lived_access_token_here"),
    reason="MCP server not running or HA_TOKEN not configured",
)


def _list_tools():
    resp = requests.get(f"{REST_API_URL}/api/tools", timeout=10)
    resp.raise_for_status()
    return resp.json()["tools"]


# Tools that require mandatory parameters — can't call without them
_REQUIRES_PARAMS = {
    "bulk_search_entities",
    "check_entities_batch",
    "check_entity_exists",
    "compare_entities_state",
    "compare_entity_health_snapshot",
    "compare_templates",
    "device_get_wifi_status",
    "diagnose_automation",
    "diagnose_config_entry",
    "diagnose_entity",
    "diagnose_template",
    "entity_get_context_tree",
    "eval_templates_batch",
    "get_area_automation_summary",
    "get_area_diagnostic",
    "get_area_devices_summary",
    "get_area_overview",
    "get_automation_code",
    "get_automation_codes_batch",
    "get_automation_conflicts",
    "get_automation_dependencies",
    "get_automation_entity_id",
    "get_automation_file_location",
    "get_automation_usage_stats",
    "get_blueprint_code",
    "get_blueprint_instances",
    "get_component_logs",
    "get_config_entry_details",
    "get_device_details",
    "get_device_entities",
    "get_devices_by_area",
    "get_entity_consumers",
    "get_entity_context",
    "get_entity_details",
    "get_entity_dependencies",
    "get_entity_state",
    "get_entity_state_batch",
    "get_entity_state_history_summary",
    "get_entity_with_automations",
    "get_history_batch",
    "get_history_stats",
    "get_integration_entities",
    "get_integration_health",
    "get_integration_summary",
    "get_lovelace_entity_usage",
    "get_scene_code",
    "get_script_code",
    "get_template_dependencies",
    "get_template_entity_code",
    "get_template_entities_batch",
    "get_template_performance",
    "graph_entity_impact",
    "graph_export_mermaid",
    "graph_find_references",
    "graph_get_neighbors",
    "investigate_entity",
    "read_config_file",
    "read_file",
    "search_automations_by_entity",
    "search_entities",
    "search_entity_by_name",
    "search_files",
    "search_in_config",
    "search_in_config_batch",
    "search_inside_automations",
    "search_logs",
    "test_condition",
    "test_service_call",
    "test_template",
    "test_templates_batch",
    "validate_automation_trigger",
    "validate_yaml_batch",
}


def _call_tool_safe(tool_name, **params):
    """Call a tool, return parsed JSON or None on error."""
    try:
        resp = requests.post(
            f"{REST_API_URL}/api/tools/{tool_name}",
            json=params or {},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


class TestResponseFormatCompliance:
    """Every tool must return a standard JSON with success field."""

    def test_all_tools_return_success_field(self):
        """All callable tools should have success field in response."""
        tools = _list_tools()
        missing = []
        errors = []

        for tool in tools:
            name = tool["name"]
            if name in _REQUIRES_PARAMS:
                continue

            data = _call_tool_safe(name)
            if data is None:
                errors.append(f"{name}: HTTP error or timeout")
                continue

            if "success" not in data:
                result = data.get("result", {})
                if isinstance(result, dict) and "success" not in result:
                    missing.append(name)
                elif not isinstance(result, dict):
                    missing.append(name)

        assert len(missing) == 0, f"Tools missing success field: {missing}\nErrors: {errors}"
        assert len(errors) == 0, f"Tools with errors: {errors}"
