"""E2E smoke: parametrized test that calls EVERY tool via REST API.

Each tool variant sends a POST to /api/tools/{name} and asserts:
  - HTTP 200
  - JSON response with success=True

Tools without required params are called with {}.
Tools with required params use PARAMS_MAP entries.

Tools known to return non-dict content (YAML text, not JSON) via the
REST API wrapper are marked xfail — the tool itself works but the REST
layer cannot wrap the response as a standard JSON dict.
"""

import json

import pytest
import requests

from .conftest import HA_TOKEN, REST_API_URL, _server_running

pytestmark = pytest.mark.skipif(
    not _server_running() or not HA_TOKEN or HA_TOKEN in ("", "your_long_lived_access_token_here"),
    reason="MCP server not running or HA_TOKEN not configured",
)


def _list_tools():
    """Fetch full tool list from the REST API."""
    resp = requests.get(f"{REST_API_URL}/api/tools?detail=full", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("tools", data)


# ---------------------------------------------------------------------------
# Tools whose REST API wrapper returns non-dict content -> always 502.
# The tool implementations are correct; the REST layer cannot wrap them.
# ---------------------------------------------------------------------------
_TOOLS_NON_DICT_RESPONSE: set = {}

# ---------------------------------------------------------------------------
# Tools that always return success=False in this test environment.
# Marked xfail because the tool logic is correct but HA state / logs /
# history API is not in the expected condition.
# ---------------------------------------------------------------------------
_TOOLS_ENV_FAIL = {
    "diagnose_automation_aliases": "Full automation scan exceeds 120s timeout",
    "get_recent_state_changes": "HA history API returned 400",
    "get_startup_errors": "No startup marker found in current logs",
}

# ---------------------------------------------------------------------------
# Tools that are inherently slow (>60s) on a real HA instance.
# ---------------------------------------------------------------------------
_TOOLS_SLOW = {
    "diagnose_automation_aliases",
    "diagnose_category_alias_mismatch",
}

# ---------------------------------------------------------------------------
# Minimal valid params for every tool that requires them.
# Values are extracted from the live system where possible.
# ---------------------------------------------------------------------------
PARAMS_MAP = {
    "automation_validate_triggers": {
        "automation_alias": "Morning Routine",
    },
    "bulk_search_entities": {"search_terms": "light"},
    "check_entities_batch": {"entity_ids": "sun.sun"},
    "check_entity_exists": {"entity_id": "sun.sun"},
    "compare_entities_state": {"entity_ids": "sun.sun"},
    "compare_entity_health_snapshot": {"snapshot_id": "snap_1781126439"},
    "compare_templates": {
        "template_a": "{{ 1+1 }}",
        "template_b": "{{ 2+2 }}",
    },
    "device_get_wifi_status": {"device_id": "c67a8024bc53a3d38dacc8c8c6e01cf6"},
    "diagnose_automation": {"automation_id": "1679916667559"},
    "diagnose_config_entry": {"entry_id": "b4b4c205fd776ff29b186a46cf187b70"},
    "diagnose_entity": {"entity_id": "sun.sun"},
    "diagnose_person_tracking": {"person_entity": "person.test_user"},
    "diagnose_template": {"entity_id": "binary_sensor.motion"},
    "entity_get_context_tree": {"entity_id": "sun.sun"},
    "eval_templates_batch": {"templates": json.dumps(["{{ 1+1 }}"])},
    "get_all_states": {"domain": "sun"},
    "get_area_automation_summary": {"area_id": "office"},
    "get_area_devices_summary": {"area_id": "office"},
    "get_area_diagnostic": {"area_name": "office"},
    "get_area_overview": {"area_id": "office"},
    "get_automation_code": {"automation_id": "1679916667559"},
    "get_automation_codes_batch": {"automation_ids": "1679916667559"},
    "get_automation_conflicts": {"entity_id": "sun.sun"},
    "get_automation_dependencies": {"automation_id": "1679916667559"},
    "get_automation_entity_id": {
        "identifier": "Morning Routine",
    },
    "get_automation_file_location": {"automation_id": "1679916667559"},
    "get_automation_usage_stats": {"automation_id": "1679916667559"},
    "get_blueprint_code": {
        "blueprint_path": "automation/homeassistant/motion_light.yaml",
    },
    "get_blueprint_instances": {
        "blueprint_path": "automation/homeassistant/motion_light.yaml",
    },
    "get_component_logs": {"component_name": "homeassistant"},
    "get_config_entry_details": {"entry_id": "b4b4c205fd776ff29b186a46cf187b70"},
    "get_context_chain": {"entity_id": "sun.sun"},
    "get_device_details": {"device_id": "c67a8024bc53a3d38dacc8c8c6e01cf6"},
    "get_device_entities": {"device_id": "c67a8024bc53a3d38dacc8c8c6e01cf6"},
    "get_device_triggers": {"device_id": "c67a8024bc53a3d38dacc8c8c6e01cf6"},
    "get_devices_by_area": {"area_id": "office"},
    "get_entity_consumers": {"entity_id": "sun.sun"},
    "get_entity_context": {"entity_id": "person.test_user"},
    "get_entity_details": {"entity_id": "person.test_user"},
    "get_entity_dependencies": {"entity_id": "sun.sun"},
    "get_entity_state": {"entity_id": "sun.sun"},
    "get_entity_state_batch": {"entity_ids": "sun.sun"},
    "get_entity_state_history_summary": {"entity_id": "sun.sun"},
    "get_entity_with_automations": {"entity_id": "person.test_user"},
    "get_history_batch": {"entity_ids": "sun.sun", "hours_back": 1},
    "get_history_stats": {"entity_id": "sun.sun"},
    "get_integration_entities": {"domain": "mqtt"},
    "get_integration_health": {"domain": "mqtt"},
    "get_integration_summary": {"domain": "mqtt"},
    "get_lovelace_entity_usage": {"entity_id": "sun.sun"},
    "get_scene_code": {"scene_id": "1746439620709"},
    "get_script_code": {"script_id": "morning_routine"},
    "get_template_dependencies": {
        "entity_id": "binary_sensor.motion",
    },
    "get_template_entities_batch": {"entity_ids": "sun.sun"},
    "get_template_entity_code": {
        "entity_id": "binary_sensor.motion",
    },
    "get_template_performance": {"template": "{{ 1+1 }}"},
    "graph_entity_impact": {"entity_id": "sun.sun"},
    "graph_export_mermaid": {"node_id": "sun.sun"},
    "graph_find_references": {"entity_id": "sun.sun"},
    "graph_get_neighbors": {"node_id": "sun.sun"},
    "investigate_entity": {"search_term": "sun"},
    "read_config_file": {"file_path": "configuration.yaml", "max_lines": 1},
    "read_file": {"file_path": "/config/configuration.yaml", "max_lines": 1},
    "resolve_blueprint_automation": {"automation_id": "1679916667559"},
    "search_automations_by_entity": {"entity_id": "binary_sensor.motion"},
    "search_config_by_params": {"entity_id": "sun.sun"},
    "search_entities": {"search_term": "sun"},
    "search_entity_by_name": {"search_term": "sun"},
    "search_files": {"pattern": "configuration"},
    "search_in_config": {"search_term": "sun"},
    "search_in_config_batch": {"search_terms": "sun"},
    "search_inside_automations": {"pattern": "sun"},
    "search_logs": {"search_term": "started"},
    "test_condition": {"condition_template": "{{ 1 == 1 }}"},
    "test_service_call": {"domain": "light", "service": "turn_on"},
    "test_template": {"template": "{{ 1+1 }}"},
    "test_templates_batch": {"templates": json.dumps(["{{ 1+1 }}"])},
    "validate_automation_trigger": {
        "trigger_config": "platform: state\nentity_id: sun.sun",
    },
    "validate_yaml_batch": {"file_paths": "configuration.yaml"},
    "validate_yaml_syntax": {"yaml_content": "test: 1"},
}


def _load_tool_cases():
    """Build (tool_name, params, marks) tuples for parametrization."""
    tools = _list_tools()
    cases = []
    for t in tools:
        name = t["name"]
        params = PARAMS_MAP.get(name)
        marks = []
        if name in _TOOLS_NON_DICT_RESPONSE:
            marks.append(
                pytest.mark.xfail(
                    strict=True,
                    reason="Tool returns non-dict content; REST API wrapper cannot serialize",
                )
            )
        env_fail_reason = _TOOLS_ENV_FAIL.get(name)
        if env_fail_reason:
            marks.append(
                pytest.mark.xfail(
                    strict=True,
                    reason=env_fail_reason,
                )
            )
        cases.append(pytest.param(name, params, marks=marks, id=name))
    return cases


# Build parametrized test cases once at module load
_TOOL_CASES = _load_tool_cases()


@pytest.mark.parametrize("tool_name,params", _TOOL_CASES)
def test_tool_smoke(tool_name, params):
    """Call every tool and verify success=True."""
    body = params if params is not None else {}
    resp = requests.post(
        f"{REST_API_URL}/api/tools/{tool_name}",
        json=body,
        timeout=120,
    )
    assert resp.status_code == 200, f"{tool_name}: HTTP {resp.status_code} {resp.text[:200]}"

    data = resp.json()
    assert data.get("success") is True, (
        f"{tool_name}: success=False, error={data.get('error', 'N/A')[:200]}"
    )
