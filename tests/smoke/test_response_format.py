"""Smoke test: verify ALL tools return standard JSON with success field."""

import requests

from .conftest import REST_API_URL


def _list_tools():
    resp = requests.get(f"{REST_API_URL}/api/tools", timeout=10)
    resp.raise_for_status()
    return resp.json()["tools"]


# Tools that require mandatory parameters — can't call without them
_REQUIRES_PARAMS = {
    "read_config_file",
    "read_file",
    "search_files",
    "search_logs",
    "search_in_config",
    "search_in_config_batch",
    "get_component_logs",
    "get_entity_dependencies",
    "get_entity_consumers",
    "get_entity_context",
    "entity_get_context_tree",
    "get_entity_state",
    "get_entity_state_batch",
    "get_entity_state_history_summary",
    "get_history_batch",
    "get_history_stats",
    "get_entity_details",
    "get_entity_changes",
    "check_entity_exists",
    "check_entities_batch",
    "diagnose_entity",
    "diagnose_template",
    "get_template_dependencies",
    "get_template_performance",
    "test_template",
    "test_templates_batch",
    "test_condition",
    "test_service_call",
    "validate_automation_trigger",
    "validate_yaml_syntax",
    "validate_yaml_batch",
    "investigate_entity",
    "get_entity_with_automations",
    "search_entities",
    "search_entity_by_name",
    "verify_recent_implementation",
    "compare_entities_state",
    "bulk_search_entities",
    "get_lovelace_entity_usage",
    "get_lovelace_config",
    "search_lovelace_config",
    "get_device_details",
    "get_device_entities",
    "device_get_wifi_status",
    "get_devices_by_area",
    "get_area_overview",
    "get_area_devices_summary",
    "get_area_diagnostic",
    "get_area_automation_summary",
    "get_integration_entities",
    "get_integration_summary",
    "get_integration_health",
    "get_config_entry_details",
    "diagnose_config_entry",
    "search_config_entries",
    "search_config_by_params",
    "get_automation_code",
    "get_automation_dependencies",
    "search_automations_by_entity",
    "get_automation_conflicts",
    "diagnose_automation",
    "get_automation_usage_stats",
    "automation_validate_triggers",
    "get_blueprint_code",
    "get_blueprint_instances",
    "get_script_code",
    "get_scene_code",
    "search_registries_batch",
    "search_devices",
    "get_recent_state_changes",
    "get_template_entity_code",
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
