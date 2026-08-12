"""I/O-free regression tests for the repository coverage policy."""

from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.check_coverage_policy as coverage_policy
from scripts.check_coverage_policy import CoveragePolicyError, evaluate


def _report(module_name: str) -> dict[str, object]:
    return {
        "files": {
            module_name: {
                "executed_lines": list(range(1, 91)),
                "missing_lines": list(range(91, 101)),
            }
        }
    }


def test_changed_tool_missing_from_coverage_fails_closed(
    coverage_tool_module_name: str,
    coverage_missing_tool_module_name: str,
) -> None:
    with (
        patch.object(
            coverage_policy,
            "_registered_tool_modules",
            return_value=[coverage_tool_module_name],
        ),
        patch.object(
            coverage_policy,
            "_added_lines",
            return_value={coverage_missing_tool_module_name: {1, 2}},
        ),
        pytest.raises(CoveragePolicyError, match="not measured in coverage JSON"),
    ):
        evaluate(_report(coverage_tool_module_name), Path("."), base_ref="base")


def test_new_tool_lines_at_exact_threshold_fail(
    coverage_tool_module_name: str,
) -> None:
    with (
        patch.object(
            coverage_policy,
            "_registered_tool_modules",
            return_value=[coverage_tool_module_name],
        ),
        patch.object(
            coverage_policy,
            "_added_lines",
            return_value={coverage_tool_module_name: {1, 2, 3, 4, 91}},
        ),
        pytest.raises(CoveragePolicyError, match=r"80\.00% must be > 80\.00%"),
    ):
        evaluate(_report(coverage_tool_module_name), Path("."), base_ref="base")


def test_new_tool_lines_above_threshold_pass(
    coverage_tool_module_name: str,
) -> None:
    with (
        patch.object(
            coverage_policy,
            "_registered_tool_modules",
            return_value=[coverage_tool_module_name],
        ),
        patch.object(
            coverage_policy,
            "_added_lines",
            return_value={coverage_tool_module_name: {1, 2, 3, 4, 5}},
        ),
    ):
        result = evaluate(_report(coverage_tool_module_name), Path("."), base_ref="base")

    assert result["new_executable_lines"] == 5
    assert result["new_lines_coverage"] == 100.0
