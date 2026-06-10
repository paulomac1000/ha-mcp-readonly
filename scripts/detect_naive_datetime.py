#!/usr/bin/env python3
"""
detect_naive_datetime.py — Pre-commit hook to detect naive datetime.now() calls.

Detects in tools/*.py:
  - `datetime.now()` with no tz argument (produces a naive datetime)
  - `datetime.now(tz=None)` (explicitly naive)
  - `datetime.utcnow()` (deprecated since Python 3.12)

Ignores:
  - `datetime.now(UTC)` — correct UTC-aware pattern
  - `datetime.now(<any_tz>)` — any timezone-aware call
  - Test files under tests/

Exit code 1 if violations found, 0 otherwise.
"""

import re
import sys
from pathlib import Path


def check_file(filepath: Path) -> list[tuple[int, str]]:
    """Check a single file for naive datetime.now() violations.

    Returns list of (line_number, line_text) tuples.
    """
    violations: list[tuple[int, str]] = []
    content = filepath.read_text(encoding="utf-8")
    lines = content.splitlines()

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Skip comment-only lines
        if stripped.startswith("#"):
            continue

        # 1. datetime.utcnow() — deprecated, always a violation
        if re.search(r"datetime\.utcnow\s*\(\s*\)", line):
            violations.append((lineno, stripped))
            continue

        # 2. datetime.now(tz=None) — explicitly naive
        if re.search(r"datetime\.now\s*\(\s*tz\s*=\s*None\s*\)", line):
            violations.append((lineno, stripped))
            continue

        # 3. datetime.now() with empty or whitespace-only parens — naive
        # This catches datetime.now() and datetime.now(  )
        # Does NOT match datetime.now(UTC), datetime.now(tz=...), etc.
        if re.search(r"datetime\.now\s*\(\s*\)", line):
            violations.append((lineno, stripped))
            continue

    return violations


def main() -> int:
    tools_dir = Path("tools")
    if not tools_dir.is_dir():
        print("No tools/ directory found -- skipping.")
        return 0

    all_violations: list[tuple[Path, int, str]] = []

    for pyfile in sorted(tools_dir.glob("*.py")):
        if pyfile.name.startswith("_"):
            continue
        violations = check_file(pyfile)
        for lineno, text in violations:
            all_violations.append((pyfile, lineno, text))

    if all_violations:
        print(
            "ERROR: Naive datetime.now() calls detected "
            "(use datetime.now(UTC) instead):"
        )
        print()
        for filepath, lineno, text in all_violations:
            print(f"  {filepath}:{lineno}:  {text}")
        print()
        print("Fix with: datetime.now(UTC) instead of datetime.now()")
        print("Ensure: from datetime import UTC")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
