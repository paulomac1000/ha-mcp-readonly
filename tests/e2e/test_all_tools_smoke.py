"""E2E smoke: parametrized test that calls EVERY tool via the authenticated REST API.

Each tool variant sends a POST to /api/tools/{name} and asserts:
  - HTTP 200
  - JSON response with success=True

Tools without required params are called with {}.
Tools with required params use PARAMS_MAP entries resolved from the live
server where possible (see ``discover_live_context``). Tools whose
prerequisites do not exist on the observed Home Assistant instance are
marked xfail with a clear reason instead of being skipped silently.
"""

import json

import pytest
import requests

from .conftest import (
    HA_TOKEN,
    REST_API_URL,
    REST_AUTH_CONFIGURED,
    REST_HEADERS,
    _server_running,
    discover_live_context,
)

pytestmark = pytest.mark.skipif(
    not _server_running()
    or not HA_TOKEN
    or not REST_AUTH_CONFIGURED
    or HA_TOKEN in ("", "your_long_lived_access_token_here"),
    reason="MCP server not running or HA_TOKEN not configured",
)


def _list_tools():
    """Fetch full tool list from the REST API."""
    resp = requests.get(f"{REST_API_URL}/api/tools?detail=full", headers=REST_HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("tools", data)


# ---------------------------------------------------------------------------
# Tools that always return success=False in this test environment.
# Marked xfail because the tool logic is correct but HA state / logs /
# history API is not in the expected condition.
# ---------------------------------------------------------------------------
_TOOLS_ENV_FAIL: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Tools whose parameters require an entity/device that only exists on
# Tasmota devices or in a specific deployment. Resolved live when possible;
# otherwise the tool is marked xfail for this environment.
# ---------------------------------------------------------------------------


def _discover_params(context: dict) -> dict:
    """Build the per-tool parameter map from live-discovered identifiers."""
    automation_id = context.get("automation_id")
    automation_alias = context.get("automation_alias")
    area_id = context.get("area_id")
    device_id = context.get("device_id")
    entry_id = context.get("entry_id")
    script_id = context.get("script_id")
    scene_id = context.get("scene_id")
    blueprint_path = context.get("blueprint_path")
    template_entity_id = context.get("template_entity_id")
    person_entity_id = context.get("person_entity_id")
    integration_domain = context.get("integration_domain") or "template"
    snapshot_id = context.get("snapshot_id")

    return {
        "automation_validate_triggers": {
            "automation_alias": automation_alias,
        },
        "bulk_search_entities": {"search_terms": "light"},
        "check_entities_batch": {"entity_ids": "sun.sun"},
        "check_entity_exists": {"entity_id": "sun.sun"},
        "compare_entities_state": {"entity_ids": "sun.sun"},
        "compare_entity_health_snapshot": {"snapshot_id": snapshot_id},
        "compare_templates": {
            "template_a": "{{ 1+1 }}",
            "template_b": "{{ 2+2 }}",
        },
        "device_get_wifi_status": {"device_id": device_id},
        "diagnose_automation": {"automation_id": automation_id},
        "diagnose_config_entry": {"entry_id": entry_id},
        "diagnose_entity": {"entity_id": "sun.sun"},
        "diagnose_person_tracking": {"person_entity": person_entity_id},
        "diagnose_template": {"entity_id": template_entity_id},
        "entity_get_context_tree": {"entity_id": "sun.sun"},
        "eval_templates_batch": {"templates": json.dumps(["{{ 1+1 }}"])},
        "get_all_states": {"domain": "sun"},
        "get_area_automation_summary": {"area_id": area_id},
        "get_area_devices_summary": {"area_id": area_id},
        "get_area_diagnostic": {"area_name": area_id},
        "get_area_overview": {"area_id": area_id},
        "get_automation_code": {"automation_id": automation_id},
        "get_automation_codes_batch": {"automation_ids": automation_id},
        "get_automation_conflicts": {"entity_id": "sun.sun"},
        "get_automation_dependencies": {"automation_id": automation_id},
        "get_automation_entity_id": {
            "identifier": automation_alias,
        },
        "get_automation_file_location": {"automation_id": automation_id},
        "get_automation_usage_stats": {"automation_id": automation_id},
        "get_blueprint_code": {
            "blueprint_path": blueprint_path,
        },
        "get_blueprint_instances": {
            "blueprint_path": blueprint_path,
        },
        "get_component_logs": {"component_name": "homeassistant"},
        "get_config_entry_details": {"entry_id": entry_id},
        "get_context_chain": {"entity_id": "sun.sun"},
        "get_device_details": {"device_id": device_id},
        "get_device_entities": {"device_id": device_id},
        "get_device_triggers": {"device_id": device_id},
        "get_devices_by_area": {"area_id": area_id},
        "get_entity_consumers": {"entity_id": "sun.sun"},
        "get_entity_context": {"entity_id": person_entity_id or "sun.sun"},
        "get_entity_details": {"entity_id": "sun.sun"},
        "get_entity_dependencies": {"entity_id": "sun.sun"},
        "get_entity_state": {"entity_id": "sun.sun"},
        "get_entity_state_batch": {"entity_ids": "sun.sun"},
        "get_entity_state_history_summary": {"entity_id": "sun.sun"},
        "get_entity_with_automations": {"entity_id": "sun.sun"},
        "get_history_batch": {"entity_ids": "sun.sun", "hours_back": 1},
        "get_history_stats": {"entity_id": "sun.sun"},
        "get_integration_entities": {"domain": integration_domain},
        "get_integration_health": {"domain": integration_domain},
        "get_integration_summary": {"domain": integration_domain},
        "get_lovelace_entity_usage": {"entity_id": "sun.sun"},
        "get_scene_code": {"scene_id": scene_id},
        "get_script_code": {"script_id": script_id},
        "get_template_dependencies": {
            "entity_id": template_entity_id,
        },
        "get_template_entities_batch": {"entity_ids": "sun.sun"},
        "get_template_entity_code": {
            "entity_id": template_entity_id,
        },
        "get_template_performance": {"template": "{{ 1+1 }}"},
        "graph_entity_impact": {"entity_id": "sun.sun"},
        "graph_export_mermaid": {"node_id": "entity:sun.sun"},
        "graph_find_references": {"entity_id": "sun.sun"},
        "graph_get_neighbors": {"node_id": "entity:sun.sun"},
        "investigate_entity": {"search_term": "sun"},
        "read_config_file": {"file_path": "configuration.yaml", "max_lines": 1},
        "read_file": {
            "file_path": f"{context.get('config_root', '/config')}/configuration.yaml",
            "max_lines": 1,
        },
        "resolve_blueprint_automation": {"automation_id": automation_id},
        "search_automations_by_entity": {"entity_id": "sun.sun"},
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


def _missing_prereq_reasons(context: dict) -> dict:
    """Map tool names to missing-prerequisite reasons (for xfail marks)."""
    missing: dict[str, str] = {}
    for key, label in (
        ("automation_id", "no automations configured"),
        ("area_id", "no areas defined"),
        ("device_id", "no devices in registry"),
        ("entry_id", "no config entries"),
        ("script_id", "no scripts defined"),
        ("scene_id", "no scenes defined"),
        ("blueprint_path", "no blueprints installed"),
        ("template_entity_id", "no template entities"),
        ("person_entity_id", "no person entities"),
        ("snapshot_id", "could not take a health snapshot"),
    ):
        if context.get(key):
            continue
        if key == "automation_id":
            for name in (
                "automation_validate_triggers",
                "diagnose_automation",
                "get_automation_code",
                "get_automation_codes_batch",
                "get_automation_dependencies",
                "get_automation_entity_id",
                "get_automation_file_location",
                "get_automation_usage_stats",
                "resolve_blueprint_automation",
            ):
                missing.setdefault(name, label)
        elif key == "area_id":
            for name in (
                "get_area_automation_summary",
                "get_area_devices_summary",
                "get_area_diagnostic",
                "get_area_overview",
                "get_devices_by_area",
            ):
                missing.setdefault(name, label)
        elif key == "device_id":
            for name in (
                "device_get_wifi_status",
                "get_device_details",
                "get_device_entities",
                "get_device_triggers",
            ):
                missing.setdefault(name, label)
        elif key == "entry_id":
            for name in ("diagnose_config_entry", "get_config_entry_details"):
                missing.setdefault(name, label)
        elif key == "script_id":
            missing.setdefault("get_script_code", label)
        elif key == "scene_id":
            missing.setdefault("get_scene_code", label)
        elif key == "blueprint_path":
            for name in ("get_blueprint_code", "get_blueprint_instances"):
                missing.setdefault(name, label)
        elif key == "template_entity_id":
            for name in (
                "diagnose_template",
                "get_template_dependencies",
                "get_template_entity_code",
            ):
                missing.setdefault(name, label)
        elif key == "person_entity_id":
            missing.setdefault("diagnose_person_tracking", label)
        elif key == "snapshot_id":
            missing.setdefault("compare_entity_health_snapshot", label)
    return missing


def _load_tool_cases():
    """Build (tool_name, params, marks) tuples for parametrization."""
    tools = _list_tools()
    context = discover_live_context()
    params_map = _discover_params(context)
    missing_reasons = _missing_prereq_reasons(context)

    cases = []
    for t in tools:
        name = t["name"]
        params = params_map.get(name)
        marks = []
        reason = _TOOLS_ENV_FAIL.get(name)
        if reason:
            marks.append(pytest.mark.xfail(strict=True, reason=reason))
        missing_reason = missing_reasons.get(name)
        if missing_reason:
            marks.append(
                pytest.mark.xfail(
                    strict=True,
                    reason=f"Prerequisite missing in this environment: {missing_reason}",
                )
            )
        cases.append(pytest.param(name, params, marks=marks, id=name))
    return cases


# Build parametrized test cases once at module load
_LIVE_E2E_AVAILABLE = (
    _server_running()
    and bool(HA_TOKEN)
    and HA_TOKEN not in ("", "your_long_lived_access_token_here")
)
_TOOL_CASES = _load_tool_cases() if _LIVE_E2E_AVAILABLE else []


@pytest.mark.parametrize("tool_name,params", _TOOL_CASES)
def test_tool_smoke(tool_name, params):
    """Call every tool and verify success=True."""
    body = params if params is not None else {}
    resp = requests.post(
        f"{REST_API_URL}/api/tools/{tool_name}",
        json=body,
        headers=REST_HEADERS,
        timeout=120,
    )
    assert resp.status_code == 200, f"{tool_name}: HTTP {resp.status_code} {resp.text[:200]}"

    data = resp.json()
    assert data.get("success") is True, (
        f"{tool_name}: success=False, error={data.get('error', 'N/A')[:200]}"
    )
