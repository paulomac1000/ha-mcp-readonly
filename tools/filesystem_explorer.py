"""
Filesystem Explorer – read-only and allowlisted filesystem access for MCP.
Security patterns are inspired by mcp-filesystem-python without vendor imports.
All operations are read-only to avoid modifying the host system.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any

from tools.utils import _error_response, _success_response, create_error_response

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"

BINARY_EXTENSIONS: set[str] = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".sock",
    ".pid",
    ".bin",
    ".gz",
    ".zip",
    ".tar",
}


# =============================================================================
# SECURITY CONTEXT (allowlist-based protection)
# =============================================================================


class SecurityContext:
    """
    Validate filesystem paths against an allowlist to block traversal attempts.
    Patterns follow mcp-filesystem-python without importing the vendor package.
    """

    def __init__(
        self,
        allowed_directories: list[Path],
        max_file_size: int = 10 * 1024 * 1024,
        max_depth: int = 10,
    ):
        """
        Args:
            allowed_directories: List of allowed directories for browsing.
            max_file_size: Maximum file size in bytes (default 10MB).
            max_depth: Maximum directory depth allowed for traversal.
        """
        self.allowed_directories = [d.resolve() for d in allowed_directories]
        self.max_file_size = max_file_size
        self.max_depth = max_depth

    def validate_path(self, path: str | Path) -> Path:
        """
        Validate the path against the allowlist and guard against traversal.

        Raises:
            PermissionError: If the path is outside the allowlist or contains traversal.
        """
        # SECURITY: Block path traversal attempts before any processing
        path_str = str(path)

        # Block common traversal patterns
        if ".." in path_str:
            raise PermissionError("Path traversal blocked: '..' not allowed in path")
        if "~" in path_str:
            raise PermissionError("Path traversal blocked: '~' not allowed in path")
        if path_str.startswith("/"):
            allowed_roots = [str(d) for d in self.allowed_directories]
            if not any(path_str.startswith(root) for root in allowed_roots):
                raise PermissionError("Access denied: path must be within allowed directories")

        # Normalize to absolute Path
        target = Path(path).resolve()

        # SECURITY: Ensure target is strictly under allowed directories
        # Using str().startswith() is not enough - we need to check parent relationship
        is_allowed = False
        for allowed in self.allowed_directories:
            try:
                # This will raise ValueError if target is not under allowed
                target.relative_to(allowed)
                is_allowed = True
                break
            except ValueError:
                continue

        if not is_allowed:
            raise PermissionError("Access denied: path must be within /config")

        # Validate file size when applicable
        if target.is_file():
            try:
                size = target.stat().st_size
                if size > self.max_file_size:
                    raise PermissionError(
                        f"File too large ({size / 1024 / 1024:.1f}MB > {self.max_file_size / 1024 / 1024}MB limit): {target}"
                    )
            except FileNotFoundError:
                raise PermissionError(f"File not found: {target}")

        # Validate depth relative to the allowlisted base
        try:
            base_dir = next(a for a in self.allowed_directories if str(target).startswith(str(a)))
            rel_path = target.relative_to(base_dir)
            if len(rel_path.parts) > self.max_depth:
                raise PermissionError(
                    f"Path too deep (depth {len(rel_path.parts)} > {self.max_depth} limit): {target}"
                )
        except ValueError:
            raise PermissionError(f"Path validation failed: {target}")

        return target

    def is_binary_file(self, path: Path) -> bool:
        """Check if a file is binary based on extension and magic numbers."""
        if path.suffix.lower() in BINARY_EXTENSIONS:
            return True

        # Basic magic number and null-byte detection for safety
        try:
            with open(path, "rb") as f:
                header = f.read(16)
                if b"\x00" in header:
                    return True
        except Exception:
            pass

        return False


# Global security context – restricted to Home Assistant config only
# SECURITY: Only /config is allowed for public GitHub release
SECURITY_CONTEXT = SecurityContext(
    allowed_directories=[
        Path("/config"),  # Home Assistant configuration only
    ],
    max_file_size=10 * 1024 * 1024,  # 10MB limit
    max_depth=20,  # Maximum directory depth
)


# =============================================================================
# INTERNAL TOOL LOGIC (_do_ functions)
# =============================================================================


def _do_list_directory(path: str, max_entries: int) -> dict[str, Any]:
    """List directory contents with allowlist validation.

    Returns a dict (not JSON) so the wrapper can add _meta envelope.
    """
    try:
        target = SECURITY_CONTEXT.validate_path(path)
    except PermissionError as e:
        return create_error_response("ACCESS_DENIED", str(e), retryable=False)

    if not target.is_dir():
        return create_error_response("INVALID_PARAM", f"Not a directory: {target}", retryable=False)

    entries: list[dict[str, Any]] = []
    try:
        all_iter = list(target.iterdir())
    except OSError as e:
        return create_error_response(
            "INTERNAL_ERROR", f"Cannot read directory {target}: {e}", retryable=True
        )

    for entry in all_iter:
        if len(entries) >= max_entries:
            break
        try:
            stat = entry.stat()
            entries.append(
                {
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "size_bytes": stat.st_size if entry.is_file() else None,
                    "modified_timestamp": stat.st_mtime,
                    "is_binary": SECURITY_CONTEXT.is_binary_file(entry)
                    if entry.is_file()
                    else False,
                }
            )
        except PermissionError:
            continue

    total = len(all_iter)
    return {
        "success": True,
        "path": str(target),
        "entries_count": len(entries),
        "total_entries": total,
        "entries": entries,
        "truncated": len(entries) < total,
        "allowed_directories": [str(d) for d in SECURITY_CONTEXT.allowed_directories],
    }


def _do_read_file(file_path: str, max_lines: int, offset: int) -> dict[str, Any]:
    """Read a text file with allowlist validation and size limits."""
    try:
        target = SECURITY_CONTEXT.validate_path(file_path)
    except PermissionError as e:
        return create_error_response("ACCESS_DENIED", str(e), retryable=False)

    if not target.is_file():
        return create_error_response("INVALID_PARAM", f"Not a file: {target}", retryable=False)

    if SECURITY_CONTEXT.is_binary_file(target):
        return create_error_response(
            "UNSUPPORTED",
            f"Binary file type not allowed: {target}. Use list_directory to explore this location",
            retryable=False,
        )

    max_bytes = 5 * 1024 * 1024
    try:
        file_size = target.stat().st_size
    except FileNotFoundError:
        return create_error_response(
            "RESOURCE_NOT_FOUND", f"File not found: {target}", retryable=False
        )

    if file_size > max_bytes:
        return create_error_response(
            "VALIDATION_FAILED",
            f"File too large ({file_size} bytes > {max_bytes} max): {target}",
            retryable=False,
        )

    if offset < 1:
        offset = 1
    lines: list[str] = []
    encoding_used = "utf-8"
    try:
        with open(target, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i < offset - 1:
                    continue
                if i >= offset - 1 + max_lines:
                    break
                lines.append(line.rstrip("\n"))
    except UnicodeDecodeError:
        try:
            with open(target, encoding="latin-1", errors="replace") as f:
                encoding_used = "latin-1"
                for i, line in enumerate(f):
                    if i < offset - 1:
                        continue
                    if i >= offset - 1 + max_lines:
                        break
                    lines.append(line.rstrip("\n"))
        except Exception as e:
            return create_error_response(
                "INTERNAL_ERROR", f"File encoding not supported: {e}", retryable=False
            )
    except Exception as e:
        return create_error_response("INTERNAL_ERROR", f"Read failed: {e}", retryable=True)

    return {
        "success": True,
        "path": str(target),
        "offset": offset,
        "lines_count": len(lines),
        "total_lines_estimate": "unknown",
        "content": "\n".join(lines),
        "truncated": len(lines) >= max_lines,
        "encoding_used": encoding_used,
    }


def _do_search_files(pattern: str, search_path: str, max_results: int) -> dict[str, Any]:
    """Search for files containing a text pattern (safe grep)."""
    if not re.match(r"^[a-zA-Z0-9\s\-_\.\/\\:\[\]\(\)\{\}\+\*\?\^\$\|@#%&=!<>~\']+$", pattern):
        return create_error_response(
            "INVALID_PARAM",
            "Invalid search pattern: pattern contains blocked special characters",
            retryable=False,
        )

    try:
        target = SECURITY_CONTEXT.validate_path(search_path)
    except PermissionError as e:
        return create_error_response("ACCESS_DENIED", str(e), retryable=False)

    if not target.is_dir():
        return create_error_response(
            "INVALID_PARAM", f"Search path must be a directory: {target}", retryable=False
        )

    results: list[dict[str, Any]] = []
    files_searched = 0

    for root, dirs, files in os.walk(target):
        if root.count(os.sep) - str(target).count(os.sep) > SECURITY_CONTEXT.max_depth:
            dirs[:] = []
            continue

        for filename in files:
            files_searched += 1
            if len(results) >= max_results:
                break

            filepath = Path(root) / filename
            if SECURITY_CONTEXT.is_binary_file(filepath):
                continue
            try:
                if filepath.stat().st_size > 2 * 1024 * 1024:
                    continue
            except FileNotFoundError:
                continue

            try:
                with open(filepath, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    if re.search(re.escape(pattern), content, re.IGNORECASE):
                        matches: list[dict[str, Any]] = []
                        for match in re.finditer(re.escape(pattern), content, re.IGNORECASE):
                            start = max(0, match.start() - 30)
                            end = min(len(content), match.end() + 30)
                            context = content[start:end].replace("\n", " ").strip()
                            matches.append({"position": match.start(), "context": context})
                            if len(matches) >= 3:
                                break
                        results.append(
                            {
                                "path": str(filepath.relative_to(target)),
                                "absolute_path": str(filepath),
                                "matches_count": len(matches),
                                "sample_matches": matches[:3],
                            }
                        )
            except Exception:
                continue

    return {
        "success": True,
        "pattern": pattern,
        "search_path": str(target),
        "files_searched": files_searched,
        "results_count": len(results),
        "results": results[:max_results],
        "truncated": len(results) > max_results,
    }


# =============================================================================
# FILESYSTEM EXPLORER (MCP tools)
# =============================================================================


def register_filesystem_tools(mcp) -> None:  # type: ignore[no-untyped-def]
    """Register filesystem tools on the MCP server with allowlist enforcement."""

    @mcp.tool()
    def list_directory(path: str = "/config", max_entries: int = 100) -> str:
        """[READ] List directory contents with allowlist validation.

        Args:
            path: Directory path (must be within the allowlist).
            max_entries: Maximum number of entries to return (default 100).

        Returns:
            JSON string containing the entries or an error message.
        """
        try:
            data = _do_list_directory(path, max_entries)
            if data.get("success") is False:
                return _error_response(data.get("error", data))
            return _success_response(data)
        except Exception as exc:
            _logger.exception("list_directory failed")
            return _error_response(str(exc))

    @mcp.tool()
    def read_file(file_path: str, max_lines: int = 200, offset: int = 1) -> str:
        """[READ] Read a text file with allowlist validation and size limits.

        Args:
            file_path: Path to the file (must be within the allowlist).
            max_lines: Maximum number of lines to return (default 200).
            offset: Line number to start reading from, 1-indexed (default 1).

        Returns:
            JSON string with file content or error details.
        """
        try:
            data = _do_read_file(file_path, max_lines, offset)
            if data.get("success") is False:
                return _error_response(data.get("error", data))
            return _success_response(data)
        except Exception as exc:
            _logger.exception("read_file failed")
            return _error_response(str(exc))

    @mcp.tool()
    def search_files(pattern: str, search_path: str = "/config", max_results: int = 50) -> str:
        """[READ] Search for files containing a text pattern (safe grep).

        Args:
            pattern: Pattern to search for (only safe characters allowed).
            search_path: Directory to search (default /config).
            max_results: Maximum number of results (default 50).

        Returns:
            JSON string with search results or error information.
        """
        try:
            data = _do_search_files(pattern, search_path, max_results)
            if data.get("success") is False:
                return _error_response(data.get("error", data))
            return _success_response(data)
        except Exception as exc:
            _logger.exception("search_files failed")
            return _error_response(str(exc))
