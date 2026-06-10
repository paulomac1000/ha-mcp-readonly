"""
Automation Management Tools
Tools for listing, searching, analyzing, and debugging Home Assistant automations.
"""

import json
import logging
import os
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import yaml

from tools.manifests import make_manifest, register_manifest
from tools.utils import (
    _build_history_url,
    _error_response,
    _success_response,
    load_registry,
    make_ha_request,
)
from tools.yaml_utils import HomeAssistantLoader

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"


# ========================================
# INTERNAL HELPERS
# ========================================


def _load_automations(config_path: str) -> list[dict]:  # type: ignore[type-arg]
    """Safely loads the automations.yaml file."""
    try:
        file_path = os.path.join(config_path, "automations.yaml")
        if not os.path.exists(file_path):
            return []
        with open(file_path, encoding="utf-8") as f:
            return cast(list[dict[str, Any]], yaml.load(f, Loader=HomeAssistantLoader) or [])  # nosec B506
    except Exception:
        return []


def _get_automation_by_id_or_alias(data: list[dict], identifier: str) -> dict | None:  # type: ignore[type-arg]
    """Finds automation by alias or id (case-insensitive)."""
    identifier_lower = identifier.lower()
    for item in data:
        if str(item.get("id")) == identifier or item.get("alias") == identifier:
            return item
    for item in data:
        if (
            str(item.get("id")).lower() == identifier_lower
            or item.get("alias", "").lower() == identifier_lower
        ):
            return item
    return None


def _extract_entities_recursive(data: Any, found: set[str]):  # type: ignore[no-untyped-def]
    """Recursively extracts entities from dictionary/list structures."""
    pattern = re.compile(
        r"\b(?:sensor|binary_sensor|light|switch|climate|cover|input_\w+|automation|script|person|device_tracker|"
        r"media_player|camera|lock|fan|vacuum|weather|sun|zone|timer|counter|number|select|button|scene)\.[a-z0-9_]+\b"
    )

    if isinstance(data, dict):
        for key, value in data.items():
            if key in ["entity_id", "service", "scene"]:
                if isinstance(value, str):
                    found.update(pattern.findall(value))
                elif isinstance(value, list):
                    for v in value:
                        if isinstance(v, str):
                            found.update(pattern.findall(v))

            if isinstance(value, str) and ("{{" in value or "{%" in value):
                found.update(pattern.findall(value))

            _extract_entities_recursive(value, found)

    elif isinstance(data, list):
        for item in data:
            _extract_entities_recursive(item, found)


def _extract_templates(data: Any, path: str = "") -> list[dict[str, str]]:
    """Extracts all templates from an automation."""
    templates = []

    if isinstance(data, dict):
        for key, value in data.items():
            new_path = f"{path}.{key}" if path else key
            if isinstance(value, str) and ("{{" in value or "{%" in value):
                templates.append({"path": new_path, "template": value})
            templates.extend(_extract_templates(value, new_path))
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            templates.extend(_extract_templates(item, f"{path}[{idx}]"))

    return templates


def _detect_reversal(prev_service: str, next_service: str) -> bool:
    """Return True if the two service calls reverse each other's state."""
    if not prev_service or not next_service:
        return False
    reversal_pairs = {
        ("turn_on", "turn_off"),
        ("turn_off", "turn_on"),
        ("open_cover", "close_cover"),
        ("close_cover", "open_cover"),
        ("lock", "unlock"),
        ("unlock", "lock"),
    }
    return (prev_service, next_service) in reversal_pairs


# ========================================
# INTERNAL BUSINESS LOGIC FUNCTIONS
# ========================================


def _find_deep_match_paths(item: dict[str, Any], search_term: str) -> list[str]:
    """Recursively search full item config for search_term, return JSON-path strings."""
    term = search_term.lower()
    found_paths: list[str] = []

    def _walk(obj: Any, path: str) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                cur_path = f"{path}/{key}" if path else key
                if isinstance(value, str) and term in value.lower():
                    found_paths.append(cur_path)
                elif isinstance(value, (dict, list)):
                    _walk(value, cur_path)
        elif isinstance(obj, list):
            for idx, value in enumerate(obj):
                cur_path = f"{path}[{idx}]"
                if isinstance(value, str) and term in value.lower():
                    found_paths.append(cur_path)
                elif isinstance(value, (dict, list)):
                    _walk(value, cur_path)

    _walk(item, "")
    return found_paths


def _do_search_automations(
    search_term: str | None = None,
    include_code: bool = False,
    mode: str | None = None,
    uses_blueprint: bool | None = None,
    deep: bool = False,
    category: str | None = None,
    include_entity_id: bool = False,
    config_path: str | None = None,
) -> dict[str, Any]:
    data = _load_automations(config_path)  # type: ignore[arg-type]
    results = []

    # Pre-load entity registry for entity_id resolution if needed
    entity_map: dict[str, str] = {}
    if include_entity_id and config_path:
        entity_entries = load_registry("core.entity_registry", config_path)
        if entity_entries:
            entities = entity_entries.get("data", {}).get("entities", [])
            for ent in entities:
                ent_eid = ent.get("entity_id", "")
                if ent_eid.startswith("automation."):
                    ent_uid = ent.get("unique_id", "")
                    if ent_uid:
                        entity_map[ent_uid] = ent_eid

    category_id: str | None = None
    if category and config_path:
        cat_registry = load_registry("core.category_registry", config_path)
        cat_entries = cat_registry.get("data", {}).get("categories", [])
        for cat_entry in cat_entries:
            if cat_entry.get("category_id") == category:
                category_id = category
                break
            if cat_entry.get("name", "").lower() == category.lower():
                category_id = cat_entry.get("category_id")
                break
        if (
            category_id
            and cat_entries is not None
            and not any(c.get("category_id") == category_id for c in cat_entries)
        ):
            category_id = None

    for item in data:
        if search_term:
            term = search_term.lower()
            text_corpus = f"{item.get('id', '')} {item.get('alias', '')} {item.get('description', '')}".lower()
            shallow_match = term in text_corpus

            if not shallow_match:
                if deep:
                    deep_paths = _find_deep_match_paths(item, term)
                    if not deep_paths:
                        continue
                else:
                    continue

        if mode and item.get("mode", "single") != mode:
            continue

        if uses_blueprint is not None:
            has_bp = "use_blueprint" in item
            if uses_blueprint != has_bp:
                continue

        auto_id = item.get("id", "")
        alias = item.get("alias", "Unnamed")

        if category_id:
            auto_unique_id = str(auto_id) if auto_id else ""
            auto_entity_id = f"automation.{re.sub(r'[^a-z0-9_]+', '_', str(auto_id or alias or '').lower()).strip('_')}"
            entity_entries = load_registry("core.entity_registry", config_path)
            entities = entity_entries.get("data", {}).get("entities", []) if entity_entries else []
            matched_categories: dict[str, str] | None = None
            for ent in entities:
                ent_eid = ent.get("entity_id", "")
                ent_uid = ent.get("unique_id", "")
                if (ent_uid and ent_uid == auto_unique_id) or ent_eid == auto_entity_id:
                    matched_categories = ent.get("categories", {})
                    break
            if not (matched_categories and matched_categories.get("automation") == category_id):
                continue

        res = {
            "alias": alias,
            "description": item.get("description"),
            "mode": item.get("mode", "single"),
            "uses_blueprint": "use_blueprint" in item,
            "blueprint_path": item.get("use_blueprint", {}).get("path")
            if "use_blueprint" in item
            else None,
            "trigger_count": len(item.get("trigger", []))
            if isinstance(item.get("trigger"), list)
            else 1
            if item.get("trigger")
            else 0,
            "action_count": len(item.get("action", []))
            if isinstance(item.get("action"), list)
            else 1
            if item.get("action")
            else 0,
        }

        if deep and search_term:
            deep_paths = _find_deep_match_paths(item, search_term)
            if deep_paths:
                res["match_paths"] = deep_paths

        if include_code:
            clean_item = item.copy()
            clean_item.pop("id", None)
            res["code"] = yaml.dump(clean_item, sort_keys=False, allow_unicode=True)

        if include_entity_id:
            auto_unique_id = str(auto_id) if auto_id else ""
            res["entity_id"] = entity_map.get(auto_unique_id, None)

        results.append(res)

    def _sort_key(r):  # type: ignore[no-untyped-def]
        is_disabled = 1 if r.get("disabled_by") else 0
        return (is_disabled, r.get("alias", "").lower())

    results.sort(key=_sort_key)

    return {
        "success": True,
        "total_automations": len(data),
        "matched_count": len(results),
        "results": results,
    }


def _do_list_automations(config_path: str, detail_level: str = "full") -> dict[str, Any]:
    data = _load_automations(config_path)
    if detail_level not in ("summary", "full"):
        return {
            "success": False,
            "error": f"Invalid detail_level '{detail_level}'. Must be 'summary' or 'full'.",
        }
    summary = [
        {
            "id": item.get("id"),
            "alias": item.get("alias", "No Name"),
            "mode": item.get("mode", "single"),
            "uses_blueprint": "use_blueprint" in item,
            "blueprint_path": item.get("use_blueprint", {}).get("path")
            if "use_blueprint" in item
            else None,
            **(
                {}
                if detail_level == "summary"
                else {
                    "description": item.get("description", ""),
                    "trigger_count": len(item.get("trigger", []))
                    if isinstance(item.get("trigger"), list)
                    else 1
                    if item.get("trigger")
                    else 0,
                    "action_count": len(item.get("action", []))
                    if isinstance(item.get("action"), list)
                    else 1
                    if item.get("action")
                    else 0,
                }
            ),
        }
        for item in data
    ]

    return {"success": True, "total_count": len(summary), "automations": summary}


def _do_get_automation_entity_id(
    identifier: str,
    config_path: str,
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> dict[str, Any]:
    """Resolve automation alias to entity_id via entity registry.

    Searches the HA entity registry for automation.* entities matching by
    alias (friendly_name). Finds BOTH YAML and UI-created automations.

    Args:
        identifier: Automation alias or partial name to search for.
        config_path: Path to HA config directory.
        ha_url: Optional HA URL (for future use).
        ha_token: Optional HA token (for future use).

    Returns:
        Dict with alias, entity_id, unique_id on match, or error on no match.
    """
    if not identifier or not isinstance(identifier, str) or not identifier.strip():
        return {
            "success": False,
            "error": "identifier is required and must be a non-empty string",
        }

    identifier = identifier.strip()

    entity_entries = load_registry("core.entity_registry", config_path)
    entities = entity_entries.get("data", {}).get("entities", []) if entity_entries else []

    exact_match: dict[str, Any] | None = None
    partial_matches: list[dict[str, Any]] = []

    for entity in entities:
        entity_id: str = entity.get("entity_id", "")
        if not entity_id.startswith("automation."):
            continue

        alias: str = entity.get("name") or entity.get("original_name", entity_id)
        unique_id: str = entity.get("unique_id", "")

        if alias and identifier.lower() == alias.lower():
            exact_match = {
                "alias": alias,
                "entity_id": entity_id,
                "unique_id": unique_id,
            }
            break

        if alias and identifier.lower() in alias.lower():
            partial_matches.append(
                {
                    "alias": alias,
                    "entity_id": entity_id,
                    "unique_id": unique_id,
                }
            )

    if exact_match:
        return {
            "success": True,
            **exact_match,
        }

    if partial_matches:
        first = partial_matches[0]
        return {
            "success": True,
            "alias": first["alias"],
            "entity_id": first["entity_id"],
            "unique_id": first["unique_id"],
            "matches_count": len(partial_matches),
        }

    return {
        "success": False,
        "error": f"No automation found matching '{identifier}'",
    }


def _do_get_automation_code(automation_id: str, config_path: str) -> dict[str, Any]:
    if not automation_id or not isinstance(automation_id, str) or not automation_id.strip():
        return {
            "success": False,
            "error": "automation_id is required and must be a non-empty string",
        }

    data = _load_automations(config_path)
    item = _get_automation_by_id_or_alias(data, automation_id)

    if not item:
        return {"success": False, "error": f"Automation '{automation_id}' not found"}

    clean_item = item.copy()
    automation_id_value = clean_item.pop("id", None)

    return {
        "success": True,
        "alias": item.get("alias"),
        "automation_id": automation_id_value,
        "code": yaml.dump(
            clean_item,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ),
    }


def _do_get_automation_dependencies(automation_id: str, config_path: str) -> dict[str, Any]:
    data = _load_automations(config_path)
    item = _get_automation_by_id_or_alias(data, automation_id)

    if not item:
        return {"success": False, "error": "Automation not found"}

    entities = set()  # type: ignore[var-annotated]
    _extract_entities_recursive(item, entities)

    scripts = sorted([e for e in entities if e.startswith("script.")])
    scenes = sorted([e for e in entities if e.startswith("scene.")])
    pure_entities = sorted(
        [e for e in entities if not e.startswith(("script.", "scene.", "automation."))]
    )

    return {
        "success": True,
        "automation": item.get("alias"),
        "uses_blueprint": item.get("use_blueprint", {}).get("path"),
        "dependencies": {
            "scripts": scripts,
            "scenes": scenes,
            "entities_count": len(pure_entities),
            "entities": pure_entities[:50]
            if len(pure_entities) <= 50
            else pure_entities[:50] + [f"... and {len(pure_entities) - 50} more"],
        },
    }


def _do_search_automations_by_entity(entity_id: str, config_path: str) -> dict[str, Any]:
    data = _load_automations(config_path)
    results = []

    for item in data:
        item_str = str(item)
        if entity_id not in item_str:
            continue

        usage = []
        if entity_id in str(item.get("trigger", [])):
            usage.append("trigger")
        if entity_id in str(item.get("condition", [])):
            usage.append("condition")
        if entity_id in str(item.get("action", [])):
            usage.append("action")
        if "use_blueprint" in item and entity_id in str(item["use_blueprint"].get("input", {})):
            usage.append("blueprint_input")

        if usage:
            results.append(
                {
                    "alias": item.get("alias", "Unnamed"),
                    "id": item.get("id"),
                    "mode": item.get("mode", "single"),
                    "usage_type": usage,
                }
            )

    return {
        "success": True,
        "entity_id": entity_id,
        "found_in_count": len(results),
        "automations": results,
    }


def _do_get_automation_conflicts(entity_id: str, config_path: str) -> dict[str, Any]:
    data = _load_automations(config_path)
    writers = []
    readers = []

    for item in data:
        item_str = str(item)
        if entity_id not in item_str:
            continue

        alias = item.get("alias", "Unnamed")
        mode = item.get("mode", "single")

        if entity_id in str(item.get("action", [])):
            writers.append({"alias": alias, "mode": mode})

        if entity_id in str(item.get("trigger", [])):
            readers.append({"alias": alias, "mode": mode})

    potential_loop = len(writers) > 0 and len(readers) > 0
    race_condition = len(writers) > 1

    return {
        "success": True,
        "entity": entity_id,
        "conflict_analysis": {
            "race_condition_risk": race_condition,
            "feedback_loop_risk": potential_loop,
            "race_description": "Multiple automations control the same entity - may cause conflicts"
            if race_condition
            else None,
            "loop_description": "Automation triggers on entity it also controls - may cause infinite loop"
            if potential_loop
            else None,
        },
        "controlling_automations": writers,
        "triggering_automations": readers,
        "recommendations": [
            "Use mode: restart for light/motion automations to prevent conflicts",
            "Use mode: single for notifications to prevent spam",
        ]
        if race_condition or potential_loop
        else ["No conflicts detected"],
    }


def _analyze_choose_branches(actions: list[dict]) -> dict:
    """Analyze choose blocks in automation actions.

    Extracts structured metadata from each choose branch: condition types,
    action count, and whether a default branch exists.

    Args:
        actions: List of action dicts from an automation.

    Returns:
        Dict with choose_count and branches list.
    """
    choose_count = 0
    branches: list[dict] = []
    has_default = False

    for action in actions:
        if not isinstance(action, dict) or "choose" not in action:
            continue

        act_default = action.get("default")
        choose_block = action["choose"]
        if act_default is not None:
            has_default = True

        if not isinstance(choose_block, list):
            continue

        for branch_idx, choice in enumerate(choose_block):
            if not isinstance(choice, dict):
                continue

            choose_count += 1

            raw_conditions = choice.get("conditions", [])
            if isinstance(raw_conditions, dict):
                raw_conditions = [raw_conditions]
            elif not isinstance(raw_conditions, list):
                raw_conditions = []

            condition_types: list[str] = []
            for cond in raw_conditions:
                if isinstance(cond, str):
                    condition_types.append(f"alias:{cond}")
                elif isinstance(cond, dict):
                    cond_type = cond.get("condition", "unknown")
                    if cond_type == "trigger":
                        tid = cond.get("id", "?")
                        if isinstance(tid, list):
                            tid = ",".join(tid)
                        condition_types.append(f"trigger:{tid}")
                    elif cond_type == "state":
                        condition_types.append(f"state:{cond.get('entity_id', '?')}")
                    elif cond_type == "numeric_state":
                        condition_types.append(f"numeric_state:{cond.get('entity_id', '?')}")
                    elif cond_type == "time":
                        condition_types.append(
                            f"time:{cond.get('after', cond.get('before', cond.get('weekday', '?')))}"
                        )
                    elif cond_type == "sun":
                        condition_types.append(f"sun:{cond.get('after', cond.get('before', '?'))}")
                    elif cond_type == "template":
                        vt = cond.get("value_template", "")
                        condition_types.append(f"template:{vt[:40]}" if vt else "template:?")
                    elif cond_type == "zone":
                        condition_types.append(f"zone:{cond.get('entity_id', '?')}")
                    else:
                        condition_types.append(cond_type)

            raw_sequence = choice.get("sequence", [])
            if not isinstance(raw_sequence, list):
                raw_sequence = [raw_sequence] if raw_sequence else []

            branches.append(
                {
                    "index": branch_idx,
                    "conditions": condition_types,
                    "actions_count": len(raw_sequence),
                    "has_default": False,
                }
            )

    return {
        "choose_count": choose_count,
        "has_default": has_default,
        "branches": branches,
    }


def _do_diagnose_automation(
    automation_id: str,
    detail_level: str = "summary",
    config_path: str | None = None,
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> dict[str, Any]:
    data = _load_automations(config_path)  # type: ignore[arg-type]
    automation = _get_automation_by_id_or_alias(data, automation_id)

    if not automation:
        return {"success": False, "error": f"Automation '{automation_id}' not found"}

    result = {
        "success": True,
        "automation_info": {
            "alias": automation.get("alias"),
            "description": automation.get("description", ""),
            "mode": automation.get("mode", "single"),
            "max_exceeded": automation.get("max_exceeded"),
            "uses_blueprint": "use_blueprint" in automation,
        },
        "issues": [],
        "recommendations": [],
        "statistics": {
            "total_entities": 0,
            "missing_entities": 0,
            "unavailable_entities": 0,
            "total_templates": 0,
            "invalid_templates": 0,
            "total_triggers": 0,
            "total_conditions": 0,
            "total_actions": 0,
        },
    }

    if detail_level in ["summary", "full"]:
        code_item = automation.copy()
        code_item.pop("id", None)
        result["automation_info"]["full_code"] = yaml.dump(  # type: ignore[index]
            code_item,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

    if detail_level == "full":
        result["blueprint_info"] = None
        result["entity_validation"] = {}
        result["script_validation"] = {}
        result["template_validation"] = []
        result["trigger_analysis"] = []
        result["condition_analysis"] = []
        result["action_analysis"] = []

    if detail_level == "full" and "use_blueprint" in automation:
        blueprint_path = automation["use_blueprint"].get("path")
        if blueprint_path:
            blueprint_file = os.path.join(config_path, "blueprints", blueprint_path)  # type: ignore[arg-type]
            if os.path.exists(blueprint_file):
                try:
                    with open(blueprint_file, encoding="utf-8") as f:
                        blueprint_data = yaml.load(f, Loader=HomeAssistantLoader)  # nosec B506
                        result["blueprint_info"] = {
                            "path": blueprint_path,
                            "name": blueprint_data.get("blueprint", {}).get("name"),
                            "description": blueprint_data.get("blueprint", {}).get("description"),
                            "domain": blueprint_data.get("blueprint", {}).get("domain"),
                            "inputs": blueprint_data.get("blueprint", {}).get("input", {}),
                            "user_inputs": automation["use_blueprint"].get("input", {}),
                            "full_code": yaml.dump(
                                blueprint_data,
                                allow_unicode=True,
                                default_flow_style=False,
                            ),
                        }
                except Exception as e:
                    result["issues"].append(  # type: ignore[attr-defined]
                        {
                            "severity": "error",
                            "type": "blueprint_load_error",
                            "message": f"Failed to load blueprint: {str(e)}",
                        }
                    )
            else:
                result["issues"].append(  # type: ignore[attr-defined]
                    {
                        "severity": "error",
                        "type": "blueprint_not_found",
                        "message": f"Blueprint file not found: {blueprint_file}",
                    }
                )

    entities = set()  # type: ignore[var-annotated]
    scripts = set()
    scenes = set()
    _extract_entities_recursive(automation, entities)

    for e in entities:  # type: ignore[misc]
        if e.startswith("script."):
            scripts.add(e.replace("script.", ""))
        elif e.startswith("scene."):
            scenes.add(e.replace("scene.", ""))

    result["statistics"]["total_entities"] = len(entities)  # type: ignore[index]

    templates = _extract_templates(automation)
    result["statistics"]["total_templates"] = len(templates)  # type: ignore[index]

    if ha_url and ha_token:
        all_states_result = make_ha_request(ha_url, ha_token, "/api/states")

        if all_states_result["success"]:
            states_dict = {s["entity_id"]: s for s in all_states_result["data"]}

            entity_issues = []
            for entity_id in sorted(entities):
                if entity_id.startswith(("script.", "scene.")):
                    continue

                if entity_id in states_dict:
                    state_data = states_dict[entity_id]

                    if detail_level == "full":
                        result["entity_validation"][entity_id] = {  # type: ignore[index]
                            "exists": True,
                            "state": state_data["state"],
                            "friendly_name": state_data.get("attributes", {}).get(
                                "friendly_name", ""
                            ),
                            "device_class": state_data.get("attributes", {}).get("device_class"),
                            "last_changed": state_data.get("last_changed"),
                            "last_updated": state_data.get("last_updated"),
                        }

                    if state_data["state"] == "unavailable":
                        result["statistics"]["unavailable_entities"] += 1  # type: ignore[index]
                        entity_issues.append(
                            {
                                "severity": "warning",
                                "type": "entity_unavailable",
                                "message": f"Entity {entity_id} is unavailable",
                                "entity_id": entity_id,
                            }
                        )
                    elif state_data["state"] == "unknown":
                        result["statistics"]["unavailable_entities"] += 1  # type: ignore[index]
                        entity_issues.append(
                            {
                                "severity": "warning",
                                "type": "entity_unknown",
                                "message": f"Entity {entity_id} has unknown state",
                                "entity_id": entity_id,
                            }
                        )
                else:
                    result["statistics"]["missing_entities"] += 1  # type: ignore[index]

                    if detail_level == "full":
                        result["entity_validation"][entity_id] = {  # type: ignore[index]
                            "exists": False,
                            "error": "Entity not found in HA",
                        }

                    entity_issues.append(
                        {
                            "severity": "error",
                            "type": "entity_not_found",
                            "message": f"Entity {entity_id} not found",
                            "entity_id": entity_id,
                        }
                    )

            if detail_level in ["minimal", "summary"]:
                result["issues"].extend(entity_issues[:10])  # type: ignore[attr-defined]
            else:
                result["issues"].extend(entity_issues)  # type: ignore[attr-defined]
        else:
            result["issues"].append(  # type: ignore[attr-defined]
                {
                    "severity": "warning",
                    "type": "validation_error",
                    "message": "Failed to fetch entity states from HA API",
                }
            )
    else:
        result["issues"].append(  # type: ignore[attr-defined]
            {
                "severity": "info",
                "type": "validation_skipped",
                "message": "Entity validation skipped - HA API not configured",
            }
        )

    if detail_level == "full" and scripts:
        scripts_file = os.path.join(config_path, "scripts.yaml")  # type: ignore[arg-type]
        if os.path.exists(scripts_file):
            try:
                with open(scripts_file, encoding="utf-8") as f:
                    scripts_data = yaml.load(f, Loader=HomeAssistantLoader)  # nosec B506 or {}

                for script_id in scripts:
                    if script_id in scripts_data:
                        result["script_validation"][script_id] = {  # type: ignore[index]
                            "exists": True,
                            "alias": scripts_data[script_id].get("alias", ""),
                            "code": yaml.dump(
                                {script_id: scripts_data[script_id]},
                                allow_unicode=True,
                                default_flow_style=False,
                            ),
                        }
                    else:
                        result["script_validation"][script_id] = {"exists": False}  # type: ignore[index]
                        result["issues"].append(  # type: ignore[attr-defined]
                            {
                                "severity": "error",
                                "type": "script_not_found",
                                "message": f"Script {script_id} not found in scripts.yaml",
                                "script_id": script_id,
                            }
                        )
            except Exception as e:
                result["issues"].append(  # type: ignore[attr-defined]
                    {
                        "severity": "error",
                        "type": "scripts_load_error",
                        "message": f"Failed to load scripts.yaml: {str(e)}",
                    }
                )

    if detail_level == "full" and scenes:
        scenes_file = os.path.join(config_path, "scenes.yaml")  # type: ignore[arg-type]
        if os.path.exists(scenes_file):
            try:
                with open(scenes_file, encoding="utf-8") as f:
                    scenes_data = yaml.load(f, Loader=HomeAssistantLoader)  # nosec B506 or []

                scene_ids = {scene.get("id"): scene for scene in scenes_data}

                for scene_id in scenes:
                    if scene_id in scene_ids:
                        result["script_validation"][f"scene.{scene_id}"] = {  # type: ignore[index]
                            "exists": True,
                            "type": "scene",
                            "name": scene_ids[scene_id].get("name", ""),
                        }
                    else:
                        result["issues"].append(  # type: ignore[attr-defined]
                            {
                                "severity": "warning",
                                "type": "scene_not_found",
                                "message": f"Scene {scene_id} not found in scenes.yaml",
                                "scene_id": scene_id,
                            }
                        )
            except Exception as e:
                result["issues"].append(  # type: ignore[attr-defined]
                    {
                        "severity": "warning",
                        "type": "scenes_load_error",
                        "message": f"Failed to load scenes.yaml: {str(e)}",
                    }
                )

    if ha_url and ha_token and templates:
        max_templates = 15 if detail_level == "full" else 5

        for template_info in templates[:max_templates]:
            template_result = make_ha_request(
                ha_url,
                ha_token,
                "/api/template",
                method="POST",
                data={"template": template_info["template"]},
            )

            is_valid = template_result["success"]
            if not is_valid:
                result["statistics"]["invalid_templates"] += 1  # type: ignore[index]

            if detail_level == "full":
                result["template_validation"].append(  # type: ignore[attr-defined]
                    {
                        "path": template_info["path"],
                        "template": template_info["template"][:200],
                        "valid": is_valid,
                        "result": str(template_result.get("data"))[:100] if is_valid else None,
                        "error": template_result.get("error") if not is_valid else None,
                    }
                )

            if not is_valid:
                result["issues"].append(  # type: ignore[attr-defined]
                    {
                        "severity": "error",
                        "type": "template_error",
                        "message": f"Template error at {template_info['path']}: {template_result.get('error')}",
                        "path": template_info["path"],
                        "template": template_info["template"][:100],
                    }
                )

    triggers = automation.get("trigger", [])
    if triggers and not isinstance(triggers, list):
        triggers = [triggers]

    result["statistics"]["total_triggers"] = len(triggers)  # type: ignore[index]

    if detail_level == "full":
        for idx, trigger in enumerate(triggers):
            analysis = {
                "index": idx,
                "platform": trigger.get("platform"),
                "config": trigger,
                "issues": [],
            }

            platform = trigger.get("platform")
            if platform == "state":
                if "entity_id" not in trigger:
                    issue = f"Trigger {idx}: 'state' platform missing 'entity_id'"
                    analysis["issues"].append(issue)
                    result["issues"].append(  # type: ignore[attr-defined]
                        {
                            "severity": "error",
                            "type": "trigger_config_error",
                            "message": issue,
                        }
                    )
            elif platform == "time":
                if "at" not in trigger:
                    issue = f"Trigger {idx}: 'time' platform missing 'at'"
                    analysis["issues"].append(issue)
                    result["issues"].append(  # type: ignore[attr-defined]
                        {
                            "severity": "error",
                            "type": "trigger_config_error",
                            "message": issue,
                        }
                    )
            elif platform == "numeric_state":
                if "entity_id" not in trigger:
                    issue = f"Trigger {idx}: 'numeric_state' missing 'entity_id'"
                    analysis["issues"].append(issue)
                    result["issues"].append(  # type: ignore[attr-defined]
                        {
                            "severity": "error",
                            "type": "trigger_config_error",
                            "message": issue,
                        }
                    )
                if "above" not in trigger and "below" not in trigger:
                    issue = f"Trigger {idx}: 'numeric_state' missing 'above' or 'below'"
                    analysis["issues"].append(issue)
                    result["issues"].append(  # type: ignore[attr-defined]
                        {
                            "severity": "error",
                            "type": "trigger_config_error",
                            "message": issue,
                        }
                    )
            elif platform == "template":
                if "value_template" not in trigger:
                    issue = f"Trigger {idx}: 'template' platform missing 'value_template'"
                    analysis["issues"].append(issue)
                    result["issues"].append(  # type: ignore[attr-defined]
                        {
                            "severity": "error",
                            "type": "trigger_config_error",
                            "message": issue,
                        }
                    )

            result["trigger_analysis"].append(analysis)  # type: ignore[attr-defined]

    has_ha_start = any(
        t.get("platform") == "homeassistant" and t.get("event") == "start" for t in triggers
    )
    if not has_ha_start and len(triggers) > 0:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "low",
                "message": "Consider adding 'homeassistant: start' trigger for resilience",
                "reason": "Ensures automation initializes correctly after HA restart",
            }
        )

    conditions = automation.get("condition", [])
    if conditions and not isinstance(conditions, list):
        conditions = [conditions]

    result["statistics"]["total_conditions"] = len(conditions)  # type: ignore[index]

    if detail_level == "full":
        for idx, condition in enumerate(conditions):
            analysis = {
                "index": idx,
                "type": condition.get("condition"),
                "config": condition,
                "issues": [],
            }

            cond_type = condition.get("condition")
            if cond_type == "state" and "entity_id" not in condition:
                issue = f"Condition {idx}: 'state' condition missing 'entity_id'"
                analysis["issues"].append(issue)
                result["issues"].append(  # type: ignore[attr-defined]
                    {
                        "severity": "error",
                        "type": "condition_config_error",
                        "message": issue,
                    }
                )
            elif cond_type == "numeric_state" and "entity_id" not in condition:
                issue = f"Condition {idx}: 'numeric_state' condition missing 'entity_id'"
                analysis["issues"].append(issue)
                result["issues"].append(  # type: ignore[attr-defined]
                    {
                        "severity": "error",
                        "type": "condition_config_error",
                        "message": issue,
                    }
                )

            result["condition_analysis"].append(analysis)  # type: ignore[attr-defined]

    actions = automation.get("action", [])
    if actions and not isinstance(actions, list):
        actions = [actions]

    result["statistics"]["total_actions"] = len(actions)  # type: ignore[index]

    has_variables = False
    variables_index = -1

    if detail_level == "full":
        for idx, action in enumerate(actions):
            action_type = "unknown"
            if "variables" in action:
                action_type = "variables"
                has_variables = True
                variables_index = idx
            elif "service" in action:
                action_type = "service_call"
            elif "choose" in action:
                action_type = "choose"
            elif "wait_template" in action:
                action_type = "wait"
            elif "delay" in action:
                action_type = "delay"
            elif "repeat" in action:
                action_type = "repeat"
            elif "if" in action:
                action_type = "if"

            analysis = {
                "index": idx,
                "type": action_type,
                "service": action.get("service"),
                "config": action,
                "issues": [],
            }

            if action_type == "service_call":
                service = action.get("service")
                if not service:
                    issue = f"Action {idx}: service call missing 'service' field"
                    analysis["issues"].append(issue)
                    result["issues"].append(  # type: ignore[attr-defined]
                        {
                            "severity": "error",
                            "type": "action_config_error",
                            "message": issue,
                        }
                    )

            result["action_analysis"].append(analysis)  # type: ignore[attr-defined]

        # Analyze choose blocks (full detail only)
        choose_analysis = _analyze_choose_branches(actions)
        if choose_analysis["choose_count"] > 0:
            result["choose_analysis"] = choose_analysis
    else:
        for idx, action in enumerate(actions):
            if "variables" in action:
                has_variables = True
                variables_index = idx
                break

    _STATE_CHANGING_SERVICES = {
        "turn_on",
        "turn_off",
        "toggle",
        "open_cover",
        "close_cover",
        "lock",
        "unlock",
        "set_temperature",
        "set_hvac_mode",
        "set_fan_mode",
        "set_preset_mode",
        "reload",
    }
    for idx, action in enumerate(actions):
        if "delay" in action and idx > 0 and idx + 1 < len(actions):
            prev_action = actions[idx - 1]
            next_action = actions[idx + 1]
            prev_svc = (
                (prev_action.get("service") or "").split(".")[-1]
                if "service" in prev_action
                else ""
            )
            next_svc = (
                (next_action.get("service") or "").split(".")[-1]
                if "service" in next_action
                else ""
            )
            _prev_target = prev_action.get("target", prev_action.get("entity_id", ""))
            _next_target = next_action.get("target", next_action.get("entity_id", ""))
            prev_changes_state = prev_svc in _STATE_CHANGING_SERVICES
            next_changes_state = next_svc in _STATE_CHANGING_SERVICES
            if prev_changes_state and next_changes_state:
                _prev_domain = (prev_action.get("service") or "").split(".")[0]
                _next_domain = (next_action.get("service") or "").split(".")[0]
                reversing = _detect_reversal(prev_svc, next_svc)
                result["issues"].append(  # type: ignore[attr-defined]
                    {
                        "severity": "error",
                        "type": "fragile_delay_pattern",
                        "message": (
                            f"Action {idx}: delay between state-changing calls "
                            f"({prev_action.get('service')} at {idx - 1} and "
                            f"{next_action.get('service')} at {idx + 1}) — "
                            f"fragile pattern. Use timer helpers instead."
                        ),
                        "detail": {
                            "delay_action_index": idx,
                            "previous_action": {
                                "index": idx - 1,
                                "service": prev_action.get("service"),
                            },
                            "next_action": {
                                "index": idx + 1,
                                "service": next_action.get("service"),
                            },
                            "reverses_state": reversing,
                        },
                        "fix": "Replace 'delay' with a timer helper (timer or schedule). "
                        "Start the timer in the first action, trigger the second "
                        "action on timer.finished event via a separate automation.",
                    }
                )

    if has_variables and variables_index > 0:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "medium",
                "message": "Move 'variables' block to the beginning of actions",
                "reason": "Best practice: define all variables before using them",
            }
        )

    if not automation.get("mode"):
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "medium",
                "message": "Add 'mode' parameter (single/restart/queued/parallel)",
                "reason": "Explicit mode prevents unexpected behavior with multiple triggers",
            }
        )

    if len(triggers) > 3:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "low",
                "message": "Consider splitting into separate automations",
                "reason": f"Automation has {len(triggers)} triggers - simpler automations are easier to maintain",
            }
        )

    if result["statistics"]["missing_entities"] > 0:  # type: ignore[index]
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "high",
                "message": f"Fix {result['statistics']['missing_entities']} missing entities",  # type: ignore[index]
                "reason": "Missing entities will cause automation failures",
            }
        )

    if result["statistics"]["unavailable_entities"] > 0:  # type: ignore[index]
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "medium",
                "message": f"Fix {result['statistics']['unavailable_entities']} unavailable/unknown entities",  # type: ignore[index]
                "reason": "Unavailable entities may cause unexpected behavior",
            }
        )

    if result["statistics"]["invalid_templates"] > 0:  # type: ignore[index]
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "high",
                "message": f"Fix {result['statistics']['invalid_templates']} invalid templates",  # type: ignore[index]
                "reason": "Template errors will prevent automation from working",
            }
        )

    if len(templates) > 5 and not has_variables:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "medium",
                "message": "Consider moving complex logic to 'variables' block",
                "reason": "Improves readability and performance",
            }
        )

    if automation.get("mode") == "single" and len(triggers) > 1:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "low",
                "message": "Multiple triggers with 'single' mode - consider 'restart' or 'queued'",
                "reason": "Single mode may skip executions if automation is already running",
            }
        )

    has_notifications = any(
        (action.get("service") or "").startswith("notify.") for action in actions
    )
    if has_notifications and automation.get("mode") != "single":
        result["recommendations"].append(  # type: ignore[attr-defined]
            {
                "priority": "low",
                "message": "Notification automations should use mode: single",
                "reason": "Prevents notification spam",
            }
        )

    if not result["recommendations"]:
        result["recommendations"].append(  # type: ignore[attr-defined]
            {"priority": "info", "message": "Automation looks healthy!"}
        )

    priority_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    result["recommendations"].sort(key=lambda x: priority_order.get(x.get("priority", "info"), 3))  # type: ignore[attr-defined]

    severity_order = {"error": 0, "warning": 1, "info": 2}
    result["issues"].sort(key=lambda x: severity_order.get(x.get("severity", "info"), 2))  # type: ignore[attr-defined]

    return result


def _do_get_automation_usage_stats(
    automation_id: str,
    hours_back: int = 24,
    config_path: str | None = None,
    ha_url: str | None = None,
    ha_token: str | None = None,
    detail_level: str = "summary",
) -> dict[str, Any]:
    if not ha_url or not ha_token:
        return {"success": False, "error": "HA API not configured (ha_url/ha_token missing)"}

    if detail_level not in ("summary", "full"):
        return {
            "success": False,
            "error": f"Invalid detail_level '{detail_level}'. Must be 'summary' or 'full'.",
        }

    data = _load_automations(config_path)  # type: ignore[arg-type]
    automation = _get_automation_by_id_or_alias(data, automation_id)
    from_api = False

    if not automation:
        states_result = make_ha_request(ha_url, ha_token, "/api/states")
        if states_result.get("success"):
            for state in states_result.get("data", []):
                eid = state.get("entity_id", "")
                if eid.startswith("automation."):
                    attrs = state.get("attributes", {})
                    state_alias = attrs.get("friendly_name", "")
                    if (
                        state_alias.lower() == automation_id.lower()
                        or eid == f"automation.{automation_id}"
                        or automation_id.lower() in state_alias.lower()
                    ):
                        entity_id = eid
                        ha_state = state
                        from_api = True
                        break

        if not from_api:
            return {
                "success": False,
                "error": f"Automation '{automation_id}' not found in automations.yaml or HA states",
            }

    if not from_api:
        auto_id = automation.get("id") or automation.get("alias") or "no_id"  # type: ignore[union-attr]
        slug = re.sub(r"[^a-z0-9_]+", "_", str(auto_id).lower()).strip("_")
        entity_id = f"automation.{slug}"

        state_result = make_ha_request(ha_url, ha_token, f"/api/states/{entity_id}")
        ha_state = state_result["data"] if state_result["success"] else None

    entities = set()  # type: ignore[var-annotated]
    if not from_api and automation:
        _extract_entities_recursive(automation, entities)

    missing_entities = []
    if entities:
        all_states_result = make_ha_request(ha_url, ha_token, "/api/states")
        if all_states_result["success"]:
            states_dict = {s["entity_id"]: s for s in all_states_result["data"]}
            for eid in sorted(entities):
                if eid not in states_dict:
                    missing_entities.append(eid)

    start_dt = datetime.now(UTC) - timedelta(hours=hours_back)
    start_time = start_dt.isoformat()
    history_result = make_ha_request(
        ha_url,
        ha_token,
        _build_history_url(start_dt, entity_id=entity_id, minimal=False),
    )

    last_run = None
    run_count = 0

    if history_result["success"] and history_result["data"]:
        try:
            series = history_result["data"][0] if history_result["data"] else []
        except (TypeError, IndexError):
            series = []

        prev_state = None
        for point in series:
            state_val = point.get("state")
            last_changed = point.get("last_changed")
            if (
                prev_state is not None
                and prev_state in ("off", "idle")
                and state_val in ("on", "triggered")
            ):
                run_count += 1
                last_run = last_changed or last_run
            prev_state = state_val

        if run_count == 0 and series:
            for point in reversed(series):
                if point.get("state") in ("on", "triggered"):
                    last_run = point.get("last_changed") or point.get("last_updated")
                    break

    last_triggered_attr = None
    is_enabled = None
    current_state = None
    if ha_state:
        attrs = ha_state.get("attributes", {})
        current_state = ha_state.get("state")
        last_triggered_attr = attrs.get("last_triggered")
        is_enabled = current_state != "off"

        def _parse_dt(v):  # type: ignore[no-untyped-def]
            if not v:
                return None
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            except Exception:
                return None

        dt_hist = _parse_dt(last_run)  # type: ignore[no-untyped-call]
        dt_attr = _parse_dt(last_triggered_attr)  # type: ignore[no-untyped-call]

        if dt_attr and (not dt_hist or dt_attr > dt_hist):
            last_run = last_triggered_attr

    is_working = False
    if ha_state:
        recently_triggered = False
        if last_run:
            try:
                lr_dt = datetime.fromisoformat(str(last_run).replace("Z", "+00:00"))
                if lr_dt >= datetime.now(lr_dt.tzinfo) - timedelta(hours=hours_back):
                    recently_triggered = True
            except Exception:
                pass

        is_working = bool(is_enabled and (run_count > 0 or recently_triggered))

    response: dict[str, Any] = {
        "success": True,
        "automation": {
            "alias": automation.get("alias")  # type: ignore[union-attr]
            if not from_api
            else ha_state.get("attributes", {}).get("friendly_name"),
            "entity_id": entity_id,
            "mode": automation.get("mode", "single") if not from_api else "single",  # type: ignore[union-attr]
        },
        "stats": {
            "hours_back": hours_back,
            "run_count": run_count,
            "last_run": last_run,
            "last_triggered_attr": last_triggered_attr,
            "current_state": current_state,
            "is_enabled": is_enabled,
            "is_working": is_working,
        },
        "missing_entities": missing_entities,
    }

    if detail_level == "full":
        try:
            logbook_result = make_ha_request(
                ha_url, ha_token, f"/api/logbook/{start_time}?entity={entity_id}"
            )
            recent_activity: list[dict[str, Any]] = []
            if logbook_result.get("success") and isinstance(logbook_result.get("data"), list):
                for entry in logbook_result["data"][-10:]:
                    recent_activity.append(
                        {
                            "when": entry.get("when"),
                            "message": entry.get("message"),
                            "context_id": entry.get("context_id"),
                            "domain": entry.get("domain"),
                        }
                    )
            response["recent_activity"] = recent_activity

            state_changes: list[dict[str, Any]] = []
            entity_candidates = set(entities)
            if not entity_candidates and not from_api:
                deps_result = _do_get_automation_dependencies(automation_id, config_path)  # type: ignore[arg-type]
                if deps_result.get("success"):
                    for ent in deps_result.get("dependencies", {}).get("entities", []):
                        if isinstance(ent, str) and not ent.startswith("script."):
                            entity_candidates.add(ent)

            sample_entities = sorted(entity_candidates)[:5]
            if sample_entities:
                history_filter = ",".join(sample_entities)
                entity_hist_result = make_ha_request(
                    ha_url,
                    ha_token,
                    _build_history_url(start_dt, entity_id=history_filter, minimal=False),
                )
                if entity_hist_result.get("success") and isinstance(
                    entity_hist_result.get("data"), list
                ):
                    for series in entity_hist_result["data"]:
                        if not isinstance(series, list) or len(series) < 2:
                            continue
                        eid = series[0].get("entity_id", "unknown") if series else "unknown"
                        for point in series[-5:]:
                            idx = series.index(point)
                            prev_point = series[idx - 1] if idx > 0 else None
                            state_changes.append(
                                {
                                    "entity_id": eid,
                                    "from": prev_point.get("state") if prev_point else None,
                                    "to": point.get("state"),
                                    "when": point.get("last_changed") or point.get("last_updated"),
                                }
                            )
            response["state_changes"] = state_changes

            context_chain: list[dict[str, Any]] = []
            if logbook_result.get("success") and isinstance(logbook_result.get("data"), list):
                context_ids_seen: dict[str, dict[str, Any]] = {}
                for entry in logbook_result["data"]:
                    ctx_id = entry.get("context_id")
                    if ctx_id and ctx_id not in context_ids_seen:
                        context_ids_seen[ctx_id] = {
                            "context_id": ctx_id,
                            "parent_id": entry.get("context_parent_id"),
                            "entries_count": 1,
                        }
                    elif ctx_id:
                        context_ids_seen[ctx_id]["entries_count"] += 1
                context_chain = list(context_ids_seen.values())
            response["context_chain"] = context_chain

        except Exception:
            response.setdefault("recent_activity", [])
            response.setdefault("state_changes", [])
            response.setdefault("context_chain", [])

    return response


def _do_automation_validate_triggers(
    automation_id: str | None = None,
    automation_alias: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    automations = _load_automations(config_path)  # type: ignore[arg-type]

    if not automations:
        return {"success": False, "error": "No automations found"}

    automation = None
    if automation_id:
        for auto in automations:
            if str(auto.get("id", "")) == str(automation_id):
                automation = auto
                break

    if not automation and automation_alias:
        for auto in automations:
            if auto.get("alias", "").lower() == automation_alias.lower():
                automation = auto
                break

    if not automation:
        available = [f"{a.get('id')}: {a.get('alias', 'unnamed')}" for a in automations]
        return {
            "success": False,
            "error": "Automation not found",
            "provided_id": automation_id,
            "provided_alias": automation_alias,
            "available_automations": available[:20],
        }

    triggers = automation.get("trigger", [])
    if isinstance(triggers, dict):
        triggers = [triggers]

    trigger_ids = set()
    trigger_details = []

    for i, trigger in enumerate(triggers):
        if isinstance(trigger, dict):
            trigger_id = trigger.get("id")
            trigger_platform = trigger.get("platform", "unknown")

            trigger_info = {
                "index": i,
                "platform": trigger_platform,
                "id": trigger_id,
                "has_id": trigger_id is not None,
            }

            if trigger_id:
                trigger_ids.add(trigger_id)

                if (
                    sum(1 for t in triggers if isinstance(t, dict) and t.get("id") == trigger_id)
                    > 1
                ):
                    trigger_info["duplicate"] = True

            trigger_details.append(trigger_info)

    actions = automation.get("action", [])

    handlers_found = set()
    handler_locations = []

    def _scan_for_trigger_handlers(obj, path=""):  # type: ignore[no-untyped-def]
        if isinstance(obj, dict):
            if "choose" in obj:
                for i, choice in enumerate(obj["choose"]):
                    if isinstance(choice, dict):
                        conditions = choice.get("conditions", [])
                        if isinstance(conditions, dict):
                            conditions = [conditions]

                        for cond in conditions:
                            if isinstance(cond, dict) and cond.get("condition") == "trigger":
                                ids = cond.get("id", [])
                                if isinstance(ids, str):
                                    ids = [ids]
                                for tid in ids:
                                    handlers_found.add(tid)
                                    handler_locations.append(
                                        {
                                            "trigger_id": tid,
                                            "location": f"{path}.choose[{i}].conditions",
                                            "type": "choose",
                                        }
                                    )

            if "if" in obj:
                if_conditions = obj["if"]
                if isinstance(if_conditions, dict):
                    if_conditions = [if_conditions]

                for i, cond in enumerate(if_conditions):
                    if isinstance(cond, dict) and cond.get("condition") == "trigger":
                        ids = cond.get("id", [])
                        if isinstance(ids, str):
                            ids = [ids]
                        for tid in ids:
                            handlers_found.add(tid)
                            handler_locations.append(
                                {
                                    "trigger_id": tid,
                                    "location": f"{path}.if[{i}]",
                                    "type": "if",
                                }
                            )

            if "parallel" in obj:
                parallel_items = obj["parallel"]
                if isinstance(parallel_items, dict):
                    parallel_items = [parallel_items]

                for i, item in enumerate(parallel_items):
                    if isinstance(item, dict):
                        _scan_for_trigger_handlers(item, f"{path}.parallel[{i}]")  # type: ignore[no-untyped-call]

            for key, value in obj.items():
                if key not in ["choose", "if", "parallel"]:
                    _scan_for_trigger_handlers(value, f"{path}.{key}")  # type: ignore[no-untyped-call]

        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _scan_for_trigger_handlers(item, f"{path}[{i}]")  # type: ignore[no-untyped-call]

    _scan_for_trigger_handlers(actions, "action")  # type: ignore[no-untyped-call]

    orphaned_triggers = trigger_ids - handlers_found
    missing_handlers = handlers_found - trigger_ids

    recommendations = []

    if orphaned_triggers:
        recommendations.append(
            {
                "severity": "warning",
                "issue": "orphaned_triggers",
                "message": f"Trigger IDs without handlers: {list(orphaned_triggers)}",
                "fix": "Add 'choose' or 'if' conditions that reference these trigger IDs, or remove the IDs if not needed",
            }
        )

    if missing_handlers:
        recommendations.append(
            {
                "severity": "error",
                "issue": "missing_triggers",
                "message": f"Handlers reference non-existent trigger IDs: {list(missing_handlers)}",
                "fix": "Add triggers with these IDs or correct the handler references",
            }
        )

    triggers_without_id = [t for t in trigger_details if not t["has_id"]]
    if len(triggers) > 1 and len(triggers_without_id) == len(triggers):
        recommendations.append(
            {
                "severity": "info",
                "issue": "no_trigger_ids",
                "message": "Multiple triggers but no IDs defined",
                "fix": "Consider adding IDs to triggers for better control flow with 'choose' conditions",
            }
        )

    duplicates = [t for t in trigger_details if t.get("duplicate")]
    if duplicates:
        recommendations.append(
            {
                "severity": "error",
                "issue": "duplicate_ids",
                "message": f"Duplicate trigger IDs found: {[d['id'] for d in duplicates]}",
                "fix": "Make each trigger ID unique",
            }
        )

    return {
        "success": True,
        "automation": {
            "id": automation.get("id"),
            "alias": automation.get("alias"),
            "mode": automation.get("mode", "single"),
        },
        "validation": {
            "total_triggers": len(triggers),
            "triggers_with_ids": len(trigger_ids),
            "handlers_found": len(handlers_found),
            "orphaned_triggers": list(orphaned_triggers),
            "missing_handlers": list(missing_handlers),
            "duplicate_ids": [d["id"] for d in duplicates],
        },
        "details": {
            "triggers": trigger_details,
            "handlers": handler_locations,
        },
        "recommendations": recommendations,
        "is_valid": len(missing_handlers) == 0 and len(duplicates) == 0,
    }


def _do_get_automation_file_location(automation_id: str, config_path: str) -> dict[str, Any]:
    if not automation_id or not isinstance(automation_id, str):
        return {"success": False, "error": "automation_id is required and must be a string"}

    if not automation_id.strip():
        return {"success": False, "error": "automation_id is required"}

    data = _load_automations(config_path)
    if not data:
        return {
            "success": False,
            "error": f"No automations found in {config_path}/automations.yaml",
        }

    item = _get_automation_by_id_or_alias(data, automation_id)
    if not item:
        return {"success": False, "error": f"Automation '{automation_id}' not found"}

    auto_id = str(item.get("id", ""))
    file_path = os.path.join(config_path, "automations.yaml")

    try:
        with open(file_path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        return {"success": False, "error": f"Cannot read automations.yaml: {e}"}

    line_start = None
    line_end = None
    found_current = False

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("- "):
            if found_current:
                line_end = i - 1
                break
            id_str = f"id: '{auto_id}'" if auto_id else ""
            id_str_dq = f'id: "{auto_id}"' if auto_id else ""
            id_str_nq = f"id: {auto_id}" if auto_id else ""
            if (
                (auto_id and id_str in stripped)
                or (auto_id and id_str_dq in stripped)
                or (auto_id and id_str_nq in stripped)
            ):
                found_current = True
                line_start = i

    if line_start is None:
        return {"success": False, "error": f"Could not locate automation '{auto_id}' in file lines"}

    if line_end is None:
        line_end = len(lines)

    surrounding = "".join(lines[line_start - 1 : line_end])

    return {
        "success": True,
        "automation_id": auto_id,
        "alias": item.get("alias"),
        "file_path": "automations.yaml",
        "line_start": line_start,
        "line_end": line_end,
        "surrounding_yaml": surrounding.rstrip(),
    }


def _do_diagnose_automation_aliases(
    config_path: str,
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "duplicates": [],
        "total_duplicates": 0,
    }

    aliases: dict[str, list[dict[str, Any]]] = defaultdict(list)

    yaml_autos = _load_automations(config_path)
    for item in yaml_autos:
        alias = item.get("alias", "Unknown")
        aliases[alias].append(
            {
                "entity_id": f"automation.{alias}",
                "state": "unknown",
                "source": "automations.yaml",
            }
        )

    states_res = make_ha_request(ha_url, ha_token, "/api/states")
    if states_res.get("success"):
        for s in states_res["data"]:
            if s["entity_id"].startswith("automation."):
                alias = s["attributes"].get("friendly_name", s["entity_id"])
                entry = {
                    "entity_id": s["entity_id"],
                    "state": s.get("state", "unknown"),
                    "last_triggered": s["attributes"].get("last_triggered"),
                    "source": "ui",
                }
                already = any(e["entity_id"] == entry["entity_id"] for e in aliases.get(alias, []))
                if not already:
                    aliases[alias].append(entry)

    yaml_by_alias: dict[str, dict[str, Any]] = {}
    for item in yaml_autos:
        alias = item.get("alias", "Unknown")
        if alias not in yaml_by_alias:
            yaml_by_alias[alias] = item

    duplicates = []
    for alias, entries in aliases.items():
        if len(entries) > 1:
            states_set = set(e.get("state") for e in entries)
            if states_set == {"unknown"} or states_set == {None}:
                impact = "all_disabled"
            elif all(s == "on" for s in states_set if s and s != "unknown"):
                impact = "all_enabled"
            else:
                impact = "mixed"

            dup_entry: dict[str, Any] = {
                "alias": alias,
                "entities": entries,
                "impact": impact,
            }

            _compute_overlap(dup_entry, entries, yaml_by_alias.get(alias), config_path)
            duplicates.append(dup_entry)

    result["duplicates"] = duplicates
    result["total_duplicates"] = len(duplicates)
    return result


def _compute_overlap(
    dup_entry: dict[str, Any],
    entries: list[dict[str, Any]],
    yaml_auto: dict[str, Any] | None,
    config_path: str,
) -> None:
    """Enrich a duplicate group with overlap score, trigger/action overlap, and stale detection."""
    if not yaml_auto:
        dup_entry["overlap_score"] = 0
        dup_entry["trigger_overlap"] = []
        dup_entry["action_target_overlap"] = []
        return

    triggers = yaml_auto.get("trigger", [])
    if isinstance(triggers, dict):
        triggers = [triggers]

    actions = yaml_auto.get("action", [])
    if isinstance(actions, dict):
        actions = [actions]

    primary_trigger_entities: set[str] = set()
    primary_action_entities: set[str] = set()

    for trigger in triggers:
        entity_id = trigger.get("entity_id", "")
        if isinstance(entity_id, str):
            primary_trigger_entities.add(entity_id)
        elif isinstance(entity_id, list):
            primary_trigger_entities.update(entity_id)

    def _collect_action_targets(obj: Any, targets: set[str]) -> None:
        if isinstance(obj, dict):
            eid = obj.get("entity_id", "")
            if isinstance(eid, str):
                targets.add(eid)
            target = obj.get("target", {})
            if isinstance(target, dict):
                t_eid = target.get("entity_id", "")
                if isinstance(t_eid, str):
                    targets.add(t_eid)
                elif isinstance(t_eid, list):
                    targets.update(t_eid)
            for val in obj.values():
                _collect_action_targets(val, targets)
        elif isinstance(obj, list):
            for item in obj:
                _collect_action_targets(item, targets)

    _collect_action_targets(actions, primary_action_entities)

    primary_trigger_entities.discard("")
    primary_action_entities.discard("")

    other_trigger_entities: set[str] = set()
    other_action_entities: set[str] = set()

    other_autos = _load_automations(config_path)
    for other in other_autos:
        other_alias = other.get("alias", "")
        if other_alias != dup_entry.get("alias"):
            continue
        other_triggers = other.get("trigger", [])
        if isinstance(other_triggers, dict):
            other_triggers = [other_triggers]
        other_actions = other.get("action", [])
        if isinstance(other_actions, dict):
            other_actions = [other_actions]

        other_trigger_set: set[str] = set()
        for t in other_triggers:
            eid = t.get("entity_id", "")
            if isinstance(eid, str):
                other_trigger_set.add(eid)
            elif isinstance(eid, list):
                other_trigger_set.update(eid)
        other_trigger_set.discard("")

        other_action_set: set[str] = set()
        _collect_action_targets(other_actions, other_action_set)
        other_action_set.discard("")

        other_trigger_entities |= other_trigger_set
        other_action_entities |= other_action_set

    trigger_entities = primary_trigger_entities | other_trigger_entities
    action_entities = primary_action_entities | other_action_entities

    for entry in entries:
        entity_id = entry.get("entity_id", "")
        auto_item = None
        if yaml_auto and yaml_auto.get("alias") == dup_entry.get("alias"):
            auto_item = yaml_auto
        if not auto_item:
            for other in other_autos:
                if other.get("alias") == dup_entry.get("alias"):
                    auto_item = other
                    break

    trigger_overlap_list: list[str] = sorted(trigger_entities)
    action_overlap_list: list[str] = sorted(action_entities)

    primary_condition_entities: set[str] = set()
    conditions = yaml_auto.get("condition", [])
    if isinstance(conditions, dict):
        conditions = [conditions]
    for cond in conditions:
        eid = cond.get("entity_id", "")
        if isinstance(eid, str):
            primary_condition_entities.add(eid)
        elif isinstance(eid, list):
            primary_condition_entities.update(eid)
    primary_condition_entities.discard("")

    other_condition_entities: set[str] = set()
    for other in other_autos:
        other_alias = other.get("alias", "")
        if other_alias != dup_entry.get("alias"):
            continue
        other_conds = other.get("condition", [])
        if isinstance(other_conds, dict):
            other_conds = [other_conds]
        other_cond_set: set[str] = set()
        for c in other_conds:
            eid = c.get("entity_id", "")
            if isinstance(eid, str):
                other_cond_set.add(eid)
            elif isinstance(eid, list):
                other_cond_set.update(eid)
        other_cond_set.discard("")
        other_condition_entities |= other_cond_set

    condition_entities = primary_condition_entities | other_condition_entities

    # Compute overlap scores based on intersection of entities shared between
    # the primary automation and other automations with the same alias.
    # Ratio-based scoring prevents inflating scores when entities only exist
    # in a single automation within the duplicate group.
    trigger_intersection = primary_trigger_entities & other_trigger_entities
    if len(trigger_entities) > 0:
        trigger_score = int(40 * len(trigger_intersection) / len(trigger_entities))
    else:
        trigger_score = 0

    action_intersection = primary_action_entities & other_action_entities
    if len(action_entities) > 0:
        action_score = int(40 * len(action_intersection) / len(action_entities))
    else:
        action_score = 0

    condition_intersection = primary_condition_entities & other_condition_entities
    if len(condition_entities) > 0:
        condition_score = int(20 * len(condition_intersection) / len(condition_entities))
    else:
        condition_score = 0

    overlap_score = trigger_score + action_score + condition_score

    stale_entities: list[dict[str, Any]] = []
    states_list = [e.get("state") for e in entries]
    has_on = "on" in states_list
    has_unavailable = "unavailable" in states_list
    if has_on and has_unavailable:
        for e in entries:
            if e.get("state") == "unavailable":
                stale_entities.append(
                    {
                        "entity_id": e.get("entity_id"),
                        "state": "unavailable",
                        "recommendation": "Consider deleting this stale duplicate entry.",
                    }
                )

    dup_entry["overlap_score"] = overlap_score
    dup_entry["trigger_overlap"] = trigger_overlap_list
    dup_entry["action_target_overlap"] = action_overlap_list
    if stale_entities:
        dup_entry["stale_duplicates"] = stale_entities


def _do_search_inside_automations(
    pattern: str,
    search_in: str = "all",
    config_path: str | None = None,
) -> dict[str, Any]:
    if not pattern or not isinstance(pattern, str) or not pattern.strip():
        return {"success": False, "error": "pattern is required and must be a non-empty string"}

    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return {"success": False, "error": f"Invalid regex pattern: {e}"}

    automations = _load_automations(config_path)  # type: ignore[arg-type]
    matches: list[dict[str, Any]] = []

    for auto in automations:
        alias = auto.get("alias", "Unnamed")
        sections: dict[str, Any] = {}
        if search_in in ("all", "triggers"):
            sections["trigger"] = auto.get("trigger")
        if search_in in ("all", "conditions"):
            sections["condition"] = auto.get("condition")
        if search_in in ("all", "actions"):
            sections["action"] = auto.get("action")

        for section_name, section_data in sections.items():
            if not section_data:
                continue
            yaml_str = yaml.dump(section_data, sort_keys=False, allow_unicode=True)
            for match in compiled.finditer(yaml_str):
                line_number = yaml_str[: match.start()].count("\n") + 1
                matched_text = match.group()[:200]
                matches.append(
                    {
                        "automation_alias": alias,
                        "matched_field": section_name,
                        "matched_text": matched_text,
                        "line_number": line_number,
                    }
                )

    return {
        "success": True,
        "total_automations": len(automations),
        "pattern": pattern,
        "search_in": search_in,
        "match_count": len(matches),
        "matches": matches,
    }


def _do_diagnose_uncategorized_automations(
    scope: str = "automation",
    auto_suggest: bool = True,
    config_path: str | None = None,
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> dict[str, Any]:
    uncategorized: list[dict[str, Any]] = []

    entity_entries = load_registry("core.entity_registry", config_path)
    entities = entity_entries.get("data", {}).get("entities", []) if entity_entries else []

    for entity in entities:
        categories = entity.get("categories", {})
        if categories is None or categories == {}:
            entity_id = entity.get("entity_id", "")
            if scope != "automation" or entity_id.startswith("automation."):
                domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
                if scope == "automation" and domain != "automation":
                    continue
                entry: dict[str, Any] = {
                    "entity_id": entity_id,
                    "alias": entity.get("name") or entity.get("original_name", entity_id),
                    "area_id": entity.get("area_id"),
                    "suggested_category": None,
                }
                if auto_suggest:
                    alias = entry["alias"] or ""
                    if alias and " " in alias:
                        prefix = alias.split(" ", 1)[0].strip()
                        if len(prefix) >= 3 and len(prefix) <= 20 and prefix.isalnum():
                            entry["suggested_category"] = prefix.lower()
                uncategorized.append(entry)

    return {
        "success": True,
        "scope": scope,
        "auto_suggest": auto_suggest,
        "total_uncategorized": len(uncategorized),
        "uncategorized": uncategorized,
    }


_CANONICAL_PREFIXES = frozenset(
    {
        "Heating",
        "Light",
        "Notify",
        "Energy",
        "Watchdog",
        "Camera",
        "Dashboard",
        "OpenHASP",
        "Device",
        "System",
        "Enhanced Smart Control",
    }
)

_POLISH_CHARS = frozenset("\u0105\u0119\u015b\u0107\u0144\u00f3\u0142\u017c\u017a")
_EMOJI_RANGE_RE = re.compile("[\U0001f300-\U0001f9ff]")

_PREFIX_CATEGORY_MAP: dict[str, list[str]] = {
    "Heating": ["Heating", "Climate"],
    "Light": ["Lighting", "Light"],
    "Notify": ["Notification", "Notify"],
    "Energy": ["Energy"],
    "Watchdog": ["Watchdog", "Monitoring"],
    "Camera": ["Camera", "Security"],
    "Dashboard": ["Dashboard"],
    "OpenHASP": ["OpenHASP", "Display"],
    "Device": ["Device"],
    "System": ["System", "Core"],
    "Enhanced Smart Control": ["Energy", "Smart Control"],
}

_TITLE_CASE_WORDS_RE = re.compile(r"\b[A-Z][a-z]*\b")


def _do_validate_automation_names(
    category_filter: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    automations = _load_automations(config_path)  # type: ignore[arg-type]
    violations: list[dict[str, Any]] = []

    if category_filter and config_path:
        cat_registry = load_registry("core.category_registry", config_path)
        cat_entries = cat_registry.get("data", {}).get("categories", [])
        target_category_id: str | None = None
        for cat_entry in cat_entries:
            if cat_entry.get("category_id") == category_filter:
                target_category_id = category_filter
                break
            if cat_entry.get("name", "").lower() == category_filter.lower():
                target_category_id = cat_entry.get("category_id")
                break

    for item in automations:
        alias = str(item.get("alias") or "")
        entity_id = f"automation.{re.sub(r'[^a-z0-9_]+', '_', str(item.get('id') or alias).lower()).strip('_')}"

        if category_filter and config_path:
            auto_unique_id = str(item.get("id", ""))
            entity_entries = load_registry("core.entity_registry", config_path)
            entities = entity_entries.get("data", {}).get("entities", [])
            matched = False
            for ent in entities:
                ent_uid = ent.get("unique_id", "")
                ent_eid = ent.get("entity_id", "")
                if (ent_uid and ent_uid == auto_unique_id) or ent_eid == entity_id:
                    ent_cats = ent.get("categories", {}) or {}
                    if ent_cats.get("automation") == target_category_id:
                        matched = True
                    break
            if not matched:
                continue

        if not alias:
            continue

        # Rule 1: Separator must be " - " not " — ", " – ", ": "
        if " - " not in alias and (" — " in alias or " – " in alias or ": " in alias):
            violations.append(
                {
                    "entity_id": entity_id,
                    "alias": alias,
                    "violation_type": "wrong_separator",
                    "current_value": alias,
                    "suggested_fix": alias.replace(" — ", " - ")
                    .replace(" – ", " - ")
                    .replace(": ", " - "),
                    "severity": "warning",
                }
            )
            continue

        # Rule 2: Prefix before first " - " must be canonical
        parts = alias.split(" - ", 1)
        prefix = parts[0].strip() if parts else ""
        remainder = parts[1] if len(parts) > 1 else ""

        if prefix not in _CANONICAL_PREFIXES:
            violations.append(
                {
                    "entity_id": entity_id,
                    "alias": alias,
                    "violation_type": "missing_prefix",
                    "current_value": prefix if prefix else "(empty)",
                    "suggested_fix": f"Add canonical prefix from: {sorted(_CANONICAL_PREFIXES)}",
                    "severity": "error",
                }
            )

        # Rule 3: No Polish characters
        polish_found = [c for c in alias if c.lower() in _POLISH_CHARS or c in _POLISH_CHARS]
        if polish_found:
            violations.append(
                {
                    "entity_id": entity_id,
                    "alias": alias,
                    "violation_type": "polish_characters",
                    "current_value": "".join(polish_found),
                    "suggested_fix": "Replace Polish characters with ASCII equivalents",
                    "severity": "warning",
                }
            )

        # Rule 4: No emoji in alias
        if _EMOJI_RANGE_RE.search(alias):
            violations.append(
                {
                    "entity_id": entity_id,
                    "alias": alias,
                    "violation_type": "emoji_in_alias",
                    "current_value": alias,
                    "suggested_fix": "Remove emoji characters from alias",
                    "severity": "error",
                }
            )

        # Rule 5: Title Case in purpose text (after prefix)
        if remainder:
            words = remainder.split()
            title_words = _TITLE_CASE_WORDS_RE.findall(remainder)
            if title_words and len(title_words) < len([w for w in words if w[0].isalpha()]) * 0.5:
                violations.append(
                    {
                        "entity_id": entity_id,
                        "alias": alias,
                        "violation_type": "bad_capitalization",
                        "current_value": remainder,
                        "suggested_fix": remainder.title(),
                        "severity": "warning",
                    }
                )

        # Rule 6: Compound hyphens (e.g. "Auto-Off" should use hyphens not spaces)
        compound_pattern = re.compile(
            r"\b(Auto|Semi|Multi|Cross|Pre|Post|Co|Non|Sub|Super|Over|Under|"
            r"Cache|Real|Time|Stage|Phase|Level|State|Self|Full|Half|Part"
            r")[ -]([A-Z][a-z]+)\b"
        )
        matches = compound_pattern.findall(alias)
        if matches:
            for m in matches:
                first, second = m
                full = f"{first} {second}"
                if f"{first} {second}" in alias and f"{first}-{second}" not in alias:
                    violations.append(
                        {
                            "entity_id": entity_id,
                            "alias": alias,
                            "violation_type": "missing_compound_hyphen",
                            "current_value": full,
                            "suggested_fix": f"{first}-{second}",
                            "severity": "warning",
                        }
                    )

        # Rule 7: Version strings must be lowercase (v5.0, v2)
        version_pattern = re.compile(r"\b[Vv]\d+(?:\.\d+)?\b")
        for vmatch in version_pattern.finditer(alias):
            vtext = vmatch.group()
            if vtext[0].isupper():
                violations.append(
                    {
                        "entity_id": entity_id,
                        "alias": alias,
                        "violation_type": "uppercase_version",
                        "current_value": vtext,
                        "suggested_fix": vtext.lower(),
                        "severity": "warning",
                    }
                )

    return {
        "success": True,
        "total_automations": len(automations),
        "total_violations": len(violations),
        "violations": violations,
    }


def _deep_substitute_inputs(obj: Any, user_inputs: dict[str, Any]) -> Any:
    """Recursively substitute ``!input <var>`` strings with user-provided values.

    Blueprint YAML templates use ``!input <variable_name>`` tags that the
    ``HomeAssistantLoader`` converts to plain strings (e.g. ``"!input motion_sensor"``).
    This function walks the parsed dict/list structure and replaces those placeholder
    strings with the concrete values from ``user_inputs``.

    Args:
        obj: Parsed blueprint template (dict, list, or scalar).
        user_inputs: Dictionary mapping input variable names to their values.

    Returns:
        The same structure with ``!input <var>`` strings replaced by concrete values.
    """
    if isinstance(obj, dict):
        return {key: _deep_substitute_inputs(value, user_inputs) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_deep_substitute_inputs(item, user_inputs) for item in obj]
    if isinstance(obj, str) and obj.startswith("!input ") and len(obj) > 7:
        var_name = obj[7:].strip()
        if var_name in user_inputs:
            return user_inputs[var_name]
    return obj


def _do_resolve_blueprint_automation(
    automation_id: str,
    config_path: str,
) -> dict[str, Any]:
    """Resolve a blueprint automation to its concrete configuration.

    If the automation uses a blueprint (``use_blueprint`` key present), loads the
    blueprint YAML, substitutes the user-provided input values into the blueprint
    template, and returns the resolved automation as if it were a regular
    (non-blueprint) automation.  The stored YAML is never modified — resolution is
    computed, not persisted.

    If the automation does NOT use a blueprint, returns it unchanged (same shape as
    ``_do_get_automation_code``).

    Args:
        automation_id: Automation alias or id (from ``automations.yaml``).
        config_path: Path to the Home Assistant config directory.

    Returns:
        Dict with ``success``, ``alias``, ``automation_id``, ``is_blueprint``,
        ``resolved_yaml``, and optionally ``blueprint_path`` and ``user_inputs``.
    """
    if not automation_id or not isinstance(automation_id, str) or not automation_id.strip():
        return {
            "success": False,
            "error": "automation_id is required and must be a non-empty string",
        }

    data = _load_automations(config_path)
    item = _get_automation_by_id_or_alias(data, automation_id)

    if not item:
        return {"success": False, "error": f"Automation '{automation_id}' not found"}

    automation_id_value = item.get("id")
    use_blueprint = item.get("use_blueprint")

    if not use_blueprint:
        clean_item = item.copy()
        clean_item.pop("id", None)
        return {
            "success": True,
            "alias": item.get("alias"),
            "automation_id": automation_id_value,
            "is_blueprint": False,
            "resolved_yaml": clean_item,
        }

    # Blueprint automation — resolve
    blueprint_path = use_blueprint.get("path")
    user_inputs = use_blueprint.get("input", {})

    if not blueprint_path:
        return {
            "success": False,
            "error": "Automation uses a blueprint but no path specified",
        }

    # Load the blueprint YAML file
    import os as _os

    blueprint_file = _os.path.join(config_path, "blueprints", blueprint_path)
    if not _os.path.isfile(blueprint_file):
        return {
            "success": False,
            "error": f"Blueprint file not found: {blueprint_path}",
        }

    try:
        with open(blueprint_file, encoding="utf-8") as f:
            blueprint_data = yaml.load(f, Loader=HomeAssistantLoader)  # nosec B506
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to load blueprint '{blueprint_path}': {str(e)}",
        }

    if not isinstance(blueprint_data, dict):
        return {
            "success": False,
            "error": f"Blueprint '{blueprint_path}' is not a valid mapping",
        }

    # Build the resolved automation from the blueprint template.
    # The blueprint contains a `blueprint:` metadata key and the template sections
    # (trigger, condition, action, mode, max, max_exceeded, variables).
    # We take everything EXCEPT the `blueprint:` key and substitute inputs.
    template_keys = {k for k in blueprint_data if k != "blueprint"}

    resolved: dict[str, Any] = {}
    for key in template_keys:
        resolved[key] = _deep_substitute_inputs(blueprint_data[key], user_inputs)

    # Merge automation-level fields from the blueprint instance
    # (alias, description, mode, max, max_exceeded) that may override template values.
    for override_key in ("alias", "description", "mode", "max", "max_exceeded"):
        if override_key in item:
            resolved[override_key] = item[override_key]

    return {
        "success": True,
        "alias": item.get("alias"),
        "automation_id": automation_id_value,
        "is_blueprint": True,
        "blueprint_path": blueprint_path,
        "user_inputs": user_inputs,
        "resolved_yaml": resolved,
    }


def _do_diagnose_category_alias_mismatch(
    config_path: str | None = None,
) -> dict[str, Any]:
    automations = _load_automations(config_path)  # type: ignore[arg-type]
    mismatches: list[dict[str, Any]] = []

    entity_entries = load_registry("core.entity_registry", config_path)
    entities = entity_entries.get("data", {}).get("entities", []) if entity_entries else []

    cat_registry = load_registry("core.category_registry", config_path)
    cat_entries = cat_registry.get("data", {}).get("categories", [])
    cat_id_to_name: dict[str, str] = {}
    for cat in cat_entries:
        cid = cat.get("category_id", "")
        if cid:
            cat_id_to_name[cid] = cat.get("name", cid)

    for item in automations:
        alias = str(item.get("alias") or "")
        if not alias:
            continue

        parts = alias.split(" - ", 1)
        prefix = parts[0].strip() if parts else ""
        if prefix not in _PREFIX_CATEGORY_MAP:
            continue

        auto_unique_id = str(item.get("id", ""))
        auto_entity_id = f"automation.{re.sub(r'[^a-z0-9_]+', '_', str(item.get('id') or alias).lower()).strip('_')}"

        assigned_cat_id: str | None = None
        for ent in entities:
            ent_uid = ent.get("unique_id", "")
            ent_eid = ent.get("entity_id", "")
            if (ent_uid and ent_uid == auto_unique_id) or ent_eid == auto_entity_id:
                ent_cats = ent.get("categories", {}) or {}
                assigned_cat_id = ent_cats.get("automation")
                break

        if not assigned_cat_id:
            continue

        assigned_cat_name = cat_id_to_name.get(assigned_cat_id, assigned_cat_id)
        expected_names = _PREFIX_CATEGORY_MAP.get(prefix, [])

        if not any(assigned_cat_name.lower() == expected.lower() for expected in expected_names):
            mismatches.append(
                {
                    "entity_id": auto_entity_id,
                    "alias": alias,
                    "alias_prefix": prefix,
                    "assigned_category_name": assigned_cat_name,
                    "expected_category_name": " or ".join(expected_names),
                    "severity": "warning",
                }
            )

    return {
        "success": True,
        "total_automations": len(automations),
        "total_mismatches": len(mismatches),
        "mismatches": mismatches,
    }


# ========================================
# TOOL REGISTRATION
# ========================================


def register_automation_tools(mcp, config_path, ha_url=None, ha_token=None) -> None:  # type: ignore[no-untyped-def]
    """
    Registers tools for managing automations.
    """

    @mcp.tool()
    def search_automations(
        search_term: str | None = None,
        include_code: bool = False,
        mode: str | None = None,
        uses_blueprint: bool | None = None,
        deep: bool = False,
        category: str | None = None,
        include_entity_id: bool = False,
    ) -> str:
        """[READ] Search automations by alias, description, mode, or blueprint usage. ~95% token savings vs listing all.

        ~95% token savings when searching for a specific automation.
        Instead of: list_automations() (111 items) -> search (1-5 items)

        Args:
            search_term: Searches in id, alias, description (case-insensitive)
            include_code: Whether to include full YAML code (default: False)
            mode: Filter by mode: "single", "restart", "queued", "parallel"
            uses_blueprint: True = only blueprint, False = only native, None = all
            deep: Recursively search nested fields (variables, choose branches, sequences) (default: False)
            category: Filter by category_id or category_name (default: None)
            include_entity_id: Whether to include resolved entity_id from entity registry (default: False)

        Returns:
            JSON with matching automations, optional full code, and match_paths when deep=True

        Examples:
            search_automations("energy")
            search_automations("dashboard", include_code=True)
            search_automations(mode="restart", uses_blueprint=True)
            search_automations("_hp_days", deep=True)
            search_automations(category="Lighting")
        """
        try:
            result = _do_search_automations(
                search_term=search_term,
                include_code=include_code,
                mode=mode,
                uses_blueprint=uses_blueprint,
                deep=deep,
                category=category,
                include_entity_id=include_entity_id,
                config_path=config_path,
            )
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("search_automations failed")
            return _error_response(str(exc))

    @mcp.tool()
    def list_automations(detail_level: str = "full") -> str:
        """[READ] Fetches list of names and ids of all automations.

        Warning: returns all 111 automations - use search_automations() if looking for a specific one.

        Args:
            detail_level: "summary" (alias+mode only) or "full" (all metadata including
                description, trigger_count, action_count). Default: "full".

        Returns:
            JSON with success, total_count, and automations list.
        """
        try:
            result = _do_list_automations(config_path, detail_level=detail_level)
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("list_automations failed")
            return _error_response(str(exc))

    @mcp.tool()
    def get_automation_code(automation_id: str) -> str:
        """[READ] Fetches full automation code (without 'id' - ready to paste in UI).

        Args:
            automation_id: Automation alias or id (prefer alias)
        """
        try:
            result = _do_get_automation_code(automation_id, config_path)
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("get_automation_code failed")
            return _error_response(str(exc))

    @mcp.tool()
    def get_automation_dependencies(automation_id: str) -> str:
        """[READ] Analyze automation dependencies: lists used entities, scripts, services, and blueprints.
        Lists used entities, scripts, services, and blueprints.

        Args:
            automation_id: Automation id or alias.

        Returns:
            JSON with lists: entities, scripts, services, blueprints.
        """
        try:
            result = _do_get_automation_dependencies(automation_id, config_path)
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("get_automation_dependencies failed")
            return _error_response(str(exc))

    @mcp.tool()
    def search_automations_by_entity(entity_id: str) -> str:
        """[READ] Find all automations that reference a given entity in triggers, conditions, or actions.
        Checks if entity is in triggers, conditions, or actions.

        Args:
            entity_id: Entity id (e.g. "binary_sensor.motion").

        Returns:
            JSON with list of automations and usage context (trigger/condition/action).
        """
        try:
            result = _do_search_automations_by_entity(entity_id, config_path)
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("search_automations_by_entity failed")
            return _error_response(str(exc))

    @mcp.tool()
    def get_automation_conflicts(entity_id: str) -> str:
        """[READ] Detect potential conflicts where multiple automations modify the same entity, which may cause flickering or unexpected states.
        Detects if a given entity is modified by multiple automations at once
        (which may cause light flickering or unexpected states).

        Args:
            entity_id: Entity to check (e.g. "light.living_room").

        Returns:
            JSON with list of automations that control this entity (Action).
        """
        try:
            result = _do_get_automation_conflicts(entity_id, config_path)
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("get_automation_conflicts failed")
            return _error_response(str(exc))

    @mcp.tool()
    def diagnose_automation(automation_id: str, detail_level: str = "summary") -> str:
        """[READ] Comprehensive automation diagnostics with entity resolution, template validation, and usage analysis. ~75% token savings.

        ~75% token savings when analyzing automations.
        Instead of: get_automation_code() + check_entity_exists() × N + test_template() × M

        Args:
            automation_id: Automation alias or id (prefer alias)
            detail_level: "minimal" | "summary" | "full"

        Returns:
            JSON with:
            - automation_info
            - entity_validation
            - template_validation
            - trigger_analysis
            - condition_analysis
            - action_analysis
            - issues
            - recommendations
        """
        try:
            result = _do_diagnose_automation(
                automation_id=automation_id,
                detail_level=detail_level,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("diagnose_automation failed")
            return _error_response(str(exc))

    @mcp.tool()
    def get_automation_usage_stats(
        automation_id: str, hours_back: int = 24, detail_level: str = "summary"
    ) -> str:
        """[READ] Get automation usage statistics: run count, last triggered time, recent activity from history.

        Target: Automation usage statistics.

        Args:
            automation_id: Alias or id from automations.yaml (prefer alias).
            hours_back: How many hours back to check history (default: 24).
            detail_level: 'summary' (default, backward compatible) or 'full'. 'full' adds
                recent_activity (logbook entries), state_changes (dependency entity state
                transitions), and context_chain (context_id relationships).

        Returns:
            JSON with success, automation metadata, stats, and optionally recent_activity,
            state_changes, and context_chain when detail_level='full'.
        """
        try:
            result = _do_get_automation_usage_stats(
                automation_id=automation_id,
                hours_back=hours_back,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
                detail_level=detail_level,
            )
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("get_automation_usage_stats failed")
            return _error_response(str(exc))

    @mcp.tool()
    async def automation_validate_triggers(
        automation_id: str | None = None, automation_alias: str | None = None
    ) -> str:
        """[READ] Validate that all trigger ids in an automation have corresponding handlers.

        Checks automation configuration for:
        - Triggers with ids that have no corresponding choose/parallel/if blocks
        - Orphaned trigger ids
        - Duplicate trigger ids
        - Missing trigger ids in conditions/actions

        Args:
            automation_id: The automation id (from automations.yaml)
            automation_alias: Alternative - use automation alias/friendly name

        Returns:
            JSON with validation results:
            - triggers_found: list of all trigger ids
            - handlers_found: list of all trigger id handlers
            - orphaned_triggers: triggers with no handlers
            - missing_handlers: handlers referenced but not defined
            - duplicates: duplicate trigger ids
            - recommendations: suggested fixes
        """
        try:
            result = _do_automation_validate_triggers(
                automation_id=automation_id,
                automation_alias=automation_alias,
                config_path=config_path,
            )
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("automation_validate_triggers failed")
            return _error_response(str(exc))

    @mcp.tool()
    def get_automation_file_location(automation_id: str) -> str:
        """[READ] Returns the file path and line numbers (line_start, line_end) of an automation in automations.yaml.

        Use this to locate an automation's exact position before reading surrounding context
        with read_config_file(offset=line_start, limit=...). Eliminates manual grep + read steps.

        Args:
            automation_id: Automation alias or ID

        Returns:
            JSON with file_path, line_start, line_end, and optionally surrounding_yaml
        """
        try:
            result = _do_get_automation_file_location(automation_id, config_path)
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("get_automation_file_location failed")
            return _error_response(str(exc))

    @mcp.tool()
    def search_inside_automations(pattern: str, search_in: str = "all") -> str:
        """Searches inside automation YAML for a pattern across triggers, conditions, or actions.

        Searches within YAML dump of each automation for the given pattern.
        Supports regex with safe validation.

        Args:
            pattern: Text or regex pattern to search for (e.g. "light.living_room", "turn_on").
            search_in: Scope of search — "all", "triggers", "conditions", or "actions". Default: "all".

        Returns:
            JSON with matches list containing automation_alias, matched_field, matched_text, and line_number.
        """
        try:
            result = _do_search_inside_automations(
                pattern=pattern,
                search_in=search_in,
                config_path=config_path,
            )
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("search_inside_automations failed")
            return _error_response(str(exc))

    @mcp.tool()
    def diagnose_uncategorized_automations(
        scope: str = "automation",
        auto_suggest: bool = True,
    ) -> str:
        """Scans entity registry for automations without a category assigned.

        Filters entities with empty categories dict and optionally suggests
        a category based on the alias prefix.

        Args:
            scope: Entity domain to scan (default: "automation").
            auto_suggest: If True, suggest category from alias prefix (default: True).

        Returns:
            JSON with uncategorized list containing entity_id, alias, area_id, and suggested_category.
        """
        try:
            result = _do_diagnose_uncategorized_automations(
                scope=scope,
                auto_suggest=auto_suggest,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("diagnose_uncategorized_automations failed")
            return _error_response(str(exc))

    @mcp.tool()
    def diagnose_automation_aliases() -> str:
        """Detect duplicate automation aliases from YAML and UI sources.

        Groups all automations by alias and flags groups of 2+ entries
        with the same display name, categorizing the impact as
        all_disabled, all_enabled, or mixed.

        Returns:
            JSON with:
            - duplicates: list of groups with alias, entities, and impact
            - total_duplicates: number of duplicate groups
        """
        try:
            result = _do_diagnose_automation_aliases(
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            return _success_response(result)
        except Exception as exc:
            _logger.exception("diagnose_automation_aliases failed")
            return _error_response(str(exc))

    @mcp.tool()
    def validate_automation_names(
        category_filter: str | None = None,
    ) -> str:
        """Validate automation alias naming conventions against 7 quality rules.

        Checks each automation alias for separator style, canonical prefix,
        Polish characters, emoji, title case, compound hyphens, and version casing.

        Args:
            category_filter: Optional category ID or name to limit validation scope.

        Returns:
            JSON with violations list containing entity_id, alias, violation_type,
            current_value, suggested_fix, and severity.
        """
        try:
            result = _do_validate_automation_names(
                category_filter=category_filter,
                config_path=config_path,
            )
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("validate_automation_names failed")
            return _error_response(str(exc))

    @mcp.tool()
    async def get_automation_entity_id(identifier: str) -> str:
        """[READ] Resolve automation alias to entity_id. Searches HA entity registry for automation.* entities matching by friendly_name (alias).

        Args:
            identifier: Automation alias or partial name to search for (e.g., "Morning Routine").

        Returns:
            JSON with alias, entity_id, unique_id, and optionally matches_count for partial matches.
        """
        try:
            result = _do_get_automation_entity_id(identifier, config_path, ha_url, ha_token)
            return _success_response(result) if result.get("entity_id") else json.dumps(result)
        except Exception as exc:
            _logger.exception("get_automation_entity_id failed")
            return _error_response(str(exc))

    @mcp.tool()
    def resolve_blueprint_automation(automation_id: str) -> str:
        """[READ] Resolves a blueprint automation to its concrete configuration.

        If the automation uses a blueprint (``use_blueprint`` key), loads the
        blueprint YAML, substitutes user-provided input values into the template,
        and returns the resolved automation as if it were a regular (non-blueprint)
        automation.  Regular automations are returned unchanged.

        Resolution is computed, not persisted — the stored YAML is never modified.

        Args:
            automation_id: Automation alias or id (from automations.yaml).

        Returns:
            JSON with ``resolved_yaml`` containing the concrete triggers, conditions,
            and actions with all ``!input`` tags substituted.  Includes ``is_blueprint``
            flag and, for blueprint automations, ``blueprint_path`` and ``user_inputs``.
        """
        try:
            result = _do_resolve_blueprint_automation(automation_id, config_path)
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("resolve_blueprint_automation failed")
            return _error_response(str(exc))

    @mcp.tool()
    def diagnose_category_alias_mismatch() -> str:
        """Detect mismatches between automation alias prefixes and assigned categories.

        For each automation, extracts the prefix from the alias and verifies
        that the assigned category matches the canonical prefix-to-category mapping.

        Returns:
            JSON with mismatches list containing entity_id, alias, alias_prefix,
            assigned_category_name, expected_category_name, and severity.
        """
        try:
            result = _do_diagnose_category_alias_mismatch(
                config_path=config_path,
            )
            return (
                _success_response(result)
                if result.get("success")
                else _error_response(result.get("error", "Unknown error"))
            )
        except Exception as exc:
            _logger.exception("diagnose_category_alias_mismatch failed")
            return _error_response(str(exc))

    register_manifest(
        "get_automation_entity_id", make_manifest("get_automation_entity_id", latency="fast")
    )
    register_manifest(
        "search_inside_automations", make_manifest("search_inside_automations", latency="fast")
    )
    register_manifest(
        "diagnose_uncategorized_automations",
        make_manifest("diagnose_uncategorized_automations", latency="fast"),
    )
    register_manifest(
        "validate_automation_names", make_manifest("validate_automation_names", latency="fast")
    )
    register_manifest(
        "diagnose_category_alias_mismatch",
        make_manifest("diagnose_category_alias_mismatch", latency="fast"),
    )
    register_manifest(
        "resolve_blueprint_automation",
        make_manifest("resolve_blueprint_automation", latency="fast"),
    )
