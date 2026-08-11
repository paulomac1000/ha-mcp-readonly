"""Regression tests for the fail-closed Semgrep SARIF gate."""

from pathlib import Path

import pytest

from scripts.check_semgrep_sarif import evaluate


def test_empty_runs_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one run"):
        evaluate({"runs": []}, tmp_path)


def test_missing_runs_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one run"):
        evaluate({}, tmp_path)


def test_missing_results_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="results list"):
        evaluate({"runs": [{}]}, tmp_path)


def test_valid_empty_results_are_clean(tmp_path: Path) -> None:
    assert evaluate({"runs": [{"results": []}]}, tmp_path) == (0, 0)
