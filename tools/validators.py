"""Centralized Input Validation for MCP tools.

Provides shared validation functions and a ValidationError exception class.
All tools SHOULD use these functions instead of inline validation.

Usage:
    from tools.validators import ValidationError, validate_path, validate_port

    try:
        path = validate_path(user_input)
    except ValidationError as exc:
        return _error_response(str(exc))
"""

import os
from pathlib import Path


class ValidationError(Exception):
    """Raised when input fails validation."""


def validate_path(path: str, allowed_dirs: list[str] | None = None) -> str:
    """Validate filesystem path — reject traversal, resolve to real path.

    Args:
        path: User-provided path string.
        allowed_dirs: List of allowed directory prefixes. If None, only blocks ``..`` and ``~``.

    Returns:
        Resolved absolute path.

    Raises:
        ValidationError: If path contains traversal or is outside allowed directories.
    """
    if not path or not isinstance(path, str):
        raise ValidationError("Path must be a non-empty string")
    if ".." in path.split(os.sep):
        raise ValidationError("Path traversal detected")
    if path.startswith("~"):
        raise ValidationError("Home directory shorthand not allowed")
    resolved = Path(path).resolve()
    if allowed_dirs:
        if not any(str(resolved).startswith(str(d)) for d in allowed_dirs):
            raise ValidationError(f"Path not in allowed directories: {resolved}")
    return str(resolved)


def validate_port(port: int) -> int:
    """Validate TCP/UDP port number is in 1-65535 range.

    Args:
        port: Port number as int.

    Returns:
        Validated port number.

    Raises:
        ValidationError: If port outside valid range.
    """
    p = int(port)
    if not 1 <= p <= 65535:
        raise ValidationError(f"Port must be 1-65535, got {p}")
    return p


def validate_nonempty(value: str, name: str = "parameter") -> str:
    """Validate a string parameter is not None or empty.

    Args:
        value: String value to validate.
        name: Parameter name for error message.

    Returns:
        The validated string.

    Raises:
        ValidationError: If value is None or empty.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"{name} must not be empty")
    return value


def validate_entity_id(entity_id: str) -> str:
    """Validate entity_id format (domain.name).

    Args:
        entity_id: Entity ID to validate.

    Returns:
        Validated entity ID.

    Raises:
        ValidationError: If format is invalid.
    """
    if not entity_id or not isinstance(entity_id, str):
        raise ValidationError("entity_id must be a non-empty string")
    if "." not in entity_id:
        raise ValidationError(f"Invalid entity_id format: {entity_id} (expected domain.name)")
    return entity_id
