#!/usr/bin/env python3
"""Enforce repository coverage policy from coverage.py JSON plus an optional Git diff."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

_REGISTER_TOOL = re.compile(r"^\s*(?:async\s+)?def\s+register_[A-Za-z0-9_]*_tools\s*\(", re.MULTILINE)
_DIFF_HUNK = re.compile(r"^@@ -(?:\d+)(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class CoveragePolicyError(RuntimeError):
    """Raised when coverage evidence is malformed or below policy."""


def _load_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), dict):
        raise CoveragePolicyError("coverage JSON does not contain a files object")
    return payload


def _normalized_file_key(value: str) -> str:
    return value.replace("\\", "/").removeprefix("./")


def _file_record(files: dict[str, Any], relative: str) -> dict[str, Any] | None:
    relative = _normalized_file_key(relative)
    for key, record in files.items():
        if _normalized_file_key(str(key)) == relative and isinstance(record, dict):
            return record
    return None


def _line_sets(record: dict[str, Any]) -> tuple[set[int], set[int]]:
    executed = record.get("executed_lines", [])
    missing = record.get("missing_lines", [])
    if not isinstance(executed, list) or not isinstance(missing, list):
        raise CoveragePolicyError("coverage file record lacks executed_lines/missing_lines")
    if not all(isinstance(line, int) for line in executed + missing):
        raise CoveragePolicyError("coverage line lists contain non-integer values")
    return set(executed), set(missing)


def _percent(covered: int, total: int) -> float:
    return 100.0 if total == 0 else covered * 100.0 / total


def _registered_tool_modules(root: Path) -> list[str]:
    modules: list[str] = []
    for path in sorted((root / "tools").glob("*.py")):
        if _REGISTER_TOOL.search(path.read_text(encoding="utf-8")):
            modules.append(path.relative_to(root).as_posix())
    if not modules:
        raise CoveragePolicyError("no registered tool modules were discovered")
    return modules


def _added_lines(root: Path, base_ref: str) -> dict[str, set[int]]:
    completed = subprocess.run(
        ["git", "diff", "--unified=0", f"{base_ref}...HEAD", "--", "tools/*.py"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    current_file: str | None = None
    current_line: int | None = None
    added: dict[str, set[int]] = {}
    for raw_line in completed.stdout.splitlines():
        if raw_line.startswith("+++ b/"):
            current_file = raw_line[6:]
            current_line = None
            continue
        match = _DIFF_HUNK.match(raw_line)
        if match:
            current_line = int(match.group(1))
            continue
        if current_file is None or current_line is None:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            added.setdefault(current_file, set()).add(current_line)
            current_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
        else:
            current_line += 1
    return added


def evaluate(
    report: dict[str, Any],
    root: Path,
    *,
    base_ref: str | None = None,
    module_minimum: float = 80.0,
    tools_minimum_exclusive: float = 85.0,
    new_lines_minimum_exclusive: float = 80.0,
) -> dict[str, float | int | None]:
    files = report["files"]
    if not isinstance(files, dict):
        raise CoveragePolicyError("coverage JSON files value is not an object")

    registered = _registered_tool_modules(root)
    module_failures: list[str] = []
    tools_covered = 0
    tools_total = 0

    for key, record in files.items():
        normalized = _normalized_file_key(str(key))
        if not normalized.startswith("tools/") or not normalized.endswith(".py"):
            continue
        if not isinstance(record, dict):
            raise CoveragePolicyError(f"coverage record for {normalized} is not an object")
        executed, missing = _line_sets(record)
        tools_covered += len(executed)
        tools_total += len(executed | missing)

    if tools_total == 0:
        raise CoveragePolicyError("coverage JSON contains no measured tools/ statements")
    tools_percent = _percent(tools_covered, tools_total)
    if tools_percent <= tools_minimum_exclusive:
        raise CoveragePolicyError(
            f"tools/ aggregate coverage {tools_percent:.2f}% must be > {tools_minimum_exclusive:.2f}%"
        )

    for module in registered:
        record = _file_record(files, module)
        if record is None:
            module_failures.append(f"{module}=unmeasured")
            continue
        executed, missing = _line_sets(record)
        value = _percent(len(executed), len(executed | missing))
        if value < module_minimum:
            module_failures.append(f"{module}={value:.2f}%")
    if module_failures:
        raise CoveragePolicyError(
            f"registered tool modules must be >= {module_minimum:.2f}%: "
            + ", ".join(module_failures)
        )

    new_lines_percent: float | None = None
    new_executable = 0
    new_covered = 0
    if base_ref:
        for relative, added in _added_lines(root, base_ref).items():
            record = _file_record(files, relative)
            if record is None:
                continue
            executed, missing = _line_sets(record)
            executable = added & (executed | missing)
            new_executable += len(executable)
            new_covered += len(executable & executed)
        if new_executable:
            new_lines_percent = _percent(new_covered, new_executable)
            if new_lines_percent <= new_lines_minimum_exclusive:
                raise CoveragePolicyError(
                    "new executable tool-line coverage "
                    f"{new_lines_percent:.2f}% must be > {new_lines_minimum_exclusive:.2f}% "
                    f"({new_covered}/{new_executable})"
                )

    return {
        "registered_tool_modules": len(registered),
        "tools_coverage": tools_percent,
        "new_executable_lines": new_executable,
        "new_lines_coverage": new_lines_percent,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--base-ref")
    args = parser.parse_args()

    root = args.repository_root.resolve()
    result = evaluate(_load_report(args.coverage_json), root, base_ref=args.base_ref)
    new_lines = result["new_lines_coverage"]
    new_lines_text = "n/a" if new_lines is None else f"{new_lines:.2f}%"
    print(
        "Coverage policy: "
        f"registered_modules={result['registered_tool_modules']}, "
        f"tools={result['tools_coverage']:.2f}%, "
        f"new_tool_lines={new_lines_text}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
