"""Capability introspection tool.

Exposes the full tool catalog with capability manifests over the MCP
transport itself. The REST endpoint ``GET /api/tools/{name}/manifest`` is
unreachable for an agent connected over pure MCP/SSE; this tool closes that
gap (mcp-server-standards.md, rule 2b, L3+).
"""

import logging
import re
from typing import Any

from tools import TOOLS_VERSION
from tools.manifests import get_all_manifests, make_manifest, register_manifest
from tools.utils import _error_response, _success_response

_logger = logging.getLogger(__name__)

CAPABILITIES_SCHEMA_VERSION = "1.0"

# Category assignment rules: (regex_pattern, category_name).
# Tools are matched in order; first match wins. Unmatched tools fall into "Other".
CATEGORY_PREFIXES: list[tuple[str, str]] = [
    (r"get_entity_state|get_all_states|get_domains_|get_system_overview|get_states_|^search_entit", "States"),
    (r"list_automations|get_automation_|^search_automations|^search_inside_|validate_automation|^automation_validate|^list_automation_categor", "Automations"),
    (r"list_scripts|get_script_|list_scenes|get_scene_", "Scripts & Scenes"),
    (r"list_blueprints|get_blueprint_", "Blueprints"),
    (r"get_device_|search_devices|get_devices_by_area|device_get_|get_area_", "Devices & Areas"),
    (r"get_config_|read_config_|get_main_configuration|validate_yaml|^search_config_|^search_in_config\b|list_config_entry", "Config"),
    (r"get_log_|search_logs|analyze_log_|get_previous_logs|get_recent_logs|get_startup_errors|get_component_logs", "Logs"),
    (r"get_history_|get_recent_state_|get_entity_changes", "History"),
    (r"diagnose_|trigger_health|get_entity_context|get_entity_dependencies|get_entity_consumers|compare_entity_health|take_entity_health|verify_recent_|get_integration_health|get_unavailable_", "Diagnostics"),
    (r"search_lovelace|get_lovelace|list_themes", "Lovelace"),
    (r"bulk_|compare_entities_state|check_entities|get_automation_codes_batch|get_entity_registry_batch|eval_templates_batch|test_templates_batch|search_in_config_batch|search_registries_batch|validate_yaml_batch", "Batch"),
    (r"investigate_|get_area_diagnostic|get_entity_with_automations", "Composite"),
    (r"test_template|check_entity_exists|test_condition|test_service_call|get_template_|compare_templates", "Dev Tools"),
    (r"graph_", "Graph"),
    (r"describe_|get_exposed_entities|get_hacs_data|hacs_get_|get_nfc_tags|get_services|get_notification_|get_input_helpers|get_counters|get_timers|get_persons|get_zones|get_energy_dashboard|list_custom_components", "System"),
    (r"entity_get_|get_entity_details|get_entity_registry\b|search_registries", "Storage & Registry"),
    (r"search_files|list_directory|read_file", "Filesystem"),
]


def _categorize_tool(name: str) -> str:
    """Assign a tool name to a category by matching against prefix patterns."""
    for pattern, category in CATEGORY_PREFIXES:
        if re.match(pattern, name):
            return category
    return "Other"


def _do_describe_ha_capabilities() -> dict[str, Any]:
    """Build the capability catalog from registered tool manifests. Zero I/O.

    Returns:
        Dict with schema_version, server name, tools_version, supported
        transports, tool_count, the sorted list of tool manifests, and
        a categories dict grouping tools by category.
    """
    manifests = get_all_manifests()
    tools = sorted(manifests.values(), key=lambda m: str(m.get("name", "")))

    # Group tools by category
    categories: dict[str, dict[str, Any]] = {}
    for t in tools:
        name = str(t.get("name", ""))
        cat_name = _categorize_tool(name)
        if cat_name not in categories:
            categories[cat_name] = {"tool_count": 0, "tools": []}
        categories[cat_name]["tools"].append({"name": name, "description": str(t.get("description", ""))})
        categories[cat_name]["tool_count"] = len(categories[cat_name]["tools"])

    return {
        "schema_version": CAPABILITIES_SCHEMA_VERSION,
        "server": "HA-Observer",
        "tools_version": TOOLS_VERSION,
        "transports": ["sse", "rest"],
        "tool_count": len(tools),
        "tools": tools,
        "categories": categories,
    }


def register_capability_tools(mcp: Any) -> None:
    """Register the capability introspection tool on the MCP server."""

    register_manifest(
        "describe_ha_capabilities",
        make_manifest("describe_ha_capabilities", timeout_ms=1000, latency="instant"),
    )

    @mcp.tool()
    async def describe_ha_capabilities() -> str:
        """Return the catalog of registered tools with their capability manifests.

        This is a zero-I/O introspection tool. It lets an AI agent inspect
        every tool's risk level, side effects, determinism, latency and other
        manifest metadata without invoking the tools themselves. Unlike the
        REST-only manifest endpoint, this works over the MCP/SSE transport.

        Args:
            None.

        Returns:
            JSON string with a ``success`` flag and a payload containing
            ``schema_version``, ``tools_version``, supported ``transports``,
            ``tool_count`` and the list of tool manifests.
        """
        try:
            return _success_response(_do_describe_ha_capabilities())
        except Exception as exc:
            _logger.error("describe_ha_capabilities failed: %s", exc)
            return _error_response(str(exc))
