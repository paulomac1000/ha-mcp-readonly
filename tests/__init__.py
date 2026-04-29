"""
Pytest configuration for tests/ directory.
This file handles both unit and integration tests.
"""

import os
import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(scope="session")
def ha_mcp_root():
    """Return the path to ha-mcp-readonly root directory."""
    return str(Path(__file__).parent.parent)


@pytest.fixture(scope="session")
def config_path_default():
    """Return default HA_CONFIG_PATH."""
    return os.getenv("HA_CONFIG_PATH", "/config")
