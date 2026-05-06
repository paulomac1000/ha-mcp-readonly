"""
Automation Management Tools
Tools for listing, searching, analyzing, and debugging Home Assistant automations.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import yaml

from tools.utils import make_ha_request
from tools.yaml_utils import HomeAssistantLoader


def register_automation_tools(mcp, config_path, ha_url=None, ha_token=None):
    """
    Registers tools for managing automations.
    """

    # ========================================
    # 🛠️ INTERNAL HELPERS
    # ========================================

    def _load_automations() -> List[Dict]:
        """Safely loads the automations.yaml file."""
        try:
            file_path = os.path.join(config_path, "automations.yaml")
            if not os.path.exists(file_path):
                return []
            with open(file_path, "r", encoding="utf-8") as f:
                # Use HomeAssistantLoader to support !include, !secret, etc. tags
                return yaml.load(f, Loader=HomeAssistantLoader) or []
        except Exception:
            return []

    def _get_automation_by_id_or_alias(data: List[Dict], identifier: str) -> Optional[Dict]:
        """Finds automation by alias or id (case-insensitive)."""
        identifier_lower = identifier.lower()
        # 1. Exact match (case-sensitive)
        for item in data:
            if str(item.get("id")) == identifier or item.get("alias") == identifier:
                return item
        # 2. Case insensitive
        for item in data:
            if (
                str(item.get("id")).lower() == identifier_lower
                or item.get("alias", "").lower() == identifier_lower
            ):
                return item
        return None

    def _extract_entities_recursive(data: Any, found: Set[str]):
        """Recursively extracts entities from dictionary/list structures."""
        # Regex for domain.entity_id
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

                # Search templates in strings
                if isinstance(value, str) and ("{{" in value or "{%" in value):
                    found.update(pattern.findall(value))

                _extract_entities_recursive(value, found)

        elif isinstance(data, list):
            for item in data:
                _extract_entities_recursive(item, found)

    def _extract_templates(data: Any, path: str = "") -> List[Dict[str, str]]:
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
    # 🚀 SEARCH & LIST TOOLS
    # ========================================

    @mcp.tool()
    def search_automations(
        search_term: Optional[str] = None,
        include_code: bool = False,
        mode: Optional[str] = None,
        uses_blueprint: Optional[bool] = None,
    ) -> str:
        """
        🚀 OPTIMIZED - searches automations instead of returning all 111.

        ~95% token savings when searching for a specific automation.
        Instead of: list_automations() (111 items) → search (1-5 items)

        Args:
            search_term: Searches in id, alias, description (case-insensitive)
            include_code: Whether to include full YAML code (default: False)
            mode: Filter by mode: "single", "restart", "queued", "parallel"
            uses_blueprint: True = only blueprint, False = only native, None = all

        Returns:
            JSON with matching automations + optional full code

        Examples:
            search_automations("energy")
            search_automations("dashboard", include_code=True)
            search_automations(mode="restart", uses_blueprint=True)
        """
        data = _load_automations()
        results = []

        for item in data:
            # Filters
            if search_term:
                term = search_term.lower()
                text_corpus = f"{item.get('id', '')} {item.get('alias', '')} {item.get('description', '')}".lower()
                if term not in text_corpus:
                    continue

            if mode and item.get("mode", "single") != mode:
                continue

            if uses_blueprint is not None:
                has_bp = "use_blueprint" in item
                if uses_blueprint != has_bp:
                    continue

            # Build result
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

            if include_code:
                # Remove id from code output (UI can't save it)
                clean_item = item.copy()
                clean_item.pop("id", None)
                res["code"] = yaml.dump(clean_item, sort_keys=False, allow_unicode=True)

            results.append(res)

        return json.dumps(
            {
                "success": True,
                "total_automations": len(data),
                "matched_count": len(results),
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    def list_automations() -> str:
        """
        Fetches list of names and ids of all automations.

        ⚠️ Warning: returns all 111 automations - use search_automations() if looking for a specific one.
        """
        data = _load_automations()
        summary = [
            {
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

        return json.dumps(
            {"success": True, "total_count": len(summary), "automations": summary},
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    def get_automation_code(automation_id: str) -> str:
        """
        Fetches full automation code (without 'id' - ready to paste in UI).

        Args:
            automation_id: Automation alias or id (prefer alias)
        """
        data = _load_automations()
        item = _get_automation_by_id_or_alias(data, automation_id)

        if not item:
            return json.dumps(
                {"success": False, "error": f"Automation '{automation_id}' not found"},
                indent=2,
            )

        # REMOVE 'id' FROM code (UI cannot save it)
        clean_item = item.copy()
        automation_id_value = clean_item.pop("id", None)

        return json.dumps(
            {
                "success": True,
                "alias": item.get("alias"),
                "automation_id": automation_id_value,  # returned as metadata
                "code": yaml.dump(
                    clean_item,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                ),
            },
            indent=2,
            ensure_ascii=False,
        )

    # ========================================
    # 🧠 ANALYSIS & DIAGNOSTICS
    # ========================================

    @mcp.tool()
    def get_automation_dependencies(automation_id: str) -> str:
        """
        🚀 DEPENDENCY GRAPH - checks what the automation depends on.
        Lists used entities, scripts, services, and blueprints.

        Args:
            automation_id: Automation id or alias.

        Returns:
            JSON with lists: entities, scripts, services, blueprints.
        """
        data = _load_automations()
        item = _get_automation_by_id_or_alias(data, automation_id)

        if not item:
            return json.dumps({"success": False, "error": "Automation not found"}, indent=2)

        entities = set()
        _extract_entities_recursive(item, entities)

        # Categorize
        scripts = sorted([e for e in entities if e.startswith("script.")])
        scenes = sorted([e for e in entities if e.startswith("scene.")])
        pure_entities = sorted(
            [e for e in entities if not e.startswith(("script.", "scene.", "automation."))]
        )

        return json.dumps(
            {
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
            },
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    def search_automations_by_entity(entity_id: str) -> str:
        """
        🚀 REVERSE LOOKUP - Find automations using a given entity.
        Checks if entity is in triggers, conditions, or actions.

        Args:
            entity_id: Entity id (e.g. "binary_sensor.motion").

        Returns:
            JSON with list of automations and usage context (trigger/condition/action).
        """
        data = _load_automations()
        results = []

        for item in data:
            # Fast check: conversion to string
            item_str = str(item)
            if entity_id not in item_str:
                continue

            # Deep check: where is it?
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

        return json.dumps(
            {
                "success": True,
                "entity_id": entity_id,
                "found_in_count": len(results),
                "automations": results,
            },
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    def get_automation_conflicts(entity_id: str) -> str:
        """
        ⚠️ CONFLICT DETECTOR - checks for potential conflicts.
        Detects if a given entity is modified by multiple automations at once
        (which may cause light flickering or unexpected states).

        Args:
            entity_id: Entity to check (e.g. "light.salon").

        Returns:
            JSON with list of automations that control this entity (Action).
        """
        data = _load_automations()
        writers = []  # Automations that control this entity
        readers = []  # Automations that trigger off this entity

        for item in data:
            item_str = str(item)
            if entity_id not in item_str:
                continue

            alias = item.get("alias", "Unnamed")
            mode = item.get("mode", "single")

            # Check Action (Writer)
            if entity_id in str(item.get("action", [])):
                writers.append({"alias": alias, "mode": mode})

            # Check Trigger (Reader)
            if entity_id in str(item.get("trigger", [])):
                readers.append({"alias": alias, "mode": mode})

        potential_loop = len(writers) > 0 and len(readers) > 0
        race_condition = len(writers) > 1

        return json.dumps(
            {
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
                else ["No conflicts detected ✅"],
            },
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    def diagnose_automation(automation_id: str, detail_level: str = "summary") -> str:
        """
        🚀 CONTEXTUALIZED DIAGNOSTICS - Comprehensive automation diagnostics.

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
            data = _load_automations()
            automation = _get_automation_by_id_or_alias(data, automation_id)

            if not automation:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Automation '{automation_id}' not found",
                    },
                    indent=2,
                )

            # Base result structure
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

            # Add full_code only for summary/full (WITHOUT 'id')
            if detail_level in ["summary", "full"]:
                code_item = automation.copy()
                code_item.pop("id", None)
                result["automation_info"]["full_code"] = yaml.dump(
                    code_item,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )

            # Initialize detailed sections only for full
            if detail_level == "full":
                result["blueprint_info"] = None
                result["entity_validation"] = {}
                result["script_validation"] = {}
                result["template_validation"] = []
                result["trigger_analysis"] = []
                result["condition_analysis"] = []
                result["action_analysis"] = []

            # === BLUEPRINT CONTEXT (only for full) ===
            if detail_level == "full" and "use_blueprint" in automation:
                blueprint_path = automation["use_blueprint"].get("path")
                if blueprint_path:
                    blueprint_file = os.path.join(config_path, "blueprints", blueprint_path)
                    if os.path.exists(blueprint_file):
                        try:
                            with open(blueprint_file, "r", encoding="utf-8") as f:
                                blueprint_data = yaml.load(f, Loader=HomeAssistantLoader)
                                result["blueprint_info"] = {
                                    "path": blueprint_path,
                                    "name": blueprint_data.get("blueprint", {}).get("name"),
                                    "description": blueprint_data.get("blueprint", {}).get(
                                        "description"
                                    ),
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
                            result["issues"].append(
                                {
                                    "severity": "error",
                                    "type": "blueprint_load_error",
                                    "message": f"Failed to load blueprint: {str(e)}",
                                }
                            )
                    else:
                        result["issues"].append(
                            {
                                "severity": "error",
                                "type": "blueprint_not_found",
                                "message": f"Blueprint file not found: {blueprint_file}",
                            }
                        )

            # === EXTRACT ALL ENTITIES ===
            entities = set()
            scripts = set()
            scenes = set()
            _extract_entities_recursive(automation, entities)

            # Categorize
            for e in entities:
                if e.startswith("script."):
                    scripts.add(e.replace("script.", ""))
                elif e.startswith("scene."):
                    scenes.add(e.replace("scene.", ""))

            result["statistics"]["total_entities"] = len(entities)

            # === EXTRACT TEMPLATES ===
            templates = _extract_templates(automation)
            result["statistics"]["total_templates"] = len(templates)

            # === VALIDATE ENTITIES (batch optimization) ===
            if ha_url and ha_token:
                # BATCH: Get all states once
                all_states_result = make_ha_request(ha_url, ha_token, "/api/states")

                if all_states_result["success"]:
                    states_dict = {s["entity_id"]: s for s in all_states_result["data"]}

                    entity_issues = []
                    for entity_id in sorted(entities):
                        if entity_id.startswith(("script.", "scene.")):
                            continue  # Skip scripts/scenes for entity validation

                        if entity_id in states_dict:
                            state_data = states_dict[entity_id]

                            if detail_level == "full":
                                result["entity_validation"][entity_id] = {
                                    "exists": True,
                                    "state": state_data["state"],
                                    "friendly_name": state_data.get("attributes", {}).get(
                                        "friendly_name", ""
                                    ),
                                    "device_class": state_data.get("attributes", {}).get(
                                        "device_class"
                                    ),
                                    "last_changed": state_data.get("last_changed"),
                                    "last_updated": state_data.get("last_updated"),
                                }

                            # Check for issues
                            if state_data["state"] == "unavailable":
                                result["statistics"]["unavailable_entities"] += 1
                                entity_issues.append(
                                    {
                                        "severity": "warning",
                                        "type": "entity_unavailable",
                                        "message": f"Entity {entity_id} is unavailable",
                                        "entity_id": entity_id,
                                    }
                                )
                            elif state_data["state"] == "unknown":
                                result["statistics"]["unavailable_entities"] += 1
                                entity_issues.append(
                                    {
                                        "severity": "warning",
                                        "type": "entity_unknown",
                                        "message": f"Entity {entity_id} has unknown state",
                                        "entity_id": entity_id,
                                    }
                                )
                        else:
                            result["statistics"]["missing_entities"] += 1

                            if detail_level == "full":
                                result["entity_validation"][entity_id] = {
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

                    # Add only top 10 entity issues for minimal/summary
                    if detail_level in ["minimal", "summary"]:
                        result["issues"].extend(entity_issues[:10])
                    else:
                        result["issues"].extend(entity_issues)
                else:
                    result["issues"].append(
                        {
                            "severity": "warning",
                            "type": "validation_error",
                            "message": "Failed to fetch entity states from HA API",
                        }
                    )
            else:
                result["issues"].append(
                    {
                        "severity": "info",
                        "type": "validation_skipped",
                        "message": "Entity validation skipped - HA API not configured",
                    }
                )

            # === VALIDATE SCRIPTS (only for full) ===
            if detail_level == "full" and scripts:
                scripts_file = os.path.join(config_path, "scripts.yaml")
                if os.path.exists(scripts_file):
                    try:
                        with open(scripts_file, "r", encoding="utf-8") as f:
                            scripts_data = yaml.load(f, Loader=HomeAssistantLoader) or {}

                        for script_id in scripts:
                            if script_id in scripts_data:
                                result["script_validation"][script_id] = {
                                    "exists": True,
                                    "alias": scripts_data[script_id].get("alias", ""),
                                    "code": yaml.dump(
                                        {script_id: scripts_data[script_id]},
                                        allow_unicode=True,
                                        default_flow_style=False,
                                    ),
                                }
                            else:
                                result["script_validation"][script_id] = {"exists": False}
                                result["issues"].append(
                                    {
                                        "severity": "error",
                                        "type": "script_not_found",
                                        "message": f"Script {script_id} not found in scripts.yaml",
                                        "script_id": script_id,
                                    }
                                )
                    except Exception as e:
                        result["issues"].append(
                            {
                                "severity": "error",
                                "type": "scripts_load_error",
                                "message": f"Failed to load scripts.yaml: {str(e)}",
                            }
                        )

            # === VALIDATE SCENES (only for full) ===
            if detail_level == "full" and scenes:
                scenes_file = os.path.join(config_path, "scenes.yaml")
                if os.path.exists(scenes_file):
                    try:
                        with open(scenes_file, "r", encoding="utf-8") as f:
                            scenes_data = yaml.load(f, Loader=HomeAssistantLoader) or []

                        scene_ids = {scene.get("id"): scene for scene in scenes_data}

                        for scene_id in scenes:
                            if scene_id in scene_ids:
                                result["script_validation"][f"scene.{scene_id}"] = {
                                    "exists": True,
                                    "type": "scene",
                                    "name": scene_ids[scene_id].get("name", ""),
                                }
                            else:
                                result["issues"].append(
                                    {
                                        "severity": "warning",
                                        "type": "scene_not_found",
                                        "message": f"Scene {scene_id} not found in scenes.yaml",
                                        "scene_id": scene_id,
                                    }
                                )
                    except Exception as e:
                        result["issues"].append(
                            {
                                "severity": "warning",
                                "type": "scenes_load_error",
                                "message": f"Failed to load scenes.yaml: {str(e)}",
                            }
                        )

            # === VALIDATE TEMPLATES (batch optimization) ===
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
                        result["statistics"]["invalid_templates"] += 1

                    if detail_level == "full":
                        result["template_validation"].append(
                            {
                                "path": template_info["path"],
                                "template": template_info["template"][:200],
                                "valid": is_valid,
                                "result": str(template_result.get("data"))[:100]
                                if is_valid
                                else None,
                                "error": template_result.get("error") if not is_valid else None,
                            }
                        )

                    if not is_valid:
                        result["issues"].append(
                            {
                                "severity": "error",
                                "type": "template_error",
                                "message": f"Template error at {template_info['path']}: {template_result.get('error')}",
                                "path": template_info["path"],
                                "template": template_info["template"][:100],
                            }
                        )

            # === ANALYZE TRIGGERS ===
            triggers = automation.get("trigger", [])
            if triggers and not isinstance(triggers, list):
                triggers = [triggers]

            result["statistics"]["total_triggers"] = len(triggers)

            if detail_level == "full":
                for idx, trigger in enumerate(triggers):
                    analysis = {
                        "index": idx,
                        "platform": trigger.get("platform"),
                        "config": trigger,
                        "issues": [],
                    }

                    # Platform-specific validation
                    platform = trigger.get("platform")
                    if platform == "state":
                        if "entity_id" not in trigger:
                            issue = f"Trigger {idx}: 'state' platform missing 'entity_id'"
                            analysis["issues"].append(issue)
                            result["issues"].append(
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
                            result["issues"].append(
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
                            result["issues"].append(
                                {
                                    "severity": "error",
                                    "type": "trigger_config_error",
                                    "message": issue,
                                }
                            )
                        if "above" not in trigger and "below" not in trigger:
                            issue = f"Trigger {idx}: 'numeric_state' missing 'above' or 'below'"
                            analysis["issues"].append(issue)
                            result["issues"].append(
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
                            result["issues"].append(
                                {
                                    "severity": "error",
                                    "type": "trigger_config_error",
                                    "message": issue,
                                }
                            )

                    result["trigger_analysis"].append(analysis)

            # Check for homeassistatet start trigger
            has_ha_start = any(
                t.get("platform") == "homeassistant" and t.get("event") == "start" for t in triggers
            )
            if not has_ha_start and len(triggers) > 0:
                result["recommendations"].append(
                    {
                        "priority": "low",
                        "message": "Consider adding 'homeassistant: start' trigger for resilience",
                        "reason": "Ensures automation initializes correctly after HA restart",
                    }
                )

            # === ANALYZE CONDITIONS ===
            conditions = automation.get("condition", [])
            if conditions and not isinstance(conditions, list):
                conditions = [conditions]

            result["statistics"]["total_conditions"] = len(conditions)

            if detail_level == "full":
                for idx, condition in enumerate(conditions):
                    analysis = {
                        "index": idx,
                        "type": condition.get("condition"),
                        "config": condition,
                        "issues": [],
                    }

                    # Validate condition structure
                    cond_type = condition.get("condition")
                    if cond_type == "state" and "entity_id" not in condition:
                        issue = f"Condition {idx}: 'state' condition missing 'entity_id'"
                        analysis["issues"].append(issue)
                        result["issues"].append(
                            {
                                "severity": "error",
                                "type": "condition_config_error",
                                "message": issue,
                            }
                        )
                    elif cond_type == "numeric_state" and "entity_id" not in condition:
                        issue = f"Condition {idx}: 'numeric_state' condition missing 'entity_id'"
                        analysis["issues"].append(issue)
                        result["issues"].append(
                            {
                                "severity": "error",
                                "type": "condition_config_error",
                                "message": issue,
                            }
                        )

                    result["condition_analysis"].append(analysis)

            # === ANALYZE ACTIONS ===
            actions = automation.get("action", [])
            if actions and not isinstance(actions, list):
                actions = [actions]

            result["statistics"]["total_actions"] = len(actions)

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

                    # Validate service calls
                    if action_type == "service_call":
                        service = action.get("service")
                        if not service:
                            issue = f"Action {idx}: service call missing 'service' field"
                            analysis["issues"].append(issue)
                            result["issues"].append(
                                {
                                    "severity": "error",
                                    "type": "action_config_error",
                                    "message": issue,
                                }
                            )

                    result["action_analysis"].append(analysis)
            else:
                # Quick check for variables position even in minimal/summary
                for idx, action in enumerate(actions):
                    if "variables" in action:
                        has_variables = True
                        variables_index = idx
                        break

            # Check if variables are at the beginning
            if has_variables and variables_index > 0:
                result["recommendations"].append(
                    {
                        "priority": "medium",
                        "message": "Move 'variables' block to the beginning of actions",
                        "reason": "Best practice: define all variables before using them",
                    }
                )

            # === RECOMMENDATIONS ===
            if not automation.get("mode"):
                result["recommendations"].append(
                    {
                        "priority": "medium",
                        "message": "Add 'mode' parameter (single/restart/queued/parallel)",
                        "reason": "Explicit mode prevents unexpected behavior with multiple triggers",
                    }
                )

            if len(triggers) > 3:
                result["recommendations"].append(
                    {
                        "priority": "low",
                        "message": "Consider splitting into separate automations",
                        "reason": f"Automation has {len(triggers)} triggers - simpler automations are easier to maintain",
                    }
                )

            if result["statistics"]["missing_entities"] > 0:
                result["recommendations"].append(
                    {
                        "priority": "high",
                        "message": f"Fix {result['statistics']['missing_entities']} missing entities",
                        "reason": "Missing entities will cause automation failures",
                    }
                )

            if result["statistics"]["unavailable_entities"] > 0:
                result["recommendations"].append(
                    {
                        "priority": "medium",
                        "message": f"Fix {result['statistics']['unavailable_entities']} unavailable/unknown entities",
                        "reason": "Unavailable entities may cause unexpected behavior",
                    }
                )

            if result["statistics"]["invalid_templates"] > 0:
                result["recommendations"].append(
                    {
                        "priority": "high",
                        "message": f"Fix {result['statistics']['invalid_templates']} invalid templates",
                        "reason": "Template errors will prevent automation from working",
                    }
                )

            if len(templates) > 5 and not has_variables:
                result["recommendations"].append(
                    {
                        "priority": "medium",
                        "message": "Consider moving complex logic to 'variables' block",
                        "reason": "Improves readability and performance",
                    }
                )

            if automation.get("mode") == "single" and len(triggers) > 1:
                result["recommendations"].append(
                    {
                        "priority": "low",
                        "message": "Multiple triggers with 'single' mode - consider 'restart' or 'queued'",
                        "reason": "Single mode may skip executions if automation is already running",
                    }
                )

            # Check for notification best practices
            has_notifications = any(
                action.get("service", "").startswith("notify.") for action in actions
            )
            if has_notifications and automation.get("mode") != "single":
                result["recommendations"].append(
                    {
                        "priority": "low",
                        "message": "Notification automations should use mode: single",
                        "reason": "Prevents notification spam",
                    }
                )

            if not result["recommendations"]:
                result["recommendations"].append(
                    {"priority": "info", "message": "Automation looks healthy! ✅"}
                )

            # Sort recommendations by priority
            priority_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
            result["recommendations"].sort(
                key=lambda x: priority_order.get(x.get("priority", "info"), 3)
            )

            # Sort issues by severity
            severity_order = {"error": 0, "warning": 1, "info": 2}
            result["issues"].sort(key=lambda x: severity_order.get(x.get("severity", "info"), 2))

            return json.dumps(result, indent=2, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    def get_automation_usage_stats(automation_id: str, hours_back: int = 24) -> str:
        """
        🚀 get_automation_usage_stats()

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
            if not ha_url or not ha_token:
                return json.dumps(
                    {
                        "success": False,
                        "error": "HA API not configured (ha_url/ha_token missing)",
                    },
                    indent=2,
                )

            data = _load_automations()
            automation = _get_automation_by_id_or_alias(data, automation_id)

            if not automation:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Automation '{automation_id}' not found in automations.yaml",
                    },
                    indent=2,
                )

            # Derive automation entity_id
            auto_id = automation.get("id") or automation.get("alias") or "no_id"
            slug = re.sub(r"[^a-z0-9_]+", "_", str(auto_id).lower()).strip("_")
            entity_id = f"automation.{slug}"

            # 2) Check current automation state (whether it exists and is enabled)
            state_result = make_ha_request(ha_url, ha_token, f"/api/states/{entity_id}")
            ha_state = state_result["data"] if state_result["success"] else None

            # 3) Extract all entities from definition
            entities = set()
            _extract_entities_recursive(automation, entities)

            # 4) Check which entities no longer exist in HA (BATCH)
            missing_entities = []
            if entities:
                all_states_result = make_ha_request(ha_url, ha_token, "/api/states")
                if all_states_result["success"]:
                    states_dict = {s["entity_id"]: s for s in all_states_result["data"]}
                    for eid in sorted(entities):
                        if eid not in states_dict:
                            missing_entities.append(eid)

            # 5) From history: run count + last run
            start_time = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()
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
                    # Heuristic: transition from "off" to "on" (or similar) counts as a run
                    if (
                        prev_state is not None
                        and prev_state in ("off", "idle")
                        and state_val in ("on", "triggered")
                    ):
                        run_count += 1
                        last_run = last_changed or last_run
                    prev_state = state_val

                # Fallback: if nothing was counted, but there is a single "triggered" – take the last state
                if run_count == 0 and series:
                    for point in reversed(series):
                        if point.get("state") in ("on", "triggered"):
                            last_run = point.get("last_changed") or point.get("last_updated")
                            break

            # 6) last_triggered from current state (often more accurate than history)
            last_triggered_attr = None
            is_enabled = None
            current_state = None
            if ha_state:
                attrs = ha_state.get("attributes", {})
                current_state = ha_state.get("state")
                last_triggered_attr = attrs.get("last_triggered")
                is_enabled = current_state != "off"

                # If last_triggered attribute is newer than from history – overwrite
                def _parse_dt(v):
                    if not v:
                        return None
                    try:
                        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                    except Exception:
                        return None

                dt_hist = _parse_dt(last_run)
                dt_attr = _parse_dt(last_triggered_attr)

                if dt_attr and (not dt_hist or dt_attr > dt_hist):
                    last_run = last_triggered_attr

            # 7) is_working – heuristically:
            # - automation entity exists
            # - is enabled (state != "off")
            # - and in the given time window there was at least 1 change / last_triggered
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
                    "alias": automation.get("alias"),
                    "entity_id": entity_id,
                    "mode": automation.get("mode", "single"),
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

            return json.dumps(response, indent=2, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    async def automation_validate_triggers(
        automation_id: str = None, automation_alias: str = None
    ) -> str:
        """
        Validate that all trigger ids in an automation have corresponding handlers.

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
            # Load automations
            automations = _load_automations()

            if not automations:
                return json.dumps({"success": False, "error": "No automations found"}, indent=2)

            # Find the automation
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
                return json.dumps(
                    {
                        "success": False,
                        "error": "Automation not found",
                        "provided_id": automation_id,
                        "provided_alias": automation_alias,
                        "available_automations": available[:20],
                    },
                    indent=2,
                )

            # Extract triggers and their ids
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

                        # Check for duplicates
                        if (
                            sum(
                                1
                                for t in triggers
                                if isinstance(t, dict) and t.get("id") == trigger_id
                            )
                            > 1
                        ):
                            trigger_info["duplicate"] = True

                    trigger_details.append(trigger_info)

            # Extract all actions and look for trigger id references
            actions = automation.get("action", [])

            handlers_found = set()
            handler_locations = []

            def _scan_for_trigger_handlers(obj, path=""):
                """Recursively scan for trigger id handlers."""
                if isinstance(obj, dict):
                    # Check for choose with conditions
                    if "choose" in obj:
                        for i, choice in enumerate(obj["choose"]):
                            if isinstance(choice, dict):
                                conditions = choice.get("conditions", [])
                                if isinstance(conditions, dict):
                                    conditions = [conditions]

                                for cond in conditions:
                                    if (
                                        isinstance(cond, dict)
                                        and cond.get("condition") == "trigger"
                                    ):
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

                    # Check for if/then with trigger conditions
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

                    # Check for parallel with trigger conditions
                    if "parallel" in obj:
                        parallel_items = obj["parallel"]
                        if isinstance(parallel_items, dict):
                            parallel_items = [parallel_items]

                        for i, item in enumerate(parallel_items):
                            if isinstance(item, dict):
                                _scan_for_trigger_handlers(item, f"{path}.parallel[{i}]")

                    # Recurse into other keys
                    for key, value in obj.items():
                        if key not in ["choose", "if", "parallel"]:
                            _scan_for_trigger_handlers(value, f"{path}.{key}")

                elif isinstance(obj, list):
                    for i, item in enumerate(obj):
                        _scan_for_trigger_handlers(item, f"{path}[{i}]")

            _scan_for_trigger_handlers(actions, "action")

            # Analyze results
            orphaned_triggers = trigger_ids - handlers_found
            missing_handlers = handlers_found - trigger_ids

            # Build recommendations
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

            # Check for triggers without ids that might need them
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

            # Check for duplicate ids
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

            return json.dumps(
                {
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
                },
                indent=2,
                ensure_ascii=False,
            )

        except Exception as e:
            import traceback

            return json.dumps(
                {
                    "success": False,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                },
                indent=2,
            )
