import os
import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(scope="session")
def ha_mcp_root() -> str:
    """Return the path to ha-mcp-readonly root directory."""
    return str(Path(__file__).parent.parent)


@pytest.fixture(scope="session")
def config_path_default() -> str:
    """Return default HA_CONFIG_PATH."""
    return os.getenv("HA_CONFIG_PATH", "/config")


@pytest.fixture
def filesystem_test_root(tmp_path: Path) -> Path:
    """Return an isolated configured root for filesystem contract tests."""
    root = tmp_path / "fixture-config-root"
    root.mkdir()
    return root


@pytest.fixture
def filesystem_test_filename() -> str:
    """Return a stable fixture filename for filesystem contract tests."""
    return "fixture-configuration.yaml"
