#!/usr/bin/env python3
"""Fail on Semgrep SARIF findings except structurally proven scanner false positives."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_RETURN_IN_INIT_RULE = "python.lang.correctness.return-in-init.return-in-init"


def _primary_location(result: dict[str, Any]) -> tuple[Path | None, int | None]:
    locations = result.get("locations")
    if not isinstance(locations, list) or not locations:
        return None, None
    physical = locations[0].get("physicalLocation", {})
    if not isinstance(physical, dict):
        return None, None
    artifact = physical.get("artifactLocation", {})
    region = physical.get("region", {})
    if not isinstance(artifact, dict) or not isinstance(region, dict):
        return None, None
    uri = artifact.get("uri")
    line = region.get("startLine")
    return (Path(uri) if isinstance(uri, str) else None, line if isinstance(line, int) else None)


def _source_line(root: Path, relative: Path | None, line: int | None) -> str | None:
    if (
        relative is None
        or line is None
        or line < 1
        or relative.is_absolute()
        or ".." in relative.parts
    ):
        return None
    path = root / relative
    try:
        with path.open(encoding="utf-8") as handle:
            for number, text in enumerate(handle, start=1):
                if number == line:
                    return text.strip()
    except (OSError, UnicodeError):
        return None
    return None


def _is_proven_false_positive(result: dict[str, Any], root: Path) -> bool:
    """Recognize the upstream rule confusing a lambda expression with return in __init__."""
    if result.get("ruleId") != _RETURN_IN_INIT_RULE:
        return False
    relative, line = _primary_location(result)
    source = _source_line(root, relative, line)
    return source is not None and source.startswith("lambda:")


def _validated_runs(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Return structurally valid SARIF runs or fail closed on malformed scanner output."""
    runs = report.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("Semgrep SARIF must contain at least one run")
    validated: list[dict[str, Any]] = []
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            raise ValueError(f"Semgrep SARIF run {index} is not an object")
        results = run.get("results")
        if not isinstance(results, list):
            raise ValueError(f"Semgrep SARIF run {index} does not contain a results list")
        validated.append(run)
    return validated


def evaluate(report: dict[str, Any], root: Path) -> tuple[int, int]:
    blocking = 0
    suppressed = 0
    for run in _validated_runs(report):
        for result in run["results"]:
            if not isinstance(result, dict):
                raise ValueError("Semgrep SARIF result is not an object")
            relative, line = _primary_location(result)
            location = f"{relative or '<unknown>'}:{line or '?'}"
            rule = result.get("ruleId", "<unknown-rule>")
            message_data = result.get("message", {})
            message = message_data.get("text", "") if isinstance(message_data, dict) else ""
            if _is_proven_false_positive(result, root):
                suppressed += 1
                print(f"SEMGREP_FALSE_POSITIVE {location} {rule}: {message}")
                continue
            blocking += 1
            print(f"SEMGREP_FINDING {location} {rule}: {message}")
    return blocking, suppressed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sarif", type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    report = json.loads(args.sarif.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Semgrep SARIF root must be an object")
    blocking, suppressed = evaluate(report, args.repository_root.resolve())
    print(f"Semgrep gate: blocking={blocking}, proven_false_positives={suppressed}")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
