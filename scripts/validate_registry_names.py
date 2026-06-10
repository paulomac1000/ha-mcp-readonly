#!/usr/bin/env python3
"""
Pre-commit hook: validate all ``load_registry()`` calls use known-good registry names.

Parses every Python file under ``tools/``, extracts the first string argument
from each ``load_registry("...", ...)`` call, and checks it against a canonical
allowlist.

Registry names that match ``lovelace.*`` (with a dot, e.g. ``lovelace.my_dash``)
are accepted as per-dashboard configuration keys. Everything else must be an
exact member of the allowlist.

Exit code 1 when violations are found; prints every violation as
``file:line: registry_name``.
"""

import ast
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"

ALLOWLIST: set[str] = {
    "core.entity_registry",
    "core.device_registry",
    "core.area_registry",
    "core.config_entries",
    "core.category_registry",
    "core.tag",
    "core.voice_assistant",
    "lovelace",  # .storage/lovelace — dashboards dict
    "lovelace_dashboards",
    "lovelace_resources",
    "cloud.google_assistant",
    "person",
    "zone",
    "timer",
    "counter",
    "hacs.data",
}


def _is_allowed(name: str) -> bool:
    """Return True if *name* is in the allowlist or matches ``lovelace.*``."""
    if name in ALLOWLIST:
        return True
    if name.startswith("lovelace."):
        return True
    return False


def _collect_violations() -> list[tuple[str, int, str]]:
    """Scan all Python files under *tools_dir* for bad registry names.

    Returns a list of ``(file_path, line_number, registry_name)`` tuples.
    """
    violations: list[tuple[str, int, str]] = []

    for pyfile in sorted(TOOLS_DIR.rglob("*.py")):
        try:
            tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
        except SyntaxError:
            continue  # malformed file, skip

        for node in ast.walk(tree):
            # Match: load_registry("some_name", ...)
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "load_registry":
                continue
            if not node.args:
                continue

            first_arg = node.args[0]
            if not isinstance(first_arg, ast.Constant) or not isinstance(
                first_arg.value, str
            ):
                continue  # non-literal first arg, can't statically check

            name = first_arg.value
            if not _is_allowed(name):
                violations.append((str(pyfile), node.lineno, name))

    return violations


def main() -> int:
    violations = _collect_violations()

    if not violations:
        return 0

    print("ERROR: Invalid load_registry() registry names found:", file=sys.stderr)
    for filepath, lineno, name in violations:
        print(f"  {filepath}:{lineno}: {name}", file=sys.stderr)

    print(
        f"\nFound {len(violations)} violation(s). "
        "Fix by using a known-good registry name or update the allowlist.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
