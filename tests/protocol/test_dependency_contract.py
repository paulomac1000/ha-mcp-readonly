"""Repository dependency-contract checks.

These checks read repository files by design (they pin packaging
metadata contracts) and therefore live in the protocol suite, not in
the zero-I/O unit suite.
"""

import re
import tomllib
from pathlib import Path


def test_requirements_runtime_ranges_match_pyproject() -> None:
    """The README install path (requirements.txt) cannot drift from pyproject.toml.

    The FastMCP 4.x hold-back is enforced in pyproject.toml and
    constraints-ci.txt; this check keeps the direct runtime ranges in
    requirements.txt from silently diverging from them.
    """
    project_root = Path(__file__).resolve().parents[2]

    requirements = (project_root / "requirements.txt").read_text(encoding="utf-8")
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))

    pyproject_deps = {
        dep.split(">=")[0].split("==")[0].split("<")[0].strip().lower(): dep.strip()
        for dep in pyproject["project"]["dependencies"]
    }
    for line in requirements.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[><=~!]", line, maxsplit=1)[0].strip().lower()
        assert name in pyproject_deps, f"{name} present in requirements.txt but not pyproject.toml"
        assert line == pyproject_deps[name], (
            f"dependency range drift for {name}: requirements.txt has {line!r}, "
            f"pyproject.toml has {pyproject_deps[name]!r}"
        )
