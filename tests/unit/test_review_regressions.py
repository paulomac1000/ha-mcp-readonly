"""Regression tests for PR review findings."""

import gc

import pytest

from tools.invocation import InvocationKernel
from tools.settings import RuntimeSettings


def test_invocation_key_cache_releases_idle_user_keys() -> None:
    kernel = InvocationKernel(max_workers=1)
    semaphore = kernel._semaphore("resource:user-controlled", 1)
    assert "resource:user-controlled" in kernel._locks
    del semaphore
    gc.collect()
    assert "resource:user-controlled" not in kernel._locks


@pytest.mark.parametrize("name", ["HEALTH_CHECK_PORT", "MCP_PORT", "REST_API_PORT"])
def test_runtime_ports_reject_out_of_range(monkeypatch, name: str) -> None:
    monkeypatch.setenv(name, "70000")
    with pytest.raises(ValueError, match=name):
        RuntimeSettings.from_env()
