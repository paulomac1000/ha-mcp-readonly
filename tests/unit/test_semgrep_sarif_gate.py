"""I/O-free regression tests for the fail-closed Semgrep SARIF gate."""

from pathlib import Path

import pytest

from scripts.check_semgrep_sarif import evaluate

_ROOT = Path(".")


def test_empty_runs_fail_closed() -> None:
    with pytest.raises(ValueError, match="at least one run"):
        evaluate({"runs": []}, _ROOT)


def test_missing_runs_fail_closed() -> None:
    with pytest.raises(ValueError, match="at least one run"):
        evaluate({}, _ROOT)


def test_missing_results_fail_closed() -> None:
    with pytest.raises(ValueError, match="results list"):
        evaluate({"runs": [{}]}, _ROOT)


def test_valid_empty_results_are_clean() -> None:
    assert evaluate({"runs": [{"results": []}]}, _ROOT) == (0, 0)


def test_malformed_primary_location_remains_blocking() -> None:
    report = {
        "runs": [
            {
                "results": [
                    {
                        "ruleId": "example.rule",
                        "locations": [None],
                        "message": {"text": "malformed location"},
                    }
                ]
            }
        ]
    }

    assert evaluate(report, _ROOT) == (1, 0)
