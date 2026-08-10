"""Capability introspection tool.

Exposes the full tool catalog with capability manifests over the MCP
transport itself. The REST endpoint ``GET /api/tools/{name}/manifest`` is
unreachable for an agent connected over pure MCP; this tool closes that
gap (mcp-server-standards.md, rule 2b, L3+).
"""

import logging
import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any

from tools import TOOLS_VERSION
from tools.constants import DEV_TOOLS_ENABLED, MCP_TRANSPORT, REST_API_ENABLED
from tools.manifests import (
    active_profile_initialized,
    get_all_manifests,
    get_inactive_reasons,
    make_manifest,
    register_manifest,
)
from tools.utils import _error_response, _success_response
from version import __version__

_logger = logging.getLogger(__name__)

CAPABILITIES_SCHEMA_VERSION = "1.1"

# Category assignment rules: (regex_pattern, category_name).
# Tools are matched in order; first match wins. Unmatched tools fall into "Other".
CATEGORY_PREFIXES: list[tuple[str, str]] = [
    (
        r"get_entity_state|get_all_states|get_domains_|get_system_overview|get_states_|^search_entit",
        "States",
    ),
    (
        r"list_automations|get_automation_|^search_automations|^search_inside_|validate_automation|^automation_validate|^list_automation_categor",
        "Automations",
    ),
    (r"list_scripts|get_script_|list_scenes|get_scene_", "Scripts & Scenes"),
    (r"list_blueprints|get_blueprint_", "Blueprints"),
    (r"get_device_|search_devices|get_devices_by_area|device_get_|get_area_", "Devices & Areas"),
    (
        r"get_config_|read_config_|get_main_configuration|validate_yaml|^search_config_|^search_in_config\b|list_config_entry",
        "Config",
    ),
    (
        r"get_log_|search_logs|analyze_log_|get_previous_logs|get_recent_logs|get_startup_errors|get_component_logs",
        "Logs",
    ),
    (r"get_history_|get_recent_state_|get_entity_changes", "History"),
    (
        r"diagnose_|trigger_health|get_entity_context|get_entity_dependencies|get_entity_consumers|compare_entity_health|take_entity_health|verify_recent_|get_integration_health|get_unavailable_",
        "Diagnostics",
    ),
    (r"search_lovelace|get_lovelace|list_themes", "Lovelace"),
    (
        r"bulk_|compare_entities_state|check_entities|get_automation_codes_batch|get_entity_registry_batch|eval_templates_batch|test_templates_batch|search_in_config_batch|search_registries_batch|validate_yaml_batch",
        "Batch",
    ),
    (r"investigate_|get_area_diagnostic|get_entity_with_automations", "Composite"),
    (
        r"test_template|check_entity_exists|test_condition|test_service_call|get_template_|compare_templates",
        "Dev Tools",
    ),
    (r"graph_", "Graph"),
    (
        r"describe_|get_exposed_entities|get_hacs_data|hacs_get_|get_nfc_tags|get_services|get_notification_|get_input_helpers|get_counters|get_timers|get_persons|get_zones|get_energy_dashboard|list_custom_components",
        "System",
    ),
    (
        r"entity_get_|get_entity_details|get_entity_registry\b|search_registries",
        "Storage & Registry",
    ),
    (r"search_files|list_directory|read_file", "Filesystem"),
]


def _categorize_tool(name: str) -> str:
    """Assign a tool name to a category by matching against prefix patterns."""
    for pattern, category in CATEGORY_PREFIXES:
        if re.match(pattern, name):
            return category
    return "Other"


def _installed_version(distribution: str) -> str:
    try:
        return package_version(distribution)
    except PackageNotFoundError:
        return "unknown"


def _do_describe_ha_capabilities(
    supported_names: set[str] | None = None,
) -> dict[str, Any]:
    """Build supported and active catalogs without contacting external dependencies."""
    declared_manifests = get_all_manifests(active_only=False)
    supported_manifests = (
        declared_manifests
        if supported_names is None
        else {
            name: manifest
            for name, manifest in declared_manifests.items()
            if name in supported_names
        }
    )
    initialized = active_profile_initialized()
    active_manifests = get_all_manifests(active_only=True) if initialized else {}
    inactive_reasons = get_inactive_reasons() if initialized else {}
    active_names = set(active_manifests) & set(supported_manifests)
    tools = []
    for manifest in supported_manifests.values():
        item = dict(manifest)
        name = str(item.get("name", ""))
        runtime_active = name in active_names if initialized else None
        item["runtime_active"] = runtime_active
        if runtime_active is False:
            item["active_state"] = "inactive"
        if name in inactive_reasons:
            item["inactive_reason"] = inactive_reasons[name]
        tools.append(item)
    tools.sort(key=lambda manifest: str(manifest.get("name", "")))

    categories: dict[str, dict[str, Any]] = {}
    for tool in tools:
        name = str(tool.get("name", ""))
        category = _categorize_tool(name)
        bucket = categories.setdefault(category, {"tool_count": 0, "tools": []})
        bucket["tools"].append(
            {
                "name": name,
                "description": str(tool.get("description", "")),
                "active": tool.get("runtime_active"),
                "inactive_reason": tool.get("inactive_reason"),
            }
        )
        bucket["tool_count"] = len(bucket["tools"])

    supported_transports = ["stdio", "streamable-http"]
    active_transport = "stdio" if MCP_TRANSPORT == "stdio" else "streamable-http"
    compatibility_adapters = ["authenticated-rest"] if REST_API_ENABLED else []
    protocol_versions = sorted(
        {
            str(revision)
            for manifest in tools
            for revision in manifest.get("protocol_revisions", [])
            if revision
        }
    )
    active_count = len(active_names) if initialized else len(tools)
    return {
        "schema_version": CAPABILITIES_SCHEMA_VERSION,
        "server": "HA-Observer",
        "server_version": __version__,
        "tools_version": TOOLS_VERSION,
        "sdk": {
            "family": "fastmcp",
            "distribution": "fastmcp",
            "version": _installed_version("fastmcp"),
        },
        "protocol_versions": protocol_versions,
        "supported_transports": supported_transports,
        "active_transports": [active_transport],
        "compatibility_adapters": compatibility_adapters,
        "transports": supported_transports + compatibility_adapters,
        "profile": {
            "mcp_transport": active_transport,
            "dev_tools_enabled": DEV_TOOLS_ENABLED,
            "rest_api_enabled": REST_API_ENABLED,
        },
        "tool_count": active_count,
        "supported_tool_count": len(tools),
        "active_profile_initialized": initialized,
        "active_tool_count": active_count,
        "inactive_tool_count": len(tools) - active_count if initialized else None,
        "supported_component_counts": {"tools": len(tools), "resources": 0, "prompts": 0},
        "active_component_counts": {"tools": active_count, "resources": 0, "prompts": 0},
        "tools": tools,
        "categories": categories,
    }


def register_capability_tools(mcp: Any) -> None:
    """Register the capability introspection tool on the MCP server."""

    manifest = make_manifest("describe_ha_capabilities", timeout_ms=1000, latency="interactive")
    manifest["extensions"]["target_binding"] = {
        "kind": "deployment-resource",
        "target": "runtime",
        "revalidation": "per-invocation",
    }
    register_manifest("describe_ha_capabilities", manifest)

    @mcp.tool()
    async def describe_ha_capabilities() -> str:
        """Return the supported catalog and active deployment profile.

        This is a zero-I/O introspection tool. It lets an AI agent inspect
        every governed capability's risk, side effects, activation state and
        inactive reason without invoking the operation itself.

        Args:
            None.

        Returns:
            JSON string with a ``success`` flag and a payload containing the
            supported and active catalog counts plus per-tool manifests.
        """
        try:
            names = getattr(mcp, "names", None)
            supported_names = names() if callable(names) else None
            return _success_response(_do_describe_ha_capabilities(supported_names=supported_names))
        except Exception as exc:
            _logger.error("describe_ha_capabilities failed: %s", exc)
            return _error_response(str(exc))
