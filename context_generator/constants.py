#!/usr/bin/env python3
"""
Home Assistant Context Generator for AI (V7 - Full MCP Integration)

Fixes compared to V6 (based on MCP test analysis):
1. Area resolution - correct fallback when area_id is null (test_utils.py)
2. Template trigger parsing - entity extraction from {{ states('sensor.x') }} (test_automations.py)
3. Ghost entity detection - cross-referencing with dashboards, correct ignoring (test_storage.py)
4. Conflict detection - extended to scenes and scripts (test_automations.py)
5. Template entities - full attributes + YAML validation (test_config.py)
6. Integration status - status from API + config entries (test_config_entries.py)
7. Dashboard parsing - more custom cards + source file list
8. Log analysis - categorization per component + recommendations (test_real_ha.py)
9. Entity history - recent changes (test_history.py)
10. Blueprints - full usage analysis (test_blueprints.py)
11. Registry cache - optimization as in conftest.py
"""

import os
import re

import yaml

# --- configuration ---
HA_URL = os.getenv("HA_URL", "http://homeassistant:8123")
HA_TOKEN = os.getenv("HA_TOKEN", "")
HA_CONFIG_PATH = os.getenv("HA_CONFIG_PATH", "/config")
OUTPUT_FILE = "ha-ai-context.md"
LOG_HOURS_BACK = 24

# --- CONSTANTS AND PATTERNS ---
ENTITY_PATTERN = re.compile(
    r"\b(sensor|binary_sensor|light|switch|climate|cover|input_\w+|automation|script|"
    r"person|device_tracker|media_player|camera|lock|fan|vacuum|weather|sun|zone|"
    r"timer|counter|number|select|button|scene|group|alarm_control_panel|update|"
    r"calendar|todo|image|stt|tts|conversation|notify|remote|water_heater|humidifier)\.[a-zA-Z0-9_\-]+\b"
)

# Pattern for Jinja2 templates - extracts entities from states(), is_state(), etc.
TEMPLATE_ENTITY_PATTERN = re.compile(
    r"(?:states\(|is_state\(|state_attr\(|is_state_attr\()"
    r"['\"]([a-zA-Z_]+\.[a-zA-Z0-9_]+)['\"]"
)

# Alternative pattern for states.sensor.xxx
STATES_DOT_PATTERN = re.compile(r"states\.([a-zA-Z_]+\.[a-zA-Z0-9_]+)")

# Domains to ignore in unavailable reports
IGNORABLE_DOMAINS = {
    "sun",
    "weather",
    "calendar",
    "update",
    "persistent_notification",
    "conversation",
}

IGNORABLE_PATTERNS = [
    r"update\..*",
    r".*_firmware$",
    r".*_update$",
    r"sensor\.hacs.*",
    r"binary_sensor\.updater",
    r"tts\..*",
    r"stt\..*",
]

# Domains that may not have states but are valid
VIRTUAL_ENTITY_DOMAINS = {
    "script",
    "scene",
    "input_boolean",
    "input_number",
    "input_text",
    "input_select",
    "input_datetime",
    "input_button",
    "counter",
    "timer",
    "group",
    "automation",
}

# Attributes to remove from output (token savings)
ATTR_BLACKLIST = {
    "icon",
    "entity_picture",
    "context",
    "friendly_name_template",
    "supported_features",
    "assumed_state",
    "attribution",
    "device_class_icon",
    "supported_color_modes",
    "effect_list",
    "preset_modes",
    "fan_modes",
    "swing_modes",
    "hvac_modes",
    "min_temp",
    "max_temp",
    "target_temp_step",
}


# --- YAML LOADER with HA tag support ---
class HomeAssistantLoader(yaml.SafeLoader):
    """Loader ignoring HA tags (!secret, !include, etc.)"""

    pass


def _construct_stub(loader, tag_suffix, node):
    """Replaces HA tags with placeholder strings."""
    if isinstance(node, yaml.ScalarNode):
        return f"!{tag_suffix} {loader.construct_scalar(node)}"
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return f"!{tag_suffix}"


yaml.add_multi_constructor("!", _construct_stub, Loader=HomeAssistantLoader)
