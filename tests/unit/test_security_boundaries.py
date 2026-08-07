"""Tests for filesystem and artifact containment boundaries."""

from pathlib import Path

import pytest

from tools.security import (
    PathPolicy,
    SecurityBoundaryError,
    bearer_token_is_valid,
    resolve_output_path,
)


def test_prefix_sibling_is_not_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "config"
    sibling = tmp_path / "config-backup"
    root.mkdir()
    sibling.mkdir()
    policy = PathPolicy.from_paths([root])
    with pytest.raises(SecurityBoundaryError):
        policy.resolve(sibling)


def test_sensitive_and_storage_files_are_blocked(tmp_path: Path) -> None:
    root = tmp_path / "config"
    storage = root / ".storage"
    storage.mkdir(parents=True)
    (root / "secrets.yaml").write_text("token: secret")
    (storage / "auth").write_text("secret")
    policy = PathPolicy.from_paths([root])
    with pytest.raises(SecurityBoundaryError):
        policy.resolve(root / "secrets.yaml", require_file=True)
    with pytest.raises(SecurityBoundaryError):
        policy.resolve(storage / "auth", require_file=True)


def test_symlink_escape_is_blocked(tmp_path: Path) -> None:
    root = tmp_path / "config"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "data.yaml").write_text("value: 1")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    policy = PathPolicy.from_paths([root])
    with pytest.raises(SecurityBoundaryError):
        policy.resolve(root / "linked" / "data.yaml", require_file=True)


def test_artifact_output_is_confined_and_typed(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    assert resolve_output_path(root / "context.md", root) == root / "context.md"
    with pytest.raises(SecurityBoundaryError):
        resolve_output_path(tmp_path / "escape.md", root)
    with pytest.raises(SecurityBoundaryError):
        resolve_output_path(root / "context.sh", root)


def test_bearer_token_validation() -> None:
    assert bearer_token_is_valid({"authorization": "Bearer s3cret"}, "s3cret") is True
    assert bearer_token_is_valid({"authorization": "Bearer wrong"}, "s3cret") is False
    assert bearer_token_is_valid({}, "s3cret") is False
    assert bearer_token_is_valid({"authorization": "Basic abc"}, "s3cret") is False
    assert bearer_token_is_valid({"authorization": "Bearer s3cret"}, "") is False


def test_non_ascii_bearer_token_fails_closed() -> None:
    assert bearer_token_is_valid({"authorization": "Bearer żółć"}, "ascii-token") is False
