"""Unified entity and service extraction logic for HA Semantic Graph.

Adapted from context_generator/utils.py and tools/entity_dependencies.py
to avoid circular imports. This module is self-contained — no imports from
context_generator/ or tools/.

Enhancements over original extraction functions:
- Word-boundary entity regex
- expand() detection in Jinja templates
- Confidence scoring (inferred / dynamic)
- Dynamic Jinja patterns detected and filtered
"""

from __future__ import annotations

import re
from typing import Any

# =========================================================================
# COMPILED REGEX PATTERNS
# =========================================================================

# Word-boundary entity pattern matching domain.entity_id across
# all Home Assistant domain prefixes. Used to find entity references
# in plain text, YAML config values, and template strings.
ENTITY_PATTERN = re.compile(
    r"\b(?:"
    + "|".join(
        [
            "alarm_control_panel",
            "automation",
            "binary_sensor",
            "button",
            "calendar",
            "camera",
            "climate",
            "conversation",
            "counter",
            "cover",
            "date",
            "datetime",
            "device_tracker",
            "fan",
            "group",
            "humidifier",
            "image",
            "input_boolean",
            "input_button",
            "input_datetime",
            "input_number",
            "input_select",
            "input_text",
            "light",
            "lock",
            "media_player",
            "notify",
            "number",
            "person",
            "remote",
            "scene",
            "script",
            "select",
            "sensor",
            "stt",
            "sun",
            "switch",
            "text",
            "time",
            "timer",
            "todo",
            "tts",
            "update",
            "vacuum",
            "water_heater",
            "weather",
            "zone",
        ]
    )
    + r")\.[a-zA-Z0-9_-]+\b"
)

# Static Jinja2 entity extraction pattern.
# Extracts literal entity IDs from function calls like:
#   states('sensor.temperature'), is_state('light.kitchen', 'on'),
#   expand('group.lights'), area_entities('bedroom'), etc.
# Also catches states.dot syntax like states.sensor.temperature.
TEMPLATE_ENTITY_PATTERN = re.compile(
    r"(?:states|is_state|state_attr|is_state_attr|expand|has_value"
    r"|area_entities|device_entities|label_entities|integration_entities"
    r"|states\.)\s*\(\s*['\"]([a-zA-Z_]+\.[a-zA-Z0-9_]+)['\"]"
)

# Alternative pattern for states.dot syntax:
#   states.sensor.temperature  →  "sensor.temperature"
STATES_DOT_PATTERN = re.compile(r"states\.([a-zA-Z_]+\.[a-zA-Z0-9_]+)")

# Dynamic Jinja2 template pattern — detects string concatenation (~)
# used to build entity IDs at runtime. These cannot be resolved statically.
# Example: "sensor." ~ some_var  or  states(some_var)
# Any template containing the ~ operator in a function argument position
# is flagged as dynamic and its entity references are NOT returned.
DYNAMIC_TEMPLATE_PATTERN = re.compile(
    r"(?:states|is_state|state_attr|expand|area_entities|device_entities)"
    r"\s*\(\s*(?:.*?['\"]\s*~|~.*?['\"]|[^'\"]*~[^'\"]*)"
)

# Expand() call detection — captures the group/entity argument even
# when used with Jinja concatenation.
EXPAND_PATTERN = re.compile(
    r"expand\s*\(\s*['\"]([a-zA-Z_]+\.[a-zA-Z0-9_]+)['\"]"
)


# =========================================================================
# ENTITY EXTRACTION FROM TEMPLATES
# =========================================================================


def extract_entities_from_template(template_str: str) -> list[tuple[str, str]]:
    """Extract entity references from a Jinja2 template string.

    Handles static references from:
    - states('sensor.x'), is_state('light.y', 'on')
    - state_attr('climate.z', 'temperature')
    - expand('group.lights')
    - states.sensor.temperature (dot syntax)
    - area_entities('office'), device_entities('...')

    Dynamic references (using ``~`` concatenation) are detected via
    :data:`DYNAMIC_TEMPLATE_PATTERN` and **excluded** from results
    since the entity ID cannot be resolved statically.

    Args:
        template_str: Raw Jinja2 template text (may contain ``{{ }}`` markers
            or be the inner expression).

    Returns:
        List of ``(entity_id, confidence)`` tuples. Static references
        receive ``confidence="inferred"``. Dynamic references are
        filtered out entirely — use :func:`has_dynamic_template_refs`
        to check if a template contains unresolved dynamic patterns.
    """
    if not template_str or not isinstance(template_str, str):
        return []

    results: list[tuple[str, str]] = []

    # Pattern 1: function-call entity extraction
    #   states('sensor.x') → sensor.x
    for match in TEMPLATE_ENTITY_PATTERN.finditer(template_str):
        entity_id = match.group(1)
        results.append((entity_id, "inferred"))

    # Pattern 2: states.dot syntax
    #   states.sensor.temperature → sensor.temperature
    for match in STATES_DOT_PATTERN.finditer(template_str):
        entity_id = match.group(1)
        results.append((entity_id, "inferred"))

    # Pattern 3: expand() with literal argument
    #   expand('group.living_room_lights') → group.living_room_lights
    for match in EXPAND_PATTERN.finditer(template_str):
        entity_id = match.group(1)
        if (entity_id, "inferred") not in results:
            results.append((entity_id, "inferred"))

    # Pattern 4: bare entity references (fallback)
    #   {{ light.kitchen }} or {{ sensor.temperature | float }}
    for entity_id in ENTITY_PATTERN.findall(template_str):
        if (entity_id, "inferred") not in results:
            results.append((entity_id, "inferred"))

    return results


def has_dynamic_template_refs(template_str: str) -> bool:
    """Check whether a Jinja2 template contains dynamic entity references.

    Dynamic references use Jinja's ``~`` (string concatenation) operator
    to build entity IDs at runtime, e.g. ``states('sensor.' ~ variable)``.
    These cannot be resolved to a static entity ID.

    Args:
        template_str: Raw Jinja2 template text.

    Returns:
        ``True`` if the template contains dynamic entity references
        that cannot be statically resolved.
    """
    if not template_str or not isinstance(template_str, str):
        return False
    return bool(DYNAMIC_TEMPLATE_PATTERN.search(template_str))


# =========================================================================
# ENTITY EXTRACTION FROM STRUCTURED DATA
# =========================================================================


def extract_entities_from_data(
    data: Any,
    extract_from_templates: bool = True,
) -> set[str]:
    """Recursively extract entity IDs from nested YAML/dict/list structures.

    Understands entity-bearing keys (``entity_id``, ``entity``, ``target``,
    ``scene``), template expressions (``{{ }}`` / ``{% %}``), and deeply
    nested automations/scripts/dashboards.

    Args:
        data: A string, dict, list, or other value to scan.
        extract_from_templates: If ``True`` (default), also extract
            entity references from Jinja2 template expressions found
            in string values.

    Returns:
        Set of entity ID strings (e.g. ``{"light.kitchen", "sensor.temp"}``).
    """
    found: set[str] = set()

    if isinstance(data, dict):
        for key, value in data.items():
            # Special keys known to carry entity IDs
            if key in ("entity_id", "entity", "target", "scene"):
                if isinstance(value, str):
                    if "." in value and not value.startswith("!"):
                        found.add(value)
                    found.update(ENTITY_PATTERN.findall(value))
                elif isinstance(value, list):
                    for v in value:
                        if isinstance(v, str) and "." in v:
                            found.add(v)
                elif isinstance(value, dict):
                    # target: { entity_id: "light.x" } or { entity_id: [...] }
                    if "entity_id" in value:
                        eid = value["entity_id"]
                        if isinstance(eid, str):
                            found.add(eid)
                        elif isinstance(eid, list):
                            found.update(e for e in eid if isinstance(e, str))

            # Template expressions in string values
            if (
                extract_from_templates
                and isinstance(value, str)
                and ("{{" in value or "{%" in value)
            ):
                for entity_id, _confidence in extract_entities_from_template(value):
                    found.add(entity_id)

            # Recurse into nested structures
            found.update(
                extract_entities_from_data(value, extract_from_templates)
            )

    elif isinstance(data, list):
        for item in data:
            found.update(
                extract_entities_from_data(item, extract_from_templates)
            )

    elif isinstance(data, str):
        # Scan plain strings for entity IDs
        found.update(ENTITY_PATTERN.findall(data))
        # Also scan for template expressions in strings
        if extract_from_templates and ("{{" in data or "{%" in data):
            for entity_id, _confidence in extract_entities_from_template(data):
                found.add(entity_id)

    return found


# =========================================================================
# TRIGGER INFO EXTRACTION
# =========================================================================


def extract_trigger_info(triggers: list[dict]) -> list[tuple[str, str]]:
    """Extract ``(entity_id, platform)`` pairs from automation triggers.

    Handles trigger types: state, numeric_state, template, event, zone,
    device, time_pattern (no entity), and sun (no entity).

    Args:
        triggers: List of trigger dicts from an automation configuration.

    Returns:
        List of ``(entity_id, trigger_platform)`` tuples ordered as they
        appear in the trigger list.
    """
    results: list[tuple[str, str]] = []

    if not isinstance(triggers, list):
        return results

    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue

        platform = trigger.get("platform", trigger.get("trigger", "unknown"))

        # Direct entity_id on most triggers
        for key in ("entity_id",):
            eid = trigger.get(key)
            if isinstance(eid, str):
                results.append((eid, platform))
            elif isinstance(eid, list):
                for e in eid:
                    if isinstance(e, str):
                        results.append((e, platform))

        # Template triggers: extract from value_template
        if platform == "template":
            value_template = trigger.get("value_template", "")
            if value_template:
                for entity_id, _confidence in extract_entities_from_template(
                    value_template
                ):
                    results.append((entity_id, platform))

        # numeric_state triggers: also check value_template
        if platform == "numeric_state":
            value_template = trigger.get("value_template", "")
            if value_template:
                for entity_id, _confidence in extract_entities_from_template(
                    value_template
                ):
                    results.append((entity_id, platform))

        # event triggers: extract from event_data
        if platform == "event" and "event_data" in trigger:
            for entity_id in extract_entities_from_data(trigger["event_data"]):
                results.append((entity_id, platform))

        # zone triggers: include zone entity
        if platform == "zone":
            zone_id = trigger.get("zone")
            if isinstance(zone_id, str):
                results.append((zone_id, platform))

        # device triggers resolve to the entity_id
        if platform == "device":
            device_id = trigger.get("device_id")
            if device_id:
                results.append((device_id, platform))

    return results


# =========================================================================
# SERVICE EXTRACTION
# =========================================================================


def extract_services(actions: list[dict]) -> set[str]:
    """Extract service names from automation or script action sequences.

    Recursively walks ``sequence``, ``choose`` branches, ``then``/``else``,
    ``parallel``, and ``repeat`` structures.

    Args:
        actions: List of action dicts (e.g. ``automation["action"]``
            or ``script["sequence"]``).

    Returns:
        Set of service name strings (e.g. ``{"light.turn_on", "notify.mobile"}``).
    """
    services: set[str] = set()

    def _walk(items: Any) -> None:
        if not items:
            return
        for item in items if isinstance(items, list) else [items]:
            if not isinstance(item, dict):
                continue
            # Service / action key
            for service_key in ("service", "action"):
                svc = item.get(service_key)
                if isinstance(svc, str) and "." in svc:
                    services.add(svc)

            # Recurse into nested action structures
            _walk(item.get("sequence", []))
            _walk(item.get("then", []))
            _walk(item.get("else", []))
            for branch in item.get("choose", []):
                if isinstance(branch, dict):
                    _walk(branch.get("sequence", []))
            for default_item in item.get("default", []):
                _walk(
                    [default_item]
                    if isinstance(default_item, dict)
                    else default_item
                )
            # parallel and repeat branches
            _walk(item.get("parallel", []))
            repeat_seq = item.get("repeat", {})
            if isinstance(repeat_seq, dict):
                _walk(repeat_seq.get("sequence", []))

    _walk(actions)
    return services


# =========================================================================
# CONTROLLED ENTITY EXTRACTION
# =========================================================================


def extract_controlled_entities(actions: list[dict]) -> set[str]:
    """Extract entity IDs targeted by service calls in automation/script actions.

    Inspects ``target.entity_id``, ``data.entity_id``, scene activations,
    and recursively walks nested action structures (choose, repeat, parallel).

    Args:
        actions: List of action dicts from an automation or script.

    Returns:
        Set of entity ID strings that are the target of service calls
        or scene activations within the action sequence.
    """
    entities: set[str] = set()

    def _walk(items: Any) -> None:
        if not items:
            return
        for item in items if isinstance(items, list) else [items]:
            if not isinstance(item, dict):
                continue

            # target.entity_id (primary pattern)
            target = item.get("target", {}) or {}
            if isinstance(target, dict):
                teid = target.get("entity_id")
                if isinstance(teid, str):
                    entities.add(teid)
                elif isinstance(teid, list):
                    entities.update(teid)

            # data.entity_id (secondary pattern, e.g. scene.create)
            data = item.get("data", {}) or {}
            if isinstance(data, dict):
                for ref_key in ("entity_id",):
                    eid = data.get(ref_key)
                    if isinstance(eid, str):
                        entities.add(eid)

            # scene activation
            scene_id = item.get("scene")
            if isinstance(scene_id, str):
                entities.add(scene_id)

            # Recurse into nested action structures
            _walk(item.get("sequence", []))
            _walk(item.get("then", []))
            _walk(item.get("else", []))
            for branch in item.get("choose", []):
                if isinstance(branch, dict):
                    _walk(branch.get("sequence", []))
            for default_item in item.get("default", []):
                _walk(
                    [default_item]
                    if isinstance(default_item, dict)
                    else default_item
                )
            _walk(item.get("parallel", []))
            repeat_seq = item.get("repeat", {})
            if isinstance(repeat_seq, dict):
                _walk(repeat_seq.get("sequence", []))

    _walk(actions)
    return entities
