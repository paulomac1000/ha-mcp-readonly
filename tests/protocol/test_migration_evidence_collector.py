"""Filesystem-boundary tests for the trusted migration evidence collector."""

from pathlib import Path

import pytest

from scripts.collect_migration_evidence import EvidenceError, _read_file_bounded


def test_bounded_evidence_read_accepts_regular_file(
    tmp_path: Path,
    evidence_filename: str,
) -> None:
    path = tmp_path / evidence_filename
    path.write_bytes(b"trusted-evidence")

    assert _read_file_bounded(path, limit=64, label="test evidence") == b"trusted-evidence"


def test_bounded_evidence_read_rejects_oversized_file(
    tmp_path: Path,
    evidence_filename: str,
) -> None:
    path = tmp_path / evidence_filename
    path.write_bytes(b"x" * 65)

    with pytest.raises(EvidenceError, match="exceeds byte limit"):
        _read_file_bounded(path, limit=64, label="test evidence")


def test_bounded_evidence_read_rejects_symlink(
    tmp_path: Path,
    evidence_filename: str,
    evidence_target_filename: str,
) -> None:
    target = tmp_path / evidence_target_filename
    target.write_bytes(b"trusted-evidence")
    link = tmp_path / evidence_filename
    link.symlink_to(target)

    with pytest.raises(EvidenceError, match="regular non-symlink file"):
        _read_file_bounded(link, limit=64, label="test evidence")
