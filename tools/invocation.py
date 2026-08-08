"""Shared invocation policy for MCP and optional HTTP adapters."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any


class InvocationError(RuntimeError):
    """Stable, non-sensitive invocation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Principal:
    subject: str
    transport: str
    capabilities: frozenset[str]
    targets: frozenset[str] = frozenset({"runtime", "home_assistant", "home_assistant_config"})
    credential_id: str | None = None


LOCAL_PRINCIPAL = Principal(
    subject="local-process",
    transport="stdio",
    capabilities=frozenset({"ha.read", "filesystem.read", "artifact.read"}),
)

_current_principal: contextvars.ContextVar[Principal] = contextvars.ContextVar(
    "ha_mcp_principal", default=LOCAL_PRINCIPAL
)
_current_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "ha_mcp_deadline", default=None
)


@contextlib.contextmanager
def principal_scope(principal: Principal) -> Iterator[None]:
    token = _current_principal.set(principal)
    try:
        yield
    finally:
        _current_principal.reset(token)


def current_principal() -> Principal:
    return _current_principal.get()


def set_process_principal(principal: Principal) -> None:
    """Set the principal inherited by subsequently created server tasks."""
    _current_principal.set(principal)


def remaining_budget_seconds() -> float | None:
    """Return the remaining invocation budget for nested I/O operations."""
    deadline = _current_deadline.get()
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


class InvocationKernel:
    """Enforce manifests, authorization, deadlines, concurrency and output bounds."""

    def __init__(self, max_workers: int = 16, max_queue: int | None = None) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        queue_capacity = max_workers if max_queue is None else max_queue
        if queue_capacity < 0:
            raise ValueError("max_queue cannot be negative")
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ha-tool")
        # ThreadPoolExecutor itself has an unbounded queue. This semaphore bounds
        # running plus queued synchronous invocations.
        self._executor_capacity = threading.BoundedSemaphore(max_workers + queue_capacity)
        self._locks: dict[str, tuple[int, threading.BoundedSemaphore]] = {}
        self._locks_guard = threading.Lock()

    def _manifest(self, tool_name: str) -> dict[str, Any]:
        from tools.manifests import get_manifest, inactive_reason, is_tool_active

        manifest = get_manifest(tool_name)
        if manifest is None:
            raise InvocationError("MANIFEST_MISSING", f"Tool '{tool_name}' has no manifest")
        if not is_tool_active(tool_name):
            reason = (
                inactive_reason(tool_name) or "Capability is inactive in this deployment profile"
            )
            raise InvocationError("UNAVAILABLE_DEPENDENCY", reason)
        return manifest

    @staticmethod
    def _declared_target(manifest: dict[str, Any]) -> str:
        binding = manifest.get("extensions", {}).get("target_binding")
        if not isinstance(binding, dict):
            raise InvocationError("MANIFEST_INVALID", "Operation target binding is missing")
        target = binding.get("target")
        if not isinstance(target, str) or not target:
            raise InvocationError(
                "MANIFEST_INVALID", "Operation target binding has no stable target"
            )
        if binding.get("kind") != "deployment-resource":
            raise InvocationError("MANIFEST_INVALID", "Unsupported target binding kind")
        return target

    def _authorize(self, tool_name: str, manifest: dict[str, Any]) -> str:
        required_scopes = {str(scope) for scope in manifest["authorization_scopes"]}
        principal = current_principal()
        missing = required_scopes - principal.capabilities
        if missing:
            required = sorted(missing)[0]
            raise InvocationError(
                "FORBIDDEN",
                f"Principal '{principal.subject}' lacks capability '{required}' for '{tool_name}'",
            )
        target = self._declared_target(manifest)
        if target not in principal.targets:
            raise InvocationError(
                "FORBIDDEN",
                f"Principal '{principal.subject}' is not authorized for target '{target}'",
            )
        # deployment-resource bindings resolve to one immutable process target;
        # re-reading the same manifest immediately before execution is the
        # revalidation step and prevents transport adapters from substituting it.
        if self._declared_target(self._manifest(tool_name)) != target:
            raise InvocationError("TARGET_CHANGED", "Operation target changed during authorization")
        return target

    def _concurrency_key(
        self,
        tool_name: str,
        manifest: dict[str, Any],
        target: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        del args
        scope = str(manifest["concurrency"]["scope"])
        principal = current_principal()
        if scope == "global":
            return "global"
        if scope == "capability":
            return f"capability:{tool_name}"
        if scope == "principal":
            return f"principal:{principal.subject}"
        if scope == "target":
            return f"target:{target}"
        if scope == "principal-target":
            return f"principal-target:{principal.subject}:{target}"
        if scope == "credential":
            credential = principal.credential_id or principal.subject
            return f"credential:{credential}"
        if scope == "resource":
            field = manifest.get("extensions", {}).get("concurrency_resource_argument")
            if not isinstance(field, str) or field not in kwargs:
                raise InvocationError(
                    "MANIFEST_INVALID",
                    "resource concurrency requires extensions.concurrency_resource_argument",
                )
            return f"resource:{target}:{kwargs[field]}"
        if scope == "custom":
            fields = manifest.get("extensions", {}).get("concurrency_key_fields")
            if (
                not isinstance(fields, list)
                or not fields
                or not all(isinstance(x, str) for x in fields)
            ):
                raise InvocationError(
                    "MANIFEST_INVALID",
                    "custom concurrency requires extensions.concurrency_key_fields",
                )
            try:
                values = ":".join(str(kwargs[field]) for field in fields)
            except KeyError as exc:
                raise InvocationError(
                    "INVALID_ARGUMENTS", f"Missing concurrency key argument: {exc.args[0]}"
                ) from exc
            return f"custom:{tool_name}:{values}"
        raise InvocationError("MANIFEST_INVALID", f"Unsupported concurrency scope: {scope}")

    def _semaphore(self, key: str, limit: int) -> threading.BoundedSemaphore:
        with self._locks_guard:
            existing = self._locks.get(key)
            if existing is None:
                semaphore = threading.BoundedSemaphore(limit)
                self._locks[key] = (limit, semaphore)
                return semaphore
            existing_limit, semaphore = existing
            if existing_limit != limit:
                raise InvocationError(
                    "MANIFEST_INVALID",
                    f"Concurrency key '{key}' is declared with conflicting limits",
                )
            return semaphore

    def enforce_final_result_size(self, tool_name: str, result: Any) -> None:
        """Enforce the bound on the final serialized response returned to a client."""
        manifest = self._manifest(tool_name)
        self._enforce_result_size(result, int(manifest["max_response_bytes"]))

    @staticmethod
    def _enforce_result_size(result: Any, maximum: int) -> None:
        if isinstance(result, bytes):
            size = len(result)
        elif isinstance(result, str):
            size = len(result.encode("utf-8"))
        else:
            try:
                size = len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8"))
            except (TypeError, ValueError):
                size = len(repr(result).encode("utf-8", errors="replace"))
        if size > maximum:
            raise InvocationError("RESPONSE_TOO_LARGE", "Tool response exceeds configured limit")

    @staticmethod
    def _release_when_done(
        future: Future[Any],
        tool_semaphore: threading.BoundedSemaphore,
        executor_capacity: threading.BoundedSemaphore,
    ) -> None:
        # This callback runs only when the underlying work has actually ended
        # (or when a queued future was successfully cancelled). A client timeout
        # therefore cannot make the same concurrency permit available early.
        tool_semaphore.release()
        executor_capacity.release()

    def invoke_sync(
        self,
        tool_name: str,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        manifest = self._manifest(tool_name)
        target = self._authorize(tool_name, manifest)
        timeout = int(manifest["extensions"]["timeout_ms"]) / 1000
        deadline = time.monotonic() + timeout
        concurrency_key = self._concurrency_key(tool_name, manifest, target, args, kwargs)
        semaphore = self._semaphore(concurrency_key, int(manifest["concurrency"]["limit"]))

        if not semaphore.acquire(timeout=_remaining(deadline)):
            raise InvocationError("BUSY", f"Tool '{tool_name}' concurrency limit reached")

        capacity_acquired = False
        tool_permit_owned = True
        try:
            remaining = _remaining(deadline)
            if remaining <= 0 or not self._executor_capacity.acquire(timeout=remaining):
                raise InvocationError("BUSY", "Synchronous invocation queue is full")
            capacity_acquired = True

            token = _current_deadline.set(deadline)
            try:
                context = contextvars.copy_context()
            finally:
                _current_deadline.reset(token)

            try:
                future = self._executor.submit(context.run, function, *args, **kwargs)
            except Exception:
                self._executor_capacity.release()
                capacity_acquired = False
                raise

            future.add_done_callback(
                lambda completed: self._release_when_done(
                    completed, semaphore, self._executor_capacity
                )
            )
            # Ownership of both permits has moved to the completion callback.
            capacity_acquired = False
            tool_permit_owned = False

            try:
                result = future.result(timeout=_remaining(deadline))
            except FutureTimeoutError as exc:
                # Cancellation only succeeds while queued. If work is already
                # running, the callback retains permits until it really exits.
                future.cancel()
                raise InvocationError("DEADLINE_EXCEEDED", f"Tool '{tool_name}' timed out") from exc
            self._enforce_result_size(result, int(manifest["max_response_bytes"]))
            return result
        finally:
            if capacity_acquired:
                self._executor_capacity.release()
            if tool_permit_owned:
                semaphore.release()

    async def invoke_async(
        self,
        tool_name: str,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        manifest = self._manifest(tool_name)
        target = self._authorize(tool_name, manifest)
        timeout = int(manifest["extensions"]["timeout_ms"]) / 1000
        deadline = time.monotonic() + timeout
        concurrency_key = self._concurrency_key(tool_name, manifest, target, args, kwargs)
        semaphore = self._semaphore(concurrency_key, int(manifest["concurrency"]["limit"]))

        acquire_task = asyncio.create_task(
            asyncio.to_thread(semaphore.acquire, True, _remaining(deadline))
        )
        try:
            acquired = await acquire_task
        except asyncio.CancelledError:
            # asyncio.to_thread cannot stop the blocking acquire. If it later
            # obtains the permit, release it from the completion callback.
            def release_cancelled_acquire(completed: asyncio.Task[bool]) -> None:
                if (
                    not completed.cancelled()
                    and completed.exception() is None
                    and completed.result()
                ):
                    semaphore.release()

            acquire_task.add_done_callback(release_cancelled_acquire)
            raise
        if not acquired:
            raise InvocationError("BUSY", f"Tool '{tool_name}' concurrency limit reached")

        permit_owned = True
        token = _current_deadline.set(deadline)
        operation: asyncio.Task[Any] | None = None
        try:
            remaining = _remaining(deadline)
            if remaining <= 0:
                raise InvocationError("DEADLINE_EXCEEDED", f"Tool '{tool_name}' timed out")
            operation = asyncio.create_task(function(*args, **kwargs))
            done, _ = await asyncio.wait({operation}, timeout=remaining)
            if not done:
                operation.cancel()
                operation.add_done_callback(lambda completed: semaphore.release())
                permit_owned = False
                raise InvocationError("DEADLINE_EXCEEDED", f"Tool '{tool_name}' timed out")
            result = operation.result()
            self._enforce_result_size(result, int(manifest["max_response_bytes"]))
            return result
        except asyncio.CancelledError:
            if operation is not None and not operation.done():
                operation.cancel()
                operation.add_done_callback(lambda completed: semaphore.release())
                permit_owned = False
            raise
        finally:
            _current_deadline.reset(token)
            if permit_owned:
                semaphore.release()


KERNEL = InvocationKernel()
