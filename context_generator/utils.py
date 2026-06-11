"""Utility functions for context generation."""

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml

from . import constants

_logger = logging.getLogger(__name__)

# --- REGISTRY CACHE (based on conftest.py) ---
_registry_cache: dict[str, dict] = {}
_registry_cache_timestamps: dict[str, float] = {}
CACHE_TTL = 300  # 5 minutes
_CACHE_LOCK = threading.Lock()

BLOCKED_REGISTRIES = frozenset(
    {
        "auth",
        "auth_provider.homeassistant",
        "onboarding",
    }
)
"""Registry names that must never be loaded -- contain credentials."""

_CACHE_STATS: dict[str, int] = {"hits": 0, "misses": 0, "blocked": 0, "total": 0}


def invalidate_registry_cache():
    """Clears registry cache."""
    global _registry_cache, _registry_cache_timestamps
    with _CACHE_LOCK:
        _registry_cache.clear()
        _registry_cache_timestamps.clear()


def get_cache_stats() -> dict[str, int | float | list[str]]:
    """
    Returns cache statistics with calculated fields.

    Args:
        None

    Returns:
        dict with keys: hits, misses, blocked, total, hit_rate_percent, cached_keys
    """
    with _CACHE_LOCK:
        stats = dict(_CACHE_STATS)
        stats["hit_rate_percent"] = round(
            (stats["hits"] / stats["total"] * 100) if stats["total"] > 0 else 0, 1
        )
        stats["cached_keys"] = list(_registry_cache.keys())
    return stats


def load_registry(name: str, use_cache: bool = True) -> dict:
    """
    Loads file from .storage/ with caching.
    Based on test_utils.py load_registry.
    """
    global _registry_cache, _registry_cache_timestamps

    with _CACHE_LOCK:
        _CACHE_STATS["total"] += 1

    if name in BLOCKED_REGISTRIES or any(name.startswith(prefix) for prefix in ("auth_provider.",)):
        with _CACHE_LOCK:
            _CACHE_STATS["blocked"] += 1
        return {}

    cache_key = f"{constants.HA_CONFIG_PATH}:{name}"
    now = datetime.now().timestamp()

    # Check cache
    with _CACHE_LOCK:
        if use_cache and cache_key in _registry_cache:
            if now - _registry_cache_timestamps.get(cache_key, 0) < CACHE_TTL:
                _CACHE_STATS["hits"] += 1
                return _registry_cache[cache_key]

    with _CACHE_LOCK:
        _CACHE_STATS["misses"] += 1

    try:
        path = Path(constants.HA_CONFIG_PATH) / ".storage" / name
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                with _CACHE_LOCK:
                    _registry_cache[cache_key] = data
                    _registry_cache_timestamps[cache_key] = now
                return data
    except Exception as e:
        _logger.warning("Error loading registry %s: %s", name, e)

    return {}


def make_ha_request(
    endpoint: str, method: str = "GET", data: Any = None, timeout: int = 15
) -> dict[str, Any]:
    """
    Executes request to HA API with retry.
    Based on test_utils.py make_ha_request.
    """
    headers = {
        "Authorization": f"Bearer {constants.HA_TOKEN}",
        "Content-Type": "application/json",
    }

    for attempt in range(3):
        try:
            if method == "GET":
                response = requests.get(
                    f"{constants.HA_URL}{endpoint}", headers=headers, timeout=timeout
                )
            elif method == "POST":
                response = requests.post(
                    f"{constants.HA_URL}{endpoint}", headers=headers, json=data, timeout=timeout
                )
            else:
                return {"success": False, "error": f"Unsupported method: {method}"}

            response.raise_for_status()
            return {"success": True, "data": response.json()}

        except requests.exceptions.HTTPError as e:
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {str(e)}",
            }
        except requests.exceptions.Timeout:
            if attempt < 2:
                continue
            return {"success": False, "error": "Request timeout after 3 attempts"}
        except Exception as e:
            if attempt < 2:
                continue
            return {"success": False, "error": str(e)}

    return {"success": False, "error": "Max retries exceeded"}


def load_yaml_file(filepath: str) -> Any:
    """Loads YAML file with error handling."""
    path = Path(filepath) if os.path.isabs(filepath) else Path(constants.HA_CONFIG_PATH) / filepath
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.load(f, Loader=constants.HomeAssistantLoader)
    except Exception as e:
        _logger.warning("YAML error %s: %s", filepath, e)
        return None


def validate_yaml_syntax(yaml_content: str) -> dict[str, Any]:
    """
    Validates YAML syntax.
    Based on test_config.py validate_yaml_syntax.
    """
    try:
        yaml.load(yaml_content, Loader=constants.HomeAssistantLoader)
        return {"syntax_valid": True, "issues": []}
    except yaml.YAMLError as e:
        return {"syntax_valid": False, "error": f"YAML syntax error: {str(e)}"}


def is_ignorable_entity(entity_id: str) -> bool:
    """Checks if entity should be ignored."""
    domain = entity_id.split(".")[0]
    if domain in constants.IGNORABLE_DOMAINS:
        return True
    for pattern in constants.IGNORABLE_PATTERNS:
        if re.match(pattern, entity_id):
            return True
    return False


def slugify(text: str) -> str:
    """Converts text to slug."""
    if not text:
        return "unknown"
    slug = text.lower().replace(" ", "_").replace("-", "_")
    slug = re.sub(r"[^a-z0-9_]", "", slug)
    return slug.strip("_") or "unknown"


def get_best_name(item: dict, item_type: str = "entity") -> str:
    """
    Fetches best name for entities/devices.
    Based on test_utils.py get_best_name.
    """
    if item_type == "device":
        return item.get("name_by_user") or item.get("name") or "Unknown Device"
    else:  # entity
        return item.get("name") or item.get("original_name") or item.get("entity_id", "Unknown")


def resolve_area_id(entity: dict, device_map: dict) -> str | None:
    """
    Resolve area for entity - first from entity, then from devices.
    FIXED version based on test_utils.py.

    Returns area_id or None.
    """
    # 1. Direct area_id from entity (may be None or empty string)
    entity_area = entity.get("area_id")
    if entity_area:  # Not None and not empty string
        return entity_area

    # 2. Area from devices (fallback)
    device_id = entity.get("device_id")
    if device_id and device_id in device_map:
        device = device_map[device_id]
        device_area = device.get("area_id")
        if device_area:  # Not None and not empty string
            return device_area

    return None


def extract_entities_from_template(template_str: str) -> set[str]:
    """
    Extracts entity_id from Jinja2 templates.
    Handles: states('sensor.x'), is_state('sensor.x', 'on'), state_attr(), states.sensor.x
    """
    found = set()

    if not isinstance(template_str, str):
        return found

    # Pattern 1: states('entity_id'), is_state('entity_id', ...), etc.
    found.update(constants.TEMPLATE_ENTITY_PATTERN.findall(template_str))

    # Pattern 2: states.domain.name
    for match in constants.STATES_DOT_PATTERN.findall(template_str):
        # match = "sensor.temperature" (without "states.")
        found.add(match)

    # Pattern 3: standard entity_id pattern as fallback
    found.update(constants.ENTITY_PATTERN.findall(template_str))

    return found


def extract_entities_from_data(data: Any, extract_from_templates: bool = True) -> set[str]:
    """
    Recursively extracts entity_id from structure.
    EXTENDED version with template support.
    """
    found = set()

    if isinstance(data, dict):
        for key, value in data.items():
            # Special keys with entity_id
            if key in ["entity_id", "entity", "target", "scene"]:
                if isinstance(value, str):
                    if "." in value and not value.startswith("!"):
                        found.add(value)
                    found.update(constants.ENTITY_PATTERN.findall(value))
                elif isinstance(value, list):
                    for v in value:
                        if isinstance(v, str) and "." in v:
                            found.add(v)
                elif isinstance(value, dict):
                    # target: {entity_id: [...]}
                    if "entity_id" in value:
                        eid = value["entity_id"]
                        if isinstance(eid, str):
                            found.add(eid)
                        elif isinstance(eid, list):
                            found.update(e for e in eid if isinstance(e, str))

            # Search in templates
            if (
                extract_from_templates
                and isinstance(value, str)
                and ("{{" in value or "{%" in value)
            ):
                found.update(extract_entities_from_template(value))

            # Recursively
            found.update(extract_entities_from_data(value, extract_from_templates))

    elif isinstance(data, list):
        for item in data:
            found.update(extract_entities_from_data(item, extract_from_templates))

    elif isinstance(data, str):
        # Search for entity_id in string
        found.update(constants.ENTITY_PATTERN.findall(data))
        # And in templates
        if extract_from_templates and ("{{" in data or "{%" in data):
            found.update(extract_entities_from_template(data))

    return found


def extract_trigger_info(triggers: Any) -> tuple[set[str], list[str]]:
    """
    Extracts entities and platforms from triggers.
    EXTENDED version with full template trigger support.
    Based on test_automations.py.
    """
    entities = set()
    platforms = []

    if isinstance(triggers, dict):
        triggers = [triggers]

    if not isinstance(triggers, list):
        return entities, platforms

    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue

        platform = trigger.get("platform", trigger.get("trigger", "unknown"))
        platforms.append(platform)

        # Extract entity_id from trigger
        if "entity_id" in trigger:
            eid = trigger["entity_id"]
            if isinstance(eid, str):
                entities.add(eid)
            elif isinstance(eid, list):
                entities.update(e for e in eid if isinstance(e, str))

        # Template triggers - NEW LOGIC
        if platform == "template":
            value_template = trigger.get("value_template", "")
            entities.update(extract_entities_from_template(value_template))

        # Numeric state triggers
        if platform == "numeric_state":
            if "entity_id" in trigger:
                eid = trigger["entity_id"]
                if isinstance(eid, str):
                    entities.add(eid)
                elif isinstance(eid, list):
                    entities.update(eid)
            # value_template in numeric_state
            if "value_template" in trigger:
                entities.update(extract_entities_from_template(trigger["value_template"]))

        # Event triggers
        if platform == "event" and "event_data" in trigger:
            entities.update(extract_entities_from_data(trigger["event_data"]))

        # Zone triggers
        if platform == "zone":
            if "entity_id" in trigger:
                entities.add(trigger["entity_id"])
            if "zone" in trigger:
                entities.add(trigger["zone"])

        # Device triggers
        if platform == "device" and "entity_id" in trigger:
            entities.add(trigger["entity_id"])

    return entities, platforms


def extract_services(data: Any) -> set[str]:
    """Extracts service calls from actions."""
    services = set()

    if isinstance(data, dict):
        # Various service call formats
        service = data.get("service") or data.get("action")
        if isinstance(service, str) and "." in service:
            services.add(service)
        for value in data.values():
            services.update(extract_services(value))
    elif isinstance(data, list):
        for item in data:
            services.update(extract_services(item))

    return services


def extract_controlled_entities(actions: Any) -> set[str]:
    """
    Extracts entities controlled by actions (service targets).
    Based on test_entity_dependencies.py.
    """
    controlled = set()

    if isinstance(actions, dict):
        # Service call with target
        if "service" in actions or "action" in actions:
            target = actions.get("target", {})
            data = actions.get("data", {})

            # entity_id in target or data
            for source in [target, data, actions]:
                if "entity_id" in source:
                    eid = source["entity_id"]
                    if isinstance(eid, str):
                        controlled.add(eid)
                    elif isinstance(eid, list):
                        controlled.update(e for e in eid if isinstance(e, str))

        # Scene activation
        if "scene" in actions:
            scene_id = actions["scene"]
            if isinstance(scene_id, str):
                controlled.add(scene_id)

        # Recursively for choose, if, repeat, parallel, etc.
        for key in [
            "sequence",
            "choose",
            "default",
            "then",
            "else",
            "repeat",
            "parallel",
        ]:
            if key in actions:
                controlled.update(extract_controlled_entities(actions[key]))

        # Options in choose
        if "choose" in actions and isinstance(actions["choose"], list):
            for option in actions["choose"]:
                if isinstance(option, dict):
                    controlled.update(extract_controlled_entities(option.get("sequence", [])))

        for value in actions.values():
            if isinstance(value, (dict, list)):
                controlled.update(extract_controlled_entities(value))

    elif isinstance(actions, list):
        for item in actions:
            controlled.update(extract_controlled_entities(item))

    return controlled


# --- MAIN COLLECTOR CLASSES ---
