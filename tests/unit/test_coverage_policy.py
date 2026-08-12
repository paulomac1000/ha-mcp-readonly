"""I/O-free regression tests for the repository coverage policy."""

from pathlib import Path

import pytest

import scripts.check_coverage_policy as coverage_policy
from scripts.check_coverage_policy import CoveragePolicyError, evaluate


def _report() -> dict[str, object]:
    return {
        "files": {
            "tools/example.py": {
                "executed_lines": list(range(1, 91)),
                "missing_lines": list(range(91, 101)),
            }
        }
    }


def _stub_registered_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        coverage_policy,
        "_registered_tool_modules",
        lambda _root: ["tools/example.py"],
    )


def test_changed_tool_missing_from_coverage_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_registered_modules(monkeypatch)
    monkeypatch.setattr(
        coverage_policy,
        "_added_lines",
        lambda _root, _base: {"tools/new_helper.py": {1, 2}},
    )

    with pytest.raises(CoveragePolicyError, match="not measured in coverage JSON"):
        evaluate(_report(), Path("."), base_ref="base")


def test_new_tool_lines_at_exact_threshold_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_registered_modules(monkeypatch)
    monkeypatch.setattr(
        coverage_policy,
        "_added_lines",
        lambda _root, _base: {"tools/example.py": {1, 2, 3, 4, 91}},
    )

    with pytest.raises(CoveragePolicyError, match=r"80\.00% must be > 80\.00%"):
        evaluate(_report(), Path("."), base_ref="base")


def test_new_tool_lines_above_threshold_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_registered_modules(monkeypatch)
    monkeypatch.setattr(
        coverage_policy,
        "_added_lines",
        lambda _root, _base: {"tools/example.py": {1, 2, 3, 4, 5}},
    )

    result = evaluate(_report(), Path("."), base_ref="base")

    assert result["new_executable_lines"] == 5
    assert result["new_lines_coverage"] == 100.0
