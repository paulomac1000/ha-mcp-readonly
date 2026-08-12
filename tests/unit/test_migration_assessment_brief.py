"""Migration-assessment CLI inputs must identify an immutable provider revision."""

import argparse

import pytest

from scripts.migration_assessment_brief import _positive_pr, _revision


def test_revision_requires_full_sha() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="40-character"):
        _revision("bf88ad0")
    assert _revision("A" * 40) == "a" * 40


def test_pr_requires_positive_integer() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="positive"):
        _positive_pr("0")
    assert _positive_pr("22") == 22
