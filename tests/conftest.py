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


@pytest.fixture
def mock_filesystem_root() -> Path:
    """Return a non-I/O filesystem root for mocked unit tests."""
    return Path("/fixture-config-root")


@pytest.fixture
def missing_area_name() -> str:
    """Return a generic area name that should not exist in provider data."""
    return "fixture-missing-area"


@pytest.fixture
def missing_entity_id() -> str:
    """Return a generic entity id that should not exist in provider data."""
    return "light.fixture_missing_entity"


@pytest.fixture
def evidence_filename() -> str:
    """Return a generic evidence artifact filename for filesystem tests."""
    return "fixture-evidence.bin"


@pytest.fixture
def evidence_target_filename() -> str:
    """Return a generic symlink target filename for filesystem tests."""
    return "fixture-target.bin"


@pytest.fixture
def coverage_tool_module_name() -> str:
    """Return a synthetic registered tool-module path for coverage-policy tests."""
    return "tools/fixture_tool_module.py"


@pytest.fixture
def coverage_missing_tool_module_name() -> str:
    """Return a synthetic uncovered tool-module path for coverage-policy tests."""
    return "tools/fixture_missing_tool_module.py"


@pytest.fixture
def semgrep_rule_id() -> str:
    """Return a synthetic Semgrep rule identifier."""
    return "fixture.rule"


@pytest.fixture
def semgrep_message() -> str:
    """Return a synthetic Semgrep finding message."""
    return "fixture malformed location"


@pytest.fixture
def fixture_repository_name() -> str:
    """Return a synthetic GitHub repository name for evidence-client tests."""
    return "fixture-owner/fixture-repository"


@pytest.fixture
def fixture_git_sha() -> str:
    """Return a synthetic immutable Git SHA for evidence-client tests."""
    return "a" * 40
