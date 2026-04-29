"""
Custom YAML Utilities for Home Assistant

Provides a custom YAML loader that handles Home Assistant specific tags
like !include, !secret, !input, etc.
"""

from typing import Any

import yaml

# =============================================================================
# CUSTOM YAML LOADER
# =============================================================================


class HomeAssistantLoader(yaml.SafeLoader):
    """
    Custom YAML loader that handles Home Assistant specific tags.

    Tags are returned as strings in the format "!tag value" to allow
    reading configuration without resolving includes/secrets.
    """

    pass


def _ha_tag_constructor(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> str:
    """Generic constructor for HA tags - returns them as strings."""
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
        return f"!{tag_suffix} {value}"
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
        return f"!{tag_suffix} {value}"
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node)
        return f"!{tag_suffix} {value}"
    return f"!{tag_suffix}"


# Register all common HA tags
_HA_TAGS = [
    "input",
    "include",
    "include_dir_list",
    "include_dir_named",
    "include_dir_merge_list",
    "include_dir_merge_named",
    "secret",
    "env_var",
    "file",
]

for tag in _HA_TAGS:
    HomeAssistantLoader.add_constructor(
        f"!{tag}", lambda loader, node, t=tag: _ha_tag_constructor(loader, t, node)
    )


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def load_yaml_file(file_path: str) -> Any:
    """
    Load a YAML file using the HomeAssistantLoader.

    Args:
        file_path: Path to YAML file

    Returns:
        Parsed YAML data or None on error
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.load(f, Loader=HomeAssistantLoader)
    except (yaml.YAMLError, IOError) as e:
        print(f"[yaml_utils] Error loading {file_path}: {e}")
        return None


def parse_yaml_string(yaml_content: str) -> Any:
    """
    Parse YAML string using the HomeAssistantLoader.

    Args:
        yaml_content: YAML content as string

    Returns:
        Parsed YAML data or None on error
    """
    try:
        return yaml.load(yaml_content, Loader=HomeAssistantLoader)
    except yaml.YAMLError as e:
        print(f"[yaml_utils] Error parsing YAML: {e}")
        return None


def dump_yaml(data: Any, default_flow_style: bool = False, sort_keys: bool = False) -> str:
    """
    Dump data to YAML string.

    Args:
        data: Data to serialize
        default_flow_style: Use flow style for collections
        sort_keys: Sort dictionary keys

    Returns:
        YAML string
    """
    return yaml.dump(
        data,
        allow_unicode=True,
        default_flow_style=default_flow_style,
        sort_keys=sort_keys,
    )
