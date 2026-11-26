"""Check that the server environment matches the CI constraints."""

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_REQUIRED_PACKAGES = ("fastmcp", "mcp", "starlette")
_EXACT_PIN = re.compile(r"([\w.-]+)\s*==\s*([\w.+!-]+)")


def verify_runtime_pins(constraints: str) -> list[str]:
    """Compare installed server packages with unique unconditional exact pins.

    Args:
        constraints: Contents of the CI constraints file.

    Returns:
        Problems to report, or an empty list when the installed versions match.
    """
    pins: dict[str, list[str]] = {package: [] for package in _REQUIRED_PACKAGES}
    for raw in constraints.splitlines():
        match = _EXACT_PIN.fullmatch(raw.partition("#")[0].strip())
        if match and (name := match[1].lower()) in pins:
            pins[name].append(match[2])

    problems = []
    for package, expected in pins.items():
        if not expected:
            problems.append(f"Missing exact pin for {package}")
        elif len(expected) != 1:
            problems.append(f"Duplicate exact pin for {package}")
        else:
            try:
                actual = version(package)
            except PackageNotFoundError:
                problems.append(f"Missing installed package: {package}")
            else:
                if actual != expected[0]:
                    problems.append(f"{package} installed {actual}, pinned {expected[0]}")
    return problems


if __name__ == "__main__":
    errors = verify_runtime_pins(Path("constraints-ci.txt").read_text(encoding="utf-8"))
    if errors:
        raise SystemExit("\n".join(errors))
