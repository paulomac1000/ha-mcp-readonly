"""Request-scoped observability for MCP tool invocations.

Provides:
- A ``request_id`` bound to a ``contextvars.ContextVar`` (NEVER a module
  global -- a global is overwritten by concurrent async invocations and
  misattributes every subsequent log line).
- A ``logging.Filter`` that injects the current ``request_id`` into every
  log record so logs and the ``_meta`` envelope share the same id.
- A thread-safe per-tool invocation counter for the health endpoint.

Reference: mcp-server-standards.md, Observability rules 9/10, Canonical
Template 4c.
"""

import contextvars
import logging
import threading
import uuid

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_invocation_counts: dict[str, int] = {}
_invocation_lock = threading.Lock()


def start_tool_context() -> str:
    """Generate a fresh request_id and bind it to the current context.

    Call this once at the start of every tool wrapper, before any I/O or
    logging. Returns the generated id so the caller can place the SAME id
    in the response ``_meta`` envelope.
    """
    rid = str(uuid.uuid4())
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    """Return the request_id bound to the current context, or '-' if unset."""
    return _request_id.get()


def increment_invocation(tool_name: str) -> None:
    """Increment the invocation counter for a tool (thread-safe)."""
    with _invocation_lock:
        _invocation_counts[tool_name] = _invocation_counts.get(tool_name, 0) + 1


def get_invocation_counts() -> dict[str, int]:
    """Return a snapshot copy of per-tool invocation counts."""
    with _invocation_lock:
        return dict(_invocation_counts)


class RequestIdFilter(logging.Filter):
    """Logging filter that injects the current request_id into every record.

    Attach this to every log handler so the log format may reference
    ``%(request_id)s`` without raising ``KeyError`` on records emitted
    outside a tool context.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True
