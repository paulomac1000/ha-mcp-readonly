"""
Tests for tools/filesystem_explorer.py — compatible with Python 3.9
Safe access to file system with allowlist validation.
"""

import json
import tempfile
from pathlib import Path

import pytest

import tools.filesystem_explorer as fe_module  # For safe global context replacement
from tools.filesystem_explorer import SecurityContext, register_filesystem_tools

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def temp_dir():
    """Creates temporary directory with example structure including binary file db.sqlite."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # Create directories
        (base / "allowed").mkdir()
        (base / "allowed" / "subdir").mkdir()
        (base / "blocked").mkdir()

        # Create text files
        (base / "allowed" / "file1.txt").write_text("Line 1\nLine 2\nSecret: abc123xyz\n")
        (base / "allowed" / "large.log").write_text("X" * 11 * 1024 * 1024)  # 11MB
        (base / "allowed" / "db.sqlite").write_bytes(
            b"SQLite format 3\x00"  # Magic header SQLite
            + b"\x00" * 100  # Additional bytes for certainty
        )

        # Create file in forbidden directory
        (base / "blocked" / "secret.txt").write_text("Top secret")

        yield base


@pytest.fixture
def security_context(temp_dir):
    """SecurityContext with allowlist on temporary directory."""
    return SecurityContext(
        allowed_directories=[temp_dir / "allowed"],
        max_file_size=10 * 1024 * 1024,  # 10MB
        max_depth=5,
    )


@pytest.fixture
def allow_temp_dir_in_security_context(temp_dir):
    """
    Solves closure capture problem - tools capture context at registration.
    """
    original_context = fe_module.SECURITY_CONTEXT
    new_context = SecurityContext(
        allowed_directories=[temp_dir / "allowed"],
        max_file_size=10 * 1024 * 1024,
        max_depth=15,  # Adjusted for tests with subdirectories
    )
    fe_module.SECURITY_CONTEXT = new_context
    yield new_context
    # Restore original context after test
    fe_module.SECURITY_CONTEXT = original_context


@pytest.fixture
def mock_mcp():
    """Mock MCP server for registering tools."""

    class MockMCP:
        def __init__(self):
            self._tools = {}

        def tool(self, name=None, description=None):
            def decorator(func):
                self._tools[name or func.__name__] = func
                return func

            return decorator

        def get_tool(self, name):
            return self._tools.get(name)

    return MockMCP()


# =============================================================================
# UNIT TESTS: SecurityContext
# =============================================================================


class TestSecurityContext:
    """Path security validation tests."""

    def test_allowed_path(self, security_context, temp_dir):
        """Test allowed path."""
        path = security_context.validate_path(temp_dir / "allowed" / "file1.txt")
        assert path.name == "file1.txt"
        assert "allowed" in str(path)

    def test_blocked_path_traversal(self, security_context, temp_dir):
        """Test path traversal blocking."""
        with pytest.raises(PermissionError, match="Access denied"):
            security_context.validate_path(temp_dir / "blocked" / "secret.txt")

    def test_path_traversal_attempts(self, security_context, temp_dir):
        """Test various path traversal techniques."""
        traversal_attempts = [
            "../blocked/secret.txt",
            "..\\blocked\\secret.txt",
            "%2e%2e/blocked/secret.txt",
            "/etc/passwd",
            "../../../../etc/shadow",
        ]
        for attempt in traversal_attempts:
            with pytest.raises(PermissionError):
                security_context.validate_path(attempt)

    def test_max_file_size(self, security_context, temp_dir):
        """Test blocking of overly large files."""
        with pytest.raises(PermissionError, match="too large"):
            security_context.validate_path(temp_dir / "allowed" / "large.log")

    def test_max_depth(self, security_context, temp_dir):
        """Test blocking of overly deep paths."""
        # Create deep structure
        deep_path = temp_dir / "allowed"
        for i in range(6):  # 6 > max_depth=5
            deep_path = deep_path / f"level{i}"
            deep_path.mkdir(exist_ok=True)

        (deep_path / "deep.txt").write_text("Deep file")

        with pytest.raises(PermissionError, match="too deep"):
            security_context.validate_path(deep_path / "deep.txt")


# =============================================================================
# UNIT TESTS: Filesystem Tools
# =============================================================================


class TestFilesystemTools:
    """MCP tools tests for file system."""

    def test_list_directory_success(self, mock_mcp, temp_dir, allow_temp_dir_in_security_context):
        """Test correct directory listing."""
        register_filesystem_tools(mock_mcp)
        tool = mock_mcp.get_tool("list_directory")

        result_str = tool(path=str(temp_dir / "allowed"), max_entries=10)
        result = json.loads(result_str)

        assert result["success"] is True
        assert result["path"] == str(temp_dir / "allowed")
        assert result["entries_count"] >= 3  # file1.txt, large.log, db.sqlite, subdir
        assert any(e["name"] == "file1.txt" for e in result["entries"])

    def test_list_directory_blocked_path(
        self, mock_mcp, temp_dir, allow_temp_dir_in_security_context
    ):
        """Path outside allowlist → Access denied."""
        register_filesystem_tools(mock_mcp)
        result = json.loads(mock_mcp.get_tool("list_directory")(path=str(temp_dir / "blocked")))
        assert "error" in result
        assert "Access denied" in result["error"]

    def test_list_directory_not_a_directory(
        self, mock_mcp, temp_dir, allow_temp_dir_in_security_context
    ):
        """Path to file instead of directory → Not a directory."""
        register_filesystem_tools(mock_mcp)
        result = json.loads(
            mock_mcp.get_tool("list_directory")(path=str(temp_dir / "allowed" / "file1.txt"))
        )
        assert "error" in result
        assert "Not a directory" in result["error"]

    def test_list_directory_truncation(
        self, mock_mcp, temp_dir, allow_temp_dir_in_security_context
    ):
        """max_entries=1 przy >1 fileu → truncated: True."""
        register_filesystem_tools(mock_mcp)
        result = json.loads(
            mock_mcp.get_tool("list_directory")(path=str(temp_dir / "allowed"), max_entries=1)
        )
        assert result["success"] is True
        assert result["entries_count"] == 1
        assert result["truncated"] is True

    def test_read_file_success(self, mock_mcp, temp_dir, allow_temp_dir_in_security_context):
        """Test correct file reading."""
        register_filesystem_tools(mock_mcp)
        tool = mock_mcp.get_tool("read_file")

        result_str = tool(file_path=str(temp_dir / "allowed" / "file1.txt"), max_lines=10)
        result = json.loads(result_str)

        assert result["success"] is True
        assert "Line 1" in result["content"]
        assert "abc123xyz" in result["content"]  # Secret is not redacted (AI does this)

    def test_read_binary_file_blocked(self, mock_mcp, temp_dir, allow_temp_dir_in_security_context):
        """
        1. file db.sqlite EXISTS (created in temp_dir fixture)
        2. Security context allows access to allowed/
        3. Real binary detection via .sqlite extension + magic bytes
        4. No mocking - tests REAL security logic
        """
        register_filesystem_tools(mock_mcp)
        tool = mock_mcp.get_tool("read_file")

        # File now exists and is correctly detected as binary
        result_str = tool(file_path=str(temp_dir / "allowed" / "db.sqlite"), max_lines=10)
        result = json.loads(result_str)

        assert "error" in result
        assert "binary" in result["error"].lower()
        assert "db.sqlite" in result["error"]

    def test_read_file_not_a_file(self, mock_mcp, temp_dir, allow_temp_dir_in_security_context):
        """Directory passed as path → Not a file."""
        register_filesystem_tools(mock_mcp)
        result = json.loads(
            mock_mcp.get_tool("read_file")(file_path=str(temp_dir / "allowed" / "subdir"))
        )
        assert "error" in result
        assert "Not a file" in result["error"]

    def test_read_file_blocked_path(self, mock_mcp, temp_dir, allow_temp_dir_in_security_context):
        """Path outside allowlist → Access denied."""
        register_filesystem_tools(mock_mcp)
        result = json.loads(
            mock_mcp.get_tool("read_file")(file_path=str(temp_dir / "blocked" / "secret.txt"))
        )
        assert "error" in result
        assert "Access denied" in result["error"]

    def test_read_file_max_lines_truncation(
        self, mock_mcp, temp_dir, allow_temp_dir_in_security_context
    ):
        """max_lines=1 dla fileu z 3 liniami → truncated: True."""
        register_filesystem_tools(mock_mcp)
        result = json.loads(
            mock_mcp.get_tool("read_file")(
                file_path=str(temp_dir / "allowed" / "file1.txt"), max_lines=1
            )
        )
        assert result["success"] is True
        assert result["truncated"] is True
        assert result["lines_count"] == 1

    def test_search_files_success(self, mock_mcp, temp_dir, allow_temp_dir_in_security_context):
        """
        1. Security context allows access to allowed/ BEFORE tool registration
        2. No late patching (solved closure capture problem)
        3. Pattern "secret" exists in file1.txt
        """
        register_filesystem_tools(mock_mcp)
        tool = mock_mcp.get_tool("search_files")

        # Safe search in allowed directory
        result_str = tool(pattern="secret", search_path=str(temp_dir / "allowed"), max_results=5)
        result = json.loads(result_str)

        assert result["success"] is True
        assert result["results_count"] >= 1
        assert any("file1.txt" in str(r.get("path", "")) for r in result["results"])

    def test_search_files_invalid_pattern(
        self, mock_mcp, temp_dir, allow_temp_dir_in_security_context
    ):
        """Pattern with forbidden characters → Invalid search pattern."""
        register_filesystem_tools(mock_mcp)
        result = json.loads(
            mock_mcp.get_tool("search_files")(
                pattern="secret`$(rm -rf /)`", search_path=str(temp_dir / "allowed")
            )
        )
        assert "error" in result
        assert "Invalid search pattern" in result["error"]

    def test_search_files_no_results(self, mock_mcp, temp_dir, allow_temp_dir_in_security_context):
        """Pattern not found → results_count: 0."""
        register_filesystem_tools(mock_mcp)
        result = json.loads(
            mock_mcp.get_tool("search_files")(
                pattern="xyzzy_not_in_any_file_12345", search_path=str(temp_dir / "allowed")
            )
        )
        assert result["success"] is True
        assert result["results_count"] == 0


# =============================================================================
# EDGE CASE TESTS
# =============================================================================


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_directory(self, security_context, temp_dir):
        """Test empty directory."""
        empty_dir = temp_dir / "allowed" / "empty"
        empty_dir.mkdir()

        target = security_context.validate_path(empty_dir)
        assert target.is_dir()

    def test_unicode_filenames(self, security_context, temp_dir):
        """Test unicode file names."""
        unicode_file = temp_dir / "allowed" / "unicode_test_file_äöü.txt"
        unicode_file.write_text("Quick brown fox jumps", encoding="utf-8")

        target = security_context.validate_path(unicode_file)
        assert target.exists()
        assert "unicode_test_file" in target.name.lower()

    def test_symlink_protection(self, security_context, temp_dir):
        """Test protection against symlinks to forbidden locations."""
        # Create symlink to forbidden directory
        safe_file = temp_dir / "allowed" / "safe.txt"
        safe_file.write_text("Safe content")

        symlink = temp_dir / "allowed" / "symlink_to_blocked"
        try:
            symlink.symlink_to(temp_dir / "blocked" / "secret.txt")
        except OSError:
            # Symlinks may be blocked in some environments – skip test
            pytest.skip("Symlinks not supported in this environment")

        # Validation should reject symlink (resolve() resolves symlinks)
        with pytest.raises(PermissionError):
            security_context.validate_path(symlink)
