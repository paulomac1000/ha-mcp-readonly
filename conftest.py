"""
Test fixtures for unit tests - mocked dependencies, no real connections.

This conftest.py is loaded for ALL tests (unit + integration) when running from
the ha-mcp-readonly directory.
"""

import asyncio
import inspect
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def load_env():
    """Load environment variables from .env file if available."""
    env_paths = [
        Path("/app/.env"),
        Path(".env"),
    ]
    for env_path in env_paths:
        if env_path.exists():
            try:
                with open(env_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
                print(f"Loaded environment variables from {env_path}")
                return
            except Exception as e:
                print(f"Warning: failed to load .env: {e}")


load_env()

# Configuration from environment
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
HA_CONFIG_PATH = os.getenv("HA_CONFIG_PATH", "/config")


# =============================================================================
# MOCK DATA FOR HOME ASSISTANT (ADJUSTED FOR TESTS)
# =============================================================================

# Area Registry - 3 areas
MOCK_AREA_REGISTRY = {
    "version": 1,
    "minor_version": 6,
    "key": "core.area_registry",
    "data": {
        "areas": [
            {
                "id": "salon",
                "name": "Salon",
                "aliases": ["living room", "living room"],
                "floor_id": None,
                "icon": "mdi:sofa",
                "labels": [],
                "picture": None,
                "created_at": "2024-01-01T00:00:00.000000+00:00",
                "modified_at": "2024-01-01T00:00:00.000000+00:00",
            },
            {
                "id": "biuro",
                "name": "Biuro",
                "aliases": ["office", "gabinet"],
                "floor_id": None,
                "icon": "mdi:desk",
                "labels": [],
                "picture": None,
                "created_at": "2024-01-01T00:00:00.000000+00:00",
                "modified_at": "2024-01-01T00:00:00.000000+00:00",
            },
            {
                "id": "sypialnia",
                "name": "Sypialnia",
                "aliases": ["bedroom"],
                "floor_id": None,
                "icon": "mdi:bed",
                "labels": [],
                "picture": None,
                "created_at": "2024-01-01T00:00:00.000000+00:00",
                "modified_at": "2024-01-01T00:00:00.000000+00:00",
            },
        ]
    },
}

# Device Registry - 4 devices (one with via_device_id, one disabled)
MOCK_DEVICE_REGISTRY = {
    "version": 1,
    "minor_version": 6,
    "key": "core.device_registry",
    "data": {
        "devices": [
            # Device 1: Sonoff Button (has via_device_id pointing to ZigBee hub)
            {
                "id": "c67a8024bc53a3d38dacc8c8c6e01cf6",
                "config_entries": ["e01182bae2f8b20605c8317f4623d1e9"],
                "connections": [],
                "identifiers": [["mqtt", "sonoff_button_001"]],
                "manufacturer": "SONOFF",
                "model": "Wireless button",
                "model_id": "SNZB-01",
                "name": "Sonoff Button",
                "name_by_user": None,
                "sw_version": "1.0.0",
                "hw_version": "1.0",
                "serial_number": None,
                "via_device_id": "zigbee_hub_device_001",
                "area_id": "biuro",
                "disabled_by": None,
                "entry_type": None,
                "configuration_url": None,
                "labels": [],
                "primary_config_entry": "e01182bae2f8b20605c8317f4623d1e9",
                "created_at": "2024-01-20T10:00:00.000000+00:00",
                "modified_at": "2024-01-20T10:00:00.000000+00:00",
            },
            # Device 2: ZigBee Hub (parent device)
            {
                "id": "zigbee_hub_device_001",
                "config_entries": ["e01182bae2f8b20605c8317f4623d1e9"],
                "connections": [["mac", "aa:bb:cc:dd:ee:ff"]],
                "identifiers": [["mqtt", "zigbee_hub"]],
                "manufacturer": "SONOFF",
                "model": "ZigBee Bridge Pro",
                "model_id": "ZBBridge-P",
                "name": "ZigBee Hub",
                "name_by_user": "Main ZigBee Hub",
                "sw_version": "2.0.0",
                "hw_version": "2.0",
                "serial_number": "ZB123456",
                "via_device_id": None,
                "area_id": "salon",
                "disabled_by": None,
                "entry_type": None,
                "configuration_url": "http://192.168.1.50",
                "labels": ["hub", "zigbee"],
                "primary_config_entry": "e01182bae2f8b20605c8317f4623d1e9",
                "created_at": "2024-01-15T08:00:00.000000+00:00",
                "modified_at": "2024-01-15T08:00:00.000000+00:00",
            },
            # Device 3: Philips Hue Light
            {
                "id": "philips_light_device_001",
                "config_entries": ["tuya_entry_456"],
                "connections": [],
                "identifiers": [["tuya", "philips_hue_01"]],
                "manufacturer": "Philips",
                "model": "Hue White",
                "model_id": "LWB010",
                "name": "Living Room Light",
                "name_by_user": None,
                "sw_version": "1.88.1",
                "hw_version": None,
                "serial_number": None,
                "via_device_id": None,
                "area_id": "salon",
                "disabled_by": None,
                "entry_type": None,
                "configuration_url": None,
                "labels": [],
                "primary_config_entry": "tuya_entry_456",
                "created_at": "2024-03-01T09:30:00.000000+00:00",
                "modified_at": "2024-03-01T09:30:00.000000+00:00",
            },
            # Device 4: Disabled device (for test_search_disabled_only)
            {
                "id": "disabled_device_001",
                "config_entries": ["gree_disabled_entry_123"],
                "connections": [],
                "identifiers": [["gree", "ac_bedroom"]],
                "manufacturer": "Gree",
                "model": "Air Conditioner",
                "model_id": "GWH12ACC",
                "name": "Bedroom AC Unit",
                "name_by_user": None,
                "sw_version": "1.0",
                "hw_version": None,
                "serial_number": None,
                "via_device_id": None,
                "area_id": "sypialnia",
                "disabled_by": "user",
                "entry_type": None,
                "configuration_url": None,
                "labels": [],
                "primary_config_entry": "gree_disabled_entry_123",
                "created_at": "2024-02-01T08:30:00.000000+00:00",
                "modified_at": "2024-05-15T12:00:00.000000+00:00",
            },
        ],
        "deleted_devices": [],
    },
}

# Entity Registry - entities linked to devices (for test_device_entities_summary)
MOCK_ENTITY_REGISTRY = {
    "version": 1,
    "minor_version": 14,
    "key": "core.entity_registry",
    "data": {
        "entities": [
            # Entities for Sonoff Button (device: c67a8024bc53a3d38dacc8c8c6e01cf6)
            {
                "entity_id": "sensor.sonoff_button_battery",
                "config_entry_id": "e01182bae2f8b20605c8317f4623d1e9",
                "device_id": "c67a8024bc53a3d38dacc8c8c6e01cf6",
                "unique_id": "sonoff_button_001_battery",
                "platform": "mqtt",
                "original_name": "Battery",
                "name": None,
                "icon": None,
                "disabled_by": None,
                "hidden_by": None,
                "entity_category": "diagnostic",
                "device_class": "battery",
                "unit_of_measurement": "%",
                "area_id": None,
                "capabilities": None,
                "supported_features": 0,
                "options": {},
                "aliases": [],
                "labels": [],
                "created_at": "2024-01-20T10:00:00.000000+00:00",
                "modified_at": "2024-01-20T10:00:00.000000+00:00",
            },
            {
                "entity_id": "binary_sensor.sonoff_button_action",
                "config_entry_id": "e01182bae2f8b20605c8317f4623d1e9",
                "device_id": "c67a8024bc53a3d38dacc8c8c6e01cf6",
                "unique_id": "sonoff_button_001_action",
                "platform": "mqtt",
                "original_name": "Button Action",
                "name": None,
                "icon": "mdi:gesture-tap-button",
                "disabled_by": None,
                "hidden_by": None,
                "entity_category": None,
                "device_class": None,
                "unit_of_measurement": None,
                "area_id": None,
                "capabilities": None,
                "supported_features": 0,
                "options": {},
                "aliases": [],
                "labels": [],
                "created_at": "2024-01-20T10:00:00.000000+00:00",
                "modified_at": "2024-01-20T10:00:00.000000+00:00",
            },
            {
                "entity_id": "sensor.sonoff_button_linkquality",
                "config_entry_id": "e01182bae2f8b20605c8317f4623d1e9",
                "device_id": "c67a8024bc53a3d38dacc8c8c6e01cf6",
                "unique_id": "sonoff_button_001_linkquality",
                "platform": "mqtt",
                "original_name": "Link Quality",
                "name": None,
                "icon": None,
                "disabled_by": "integration",
                "hidden_by": None,
                "entity_category": "diagnostic",
                "device_class": None,
                "unit_of_measurement": "lqi",
                "area_id": None,
                "capabilities": None,
                "supported_features": 0,
                "options": {},
                "aliases": [],
                "labels": [],
                "created_at": "2024-01-20T10:00:00.000000+00:00",
                "modified_at": "2024-01-20T10:00:00.000000+00:00",
            },
            # Entities for ZigBee Hub (device: zigbee_hub_device_001)
            {
                "entity_id": "sensor.zigbee_hub_status",
                "config_entry_id": "e01182bae2f8b20605c8317f4623d1e9",
                "device_id": "zigbee_hub_device_001",
                "unique_id": "zigbee_hub_status",
                "platform": "mqtt",
                "original_name": "Hub Status",
                "name": None,
                "icon": None,
                "disabled_by": None,
                "hidden_by": None,
                "entity_category": "diagnostic",
                "device_class": None,
                "unit_of_measurement": None,
                "area_id": None,
                "capabilities": None,
                "supported_features": 0,
                "options": {},
                "aliases": [],
                "labels": [],
                "created_at": "2024-01-15T08:00:00.000000+00:00",
                "modified_at": "2024-01-15T08:00:00.000000+00:00",
            },
            # Entities for Philips Light (device: philips_light_device_001)
            {
                "entity_id": "light.living_room_light",
                "config_entry_id": "tuya_entry_456",
                "device_id": "philips_light_device_001",
                "unique_id": "philips_hue_01_light",
                "platform": "tuya",
                "original_name": "Living Room Light",
                "name": None,
                "icon": None,
                "disabled_by": None,
                "hidden_by": None,
                "entity_category": None,
                "device_class": None,
                "unit_of_measurement": None,
                "area_id": None,
                "capabilities": {"supported_color_modes": ["brightness"]},
                "supported_features": 1,
                "options": {},
                "aliases": [],
                "labels": [],
                "created_at": "2024-03-01T09:30:00.000000+00:00",
                "modified_at": "2024-03-01T09:30:00.000000+00:00",
            },
            # Entities for disabled Gree device (disabled_by: "config_entry")
            {
                "entity_id": "climate.bedroom_ac",
                "config_entry_id": "gree_disabled_entry_123",
                "device_id": "disabled_device_001",
                "unique_id": "gree_ac_bedroom_climate",
                "platform": "gree",
                "original_name": "Bedroom AC",
                "name": None,
                "icon": None,
                "disabled_by": "config_entry",
                "hidden_by": None,
                "entity_category": None,
                "device_class": None,
                "unit_of_measurement": None,
                "area_id": None,
                "capabilities": None,
                "supported_features": 0,
                "options": {},
                "aliases": [],
                "labels": [],
                "created_at": "2024-02-01T08:30:00.000000+00:00",
                "modified_at": "2024-05-15T12:00:00.000000+00:00",
            },
            # Sun entity (no device)
            {
                "entity_id": "sun.sun",
                "config_entry_id": "sun_entry_001",
                "device_id": None,
                "unique_id": "sun",
                "platform": "sun",
                "original_name": "Sun",
                "name": None,
                "icon": None,
                "disabled_by": None,
                "hidden_by": None,
                "entity_category": None,
                "device_class": None,
                "unit_of_measurement": None,
                "area_id": None,
                "capabilities": None,
                "supported_features": 0,
                "options": {},
                "aliases": [],
                "labels": [],
                "created_at": "2024-01-01T00:00:00.000000+00:00",
                "modified_at": "2024-01-01T00:00:00.000000+00:00",
            },
        ]
    },
}

# Config Entries - 4 entries (one disabled_by: "user")
MOCK_CONFIG_ENTRIES = {
    "version": 1,
    "minor_version": 3,
    "key": "core.config_entries",
    "data": {
        "entries": [
            # Entry 1: Sun (built-in, always loaded)
            {
                "entry_id": "sun_entry_001",
                "domain": "sun",
                "title": "Sun",
                "source": "import",
                "state": "loaded",
                "version": 1,
                "minor_version": 1,
                "disabled_by": None,
                "supports_options": False,
                "supports_remove_device": False,
                "supports_unload": True,
                "pref_disable_new_entities": False,
                "pref_disable_polling": False,
                "options": {},
                "data": {},
                "unique_id": None,
                "discovery_keys": [],
                "created_at": "2024-01-01T00:00:00.000000+00:00",
                "modified_at": "2024-01-01T00:00:00.000000+00:00",
            },
            # Entry 2: MQTT (healthy, with entities)
            {
                "entry_id": "e01182bae2f8b20605c8317f4623d1e9",
                "domain": "mqtt",
                "title": "192.168.1.100",
                "source": "user",
                "state": "loaded",
                "version": 1,
                "minor_version": 2,
                "disabled_by": None,
                "supports_options": True,
                "supports_remove_device": True,
                "supports_unload": True,
                "pref_disable_new_entities": False,
                "pref_disable_polling": False,
                "options": {"broker": "192.168.1.100", "port": 1883},
                "data": {"host": "192.168.1.100"},
                "unique_id": None,
                "discovery_keys": [],
                "created_at": "2024-01-15T10:30:00.000000+00:00",
                "modified_at": "2024-06-01T14:20:00.000000+00:00",
            },
            # Entry 3: Gree (disabled by user) - key for test_disabled_entry_state
            {
                "entry_id": "gree_disabled_entry_123",
                "domain": "gree",
                "title": "Bedroom AC",
                "source": "user",
                "state": "not_loaded",
                "version": 1,
                "minor_version": 1,
                "disabled_by": "user",
                "supports_options": True,
                "supports_remove_device": False,
                "supports_unload": True,
                "pref_disable_new_entities": False,
                "pref_disable_polling": False,
                "options": {},
                "data": {},
                "unique_id": "gree_ac_bedroom",
                "discovery_keys": [],
                "created_at": "2024-02-01T08:00:00.000000+00:00",
                "modified_at": "2024-05-15T12:00:00.000000+00:00",
            },
            # Entry 4: Tuya (healthy)
            {
                "entry_id": "tuya_entry_456",
                "domain": "tuya",
                "title": "Tuya Smart",
                "source": "user",
                "state": "loaded",
                "version": 1,
                "minor_version": 1,
                "disabled_by": None,
                "supports_options": True,
                "supports_remove_device": True,
                "supports_unload": True,
                "pref_disable_new_entities": False,
                "pref_disable_polling": False,
                "options": {},
                "data": {},
                "unique_id": "tuya_cloud_123",
                "discovery_keys": [],
                "created_at": "2024-03-01T09:00:00.000000+00:00",
                "modified_at": "2024-03-01T09:00:00.000000+00:00",
            },
        ]
    },
}

# Sample states - API /api/states responses
MOCK_SAMPLE_STATES = [
    # Sonoff Button entities - available
    {
        "entity_id": "sensor.sonoff_button_battery",
        "state": "85",
        "attributes": {
            "unit_of_measurement": "%",
            "device_class": "battery",
            "friendly_name": "Sonoff Button Battery",
        },
        "last_changed": "2024-06-01T10:00:00.000000+00:00",
        "last_updated": "2024-06-01T14:30:00.000000+00:00",
        "context": {
            "id": "01234567890123456789012345",
            "parent_id": None,
            "user_id": None,
        },
    },
    {
        "entity_id": "binary_sensor.sonoff_button_action",
        "state": "off",
        "attributes": {
            "icon": "mdi:gesture-tap-button",
            "friendly_name": "Sonoff Button Action",
        },
        "last_changed": "2024-06-01T09:00:00.000000+00:00",
        "last_updated": "2024-06-01T09:00:00.000000+00:00",
        "context": {
            "id": "01234567890123456789012345",
            "parent_id": None,
            "user_id": None,
        },
    },
    # ZigBee Hub - available
    {
        "entity_id": "sensor.zigbee_hub_status",
        "state": "online",
        "attributes": {"friendly_name": "ZigBee Hub Status", "devices_count": 15},
        "last_changed": "2024-06-01T08:00:00.000000+00:00",
        "last_updated": "2024-06-01T14:35:00.000000+00:00",
        "context": {
            "id": "01234567890123456789012345",
            "parent_id": None,
            "user_id": None,
        },
    },
    # Philips Light - available
    {
        "entity_id": "light.living_room_light",
        "state": "on",
        "attributes": {
            "brightness": 200,
            "friendly_name": "Living Room Light",
            "supported_color_modes": ["brightness"],
            "color_mode": "brightness",
        },
        "last_changed": "2024-06-01T18:00:00.000000+00:00",
        "last_updated": "2024-06-01T18:00:00.000000+00:00",
        "context": {
            "id": "01234567890123456789012345",
            "parent_id": None,
            "user_id": None,
        },
    },
    # Sun entity
    {
        "entity_id": "sun.sun",
        "state": "above_horizon",
        "attributes": {
            "friendly_name": "Sun",
            "next_dawn": "2024-06-02T03:30:00.000000+00:00",
            "next_dusk": "2024-06-01T21:00:00.000000+00:00",
            "elevation": 45.5,
            "azimuth": 180.0,
            "rising": False,
        },
        "last_changed": "2024-06-01T05:30:00.000000+00:00",
        "last_updated": "2024-06-01T14:35:00.000000+00:00",
        "context": {
            "id": "01234567890123456789012345",
            "parent_id": None,
            "user_id": None,
        },
    },
    # Gree AC - unavailable (disabled entry)
    {
        "entity_id": "climate.bedroom_ac",
        "state": "unavailable",
        "attributes": {"friendly_name": "Bedroom AC"},
        "last_changed": "2024-05-15T12:00:00.000000+00:00",
        "last_updated": "2024-05-15T12:00:00.000000+00:00",
        "context": {
            "id": "01234567890123456789012345",
            "parent_id": None,
            "user_id": None,
        },
    },
]


# =============================================================================
# FIXTURES FOR UNIT TESTS
# =============================================================================


@pytest.fixture
def config_path(tmp_path):
    """
    Create a temporary config path with mock registry files.

    Critical: creates .storage files with correct data for tests.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    storage_dir = config_dir / ".storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    # Create area registry
    (storage_dir / "core.area_registry").write_text(
        json.dumps(MOCK_AREA_REGISTRY), encoding="utf-8"
    )

    # Create device registry
    (storage_dir / "core.device_registry").write_text(
        json.dumps(MOCK_DEVICE_REGISTRY), encoding="utf-8"
    )

    # Create entity registry
    (storage_dir / "core.entity_registry").write_text(
        json.dumps(MOCK_ENTITY_REGISTRY), encoding="utf-8"
    )

    # Create config entries
    (storage_dir / "core.config_entries").write_text(
        json.dumps(MOCK_CONFIG_ENTRIES), encoding="utf-8"
    )

    # Create mock log file for diagnose tests
    log_file = config_dir / "home-assistant.log"
    log_file.write_text(
        """2024-06-01 14:20:00.000 INFO (MainThread) [homeassistatet.core] Starting Home Assistant
2024-06-01 14:20:01.000 INFO (MainThread) [homeassistatet.components.mqtt] Setting up MQTT
2024-06-01 14:24:55.286 ERROR (MainThread) [homeassistatet.components.gree] Connection failed for Bedroom AC
2024-06-01 14:24:56.000 WARNING (MainThread) [homeassistatet.components.gree] Retrying connection...
""",
        encoding="utf-8",
    )

    return str(config_dir)


@pytest.fixture
def ha_url():
    """Mock Home Assistant URL for unit tests."""
    return "http://localhost:8123"


@pytest.fixture
def ha_token():
    """Mock Home Assistant token for unit tests."""
    return "mock_token_for_testing_1234567890"


@pytest.fixture
def mock_registry_data():
    """
    Return mock registry data as a dict for patching load_registry().

    Keys match the registry names used by tools.utils.load_registry().
    Includes all required fields for tests.
    """
    return {
        "core.area_registry": MOCK_AREA_REGISTRY,
        "core.device_registry": MOCK_DEVICE_REGISTRY,
        "core.entity_registry": MOCK_ENTITY_REGISTRY,
        "core.config_entries": MOCK_CONFIG_ENTRIES,
    }


@pytest.fixture
def sample_states():
    """Return sample entity states for unit tests."""
    return MOCK_SAMPLE_STATES.copy()


@pytest.fixture(scope="session")
def real_mcp():
    """
    Create a real MCP server with all tools registered.
    Session-scoped for performance.
    """
    if not HA_URL or not HA_TOKEN:
        pytest.skip("HA_URL and HA_TOKEN required for integration tests")

    from fastmcp import FastMCP

    mcp = FastMCP("HA-Observer-Test")

    # Register HA tools
    from tools.states import register_state_tools

    register_state_tools(mcp, HA_URL, HA_TOKEN, HA_CONFIG_PATH)

    from tools.automations import register_automation_tools

    register_automation_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

    from tools.scripts import register_script_tools

    register_script_tools(mcp, HA_CONFIG_PATH)

    from tools.scenes import register_scene_tools

    register_scene_tools(mcp, HA_CONFIG_PATH)

    from tools.blueprints import register_blueprint_tools

    register_blueprint_tools(mcp, HA_CONFIG_PATH)

    from tools.config import register_config_tools

    register_config_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

    from tools.logs import register_log_tools

    register_log_tools(mcp, HA_CONFIG_PATH)

    from tools.storage import register_storage_tools

    register_storage_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

    from tools.config_entries import register_config_entry_tools

    register_config_entry_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

    from tools.devices import register_device_tools

    register_device_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

    from tools.entity_dependencies import register_entity_dependency_tools

    register_entity_dependency_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

    from tools.history import register_history_tools

    register_history_tools(mcp, HA_URL, HA_TOKEN)

    from tools.areas import register_area_tools

    register_area_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

    from tools.integrations import register_integration_tools

    register_integration_tools(mcp, HA_CONFIG_PATH, HA_URL, HA_TOKEN)

    return mcp


class MCPWrapper:
    """FastMCP wrapper providing compatibility across versions."""

    def __init__(self, mcp):
        self.mcp = mcp
        self._tools_cache = None
        self._has_get_tool = False
        self._has_tm_get_tool = False

    def _discover_tools(self):
        """Discover available tools using multiple strategies for compatibility."""
        if self._tools_cache is not None:
            return self._tools_cache

        tools = {}

        self._has_get_tool = hasattr(self.mcp, "get_tool") and callable(self.mcp.get_tool)

        self._has_tm_get_tool = False
        if hasattr(self.mcp, "_tool_manager"):
            tm = self.mcp._tool_manager
            if hasattr(tm, "get_tool") and callable(tm.get_tool):
                self._has_tm_get_tool = True

        if hasattr(self.mcp, "_tools") and isinstance(self.mcp._tools, dict):
            for name, tool in self.mcp._tools.items():
                unwrapped = self._unwrap_tool(tool)
                if unwrapped:
                    tools[name] = unwrapped

        if not tools and hasattr(self.mcp, "_tool_manager"):
            tm = self.mcp._tool_manager
            if hasattr(tm, "_tools") and isinstance(tm._tools, dict):
                for name, tool in tm._tools.items():
                    unwrapped = self._unwrap_tool(tool)
                    if unwrapped:
                        tools[name] = unwrapped

        if not tools and hasattr(self.mcp, "tools"):
            tools_attr = self.mcp.tools
            if isinstance(tools_attr, dict):
                for name, tool in tools_attr.items():
                    unwrapped = self._unwrap_tool(tool)
                    if unwrapped:
                        tools[name] = unwrapped
            elif hasattr(tools_attr, "__iter__"):
                for tool in tools_attr:
                    name = self._get_tool_name(tool)
                    if name:
                        unwrapped = self._unwrap_tool(tool)
                        if unwrapped:
                            tools[name] = unwrapped

        self._tools_cache = tools
        return tools

    def _get_tool_name(self, tool):
        """Extract name from a tool object."""
        for attr in ("name", "__name__", "_name"):
            if hasattr(tool, attr):
                val = getattr(tool, attr)
                if val:
                    return val
        return None

    def _unwrap_tool(self, tool):
        """Unwrap tool object to get the actual callable function."""
        if tool is None:
            return None

        for attr in ("fn", "func", "_func", "function", "_function", "callback"):
            if hasattr(tool, attr):
                unwrapped = getattr(tool, attr)
                if callable(unwrapped):
                    return unwrapped

        if callable(tool):
            return tool

        return None

    def _get_tool_function(self, name):
        """Get tool function by name using multiple strategies."""
        tools = self._discover_tools()
        if name in tools and tools[name] is not None:
            return tools[name]

        if self._has_get_tool:
            try:
                tool = self.mcp.get_tool(name)
                if tool:
                    unwrapped = self._unwrap_tool(tool)
                    if unwrapped:
                        if self._tools_cache is not None:
                            self._tools_cache[name] = unwrapped
                        return unwrapped
            except Exception:
                pass

        if self._has_tm_get_tool:
            try:
                tool = self.mcp._tool_manager.get_tool(name)
                if tool:
                    unwrapped = self._unwrap_tool(tool)
                    if unwrapped:
                        if self._tools_cache is not None:
                            self._tools_cache[name] = unwrapped
                        return unwrapped
            except Exception:
                pass

        return None

    def _list_available_tools(self):
        """List available tool names for debugging."""
        tools = self._discover_tools()
        return list(tools.keys())

    def call_tool(self, name, *args, **kwargs):
        """Execute a tool by name, handling async execution automatically."""
        func = self._get_tool_function(name)

        if not func:
            available = self._list_available_tools()
            preview = available[:10] if len(available) > 10 else available
            raise ValueError(f"Tool '{name}' not found. Available ({len(available)}): {preview}")

        if inspect.iscoroutinefunction(func):
            return self._run_async(func, *args, **kwargs)
        else:
            return func(*args, **kwargs)

    def _run_async(self, func, *args, **kwargs):
        """Run an async function from sync context."""
        try:
            asyncio.get_running_loop()
            is_running = True
        except RuntimeError:
            is_running = False

        if is_running:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, func(*args, **kwargs))
                return future.result()
        else:
            return asyncio.run(func(*args, **kwargs))


@pytest.fixture
def mcp_client(real_mcp):
    """Provide an MCPWrapper instatece for calling tools in tests."""
    return MCPWrapper(real_mcp)


@pytest.fixture
def mock_mcp():
    """Mock MCP for unit tests that properly handles tool registration."""
    mcp = MagicMock()
    mcp._tools = {}

    def tool_decorator(*args, **kwargs):
        def wrapper(func):
            tool_name = kwargs.get("name", func.__name__)
            mcp._tools[tool_name] = func
            return func

        if len(args) == 1 and callable(args[0]) and not kwargs:
            mcp._tools[args[0].__name__] = args[0]
            return args[0]

        return wrapper

    mcp.tool = tool_decorator

    def mock_get_tool(name):
        """Returns registered tool by name."""
        return mcp._tools.get(name)

    mcp.get_tool = mock_get_tool
    return mcp
