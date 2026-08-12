"""Operation registry hardening for blocking coroutine adapters."""

import asyncio
import json
import time

import pytest

from tools.manifests import get_all_manifests, make_manifest, register_manifest, set_active_tools
from tools.operations import OperationRegistry


def _register(name: str) -> None:
    manifest = make_manifest(name, timeout_ms=1000)
    manifest["extensions"]["target_binding"] = {
        "kind": "deployment-resource",
        "target": "runtime",
        "revalidation": "per-invocation",
    }
    active = set(get_all_manifests(active_only=True))
    register_manifest(name, manifest)
    set_active_tools(active | {name})


@pytest.mark.asyncio
async def test_storage_coroutine_blocking_io_does_not_block_server_event_loop() -> None:
    name = "test_blocking_storage_coroutine"
    _register(name)

    async def blocking_tool() -> str:
        time.sleep(0.2)
        return json.dumps({"success": True})

    blocking_tool.__module__ = "tools.storage"
    registry = OperationRegistry()
    operation = registry.register(name, blocking_tool)
    started = time.monotonic()
    invocation = asyncio.create_task(operation.fn())
    await asyncio.sleep(0.03)
    elapsed = time.monotonic() - started
    assert elapsed < 0.12, "blocking adapter stalled the shared event loop"
    result = json.loads(await invocation)
    assert result["success"] is True
