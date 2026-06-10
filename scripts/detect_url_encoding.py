#!/usr/bin/env python3
"""Detect urllib.parse.quote() used in HA API URL path construction.

Home Assistant's REST API expects raw path segments (timestamps, entity_ids)
without URL-encoding. Using quote() on path components introduces double-
encoding and breaks the request.

This hook flags:
  f"/api/...{quote(...)}..."
  f"/api/...{urllib.parse.quote(...)}..."

It does NOT flag:
  - quote() on non-API URLs (third-party services, MQTT topics)
  - quote() on query parameter values (after ? or &)

Exit code 1 on violations, 0 otherwise.
"""

import re
import sys

# Regex: f-string lines that start constructing an /api/ URL
# and use quote( inside an f-string interpolation {...}
# Requires a realistic path segment between /api/ and { (e.g., /api/history/period/{quote(...)})
# This avoids matching docstring examples with "..." placeholders.
API_QUOTE_RE = re.compile(r'f"/api/[a-z_/\-0-9]+\{[^}]*\bquote\(')


def _line_has_api_quote(line: str) -> bool:
    """Check if a single line uses quote() inside an f"/api/..." string."""
    return bool(API_QUOTE_RE.search(line))


def check_file(filepath: str) -> list[tuple[int, str]]:
    """Check a Python file for API URL encoding violations.

    Returns list of (line_number, line_text) tuples.
    """
    violations: list[tuple[int, str]] = []

    try:
        with open(filepath, encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return violations

    for i, line in enumerate(lines, start=1):
        stripped = line.rstrip("\n")
        if _line_has_api_quote(stripped):
            violations.append((i, stripped))

    return violations


def main() -> int:
    """Run the detector over all Python files passed as arguments."""
    files = sys.argv[1:] if len(sys.argv) > 1 else []
    if not files:
        # Default: scan tools/ directory
        import glob as _glob

        files = _glob.glob("tools/**/*.py", recursive=True)
        files += _glob.glob("scripts/**/*.py", recursive=True)
        # Deduplicate and sort
        files = sorted(set(files))

    exit_code = 0

    for filepath in files:
        if not filepath.endswith(".py"):
            continue
        violations = check_file(filepath)
        if violations:
            exit_code = 1
            for lineno, line in violations:
                print(
                    f"{filepath}:{lineno}: "
                    f"URL-encoding on API path component - "
                    f"remove quote() call\n    {line}",
                )

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
