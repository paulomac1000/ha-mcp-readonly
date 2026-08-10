"""Tests for the shared invocation policy used by every transport."""

import time

import pytest

from tests.unit.manifest_helpers import register_test_manifest as _register
from tools.invocation import InvocationError, InvocationKernel, Principal, principal_scope
from tools.manifests import make_manifest


def test_missing_manifest_fails_closed() -> None:
    kernel = InvocationKernel(max_workers=1)
    with pytest.raises(InvocationError, match="no manifest") as caught:
        kernel.invoke_sync("not_declared_anywhere", lambda: "value")
    assert caught.value.code == "MANIFEST_MISSING"


def test_capability_is_enforced() -> None:
    name = "test_kernel_capability"
    _register(name, authorization_scopes=["filesystem.read"])
    kernel = InvocationKernel(max_workers=1)
    principal = Principal("limited", "test", frozenset({"ha.read"}))
    with principal_scope(principal), pytest.raises(InvocationError) as caught:
        kernel.invoke_sync(name, lambda: "value")
    assert caught.value.code == "FORBIDDEN"


def test_deadline_is_enforced() -> None:
    name = "test_kernel_deadline"
    _register(name, extensions={**make_manifest(name)["extensions"], "timeout_ms": 100})
    kernel = InvocationKernel(max_workers=1)
    with pytest.raises(InvocationError) as caught:
        kernel.invoke_sync(name, lambda: time.sleep(0.25))
    assert caught.value.code == "DEADLINE_EXCEEDED"


def test_response_limit_is_enforced() -> None:
    name = "test_kernel_response_limit"
    _register(name, max_response_bytes=1024)
    kernel = InvocationKernel(max_workers=1)
    with pytest.raises(InvocationError) as caught:
        kernel.invoke_sync(name, lambda: "x" * 2048)
    assert caught.value.code == "RESPONSE_TOO_LARGE"


def test_timeout_keeps_permit_until_underlying_work_finishes() -> None:
    """A timed-out worker must still count against max_concurrency."""
    import threading

    name = "test_kernel_timeout_permit"
    _register(
        name,
        extensions={**make_manifest(name)["extensions"], "timeout_ms": 100},
        concurrency={"scope": "capability", "limit": 1, "queue_limit": 1},
    )
    kernel = InvocationKernel(max_workers=2, max_queue=1)
    started = threading.Event()
    release = threading.Event()
    running = 0
    maximum_running = 0
    guard = threading.Lock()

    def blocking() -> str:
        nonlocal running, maximum_running
        with guard:
            running += 1
            maximum_running = max(maximum_running, running)
        started.set()
        release.wait(1)
        with guard:
            running -= 1
        return "done"

    with pytest.raises(InvocationError) as first:
        kernel.invoke_sync(name, blocking)
    assert first.value.code == "DEADLINE_EXCEEDED"
    assert started.is_set()

    with pytest.raises(InvocationError) as second:
        kernel.invoke_sync(name, blocking)
    assert second.value.code == "BUSY"
    assert maximum_running == 1

    release.set()
    time.sleep(0.05)
    assert kernel.invoke_sync(name, lambda: "ok") == "ok"


@pytest.mark.asyncio
async def test_async_timeout_keeps_permit_until_cancelled_operation_finishes() -> None:
    """An async operation that suppresses cancellation must retain its permit."""
    import asyncio

    name = "test_kernel_async_timeout_permit"
    _register(
        name,
        extensions={**make_manifest(name)["extensions"], "timeout_ms": 100},
        concurrency={"scope": "capability", "limit": 1, "queue_limit": 1},
    )
    kernel = InvocationKernel(max_workers=1)
    release = asyncio.Event()
    cancellation_seen = asyncio.Event()

    async def stubborn() -> str:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            await release.wait()
        return "done"

    with pytest.raises(InvocationError) as first:
        await kernel.invoke_async(name, stubborn)
    assert first.value.code == "DEADLINE_EXCEEDED"
    await asyncio.wait_for(cancellation_seen.wait(), timeout=1)

    with pytest.raises(InvocationError) as second:
        await kernel.invoke_async(name, stubborn)
    assert second.value.code == "BUSY"

    release.set()
    await asyncio.sleep(0.01)
    assert await kernel.invoke_async(name, _async_ok) == "ok"


async def _async_ok() -> str:
    return "ok"


@pytest.mark.asyncio
async def test_cancel_while_waiting_for_permit_does_not_leak_capacity() -> None:
    """Cancelling admission must release a permit acquired later by the worker thread."""
    import asyncio

    name = "test_kernel_cancelled_admission"
    _register(
        name,
        extensions={**make_manifest(name)["extensions"], "timeout_ms": 1000},
        concurrency={"scope": "capability", "limit": 1, "queue_limit": 1},
    )
    kernel = InvocationKernel(max_workers=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> str:
        started.set()
        await release.wait()
        return "held"

    first = asyncio.create_task(kernel.invoke_async(name, hold))
    await asyncio.wait_for(started.wait(), timeout=1)
    waiting = asyncio.create_task(kernel.invoke_async(name, _async_ok))
    await asyncio.sleep(0.03)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    release.set()
    assert await asyncio.wait_for(first, timeout=1) == "held"
    await asyncio.sleep(0.05)
    assert await asyncio.wait_for(kernel.invoke_async(name, _async_ok), timeout=1) == "ok"
