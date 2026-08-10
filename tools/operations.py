"""Application-owned operation registry and transport-neutral dispatcher."""

from __future__ import annotations

import functools
import inspect
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tools.invocation import KERNEL
from tools.manifests import KNOWN_RISK_PREFIXES, get_manifest
from tools.observability import increment_invocation, start_tool_context
from tools.redaction import sanitize_response_data
from tools.utils import build_meta

_BLOCKING_COROUTINE_MODULES = frozenset({"tools.storage"})


@dataclass(frozen=True, slots=True)
class Operation:
    """One application-owned operation independent of the MCP SDK."""

    name: str
    fn: Callable[..., Any]
    raw_fn: Callable[..., Any]
    description: str


class OperationRegistry:
    """Thread-safe registry populated before transport adapters expose operations."""

    def __init__(self) -> None:
        self._operations: dict[str, Operation] = {}
        self._lock = threading.RLock()

    def register(self, name: str, raw_fn: Callable[..., Any]) -> Operation:
        manifest = get_manifest(name)
        if manifest is None:
            raise RuntimeError(f"Missing explicit manifest for {name}")
        operation_kind = str(manifest["operation_kind"])
        prefix = {
            "read": "READ",
            "write": "WRITE",
            "destructive": "DESTRUCTIVE",
        }[operation_kind]
        doc = (raw_fn.__doc__ or "").strip()
        for known in KNOWN_RISK_PREFIXES:
            if doc.startswith(known):
                doc = doc[len(known) :].lstrip()
                break
        description = f"[{prefix}] {doc}".rstrip()
        wrapped = self._wrap(name, raw_fn, description)
        operation = Operation(name=name, fn=wrapped, raw_fn=raw_fn, description=description)
        with self._lock:
            if name in self._operations:
                raise RuntimeError(f"Duplicate operation registration: {name}")
            self._operations[name] = operation
        return operation

    @staticmethod
    def _wrap(name: str, raw_fn: Callable[..., Any], description: str) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(raw_fn):
            blocking_coroutine = raw_fn.__module__ in _BLOCKING_COROUTINE_MODULES

            @functools.wraps(raw_fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.monotonic()
                start_tool_context()
                increment_invocation(name)
                if blocking_coroutine:
                    result = await KERNEL.invoke_blocking_coroutine(name, raw_fn, *args, **kwargs)
                else:
                    result = await KERNEL.invoke_async(name, raw_fn, *args, **kwargs)
                return _augment_result(result, name, start)

            async_wrapper.__doc__ = description
            return async_wrapper

        @functools.wraps(raw_fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            start_tool_context()
            increment_invocation(name)
            result = KERNEL.invoke_sync(name, raw_fn, *args, **kwargs)
            return _augment_result(result, name, start)

        sync_wrapper.__doc__ = description
        return sync_wrapper

    def get(self, name: str) -> Operation | None:
        with self._lock:
            return self._operations.get(name)

    def all(self) -> dict[str, Operation]:
        with self._lock:
            return dict(self._operations)

    def names(self) -> set[str]:
        with self._lock:
            return set(self._operations)


class OperationMCPAdapter:
    """Expose application-owned operations through FastMCP's public decorator API."""

    def __init__(self, server: Any, registry: OperationRegistry) -> None:
        self._server = server
        self._registry = registry

    def names(self) -> set[str]:
        """Return names exposed by this deployment's application registry."""
        return self._registry.names()

    def tool(
        self, *decorator_args: Any, **decorator_kwargs: Any
    ) -> Callable[[Callable[..., Any]], Any]:
        server_decorator = self._server.tool(*decorator_args, **decorator_kwargs)
        explicit_name = decorator_kwargs.get("name")

        def register(raw_fn: Callable[..., Any]) -> Any:
            name = str(explicit_name or raw_fn.__name__)
            operation = self._registry.register(name, raw_fn)
            return server_decorator(operation.fn)

        return register


def _augment_result(result: Any, tool_name: str, start: float) -> Any:
    """Sanitize, attach metadata, then enforce the final serialized size limit."""
    import json

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            sanitized_text = sanitize_response_data(result)
            KERNEL.enforce_final_result_size(tool_name, sanitized_text)
            return sanitized_text
        sanitized = sanitize_response_data(parsed)
        if isinstance(sanitized, dict):
            sanitized["_meta"] = _merged_meta(sanitized.get("_meta"), build_meta(tool_name, start))
            encoded = json.dumps(sanitized, indent=2, ensure_ascii=False)
            KERNEL.enforce_final_result_size(tool_name, encoded)
            return encoded
        KERNEL.enforce_final_result_size(tool_name, sanitized)
        return sanitized
    if isinstance(result, dict):
        sanitized = sanitize_response_data(result)
        if not isinstance(sanitized, dict):
            raise TypeError("Sanitized dictionary result changed type")
        sanitized["_meta"] = _merged_meta(sanitized.get("_meta"), build_meta(tool_name, start))
        KERNEL.enforce_final_result_size(tool_name, sanitized)
        return sanitized
    sanitized = sanitize_response_data(result)
    KERNEL.enforce_final_result_size(tool_name, sanitized)
    return sanitized


def _merged_meta(tool_meta: Any, envelope: dict[str, Any]) -> dict[str, Any]:
    """Merge the invocation envelope with tool-provided metadata.

    Tools may attach their own ``_meta`` (for example pagination truncation
    markers). The envelope fields win on conflict, but tool fields such as
    ``truncated`` and ``total_count`` must survive the augmentation.
    """
    if isinstance(tool_meta, dict):
        merged = dict(tool_meta)
        merged.update(envelope)
        return merged
    return envelope
