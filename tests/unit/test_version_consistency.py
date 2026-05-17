"""Unit test: the project version is consistent across every SSOT location.

The version MUST be defined once (version.py) and never drift between
version.py, tools/__init__.py (TOOLS_VERSION) and pyproject.toml.
[RULE: TEST-HIERARCHY-2] zero I/O beyond reading the local pyproject file.
"""

import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _pyproject_version() -> str:
    with open(_ROOT / "pyproject.toml", "rb") as fh:
        return str(tomllib.load(fh)["project"]["version"])


class TestVersionConsistency:
    """All version sources must agree with version.py."""

    def test_version_py_matches_pyproject(self):
        from version import __version__

        assert __version__ == _pyproject_version()

    def test_tools_version_sourced_from_version_py(self):
        from tools import TOOLS_VERSION
        from version import __version__

        assert TOOLS_VERSION == __version__

    def test_version_is_semver(self):
        from version import __version__

        parts = __version__.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)
