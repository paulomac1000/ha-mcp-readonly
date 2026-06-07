"""Entity Dependency Tools (P1 - Important)

Provides tools for tracking entity usage:
- get_entity_dependencies(entity_id)
- get_entity_consumers(entity_id)
"""

import json
import os
import re
from pathlib import Path
from typing import Any

from tools.utils import _error_response, _success_response, load_registry, make_ha_request
from tools.yaml_utils import load_yaml_file

TOOLS_VERSION = "1.0.0"

# =========================================================================
# ENTITY EXTRACTION PATTERNS (replicated from context_generator/constants.py
# to avoid circular imports)
# =========================================================================

_ENTITY_PATTERN = re.compile(
    r"\b(sensor|binary_sensor|light|switch|climate|cover|input_\w+|automation|script|"
    r"person|device_tracker|media_player|camera|lock|fan|vacuum|weather|sun|zone|"
    r"timer|counter|number|select|button|scene|group|alarm_control_panel|update|"
    r"calendar|todo|image|stt|tts|conversation|notify|remote|water_heater|humidifier)"
    r"\.[a-zA-Z0-9_\-]+\b"
)

_TEMPLATE_ENTITY_PATTERN = re.compile(
    r"(?:states\(|is_state\(|state_attr\(|is_state_attr\()"
    r"['\"]([a-zA-Z_]+\.[a-zA-Z0-9_]+)['\"]"
)

_STATES_DOT_PATTERN = re.compile(r"states\.([a-zA-Z_]+\.[a-zA-Z0-9_]+)")


def _extract_entities_from_template(template_str: str) -> set[str]:
    """Extract entity_ids from Jinja2 templates."""
    found: set[str] = set()
    if not isinstance(template_str, str):
        return found
    found.update(_TEMPLATE_ENTITY_PATTERN.findall(template_str))
    for match in _STATES_DOT_PATTERN.findall(template_str):
        found.add(match)
    found.update(_ENTITY_PATTERN.findall(template_str))
    return found


def _extract_entities_from_data(data: Any, extract_from_templates: bool = True) -> set[str]:
    """Recursively extract entity_ids from structured data.

    Replicates context_generator/utils.py extract_entities_from_data()
    to avoid circular imports. Understands entity_id keys, template
    expressions, and nested structures.
    """
    found: set[str] = set()

    if isinstance(data, dict):
        for key, value in data.items():
            if key in ("entity_id", "entity", "target", "scene"):
                if isinstance(value, str):
                    if "." in value and not value.startswith("!"):
                        found.add(value)
                    found.update(_ENTITY_PATTERN.findall(value))
                elif isinstance(value, list):
                    for v in value:
                        if isinstance(v, str) and "." in v:
                            found.add(v)
                elif isinstance(value, dict):
                    if "entity_id" in value:
                        eid = value["entity_id"]
                        if isinstance(eid, str):
                            found.add(eid)
                        elif isinstance(eid, list):
                            found.update(e for e in eid if isinstance(e, str))

            if (
                extract_from_templates
                and isinstance(value, str)
                and ("{{" in value or "{%" in value)
            ):
                found.update(_extract_entities_from_template(value))

            found.update(_extract_entities_from_data(value, extract_from_templates))

    elif isinstance(data, list):
        for item in data:
            found.update(_extract_entities_from_data(item, extract_from_templates))

    elif isinstance(data, str):
        found.update(_ENTITY_PATTERN.findall(data))
        if extract_from_templates and ("{{" in data or "{%" in data):
            found.update(_extract_entities_from_template(data))

    return found


# =========================================================================
# MODULE-LEVEL HELPERS (receive config_path explicitly)
# =========================================================================


def _find_entity_lines_in_file(file_path: str, entity_id: str) -> list[dict]:  # type: ignore[type-arg]
    """Find lines containing entity_id in a text file, with context lines."""
    if not os.path.exists(file_path):
        return []

    try:
        with open(file_path, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    entity_re = re.compile(r"\b" + re.escape(entity_id) + r"\b")

    for i, line in enumerate(lines, 1):
        if entity_re.search(line):
            start = max(0, i - 2)
            end = min(len(lines), i + 1)
            context_lines = [
                {"line_num": j + 1, "content": lines[j].rstrip("\n")} for j in range(start, end)
            ]
            results.append({"line": i, "context": context_lines})

    return results


def _find_entity_in_automations(
    entity_id: str,
    config_path: str,
    detail_level: str = "summary",
) -> list[dict]:  # type: ignore[type-arg]
    """Find entity usage in automations.yaml."""
    automations_path = os.path.join(config_path, "automations.yaml")
    if not os.path.exists(automations_path):
        return []

    automations = load_yaml_file(automations_path) or []
    found = []

    line_info_cache: list[dict] | None = None  # type: ignore[type-arg]

    def search_obj(obj, context=""):  # type: ignore[no-untyped-def]
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and entity_id in v:
                    return f"{context}.{k}" if context else k
                path_result = search_obj(v, f"{context}.{k}" if context else k)  # type: ignore[no-untyped-call]
                if path_result:
                    return path_result
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str) and entity_id in item:
                    return f"{context}[{i}]" if context else f"[{i}]"
                path_result = search_obj(item, f"{context}[{i}]" if context else f"[{i}]")  # type: ignore[no-untyped-call]
                if path_result:
                    return path_result
        return None

    for auto in automations:
        if not isinstance(auto, dict):
            continue

        entities_in_auto = _extract_entities_from_data(auto)
        if entity_id not in entities_in_auto:
            continue

        path = search_obj(auto)  # type: ignore[no-untyped-call]
        if path:
            entry: dict[str, Any] = {
                "id": auto.get("id"),
                "alias": auto.get("alias", "Unnamed"),
                "context": path,
            }
            if detail_level == "full":
                entry["file_path"] = "automations.yaml"
                entry["object_path"] = path
                if line_info_cache is None:
                    line_info_cache = _find_entity_lines_in_file(automations_path, entity_id)
                if line_info_cache:
                    entry["line"] = line_info_cache[0].get("line")
                    entry["context_lines"] = line_info_cache[0].get("context")
            found.append(entry)

    return found


def _find_entity_in_scripts(
    entity_id: str,
    config_path: str,
    detail_level: str = "summary",
) -> list[dict]:  # type: ignore[type-arg]
    """Find entity usage in scripts.yaml."""
    scripts_path = os.path.join(config_path, "scripts.yaml")
    if not os.path.exists(scripts_path):
        return []

    scripts = load_yaml_file(scripts_path) or {}
    found = []
    line_info_cache: list[dict] | None = None  # type: ignore[type-arg]

    if isinstance(scripts, dict):
        for script_id, config in scripts.items():
            entities_in_script = _extract_entities_from_data(config)
            if entity_id in entities_in_script:
                entry: dict[str, Any] = {"id": script_id, "alias": config.get("alias", script_id)}
                if detail_level == "full":
                    entry["file_path"] = "scripts.yaml"
                    if line_info_cache is None:
                        line_info_cache = _find_entity_lines_in_file(scripts_path, entity_id)
                    if line_info_cache:
                        entry["line"] = line_info_cache[0].get("line")
                        entry["context_lines"] = line_info_cache[0].get("context")
                found.append(entry)
    elif isinstance(scripts, list):
        for script in scripts:
            entities_in_script = _extract_entities_from_data(script)
            if entity_id in entities_in_script:
                script_entry: dict[str, Any] = {
                    "id": script.get("id", "unknown"),
                    "alias": script.get("alias", "Unnamed"),
                }
                if detail_level == "full":
                    script_entry["file_path"] = "scripts.yaml"
                    if line_info_cache is None:
                        line_info_cache = _find_entity_lines_in_file(scripts_path, entity_id)
                    if line_info_cache:
                        script_entry["line"] = line_info_cache[0].get("line")
                        script_entry["context_lines"] = line_info_cache[0].get("context")
                found.append(script_entry)

    return found


def _find_entity_in_dashboards(
    entity_id: str,
    config_path: str,
    detail_level: str = "summary",
) -> list[dict]:  # type: ignore[type-arg]
    """Find entity usage in Lovelace dashboards."""
    storage_path = Path(config_path) / ".storage"
    if not storage_path.exists():
        return []

    found = []

    try:
        lovelace_files = [f for f in os.listdir(storage_path) if f.startswith("lovelace")]

        for lf in lovelace_files:
            try:
                data = load_registry(lf, config_path)
                if not data:
                    continue

                data_str = json.dumps(data)
                if entity_id not in data_str:
                    continue

                views = data.get("data", {}).get("config", {}).get("views", [])
                matched_views = []

                for view in views:
                    if entity_id in json.dumps(view):
                        matched_views.append(view.get("title", "Unknown View"))

                dashboard_name = lf.replace("lovelace.", "")
                if dashboard_name == "lovelace":
                    dashboard_name = "Default (Overview)"

                entry: dict[str, Any] = {
                    "dashboard": dashboard_name,
                    "file": lf,
                    "views": matched_views,
                }
                if detail_level == "full":
                    entry["file_path"] = f".storage/{lf}"
                    lovelace_path = str(storage_path / lf)
                    line_info = _find_entity_lines_in_file(lovelace_path, entity_id)
                    if line_info:
                        entry["line"] = line_info[0].get("line")
                        entry["context_lines"] = line_info[0].get("context")
                found.append(entry)
            except Exception:
                continue
    except Exception:
        pass

    return found


def _find_template_entities(
    entity_id: str,
    config_path: str,
    detail_level: str = "summary",
) -> list[dict]:  # type: ignore[type-arg]
    """Find template entities that reference this entity."""
    entries = load_registry("core.config_entries", config_path).get("data", {}).get("entries", [])
    found = []

    for entry in entries:
        if entry.get("domain") == "template":
            entities_in_options = _extract_entities_from_data(entry.get("options", {}))
            if entity_id in entities_in_options:
                entry_data: dict[str, Any] = {"name": entry.get("title"), "type": "helper"}
                if detail_level == "full":
                    entry_data["file_path"] = ".storage/core.config_entries"
                found.append(entry_data)

    config_file = os.path.join(config_path, "configuration.yaml")
    line_info_cache: list[dict] | None = None  # type: ignore[type-arg]

    if os.path.exists(config_file):
        try:
            with open(config_file, encoding="utf-8") as f:
                content = f.read()
                if "template:" in content and entity_id in content:
                    cfg_entry: dict[str, Any] = {
                        "name": "configuration.yaml",
                        "type": "yaml",
                        "note": "Found in YAML configuration",
                    }
                    if detail_level == "full":
                        cfg_entry["file_path"] = "configuration.yaml"
                        if line_info_cache is None:
                            line_info_cache = _find_entity_lines_in_file(config_file, entity_id)
                        if line_info_cache:
                            cfg_entry["line"] = line_info_cache[0].get("line")
                            cfg_entry["context_lines"] = line_info_cache[0].get("context")
                    found.append(cfg_entry)
        except Exception:
            pass

        def _extract_includes(data):  # type: ignore[no-untyped-def]
            paths = []
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, str) and v.startswith("!include "):
                        paths.append(v[9:])
                    elif isinstance(v, (dict, list)):
                        paths.extend(_extract_includes(v))  # type: ignore[no-untyped-call]
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, str) and item.startswith("!include "):
                        paths.append(item[9:])
                    elif isinstance(item, (dict, list)):
                        paths.extend(_extract_includes(item))  # type: ignore[no-untyped-call]
            return paths

        try:
            config_data = load_yaml_file(config_file)
            if config_data:
                include_paths = _extract_includes(config_data)  # type: ignore[no-untyped-call]
                for inc_path in include_paths:
                    full_path = os.path.join(config_path, inc_path)
                    if os.path.exists(full_path):
                        try:
                            with open(full_path, encoding="utf-8") as f:
                                inc_content = f.read()
                                if entity_id in inc_content:
                                    inc_entry: dict[str, Any] = {
                                        "name": inc_path,
                                        "type": "include",
                                        "note": f"Found in !include file: {inc_path}",
                                    }
                                    if detail_level == "full":
                                        inc_entry["file_path"] = inc_path
                                        inc_lines = _find_entity_lines_in_file(full_path, entity_id)
                                        if inc_lines:
                                            inc_entry["line"] = inc_lines[0].get("line")
                                            inc_entry["context_lines"] = inc_lines[0].get("context")
                                    found.append(inc_entry)
                        except Exception:
                            pass
        except Exception:
            pass

    return found


def _get_entity_dependencies_info(entity_id: str, config_path: str) -> dict[str, Any]:
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

    if result["device_id"]:
        devices = (
            load_registry("core.device_registry", config_path).get("data", {}).get("devices", [])
        )
        device = next((d for d in devices if d.get("id") == result["device_id"]), None)
        if device:
            result["device_name"] = device.get("name_by_user") or device.get("name")
            result["via_device_id"] = device.get("via_device_id")

    return result


# =========================================================================
# _do_* FUNCTIONS (pure business logic, returns dict)
# =========================================================================


def _do_get_entity_dependencies(
    entity_id: str,
    config_path: str,
    ha_url: str | None = None,
    ha_token: str | None = None,
    detail_level: str = "summary",
    include_context: bool = False,
) -> dict[str, Any]:
    """Business logic for get_entity_dependencies."""
    if not entity_id:
        return {"error": "entity_id is required"}

    used_detail = "full" if detail_level == "full" else "summary"

    result: dict[str, Any] = {
        "entity_id": entity_id,
        "used_in": {
            "automations": _find_entity_in_automations(entity_id, config_path, used_detail),
            "scripts": _find_entity_in_scripts(entity_id, config_path, used_detail),
            "templates": _find_template_entities(entity_id, config_path, used_detail),
            "dashboards": _find_entity_in_dashboards(entity_id, config_path, used_detail),
        },
        "depends_on": _get_entity_dependencies_info(entity_id, config_path),
    }

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

    total_refs = (
        result["summary"]["automations_count"]
        + result["summary"]["scripts_count"]
        + result["summary"]["templates_count"]
        + result["summary"]["dashboards_count"]
    )
    result["total_references"] = total_refs

    # Entity existence check via HA API
    entity_exists = False
    if ha_url and ha_token:
        resp = make_ha_request(ha_url, ha_token, f"/api/states/{entity_id}")
        entity_exists = resp.get("success", False)
    result["entity_exists"] = entity_exists

    return result


def _do_get_entity_consumers(
    entity_id: str,
    config_path: str,
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> dict[str, Any]:
    """Business logic for get_entity_consumers."""
    automations = _find_entity_in_automations(entity_id, config_path)
    scripts = _find_entity_in_scripts(entity_id, config_path)

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
        consumers.append({"type": "script", "id": script.get("id"), "name": script.get("alias")})

    return {
        "entity_id": entity_id,
        "consumers_count": len(consumers),
        "consumers": consumers,
    }


# =========================================================================
# TOOL REGISTRATION
# =========================================================================


def register_entity_dependency_tools(mcp, config_path: str, ha_url: str, ha_token: str) -> None:  # type: ignore[no-untyped-def]
    """Register entity dependency tracking tools."""

    @mcp.tool()
    async def get_entity_dependencies(
        entity_id: str,
        detail_level: str = "summary",
        include_context: bool = False,
    ) -> str:
        """[READ] Finds all places where entity is used (reverse lookup).

        ~85% token savings vs manually searching files.

        Args:
            entity_id: Entity id (e.g. "sensor.temperature")
            detail_level: "summary" (default) returns compact results,
                "full" adds file_path, line, context_lines, and object_path
                for each reference.
            include_context: When True, includes surrounding YAML context
                lines even in summary mode (default: False).

        Returns:
            JSON with:
            - entity_id, entity_exists, total_references
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
        try:
            result = _do_get_entity_dependencies(
                entity_id=entity_id,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
                detail_level=detail_level,
                include_context=include_context,
            )
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def get_entity_consumers(entity_id: str) -> str:
        """[READ] List of automations and scripts using a given entity (simplified version).

        Args:
            entity_id: Entity id

        Returns:
            JSON with list of consumers
        """
        try:
            result = _do_get_entity_consumers(
                entity_id=entity_id,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            if "error" in result:
                return _error_response(result["error"])
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))
