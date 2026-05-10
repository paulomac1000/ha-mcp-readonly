"""
Entity Dependency Tools (P1 - Important)

Provides tools for tracking entity usage:
- get_entity_dependencies(entity_id)
- get_entity_consumers(entity_id)
"""

import json
import os
from pathlib import Path

from tools.utils import _error_response, _success_response, load_registry
from tools.yaml_utils import load_yaml_file

TOOLS_VERSION = "1.0.0"


def register_entity_dependency_tools(mcp, config_path: str, ha_url: str, ha_token: str):
    """Register entity dependency tracking tools."""

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _find_entity_in_automations(entity_id: str) -> list[dict]:
        """Find entity usage in automations.yaml."""
        automations_path = os.path.join(config_path, "automations.yaml")
        if not os.path.exists(automations_path):
            return []

        automations = load_yaml_file(automations_path) or []
        found = []

        # Helper to recursively search in dict/list
        def search_obj(obj, context=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, str) and entity_id in v:
                        return f"{context}.{k}" if context else k
                    path = search_obj(v, f"{context}.{k}" if context else k)
                    if path:
                        return path
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    if isinstance(item, str) and entity_id in item:
                        return f"{context}[{i}]" if context else f"[{i}]"
                    path = search_obj(item, f"{context}[{i}]" if context else f"[{i}]")
                    if path:
                        return path
            return None

        for auto in automations:
            # Skip if not a valid automation dict
            if not isinstance(auto, dict):
                continue

            # Quick check (convert to string)
            auto_str = str(auto)
            if entity_id not in auto_str:
                continue

            # Detailed check
            path = search_obj(auto)
            if path:
                found.append(
                    {
                        "id": auto.get("id"),
                        "alias": auto.get("alias", "Unnamed"),
                        "context": path,
                    }
                )

        return found

    def _find_entity_in_scripts(entity_id: str) -> list[dict]:
        """Find entity usage in scripts.yaml."""
        scripts_path = os.path.join(config_path, "scripts.yaml")
        if not os.path.exists(scripts_path):
            return []

        scripts = load_yaml_file(scripts_path) or {}
        found = []

        # Scripts can be dict (script_id: config) or list (rare)
        if isinstance(scripts, dict):
            for script_id, config in scripts.items():
                if entity_id in str(config):
                    found.append({"id": script_id, "alias": config.get("alias", script_id)})
        elif isinstance(scripts, list):
            for script in scripts:
                if entity_id in str(script):
                    found.append(
                        {
                            "id": script.get("id", "unknown"),
                            "alias": script.get("alias", "Unnamed"),
                        }
                    )

        return found

    def _find_entity_in_dashboards(entity_id: str) -> list[dict]:
        """Find entity usage in Lovelace dashboards."""
        storage_path = Path(config_path) / ".storage"
        if not storage_path.exists():
            return []

        found = []

        # List all lovelace files
        try:
            lovelace_files = [f for f in os.listdir(storage_path) if f.startswith("lovelace")]

            for lf in lovelace_files:
                try:
                    data = load_registry(lf, config_path)
                    if not data:
                        continue

                    data_str = json.dumps(data)
                    if entity_id in data_str:
                        # Try to find specific view
                        views = data.get("data", {}).get("config", {}).get("views", [])
                        matched_views = []

                        for view in views:
                            if entity_id in json.dumps(view):
                                matched_views.append(view.get("title", "Unknown View"))

                        dashboard_name = lf.replace("lovelace.", "")
                        if dashboard_name == "lovelace":
                            dashboard_name = "Default (Overview)"

                        found.append(
                            {
                                "dashboard": dashboard_name,
                                "file": lf,
                                "views": matched_views,
                            }
                        )
                except Exception:
                    continue
        except Exception:
            pass

        return found

    def _find_template_entities(entity_id: str) -> list[dict]:
        """Find template entities that reference this entity."""
        # Check config entries for template helpers
        entries = (
            load_registry("core.config_entries", config_path).get("data", {}).get("entries", [])
        )
        found = []

        for entry in entries:
            if entry.get("domain") == "template":
                options_str = str(entry.get("options", {}))
                if entity_id in options_str:
                    found.append({"name": entry.get("title"), "type": "helper"})

        # Check YAML configuration
        # Also scan files referenced via !include directives
        config_file = os.path.join(config_path, "configuration.yaml")
        if os.path.exists(config_file):
            try:
                with open(config_file, encoding="utf-8") as f:
                    content = f.read()
                    if "template:" in content and entity_id in content:
                        found.append(
                            {
                                "name": "configuration.yaml",
                                "type": "yaml",
                                "note": "Found in YAML configuration",
                            }
                        )
            except Exception:
                pass

            # Scan !include referenced files
            def _extract_includes(data):
                paths = []
                if isinstance(data, dict):
                    for v in data.values():
                        if isinstance(v, str) and v.startswith("!include "):
                            paths.append(v[9:])
                        elif isinstance(v, (dict, list)):
                            paths.extend(_extract_includes(v))
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, str) and item.startswith("!include "):
                            paths.append(item[9:])
                        elif isinstance(item, (dict, list)):
                            paths.extend(_extract_includes(item))
                return paths

            try:
                config_data = load_yaml_file(config_file)
                if config_data:
                    include_paths = _extract_includes(config_data)
                    for inc_path in include_paths:
                        full_path = os.path.join(config_path, inc_path)
                        if os.path.exists(full_path):
                            try:
                                with open(full_path, encoding="utf-8") as f:
                                    inc_content = f.read()
                                    if entity_id in inc_content:
                                        found.append(
                                            {
                                                "name": inc_path,
                                                "type": "include",
                                                "note": f"Found in !include file: {inc_path}",
                                            }
                                        )
                            except Exception:
                                pass
            except Exception:
                pass

        return found

    def _get_entity_dependencies_info(entity_id: str) -> dict:
        """Get dependencies (device, integration, etc) for an entity."""
        entities = (
            load_registry("core.entity_registry", config_path).get("data", {}).get("entities", [])
        )

        entity_reg = next((e for e in entities if e.get("entity_id") == entity_id), None)
        if not entity_reg:
            return {}

        result = {
            "platform": entity_reg.get("platform"),
            "config_entry_id": entity_reg.get("config_entry_id"),
            "device_id": entity_reg.get("device_id"),
        }

        # Resolve config entry title
        if result["config_entry_id"]:
            entries = (
                load_registry("core.config_entries", config_path).get("data", {}).get("entries", [])
            )
            entry = next(
                (e for e in entries if e.get("entry_id") == result["config_entry_id"]),
                None,
            )
            if entry:
                result["integration"] = entry.get("domain")
                result["integration_title"] = entry.get("title")

        # Resolve device name
        if result["device_id"]:
            devices = (
                load_registry("core.device_registry", config_path)
                .get("data", {})
                .get("devices", [])
            )
            device = next((d for d in devices if d.get("id") == result["device_id"]), None)
            if device:
                result["device_name"] = device.get("name_by_user") or device.get("name")
                result["via_device_id"] = device.get("via_device_id")

        return result

    # =========================================================================
    # TOOLS
    # =========================================================================

    @mcp.tool()
    async def get_entity_dependencies(entity_id: str) -> str:
        """[READ] Finds all places where entity is used (reverse lookup).

        ~85% token savings vs manually searching files.

        Args:
            entity_id: Entity id (e.g. "sensor.temperature")

        Returns:
            JSON with:
            - entity_id
            - used_in:
                - automations[]
                - scripts[]
                - templates[]
                - dashboards[]
            - depends_on:
                - device
                - integration
                - via_device
        """
        if not entity_id:
            return _error_response("entity_id is required")

        result = {
            "entity_id": entity_id,
            "used_in": {
                "automations": _find_entity_in_automations(entity_id),
                "scripts": _find_entity_in_scripts(entity_id),
                "templates": _find_template_entities(entity_id),
                "dashboards": _find_entity_in_dashboards(entity_id),
            },
            "depends_on": _get_entity_dependencies_info(entity_id),
        }

        # Add summary counts
        result["summary"] = {
            "automations_count": len(result["used_in"]["automations"]),
            "scripts_count": len(result["used_in"]["scripts"]),
            "templates_count": len(result["used_in"]["templates"]),
            "dashboards_count": len(result["used_in"]["dashboards"]),
            "total_usages": (
                len(result["used_in"]["automations"])
                + len(result["used_in"]["scripts"])
                + len(result["used_in"]["templates"])
                + len(result["used_in"]["dashboards"])
            ),
        }

        return _success_response(result)

    @mcp.tool()
    async def get_entity_consumers(entity_id: str) -> str:
        """[READ] List of automations and scripts using a given entity (simplified version).

        Args:
            entity_id: Entity id

        Returns:
            JSON with list of consumers
        """
        # Reuse internal functions
        automations = _find_entity_in_automations(entity_id)
        scripts = _find_entity_in_scripts(entity_id)

        consumers = []

        for auto in automations:
            consumers.append(
                {
                    "type": "automation",
                    "id": auto.get("id"),
                    "name": auto.get("alias"),
                    "context": auto.get("context"),
                }
            )

        for script in scripts:
            consumers.append(
                {"type": "script", "id": script.get("id"), "name": script.get("alias")}
            )

        return _success_response(
            {
                "entity_id": entity_id,
                "consumers_count": len(consumers),
                "consumers": consumers,
            }
        )
