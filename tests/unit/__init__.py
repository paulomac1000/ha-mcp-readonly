"""
Unit test configuration.
Unit tests use mocked dependencies from the parent conftest.py.
"""

from conftest import (  # noqa: F401 — re-exported for test discovery
    MOCK_AREA_REGISTRY,
    MOCK_CONFIG_ENTRIES,
    MOCK_DEVICE_REGISTRY,
    MOCK_ENTITY_REGISTRY,
    MOCK_SAMPLE_STATES,
    config_path,
    ha_token,
    ha_url,
    mock_mcp,
    mock_registry_data,
    sample_states,
)
