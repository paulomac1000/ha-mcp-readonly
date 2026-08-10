"""Security boundaries shared by filesystem and HTTP adapters."""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class SecurityBoundaryError(PermissionError):
    """Raised when an input crosses a configured security boundary."""


_SENSITIVE_NAMES = frozenset(
    {
        "secrets.yaml",
        "secrets.yml",
        ".env",
        "auth",
        "auth_provider.homeassistant",
        "core.config_entries",
        "onboarding",
    }
)
_ALLOWED_TEXT_SUFFIXES = frozenset(
    {".yaml", ".yml", ".json", ".md", ".txt", ".log", ".jinja", ".j2", ".conf"}
)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class PathPolicy:
    """Resolve paths fail-closed under explicit roots.

    Generic filesystem tools intentionally cannot traverse ``.storage``. The
    application has dedicated registry readers that expose reviewed fields
    from selected storage records without exposing credentials.
    """

    roots: tuple[Path, ...]
    max_file_size: int = 5 * 1024 * 1024
    max_depth: int = 20
    allowed_suffixes: frozenset[str] = _ALLOWED_TEXT_SUFFIXES
    deny_storage: bool = True
    reject_symlinks: bool = True

    @classmethod
    def from_paths(
        cls,
        paths: list[Path] | tuple[Path, ...],
        *,
        max_file_size: int = 5 * 1024 * 1024,
        max_depth: int = 20,
        allowed_suffixes: frozenset[str] = _ALLOWED_TEXT_SUFFIXES,
        deny_storage: bool = True,
        reject_symlinks: bool = True,
    ) -> PathPolicy:
        roots = tuple(path.expanduser().resolve(strict=False) for path in paths)
        if not roots:
            raise ValueError("At least one filesystem root is required")
        return cls(
            roots=roots,
            max_file_size=max_file_size,
            max_depth=max_depth,
            allowed_suffixes=allowed_suffixes,
            deny_storage=deny_storage,
            reject_symlinks=reject_symlinks,
        )

    def _root_for(self, resolved: Path) -> Path:
        for root in self.roots:
            if _is_within(resolved, root):
                return root
        raise SecurityBoundaryError("Access denied: path is outside configured roots")

    def _reject_sensitive(self, relative: Path) -> None:
        lowered = tuple(part.casefold() for part in relative.parts)
        if self.deny_storage and ".storage" in lowered:
            raise SecurityBoundaryError("Access denied: generic access to .storage is blocked")
        for part in lowered:
            if part in _SENSITIVE_NAMES:
                raise SecurityBoundaryError(
                    "Access denied: sensitive Home Assistant data is blocked"
                )
            if part.startswith("auth_provider."):
                raise SecurityBoundaryError("Access denied: authentication data is blocked")

    def reject_symlink_components(self, candidate: Path) -> None:
        """Reject any existing symlink in an unresolved candidate path."""
        if not self.reject_symlinks:
            return
        unresolved = Path(os.path.abspath(os.fspath(candidate)))
        cursor = unresolved
        while cursor != cursor.parent:
            if cursor.is_symlink():
                raise SecurityBoundaryError("Access denied: symbolic links are not allowed")
            cursor = cursor.parent

    def resolve(
        self,
        raw_path: str | Path,
        *,
        require_exists: bool = True,
        require_file: bool = False,
        require_directory: bool = False,
        require_text: bool = False,
        check_file_size: bool = True,
    ) -> Path:
        text = os.fspath(raw_path)
        if not text or "\x00" in text:
            raise SecurityBoundaryError("Invalid path")
        raw_candidate = Path(text)
        if any(part in {"..", "~"} for part in raw_candidate.parts):
            raise SecurityBoundaryError("Access denied: ambiguous path components are not allowed")
        candidate = raw_candidate.expanduser()
        if not candidate.is_absolute():
            if len(self.roots) != 1:
                raise SecurityBoundaryError("Relative paths require exactly one configured root")
            candidate = self.roots[0] / candidate

        self.reject_symlink_components(candidate)
        resolved = candidate.resolve(strict=False)
        root = self._root_for(resolved)
        relative = resolved.relative_to(root)
        if len(relative.parts) > self.max_depth:
            raise SecurityBoundaryError("Access denied: path is too deep and exceeds maximum depth")
        self._reject_sensitive(relative)

        if require_exists and not resolved.exists():
            raise SecurityBoundaryError("Path does not exist")
        if require_file and not resolved.is_file():
            raise SecurityBoundaryError("Path is not a regular file")
        if require_directory and not resolved.is_dir():
            raise SecurityBoundaryError("Path is not a directory")
        if resolved.is_file():
            size = resolved.stat().st_size
            if check_file_size and size > self.max_file_size:
                raise SecurityBoundaryError(
                    f"File too large: exceeds maximum size ({size} > {self.max_file_size} bytes)"
                )
            if require_text and resolved.suffix.casefold() not in self.allowed_suffixes:
                raise SecurityBoundaryError(
                    f"File type is not allowlisted for text access: {resolved.name}"
                )
        return resolved


def resolve_output_path(raw_path: str | Path, output_root: str | Path) -> Path:
    """Resolve an artifact path below the configured output root."""
    policy = PathPolicy.from_paths(
        [Path(output_root)],
        max_file_size=50 * 1024 * 1024,
        max_depth=8,
        deny_storage=False,
    )
    target = policy.resolve(raw_path, require_exists=False)
    if target.suffix.casefold() not in {".md", ".json"}:
        raise SecurityBoundaryError("Context artifacts must use .md or .json")
    parent = target.parent
    root = policy.roots[0]
    try:
        parent.relative_to(root)
    except ValueError as exc:
        raise SecurityBoundaryError("Artifact path escapes the output root") from exc
    policy.reject_symlink_components(target)
    return target


def bearer_token_is_valid(headers: Mapping[str, str], expected_token: str) -> bool:
    """Validate an RFC 6750-style bearer token using constant-time comparison."""
    if not expected_token:
        return False
    value = headers.get("authorization", "")
    scheme, separator, supplied = value.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not supplied:
        return False
    return hmac.compare_digest(
        supplied.encode("utf-8", "surrogateescape"),
        expected_token.encode("utf-8", "surrogateescape"),
    )