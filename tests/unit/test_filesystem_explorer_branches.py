"""I/O-free branch coverage for filesystem explorer error handling."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.filesystem_explorer import (
    SecurityContext,
    _do_list_directory,
    _do_read_file,
)


def test_binary_probe_error_is_treated_as_binary(
    mock_filesystem_root: Path,
    filesystem_test_filename: str,
) -> None:
    target = mock_filesystem_root / filesystem_test_filename
    context = SecurityContext(allowed_directories=[mock_filesystem_root])
    with patch.object(Path, "open", side_effect=OSError("probe failed")):
        assert context.is_binary_file(target) is True


def test_list_directory_read_error_is_controlled(mock_filesystem_root: Path) -> None:
    target = MagicMock(spec=Path)
    target.is_dir.return_value = True
    target.iterdir.side_effect = OSError("directory unavailable")
    context = MagicMock(spec=SecurityContext)
    context.validate_path.return_value = target

    result = _do_list_directory(str(mock_filesystem_root), 10, context)

    assert result["success"] is False
    assert result["error"]["code"] == "INTERNAL_ERROR"
    assert result["error"]["retryable"] is True


def test_list_directory_skips_entry_rejected_by_metadata_policy(
    mock_filesystem_root: Path,
) -> None:
    target = MagicMock(spec=Path)
    target.is_dir.return_value = True
    entry = MagicMock(spec=Path)
    target.iterdir.return_value = [entry]
    context = MagicMock(spec=SecurityContext)
    context.validate_path.return_value = target
    context.validate_metadata_path.side_effect = PermissionError("blocked")
    context.allowed_directories = [mock_filesystem_root]

    result = _do_list_directory(str(mock_filesystem_root), 10, context)

    assert result["success"] is True
    assert result["entries"] == []
    assert result["total_entries"] == 1
    assert result["truncated"] is True


def test_read_file_disappearing_after_validation_is_not_found(
    mock_filesystem_root: Path,
    filesystem_test_filename: str,
) -> None:
    target = MagicMock(spec=Path)
    target.is_file.return_value = True
    target.stat.side_effect = FileNotFoundError("gone")
    context = MagicMock(spec=SecurityContext)
    context.validate_metadata_path.return_value = target
    context.validate_text_file.return_value = target
    context.is_binary_file.return_value = False

    result = _do_read_file(
        str(mock_filesystem_root / filesystem_test_filename),
        10,
        1,
        context,
    )

    assert result["success"] is False
    assert result["error"]["code"] == "RESOURCE_NOT_FOUND"
