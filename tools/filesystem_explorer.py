"""
Filesystem Explorer – read-only and allowlisted filesystem access for MCP.
Security patterns are inspired by mcp-filesystem-python without vendor imports.
All operations are read-only to avoid modifying the host system.
"""

import json
import os
import re
from pathlib import Path
from typing import List, Union

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
        allowed_directories: List[Path],
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

    def validate_path(self, path: Union[str, Path]) -> Path:
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
# FILESYSTEM EXPLORER (MCP tools)
# =============================================================================


def register_filesystem_tools(mcp) -> None:
    """Register filesystem tools on the MCP server with allowlist enforcement."""

    @mcp.tool()
    def list_directory(path: str = "/config", max_entries: int = 100) -> str:
        """
        List directory contents with allowlist validation.

        Args:
            path: Directory path (must be within the allowlist).
            max_entries: Maximum number of entries to return (default 100).

        Returns:
            JSON string containing the entries or an error message.
        """
        try:
            target = SECURITY_CONTEXT.validate_path(path)

            if not target.is_dir():
                return json.dumps(
                    {
                        "error": "Not a directory",
                        "path": str(target),
                        "allowed_directories": [
                            str(d) for d in SECURITY_CONTEXT.allowed_directories
                        ],
                    },
                    indent=2,
                )

            entries = []
            for entry in target.iterdir():
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

            return json.dumps(
                {
                    "success": True,
                    "path": str(target),
                    "entries_count": len(entries),
                    "total_entries": len(list(target.iterdir())),
                    "entries": entries,
                    "truncated": len(entries) < len(list(target.iterdir())),
                    "allowed_directories": [str(d) for d in SECURITY_CONTEXT.allowed_directories],
                },
                indent=2,
                default=str,
            )

        except PermissionError as e:
            return json.dumps(
                {
                    "error": "Access denied",
                    "message": str(e),
                    "allowed_directories": [str(d) for d in SECURITY_CONTEXT.allowed_directories],
                },
                indent=2,
            )
        except Exception as e:
            return json.dumps(
                {"error": "Operation failed", "message": str(e), "path": str(path)},
                indent=2,
            )

    @mcp.tool()
    def read_file(path: str, max_lines: int = 200) -> str:
        """
        Read a text file with allowlist validation and size limits.

        Args:
            path: Path to the file (must be within the allowlist).
            max_lines: Maximum number of lines to return (default 200).

        Returns:
            JSON string with file content or error details.
        """
        try:
            target = SECURITY_CONTEXT.validate_path(path)

            if not target.is_file():
                return json.dumps({"error": "Not a file", "path": str(target)}, indent=2)

            # Block binary files
            if SECURITY_CONTEXT.is_binary_file(target):
                return json.dumps(
                    {
                        "error": "Binary file type not allowed",
                        "path": str(target),
                        "suggestion": "Use list_directory to explore this location",
                    },
                    indent=2,
                )

            # Limit read size for safety
            max_bytes = 5 * 1024 * 1024  # 5MB hard limit
            try:
                if target.stat().st_size > max_bytes:
                    return json.dumps(
                        {
                            "error": "File too large for safe reading",
                            "size_bytes": target.stat().st_size,
                            "max_allowed_bytes": max_bytes,
                            "path": str(target),
                        },
                        indent=2,
                    )
            except FileNotFoundError:
                return json.dumps({"error": "File not found", "path": str(target)}, indent=2)

            # Safe read with line limits
            lines = []
            try:
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f):
                        if i >= max_lines:
                            break
                        lines.append(line.rstrip("\n"))
            except UnicodeDecodeError:
                # Fallback to latin-1 if UTF-8 fails
                try:
                    with open(target, "r", encoding="latin-1", errors="replace") as f:
                        for i, line in enumerate(f):
                            if i >= max_lines:
                                break
                            lines.append(line.rstrip("\n"))
                except Exception as e:
                    return json.dumps(
                        {
                            "error": "File encoding not supported",
                            "message": str(e),
                            "path": str(target),
                        },
                        indent=2,
                    )
            except Exception as e:
                return json.dumps(
                    {"error": "Read failed", "message": str(e), "path": str(target)},
                    indent=2,
                )

            return json.dumps(
                {
                    "success": True,
                    "path": str(target),
                    "lines_count": len(lines),
                    "total_lines_estimate": "unknown",  # Do not count all lines to avoid overhead
                    "content": "\n".join(lines),
                    "truncated": len(lines) >= max_lines,
                    "encoding_used": "utf-8"
                    if all(ord(c) < 128 for c in "".join(lines[:10]))
                    else "detected",
                },
                indent=2,
                default=str,
            )

        except PermissionError as e:
            return json.dumps(
                {
                    "error": "Access denied",
                    "message": str(e),
                    "allowed_directories": [str(d) for d in SECURITY_CONTEXT.allowed_directories],
                },
                indent=2,
            )
        except Exception as e:
            return json.dumps(
                {"error": "Operation failed", "message": str(e), "path": str(path)},
                indent=2,
            )

    @mcp.tool()
    def search_files(pattern: str, path: str = "/config", max_results: int = 50) -> str:
        """
        Search for files containing a text pattern (safe grep).

        Args:
            pattern: Pattern to search for (only safe characters allowed).
            path: Directory to search (default /config).
            max_results: Maximum number of results (default 50).

        Returns:
            JSON string with search results or error information.
        """
        # Pattern validation prevents regex injection
        if not re.match(r"^[a-zA-Z0-9\s\-_\.\/\\:\[\]\(\)\{\}\+\*\?\^\$\|@#%&=!<>~\']+$", pattern):
            return json.dumps(
                {
                    "error": "Invalid search pattern",
                    "message": "Pattern contains blocked special characters",
                    "allowed_chars": "alphanumeric + basic punctuation",
                },
                indent=2,
            )

        try:
            target = SECURITY_CONTEXT.validate_path(path)

            if not target.is_dir():
                return json.dumps(
                    {"error": "Search path must be a directory", "path": str(target)},
                    indent=2,
                )

            results = []
            files_searched = 0

            # Walk the directory tree with depth limit
            for root, dirs, files in os.walk(target):
                if root.count(os.sep) - str(target).count(os.sep) > SECURITY_CONTEXT.max_depth:
                    dirs[:] = []
                    continue

                for filename in files:
                    files_searched += 1
                    if len(results) >= max_results:
                        break

                    filepath = Path(root) / filename

                    # Skip binary files
                    if SECURITY_CONTEXT.is_binary_file(filepath):
                        continue

                    # Skip overly large files
                    try:
                        if filepath.stat().st_size > 2 * 1024 * 1024:  # 2MB limit for searching
                            continue
                    except FileNotFoundError:
                        continue

                    # Safe read and search
                    try:
                        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            if re.search(re.escape(pattern), content, re.IGNORECASE):
                                matches = []
                                for match in re.finditer(
                                    re.escape(pattern), content, re.IGNORECASE
                                ):
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
                        # Skip files that cannot be read
                        continue

            return json.dumps(
                {
                    "success": True,
                    "pattern": pattern,
                    "search_path": str(target),
                    "files_searched": files_searched,
                    "results_count": len(results),
                    "results": results[:max_results],
                    "truncated": len(results) > max_results,
                },
                indent=2,
                default=str,
            )

        except PermissionError as e:
            return json.dumps(
                {
                    "error": "Access denied",
                    "message": str(e),
                    "allowed_directories": [str(d) for d in SECURITY_CONTEXT.allowed_directories],
                },
                indent=2,
            )
        except Exception as e:
            return json.dumps(
                {
                    "error": "Search failed",
                    "message": str(e),
                    "pattern": pattern,
                    "path": path,
                },
                indent=2,
            )
