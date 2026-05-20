"""
Automation Management Tools
Tools for listing, searching, analyzing, and debugging Home Assistant automations.
"""

import logging
import os
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import yaml

from tools.utils import _error_response, _success_response, make_ha_request
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
            return yaml.load(f, Loader=HomeAssistantLoader)  # nosec B506 or []
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
    config_path: str | None = None,
) -> dict[str, Any]:
    data = _load_automations(config_path)  # type: ignore[arg-type]
    results = []

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

        res = {
            "alias": item.get("alias", "Unnamed"),
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


def _do_list_automations(config_path: str) -> dict[str, Any]:
    data = _load_automations(config_path)
    summary = [
        {
            "id": item.get("id"),
            "alias": item.get("alias", "No Name"),
            "description": item.get("description", ""),
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
        for item in data
    ]

    return {"success": True, "total_count": len(summary), "automations": summary}


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
    else:
        for idx, action in enumerate(actions):
            if "variables" in action:
                has_variables = True
                variables_index = idx
                break

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
) -> dict[str, Any]:
    if not ha_url or not ha_token:
        return {"success": False, "error": "HA API not configured (ha_url/ha_token missing)"}

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

    start_time = (datetime.now(UTC) - timedelta(hours=hours_back)).isoformat()
    history_result = make_ha_request(
        ha_url,
        ha_token,
        f"/api/history/period/{start_time}?filter_entity_id={entity_id}",
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

    response = {
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
            duplicates.append(
                {
                    "alias": alias,
                    "entities": entries,
                    "impact": impact,
                }
            )

    result["duplicates"] = duplicates
    result["total_duplicates"] = len(duplicates)
    return result


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
    ) -> str:
        """[READ] Search automations by alias, description, mode, or blueprint usage. ~95% token savings vs listing all.

        ~95% token savings when searching for a specific automation.
        Instead of: list_automations() (111 items) → search (1-5 items)

        Args:
            search_term: Searches in id, alias, description (case-insensitive)
            include_code: Whether to include full YAML code (default: False)
            mode: Filter by mode: "single", "restart", "queued", "parallel"
            uses_blueprint: True = only blueprint, False = only native, None = all
            deep: Recursively search nested fields (variables, choose branches, sequences) (default: False)

        Returns:
            JSON with matching automations, optional full code, and match_paths when deep=True

        Examples:
            search_automations("energy")
            search_automations("dashboard", include_code=True)
            search_automations(mode="restart", uses_blueprint=True)
            search_automations("_hp_days", deep=True)
        """
        try:
            result = _do_search_automations(
                search_term=search_term,
                include_code=include_code,
                mode=mode,
                uses_blueprint=uses_blueprint,
                deep=deep,
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
    def list_automations() -> str:
        """[READ] Fetches list of names and ids of all automations.

        Warning: returns all 111 automations - use search_automations() if looking for a specific one.
        """
        try:
            result = _do_list_automations(config_path)
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
    def get_automation_usage_stats(automation_id: str, hours_back: int = 24) -> str:
        """[READ] Get automation usage statistics: run count, last triggered time, recent activity from history.

        Target: Automation usage statistics.

        returns:
            - last_run: Last run (based on history + last_triggered)
            - run_count: Run count from logs (history)
            - is_working: Whether automation actually works (active state + recent triggers)
            - missing_entities: Entities used in automation that no longer exist in HA

        Useful for debugging "does the automation even trigger".

        Args:
            automation_id: Alias or id from automations.yaml (prefer alias)
            hours_back: How many hours back to check history (default: 24)
        """
        try:
            result = _do_get_automation_usage_stats(
                automation_id=automation_id,
                hours_back=hours_back,
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
    def diagnose_automation_aliases() -> str:
        """[READ] Detect duplicate automation aliases from YAML and UI sources.

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
